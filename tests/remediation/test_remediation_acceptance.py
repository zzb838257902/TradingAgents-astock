"""Acceptance script integration tests (defect remediation Task 7)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "accept_existing_defect_remediation.py"


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
    home = tmp_path / "accept-live"
    result = _run_accept(
        "--live-smoke",
        "--home-dir",
        str(home),
        timeout=180,
    )
    assert result.returncode in {0, 2}, result.stderr or result.stdout
    report = _parse_report(result.stdout)
    assert report["status"] in {"PASS", "BLOCKED"}
    if report["status"] == "BLOCKED":
        assert report["exit_code"] == 2
        assert report["tiers"]["B_live_smoke"] == "BLOCKED"
    else:
        assert report["exit_code"] == 0
        assert report["tiers"]["B_live_smoke"] == "PASS"
