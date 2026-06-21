"""Acceptance script integration tests (defect remediation Task 7)."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "accept_existing_defect_remediation.py"


@dataclass
class _Step:
    name: str
    ok: bool
    required: bool = True
    error: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


def _run_accept(*args: str, timeout: int = 300) -> subprocess.CompletedProcess[str]:
    import os

    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        env={
            **os.environ,
            "PYTHONPATH": f"{ROOT / '.pip_packages'}:{ROOT}",
            "MOOTDX_SKIP_BESTIP": "1",
        },
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _parse_report(stdout: str) -> dict:
    return json.loads(stdout)


def test_compute_report_status_tencent_ok_mootdx_blocked():
    from scripts.accept_existing_defect_remediation import compute_report_status

    steps = [
        _Step("live_tencent_indicators", True),
        _Step(
            "live_mootdx_connect",
            False,
            error="AssertionError: network blocked: [Errno 113] No route to host",
        ),
        _Step("live_repository_screen", True),
    ]
    status, exit_code = compute_report_status(steps, ["live-smoke"])
    assert status == "BLOCKED"
    assert exit_code == 2


def test_compute_report_status_tencent_blocked_still_runs_other_steps():
    from scripts.accept_existing_defect_remediation import compute_report_status

    steps = [
        _Step(
            "live_tencent_indicators",
            False,
            error="AssertionError: network blocked: SSL handshake timed out",
        ),
        _Step(
            "live_mootdx_connect",
            False,
            error="AssertionError: network blocked: timed out after 60s",
        ),
        _Step("live_repository_screen", True),
    ]
    status, exit_code = compute_report_status(steps, ["live-smoke"])
    assert status == "BLOCKED"
    assert exit_code == 2
    assert sum(1 for step in steps if step.name.startswith("live_")) == 3


def test_compute_report_status_repository_failure_is_fail():
    from scripts.accept_existing_defect_remediation import compute_report_status

    steps = [
        _Step("live_tencent_indicators", True),
        _Step("live_mootdx_connect", True),
        _Step(
            "live_repository_screen",
            False,
            error="AssertionError: expected ok, got data_error",
        ),
    ]
    status, exit_code = compute_report_status(steps, ["live-smoke"])
    assert status == "FAIL"
    assert exit_code == 1


def test_run_probe_subprocess_timeout_is_hard():
    import os
    import time

    from scripts.accept_existing_defect_remediation import run_probe_subprocess

    start = time.monotonic()
    with pytest.raises(AssertionError, match="network blocked: timed out"):
        run_probe_subprocess(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            timeout_sec=0.05,
        )
    elapsed = time.monotonic() - start
    assert elapsed < 1.5


def test_accept_offline_passes(tmp_path):
    home = tmp_path / "accept-offline"
    result = _run_accept("--offline", "--home-dir", str(home))
    assert result.returncode == 0, result.stderr or result.stdout
    report = _parse_report(result.stdout)
    assert report["status"] == "PASS"
    assert report["exit_code"] == 0
    assert report["tiers"]["A_offline"] == "PASS"
    assert report["tiers"]["B_live_smoke"] == "SKIP"
    offline_steps = [
        step for step in report["steps"] if step["name"].startswith("offline_")
    ]
    assert len(offline_steps) >= 7
    assert all(step["ok"] for step in offline_steps if step["required"])


def test_accept_offline_report_covers_remediation_scope(tmp_path):
    home = tmp_path / "accept-scope"
    result = _run_accept("--offline", "--home-dir", str(home))
    report = _parse_report(result.stdout)
    names = {step["name"] for step in report["steps"]}
    assert "offline_schema_migration" in names
    assert "offline_provider_semantics" in names
    assert "offline_publish_idempotent" in names
    assert "offline_repository_screen" in names
    assert "offline_seven_analyst_registry" in names
    assert "offline_mootdx_bounded_retry" in names


def test_accept_live_smoke_pass_or_blocked(tmp_path):
    import os

    home = tmp_path / "accept-live"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--live-smoke", "--home-dir", str(home)],
        cwd=ROOT,
        env={
            **os.environ,
            "PYTHONPATH": f"{ROOT / '.pip_packages'}:{ROOT}",
            "MOOTDX_SKIP_BESTIP": "1",
            "MOOTDX_LIVE_SMOKE_TIMEOUT_SEC": "5",
        },
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    assert result.returncode in {0, 2}, result.stderr or result.stdout
    report = _parse_report(result.stdout)
    assert report["status"] in {"PASS", "BLOCKED"}
    live_steps = [step for step in report["steps"] if step["name"].startswith("live_")]
    assert len(live_steps) == 3
    if report["status"] == "BLOCKED":
        assert report["exit_code"] == 2
        assert report["tiers"]["B_live_smoke"] == "BLOCKED"
    else:
        assert report["exit_code"] == 0
        assert report["tiers"]["B_live_smoke"] == "PASS"
