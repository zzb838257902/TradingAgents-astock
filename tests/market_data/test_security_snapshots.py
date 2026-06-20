"""Security master daily snapshot tests."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from tradingagents.market_data.contracts import SecurityRecord
from tradingagents.market_data.repository import MarketDataRepository
from tradingagents.market_data.sync import MarketDataSync, SyncStatus
from tests.market_data.test_free_astock_provider import _MockBackend
from tradingagents.market_data.config import MarketDataPaths
from tradingagents.market_data.providers.free_astock import FreeAStockProvider

SHANGHAI = ZoneInfo("Asia/Shanghai")


def test_security_master_snapshot_round_trip(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "tradingagents.market_data.sync_policy.shanghai_today",
        lambda: date(2026, 1, 2),
    )
    paths = MarketDataPaths(home_dir=tmp_path)
    repo = MarketDataRepository(paths.live_db_path, snapshot_dir=paths.snapshot_dir)
    provider = FreeAStockProvider(backend=_MockBackend())
    sync = MarketDataSync(repo, provider, paths)
    sync.probe_capabilities()
    as_of = date(2026, 1, 2)
    result = sync.sync_security_master(as_of=as_of)
    assert result.status == SyncStatus.PUBLISHED
    assert repo.has_security_snapshot_on(as_of)
    assert repo.get_latest_security_snapshot_on_or_before(as_of) == as_of
    rows = repo.get_effective_securities_from_snapshot(
        as_of,
        as_of,
        datetime(2026, 1, 2, 16, 0, tzinfo=SHANGHAI),
    )
    assert {row.symbol for row in rows} == {"600000", "000001"}


def test_upsert_security_master_snapshot_direct(tmp_path):
    repo = MarketDataRepository(tmp_path / "market.duckdb")
    snapshot_date = date(2026, 1, 2)
    repo.upsert_security_master_snapshot(snapshot_date, [
        SecurityRecord(
            symbol="600001",
            name="测试",
            board="main",
            valid_from=date(2020, 1, 1),
            valid_to=None,
            list_date=date(2020, 1, 1),
            delist_date=None,
            status="L",
            st_flag=False,
            available_at=datetime(2020, 1, 1, 9, 0, tzinfo=SHANGHAI),
            source="test",
        )
    ])
    assert repo.list_security_snapshot_dates() == [snapshot_date]
