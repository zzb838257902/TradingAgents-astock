#!/usr/bin/env python3
"""Layered acceptance runner for existing-defects remediation (Task 7).

Tiers:
  A. --offline              fixture / contract checks (no network)
  B. --live-smoke           Tencent indicators, mootdx connect, repository screen

This script does not invoke pytest. Gate it via tests/remediation/test_remediation_acceptance.py.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import date, datetime
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
MINI_FIXTURE = ROOT / "tests/fixtures/market_data/provider_mini.json"
MVP_FIXTURE = ROOT / "tests/fixtures/screener/mvp_market.json"
FROZEN_FIXTURE_SHA256 = (
    "42e43a4ba99c8d81812aaa0fb875d2f70072e5555f984a1cadd0680ad6731b6e"
)

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
        live_blocked = (
            "live-smoke" in self.modes
            and any(
                step.name == "live_network_probe"
                and not step.ok
                and (step.error or "").lower().startswith("assertionerror: network blocked:")
                for step in self.steps
            )
        )
        offline_fail = any(
            not step.ok and step.required and step.name.startswith("offline_")
            for step in self.steps
        )
        required_failures = [step for step in self.steps if step.required and not step.ok]

        if offline_fail or (
            required_failures and not live_blocked
        ):
            status = "FAIL"
            exit_code = EXIT_FAIL
        elif live_blocked:
            status = "BLOCKED"
            exit_code = EXIT_BLOCKED
        elif required_failures:
            status = "FAIL"
            exit_code = EXIT_FAIL
        else:
            status = "PASS"
            exit_code = EXIT_PASS

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

        return {
            "status": status,
            "exit_code": exit_code,
            "tiers": {
                "A_offline": _tier_status("offline", "offline_"),
                "B_live_smoke": _tier_status("live-smoke", "live_", blocked=live_blocked),
            },
            "modes": self.modes,
            "home_dir": self.home_dir,
            "started_at": self.started_at.isoformat(),
            "finished_at": finished.isoformat(),
            "duration_ms": round(duration_ms, 2),
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


def _offline_steps(report: AcceptanceReport, home_dir: Path) -> None:
    from tradingagents.market_data.contracts import DataResult, DataStatus, PITLevel
    from tradingagents.market_data.fixture_store import load_fixture_into_repository
    from tradingagents.market_data.market_hours import post_close_signal_time
    from tradingagents.market_data.migrations import CURRENT_SCHEMA_VERSION, apply_migrations
    from tradingagents.market_data.config import MarketDataPaths
    from tradingagents.market_data.repository import MarketDataRepository
    from tradingagents.market_data.sync import MarketDataSync, SyncStatus
    from tradingagents.market_data.sync_policy import shanghai_today
    from tradingagents.screener.config import ScreenerConfig
    from tradingagents.screener.live import resolve_signal_trade_date, run_repository_screen
    from tradingagents.screener.report import ScreeningStatus
    from tradingagents.screener.universe_resolver import UniverseRequest, UniverseType
    from cli.analyst_registry import ANALYST_ORDER, ANALYST_REPORT_MAP, ANALYST_SPECS
    from tradingagents.dataflows.mootdx_connection import MootdxConnectionManager

    trade_date = date(2026, 1, 2)

    def schema_migration() -> dict[str, Any]:
        db_path = home_dir / "offline-schema.duckdb"
        first = apply_migrations(db_path)
        second = apply_migrations(db_path)
        if first != second or first != CURRENT_SCHEMA_VERSION:
            raise AssertionError(f"expected schema {CURRENT_SCHEMA_VERSION}, got {first}/{second}")
        repo = MarketDataRepository(db_path)
        tables = {row[0] for row in repo.connection.execute("SHOW TABLES").fetchall()}
        if "daily_indicators" not in tables:
            raise AssertionError("daily_indicators table missing after migration")
        return {"schema_version": first, "tables": len(tables)}

    report.run_step("offline_schema_migration", schema_migration)

    def provider_semantics() -> dict[str, Any]:
        from tradingagents.market_data.providers.free_astock import FreeAStockProvider

        class _Backend:
            def list_mootdx_stocks(self) -> list[dict]:
                return [{"symbol": "600000"}]

            def fetch_tencent_daily_indicators(self, symbols: list[str]) -> list[dict]:
                return [{
                    "symbol": symbols[0],
                    "pe_ttm": 6.5,
                    "pb": 0.7,
                    "turnover_pct": 0.4,
                    "mcap_yi": 320.0,
                    "float_mcap_yi": 290.0,
                }]

        provider = FreeAStockProvider(backend=_Backend())
        today = provider.get_daily_indicators(shanghai_today())
        historical = provider.get_daily_indicators(date(2020, 1, 2))
        if today.status != DataStatus.OK:
            raise AssertionError(f"unexpected today status: {today.status}")
        if historical.status != DataStatus.NOT_AVAILABLE_YET:
            raise AssertionError(f"expected NOT_AVAILABLE_YET, got {historical.status}")
        return {
            "today_status": today.status.value,
            "historical_status": historical.status.value,
            "pit_level": PITLevel.BEST_EFFORT.value,
        }

    report.run_step("offline_provider_semantics", provider_semantics)

    class _IndicatorProvider:
        name = "fixture"

        def __init__(self, rows: list[dict]) -> None:
            self.rows = rows
            self.calls = 0

        def get_daily_indicators(self, _trade_date: date) -> DataResult[list[dict]]:
            self.calls += 1
            run_time = post_close_signal_time(trade_date)
            return DataResult(
                data=self.rows,
                status=DataStatus.OK,
                source=self.name,
                as_of=run_time,
                available_at=run_time,
                pit_level=PITLevel.BEST_EFFORT,
            )

        def probe_capabilities(self):
            from tradingagents.market_data.contracts import ProviderCapability

            run_time = datetime(2026, 1, 2, 10, 0, tzinfo=SHANGHAI)
            return DataResult(
                data=[
                    ProviderCapability(
                        dataset="daily_indicators",
                        endpoint="fixture",
                        permitted=True,
                        pit_level=PITLevel.BEST_EFFORT,
                        probed_at=run_time,
                    ),
                ],
                status=DataStatus.OK,
                source=self.name,
                as_of=run_time,
                available_at=run_time,
                pit_level=PITLevel.BEST_EFFORT,
            )

    def publish_idempotent() -> dict[str, Any]:
        rows = [
            {
                "symbol": "600001",
                "trade_date": trade_date,
                "pe_ttm": 6.5,
                "pb": 0.7,
                "turnover_pct": 0.4,
                "total_market_cap_cny": 320_000_000_000.0,
                "float_market_cap_cny": 290_000_000_000.0,
                "available_at": post_close_signal_time(trade_date),
                "source": "fixture",
            },
            {
                "symbol": "600002",
                "trade_date": trade_date,
                "pe_ttm": 8.0,
                "pb": 0.9,
                "turnover_pct": 0.5,
                "total_market_cap_cny": 220_000_000_000.0,
                "float_market_cap_cny": 200_000_000_000.0,
                "available_at": post_close_signal_time(trade_date),
                "source": "fixture",
            },
        ]
        paths = MarketDataPaths(home_dir=home_dir / "offline-indicators")
        repo = MarketDataRepository(paths.live_db_path, snapshot_dir=paths.snapshot_dir)
        try:
            load_fixture_into_repository(repo, json.loads(MINI_FIXTURE.read_text(encoding="utf-8")))
            sync = MarketDataSync(repo, _IndicatorProvider(rows), paths)
            first = sync.sync_daily_indicators(trade_date)
            second = sync.sync_daily_indicators(trade_date)
        finally:
            repo.connection.close()
        if first.status != SyncStatus.PUBLISHED or second.status != SyncStatus.PUBLISHED:
            raise AssertionError(f"{first.status} / {second.status}: {first.errors} / {second.errors}")
        if first.content_hash != second.content_hash:
            raise AssertionError("idempotent sync must reuse content hash")
        return {
            "version_id": first.version_id,
            "content_hash": first.content_hash,
        }

    report.run_step("offline_publish_idempotent", publish_idempotent)

    def repository_screen() -> dict[str, Any]:
        paths = MarketDataPaths(home_dir=home_dir / "offline-screen")
        repo = MarketDataRepository(paths.live_db_path, snapshot_dir=paths.snapshot_dir)
        try:
            load_fixture_into_repository(repo, json.loads(MINI_FIXTURE.read_text(encoding="utf-8")))
            config = ScreenerConfig(home_dir=paths.home_dir).model_copy(update={
                "universe": ScreenerConfig().universe.model_copy(update={
                    "min_listing_days": 1,
                    "min_avg_amount_20d": 1_000_000,
                }),
            })
            trade_day, signal_time, errors = resolve_signal_trade_date(
                repo,
                as_of="2026-01-03T15:30:00+08:00",
                today=date(2026, 1, 3),
            )
            if errors:
                raise AssertionError(errors)
            request = UniverseRequest(
                universe_type=UniverseType.CUSTOM,
                symbols=["600001"],
                as_of=signal_time,
            )
            screen = run_repository_screen(
                repo,
                config,
                paths.live_db_path,
                request,
                trade_date=trade_day,
                signal_time=signal_time,
            )
        finally:
            repo.connection.close()
        if screen.status != ScreeningStatus.OK:
            raise AssertionError(f"expected ok, got {screen.status}: {screen.errors}")
        return {
            "screening_status": screen.status.value,
            "source": "repository",
            "included_count": screen.included_count,
        }

    report.run_step("offline_repository_screen", repository_screen)

    def fixture_cli_regression() -> dict[str, Any]:
        import subprocess

        home = home_dir / "offline-fixture-cli"
        home.mkdir(parents=True, exist_ok=True)
        config_path = home / "screener.yaml"
        config_path.write_text(
            "\n".join([
                f"home_dir: {home}",
                "universe:",
                "  min_listing_days: 2",
                "  min_avg_amount_20d: 1000000",
                "strategy:",
                "  momentum_weight: 0.5",
                "  quality_weight: 0.5",
                "portfolio:",
                "  portfolio_value: 1000000",
                "  max_positions: 10",
                "  max_stock_weight: 0.10",
                "  max_industry_weight: 0.25",
                "  cash_buffer: 0.10",
                "event_enrichment:",
                "  enabled: false",
            ]) + "\n",
            encoding="utf-8",
        )
        cmd = [
            sys.executable,
            "-m",
            "tradingagents.screener.cli",
            "screen",
            "--fixture",
            str(MVP_FIXTURE),
            "--home-dir",
            str(home),
            "--config",
            str(config_path),
        ]
        env = os.environ.copy()
        env["PYTHONPATH"] = f"{PIP}:{ROOT}"
        completed = subprocess.run(
            cmd,
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise AssertionError(completed.stderr or completed.stdout)
        payload = json.loads(completed.stdout)
        if payload.get("fixture_sha256") != FROZEN_FIXTURE_SHA256:
            raise AssertionError("fixture sha256 drift")
        if payload.get("status") not in {"ok", "empty_universe"}:
            raise AssertionError(f"unexpected status: {payload.get('status')}")
        return {
            "fixture_sha256": payload["fixture_sha256"],
            "screening_status": payload["status"],
        }

    report.run_step("offline_fixture_cli_regression", fixture_cli_regression)

    def seven_analyst_registry() -> dict[str, Any]:
        if len(ANALYST_SPECS) != 7:
            raise AssertionError(f"expected 7 analysts, got {len(ANALYST_SPECS)}")
        if ANALYST_REPORT_MAP["social"] != "sentiment_report":
            raise AssertionError("social → sentiment_report regression")
        if ANALYST_ORDER[-1] != "lockup":
            raise AssertionError("unexpected analyst order")
        return {
            "analyst_count": len(ANALYST_SPECS),
            "order": list(ANALYST_ORDER),
        }

    report.run_step("offline_seven_analyst_registry", seven_analyst_registry)

    def mootdx_bounded_retry() -> dict[str, Any]:
        created: list[int] = []
        attempts = {"count": 0}

        def connect_fn():
            created.append(len(created) + 1)
            return object()

        def operation(_client: object) -> str:
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise ConnectionResetError("transport")
            return "ok"

        manager = MootdxConnectionManager(connect_fn=connect_fn)
        result = manager.call(operation)
        if result != "ok" or created != [1, 2]:
            raise AssertionError(f"retry contract broken: {result}, {created}")

        def _raise_parse() -> None:
            raise ValueError("parse")

        try:
            manager.call(lambda _client: _raise_parse())
        except ValueError:
            pass
        else:
            raise AssertionError("parse errors must not retry")
        return {"connect_attempts": len(created), "result": result}

    report.run_step("offline_mootdx_bounded_retry", mootdx_bounded_retry)


def _live_smoke_steps(report: AcceptanceReport, *, home_dir: Path, clear_proxy: bool) -> None:
    if clear_proxy:
        for key in (
            "HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
            "ALL_PROXY", "all_proxy",
        ):
            os.environ.pop(key, None)
    os.environ.setdefault("MOOTDX_SKIP_BESTIP", "1")

    from tradingagents.market_data.config import MarketDataPaths
    from tradingagents.market_data.fixture_store import load_fixture_into_repository
    from tradingagents.market_data.providers.free_astock_sources import (
        LiveFreeAStockSourceBackend,
        ProviderFetchError,
    )
    from tradingagents.market_data.repository import MarketDataRepository
    from tradingagents.screener.config import ScreenerConfig
    from tradingagents.screener.live import resolve_signal_trade_date, run_repository_screen
    from tradingagents.screener.report import ScreeningStatus
    from tradingagents.screener.universe_resolver import UniverseRequest, UniverseType
    from tradingagents.dataflows.mootdx_connection import get_mootdx_manager

    backend = LiveFreeAStockSourceBackend()

    def _raise_network(exc: ProviderFetchError) -> None:
        if exc.status == "network_error":
            raise AssertionError(f"network blocked: {exc.message}") from exc
        raise AssertionError(f"{exc.status}: {exc.message}") from exc

    def probe_network() -> dict[str, Any]:
        try:
            rows = backend.fetch_tencent_daily_indicators(["600000"])
        except ProviderFetchError as exc:
            _raise_network(exc)
        if not rows:
            raise AssertionError("tencent indicators returned no rows for 600000")
        row = rows[0]
        mcap = row.get("total_market_cap_cny") or row.get("mcap_yi")
        if not mcap or float(mcap) <= 0:
            raise AssertionError("tencent market cap must be positive")
        return {
            "symbol": row.get("symbol"),
            "market_cap_field": "total_market_cap_cny" if "total_market_cap_cny" in row else "mcap_yi",
        }

    probe = report.run_step("live_network_probe", probe_network, required=True)
    if not probe.ok:

        def skip(name: str) -> dict[str, Any]:
            return {"skipped": True, "reason": probe.error or "network probe failed"}

        report.run_step("live_mootdx_connect", lambda: skip("mootdx"), required=False)
        report.run_step("live_repository_screen", lambda: skip("screen"), required=False)
        return

    def mootdx_connect() -> dict[str, Any]:
        frame = get_mootdx_manager().call(lambda client: client.stocks(market=0))
        if frame is None or len(frame) < 1:
            raise AssertionError("mootdx stocks(market=0) returned empty frame")
        return {"row_count": len(frame)}

    report.run_step("live_mootdx_connect", mootdx_connect, required=True)

    def repository_screen() -> dict[str, Any]:
        paths = MarketDataPaths(home_dir=home_dir / "live-screen")
        repo = MarketDataRepository(paths.live_db_path, snapshot_dir=paths.snapshot_dir)
        try:
            load_fixture_into_repository(repo, json.loads(MINI_FIXTURE.read_text(encoding="utf-8")))
            config = ScreenerConfig(home_dir=paths.home_dir).model_copy(update={
                "universe": ScreenerConfig().universe.model_copy(update={
                    "min_listing_days": 1,
                    "min_avg_amount_20d": 1_000_000,
                }),
            })
            trade_day, signal_time, errors = resolve_signal_trade_date(
                repo,
                as_of="2026-01-03T15:30:00+08:00",
                today=date(2026, 1, 3),
            )
            if errors:
                raise AssertionError(errors)
            request = UniverseRequest(
                universe_type=UniverseType.CUSTOM,
                symbols=["600001"],
                as_of=signal_time,
            )
            screen = run_repository_screen(
                repo,
                config,
                paths.live_db_path,
                request,
                trade_date=trade_day,
                signal_time=signal_time,
            )
        finally:
            repo.connection.close()
        if screen.status != ScreeningStatus.OK:
            raise AssertionError(f"expected ok, got {screen.status}: {screen.errors}")
        return {
            "screening_status": screen.status.value,
            "source": "repository",
        }

    report.run_step("live_repository_screen", repository_screen, required=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Accept existing-defects remediation tiers A/B",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="tier A: schema, provider, publish, repository screen, registry, mootdx retry",
    )
    parser.add_argument(
        "--live-smoke",
        action="store_true",
        help="tier B: tencent indicators + mootdx + repository screen (BLOCKED if network down)",
    )
    parser.add_argument("--home-dir", default="/tmp/ta-accept-remediation")
    parser.add_argument(
        "--network-mode",
        choices=("direct", "system"),
        default="direct",
        help="direct clears proxy env vars before live smoke",
    )
    parser.add_argument("--json-out", default=None)
    args = parser.parse_args(argv)

    if not (args.offline or args.live_smoke):
        args.offline = True

    home_dir = Path(args.home_dir).expanduser()
    home_dir.mkdir(parents=True, exist_ok=True)

    modes: list[str] = []
    if args.offline:
        modes.append("offline")
    if args.live_smoke:
        modes.append("live-smoke")

    report = AcceptanceReport(modes=modes, home_dir=home_dir)
    try:
        if args.offline:
            _offline_steps(report, home_dir)
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
