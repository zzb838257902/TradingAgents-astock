"""Sync orchestration for event enrichment (phase 5)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any, Protocol

from tradingagents.events.contracts import EventSymbolLink, MarketEvent
from tradingagents.events.dedup import DedupStats, EventBundle, deduplicate_event_bundles
from tradingagents.events.fetch import collect_announcement_bundles
from tradingagents.events.provider_capabilities import (
    core_announcement_gate_status,
    load_event_capability_matrix,
)
from tradingagents.market_data.config import MarketDataPaths
from tradingagents.market_data.contracts import DataResult, DataStatus, PITLevel
from tradingagents.market_data.market_hours import SHANGHAI
from tradingagents.market_data.repository import MarketDataRepository


class EventSyncStatus(StrEnum):
    PUBLISHED = "published"
    ERROR = "error"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class EventSyncResult:
    dataset: str
    status: EventSyncStatus
    run_id: str | None = None
    version_id: str | None = None
    errors: list[str] = field(default_factory=list)
    dedup_stats: DedupStats | None = None


class EventFetchProvider(Protocol):
    name: str

    def fetch_announcements(
        self,
        symbols: list[str],
        start: date,
        end: date,
    ) -> DataResult[list[MarketEvent]]: ...


class EventSyncService:
    def __init__(
        self,
        repository: MarketDataRepository,
        provider: EventFetchProvider,
        paths: MarketDataPaths,
        *,
        backend: Any | None = None,
    ):
        self.repository = repository
        self.provider = provider
        self.paths = paths
        self.backend = backend

    def sync_announcements(
        self,
        symbols: list[str],
        start: date,
        end: date,
        *,
        as_of: datetime | None = None,
    ) -> EventSyncResult:
        if core_announcement_gate_status(load_event_capability_matrix()) != "PASS":
            return EventSyncResult(
                dataset="market_events",
                status=EventSyncStatus.BLOCKED,
                errors=["core announcement gate is BLOCKED"],
            )

        backend = self.backend or getattr(self.provider, "_backend", None)
        if backend is not None:
            open_dates = self.repository.list_open_trade_dates()
            bundles, status, errors = collect_announcement_bundles(
                backend,
                symbols,
                start,
                end,
                open_dates=open_dates,
                source=getattr(self.provider, "name", "free_astock"),
            )
            if status in {
                DataStatus.NETWORK_ERROR,
                DataStatus.RATE_LIMITED,
                DataStatus.ERROR,
            }:
                return EventSyncResult(
                    dataset="market_events",
                    status=EventSyncStatus.ERROR,
                    errors=errors or [status.value],
                )
            fetched = DataResult(
                data=[bundle.event for bundle in bundles],
                status=status,
                source=self.provider.name,
                as_of=as_of or datetime.now(tz=SHANGHAI),
                available_at=as_of or datetime.now(tz=SHANGHAI),
                pit_level=PITLevel.PIT_REQUIRED,
                errors=errors,
            )
        else:
            fetched = self.provider.fetch_announcements(symbols, start, end)
            if fetched.status in {
                DataStatus.NETWORK_ERROR,
                DataStatus.RATE_LIMITED,
                DataStatus.ERROR,
            }:
                return EventSyncResult(
                    dataset="market_events",
                    status=EventSyncStatus.ERROR,
                    errors=fetched.errors or [fetched.status.value],
                )
            bundles = [
                EventBundle(
                    event=event,
                    links=(EventSymbolLink(
                        event_id=event.event_id,
                        symbol=symbols[0] if len(symbols) == 1 else "",
                        role="primary",
                        available_at=event.available_at or event.published_at,
                        source=event.source,
                    ),),
                )
                for event in (fetched.data or [])
            ]

        if fetched.status not in {DataStatus.OK, DataStatus.SUCCESS_EMPTY}:
            return EventSyncResult(
                dataset="market_events",
                status=EventSyncStatus.ERROR,
                errors=fetched.errors or [fetched.status.value],
            )

        deduped, stats = deduplicate_event_bundles(bundles)
        if not deduped:
            if fetched.status == DataStatus.SUCCESS_EMPTY:
                return EventSyncResult(
                    dataset="market_events",
                    status=EventSyncStatus.BLOCKED,
                    errors=["no announcement events to publish"],
                    dedup_stats=stats,
                )
            return EventSyncResult(
                dataset="market_events",
                status=EventSyncStatus.BLOCKED,
                errors=["no announcement events after deduplication"],
                dedup_stats=stats,
            )

        self._save_snapshot(
            "sina.corp.vCB_AllBulletin",
            {"symbols": symbols, "start": start.isoformat(), "end": end.isoformat()},
            {
                "events": [bundle.event.model_dump(mode="json") for bundle in deduped],
                "dedup_stats": stats.__dict__,
            },
        )

        run_id = self.repository.begin_ingestion_run(
            "market_events",
            {
                "dataset": "official_announcements",
                "symbols": symbols,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "as_of": (as_of or datetime.now(tz=SHANGHAI)).isoformat(),
            },
        )
        events = [bundle.event for bundle in deduped]
        links = [link for bundle in deduped for link in bundle.links]
        tags = [tag for bundle in deduped for tag in bundle.tags]
        self.repository.upsert_staging_event_bundle(
            run_id,
            events=events,
            links=links,
            tags=tags,
        )
        try:
            version_id = self.repository.publish_event_bundle(run_id)
        except ValueError as exc:
            return EventSyncResult(
                dataset="market_events",
                status=EventSyncStatus.ERROR,
                run_id=run_id,
                errors=[str(exc)],
                dedup_stats=stats,
            )
        return EventSyncResult(
            dataset="market_events",
            status=EventSyncStatus.PUBLISHED,
            run_id=run_id,
            version_id=version_id,
            dedup_stats=stats,
        )

    def _save_snapshot(self, endpoint: str, params: dict[str, Any], payload: Any) -> None:
        if self.repository.snapshot_dir is None:
            return
        self.repository.save_raw_snapshot(
            source=self.provider.name,
            endpoint=endpoint,
            request_params=params,
            response_body=payload,
        )
