"""Anti-lookahead regression tests for fixture screening pipeline."""

from __future__ import annotations

import copy
import json
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


def test_signal_uses_only_data_on_or_before_signal_date(base_fixture, config, tmp_path):
    baseline = run_fixture_backtest(base_fixture, config, tmp_path / "market.duckdb")

    mutated = copy.deepcopy(base_fixture)
    trading_dates = sorted(mutated["bars"].keys())
    last_date = trading_dates[-1]
    for symbol in mutated["bars"][last_date]:
        mutated["bars"][last_date][symbol]["close"] = 9999.0

    after = run_fixture_backtest(mutated, config, tmp_path / "market2.duckdb")

    assert baseline["top_symbol"] == after["top_symbol"]
    assert baseline["positions"] == after["positions"]
    assert baseline["orders"] == after["orders"]
    assert baseline["ranking"] == after["ranking"]
    assert baseline["target_weights"] == after["target_weights"]
