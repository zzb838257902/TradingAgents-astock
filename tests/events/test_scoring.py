"""Deterministic event scoring tests (phase 5 Task 6)."""

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
    EventDataset,
    apply_same_type_day_cap,
    build_contributions,
    clip,
    dataset_subscore,
    decay_factor,
    fuse_enhanced_score,
    normalize_base_scores,
    raw_event_impact,
    recompute_event_score_from_contributions,
    score_symbol_events,
    sentiment_numeric,
    sort_enhanced_ranking,
    weighted_dataset_average,
)
from tradingagents.market_data.contracts import PITLevel

SHANGHAI = ZoneInfo("Asia/Shanghai")
SIGNAL = datetime(2026, 6, 20, 15, 30, tzinfo=SHANGHAI)


def _event(
    *,
    event_id: str = "evt-1",
    event_type: EventType = EventType.FINANCIAL_REPORT,
    sentiment: EventSentiment = EventSentiment.POSITIVE,
    severity: EventSeverity = EventSeverity.MEDIUM,
    available_at: datetime | None = datetime(2026, 6, 13, 9, 30, tzinfo=SHANGHAI),
    pit_level: PITLevel = PITLevel.BEST_EFFORT,
) -> MarketEvent:
    return MarketEvent(
        event_id=event_id,
        event_type=event_type,
        title="title",
        published_at=available_at or SIGNAL,
        available_at=available_at,
        source="fixture",
        source_record_id=event_id,
        content_hash="hash",
        pit_level=pit_level,
        sentiment=sentiment,
        severity=severity,
    )


def test_sentiment_numeric_and_raw_impact_bounds():
    assert sentiment_numeric(EventSentiment.POSITIVE) == 1.0
    assert sentiment_numeric(EventSentiment.NEGATIVE) == -1.0
    assert sentiment_numeric(EventSentiment.NEUTRAL) == 0.0
    assert sentiment_numeric(EventSentiment.UNKNOWN) == 0.0

    event = _event(
        event_type=EventType.ST_DELIST,
        sentiment=EventSentiment.NEGATIVE,
        severity=EventSeverity.CRITICAL,
    )
    assert raw_event_impact(event) == -1.0
    assert clip(2.0) == 1.0
    assert clip(-2.0) == -1.0


def test_decay_halves_at_half_life():
    half_life = 7.0
    assert decay_factor(0.0, half_life) == pytest.approx(1.0)
    assert decay_factor(half_life, half_life) == pytest.approx(0.5)
    assert decay_factor(half_life * 2, half_life) == pytest.approx(0.25)


def test_unknown_sentiment_keeps_zero_impact_but_not_neutral_label():
    event = _event(sentiment=EventSentiment.UNKNOWN)
    contributions = build_contributions(
        [event],
        signal_time=SIGNAL,
        half_life_days=7.0,
    )
    assert contributions[0].raw_impact == 0.0
    assert contributions[0].sentiment == EventSentiment.UNKNOWN


def test_same_type_same_day_cap_scales_weighted_impact():
    same_day = datetime(2026, 6, 20, 10, 0, tzinfo=SHANGHAI)
    events = [
        _event(
            event_id="a",
            event_type=EventType.BUYBACK,
            sentiment=EventSentiment.POSITIVE,
            severity=EventSeverity.HIGH,
            available_at=same_day,
        ),
        _event(
            event_id="b",
            event_type=EventType.BUYBACK,
            sentiment=EventSentiment.POSITIVE,
            severity=EventSeverity.HIGH,
            available_at=same_day,
        ),
    ]
    contributions = build_contributions(events, signal_time=SIGNAL, half_life_days=7.0)
    total_abs = sum(abs(item.weighted_impact) for item in contributions)
    assert total_abs == pytest.approx(1.0, abs=1e-6)


def test_dataset_subscore_uses_available_datasets_only():
    news = _event(
        event_id="news-1",
        event_type=EventType.NEWS,
        sentiment=EventSentiment.POSITIVE,
        severity=EventSeverity.HIGH,
    )
    contributions = build_contributions([news], signal_time=SIGNAL, half_life_days=7.0)
    assert dataset_subscore(contributions, EventDataset.EVENT_NEWS) is not None
    assert dataset_subscore(contributions, EventDataset.EVENT_FUND_FLOW) is None

    weighted = weighted_dataset_average({
        EventDataset.OFFICIAL_ANNOUNCEMENTS: None,
        EventDataset.EVENT_NEWS: dataset_subscore(contributions, EventDataset.EVENT_NEWS),
        EventDataset.EVENT_FUND_FLOW: None,
        EventDataset.EVENT_HOT_TOPICS: None,
    })
    assert weighted is not None


def test_all_datasets_missing_degrades_without_enhanced_score():
    result = score_symbol_events(
        "600000",
        [],
        [],
        base_score=0.8,
        base_scores={"600000": 0.8, "600001": 0.2},
        signal_time=SIGNAL,
        half_life_days=7.0,
        event_weight=0.20,
    )
    assert result.event_score is None
    assert result.enhanced_score is None
    assert "all_event_datasets_missing" in result.degradation_reasons


def test_normalize_base_scores_zero_variance_defaults_to_half():
    assert normalize_base_scores({"A": 1.0, "B": 1.0}) == {"A": 0.5, "B": 0.5}


def test_fuse_enhanced_score_formula():
    enhanced = fuse_enhanced_score(0.25, 0.75, event_weight=0.20)
    assert enhanced == pytest.approx(0.25 * 0.80 + 0.75 * 0.20)


def test_contribution_reversibility():
    event = _event(
        event_type=EventType.BUYBACK,
        sentiment=EventSentiment.POSITIVE,
        severity=EventSeverity.HIGH,
    )
    contributions = build_contributions([event], signal_time=SIGNAL, half_life_days=7.0)
    event_score, event_component = recompute_event_score_from_contributions(
        contributions,
        soft_penalty=0.0,
    )
    result = score_symbol_events(
        "600000",
        [event],
        [],
        base_score=0.6,
        base_scores={"600000": 0.6},
        signal_time=SIGNAL,
        half_life_days=7.0,
        event_weight=0.20,
    )
    assert event_score == pytest.approx(result.event_score)
    assert event_component == pytest.approx(result.event_component)


def test_sort_enhanced_ranking_tie_breakers():
    results = [
        score_symbol_events(
            "600002",
            [_event(event_id="e2", event_type=EventType.NEWS, sentiment=EventSentiment.POSITIVE)],
            [],
            base_score=0.5,
            base_scores={"600001": 0.5, "600002": 0.5},
            signal_time=SIGNAL,
            half_life_days=7.0,
            event_weight=0.20,
        ),
        score_symbol_events(
            "600001",
            [_event(event_id="e1", event_type=EventType.NEWS, sentiment=EventSentiment.POSITIVE)],
            [],
            base_score=0.7,
            base_scores={"600001": 0.7, "600002": 0.5},
            signal_time=SIGNAL,
            half_life_days=7.0,
            event_weight=0.20,
        ),
    ]
    ranked = sort_enhanced_ranking(results)
    assert ranked[0].symbol == "600001"
    assert ranked[1].symbol == "600002"


def test_apply_same_type_day_cap_noop_when_under_limit():
    contributions = build_contributions(
        [_event(event_id="only")],
        signal_time=SIGNAL,
        half_life_days=7.0,
    )
    capped = apply_same_type_day_cap(contributions)
    assert capped[0].weighted_impact == pytest.approx(contributions[0].weighted_impact)
