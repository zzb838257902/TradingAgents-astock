"""Daily indicators sync tests (remediation Task 3)."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from tradingagents.market_data.config import MarketDataPaths
from tradingagents.market_data.contracts import DataResult, DataStatus, PITLevel
from tradingagents.market_data.fixture_store import load_fixture_into_repository
from tradingagents.market_data.market_hours import post_close_signal_time
from tradingagents.market_data.repository import MarketDataRepository
from tradingagents.market_data.sync import MarketDataSync, SyncStatus

SHANGHAI = ZoneInfo("Asia/Shanghai")
TRADE_DATE = date(2026, 1, 2)


def _indicator_row(symbol: str = "600000") -> dict:
    return {
        "symbol": symbol,
        "trade_date": TRADE_DATE,
        "pe_ttm": 6.5,
        "pb": 0.7,
        "turnover_pct": 0.4,
        "total_market_cap_cny": 320_000_000_000.0,
        "float_market_cap_cny": 290_000_000_000.0,
        "available_at": post_close_signal_time(TRADE_DATE),
        "source": "fixture",
    }


class _IndicatorProvider:
    name = "fixture"

    def __init__(self, result: DataResult[list[dict]]):
        self._result = result
        self.calls = 0

    def get_daily_indicators(self, trade_date: date) -> DataResult[list[dict]]:
        self.calls += 1
        return self._result

    def probe_capabilities(self):
        from tradingagents.market_data.contracts import ProviderCapability

        run_time = datetime(2026, 1, 2, 10, 0, tzinfo=SHANGHAI)
        return DataResult(
            data=[
                ProviderCapability(
                    dataset="daily_indicators",
                    endpoint="fixture",
                    permitted=True,
                    pit_level=PITLevel.BEST_EFFORT,
                    probed_at=run_time,
                ),
            ],
            status=DataStatus.OK,
            source=self.name,
            as_of=run_time,
            available_at=run_time,
            pit_level=PITLevel.BEST_EFFORT,
        )


def _setup(tmp_path, provider: _IndicatorProvider) -> MarketDataSync:
    fixture = {
        "symbols": [
            {"symbol": "600000", "board": "main", "list_date": "2020-01-01"},
            {"symbol": "000001", "board": "main", "list_date": "2020-01-01"},
        ],
        "bars": {TRADE_DATE.isoformat(): {}},
        "datasets": {"daily_bars": "pit_required", "financials": "pit_required"},
    }
    paths = MarketDataPaths(home_dir=tmp_path)
    repo = MarketDataRepository(paths.live_db_path, snapshot_dir=paths.snapshot_dir)
    load_fixture_into_repository(repo, fixture)
    return MarketDataSync(repo, provider, paths)


def _ok_result(rows: list[dict]) -> DataResult[list[dict]]:
    run_time = post_close_signal_time(TRADE_DATE)
    return DataResult(
        data=rows,
        status=DataStatus.OK,
        source="fixture",
        as_of=run_time,
        available_at=run_time,
        pit_level=PITLevel.BEST_EFFORT,
    )


def test_sync_daily_indicators_publishes_rows(tmp_path):
    provider = _IndicatorProvider(_ok_result([_indicator_row("600000"), _indicator_row("000001")]))
    sync = _setup(tmp_path, provider)
    result = sync.sync_daily_indicators(TRADE_DATE)
    assert result.status == SyncStatus.PUBLISHED
    rows = sync.repository.get_daily_indicators(
        ["600000", "000001"],
        TRADE_DATE,
        post_close_signal_time(TRADE_DATE),
    )
    assert len(rows) == 2


def test_sync_daily_indicators_is_idempotent(tmp_path):
    provider = _IndicatorProvider(_ok_result([_indicator_row("600000"), _indicator_row("000001")]))
    sync = _setup(tmp_path, provider)
    first = sync.sync_daily_indicators(TRADE_DATE)
    second = sync.sync_daily_indicators(TRADE_DATE)
    assert first.status == SyncStatus.PUBLISHED
    assert second.status == SyncStatus.PUBLISHED
    assert first.content_hash == second.content_hash


def test_sync_daily_indicators_success_empty_publishes_for_fixture_only(tmp_path):
    run_time = post_close_signal_time(TRADE_DATE)
    provider = _IndicatorProvider(DataResult(
        data=[],
        status=DataStatus.SUCCESS_EMPTY,
        source="fixture",
        as_of=run_time,
        available_at=run_time,
        pit_level=PITLevel.BEST_EFFORT,
    ))
    sync = _setup(tmp_path, provider)
    result = sync.sync_daily_indicators(TRADE_DATE)
    assert result.status == SyncStatus.PUBLISHED
    latest = sync.repository.get_latest_published_version("daily_indicators")
    assert latest is not None


def test_sync_rejects_success_empty_from_free_provider(tmp_path):
    run_time = post_close_signal_time(TRADE_DATE)
    provider = _IndicatorProvider(DataResult(
        data=[],
        status=DataStatus.SUCCESS_EMPTY,
        source="free_astock",
        as_of=run_time,
        available_at=run_time,
        pit_level=PITLevel.BEST_EFFORT,
    ))
    provider.name = "free_astock"
    sync = _setup(tmp_path, provider)
    result = sync.sync_daily_indicators(TRADE_DATE)
    assert result.status == SyncStatus.BLOCKED
    assert sync.repository.get_latest_published_version("daily_indicators") is None


def test_sync_blocks_non_trading_day(tmp_path):
    provider = _IndicatorProvider(_ok_result([_indicator_row("600000"), _indicator_row("000001")]))
    sync = _setup(tmp_path, provider)
    result = sync.sync_daily_indicators(date(2026, 1, 4))
    assert result.status == SyncStatus.BLOCKED
    assert "not an open trade date" in result.errors[0]
    assert provider.calls == 0


def test_unpublished_daily_indicators_are_not_visible(tmp_path):
    provider = _IndicatorProvider(_ok_result([_indicator_row("600000"), _indicator_row("000001")]))
    sync = _setup(tmp_path, provider)
    sync.repository.connection.execute(
        """
        INSERT INTO daily_indicators (
            symbol, trade_date, pe_ttm, pb, turnover_pct,
            total_market_cap_cny, float_market_cap_cny,
            available_at, source
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            "600000",
            TRADE_DATE,
            6.5,
            0.7,
            0.4,
            320_000_000_000.0,
            290_000_000_000.0,
            post_close_signal_time(TRADE_DATE),
            "shadow",
        ],
    )
    assert sync.repository.get_daily_indicators(
        ["600000"],
        TRADE_DATE,
        post_close_signal_time(TRADE_DATE),
    ) == []
    sync.sync_daily_indicators(TRADE_DATE)
    rows = sync.repository.get_daily_indicators(
        ["600000"],
        TRADE_DATE,
        post_close_signal_time(TRADE_DATE),
    )
    assert len(rows) == 1
    assert rows[0]["source"] == "fixture"


def test_sync_daily_indicators_historical_not_available_is_blocked(tmp_path):
    run_time = post_close_signal_time(TRADE_DATE)
    provider = _IndicatorProvider(DataResult(
        data=None,
        status=DataStatus.NOT_AVAILABLE_YET,
        source="fixture",
        as_of=run_time,
        available_at=run_time,
        pit_level=PITLevel.BEST_EFFORT,
        errors=["historical daily_indicators unsupported on free path"],
    ))
    sync = _setup(tmp_path, provider)
    result = sync.sync_daily_indicators(date(2020, 1, 2))
    assert result.status == SyncStatus.BLOCKED
    assert sync.repository.get_latest_published_version("daily_indicators") is None


def test_sync_daily_indicators_network_error_does_not_publish(tmp_path):
    run_time = post_close_signal_time(TRADE_DATE)
    provider = _IndicatorProvider(DataResult(
        data=None,
        status=DataStatus.NETWORK_ERROR,
        source="fixture",
        as_of=run_time,
        available_at=run_time,
        pit_level=PITLevel.BEST_EFFORT,
        errors=["network down"],
    ))
    sync = _setup(tmp_path, provider)
    result = sync.sync_daily_indicators(TRADE_DATE)
    assert result.status == SyncStatus.ERROR
    assert sync.repository.get_latest_published_version("daily_indicators") is None


def test_failed_quality_gate_does_not_publish(tmp_path):
    bad = _indicator_row("600000")
    bad["total_market_cap_cny"] = -1.0
    provider = _IndicatorProvider(_ok_result([bad, _indicator_row("000001")]))
    sync = _setup(tmp_path, provider)
    result = sync.sync_daily_indicators(TRADE_DATE)
    assert result.status == SyncStatus.BLOCKED
    assert sync.repository.get_daily_indicators(
        ["600000"],
        TRADE_DATE,
        post_close_signal_time(TRADE_DATE),
    ) == []


def test_staging_rows_invisible_until_published(tmp_path):
    provider = _IndicatorProvider(_ok_result([_indicator_row("600000"), _indicator_row("000001")]))
    sync = _setup(tmp_path, provider)
    run_id = sync.repository.begin_ingestion_run(
        "daily_indicators",
        {"trade_date": TRADE_DATE.isoformat()},
    )
    sync.repository.upsert_staging_daily_indicators(run_id, [_indicator_row("600000")])
    with pytest.raises(ValueError, match="quality gate"):
        sync.repository.publish_dataset_version(run_id)
    assert sync.repository.get_daily_indicators(
        ["600000"],
        TRADE_DATE,
        post_close_signal_time(TRADE_DATE),
    ) == []
