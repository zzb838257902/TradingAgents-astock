"""Pipeline factor wiring: daily_indicators and insufficient bar history."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from tradingagents.market_data.fixture_store import load_fixture_into_repository
from tradingagents.market_data.market_hours import post_close_signal_time
from tradingagents.market_data.repository import MarketDataRepository
from tradingagents.screener.config import ScreenerConfig
from tradingagents.screener.live import resolve_signal_trade_date
from tradingagents.screener.pipeline import run_fixture_backtest, run_screen
from tradingagents.screener.report import ScreeningStatus
from tradingagents.screener.report import ScreeningStatus
from tradingagents.screener.universe_resolver import UniverseRequest, UniverseType

SHANGHAI = ZoneInfo("Asia/Shanghai")


def _publish_daily_indicators(
    repo: MarketDataRepository,
    rows: list[dict],
    *,
    trade_date: date,
) -> None:
    run_id = repo.begin_ingestion_run(
        "daily_indicators",
        {"trade_date": trade_date.isoformat()},
    )
    repo.upsert_staging_daily_indicators(run_id, rows)
    repo.publish_dataset_version(run_id)


def _indicator_row(
    *,
    symbol: str,
    trade_date: date,
    pe_ttm: float,
    pb: float,
) -> dict:
    return {
        "symbol": symbol,
        "trade_date": trade_date,
        "pe_ttm": pe_ttm,
        "pb": pb,
        "turnover_pct": 1.0,
        "total_market_cap_cny": 1_000_000_000.0,
        "float_market_cap_cny": 900_000_000.0,
        "available_at": post_close_signal_time(trade_date),
        "source": "fixture",
        "ingested_at": datetime.now(tz=SHANGHAI),
    }


def _two_symbol_fixture() -> dict:
    return {
        "version": 1,
        "datasets": {"daily_bars": "pit_required", "financials": "pit_required"},
        "symbols": [
            {"symbol": "CHEAP", "industry": "电子", "st_flag": False, "list_date": "2025-12-31"},
            {"symbol": "RICH", "industry": "电子", "st_flag": False, "list_date": "2025-12-31"},
        ],
        "bars": {
            "2025-12-31": {
                "CHEAP": {"open": 10, "high": 10.5, "low": 9.8, "close": 10, "volume": 2_000_000},
                "RICH": {"open": 10, "high": 10.5, "low": 9.8, "close": 10, "volume": 2_000_000},
            },
            "2026-01-02": {
                "CHEAP": {"open": 10.1, "high": 10.6, "low": 9.9, "close": 10.1, "volume": 2_000_000},
                "RICH": {"open": 10.1, "high": 10.6, "low": 9.9, "close": 10.1, "volume": 2_000_000},
            },
            "2026-01-03": {
                "CHEAP": {"open": 10.2, "high": 10.7, "low": 10.0, "close": 10.2, "volume": 2_000_000},
                "RICH": {"open": 10.2, "high": 10.7, "low": 10.0, "close": 10.2, "volume": 2_000_000},
            },
            "2026-01-06": {
                "CHEAP": {"open": 10.3, "high": 10.8, "low": 10.1, "close": 10.3, "volume": 2_000_000},
                "RICH": {"open": 10.3, "high": 10.8, "low": 10.1, "close": 10.3, "volume": 2_000_000},
            },
        },
        "financials": [],
    }


def test_pipeline_records_insufficient_bar_history(tmp_path):
    fixture = _two_symbol_fixture()
    fixture["symbols"].append(
        {"symbol": "THIN", "industry": "银行", "st_flag": False, "list_date": "2025-12-31"},
    )
    fixture["bars"]["2026-01-03"]["THIN"] = {
        "open": 10.2,
        "high": 10.7,
        "low": 10.0,
        "close": 10.2,
        "volume": 2_000_000,
    }
    fixture["bars"]["2026-01-06"]["THIN"] = {
        "open": 10.3,
        "high": 10.8,
        "low": 10.1,
        "close": 10.3,
        "volume": 2_000_000,
    }

    config = ScreenerConfig().model_copy(update={
        "universe": ScreenerConfig().universe.model_copy(update={
            "min_listing_days": 2,
            "min_avg_amount_20d": 1_000_000,
        })
    })
    result = run_fixture_backtest(fixture, config, tmp_path / "factors.duckdb")
    assert result["excluded_reasons"]["THIN"] == ["insufficient_bar_history"]
    assert "THIN" not in result["ranking"]


def test_pipeline_blends_daily_indicators_into_quality(tmp_path):
    fixture = _two_symbol_fixture()
    db_path = tmp_path / "indicators.duckdb"
    repo = MarketDataRepository(db_path)
    load_fixture_into_repository(repo, fixture)
    signal_date = date(2026, 1, 3)
    _publish_daily_indicators(
        repo,
        [
            _indicator_row(
                symbol="CHEAP",
                trade_date=signal_date,
                pe_ttm=8.0,
                pb=0.8,
            ),
            _indicator_row(
                symbol="RICH",
                trade_date=signal_date,
                pe_ttm=40.0,
                pb=4.0,
            ),
        ],
        trade_date=signal_date,
    )

    config = ScreenerConfig().model_copy(update={
        "universe": ScreenerConfig().universe.model_copy(update={
            "min_listing_days": 2,
            "min_avg_amount_20d": 1_000_000,
        })
    })
    report = run_screen(
        fixture,
        config,
        db_path,
        reload=False,
        universe_request=UniverseRequest(
            universe_type=UniverseType.ALL,
            as_of=post_close_signal_time(signal_date),
        ),
    )
    assert report.status == ScreeningStatus.OK
    assert report.ranking[0] == "CHEAP"
    assert report.factor_contributions["CHEAP"]["quality"] > (
        report.factor_contributions["RICH"]["quality"]
    )


def test_resolve_signal_trade_date_empty_calendar_suggests_sync(tmp_path):
    repo = MarketDataRepository(tmp_path / "empty.duckdb")
    _, _, errors = resolve_signal_trade_date(repo, as_of=None, today=date(2026, 6, 21))
    assert errors
    assert "sync trade-calendar" in errors[0]


def test_resolve_signal_trade_date_future_only_calendar_suggests_sync(tmp_path):
    repo = MarketDataRepository(tmp_path / "future.duckdb")
    repo.connection.execute(
        """INSERT INTO trade_calendar (exchange, trade_date, is_open, available_at, source)
           VALUES ('SSE', ?, TRUE, ?, 'fixture')""",
        [date(2026, 12, 31), datetime(2026, 12, 31, 9, 0, tzinfo=SHANGHAI)],
    )
    _, _, errors = resolve_signal_trade_date(
        repo,
        as_of=None,
        today=date(2026, 6, 21),
    )
    assert errors
    assert "sync trade-calendar" in errors[0]
