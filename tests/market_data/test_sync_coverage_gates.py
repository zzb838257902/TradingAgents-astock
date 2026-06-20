"""Coverage gate tests for backfill and financial sync."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from tradingagents.market_data.config import MarketDataPaths
from tradingagents.market_data.contracts import (
    DataResult,
    DataStatus,
    PITLevel,
)
from tradingagents.market_data.providers.base import MarketDataProvider
from tradingagents.market_data.quality import (
    build_backfill_completeness_report,
    build_financial_symbol_coverage_report,
    build_trade_calendar_range_report,
)
from tradingagents.market_data.repository import MarketDataRepository
from tradingagents.market_data.sync import MarketDataSync, SyncStatus

SHANGHAI = ZoneInfo("Asia/Shanghai")


class _BackfillProvider(MarketDataProvider):
    name = "fixture_backfill"

    def probe_capabilities(self):
        run_time = datetime.now(tz=SHANGHAI)
        return DataResult(
            data=[],
            status=DataStatus.OK,
            source=self.name,
            as_of=run_time,
            available_at=run_time,
            pit_level=PITLevel.PIT_REQUIRED,
        )

    def get_daily_bars(self, symbols, start, end):
        run_time = datetime.now(tz=SHANGHAI)
        bars = [{
            "symbol": "600000",
            "trade_date": date(2026, 1, 2),
            "open": 10.0,
            "high": 10.5,
            "low": 9.8,
            "close": 10.2,
            "volume": 1000.0,
            "amount": 10200.0,
            "pre_close": 10.0,
        }]
        return DataResult(
            data=bars,
            status=DataStatus.OK,
            source=self.name,
            as_of=run_time,
            available_at=run_time,
            pit_level=PITLevel.PIT_REQUIRED,
        )

    def get_financials(self, symbols, as_of):
        run_time = datetime.now(tz=SHANGHAI)
        return DataResult(
            data=[],
            status=DataStatus.OK,
            source=self.name,
            as_of=run_time,
            available_at=run_time,
            pit_level=PITLevel.PIT_REQUIRED,
        )


def _seed_calendar(repo: MarketDataRepository) -> None:
    run_id = repo.begin_ingestion_run("trade_calendar", {})
    repo.upsert_staging_trade_calendar(run_id, [
        {
            "exchange": "SSE",
            "trade_date": date(2026, 1, 2),
            "is_open": True,
            "available_at": datetime(2026, 1, 2, 9, 0, tzinfo=SHANGHAI),
            "source": "fixture",
        },
        {
            "exchange": "SSE",
            "trade_date": date(2026, 1, 3),
            "is_open": True,
            "available_at": datetime(2026, 1, 3, 9, 0, tzinfo=SHANGHAI),
            "source": "fixture",
        },
    ])
    repo.publish_dataset_version(run_id)


def test_backfill_completeness_report_counts_symbol_day_cells():
    report = build_backfill_completeness_report(
        bars=[{
            "symbol": "600000",
            "trade_date": date(2026, 1, 2),
        }],
        symbols=["600000", "000001"],
        open_dates=[date(2026, 1, 2), date(2026, 1, 3)],
        threshold=1.0,
    )
    assert report.numerator == 1
    assert report.denominator == 4
    assert report.status == "fail"


def test_sync_daily_backfill_blocks_incomplete_symbol_day_grid(tmp_path):
    paths = MarketDataPaths(home_dir=tmp_path)
    repo = MarketDataRepository(paths.live_db_path, snapshot_dir=paths.snapshot_dir)
    _seed_calendar(repo)
    repo.save_sync_state("capability_probe", {
        "security_master": {"permitted": True},
    })
    sync = MarketDataSync(repo, _BackfillProvider(), paths)
    result = sync.sync_daily_backfill(
        date(2026, 1, 2),
        date(2026, 1, 3),
        symbols=["600000", "000001"],
    )
    assert result.status == SyncStatus.BLOCKED
    assert "coverage below threshold" in result.errors[0]


def test_sync_financials_blocks_empty_rows(tmp_path):
    paths = MarketDataPaths(home_dir=tmp_path)
    repo = MarketDataRepository(paths.live_db_path, snapshot_dir=paths.snapshot_dir)
    repo.save_sync_state("capability_probe", {
        "financials": {"permitted": True},
    })
    sync = MarketDataSync(repo, _BackfillProvider(), paths)
    result = sync.sync_financials(
        datetime(2026, 1, 3, 16, 0, tzinfo=SHANGHAI),
        symbols=["600000", "000001"],
    )
    assert result.status == SyncStatus.BLOCKED
    assert "no financial records" in result.errors[0]


class _FinancialProvider(_BackfillProvider):
    def get_financials(self, symbols, as_of):
        run_time = datetime.now(tz=SHANGHAI)
        rows = []
        for symbol in symbols:
            rows.append({
                "symbol": symbol,
                "report_period": "20260331",
                "roe": 0.08 if symbol == "600000" else 0.0,
                "operating_cashflow": 1_000_000.0,
                "net_profit": 500_000.0,
                "debt_ratio": 0.4,
                "announcement_date": date(2026, 3, 31),
                "available_at": datetime(2026, 4, 1, 9, 0, tzinfo=SHANGHAI),
                "source": "fixture",
            })
        return DataResult(
            data=rows,
            status=DataStatus.OK,
            source=self.name,
            as_of=run_time,
            available_at=run_time,
            pit_level=PITLevel.PIT_REQUIRED,
        )


def test_sync_financials_blocks_without_trade_calendar(tmp_path):
    paths = MarketDataPaths(home_dir=tmp_path)
    repo = MarketDataRepository(paths.live_db_path, snapshot_dir=paths.snapshot_dir)
    repo.save_sync_state("capability_probe", {
        "financials": {"permitted": True},
    })
    sync = MarketDataSync(repo, _FinancialProvider(), paths)
    result = sync.sync_financials(
        datetime(2026, 6, 18, 16, 0, tzinfo=SHANGHAI),
        symbols=["600000"],
    )
    assert result.status == SyncStatus.BLOCKED
    assert "trade_calendar must be synced" in result.errors[0]


def test_sync_financials_blocks_low_field_quality(tmp_path):
    paths = MarketDataPaths(home_dir=tmp_path)
    repo = MarketDataRepository(paths.live_db_path, snapshot_dir=paths.snapshot_dir)
    _seed_calendar(repo)
    repo.save_sync_state("capability_probe", {
        "financials": {"permitted": True},
    })
    sync = MarketDataSync(repo, _FinancialProvider(), paths)
    result = sync.sync_financials(
        datetime(2026, 6, 18, 16, 0, tzinfo=SHANGHAI),
        symbols=["600000", "000001"],
    )
    assert result.status == SyncStatus.BLOCKED
    assert "field quality below threshold" in result.errors[0]
    assert result.coverage_reports["financial_field_quality"].numerator == 1


def test_trade_calendar_range_report_blocks_early_start():
    report = build_trade_calendar_range_report(
        date(1990, 1, 1),
        date(2026, 6, 18),
        [date(2023, 3, 1), date(2026, 6, 18)],
        source_limit_bars=800,
    )
    assert report.status == "fail"
    assert report.details[0]["actual_start"] == "2023-03-01"
    assert report.details[0]["covers_start"] is False


def test_trade_calendar_range_report_passes_weekend_start():
    report = build_trade_calendar_range_report(
        date(2026, 1, 3),
        date(2026, 1, 31),
        [date(2026, 1, 5), date(2026, 1, 31)],
        source_limit_bars=800,
    )
    assert report.status == "pass"
    assert report.details[0]["effective_start"] == "2026-01-05"
    assert report.details[0]["covers_start"] is True
    assert report.details[0]["covers_end"] is True


def test_trade_calendar_range_report_passes_holiday_start_with_reference_calendar():
    reference = [
        date(2026, 1, 2),
        date(2026, 1, 5),
        date(2026, 1, 30),
    ]
    report = build_trade_calendar_range_report(
        date(2026, 1, 1),
        date(2026, 1, 31),
        reference,
        source_limit_bars=800,
        reference_open_dates=reference,
    )
    assert report.status == "pass"
    assert report.details[0]["effective_start"] == "2026-01-02"
    assert report.details[0]["effective_end"] == "2026-01-30"


def test_trade_calendar_range_report_blocks_weekday_without_reference_calendar():
    report = build_trade_calendar_range_report(
        date(2026, 1, 1),
        date(2026, 1, 31),
        [date(2026, 1, 2), date(2026, 1, 5), date(2026, 1, 30)],
        source_limit_bars=800,
    )
    assert report.status == "fail"
    assert report.details[0]["covers_start"] is False


def test_trade_calendar_range_report_blocks_supplier_gap_after_weekday_start():
    report = build_trade_calendar_range_report(
        date(2026, 1, 2),
        date(2026, 1, 31),
        [date(2026, 1, 8), date(2026, 1, 30)],
        source_limit_bars=800,
    )
    assert report.status == "fail"
    assert report.details[0]["covers_start"] is False
    assert report.details[0]["covers_end"] is True


def test_trade_calendar_range_report_end_bound_uses_reference_calendar():
    reference = [
        date(2026, 1, 2),
        date(2026, 1, 23),
        date(2026, 1, 26),
    ]
    report = build_trade_calendar_range_report(
        date(2026, 1, 2),
        date(2026, 1, 28),
        reference,
        reference_open_dates=reference,
    )
    assert report.status == "pass"
    assert report.details[0]["effective_end"] == "2026-01-26"


def test_trade_calendar_range_report_blocks_stale_end():
    report = build_trade_calendar_range_report(
        date(2026, 1, 2),
        date(2026, 1, 31),
        [date(2026, 1, 2), date(2026, 1, 5)],
        source_limit_bars=800,
    )
    assert report.status == "fail"
    assert report.details[0]["covers_start"] is True
    assert report.details[0]["covers_end"] is False


def test_trade_calendar_range_report_empty_calendar_details():
    report = build_trade_calendar_range_report(
        date(2026, 1, 1),
        date(2026, 1, 31),
        [],
        source_limit_bars=800,
    )
    assert report.status == "fail"
    assert "actual_start" not in report.details[0]
    assert report.details[0]["effective_start"] == "2026-01-01"
    assert report.details[0]["effective_end"] == "2026-01-30"


class _EmptyCalendarProvider(MarketDataProvider):
    name = "fixture_empty_calendar"

    def probe_capabilities(self):
        run_time = datetime.now(tz=SHANGHAI)
        from tradingagents.market_data.contracts import ProviderCapability

        return DataResult(
            data=[ProviderCapability(
                dataset="trade_calendar",
                endpoint="trade_cal",
                permitted=True,
                pit_level=PITLevel.PIT_REQUIRED,
                probed_at=run_time,
            )],
            status=DataStatus.OK,
            source=self.name,
            as_of=run_time,
            available_at=run_time,
            pit_level=PITLevel.PIT_REQUIRED,
        )

    def get_trade_calendar(self, start, end):
        run_time = datetime.now(tz=SHANGHAI)
        return DataResult(
            data=[],
            status=DataStatus.EMPTY,
            source=self.name,
            as_of=run_time,
            available_at=run_time,
            pit_level=PITLevel.PIT_REQUIRED,
        )


def test_sync_trade_calendar_empty_calendar_returns_blocked_not_keyerror(tmp_path):
    paths = MarketDataPaths(home_dir=tmp_path)
    repo = MarketDataRepository(paths.live_db_path, snapshot_dir=paths.snapshot_dir)
    sync = MarketDataSync(repo, _EmptyCalendarProvider(), paths)
    result = sync.sync_trade_calendar(date(2026, 1, 1), date(2026, 1, 31))
    assert result.status == SyncStatus.BLOCKED
    assert "no open days" in result.errors[0]


def test_financial_symbol_coverage_uses_target_symbol_denominator():
    report = build_financial_symbol_coverage_report(
        rows=[{"symbol": "600000"}],
        target_symbols=["600000", "000001"],
        threshold=0.0,
    )
    assert report.numerator == 1
    assert report.denominator == 2
    assert report.ratio == 0.5
    assert report.status == "pass"
