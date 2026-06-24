#!/usr/bin/env python3
"""Build Tier C daily_manifest.json from scheduler job JSON outputs."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
PIP = ROOT / ".pip_packages"
if PIP.is_dir() and str(PIP) not in sys.path:
    sys.path.insert(0, str(PIP))


def _load_json(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"empty JSON file: {path}")
    return json.loads(text)


def _step_summary(payload: dict[str, Any]) -> str:
    exit_code = payload.get("exit_code")
    status = payload.get("status", "unknown")
    if exit_code == 2:
        return "blocked"
    if exit_code == 1 or status in {"data_error", "failed"}:
        return "failed"
    if status in {"completed", "completed_with_rejections"}:
        return "success"
    return str(status)


def _infer_status(
    open_payload: dict[str, Any] | None,
    after_close_payload: dict[str, Any] | None,
) -> str:
    exit_codes = [
        payload.get("exit_code")
        for payload in (open_payload, after_close_payload)
        if payload is not None
    ]
    if any(code == 1 for code in exit_codes):
        return "failed"
    return "completed"


def _counts_from_repo(
    home_dir: Path,
    account_id: str,
    trade_date: date,
) -> tuple[int, int]:
    from tradingagents.paper.config import PaperPaths
    from tradingagents.paper.contracts import OrderStatus
    from tradingagents.paper.repository import PaperRepository

    repo = PaperRepository(PaperPaths(home_dir=home_dir))
    try:
        fills = repo.list_fills(account_id, execution_date=trade_date)
        orders = repo.connection.execute(
            """
            SELECT status
            FROM paper_orders
            WHERE account_id = ? AND execution_date = ?
            """,
            [account_id, trade_date],
        ).fetchall()
    finally:
        repo.close()
    rejections = sum(1 for (status,) in orders if status == OrderStatus.REJECTED.value)
    return len(fills), rejections


def build_manifest(
    *,
    trade_date: str,
    open_payload: dict[str, Any] | None,
    after_close_payload: dict[str, Any] | None,
    fills: int | None,
    rejections: int | None,
    recovery_count: int,
    manual_intervention: bool,
    open_defects: list[str],
    notes: str | None,
) -> dict[str, Any]:
    steps: dict[str, str] = {}
    if open_payload is not None:
        steps["run_open"] = _step_summary(open_payload)
    if after_close_payload is not None:
        steps["run_after_close"] = _step_summary(after_close_payload)

    errors: list[str] = []
    for payload in (open_payload, after_close_payload):
        if payload:
            errors.extend(payload.get("errors") or [])

    manifest: dict[str, Any] = {
        "trade_date": trade_date,
        "open_date": trade_date,
        "status": _infer_status(open_payload, after_close_payload),
        "steps": steps,
        "fills": fills,
        "rejections": rejections,
        "recovery_count": recovery_count,
        "manual_intervention": manual_intervention,
        "open_defects": open_defects,
        "errors": errors,
        "collected_at": datetime.now(timezone.utc).isoformat(),
    }
    if notes:
        manifest["notes"] = notes
    if open_payload is not None:
        manifest["run_open"] = {
            "run_id": open_payload.get("run_id"),
            "exit_code": open_payload.get("exit_code"),
            "job_status": open_payload.get("status"),
        }
    if after_close_payload is not None:
        manifest["run_after_close"] = {
            "run_id": after_close_payload.get("run_id"),
            "exit_code": after_close_payload.get("exit_code"),
            "job_status": after_close_payload.get("status"),
        }
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect Stage 6A Tier C daily manifest")
    parser.add_argument("--observation-dir", type=Path, required=True)
    parser.add_argument("--trade-date", required=True, help="YYYY-MM-DD trading day")
    parser.add_argument("--run-open", type=Path, help="JSON stdout from run-open")
    parser.add_argument("--run-after-close", type=Path, help="JSON stdout from run-after-close")
    parser.add_argument("--home-dir", type=Path, help="Paper home dir for fill/rejection counts")
    parser.add_argument("--account-id", default="demo")
    parser.add_argument("--fills", type=int, help="Override fill count")
    parser.add_argument("--rejections", type=int, help="Override rejection count")
    parser.add_argument("--recovery-count", type=int, default=0)
    parser.add_argument("--manual-intervention", action="store_true")
    parser.add_argument("--open-defect", action="append", default=[])
    parser.add_argument("--notes", default="")
    args = parser.parse_args()

    open_payload = _load_json(args.run_open) if args.run_open else None
    after_close_payload = (
        _load_json(args.run_after_close) if args.run_after_close else None
    )

    fills = args.fills
    rejections = args.rejections
    if args.home_dir is not None and (fills is None or rejections is None):
        repo_fills, repo_rejections = _counts_from_repo(
            args.home_dir.expanduser(),
            args.account_id,
            date.fromisoformat(args.trade_date),
        )
        fills = repo_fills if fills is None else fills
        rejections = repo_rejections if rejections is None else rejections

    day_dir = args.observation_dir.expanduser() / args.trade_date
    day_dir.mkdir(parents=True, exist_ok=True)
    manifest = build_manifest(
        trade_date=args.trade_date,
        open_payload=open_payload,
        after_close_payload=after_close_payload,
        fills=fills,
        rejections=rejections,
        recovery_count=args.recovery_count,
        manual_intervention=args.manual_intervention,
        open_defects=list(args.open_defect or []),
        notes=args.notes or None,
    )
    out_path = day_dir / "daily_manifest.json"
    out_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"written": str(out_path), "status": manifest["status"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
