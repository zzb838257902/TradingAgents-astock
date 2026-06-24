"""Acceptance wrapper tests for Stage 6A paper operations."""

from __future__ import annotations

from pathlib import Path

from tradingagents.paper.acceptance import run_offline_acceptance


def test_offline_acceptance_passes():
    payload = run_offline_acceptance(
        Path("tests/fixtures/paper/five_day_market.json"),
    )
    assert payload["passed"] is True
    assert payload["tier"] == "A"
    names = {step["name"] for step in payload["steps"]}
    assert "five_day_replay" in names
    assert "crash_recovery" in names
