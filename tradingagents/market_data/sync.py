"""Synchronize provider data into the live DuckDB repository."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from tradingagents.market_data.config import MarketDataPaths
from tradingagents.market_data.contracts import DataResult
from tradingagents.market_data.market_hours import SHANGHAI, post_close_signal_time
from tradingagents.market_data.providers.base import MarketDataProvider
from tradingagents.market_data.quality import (
    CoverageReport,
    assess_daily_bar_quality,
    build_daily_completeness_report,
    build_security_coverage_report,
)
from tradingagents.market_data.repository import MarketDataRepository

DAILY_COMPLETENESS_THRESHOLD = 0.995
SECURITY_COVERAGE_THRESHOLD = 0.99


class SyncStatus(StrEnum):
    PUBLISHED = "published"
    ERROR = "error"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class SyncResult:
    dataset: str
    status: SyncStatus
    run_id: str | None = None
    version_id: str | None = None
    content_hash: str | None = None
    errors: list[str] = field(default_factory=list)
    coverage_reports: dict[str, CoverageReport] = field(default_factory=dict)


class MarketDataSync:
    def __init__(
        self,
        repository: MarketDataRepository,
        provider: MarketDataProvider,
        paths: MarketDataPaths,
    ):
        self.repository = repository
        self.provider = provider
        self.paths = paths

    def probe_capabilities(self) -> SyncResult:
        result = self.provider.probe_capabilities()
        if not result.is_usable_for_screening and not result.allows_empty_universe:
            return SyncResult(
                dataset="capability_probe",
                status=SyncStatus.ERROR,
                errors=result.errors or [result.status.value],
            )
        payload = {
            item.dataset: item.model_dump(mode="json")
            for item in (result.data or [])
        }
        self.repository.save_sync_state("capability_probe", payload)
        return SyncResult(dataset="capability_probe", status=SyncStatus.PUBLISHED)

    def sync_security_master(self, as_of: date) -> SyncResult:
        probe = self._require_probe_dataset("security_master")
        if probe is not None:
            return probe
        fetched = self.provider.list_securities(as_of)
        if not fetched.is_usable_for_screening:
            return self._error_result("security_master", fetched)
        records = fetched.data or []
        run_id = self.repository.begin_ingestion_run(
            "security_master",
            {"as_of": as_of.isoformat()},
        )
        self.repository.upsert_staging_securities(run_id, records)
        self._save_snapshot("stock_basic", {"as_of": as_of.isoformat()}, records)
        report = build_security_coverage_report(
            numerator=len(records),
            denominator=len(records),
            threshold=SECURITY_COVERAGE_THRESHOLD,
        )
        version_id = self.repository.publish_dataset_version(run_id)
        published = self.repository.get_latest_published_version("security_master")
        return SyncResult(
            dataset="security_master",
            status=SyncStatus.PUBLISHED,
            run_id=run_id,
            version_id=version_id,
            content_hash=published["content_hash"] if published else None,
            coverage_reports={"security_coverage": report},
        )

    def sync_trade_calendar(self, start: date, end: date) -> SyncResult:
        probe = self._require_probe_dataset("trade_calendar")
        if probe is not None:
            return probe
        fetched = self.provider.get_trade_calendar(start, end)
        if not fetched.is_usable_for_screening and not fetched.allows_empty_universe:
            return self._error_result("trade_calendar", fetched)
        days = fetched.data or []
        run_id = self.repository.begin_ingestion_run(
            "trade_calendar",
            {"start": start.isoformat(), "end": end.isoformat()},
        )
        rows = [
            {
                "exchange": day.exchange,
                "trade_date": day.trade_date,
                "is_open": day.is_open,
                "available_at": day.available_at,
                "source": day.source,
            }
            for day in days
        ]
        self.repository.upsert_staging_trade_calendar(run_id, rows)
        self._save_snapshot(
            "trade_cal",
            {"start": start.isoformat(), "end": end.isoformat()},
            [day.model_dump(mode="json") for day in days],
        )
        version_id = self.repository.publish_dataset_version(run_id)
        published = self.repository.get_latest_published_version("trade_calendar")
        return SyncResult(
            dataset="trade_calendar",
            status=SyncStatus.PUBLISHED,
            run_id=run_id,
            version_id=version_id,
            content_hash=published["content_hash"] if published else None,
        )

    def sync_daily(self, trade_date: date) -> SyncResult:
        probe = self._require_probe_dataset("daily_bars")
        if probe is not None:
            return probe
        run_time = datetime.now(tz=SHANGHAI)
        fetched = self.provider.get_daily_by_trade_date(trade_date)
        if not fetched.is_usable_for_screening:
            return self._error_result("daily_bars", fetched)
        bars = fetched.data or []
        signal_available = post_close_signal_time(trade_date)
        for bar in bars:
            if run_time.date() == trade_date:
                bar["available_at"] = max(signal_available, run_time)
            else:
                bar["available_at"] = signal_available
            bar.setdefault("prev_close", bar.get("pre_close"))
            bar.setdefault("ingested_at", run_time)
        issues = assess_daily_bar_quality(bars)
        if issues:
            return SyncResult(
                dataset="daily_bars",
                status=SyncStatus.BLOCKED,
                errors=[issue.detail for issue in issues],
            )
        run_id = self.repository.begin_ingestion_run(
            "daily_bars",
            {"trade_date": trade_date.isoformat()},
        )
        self.repository.upsert_staging_daily_bars(run_id, bars)
        self._save_snapshot("daily", {"trade_date": trade_date.isoformat()}, bars)
        expected = self.repository.count_effective_securities(
            trade_date,
            post_close_signal_time(trade_date),
        )
        numerator = len({bar["symbol"] for bar in bars})
        coverage = build_daily_completeness_report(
            numerator=numerator,
            denominator=expected,
            threshold=DAILY_COMPLETENESS_THRESHOLD,
        )
        if coverage.status != "pass":
            self.repository.mark_ingestion_failed(
                run_id,
                f"daily completeness {coverage.ratio:.4f} < {coverage.threshold}",
            )
            self.repository.record_quality_event(
                dataset="daily_bars",
                rule="daily_completeness",
                severity="blocking",
                numerator=coverage.numerator,
                denominator=coverage.denominator,
                detail_json=coverage.to_dict(),
            )
            return SyncResult(
                dataset="daily_bars",
                status=SyncStatus.BLOCKED,
                run_id=run_id,
                errors=[f"daily completeness below threshold: {coverage.ratio:.4f}"],
                coverage_reports={"daily_completeness": coverage},
            )
        version_id = self.repository.publish_dataset_version(run_id)
        published = self.repository.get_latest_published_version("daily_bars")
        self.repository.record_quality_event(
            dataset="daily_bars",
            rule="daily_completeness",
            severity="info",
            version_id=version_id,
            numerator=coverage.numerator,
            denominator=coverage.denominator,
            detail_json=coverage.to_dict(),
        )
        return SyncResult(
            dataset="daily_bars",
            status=SyncStatus.PUBLISHED,
            run_id=run_id,
            version_id=version_id,
            content_hash=published["content_hash"] if published else None,
            coverage_reports={"daily_completeness": coverage},
        )

    def _require_probe_dataset(self, dataset: str) -> SyncResult | None:
        probe = self.repository.get_capability_probe()
        if probe is None:
            auto = self.probe_capabilities()
            if auto.status != SyncStatus.PUBLISHED:
                return auto
            probe = self.repository.get_capability_probe()
        if probe is None:
            return SyncResult(
                dataset=dataset,
                status=SyncStatus.ERROR,
                errors=["capability probe has not been run"],
            )
        entry = probe.get(dataset)
        if entry is None:
            return SyncResult(
                dataset=dataset,
                status=SyncStatus.ERROR,
                errors=[f"capability probe missing dataset {dataset}"],
            )
        if not entry.get("permitted", False):
            return SyncResult(
                dataset=dataset,
                status=SyncStatus.ERROR,
                errors=[entry.get("error") or f"{dataset} not permitted"],
            )
        return None

    def _error_result(self, dataset: str, fetched: DataResult[Any]) -> SyncResult:
        return SyncResult(
            dataset=dataset,
            status=SyncStatus.ERROR,
            errors=fetched.errors or [fetched.status.value],
        )

    def _save_snapshot(self, endpoint: str, params: dict[str, Any], payload: Any) -> None:
        if self.repository.snapshot_dir is None:
            return
        self.repository.save_raw_snapshot(
            source=self.provider.name,
            endpoint=endpoint,
            request_params=params,
            response_body=_normalize_snapshot_payload(payload),
        )


def _normalize_snapshot_payload(payload: Any) -> Any:
    if isinstance(payload, (date, datetime)):
        return payload.isoformat()
    if isinstance(payload, list):
        return [_normalize_snapshot_payload(item) for item in payload]
    if isinstance(payload, dict):
        return {key: _normalize_snapshot_payload(value) for key, value in payload.items()}
    if hasattr(payload, "model_dump"):
        return payload.model_dump(mode="json")
    return payload
