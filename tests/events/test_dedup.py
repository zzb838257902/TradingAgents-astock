"""Event deduplication tests (phase 5 Task 5)."""

from __future__ import annotations

from datetime import datetime
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
from tradingagents.events.dedup import EventBundle, deduplicate_event_bundles
from tradingagents.market_data.contracts import PITLevel

SHANGHAI = ZoneInfo("Asia/Shanghai")


def _event(event_id: str, record_id: str, title: str, *, version: str = "v1") -> MarketEvent:
    published = datetime(2026, 5, 6, 16, 0, tzinfo=SHANGHAI)
    available = datetime(2026, 5, 7, 9, 30, tzinfo=SHANGHAI)
    return MarketEvent(
        event_id=event_id,
        event_type=EventType.FINANCIAL_REPORT,
        title=title,
        published_at=published,
        available_at=available,
        source="free_astock",
        source_url=f"https://vip.stock.finance.sina.com.cn/corp/view/vCB_AllBulletinDetail.php?id={record_id}",
        source_record_id=record_id,
        source_version=version,
        content_hash=f"hash-{event_id}",
        pit_level=PITLevel.PIT_REQUIRED,
        sentiment=EventSentiment.NEUTRAL,
        severity=EventSeverity.MEDIUM,
        announcement_date_source=AnnouncementDateSource.REPORTED,
        quality_status=EventQualityStatus.VALID,
    )


def _bundle(event: MarketEvent) -> EventBundle:
    return EventBundle(
        event=event,
        links=(EventSymbolLink(
            event_id=event.event_id,
            symbol="600000",
            role="primary",
            available_at=event.available_at,
            source="free_astock",
        ),),
    )


def test_dedup_prefers_stable_source_id_over_semantic_duplicate():
    first = _bundle(_event("evt-1", "dup", "Annual report"))
    second = _bundle(_event("evt-2", "dup", "Annual report copy"))
    kept, stats = deduplicate_event_bundles([first, second])
    assert len(kept) == 1
    assert stats.physical_duplicates == 1


def test_dedup_keeps_revision_with_new_source_version():
    first = _bundle(_event("evt-old", "rev-1", "Original bulletin"))
    revised = _event("evt-new", "rev-1", "Revised bulletin").model_copy(update={
        "source_version": "v2",
        "content_hash": "hash-new",
        "supersedes_event_id": "evt-old",
    })
    kept, stats = deduplicate_event_bundles([first, _bundle(revised)])
    assert len(kept) == 2
    assert stats.physical_duplicates == 0


def test_dedup_keeps_same_title_and_time_with_different_version():
    title = "Same bulletin title"
    first = _bundle(_event("evt-old", "rev-1", title, version="v1"))
    revised = _event("evt-new", "rev-1", title, version="v2").model_copy(update={
        "content_hash": "hash-new",
        "supersedes_event_id": "evt-old",
    })
    kept, stats = deduplicate_event_bundles([first, _bundle(revised)])
    assert len(kept) == 2
    assert stats.semantic_duplicates == 0


def test_dedup_semantic_still_collapses_cross_source_same_title():
    title = "Same bulletin title"
    first = _bundle(_event("evt-a", "record-a", title, version="v1"))
    second = _event("evt-b", "record-b", title, version="v1").model_copy(update={
        "source": "other_source",
        "content_hash": "hash-b",
        "source_url": "https://vip.stock.finance.sina.com.cn/corp/view/vCB_AllBulletinDetail.php?id=b",
    })
    kept, stats = deduplicate_event_bundles([first, _bundle(second)])
    assert len(kept) == 1
    assert stats.kept == 1


def test_dedup_counts_semantic_title_time_symbol_duplicates():
    first = _bundle(_event("evt-1", "a", "Same title"))
    second = _event("evt-2", "b", "Same title")
    second = second.model_copy(update={
        "content_hash": "different-hash",
        "source_url": "https://vip.stock.finance.sina.com.cn/corp/view/vCB_OtherBulletin.php?id=b",
    })
    kept, stats = deduplicate_event_bundles([first, _bundle(second)])
    assert len(kept) == 1
    assert stats.semantic_duplicates == 1
