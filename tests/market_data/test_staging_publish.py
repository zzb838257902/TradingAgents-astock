"""Staging publish tests for financials and adjustment factors."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from tradingagents.market_data.config import MarketDataPaths
from tradingagents.market_data.providers.free_astock import FreeAStockProvider
from tradingagents.market_data.repository import MarketDataRepository
from tradingagents.market_data.sync import MarketDataSync, SyncStatus
from tests.market_data.test_free_astock_provider import _MockBackend
from tests.market_data.test_sync_coverage_gates import _seed_calendar

SHANGHAI = ZoneInfo("Asia/Shanghai")


def test_sync_financials_publishes_version_id(tmp_path):
    paths = MarketDataPaths(home_dir=tmp_path)
    repo = MarketDataRepository(paths.live_db_path, snapshot_dir=paths.snapshot_dir)
    _seed_calendar(repo)
    provider = FreeAStockProvider(backend=_MockBackend())
    sync = MarketDataSync(repo, provider, paths)
    sync.probe_capabilities()
    as_of = datetime(2026, 1, 5, 16, 0, tzinfo=SHANGHAI)
    result = sync.sync_financials(as_of, symbols=["600000"])
    assert result.status == SyncStatus.PUBLISHED
    assert result.version_id
    rows = repo.get_financials(["600000"], as_of)
    assert rows
    version_row = repo.connection.execute(
        "SELECT status FROM dataset_versions WHERE version_id = ?",
        [result.version_id],
    ).fetchone()
    assert version_row is not None
    assert version_row[0] == "PUBLISHED"


def test_sync_adjustment_factors_publishes_version_id(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "tradingagents.market_data.sync_policy.shanghai_today",
        lambda: date(2026, 1, 2),
    )
    paths = MarketDataPaths(home_dir=tmp_path)
    repo = MarketDataRepository(paths.live_db_path, snapshot_dir=paths.snapshot_dir)
    provider = FreeAStockProvider(backend=_MockBackend())
    sync = MarketDataSync(repo, provider, paths)
    result = sync.sync_adjustment_factors(symbols=["600000"], as_of=date(2026, 1, 2))
    assert result.status == SyncStatus.PUBLISHED
    assert result.version_id
    factors = repo.get_adjustment_factors(
        ["600000"],
        end=date(2026, 1, 2),
        available_before=datetime(2026, 1, 2, 16, 0, tzinfo=SHANGHAI),
    )
    assert factors
    assert factors[0]["source"] == "free_astock"
