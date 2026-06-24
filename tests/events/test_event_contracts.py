"""Event contract model tests (phase 5 Task 2)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from tradingagents.events.contracts import (
    ALL_EVENT_TYPES,
    AnnouncementDateSource,
    EventQualityStatus,
    EventSentiment,
    EventSeverity,
    EventSymbolLink,
    EventType,
    MarketEvent,
    assert_event_pit_valid,
    stable_event_id,
)
from tradingagents.market_data.contracts import PITLevel

SHANGHAI = ZoneInfo("Asia/Shanghai")


def _sample_event(**overrides) -> dict:
    base = {
        "event_id": "evt-1",
        "event_type": EventType.FINANCIAL_REPORT,
        "title": "2025 annual report",
        "summary": "summary",
        "published_at": datetime(2026, 4, 30, 16, 0, tzinfo=SHANGHAI),
        "available_at": datetime(2026, 5, 6, 9, 30, tzinfo=SHANGHAI),
        "source": "free_astock",
        "source_url": "https://example.com/a/1",
        "source_record_id": "12374895",
        "source_version": "v1",
        "content_hash": "abc123",
        "pit_level": PITLevel.PIT_REQUIRED,
        "sentiment": EventSentiment.NEUTRAL,
        "severity": EventSeverity.MEDIUM,
        "announcement_date_source": AnnouncementDateSource.REPORTED,
        "raw_snapshot_id": "snap-1",
        "dataset_version_id": None,
        "ingestion_run_id": None,
        "quality_status": EventQualityStatus.VALID,
        "supersedes_event_id": None,
        "ingested_at": datetime(2026, 6, 20, 10, 0, tzinfo=SHANGHAI),
    }
    base.update(overrides)
    return base


def test_event_type_enum_covers_phase5_catalog():
    expected = {
        EventType.FINANCIAL_REPORT,
        EventType.EARNINGS_FORECAST,
        EventType.DIVIDEND,
        EventType.BUYBACK,
        EventType.HOLDING_CHANGE,
        EventType.PLEDGE,
        EventType.MAJOR_CONTRACT,
        EventType.RESTRUCTURING,
        EventType.INVESTIGATION,
        EventType.PENALTY,
        EventType.ST_DELIST,
        EventType.SUSPEND_RESUME,
        EventType.LOCKUP,
        EventType.MANAGEMENT_CHANGE,
        EventType.NEWS,
        EventType.FUND_FLOW,
        EventType.HOT_TOPIC,
    }
    assert expected == set(ALL_EVENT_TYPES)
    assert len(ALL_EVENT_TYPES) == 17


def test_market_event_normalizes_naive_datetimes_to_shanghai():
    event = MarketEvent.model_validate(_sample_event(
        published_at=datetime(2026, 4, 30, 16, 0),
    ))
    assert event.published_at.tzinfo is not None
    assert event.published_at.utcoffset().total_seconds() == 8 * 3600


def test_pit_required_rejects_missing_available_at():
    event = MarketEvent.model_validate(_sample_event())
    event = event.model_copy(update={"available_at": None})
    with pytest.raises(ValueError, match="available_at"):
        assert_event_pit_valid(event)


def test_pit_required_rejects_available_at_after_signal_time():
    event = MarketEvent.model_validate(_sample_event())
    signal_time = datetime(2026, 5, 1, 9, 0, tzinfo=SHANGHAI)
    with pytest.raises(ValueError, match="signal_time"):
        assert_event_pit_valid(event, signal_time=signal_time)


def test_revision_chain_preserves_supersedes_link():
    original = MarketEvent.model_validate(_sample_event(event_id="evt-old"))
    revised = MarketEvent.model_validate(_sample_event(
        event_id="evt-new",
        source_version="v2",
        supersedes_event_id=original.event_id,
        available_at=datetime(2026, 5, 7, 9, 30, tzinfo=SHANGHAI),
    ))
    assert revised.supersedes_event_id == "evt-old"
    assert revised.available_at > original.available_at


def test_stable_event_id_uses_source_record_and_version():
    event = MarketEvent.model_validate(_sample_event())
    assert stable_event_id(event) == "free_astock:12374895:v1"


def test_event_symbol_link_is_separate_from_event_body():
    EventSymbolLink(
        event_id="evt-1",
        symbol="600000",
        role="primary",
        available_at=datetime(2026, 5, 6, 9, 30, tzinfo=SHANGHAI),
        source="free_astock",
    )
    event = MarketEvent.model_validate(_sample_event())
    assert "600000" not in event.model_dump()


def test_rejected_quality_status_fails_pit_gate():
    event = MarketEvent.model_validate(_sample_event(
        quality_status=EventQualityStatus.REJECTED,
    ))
    with pytest.raises(ValueError, match="quality_status"):
        assert_event_pit_valid(event)
