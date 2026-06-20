#!/usr/bin/env python3
"""Layered acceptance runner for phase-5 event enrichment.

Tiers (see docs/event-data-quickstart.md):
  A. --offline + --recorded-contract  (fixture / contract, no network)
  B. --live-smoke                     (free Sina bulletin probe; BLOCKED if network down)

This script does not invoke pytest. Use tests/events/test_acceptance.py to gate it in CI.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import resource
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import date, datetime, time as dt_time
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
PIP = ROOT / ".pip_packages"
if PIP.is_dir() and str(PIP) not in sys.path:
    sys.path.insert(0, str(PIP))

SHANGHAI = ZoneInfo("Asia/Shanghai")
MVP_FIXTURE = ROOT / "tests/fixtures/screener/mvp_market.json"
EVENTS_FIXTURE = ROOT / "tests/fixtures/events/provider_events.json"
RECORDED_DIR = ROOT / "tests/fixtures/events/recorded"
SAMPLE_HTML = ROOT / "tests/fixtures/events/sina_bulletin_sample.html"
DATELIST_HTML = ROOT / "tests/fixtures/events/sina_bulletin_datelist_sample.html"
EMPTY_HTML = ROOT / "tests/fixtures/events/sina_bulletin_empty_sample.html"
MATRIX_PATH = ROOT / "docs/data/data-capability-matrix.yaml"

LIVE_SMOKE_SYMBOLS: dict[str, str] = {
    "600000": "sse_main",
    "000001": "szse_main",
    "300001": "chinext",
    "688001": "star",
}

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_BLOCKED = 2


@dataclass
class StepResult:
    name: str
    ok: bool
    required: bool = True
    duration_ms: float = 0.0
    detail: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class AcceptanceReport:
    def __init__(self, *, modes: list[str], home_dir: Path) -> None:
        self.started_at = datetime.now(tz=SHANGHAI)
        self.modes = modes
        self.home_dir = str(home_dir)
        self.steps: list[StepResult] = []
        self.request_count = 0
        self._memory_start = _memory_mb()

    def run_step(
        self,
        name: str,
        fn: Callable[[], dict[str, Any]],
        *,
        required: bool = True,
    ) -> StepResult:
        started = time.perf_counter()
        try:
            detail = fn()
            step = StepResult(
                name=name,
                ok=True,
                required=required,
                duration_ms=(time.perf_counter() - started) * 1000,
                detail=detail,
            )
        except Exception as exc:
            step = StepResult(
                name=name,
                ok=False,
                required=required,
                duration_ms=(time.perf_counter() - started) * 1000,
                detail={},
                error=f"{type(exc).__name__}: {exc}",
            )
        self.steps.append(step)
        return step

    def to_dict(self) -> dict[str, Any]:
        finished = datetime.now(tz=SHANGHAI)
        duration_ms = (finished - self.started_at).total_seconds() * 1000
        required_failures = [s for s in self.steps if s.required and not s.ok]
        optional_failures = [s for s in self.steps if not s.required and not s.ok]
        live_blocked = (
            "live-smoke" in self.modes
            and any(
                s.name == "live_network_probe"
                and not s.ok
                and (s.error or "").lower().startswith("assertionerror: network blocked:")
                for s in self.steps
            )
        )
        offline_or_contract_fail = any(
            not s.ok and s.required and not s.name.startswith("live_")
            for s in self.steps
        )

        if offline_or_contract_fail:
            status = "FAIL"
            exit_code = EXIT_FAIL
        elif live_blocked and "live-smoke" in self.modes:
            status = "BLOCKED"
            exit_code = EXIT_BLOCKED
        elif required_failures:
            status = "FAIL"
            exit_code = EXIT_FAIL
        else:
            status = "PASS"
            exit_code = EXIT_PASS

        versions: dict[str, Any] = {}
        sources: set[str] = set()
        pit_levels: dict[str, str] = {}
        failed_sources: list[str] = []
        degradations: list[str] = []

        for step in self.steps:
            if step.detail.get("version_id"):
                versions[step.name] = step.detail.get("version_id")
            if step.detail.get("dataset_version"):
                versions[step.name] = step.detail["dataset_version"]
            for src in step.detail.get("sources") or []:
                sources.add(str(src))
            for key, value in (step.detail.get("pit_levels") or {}).items():
                pit_levels[str(key)] = str(value)
            failed_sources.extend(step.detail.get("failed_sources") or [])
            degradations.extend(step.detail.get("degradations") or [])

        def _tier_status(mode: str, prefix: str, *, blocked: bool = False) -> str:
            if mode not in self.modes:
                return "SKIP"
            relevant = [step for step in self.steps if step.name.startswith(prefix)]
            if not relevant:
                return "SKIP"
            if blocked:
                return "BLOCKED"
            if any(not step.ok and step.required for step in relevant):
                return "FAIL"
            return "PASS"

        tiers = {
            "A_offline_fixture": _tier_status("offline", "offline_"),
            "A_recorded_contract": _tier_status("recorded-contract", "recorded_"),
            "B_live_smoke": _tier_status("live-smoke", "live_", blocked=live_blocked),
            "C_formal_historical_backtest": "NOT_IN_SCOPE",
            "D_five_day_operations": "NOT_IN_SCOPE",
        }

        return {
            "status": status,
            "exit_code": exit_code,
            "tiers": tiers,
            "modes": self.modes,
            "home_dir": self.home_dir,
            "started_at": self.started_at.isoformat(),
            "finished_at": finished.isoformat(),
            "duration_ms": round(duration_ms, 2),
            "memory_peak_mb": round(max(self._memory_start, _memory_mb()), 2),
            "request_count": self.request_count,
            "dataset_versions": versions,
            "sources": sorted(sources),
            "pit_levels": pit_levels,
            "failed_sources": failed_sources,
            "degradations": degradations,
            "optional_failures": len(optional_failures),
            "steps": [
                {
                    "name": step.name,
                    "ok": step.ok,
                    "required": step.required,
                    "duration_ms": round(step.duration_ms, 2),
                    "error": step.error,
                    **step.detail,
                }
                for step in self.steps
            ],
        }


def _memory_mb() -> float:
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Linux: KB; macOS: bytes
    if sys.platform == "darwin":
        return usage / (1024 * 1024)
    return usage / 1024


def _stable_fingerprint(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _load_events_fixture(**overrides: Any) -> dict:
    fixture = json.loads(EVENTS_FIXTURE.read_text(encoding="utf-8"))
    fixture.update(overrides)
    return fixture


def _relaxed_screener_config(**event_updates: Any):
    from tradingagents.screener.config import ScreenerConfig

    base = ScreenerConfig()
    relaxed = base.model_copy(update={
        "universe": base.universe.model_copy(update={
            "min_listing_days": 2,
            "min_avg_amount_20d": 1_000_000,
        }),
    })
    if event_updates:
        relaxed = relaxed.model_copy(update={
            "event_enrichment": relaxed.event_enrichment.model_copy(update=event_updates),
        })
    return relaxed


def _signal_time_for_date(signal_date: date) -> datetime:
    return datetime.combine(signal_date, dt_time(15, 30), tzinfo=SHANGHAI)


def _seed_pipeline_events(repo) -> None:
    from tradingagents.events.contracts import (
        AnnouncementDateSource,
        EventSentiment,
        EventSeverity,
        EventSymbolLink,
        EventType,
        MarketEvent,
    )
    from tradingagents.market_data.contracts import PITLevel

    available = datetime(2025, 12, 10, 9, 30, tzinfo=SHANGHAI)
    events = [
        MarketEvent(
            event_id="evt-600001",
            event_type=EventType.BUYBACK,
            title="buyback",
            published_at=available,
            available_at=available,
            source="fixture",
            source_url="https://example.com/buyback",
            source_record_id="acc-600001",
            source_version="v1",
            content_hash="hash-600001",
            pit_level=PITLevel.PIT_REQUIRED,
            sentiment=EventSentiment.POSITIVE,
            severity=EventSeverity.HIGH,
            announcement_date_source=AnnouncementDateSource.REPORTED,
        ),
        MarketEvent(
            event_id="evt-600003",
            event_type=EventType.PENALTY,
            title="penalty",
            published_at=available,
            available_at=available,
            source="fixture",
            source_url="https://example.com/penalty",
            source_record_id="acc-600003",
            source_version="v1",
            content_hash="hash-600003",
            pit_level=PITLevel.PIT_REQUIRED,
            sentiment=EventSentiment.NEGATIVE,
            severity=EventSeverity.HIGH,
            announcement_date_source=AnnouncementDateSource.REPORTED,
        ),
    ]
    run_id = repo.begin_ingestion_run("market_events", {"source": "acceptance"})
    links = [
        EventSymbolLink(
            event_id=event.event_id,
            symbol=symbol,
            role="primary",
            available_at=available,
            source="fixture",
        )
        for event, symbol in zip(events, ["600001", "600003"], strict=True)
    ]
    repo.upsert_staging_event_bundle(run_id, events=events, links=links, tags=[])
    version_id = repo.publish_event_bundle(run_id)
    repo.connection.execute(
        "UPDATE dataset_versions SET published_at = ? WHERE version_id = ?",
        [datetime(2025, 12, 18, 15, 0, tzinfo=SHANGHAI), version_id],
    )


def _offline_steps(report: AcceptanceReport, home_dir: Path) -> None:
    from tradingagents.events.provider_capabilities import (
        core_announcement_gate_status,
        load_event_capability_matrix,
        validate_event_capability_matrix,
    )
    from tradingagents.market_data.contracts import DataStatus, PITLevel
    from tradingagents.market_data.providers.fixture import FixtureProvider
    from tradingagents.market_data.repository import MarketDataRepository
    from tradingagents.screener.pipeline import run_screen
    from tradingagents.screener.report import ScreeningStatus
    from tradingagents.screener.universe_resolver import UniverseRequest, UniverseType

    def capability_matrix() -> dict[str, Any]:
        matrix = load_event_capability_matrix(MATRIX_PATH)
        errors = validate_event_capability_matrix(matrix)
        if errors:
            raise AssertionError("; ".join(errors))
        if core_announcement_gate_status(matrix) != "PASS":
            raise AssertionError("core announcement gate is BLOCKED")
        return {
            "pit_levels": {
                name: definition["pit_level"]
                for name, definition in matrix["event_datasets"].items()
            },
            "sources": [
                matrix["event_datasets"]["official_announcements"]["primary_source"]["id"]
            ],
        }

    report.run_step("offline_capability_matrix", capability_matrix)

    def success_empty_semantics() -> dict[str, Any]:
        provider = FixtureProvider(_load_events_fixture(active_scenario="no_announcements"))
        result = provider.fetch_announcements(
            ["600000"],
            date(2026, 5, 1),
            date(2026, 5, 31),
        )
        if result.status != DataStatus.SUCCESS_EMPTY:
            raise AssertionError(f"expected SUCCESS_EMPTY, got {result.status}")
        return {"data_status": result.status.value, "pit_level": result.pit_level.value}

    report.run_step("offline_success_empty", success_empty_semantics)

    def network_error_not_empty() -> dict[str, Any]:
        provider = FixtureProvider(_load_events_fixture(active_scenario="network_error"))
        result = provider.fetch_announcements(
            ["600000"],
            date(2026, 5, 1),
            date(2026, 5, 31),
        )
        if result.status != DataStatus.NETWORK_ERROR:
            raise AssertionError(f"expected NETWORK_ERROR, got {result.status}")
        if result.status == DataStatus.SUCCESS_EMPTY:
            raise AssertionError("network error must not map to SUCCESS_EMPTY")
        return {"data_status": result.status.value}

    report.run_step("offline_network_error", network_error_not_empty)

    def blocked_gate_simulation() -> dict[str, Any]:
        matrix = load_event_capability_matrix(MATRIX_PATH)
        broken = copy.deepcopy(matrix)
        broken["event_datasets"]["official_announcements"]["probe_status"] = "FAIL"
        if core_announcement_gate_status(broken) != "BLOCKED":
            raise AssertionError("expected BLOCKED when probe_status=FAIL")
        return {"gate_status": "BLOCKED"}

    report.run_step("offline_blocked_gate", blocked_gate_simulation)

    def revision_chain() -> dict[str, Any]:
        provider = FixtureProvider(_load_events_fixture())
        result = provider.fetch_announcements(
            ["600003"],
            date(2026, 4, 1),
            date(2026, 4, 30),
        )
        if result.status != DataStatus.OK:
            raise AssertionError(result.status.value)
        by_id = {item.event_id: item for item in result.data or []}
        if by_id["evt-new"].supersedes_event_id != "evt-old":
            raise AssertionError("revision chain missing supersedes link")
        return {
            "event_ids": sorted(by_id),
            "pit_level": PITLevel.PIT_REQUIRED.value,
        }

    report.run_step("offline_revision_chain", revision_chain)

    def data_error_missing_required_dataset() -> dict[str, Any]:
        fixture = json.loads(MVP_FIXTURE.read_text(encoding="utf-8"))
        config = _relaxed_screener_config(
            enabled=True,
            candidate_limit=3,
            require_announcements=True,
        )
        db_path = home_dir / "offline-data-error.duckdb"
        signal = _signal_time_for_date(date.fromisoformat(sorted(fixture["bars"])[-2]))
        screen = run_screen(
            fixture,
            config,
            db_path,
            universe_request=UniverseRequest(
                universe_type=UniverseType.ALL,
                as_of=signal,
            ),
        )
        if screen.status != ScreeningStatus.DATA_ERROR:
            raise AssertionError(f"expected DATA_ERROR, got {screen.status}")
        if "official_announcements missing" not in " ".join(screen.errors):
            raise AssertionError(screen.errors)
        return {"screening_status": screen.status.value, "errors": screen.errors}

    report.run_step("offline_data_error", data_error_missing_required_dataset)

    def pit_future_empty_sync() -> dict[str, Any]:
        fixture = json.loads(MVP_FIXTURE.read_text(encoding="utf-8"))
        config = _relaxed_screener_config(
            enabled=True,
            candidate_limit=3,
            require_announcements=True,
        )
        db_path = home_dir / "offline-pit-empty.duckdb"
        signal = _signal_time_for_date(date.fromisoformat(sorted(fixture["bars"])[-2]))
        request = UniverseRequest(universe_type=UniverseType.ALL, as_of=signal)
        run_screen(fixture, config, db_path, universe_request=request)
        repo = MarketDataRepository(db_path)
        run_id = repo.begin_ingestion_run(
            "market_events",
            {
                "dataset": "official_announcements",
                "symbols": ["600001", "600002", "600003"],
                "start": "2025-11-01",
                "end": "2025-12-18",
                "success_empty": True,
            },
        )
        repo.upsert_staging_event_bundle(run_id, events=[], links=[], tags=[])
        version_id = repo.publish_event_bundle(run_id)
        repo.connection.execute(
            "UPDATE dataset_versions SET published_at = ? WHERE version_id = ?",
            [datetime(2026, 6, 19, 10, 0, tzinfo=SHANGHAI), version_id],
        )
        screen = run_screen(fixture, config, db_path, reload=False, universe_request=request)
        if screen.status != ScreeningStatus.DATA_ERROR:
            raise AssertionError("future empty sync must not satisfy historical signal")
        return {"screening_status": screen.status.value}

    report.run_step("offline_pit_future_empty", pit_future_empty_sync)

    def enrichment_pipeline() -> dict[str, Any]:
        fixture = json.loads(MVP_FIXTURE.read_text(encoding="utf-8"))
        off_config = _relaxed_screener_config(enabled=False, candidate_limit=3)
        on_config = _relaxed_screener_config(enabled=True, candidate_limit=3)
        db_path = home_dir / "offline-enrichment.duckdb"
        signal = _signal_time_for_date(date.fromisoformat(sorted(fixture["bars"])[-2]))
        request = UniverseRequest(universe_type=UniverseType.ALL, as_of=signal)
        run_screen(fixture, off_config, db_path, universe_request=request)
        _seed_pipeline_events(MarketDataRepository(db_path))
        off_report = run_screen(
            fixture,
            off_config,
            db_path,
            reload=False,
            universe_request=request,
        )
        on_report = run_screen(
            fixture,
            on_config,
            db_path,
            reload=False,
            universe_request=request,
        )
        if off_report.ranking != on_report.base_ranking:
            raise AssertionError("factor ranking changed when enrichment disabled vs enabled")
        if on_report.status != ScreeningStatus.OK:
            raise AssertionError(on_report.status.value)
        if not on_report.enhanced_ranking:
            raise AssertionError("enhanced ranking empty")
        return {
            "screening_status": on_report.status.value,
            "dataset_version": on_report.event_dataset_versions.get("official_announcements"),
            "sources": list(on_report.event_data_sources.values()),
            "degradations": on_report.event_degradations.get("__global__", []),
        }

    report.run_step("offline_enrichment_pipeline", enrichment_pipeline)

    def five_day_determinism() -> dict[str, Any]:
        fixture = json.loads(MVP_FIXTURE.read_text(encoding="utf-8"))
        trading_dates = [date.fromisoformat(day) for day in sorted(fixture["bars"])]
        signal_dates = [trading_dates[idx - 1] for idx in range(len(trading_dates) - 1, 0, -1)]
        signal_dates = list(reversed(signal_dates[-5:]))
        config = _relaxed_screener_config(enabled=True, candidate_limit=3)
        fingerprints: dict[str, str] = {}
        for signal_date in signal_dates:
            db_path = home_dir / f"offline-replay-{signal_date.isoformat()}.duckdb"
            signal = _signal_time_for_date(signal_date)
            request = UniverseRequest(universe_type=UniverseType.ALL, as_of=signal)
            run_screen(fixture, config, db_path, universe_request=request)
            _seed_pipeline_events(MarketDataRepository(db_path))
            first = run_screen(fixture, config, db_path, reload=False, universe_request=request)
            second = run_screen(fixture, config, db_path, reload=False, universe_request=request)
            payload = {
                "enhanced_ranking": first.enhanced_ranking,
                "target_weights": first.target_weights,
                "ranking": first.ranking,
            }
            if payload != {
                "enhanced_ranking": second.enhanced_ranking,
                "target_weights": second.target_weights,
                "ranking": second.ranking,
            }:
                raise AssertionError(f"non-deterministic replay on {signal_date}")
            fingerprints[signal_date.isoformat()] = _stable_fingerprint(payload)
        return {"signal_dates": [day.isoformat() for day in signal_dates], "fingerprints": fingerprints}

    report.run_step("offline_five_day_replay", five_day_determinism)


def _recorded_contract_steps(report: AcceptanceReport) -> None:
    from tradingagents.market_data.providers.free_astock_sources import (
        count_sina_bulletin_detail_links,
        parse_sina_bulletin_html,
        sina_bulletin_page_is_supplier_empty,
    )

    def catalog() -> dict[str, Any]:
        readme = (RECORDED_DIR / "README.md").read_text(encoding="utf-8")
        scenarios = (
            "with_announcements",
            "no_announcements",
            "revised_announcement",
            "pagination",
            "rate_limited",
            "network_error",
        )
        missing = [name for name in scenarios if name not in readme]
        if missing:
            raise AssertionError(f"README missing scenarios: {missing}")
        meta_files = sorted(RECORDED_DIR.glob("*_meta.json"))
        if len(meta_files) < 4:
            raise AssertionError("expected at least four board metadata fixtures")
        boards = set()
        for path in meta_files:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("desensitized") is not True:
                raise AssertionError(f"{path.name} not desensitized")
            if payload.get("full_text_saved") is not False:
                raise AssertionError(f"{path.name} must not save full text")
            if payload.get("error_type"):
                if payload.get("must_not_map_to") != "SUCCESS_EMPTY":
                    raise AssertionError(f"{path.name} missing must_not_map_to")
            else:
                if "response_sha256" not in payload:
                    raise AssertionError(f"{path.name} missing response_sha256")
            if payload.get("board"):
                boards.add(payload["board"])
        expected_boards = set(LIVE_SMOKE_SYMBOLS.values())
        if not expected_boards <= boards | {"synthetic"}:
            raise AssertionError(f"boards missing from metadata: {expected_boards - boards}")
        return {"meta_files": len(meta_files), "boards": sorted(boards)}

    report.run_step("recorded_catalog", catalog)

    def parser_contract() -> dict[str, Any]:
        table_html = SAMPLE_HTML.read_text(encoding="utf-8")
        table_rows = parse_sina_bulletin_html(table_html, "600000")
        if len(table_rows) < 1:
            raise AssertionError("legacy table parser returned no rows")
        datelist_html = DATELIST_HTML.read_text(encoding="utf-8")
        datelist_rows = parse_sina_bulletin_html(datelist_html, "600000")
        detail_links = count_sina_bulletin_detail_links(datelist_html)
        if detail_links < 1:
            raise AssertionError("datelist fixture must contain detail links")
        if len(datelist_rows) != detail_links:
            raise AssertionError(
                f"expected {detail_links} datelist rows, parsed {len(datelist_rows)}"
            )
        mismatch_html = (
            datelist_html
            + '<a href="/corp/view/vCB_AllBulletinDetail.php?stockid=600000&id=1">x</a>'
        )
        try:
            from tradingagents.market_data.providers.free_astock_sources import (
                ProviderFetchError,
                validate_sina_bulletin_parse,
            )

            validate_sina_bulletin_parse(mismatch_html, [], symbol="600000")
        except ProviderFetchError as exc:
            if exc.status != "parse_error":
                raise AssertionError(f"expected parse_error, got {exc.status}") from exc
        else:
            raise AssertionError("parse mismatch must raise parse_error")
        empty_html = EMPTY_HTML.read_text(encoding="utf-8")
        if not sina_bulletin_page_is_supplier_empty(empty_html, "999998"):
            raise AssertionError("recorded empty bulletin fixture must be supplier-empty")
        validate_sina_bulletin_parse(empty_html, [], symbol="999998")
        blocked_html = (
            "<html><head><title>Access Denied</title></head><body>captcha</body></html>"
        )
        try:
            validate_sina_bulletin_parse(blocked_html, [], symbol="600000")
        except ProviderFetchError as exc:
            if exc.status != "parse_error":
                raise AssertionError(f"expected parse_error, got {exc.status}") from exc
        else:
            raise AssertionError("blocked page must raise parse_error")
        return {
            "table_rows": len(table_rows),
            "datelist_rows": len(datelist_rows),
            "detail_links": detail_links,
            "empty_fixture_symbol": "999998",
        }

    report.run_step("recorded_parser_contract", parser_contract)


def _live_smoke_steps(
    report: AcceptanceReport,
    *,
    home_dir: Path,
    clear_proxy: bool,
) -> None:
    from tradingagents.market_data.config import MarketDataPaths
    from tradingagents.market_data.contracts import DataStatus
    from tradingagents.market_data.providers.free_astock import FreeAStockProvider
    from tradingagents.market_data.providers.free_astock_sources import (
        LiveFreeAStockSourceBackend,
        ProviderFetchError,
    )
    from tradingagents.events.service import EventSyncService, EventSyncStatus
    from tradingagents.market_data.repository import MarketDataRepository

    if clear_proxy:
        for key in (
            "HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
            "ALL_PROXY", "all_proxy",
        ):
            os.environ.pop(key, None)

    backend = LiveFreeAStockSourceBackend()
    provider = FreeAStockProvider(backend=backend)
    paths = MarketDataPaths(home_dir=home_dir)
    repo = MarketDataRepository(paths.live_db_path, snapshot_dir=paths.snapshot_dir)

    def _raise_fetch_error(exc: ProviderFetchError) -> None:
        if exc.status == "network_error":
            raise AssertionError(f"network blocked: {exc.message}") from exc
        raise AssertionError(f"{exc.status}: {exc.message}") from exc

    def probe_network() -> dict[str, Any]:
        nonlocal report
        try:
            rows = backend.fetch_sina_bulletin_rows("600000", page=1)
        except ProviderFetchError as exc:
            _raise_fetch_error(exc)
        report.request_count += 1
        if len(rows) < 1:
            raise AssertionError("600000 page 1 must contain bulletin rows")
        page2_rows = backend.fetch_sina_bulletin_rows("600000", page=2)
        report.request_count += 1
        if len(page2_rows) < 1:
            raise AssertionError("600000 page 2 must contain bulletin rows")
        page1_ids = {row["source_record_id"] for row in rows}
        page2_ids = {row["source_record_id"] for row in page2_rows}
        if page1_ids == page2_ids:
            raise AssertionError("600000 page 1 and page 2 must not be identical")
        return {
            "row_count": len(rows),
            "page2_row_count": len(page2_rows),
            "data_status": DataStatus.OK.value,
        }

    probe = report.run_step("live_network_probe", probe_network, required=True)
    if not probe.ok:
        def skip_remaining(name: str) -> dict[str, Any]:
            return {"skipped": True, "reason": probe.error or "network probe failed"}

        for symbol, board in LIVE_SMOKE_SYMBOLS.items():
            report.run_step(
                f"live_board_{board}_{symbol}",
                lambda sym=symbol, brd=board: skip_remaining(f"{brd}:{sym}"),
                required=False,
            )
        report.run_step("live_sync_smoke", lambda: skip_remaining("sync"), required=False)
        return

    board_results: dict[str, Any] = {}

    def fetch_board(symbol: str, board: str) -> dict[str, Any]:
        nonlocal report
        try:
            rows = backend.fetch_sina_bulletin_rows(symbol, page=1)
            report.request_count += 1
            page2_rows: list[dict[str, Any]] = []
            if symbol == "600000":
                if len(rows) < 1:
                    raise AssertionError("600000 page 1 must contain bulletin rows")
                page2_rows = backend.fetch_sina_bulletin_rows(symbol, page=2)
                report.request_count += 1
                if len(page2_rows) < 1:
                    raise AssertionError("600000 page 2 must contain bulletin rows")
                page1_ids = {row["source_record_id"] for row in rows}
                page2_ids = {row["source_record_id"] for row in page2_rows}
                if page1_ids == page2_ids:
                    raise AssertionError("600000 pagination must differ between pages")
            status = DataStatus.OK if rows else DataStatus.SUCCESS_EMPTY
            board_results[board] = {
                "symbol": symbol,
                "row_count": len(rows),
                "data_status": status.value,
                "pagination_checked": symbol == "600000",
                "page2_row_count": len(page2_rows),
            }
            return board_results[board]
        except ProviderFetchError as exc:
            _raise_fetch_error(exc)
        raise AssertionError(f"{symbol}@{board}: fetch failed")

    for symbol, board in LIVE_SMOKE_SYMBOLS.items():
        report.run_step(
            f"live_board_{board}_{symbol}",
            lambda sym=symbol, brd=board: fetch_board(sym, brd),
            required=True,
        )

    def sync_smoke() -> dict[str, Any]:
        _seed_trade_calendar(repo)
        service = EventSyncService(repo, provider, paths, backend=backend)
        end = date.today()
        start = end.replace(day=1)
        result = service.sync_announcements(
            list(LIVE_SMOKE_SYMBOLS),
            start,
            end,
            as_of=datetime.now(tz=SHANGHAI),
        )
        report.request_count += len(LIVE_SMOKE_SYMBOLS)
        version = None
        if result.version_id:
            version = repo.get_latest_published_version("market_events")
        if result.status == EventSyncStatus.ERROR:
            raise AssertionError("; ".join(result.errors or [result.status.value]))
        if result.status == EventSyncStatus.BLOCKED:
            raise AssertionError("; ".join(result.errors or ["blocked"]))
        if result.status != EventSyncStatus.PUBLISHED:
            raise AssertionError(f"unexpected sync status: {result.status.value}")
        return {
            "sync_status": result.status.value,
            "version_id": result.version_id,
            "dataset_version": version,
            "sources": ["free_astock"],
            "pit_levels": {"official_announcements": "pit_required"},
            "dedup_stats": (
                None if result.dedup_stats is None else result.dedup_stats.__dict__
            ),
        }

    report.run_step("live_sync_smoke", sync_smoke, required=True)


def _seed_trade_calendar(repo) -> None:
    from tradingagents.market_data.sync_policy import shanghai_today

    today = shanghai_today()
    run_id = repo.begin_ingestion_run("trade_calendar", {})
    repo.upsert_staging_trade_calendar(run_id, [
        {
            "exchange": "SSE",
            "trade_date": today,
            "is_open": True,
            "available_at": datetime.combine(today, dt_time(9, 0), tzinfo=SHANGHAI),
            "source": "acceptance",
        },
    ])
    repo.publish_dataset_version(run_id)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Accept event enrichment tiers A/B")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="tier A: fixture pipeline, PIT, SUCCESS_EMPTY / DATA_ERROR semantics",
    )
    parser.add_argument(
        "--recorded-contract",
        action="store_true",
        help="tier A: recorded metadata + offline parser contract",
    )
    parser.add_argument(
        "--live-smoke",
        action="store_true",
        help="tier B: live Sina bulletin smoke (BLOCKED when network unavailable)",
    )
    parser.add_argument(
        "--home-dir",
        default="/tmp/ta-accept-events",
        help="working directory for acceptance DuckDB files",
    )
    parser.add_argument(
        "--network-mode",
        choices=("direct", "system"),
        default="direct",
        help="direct clears proxy env vars before live smoke",
    )
    parser.add_argument(
        "--json-out",
        default=None,
        help="optional path to write JSON report (stdout always prints report)",
    )
    args = parser.parse_args(argv)

    if not (args.offline or args.recorded_contract or args.live_smoke):
        args.offline = True
        args.recorded_contract = True

    home_dir = Path(args.home_dir).expanduser()
    home_dir.mkdir(parents=True, exist_ok=True)

    modes: list[str] = []
    if args.offline:
        modes.append("offline")
    if args.recorded_contract:
        modes.append("recorded-contract")
    if args.live_smoke:
        modes.append("live-smoke")

    report = AcceptanceReport(modes=modes, home_dir=home_dir)
    try:
        if args.offline:
            _offline_steps(report, home_dir)
        if args.recorded_contract:
            _recorded_contract_steps(report)
        if args.live_smoke:
            _live_smoke_steps(
                report,
                home_dir=home_dir / "live",
                clear_proxy=args.network_mode == "direct",
            )
    except Exception:
        traceback.print_exc()
        payload = report.to_dict()
        payload["status"] = "FAIL"
        payload["exit_code"] = EXIT_FAIL
        payload["fatal_error"] = traceback.format_exc()
        text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        print(text)
        if args.json_out:
            Path(args.json_out).write_text(text, encoding="utf-8")
        return EXIT_FAIL

    payload = report.to_dict()
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    print(text)
    if args.json_out:
        Path(args.json_out).write_text(text, encoding="utf-8")
    return int(payload["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
