"""Forward-adjusted momentum and historical snapshot gate tests."""

from __future__ import annotations

import copy
import json
from datetime import date
from pathlib import Path

import pytest

from tradingagents.market_data.contracts import PriceBasis
from tradingagents.market_data.fixture_store import load_fixture_into_repository
from tradingagents.market_data.repository import MarketDataRepository
from tradingagents.screener.config import ScreenerConfig, StrategyConfig
from tradingagents.screener.pipeline import run_screen
from tradingagents.screener.report import ScreeningStatus

FIXTURE_PATH = Path("tests/fixtures/screener/mvp_market.json")


@pytest.fixture
def base_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_forward_adjusted_momentum_ignores_dividend_gap(base_fixture, tmp_path):
    """A cash dividend should not inflate momentum when price_basis=forward_adjusted."""
    fixture = copy.deepcopy(base_fixture)
    trading_dates = sorted(fixture["bars"].keys())
    symbol = "600002"

    for day in trading_dates:
        fixture["bars"][day][symbol]["close"] = 10.0
    ex_date = trading_dates[-3]
    fixture["bars"][ex_date][symbol]["close"] = 5.0
    fixture["adjustment_factors"] = [{
        "symbol": symbol,
        "trade_date": ex_date,
        "factor": 0.5,
        "available_at": f"{ex_date}T15:00:00+08:00",
        "source": "fixture",
    }]

    adjusted_config = ScreenerConfig(
        strategy=StrategyConfig(price_basis=PriceBasis.FORWARD_ADJUSTED),
    )
    raw_config = ScreenerConfig(strategy=StrategyConfig(price_basis=PriceBasis.RAW))

    adjusted = run_screen(fixture, adjusted_config, tmp_path / "adj.duckdb")
    raw = run_screen(fixture, raw_config, tmp_path / "raw.duckdb")

    assert adjusted.status == ScreeningStatus.OK
    assert raw.status == ScreeningStatus.OK
    assert adjusted.data_quality["price_basis"] == "forward_adjusted"
    assert raw.data_quality["experimental_not_for_formal_evaluation"] is True
    assert symbol in adjusted.factor_contributions
    assert symbol in raw.factor_contributions
    assert adjusted.factor_contributions[symbol]["momentum"] != raw.factor_contributions[symbol]["momentum"]


def test_historical_backtest_requires_security_snapshot(base_fixture, tmp_path):
    repo = MarketDataRepository(tmp_path / "market.duckdb")
    load_fixture_into_repository(repo, base_fixture)
    trading_dates = sorted(base_fixture["bars"].keys())
    signal_date = date.fromisoformat(trading_dates[-2])
    repo.connection.execute(
        "DELETE FROM security_master_snapshots WHERE snapshot_date = ?",
        [signal_date],
    )

    config = ScreenerConfig()
    report = run_screen(
        base_fixture,
        config,
        tmp_path / "market.duckdb",
        reload=False,
    )
    assert report.status == ScreeningStatus.DATA_ERROR
    assert any("security_master snapshot" in error for error in report.errors)


def test_mvp_fixture_still_passes_with_forward_adjusted_default(base_fixture, tmp_path):
    report = run_screen(base_fixture, ScreenerConfig(), tmp_path / "market.duckdb")
    assert report.status == ScreeningStatus.OK
    assert report.data_quality["price_basis"] == "forward_adjusted"


def test_forward_adjusted_requires_factor_coverage(base_fixture, tmp_path):
    repo = MarketDataRepository(tmp_path / "market.duckdb")
    load_fixture_into_repository(repo, base_fixture)
    repo.connection.execute("DELETE FROM adjustment_factors")
    report = run_screen(
        base_fixture,
        ScreenerConfig(),
        tmp_path / "market.duckdb",
        reload=False,
    )
    assert report.status == ScreeningStatus.DATA_ERROR
    assert any("adjustment factor coverage" in error for error in report.errors)
