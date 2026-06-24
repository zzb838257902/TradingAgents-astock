"""Live repository fixture builder tests."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

from tradingagents.market_data.fixture_store import load_fixture_into_repository
from tradingagents.market_data.market_hours import post_close_signal_time
from tradingagents.market_data.repository import MarketDataRepository
from tradingagents.screener.live import build_fixture_from_repository

SHANGHAI = ZoneInfo("Asia/Shanghai")


def test_build_fixture_from_repository(tmp_path):
    fixture = json.loads(Path("tests/fixtures/market_data/provider_mini.json").read_text())
    repo = MarketDataRepository(tmp_path / "market.duckdb")
    load_fixture_into_repository(repo, fixture)
    signal_date = date(2026, 1, 2)
    signal_time = post_close_signal_time(signal_date)
    built = build_fixture_from_repository(
        repo,
        ["600001", "600002"],
        [date(2026, 1, 2), date(2026, 1, 3)],
        signal_time,
    )
    assert "2026-01-02" in built["bars"]
    assert "600001" in built["bars"]["2026-01-02"]
    assert "daily_indicators" in built
    assert built["datasets"]["daily_indicators"] == "best_effort"
