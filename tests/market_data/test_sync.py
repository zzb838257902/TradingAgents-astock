"""Offline sync and quality tests for phase 4.3a."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from tradingagents.market_data.config import MarketDataPaths
from tradingagents.market_data.contracts import (
    DataResult,
    DataStatus,
    PITLevel,
    ProviderCapability,
    SecurityRecord,
)
from tradingagents.market_data.providers.tushare import TushareProvider
from tradingagents.market_data.quality import (
    assess_daily_bar_quality,
    build_daily_completeness_report,
)
from tradingagents.market_data.repository import MarketDataRepository
from tradingagents.market_data.sync import MarketDataSync, SyncStatus

SHANGHAI = ZoneInfo("Asia/Shanghai")


class _MockClient:
    def __init__(self, handlers: dict[str, object]):
        self._handlers = handlers

    def query(self, api_name: str, fields: str = "", **params):
        handler = self._handlers.get(api_name)
        if handler is None:
            return pd.DataFrame()
        return handler(fields=fields, **params)


def _mock_provider() -> TushareProvider:
    handlers = {
        "stock_basic": lambda **_kwargs: pd.DataFrame([
            {
                "ts_code": "600000.SH",
                "name": "浦发银行",
                "list_date": "19991110",
                "delist_date": None,
                "market": "主板",
                "list_status": "L",
            },
            {
                "ts_code": "000001.SZ",
                "name": "平安银行",
                "list_date": "19910403",
                "delist_date": None,
                "market": "主板",
                "list_status": "L",
            },
        ]),
        "trade_cal": lambda **_kwargs: pd.DataFrame([
            {"exchange": "SSE", "cal_date": "20260102", "is_open": 1},
            {"exchange": "SSE", "cal_date": "20260103", "is_open": 1},
        ]),
        "daily": lambda **_kwargs: pd.DataFrame([
            {
                "ts_code": "600000.SH",
                "trade_date": "20260102",
                "open": 10.0,
                "high": 10.5,
                "low": 9.8,
                "close": 10.2,
                "pre_close": 10.0,
                "vol": 1000.0,
                "amount": 10200.0,
            },
            {
                "ts_code": "000001.SZ",
                "trade_date": "20260102",
                "open": 20.0,
                "high": 20.5,
                "low": 19.8,
                "close": 20.2,
                "pre_close": 20.0,
                "vol": 2000.0,
                "amount": 40400.0,
            },
        ]),
    }
    return TushareProvider(token="test-token", client=_MockClient(handlers))


def test_assess_daily_bar_quality_rejects_invalid_ohlc():
    issues = assess_daily_bar_quality([{
        "symbol": "600000",
        "trade_date": date(2026, 1, 2),
        "open": 10.0,
        "high": 9.0,
        "low": 8.0,
        "close": 10.2,
        "volume": 1000.0,
        "amount": 10200.0,
        "available_at": datetime(2026, 1, 2, 16, 0, tzinfo=SHANGHAI),
    }])
    assert issues
    assert issues[0].rule == "ohlc_invalid"


def test_coverage_report_machine_readable_fields():
    report = build_daily_completeness_report(
        numerator=2,
        denominator=2,
        threshold=0.995,
        exclusions=[],
    )
    assert report.status == "pass"
    assert report.numerator == 2
    assert report.denominator == 2
    assert report.ratio == pytest.approx(1.0)
    assert report.threshold == 0.995


def test_sync_security_master_publishes_to_live_db(tmp_path):
    paths = MarketDataPaths(home_dir=tmp_path)
    repo = MarketDataRepository(paths.live_db_path, snapshot_dir=paths.snapshot_dir)
    sync = MarketDataSync(repo, _mock_provider(), paths)
    result = sync.sync_security_master(as_of=date(2026, 1, 2))
    assert result.status == SyncStatus.PUBLISHED
    rows = repo.get_effective_securities(
        date(2026, 1, 2),
        datetime(2026, 1, 2, 16, 0, tzinfo=SHANGHAI),
    )
    assert {row.symbol for row in rows} == {"600000", "000001"}
    assert repo.get_latest_published_version("security_master") is not None


def test_sync_trade_calendar_publishes_open_days(tmp_path):
    paths = MarketDataPaths(home_dir=tmp_path)
    repo = MarketDataRepository(paths.live_db_path, snapshot_dir=paths.snapshot_dir)
    sync = MarketDataSync(repo, _mock_provider(), paths)
    result = sync.sync_trade_calendar(date(2026, 1, 2), date(2026, 1, 3))
    assert result.status == SyncStatus.PUBLISHED
    days = repo.get_trade_calendar(
        "SSE",
        date(2026, 1, 1),
        date(2026, 1, 31),
        datetime(2026, 1, 31, 16, 0, tzinfo=SHANGHAI),
    )
    assert len(days) == 2


def test_sync_daily_full_market_single_day(tmp_path):
    paths = MarketDataPaths(home_dir=tmp_path)
    repo = MarketDataRepository(paths.live_db_path, snapshot_dir=paths.snapshot_dir)
    sync = MarketDataSync(repo, _mock_provider(), paths)
    sync.sync_security_master(as_of=date(2026, 1, 2))
    result = sync.sync_daily(trade_date=date(2026, 1, 2))
    assert result.status == SyncStatus.PUBLISHED
    assert result.coverage_reports["daily_completeness"].status == "pass"
    rows = repo.get_daily_bars(
        ["600000", "000001"],
        end=date(2026, 1, 2),
        available_before=datetime(2026, 1, 2, 18, 0, tzinfo=SHANGHAI),
    )
    assert len(rows) == 2


def test_sync_blocks_when_provider_returns_error(tmp_path):
    class _ErrorProvider(TushareProvider):
        def probe_capabilities(self):
            run_time = datetime.now(tz=SHANGHAI)
            return DataResult(
                data=[ProviderCapability(
                    dataset="security_master",
                    endpoint="stock_basic",
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

        def list_securities(self, as_of: date) -> DataResult[list[SecurityRecord]]:
            return DataResult(
                data=None,
                status=DataStatus.NETWORK_ERROR,
                source=self.name,
                as_of=datetime.now(tz=SHANGHAI),
                available_at=datetime.now(tz=SHANGHAI),
                pit_level=PITLevel.PIT_REQUIRED,
                errors=["network down"],
            )

    paths = MarketDataPaths(home_dir=tmp_path)
    repo = MarketDataRepository(paths.live_db_path, snapshot_dir=paths.snapshot_dir)
    sync = MarketDataSync(repo, _ErrorProvider(token="x"), paths)
    result = sync.sync_security_master(as_of=date(2026, 1, 2))
    assert result.status == SyncStatus.ERROR
    assert "network down" in result.errors[0]


def test_sync_is_idempotent_for_same_input(tmp_path):
    paths = MarketDataPaths(home_dir=tmp_path)
    repo = MarketDataRepository(paths.live_db_path, snapshot_dir=paths.snapshot_dir)
    sync = MarketDataSync(repo, _mock_provider(), paths)
    sync.sync_security_master(as_of=date(2026, 1, 2))
    first = sync.sync_daily(trade_date=date(2026, 1, 2))
    second = sync.sync_daily(trade_date=date(2026, 1, 2))
    assert first.status == SyncStatus.PUBLISHED
    assert second.status == SyncStatus.PUBLISHED
    assert first.content_hash == second.content_hash


def test_probe_capabilities_persisted(tmp_path):
    paths = MarketDataPaths(home_dir=tmp_path)
    repo = MarketDataRepository(paths.live_db_path, snapshot_dir=paths.snapshot_dir)
    sync = MarketDataSync(repo, _mock_provider(), paths)
    result = sync.probe_capabilities()
    assert result.status == SyncStatus.PUBLISHED
    stored = repo.get_capability_probe()
    assert stored is not None
    assert "security_master" in stored
