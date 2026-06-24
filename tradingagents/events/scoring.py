"""Deterministic explainable event scoring (phase 5 Task 6)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Iterable

from tradingagents.events.contracts import (
    EventSentiment,
    EventSeverity,
    EventType,
    MarketEvent,
)
from tradingagents.market_data.contracts import PITLevel
from tradingagents.market_data.market_hours import ensure_aware_shanghai

LN2 = math.log(2)


class EventDataset(StrEnum):
    OFFICIAL_ANNOUNCEMENTS = "official_announcements"
    EVENT_NEWS = "event_news"
    EVENT_FUND_FLOW = "event_fund_flow"
    EVENT_HOT_TOPICS = "event_hot_topics"


DATASET_WEIGHTS: dict[EventDataset, float] = {
    EventDataset.OFFICIAL_ANNOUNCEMENTS: 0.45,
    EventDataset.EVENT_NEWS: 0.20,
    EventDataset.EVENT_FUND_FLOW: 0.20,
    EventDataset.EVENT_HOT_TOPICS: 0.15,
}

TYPE_MAGNITUDE: dict[EventType, float] = {
    EventType.FINANCIAL_REPORT: 0.50,
    EventType.EARNINGS_FORECAST: 0.60,
    EventType.DIVIDEND: 0.35,
    EventType.BUYBACK: 0.65,
    EventType.HOLDING_CHANGE: 0.50,
    EventType.PLEDGE: 0.45,
    EventType.MAJOR_CONTRACT: 0.45,
    EventType.RESTRUCTURING: 0.70,
    EventType.INVESTIGATION: 0.90,
    EventType.PENALTY: 0.80,
    EventType.ST_DELIST: 1.00,
    EventType.SUSPEND_RESUME: 0.60,
    EventType.LOCKUP: 0.35,
    EventType.MANAGEMENT_CHANGE: 0.30,
    EventType.NEWS: 0.30,
    EventType.FUND_FLOW: 0.40,
    EventType.HOT_TOPIC: 0.25,
}

EVENT_TYPE_DATASET: dict[EventType, EventDataset] = {
    EventType.FINANCIAL_REPORT: EventDataset.OFFICIAL_ANNOUNCEMENTS,
    EventType.EARNINGS_FORECAST: EventDataset.OFFICIAL_ANNOUNCEMENTS,
    EventType.DIVIDEND: EventDataset.OFFICIAL_ANNOUNCEMENTS,
    EventType.BUYBACK: EventDataset.OFFICIAL_ANNOUNCEMENTS,
    EventType.HOLDING_CHANGE: EventDataset.OFFICIAL_ANNOUNCEMENTS,
    EventType.PLEDGE: EventDataset.OFFICIAL_ANNOUNCEMENTS,
    EventType.MAJOR_CONTRACT: EventDataset.OFFICIAL_ANNOUNCEMENTS,
    EventType.RESTRUCTURING: EventDataset.OFFICIAL_ANNOUNCEMENTS,
    EventType.INVESTIGATION: EventDataset.OFFICIAL_ANNOUNCEMENTS,
    EventType.PENALTY: EventDataset.OFFICIAL_ANNOUNCEMENTS,
    EventType.ST_DELIST: EventDataset.OFFICIAL_ANNOUNCEMENTS,
    EventType.SUSPEND_RESUME: EventDataset.OFFICIAL_ANNOUNCEMENTS,
    EventType.LOCKUP: EventDataset.OFFICIAL_ANNOUNCEMENTS,
    EventType.MANAGEMENT_CHANGE: EventDataset.OFFICIAL_ANNOUNCEMENTS,
    EventType.NEWS: EventDataset.EVENT_NEWS,
    EventType.FUND_FLOW: EventDataset.EVENT_FUND_FLOW,
    EventType.HOT_TOPIC: EventDataset.EVENT_HOT_TOPICS,
}

SEVERITY_MULTIPLIER: dict[EventSeverity, float] = {
    EventSeverity.LOW: 0.50,
    EventSeverity.MEDIUM: 0.75,
    EventSeverity.HIGH: 1.00,
    EventSeverity.CRITICAL: 1.00,
}

SOFT_RISK_SEVERITY_PENALTY: dict[EventSeverity, float] = {
    EventSeverity.LOW: 0.05,
    EventSeverity.MEDIUM: 0.15,
    EventSeverity.HIGH: 0.30,
}

HARD_RISK_EVENT_TYPES: frozenset[EventType] = frozenset({
    EventType.ST_DELIST,
    EventType.INVESTIGATION,
    EventType.PENALTY,
    EventType.SUSPEND_RESUME,
})


def clip(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def sentiment_numeric(sentiment: EventSentiment) -> float:
    if sentiment == EventSentiment.POSITIVE:
        return 1.0
    if sentiment == EventSentiment.NEGATIVE:
        return -1.0
    return 0.0


def raw_event_impact(event: MarketEvent) -> float:
    magnitude = TYPE_MAGNITUDE[event.event_type]
    multiplier = SEVERITY_MULTIPLIER[event.severity]
    return clip(sentiment_numeric(event.sentiment) * magnitude * multiplier)


def decay_factor(age_days: float, half_life_days: float) -> float:
    if half_life_days <= 0:
        return 0.0
    return math.exp(-LN2 * age_days / half_life_days)


def event_age_days(event: MarketEvent, signal_time: datetime) -> float:
    if event.available_at is None:
        return 0.0
    signal = ensure_aware_shanghai(signal_time)
    available = ensure_aware_shanghai(event.available_at)
    return max(0.0, (signal - available).total_seconds() / 86_400)


def dataset_for_event(event: MarketEvent) -> EventDataset:
    return EVENT_TYPE_DATASET[event.event_type]


@dataclass(frozen=True)
class SoftRiskTag:
    category: str
    severity: EventSeverity


@dataclass(frozen=True)
class EventContribution:
    event_id: str
    event_type: EventType
    dataset: EventDataset
    event_day: date
    raw_impact: float
    decay: float
    weighted_impact: float
    sentiment: EventSentiment


@dataclass
class SymbolEventScoringResult:
    symbol: str
    base_score: float
    normalized_base_score: float
    dataset_scores: dict[str, float | None] = field(default_factory=dict)
    event_score: float | None = None
    event_component: float | None = None
    enhanced_score: float | None = None
    soft_risk_penalty: float = 0.0
    hard_risk_excluded: bool = False
    hard_risk_flags: list[str] = field(default_factory=list)
    degradation_reasons: list[str] = field(default_factory=list)
    contributions: list[EventContribution] = field(default_factory=list)


def soft_risk_penalty(tags: Iterable[SoftRiskTag]) -> float:
    by_category: dict[str, EventSeverity] = {}
    for tag in tags:
        if tag.severity == EventSeverity.CRITICAL:
            continue
        by_category[tag.category] = tag.severity
    total = sum(
        SOFT_RISK_SEVERITY_PENALTY[severity]
        for severity in by_category.values()
    )
    return min(total, 0.50)


def is_hard_risk_event(event: MarketEvent) -> bool:
    if event.pit_level != PITLevel.PIT_REQUIRED:
        return False
    if event.severity != EventSeverity.CRITICAL:
        return False
    return event.event_type in HARD_RISK_EVENT_TYPES


def hard_risk_flags(events: Iterable[MarketEvent]) -> list[str]:
    flags: list[str] = []
    for event in events:
        if not is_hard_risk_event(event):
            continue
        flags.append(f"hard_risk:{event.event_type.value}:{event.event_id}")
    return flags


def build_contributions(
    events: Iterable[MarketEvent],
    *,
    signal_time: datetime,
    half_life_days: float,
) -> list[EventContribution]:
    items: list[EventContribution] = []
    for event in events:
        if event.sentiment == EventSentiment.UNKNOWN:
            raw = 0.0
        else:
            raw = raw_event_impact(event)
        age = event_age_days(event, signal_time)
        decay = decay_factor(age, half_life_days)
        event_day = (
            ensure_aware_shanghai(event.available_at).date()
            if event.available_at is not None
            else ensure_aware_shanghai(event.published_at).date()
        )
        items.append(EventContribution(
            event_id=event.event_id,
            event_type=event.event_type,
            dataset=dataset_for_event(event),
            event_day=event_day,
            raw_impact=raw,
            decay=decay,
            weighted_impact=raw * decay,
            sentiment=event.sentiment,
        ))
    return apply_same_type_day_cap(items)


def apply_same_type_day_cap(contributions: list[EventContribution]) -> list[EventContribution]:
    grouped: dict[tuple[EventType, date], list[EventContribution]] = {}
    for item in contributions:
        key = (item.event_type, item.event_day)
        grouped.setdefault(key, []).append(item)

    capped: list[EventContribution] = []
    for group in grouped.values():
        total_abs = sum(abs(item.weighted_impact) for item in group)
        if total_abs <= 1.0 or total_abs == 0.0:
            capped.extend(group)
            continue
        scale = 1.0 / total_abs
        for item in group:
            weighted = item.weighted_impact * scale
            capped.append(EventContribution(
                event_id=item.event_id,
                event_type=item.event_type,
                dataset=item.dataset,
                event_day=item.event_day,
                raw_impact=item.raw_impact,
                decay=item.decay,
                weighted_impact=weighted,
                sentiment=item.sentiment,
            ))
    return capped


def dataset_subscore(contributions: Iterable[EventContribution], dataset: EventDataset) -> float | None:
    rows = [item for item in contributions if item.dataset == dataset]
    if not rows:
        return None
    numerator = sum(item.weighted_impact for item in rows)
    denominator = sum(item.decay for item in rows)
    return clip(numerator / max(1.0, denominator))


def weighted_dataset_average(dataset_scores: dict[EventDataset, float | None]) -> float | None:
    available = {
        dataset: score
        for dataset, score in dataset_scores.items()
        if score is not None
    }
    if not available:
        return None
    weight_sum = sum(DATASET_WEIGHTS[dataset] for dataset in available)
    return sum(available[dataset] * DATASET_WEIGHTS[dataset] for dataset in available) / weight_sum


def normalize_base_scores(base_scores: dict[str, float]) -> dict[str, float]:
    if not base_scores:
        return {}
    values = list(base_scores.values())
    minimum = min(values)
    maximum = max(values)
    if maximum == minimum:
        return {symbol: 0.5 for symbol in base_scores}
    span = maximum - minimum
    return {
        symbol: (score - minimum) / span
        for symbol, score in base_scores.items()
    }


def fuse_enhanced_score(
    normalized_base_score: float,
    event_component: float,
    event_weight: float,
) -> float:
    return (1.0 - event_weight) * normalized_base_score + event_weight * event_component


def recompute_event_score_from_contributions(
    contributions: Iterable[EventContribution],
    *,
    soft_penalty: float,
) -> tuple[float | None, float | None]:
    dataset_scores = {
        dataset: dataset_subscore(contributions, dataset)
        for dataset in EventDataset
    }
    weighted = weighted_dataset_average(dataset_scores)
    if weighted is None:
        return None, None
    event_score = clip(weighted - soft_penalty)
    return event_score, (event_score + 1.0) / 2.0


def score_symbol_events(
    symbol: str,
    events: list[MarketEvent],
    soft_tags: list[SoftRiskTag],
    *,
    base_score: float,
    base_scores: dict[str, float],
    signal_time: datetime,
    half_life_days: float,
    event_weight: float,
    hard_risk_filter: bool = True,
) -> SymbolEventScoringResult:
    normalized = normalize_base_scores(base_scores)
    soft_penalty = soft_risk_penalty(soft_tags)
    flags = hard_risk_flags(events)
    if hard_risk_filter and flags:
        return SymbolEventScoringResult(
            symbol=symbol,
            base_score=base_score,
            normalized_base_score=normalized.get(symbol, 0.5),
            soft_risk_penalty=soft_penalty,
            hard_risk_excluded=True,
            hard_risk_flags=flags,
            degradation_reasons=["hard_risk_filter_excluded"],
        )

    contributions = build_contributions(
        events,
        signal_time=signal_time,
        half_life_days=half_life_days,
    )
    dataset_scores = {
        dataset.value: dataset_subscore(contributions, dataset)
        for dataset in EventDataset
    }
    weighted = weighted_dataset_average({
        dataset: dataset_scores[dataset.value]
        for dataset in EventDataset
    })
    if weighted is None:
        return SymbolEventScoringResult(
            symbol=symbol,
            base_score=base_score,
            normalized_base_score=normalized.get(symbol, 0.5),
            dataset_scores=dataset_scores,
            soft_risk_penalty=soft_penalty,
            degradation_reasons=["all_event_datasets_missing"],
            contributions=contributions,
        )

    event_score = clip(weighted - soft_penalty)
    event_component = (event_score + 1.0) / 2.0
    enhanced = fuse_enhanced_score(
        normalized.get(symbol, 0.5),
        event_component,
        event_weight,
    )
    return SymbolEventScoringResult(
        symbol=symbol,
        base_score=base_score,
        normalized_base_score=normalized.get(symbol, 0.5),
        dataset_scores=dataset_scores,
        event_score=event_score,
        event_component=event_component,
        enhanced_score=enhanced,
        soft_risk_penalty=soft_penalty,
        contributions=contributions,
    )


def sort_enhanced_ranking(results: Iterable[SymbolEventScoringResult]) -> list[SymbolEventScoringResult]:
    eligible = [
        item for item in results
        if item.enhanced_score is not None and not item.hard_risk_excluded
    ]
    eligible.sort(
        key=lambda item: (
            -item.enhanced_score,
            -item.base_score,
            item.symbol,
        ),
    )
    return eligible
