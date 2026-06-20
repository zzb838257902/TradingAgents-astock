"""Sync tests for default free provider."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from tradingagents.market_data.config import MarketDataPaths
from tradingagents.market_data.providers.free_astock import FreeAStockProvider
from tradingagents.market_data.repository import MarketDataRepository
from tradingagents.market_data.sync import MarketDataSync, SyncStatus
from tests.market_data.test_free_astock_provider import _MockBackend

SHANGHAI = ZoneInfo("Asia/Shanghai")


def test_free_provider_sync_security_master_publishes(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "tradingagents.market_data.sync_policy.shanghai_today",
        lambda: date(2026, 1, 2),
    )
    paths = MarketDataPaths(home_dir=tmp_path)
    repo = MarketDataRepository(paths.live_db_path, snapshot_dir=paths.snapshot_dir)
    provider = FreeAStockProvider(backend=_MockBackend())
    sync = MarketDataSync(repo, provider, paths)
    sync.probe_capabilities()
    result = sync.sync_security_master(as_of=date(2026, 1, 2))
    assert result.status == SyncStatus.PUBLISHED
    rows = repo.get_effective_securities(
        date(2026, 1, 2),
        datetime(2026, 1, 2, 16, 0, tzinfo=SHANGHAI),
    )
    assert {row.symbol for row in rows} == {"600000", "000001"}


def test_free_provider_sync_rejects_historical_security_master(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "tradingagents.market_data.sync_policy.shanghai_today",
        lambda: date(2026, 6, 19),
    )
    paths = MarketDataPaths(home_dir=tmp_path)
    repo = MarketDataRepository(paths.live_db_path, snapshot_dir=paths.snapshot_dir)
    provider = FreeAStockProvider(backend=_MockBackend())
    sync = MarketDataSync(repo, provider, paths)
    sync.probe_capabilities()
    result = sync.sync_security_master(as_of=date(2025, 1, 2))
    assert result.status == SyncStatus.BLOCKED
