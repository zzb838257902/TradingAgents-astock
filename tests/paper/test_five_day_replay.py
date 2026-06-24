"""Five-day deterministic replay tests (Stage 6A Task 8)."""

from __future__ import annotations

from datetime import date

import pytest

from tradingagents.paper.five_day_replay import load_scenario, run_five_day_replay


@pytest.fixture
def scenario() -> dict:
    return load_scenario()


def test_five_day_replay_is_deterministic(tmp_path, scenario):
    first = run_five_day_replay(tmp_path / "first", scenario=scenario)
    second = run_five_day_replay(tmp_path / "second", scenario=scenario)
    assert first.fingerprint == second.fingerprint
    assert first.fill_count == 4
    assert first.nav_points == 5
    expected = scenario.get("expected_fingerprint")
    if expected:
        assert first.fingerprint == expected


def test_crash_recovery_matches_clean_replay(tmp_path, scenario):
    golden = run_five_day_replay(tmp_path / "golden", scenario=scenario)
    recovered = run_five_day_replay(
        tmp_path / "recovered",
        scenario=scenario,
        crash_on_execution_date=date(2026, 1, 8),
        recover_after_crash=True,
    )
    assert recovered.fingerprint == golden.fingerprint
