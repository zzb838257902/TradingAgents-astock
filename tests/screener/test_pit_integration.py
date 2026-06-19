"""PIT integration tests for historical fixture backtest."""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tradingagents.screener.config import ScreenerConfig
from tradingagents.screener.pipeline import run_fixture_backtest


FIXTURE_PATH = Path("tests/fixtures/screener/mvp_market.json")


@pytest.fixture
def base_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def config() -> ScreenerConfig:
    return ScreenerConfig()


def test_future_financial_available_at_does_not_affect_scoring(base_fixture, config, tmp_path):
    baseline = run_fixture_backtest(base_fixture, config, tmp_path / "a.duckdb")

    mutated = copy.deepcopy(base_fixture)
    mutated["financials"].append({
        "symbol": "600002",
        "report_period": "2099-09-30",
        "available_at": "2099-01-01T00:00:00+00:00",
        "roe": 0.99,
        "operating_cashflow": 999,
        "net_profit": 999,
        "debt_ratio": 0.01,
    })

    after = run_fixture_backtest(mutated, config, tmp_path / "b.duckdb")
    assert baseline["ranking"] == after["ranking"]
    assert baseline["top_symbol"] == after["top_symbol"]


def test_rejects_non_pit_required_dataset(base_fixture, config, tmp_path):
    mutated = copy.deepcopy(base_fixture)
    mutated["datasets"]["financials"] = "best_effort"
    with pytest.raises(ValueError, match="historical backtest requires pit_required"):
        run_fixture_backtest(mutated, config, tmp_path / "bad.duckdb")


def test_repository_filters_financials_by_available_at(base_fixture, tmp_path):
    from tradingagents.market_data.fixture_store import load_fixture_into_repository
    from tradingagents.market_data.repository import MarketDataRepository

    repo = MarketDataRepository(tmp_path / "market.duckdb")
    load_fixture_into_repository(repo, base_fixture)
    before_pub = datetime(2025, 10, 29, tzinfo=timezone.utc)
    after_pub = datetime(2025, 10, 31, tzinfo=timezone.utc)
    assert repo.get_financials(["600002"], before_pub) == []
    assert len(repo.get_financials(["600002"], after_pub)) == 1
