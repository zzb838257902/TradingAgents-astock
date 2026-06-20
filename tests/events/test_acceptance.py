"""Acceptance script integration tests (phase 5 Task 8)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "accept_event_enrichment.py"


def _run_accept(*args: str, timeout: int = 300) -> subprocess.CompletedProcess[str]:
    import os

    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": f"{ROOT / '.pip_packages'}:{ROOT}"},
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _parse_report(stdout: str) -> dict:
    return json.loads(stdout)


def test_accept_offline_and_recorded_contract_passes(tmp_path):
    home = tmp_path / "accept-offline"
    result = _run_accept(
        "--offline",
        "--recorded-contract",
        "--home-dir",
        str(home),
    )
    assert result.returncode == 0, result.stderr or result.stdout
    report = _parse_report(result.stdout)
    assert report["status"] == "PASS"
    assert report["exit_code"] == 0
    assert report["tiers"]["A_offline_fixture"] == "PASS"
    assert report["tiers"]["A_recorded_contract"] == "PASS"
    required_steps = [
        step for step in report["steps"] if step["required"] and step["name"].startswith("offline_")
    ]
    assert required_steps
    assert all(step["ok"] for step in required_steps)


def test_accept_offline_report_has_audit_fields(tmp_path):
    home = tmp_path / "accept-audit"
    result = _run_accept("--offline", "--home-dir", str(home))
    report = _parse_report(result.stdout)
    assert "duration_ms" in report
    assert "memory_peak_mb" in report
    assert "pit_levels" in report
    assert "dataset_versions" in report
    offline_names = {step["name"] for step in report["steps"]}
    assert "offline_success_empty" in offline_names
    assert "offline_network_error" in offline_names
    assert "offline_data_error" in offline_names
    assert "offline_five_day_replay" in offline_names


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
    if result.returncode == 0:
        assert report["status"] == "PASS"
        assert report["tiers"]["B_live_smoke"] == "PASS"
        live_steps = [s for s in report["steps"] if s["name"].startswith("live_") and s["required"]]
        assert all(step["ok"] for step in live_steps)
    else:
        assert report["status"] == "BLOCKED"
        assert report["exit_code"] == 2
        assert report["tiers"]["B_live_smoke"] == "BLOCKED"
        probe = next(s for s in report["steps"] if s["name"] == "live_network_probe")
        assert not probe["ok"]


@pytest.mark.parametrize(
    "mode",
    [
        pytest.param("--offline", id="offline"),
        pytest.param("--recorded-contract", id="recorded-contract"),
    ],
)
def test_accept_single_mode_exits_zero(tmp_path, mode: str):
    result = _run_accept(mode, "--home-dir", str(tmp_path / mode.lstrip("-")))
    assert result.returncode == 0, result.stderr or result.stdout
    report = _parse_report(result.stdout)
    assert report["status"] == "PASS"
