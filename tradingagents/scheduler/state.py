"""Persist scheduler job attempts and reports."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class JobKey:
    job_name: str
    trade_date: date
    config_hash: str
    universe_hash: str = ""

    def storage_id(self) -> str:
        universe_part = f"_{self.universe_hash[:12]}" if self.universe_hash else ""
        return (
            f"{self.job_name}_{self.trade_date.isoformat()}"
            f"_{self.config_hash[:12]}{universe_part}"
        )


class JobStateStore:
    def __init__(self, root: Path):
        self.root = root
        self.runs_dir = root / "runs"
        self.reports_dir = root / "reports"
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    def _run_path(self, key: JobKey) -> Path:
        return self.runs_dir / f"{key.storage_id()}.json"

    def _report_path(self, key: JobKey) -> Path:
        return self.reports_dir / f"{key.storage_id()}.json"

    def load_run(self, key: JobKey) -> dict[str, Any] | None:
        path = self._run_path(key)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def latest_success(self, key: JobKey) -> dict[str, Any] | None:
        payload = self.load_run(key)
        if payload is None:
            return None
        for attempt in reversed(payload.get("attempts", [])):
            if attempt.get("status") == "success":
                return attempt
        return None

    def begin_attempt(self, key: JobKey) -> int:
        payload = self.load_run(key) or {
            "job_name": key.job_name,
            "trade_date": key.trade_date.isoformat(),
            "config_hash": key.config_hash,
            "universe_hash": key.universe_hash,
            "attempts": [],
        }
        attempt_id = len(payload["attempts"]) + 1
        payload["attempts"].append({
            "attempt_id": attempt_id,
            "status": "running",
            "started_at": datetime.now(tz=SHANGHAI).isoformat(),
        })
        self._run_path(key).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return attempt_id

    def finish_attempt(
        self,
        key: JobKey,
        attempt_id: int,
        status: str,
        *,
        report_path: str | None = None,
        errors: list[str] | None = None,
    ) -> None:
        payload = self.load_run(key)
        if payload is None:
            return
        for attempt in payload["attempts"]:
            if attempt["attempt_id"] == attempt_id:
                attempt["status"] = status
                attempt["finished_at"] = datetime.now(tz=SHANGHAI).isoformat()
                if report_path is not None:
                    attempt["report_path"] = report_path
                if errors:
                    attempt["errors"] = errors
                break
        self._run_path(key).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def save_report(self, key: JobKey, report: dict[str, Any]) -> Path:
        path = self._report_path(key)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def load_report(self, key: JobKey) -> dict[str, Any] | None:
        path = self._report_path(key)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def list_runs(self) -> list[dict[str, Any]]:
        runs = []
        for path in sorted(self.runs_dir.glob("*.json")):
            runs.append(json.loads(path.read_text(encoding="utf-8")))
        return runs
