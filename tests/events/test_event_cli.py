"""CLI integration for optional event enrichment (phase 5 Task 7)."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from tradingagents.screener.cli import app

runner = CliRunner()


def test_screen_cli_accepts_event_enrichment_override(tmp_path: Path):
    result = runner.invoke(app, [
        "screen",
        "--fixture",
        "tests/fixtures/screener/mvp_market.json",
        "--home-dir",
        str(tmp_path),
        "--event-enrichment",
    ])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert payload["base_ranking"] == payload["ranking"]
    assert "event_ranking" in payload
    assert "enhanced_ranking" in payload
    assert "event_contributions" in payload


def test_screen_cli_disabled_enrichment_omits_ranking_sidecars(tmp_path: Path):
    result = runner.invoke(app, [
        "screen",
        "--fixture",
        "tests/fixtures/screener/mvp_market.json",
        "--home-dir",
        str(tmp_path),
        "--no-event-enrichment",
    ])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["base_ranking"] == []
    assert payload["enhanced_ranking"] == []
