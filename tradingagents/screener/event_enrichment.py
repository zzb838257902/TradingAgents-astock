"""Optional event enrichment for screening pipeline (phase 5 Task 7)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from tradingagents.events.contracts import (
    EventSentiment,
    EventSeverity,
    EventType,
    MarketEvent,
)
from tradingagents.events.scoring import (
    EventContribution,
    EventDataset,
    SoftRiskTag,
    dataset_for_event,
    event_age_days,
    score_symbol_events,
    sort_enhanced_ranking,
)
from tradingagents.market_data.contracts import PITLevel
from tradingagents.market_data.market_hours import ensure_aware_shanghai
from tradingagents.market_data.repository import MarketDataRepository
from tradingagents.market_data.sync_policy import live_snapshot_date_error, shanghai_today
from tradingagents.screener.config import ScreenerConfig

REQUIRED_DATASET_MAP: dict[str, EventDataset] = {
    "require_announcements": EventDataset.OFFICIAL_ANNOUNCEMENTS,
    "require_news": EventDataset.EVENT_NEWS,
    "require_fund_flow": EventDataset.EVENT_FUND_FLOW,
}

DATASET_REPORT_KEYS: dict[EventDataset, str] = {
    EventDataset.OFFICIAL_ANNOUNCEMENTS: "official_announcements",
    EventDataset.EVENT_NEWS: "event_news",
    EventDataset.EVENT_FUND_FLOW: "event_fund_flow",
    EventDataset.EVENT_HOT_TOPICS: "event_hot_topics",
}


@dataclass
class EventEnrichmentResult:
    base_ranking: list[str]
    event_ranking: list[str] = field(default_factory=list)
    enhanced_ranking: list[str] = field(default_factory=list)
    event_contributions: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    risk_flags: dict[str, list[str]] = field(default_factory=dict)
    event_dataset_versions: dict[str, dict[str, Any] | None] = field(default_factory=dict)
    event_data_sources: dict[str, str] = field(default_factory=dict)
    event_degradations: dict[str, list[str]] = field(default_factory=dict)
    event_pit_level: str = ""
    event_enrichment_errors: list[str] = field(default_factory=list)

    def as_report_kwargs(self) -> dict[str, Any]:
        return {
            "base_ranking": self.base_ranking,
            "event_ranking": self.event_ranking,
            "enhanced_ranking": self.enhanced_ranking,
            "event_contributions": self.event_contributions,
            "risk_flags": self.risk_flags,
            "event_dataset_versions": self.event_dataset_versions,
            "event_data_sources": self.event_data_sources,
            "event_degradations": self.event_degradations,
            "event_pit_level": self.event_pit_level,
            "event_enrichment_errors": self.event_enrichment_errors,
        }


def _market_event_from_row(row: dict[str, Any]) -> MarketEvent:
    return MarketEvent(
        event_id=row["event_id"],
        event_type=EventType(row["event_type"]),
        title=row["title"],
        summary=row.get("summary") or "",
        published_at=row["published_at"],
        available_at=row.get("available_at"),
        source=row["source"],
        source_url=row.get("source_url") or "",
        source_record_id=row["source_record_id"],
        source_version=row.get("source_version") or "",
        content_hash=row["content_hash"],
        pit_level=PITLevel(row["pit_level"]),
        sentiment=EventSentiment(row.get("sentiment") or EventSentiment.UNKNOWN.value),
        severity=EventSeverity(row.get("severity") or EventSeverity.MEDIUM.value),
        announcement_date_source=row.get("announcement_date_source"),
        quality_status=row.get("quality_status") or "valid",
        supersedes_event_id=row.get("supersedes_event_id"),
    )


def _contribution_dict(item: EventContribution) -> dict[str, Any]:
    return {
        "event_id": item.event_id,
        "event_type": item.event_type.value,
        "dataset": item.dataset.value,
        "event_day": item.event_day.isoformat(),
        "raw_impact": item.raw_impact,
        "decay": item.decay,
        "weighted_impact": item.weighted_impact,
        "sentiment": item.sentiment.value,
    }


def _soft_tags_from_rows(rows: list[dict[str, str]]) -> list[SoftRiskTag]:
    tags: list[SoftRiskTag] = []
    for row in rows:
        if row["tag_key"] != "soft_risk":
            continue
        category, _, severity_raw = row["tag_value"].partition(":")
        if not category or not severity_raw:
            continue
        tags.append(SoftRiskTag(category=category, severity=EventSeverity(severity_raw)))
    return tags


def _filter_events(
    events: list[MarketEvent],
    *,
    signal_time: datetime,
    max_event_age_days: int,
    allow_current_only: bool,
) -> tuple[list[MarketEvent], list[str]]:
    kept: list[MarketEvent] = []
    degradations: list[str] = []
    for event in events:
        if event.pit_level == PITLevel.CURRENT_ONLY and not allow_current_only:
            degradations.append("event_hot_topics_current_only_historical_rejected")
            continue
        age = event_age_days(event, signal_time)
        if age > max_event_age_days:
            continue
        kept.append(event)
    return kept, sorted(set(degradations))


def _collect_dataset_sources(events: list[MarketEvent]) -> dict[str, str]:
    sources: dict[str, str] = {}
    for event in events:
        dataset_key = DATASET_REPORT_KEYS[dataset_for_event(event)]
        sources.setdefault(dataset_key, event.source)
    return sources


def _event_pit_level(events: list[MarketEvent]) -> str:
    if not events:
        return ""
    levels = {event.pit_level.value for event in events}
    if len(levels) == 1:
        return next(iter(levels))
    return "mixed"


def enrich_ranking_with_events(
    repo: MarketDataRepository,
    config: ScreenerConfig,
    *,
    base_ranking: list[str],
    base_scores: dict[str, float],
    signal_time: datetime,
) -> EventEnrichmentResult:
    cfg = config.event_enrichment
    candidates = base_ranking[: cfg.candidate_limit]
    signal = ensure_aware_shanghai(signal_time)
    allow_current_only = signal.date() >= shanghai_today()
    historical_error = live_snapshot_date_error(signal.date(), dataset="event_hot_topics")

    event_rows = repo.get_market_events(candidates, signal)
    events = [_market_event_from_row(row) for row in event_rows]
    filtered_events, global_degradations = _filter_events(
        events,
        signal_time=signal,
        max_event_age_days=cfg.max_event_age_days,
        allow_current_only=allow_current_only,
    )

    events_by_symbol: dict[str, list[MarketEvent]] = {symbol: [] for symbol in candidates}
    if filtered_events:
        placeholders = ", ".join("?" for _ in candidates)
        link_rows = repo.connection.execute(
            f"""SELECT event_id, symbol
                FROM event_symbol_links l
                LEFT JOIN dataset_versions v ON l.dataset_version_id = v.version_id
                WHERE symbol IN ({placeholders})
                  AND (l.dataset_version_id IS NULL OR v.status = 'PUBLISHED')""",
            candidates,
        ).fetchall()
        links_by_event: dict[str, set[str]] = {}
        for event_id, symbol in link_rows:
            links_by_event.setdefault(event_id, set()).add(symbol)
        for event in filtered_events:
            for symbol in links_by_event.get(event.event_id, set()):
                if symbol in events_by_symbol:
                    events_by_symbol[symbol].append(event)

    tag_rows = repo.get_event_tags([event.event_id for event in filtered_events])
    tags_by_event: dict[str, list[dict[str, str]]] = {}
    for row in tag_rows:
        tags_by_event.setdefault(row["event_id"], []).append(row)

    present_datasets: set[EventDataset] = {
        dataset_for_event(event) for event in filtered_events
    }
    enrichment_errors: list[str] = []
    if historical_error:
        global_degradations.append(historical_error)
    for flag_name, dataset in REQUIRED_DATASET_MAP.items():
        if getattr(cfg, flag_name) and dataset not in present_datasets:
            enrichment_errors.append(f"required dataset {DATASET_REPORT_KEYS[dataset]} missing")

    scored_results = []
    contributions: dict[str, list[dict[str, Any]]] = {}
    risk_flags: dict[str, list[str]] = {}
    degradations_by_symbol: dict[str, list[str]] = {}

    for symbol in candidates:
        symbol_events = events_by_symbol.get(symbol, [])
        symbol_tags: list[SoftRiskTag] = []
        for event in symbol_events:
            symbol_tags.extend(_soft_tags_from_rows(tags_by_event.get(event.event_id, [])))
        result = score_symbol_events(
            symbol,
            symbol_events,
            symbol_tags,
            base_score=base_scores.get(symbol, 0.0),
            base_scores={sym: base_scores.get(sym, 0.0) for sym in candidates},
            signal_time=signal,
            half_life_days=float(cfg.event_half_life_days),
            event_weight=cfg.event_weight,
            hard_risk_filter=cfg.hard_risk_filter,
        )
        scored_results.append(result)
        contributions[symbol] = [_contribution_dict(item) for item in result.contributions]
        flags = list(result.hard_risk_flags)
        if flags:
            risk_flags[symbol] = flags
        symbol_degradations = list(result.degradation_reasons)
        if symbol_degradations:
            degradations_by_symbol[symbol] = symbol_degradations

    event_ranking = sorted(
        candidates,
        key=lambda symbol: (
            next(
                (item.event_score for item in scored_results if item.symbol == symbol),
                None,
            ) is None,
            -(next(
                (item.event_score for item in scored_results if item.symbol == symbol),
                float("-inf"),
            ) or float("-inf")),
            symbol,
        ),
    )
    enhanced_ranking = [
        item.symbol for item in sort_enhanced_ranking(scored_results) if item.symbol in candidates
    ]

    if enrichment_errors:
        return EventEnrichmentResult(
            base_ranking=base_ranking,
            event_ranking=base_ranking[: len(candidates)],
            enhanced_ranking=base_ranking,
            event_contributions=contributions,
            risk_flags=risk_flags,
            event_dataset_versions=_event_dataset_versions(repo),
            event_data_sources=_collect_dataset_sources(filtered_events),
            event_degradations=_merge_degradations(global_degradations, degradations_by_symbol),
            event_pit_level=_event_pit_level(filtered_events),
            event_enrichment_errors=enrichment_errors,
        )

    return EventEnrichmentResult(
        base_ranking=base_ranking,
        event_ranking=event_ranking,
        enhanced_ranking=enhanced_ranking or list(base_ranking),
        event_contributions=contributions,
        risk_flags=risk_flags,
        event_dataset_versions=_event_dataset_versions(repo),
        event_data_sources=_collect_dataset_sources(filtered_events),
        event_degradations=_merge_degradations(global_degradations, degradations_by_symbol),
        event_pit_level=_event_pit_level(filtered_events),
        event_enrichment_errors=enrichment_errors,
    )


def _event_dataset_versions(repo: MarketDataRepository) -> dict[str, dict[str, Any] | None]:
    version = repo.get_latest_published_version("market_events")
    return {key: version for key in DATASET_REPORT_KEYS.values()}


def _merge_degradations(
    global_items: list[str],
    per_symbol: dict[str, list[str]],
) -> dict[str, list[str]]:
    merged: dict[str, list[str]] = {}
    if global_items:
        merged["__global__"] = global_items
    for symbol, items in per_symbol.items():
        if items:
            merged[symbol] = items
    return merged
