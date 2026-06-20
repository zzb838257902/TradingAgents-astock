"""Event sync service tests (phase 5 Task 5)."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from tradingagents.events.service import EventSyncService, EventSyncStatus
from tradingagents.market_data.config import MarketDataPaths
from tradingagents.market_data.providers.free_astock import FreeAStockProvider
from tradingagents.market_data.providers.free_astock_sources import ProviderFetchError
from tradingagents.market_data.repository import MarketDataRepository
from tests.events.test_free_event_provider import _EventBackend

SHANGHAI = ZoneInfo("Asia/Shanghai")


def _seed_calendar(repo: MarketDataRepository) -> None:
    run_id = repo.begin_ingestion_run("trade_calendar", {})
    repo.upsert_staging_trade_calendar(run_id, [
        {
            "exchange": "SSE",
            "trade_date": date(2026, 6, 5),
            "is_open": True,
            "available_at": datetime(2026, 6, 5, 9, 0, tzinfo=SHANGHAI),
            "source": "fixture",
        },
        {
            "exchange": "SSE",
            "trade_date": date(2026, 6, 8),
            "is_open": True,
            "available_at": datetime(2026, 6, 8, 9, 0, tzinfo=SHANGHAI),
            "source": "fixture",
        },
    ])
    repo.publish_dataset_version(run_id)


def test_event_sync_publishes_announcement_bundle(tmp_path):
    paths = MarketDataPaths(home_dir=tmp_path)
    repo = MarketDataRepository(paths.live_db_path, snapshot_dir=paths.snapshot_dir)
    _seed_calendar(repo)
    provider = FreeAStockProvider(backend=_EventBackend())
    service = EventSyncService(repo, provider, paths, backend=provider._backend)
    result = service.sync_announcements(
        ["600000"],
        date(2026, 6, 1),
        date(2026, 6, 30),
        as_of=datetime(2026, 6, 20, 16, 0, tzinfo=SHANGHAI),
    )
    assert result.status == EventSyncStatus.PUBLISHED
    assert result.version_id
    rows = repo.get_market_events(
        ["600000"],
        available_before=datetime(2026, 6, 20, 16, 0, tzinfo=SHANGHAI),
    )
    assert len(rows) == 1
    assert rows[0]["title"] == "2025年年度报告"
    snapshots = list(Path(paths.snapshot_dir).glob("*.json.gz"))
    assert snapshots


def test_event_sync_success_empty_publishes_empty_version(tmp_path):
    paths = MarketDataPaths(home_dir=tmp_path)
    repo = MarketDataRepository(paths.live_db_path, snapshot_dir=paths.snapshot_dir)
    _seed_calendar(repo)

    class _EmptyBackend(_EventBackend):
        def fetch_sina_bulletin_rows(self, symbol: str, page: int = 1):
            return []

    provider = FreeAStockProvider(backend=_EmptyBackend())
    service = EventSyncService(repo, provider, paths, backend=provider._backend)
    result = service.sync_announcements(
        ["600000"],
        date(2026, 6, 1),
        date(2026, 6, 30),
    )
    assert result.status == EventSyncStatus.PUBLISHED
    assert result.run_id
    assert result.version_id
    assert repo.get_latest_published_version("market_events") is not None


def test_event_sync_success_empty_does_not_block(tmp_path):
    paths = MarketDataPaths(home_dir=tmp_path)
    repo = MarketDataRepository(paths.live_db_path, snapshot_dir=paths.snapshot_dir)
    _seed_calendar(repo)

    class _EmptyBackend(_EventBackend):
        def fetch_sina_bulletin_rows(self, symbol: str, page: int = 1):
            return []

    provider = FreeAStockProvider(backend=_EmptyBackend())
    service = EventSyncService(repo, provider, paths, backend=provider._backend)
    result = service.sync_announcements(
        ["600000"],
        date(2026, 6, 1),
        date(2026, 6, 30),
    )
    assert result.status == EventSyncStatus.PUBLISHED
    assert not result.errors


def test_event_sync_network_failure_does_not_publish(tmp_path):
    paths = MarketDataPaths(home_dir=tmp_path)
    repo = MarketDataRepository(paths.live_db_path, snapshot_dir=paths.snapshot_dir)
    _seed_calendar(repo)

    class _FailingBackend(_EventBackend):
        def fetch_sina_bulletin_rows(self, symbol: str, page: int = 1):
            raise ProviderFetchError("network_error", "offline failure")

    provider = FreeAStockProvider(backend=_FailingBackend())
    service = EventSyncService(repo, provider, paths, backend=provider._backend)
    result = service.sync_announcements(
        ["600000"],
        date(2026, 6, 1),
        date(2026, 6, 30),
    )
    assert result.status == EventSyncStatus.ERROR
    rows = repo.get_market_events(
        ["600000"],
        available_before=datetime(2026, 6, 20, 16, 0, tzinfo=SHANGHAI),
    )
    assert rows == []
