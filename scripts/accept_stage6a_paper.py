#!/usr/bin/env python3
"""Layered Stage 6A paper acceptance runner."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _env(extra: dict | None = None) -> dict:
    merged = os.environ.copy()
    merged["PYTHONPATH"] = f"{ROOT / '.pip_packages'}:{ROOT}"
    if extra:
        merged.update(extra)
    return merged


def run_offline() -> dict:
    from tradingagents.paper.acceptance import run_offline_acceptance

    return run_offline_acceptance()


def _run_cmd(cmd: list[str], *, timeout: int = 120) -> dict:
    completed = subprocess.run(
        cmd,
        cwd=ROOT,
        env=_env({"MOOTDX_SKIP_BESTIP": "1"}),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return {
        "cmd": cmd,
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def run_live_smoke(home_dir: Path) -> dict:
    home = str(home_dir.expanduser())
    fixture = str(ROOT / "tests/fixtures/market_data/provider_mini.json")
    steps = [
        {
            "name": "market_data_init",
            "result": _run_cmd([
                sys.executable,
                "-m",
                "tradingagents.market_data.cli",
                "init",
                "--home-dir",
                home,
                "--provider",
                "fixture",
                "--fixture",
                fixture,
            ]),
        },
        {
            "name": "paper_init",
            "result": _run_cmd([
                sys.executable,
                "-m",
                "tradingagents.paper.cli",
                "init",
                "--account-id",
                "demo",
                "--home-dir",
                home,
            ]),
        },
        {
            "name": "paper_status_readonly",
            "result": _run_cmd([
                sys.executable,
                "-m",
                "tradingagents.paper.cli",
                "status",
                "--account-id",
                "demo",
                "--home-dir",
                home,
            ]),
        },
        {
            "name": "scheduler_run_open_blocked_or_success",
            "result": _run_cmd([
                sys.executable,
                "-m",
                "tradingagents.scheduler.cli",
                "run-open",
                "--trade-date",
                "2026-01-02",
                "--account-id",
                "demo",
                "--home-dir",
                home,
                "--fixture",
                fixture,
            ]),
        },
    ]
    normalized = []
    passed = True
    for step in steps:
        result = step["result"]
        exit_code = result["exit_code"]
        ok = exit_code in {0, 2}
        if exit_code == 1:
            passed = False
        normalized.append(
            {
                "name": step["name"],
                "ok": ok,
                "exit_code": exit_code,
                "blocked": exit_code == 2,
            }
        )
    return {"tier": "B", "passed": passed, "steps": normalized}


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 6A paper acceptance")
    parser.add_argument("--offline", action="store_true", help="Run Tier A offline acceptance")
    parser.add_argument("--live-smoke", action="store_true", help="Run Tier B live smoke")
    parser.add_argument(
        "--home-dir",
        default=str(Path("/tmp/tradingagents-stage6a-smoke")),
        help="Home directory for Tier B smoke",
    )
    args = parser.parse_args()

    if args.offline:
        payload = run_offline()
    elif args.live_smoke:
        payload = run_live_smoke(Path(args.home_dir))
    else:
        parser.error("Specify --offline or --live-smoke")
        return 2

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
