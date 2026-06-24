#!/usr/bin/env python3
"""Layered Stage 6A paper acceptance runner."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
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
    from tradingagents.paper.acceptance import run_live_smoke_acceptance

    return run_live_smoke_acceptance(home_dir, run_cmd=_run_cmd)


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
