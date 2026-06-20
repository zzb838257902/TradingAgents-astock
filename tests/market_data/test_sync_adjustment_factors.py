"""Sync adjustment factors via free provider."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from tradingagents.market_data.config import MarketDataPaths
from tradingagents.market_data.providers.free_astock import FreeAStockProvider
from tradingagents.market_data.repository import MarketDataRepository
from tradingagents.market_data.sync import MarketDataSync, SyncStatus
from tests.market_data.test_free_astock_provider import _MockBackend

SHANGHAI = ZoneInfo("Asia/Shanghai")


def test_sync_adjustment_factors_from_xdxr(tmp_path):
    paths = MarketDataPaths(home_dir=tmp_path)
    repo = MarketDataRepository(paths.live_db_path, snapshot_dir=paths.snapshot_dir)
    provider = FreeAStockProvider(backend=_MockBackend())
    sync = MarketDataSync(repo, provider, paths)
    result = sync.sync_adjustment_factors(symbols=["600000"], as_of=date(2026, 1, 2))
    assert result.status == SyncStatus.PUBLISHED
    factors = repo.get_adjustment_factors(
        ["600000"],
        end=date(2026, 1, 2),
        available_before=datetime(2026, 1, 2, 16, 0, tzinfo=SHANGHAI),
    )
    assert factors
