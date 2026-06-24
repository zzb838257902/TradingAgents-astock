"""Staging publish tests for financials and adjustment factors."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from tradingagents.events.contracts import (
    AnnouncementDateSource,
    EventQualityStatus,
    EventSentiment,
    EventSeverity,
    EventSymbolLink,
    EventType,
    MarketEvent,
)
from tradingagents.market_data.config import MarketDataPaths
from tradingagents.market_data.contracts import PITLevel
from tradingagents.market_data.providers.free_astock import FreeAStockProvider
from tradingagents.market_data.repository import MarketDataRepository
from tradingagents.market_data.sync import MarketDataSync, SyncStatus
from tests.market_data.test_free_astock_provider import _MockBackend
from tests.market_data.test_sync_coverage_gates import _seed_calendar

SHANGHAI = ZoneInfo("Asia/Shanghai")


def test_publish_event_bundle_staging_invisible_until_published(tmp_path):
    repo = MarketDataRepository(tmp_path / "market.duckdb")
    run_id = repo.begin_ingestion_run("market_events", {"symbols": ["600000"]})
    event = MarketEvent(
        event_id="evt-staging",
        event_type=EventType.FINANCIAL_REPORT,
        title="Report",
        published_at=datetime(2026, 4, 30, 16, 0, tzinfo=SHANGHAI),
        available_at=datetime(2026, 5, 6, 9, 30, tzinfo=SHANGHAI),
        source="fixture",
        source_record_id="staging-1",
        source_version="v1",
        content_hash="hash",
        pit_level=PITLevel.PIT_REQUIRED,
        sentiment=EventSentiment.NEUTRAL,
        severity=EventSeverity.MEDIUM,
        announcement_date_source=AnnouncementDateSource.REPORTED,
        quality_status=EventQualityStatus.VALID,
        ingested_at=datetime(2026, 6, 20, 10, 0, tzinfo=SHANGHAI),
    )
    repo.upsert_staging_event_bundle(
        run_id,
        events=[event],
        links=[EventSymbolLink(
            event_id=event.event_id,
            symbol="600000",
            role="primary",
            available_at=event.available_at,
            source="fixture",
        )],
        tags=[],
    )
    available = datetime(2026, 6, 20, 16, 0, tzinfo=SHANGHAI)
    assert repo.get_market_events(["600000"], available_before=available) == []
    version_id = repo.publish_event_bundle(run_id)
    rows = repo.get_market_events(["600000"], available_before=available)
    assert len(rows) == 1
    assert rows[0]["event_id"] == event.event_id
    published = repo.get_latest_published_version("market_events")
    assert published is not None
    assert published["version_id"] == version_id


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
