#!/usr/bin/env python3
"""Acceptance runner for the free default market-data path.

Offline steps always run without TUSHARE_TOKEN and without live network probes.
Use --live to exercise real network sync (requires mootdx/sina/eastmoney).

Live snapshot datasets (security-master, daily live) require --snapshot-date == Shanghai
today. Screening steps use the latest open trade day on or before today.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_TRADE_DATE = "2026-01-02"


def _resolve_live_dates(
    *,
    snapshot_date: str | None,
    screening_date: str | None,
) -> tuple[str, str, str]:
    from tradingagents.market_data.providers.free_astock_sources import LiveFreeAStockSourceBackend
    from tradingagents.market_data.sync_policy import shanghai_today

    today = shanghai_today()
    snapshot = date.fromisoformat(snapshot_date) if snapshot_date else today
    backend = LiveFreeAStockSourceBackend()
    open_dates = backend.fetch_sse_trade_dates(today - timedelta(days=31), today)
    if screening_date:
        screening = date.fromisoformat(screening_date)
    elif open_dates:
        screening = open_dates[-1]
    else:
        screening = today
        while screening.weekday() >= 5:
            screening -= timedelta(days=1)
    if open_dates:
        prior = [day for day in open_dates if day <= screening]
        backfill_start = prior[-5] if len(prior) >= 5 else prior[0]
    else:
        backfill_start = screening - timedelta(days=7)
    return snapshot.isoformat(), screening.isoformat(), backfill_start.isoformat()


def _run(cmd: list[str], *, env: dict | None = None, clear_proxy: bool = False) -> dict:
    merged = os.environ.copy()
    merged["PYTHONPATH"] = f"{ROOT / '.pip_packages'}:{ROOT}"
    if clear_proxy:
        for key in (
            "HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
            "ALL_PROXY", "all_proxy", "SOCKS_PROXY", "SOCKS5_PROXY",
            "socks_proxy", "socks5_proxy", "GIT_HTTP_PROXY", "GIT_HTTPS_PROXY",
        ):
            merged.pop(key, None)
    if env:
        merged.update(env)
    completed = subprocess.run(
        cmd,
        cwd=ROOT,
        env=merged,
        capture_output=True,
        text=True,
    )
    return {
        "cmd": cmd,
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Accept free default data path")
    parser.add_argument("--home-dir", default="/tmp/ta-accept-free")
    parser.add_argument(
        "--snapshot-date",
        default=None,
        help="YYYY-MM-DD for live security/daily snapshot; defaults to Shanghai today",
    )
    parser.add_argument(
        "--screening-date",
        default=None,
        help="YYYY-MM-DD for scheduler/backfill; defaults to latest open trade day",
    )
    parser.add_argument("--live", action="store_true", help="run real network sync steps")
    args = parser.parse_args()

    home_dir = Path(args.home_dir).expanduser()
    fixture_home = home_dir if not args.live else home_dir / "offline-fixture"
    live_home = home_dir if not args.live else home_dir / "live"
    if args.live:
        snapshot_date, screening_date, backfill_start = _resolve_live_dates(
            snapshot_date=args.snapshot_date,
            screening_date=args.screening_date,
        )
        fixture_trade_date = FIXTURE_TRADE_DATE
    else:
        snapshot_date = screening_date = backfill_start = (
            args.screening_date or args.snapshot_date or FIXTURE_TRADE_DATE
        )
        fixture_trade_date = snapshot_date
    report: dict = {
        "home_dir": str(home_dir),
        "fixture_home_dir": str(fixture_home),
        "live_home_dir": str(live_home) if args.live else None,
        "snapshot_date": snapshot_date,
        "screening_date": screening_date,
        "backfill_start": backfill_start,
        "fixture_trade_date": fixture_trade_date,
        "live": args.live,
        "steps": [],
        "passed": True,
    }

    def step(
        name: str,
        result: dict,
        *,
        required: bool = True,
        expect_status: set[str] | None = None,
    ) -> None:
        ok = result["exit_code"] == 0
        if ok and result.get("stdout", "").strip().startswith("{"):
            try:
                payload = json.loads(result["stdout"])
                status = payload.get("status")
                if expect_status is not None:
                    ok = status in expect_status
                elif status is not None and status not in {"published", "success"}:
                    ok = False
                if not ok:
                    result = {**result, "sync_status": status}
            except json.JSONDecodeError:
                pass
        report["steps"].append({"name": name, "ok": ok, "required": required, **result})
        if required and not ok:
            report["passed"] = False

    step(
        "pytest_offline_core",
        _run([
            sys.executable, "-m", "pytest", "-q",
            "tests/market_data/test_free_astock_provider.py",
            "tests/market_data/test_free_astock_sources.py",
            "tests/market_data/test_sync_free_provider.py",
            "tests/market_data/test_security_snapshots.py",
            "tests/market_data/test_adjustments.py",
            "tests/market_data/test_sync_policy.py",
            "tests/market_data/test_staging_publish.py",
            "tests/scheduler/test_jobs.py",
        ]),
    )

    step(
        "scheduler_fixture_after_close",
        _run([
            sys.executable, "-m", "tradingagents.scheduler.cli", "after-close",
            "--trade-date", fixture_trade_date,
            "--home-dir", str(fixture_home),
            "--fixture", "tests/fixtures/market_data/provider_mini.json",
        ]),
        expect_status={"success"},
    )

    if args.live:
        env = {
            "TRADINGAGENTS_MARKET_DATA_PROVIDER": "free",
        }
        live_run = lambda cmd: _run(cmd, env=env, clear_proxy=True)
        step(
            "market_data_init_free",
            live_run([
                sys.executable, "-m", "tradingagents.market_data.cli", "init",
                "--home-dir", str(live_home),
                "--provider", "free",
            ]),
        )
        sync_cmds = (
            ("security-master", [
                sys.executable, "-m", "tradingagents.market_data.cli", "sync",
                "--dataset", "security-master",
                "--as-of", snapshot_date,
                "--home-dir", str(live_home),
                "--provider", "free",
            ]),
            ("trade-calendar", [
                sys.executable, "-m", "tradingagents.market_data.cli", "sync",
                "--dataset", "trade-calendar",
                "--start", f"{screening_date[:7]}-01",
                "--end", screening_date,
                "--home-dir", str(live_home),
                "--provider", "free",
            ]),
            ("daily-live", [
                sys.executable, "-m", "tradingagents.market_data.cli", "sync",
                "--dataset", "daily",
                "--start", snapshot_date,
                "--home-dir", str(live_home),
                "--provider", "free",
            ]),
            ("daily-backfill", [
                sys.executable, "-m", "tradingagents.market_data.cli", "sync",
                "--dataset", "daily",
                "--start", backfill_start,
                "--end", screening_date,
                "--home-dir", str(live_home),
                "--provider", "free",
            ]),
            ("financials", [
                sys.executable, "-m", "tradingagents.market_data.cli", "sync",
                "--dataset", "financials",
                "--as-of", f"{screening_date}T15:30:00+08:00",
                "--home-dir", str(live_home),
                "--provider", "free",
            ]),
            ("adjustment-factors", [
                sys.executable, "-m", "tradingagents.market_data.cli", "sync",
                "--dataset", "adjustment-factors",
                "--as-of", screening_date,
                "--home-dir", str(live_home),
                "--provider", "free",
            ]),
        )
        for dataset_name, dataset_cmd in sync_cmds:
            step(f"sync_{dataset_name}", live_run(dataset_cmd))

        step(
            "scheduler_live_after_close",
            live_run([
                sys.executable, "-m", "tradingagents.scheduler.cli", "after-close",
                "--trade-date", screening_date,
                "--home-dir", str(live_home),
                "--provider", "free",
                "--force",
            ]),
            expect_status={"success"},
        )

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
