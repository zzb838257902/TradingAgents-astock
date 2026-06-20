"""Event risk rule tests (phase 5 Task 6)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from tradingagents.events.contracts import (
    EventSentiment,
    EventSeverity,
    EventType,
    MarketEvent,
)
from tradingagents.events.scoring import (
    SoftRiskTag,
    hard_risk_flags,
    is_hard_risk_event,
    score_symbol_events,
    soft_risk_penalty,
)
from tradingagents.market_data.contracts import PITLevel

SHANGHAI = ZoneInfo("Asia/Shanghai")
SIGNAL = datetime(2026, 6, 20, 15, 30, tzinfo=SHANGHAI)


def _event(
    *,
    event_id: str,
    event_type: EventType,
    severity: EventSeverity = EventSeverity.CRITICAL,
    pit_level: PITLevel = PITLevel.PIT_REQUIRED,
    sentiment: EventSentiment = EventSentiment.NEGATIVE,
) -> MarketEvent:
    available = datetime(2026, 6, 10, 9, 30, tzinfo=SHANGHAI)
    return MarketEvent(
        event_id=event_id,
        event_type=event_type,
        title="risk",
        published_at=available,
        available_at=available,
        source="fixture",
        source_record_id=event_id,
        content_hash="hash",
        pit_level=pit_level,
        sentiment=sentiment,
        severity=severity,
    )


@pytest.mark.parametrize(
    ("event_type", "expected"),
    [
        (EventType.ST_DELIST, True),
        (EventType.INVESTIGATION, True),
        (EventType.PENALTY, True),
        (EventType.SUSPEND_RESUME, True),
        (EventType.PLEDGE, False),
        (EventType.HOLDING_CHANGE, False),
        (EventType.NEWS, False),
    ],
)
def test_hard_risk_only_for_pit_required_critical_official_types(
    event_type: EventType,
    expected: bool,
):
    event = _event(event_id="hard", event_type=event_type)
    assert is_hard_risk_event(event) is expected


def test_hard_risk_requires_pit_required_and_critical():
    event = _event(
        event_id="soft-penalty",
        event_type=EventType.PENALTY,
        severity=EventSeverity.HIGH,
    )
    assert is_hard_risk_event(event) is False

    event = _event(
        event_id="best-effort",
        event_type=EventType.ST_DELIST,
        pit_level=PITLevel.BEST_EFFORT,
    )
    assert is_hard_risk_event(event) is False


def test_soft_risk_penalty_dedupes_category_and_caps():
    tags = [
        SoftRiskTag(category="pledge", severity=EventSeverity.LOW),
        SoftRiskTag(category="pledge", severity=EventSeverity.HIGH),
        SoftRiskTag(category="holding_change", severity=EventSeverity.MEDIUM),
    ]
    assert soft_risk_penalty(tags) == pytest.approx(0.45)


def test_soft_risk_penalty_ignores_critical_tags():
    tags = [
        SoftRiskTag(category="investigation", severity=EventSeverity.CRITICAL),
        SoftRiskTag(category="pledge", severity=EventSeverity.LOW),
    ]
    assert soft_risk_penalty(tags) == pytest.approx(0.05)


def test_hard_risk_excludes_symbol_from_enhanced_ranking():
    event = _event(event_id="delist", event_type=EventType.ST_DELIST)
    assert hard_risk_flags([event])

    result = score_symbol_events(
        "600000",
        [event],
        [SoftRiskTag(category="pledge", severity=EventSeverity.LOW)],
        base_score=0.9,
        base_scores={"600000": 0.9},
        signal_time=SIGNAL,
        half_life_days=7.0,
        event_weight=0.20,
        hard_risk_filter=True,
    )
    assert result.hard_risk_excluded is True
    assert result.enhanced_score is None
    assert "hard_risk_filter_excluded" in result.degradation_reasons


def test_ordinary_negative_events_only_apply_soft_penalty():
    pledge = _event(
        event_id="pledge",
        event_type=EventType.PLEDGE,
        severity=EventSeverity.MEDIUM,
        pit_level=PITLevel.PIT_REQUIRED,
    )
    news = _event(
        event_id="news",
        event_type=EventType.NEWS,
        severity=EventSeverity.HIGH,
        pit_level=PITLevel.BEST_EFFORT,
        sentiment=EventSentiment.NEGATIVE,
    )
    assert is_hard_risk_event(pledge) is False
    assert is_hard_risk_event(news) is False

    result = score_symbol_events(
        "600000",
        [pledge, news],
        [SoftRiskTag(category="pledge", severity=EventSeverity.MEDIUM)],
        base_score=0.5,
        base_scores={"600000": 0.5},
        signal_time=SIGNAL,
        half_life_days=7.0,
        event_weight=0.20,
    )
    assert result.hard_risk_excluded is False
    assert result.enhanced_score is not None
    assert result.soft_risk_penalty == pytest.approx(0.15)
