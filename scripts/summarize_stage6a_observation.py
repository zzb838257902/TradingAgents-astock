#!/usr/bin/env python3
"""Summarize Tier C Stage 6A observation manifests."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def summarize(observation_dir: Path) -> dict:
    manifests = sorted(observation_dir.glob("**/daily_manifest.json"))
    open_dates: set[str] = set()
    rows = []
    for path in manifests:
        payload = json.loads(path.read_text(encoding="utf-8"))
        trade_date = payload.get("trade_date") or payload.get("open_date")
        if trade_date:
            open_dates.add(str(trade_date))
        rows.append(
            {
                "path": str(path),
                "trade_date": trade_date,
                "status": payload.get("status"),
                "steps": payload.get("steps", {}),
                "fills": payload.get("fills"),
                "rejections": payload.get("rejections"),
                "recovery_count": payload.get("recovery_count", 0),
                "manual_intervention": payload.get("manual_intervention", False),
                "open_defects": payload.get("open_defects", []),
            }
        )
    distinct_days = len(open_dates)
    passed = distinct_days >= 5 and all(row.get("status") == "completed" for row in rows[-5:])
    return {
        "observation_dir": str(observation_dir),
        "manifest_count": len(rows),
        "distinct_open_dates": distinct_days,
        "passed": passed,
        "requires_five_dates": True,
        "rows": rows,
        "note": "Tier C PASS requires five distinct real trading-day manifests",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize Stage 6A Tier C evidence")
    parser.add_argument("observation_dir", type=Path, help="Directory containing daily manifests")
    args = parser.parse_args()
    payload = summarize(args.observation_dir.expanduser())
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
