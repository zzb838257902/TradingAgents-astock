"""Event enrichment domain contracts (phase 5)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Iterable

from pydantic import BaseModel, ConfigDict, field_validator

from tradingagents.market_data.contracts import PITLevel
from tradingagents.market_data.market_hours import ensure_aware_shanghai


class EventType(StrEnum):
    FINANCIAL_REPORT = "financial_report"
    EARNINGS_FORECAST = "earnings_forecast"
    DIVIDEND = "dividend"
    BUYBACK = "buyback"
    HOLDING_CHANGE = "holding_change"
    PLEDGE = "pledge"
    MAJOR_CONTRACT = "major_contract"
    RESTRUCTURING = "restructuring"
    INVESTIGATION = "investigation"
    PENALTY = "penalty"
    ST_DELIST = "st_delist"
    SUSPEND_RESUME = "suspend_resume"
    LOCKUP = "lockup"
    MANAGEMENT_CHANGE = "management_change"
    NEWS = "news"
    FUND_FLOW = "fund_flow"
    HOT_TOPIC = "hot_topic"


ALL_EVENT_TYPES: tuple[EventType, ...] = tuple(EventType)


class EventSentiment(StrEnum):
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"
    UNKNOWN = "unknown"


class EventSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class EventQualityStatus(StrEnum):
    VALID = "valid"
    WARN = "warn"
    REJECTED = "rejected"


class AnnouncementDateSource(StrEnum):
    REPORTED = "reported"
    REGULATORY_DEADLINE = "regulatory_deadline"


class MarketEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str
    event_type: EventType
    title: str
    summary: str = ""
    published_at: datetime
    available_at: datetime | None = None
    source: str
    source_url: str = ""
    source_record_id: str
    source_version: str = ""
    content_hash: str
    pit_level: PITLevel
    sentiment: EventSentiment = EventSentiment.UNKNOWN
    severity: EventSeverity = EventSeverity.MEDIUM
    announcement_date_source: AnnouncementDateSource | None = None
    raw_snapshot_id: str | None = None
    dataset_version_id: str | None = None
    ingestion_run_id: str | None = None
    quality_status: EventQualityStatus = EventQualityStatus.VALID
    supersedes_event_id: str | None = None
    ingested_at: datetime | None = None

    @field_validator("published_at", "available_at", "ingested_at")
    @classmethod
    def _aware_shanghai(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return ensure_aware_shanghai(value)


class EventSymbolLink(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str
    symbol: str
    role: str = "primary"
    available_at: datetime
    source: str

    @field_validator("available_at")
    @classmethod
    def _aware_shanghai(cls, value: datetime) -> datetime:
        return ensure_aware_shanghai(value)


def stable_event_id(event: MarketEvent) -> str:
    version = event.source_version or "v0"
    return f"{event.source}:{event.source_record_id}:{version}"


def assert_event_pit_valid(
    event: MarketEvent,
    *,
    signal_time: datetime | None = None,
) -> None:
    if event.quality_status == EventQualityStatus.REJECTED:
        raise ValueError("quality_status REJECTED events are not readable")
    if event.pit_level == PITLevel.PIT_REQUIRED and event.available_at is None:
        raise ValueError("pit_required event requires available_at")
    if signal_time is not None and event.available_at is not None:
        signal = ensure_aware_shanghai(signal_time)
        available = ensure_aware_shanghai(event.available_at)
        if available > signal:
            raise ValueError("available_at must be <= signal_time for PIT reads")


def iter_revision_chain(events: Iterable[MarketEvent], head_event_id: str) -> list[MarketEvent]:
    items = list(events)
    by_id = {event.event_id: event for event in items}
    chain: list[MarketEvent] = []
    current_id: str | None = head_event_id
    seen: set[str] = set()
    while current_id and current_id not in seen:
        seen.add(current_id)
        event = by_id.get(current_id)
        if event is None:
            break
        chain.append(event)
        predecessors = [
            item.event_id
            for item in items
            if item.supersedes_event_id == current_id
        ]
        current_id = predecessors[0] if predecessors else None
    return chain
