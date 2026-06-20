"""Event deduplication with frozen precedence rules."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from tradingagents.events.contracts import EventSymbolLink, MarketEvent, stable_event_id


@dataclass(frozen=True)
class DedupStats:
    kept: int = 0
    physical_duplicates: int = 0
    semantic_duplicates: int = 0


@dataclass(frozen=True)
class EventBundle:
    event: MarketEvent
    links: tuple[EventSymbolLink, ...]
    tags: tuple[dict[str, str], ...] = ()


def _canonical_url(url: str) -> str:
    return url.split("?", 1)[0].rstrip("/")


def _title_time_symbol_key(event: MarketEvent, links: tuple[EventSymbolLink, ...]) -> str:
    symbols = ",".join(sorted({link.symbol for link in links}))
    stamp = event.published_at.isoformat()
    return f"{event.title.strip()}|{stamp}|{symbols}"


def deduplicate_event_bundles(bundles: list[EventBundle]) -> tuple[list[EventBundle], DedupStats]:
    kept: list[EventBundle] = []
    seen_stable: set[str] = set()
    seen_record: set[str] = set()
    seen_url: set[str] = set()
    seen_semantic: set[str] = set()
    seen_content: set[str] = set()
    physical = 0
    semantic = 0

    for bundle in bundles:
        event = bundle.event
        stable = stable_event_id(event)
        if stable in seen_stable:
            physical += 1
            continue
        record_key = stable_event_id(event)
        if record_key in seen_record:
            physical += 1
            continue
        url_key = ""
        if event.source_url:
            url_key = f"{_canonical_url(event.source_url)}|{event.source_version or 'v0'}"
        if url_key and url_key in seen_url:
            physical += 1
            continue
        semantic_key = _title_time_symbol_key(event, bundle.links)
        if semantic_key in seen_semantic:
            semantic += 1
            continue
        if event.content_hash in seen_content:
            semantic += 1
            continue

        seen_stable.add(stable)
        seen_record.add(record_key)
        if url_key:
            seen_url.add(url_key)
        seen_semantic.add(semantic_key)
        seen_content.add(event.content_hash)
        kept.append(bundle)

    return kept, DedupStats(
        kept=len(kept),
        physical_duplicates=physical,
        semantic_duplicates=semantic,
    )


def fingerprint_bundle(bundle: EventBundle) -> str:
    payload = (
        f"{stable_event_id(bundle.event)}|"
        f"{','.join(link.symbol for link in bundle.links)}|"
        f"{bundle.event.content_hash}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
