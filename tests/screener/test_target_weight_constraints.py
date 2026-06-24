"""Target weights must preserve portfolio cash and concentration limits."""

from __future__ import annotations

import json
from pathlib import Path


from tradingagents.screener.config import ScreenerConfig
from tradingagents.screener.pipeline import run_fixture_backtest

FIXTURE = Path("tests/fixtures/screener/mvp_market.json")


def _load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _assert_weight_constraints(result: dict, config: ScreenerConfig) -> None:
    weights = result["target_weights"]
    stock_sum = sum(weights.values())
    cash_weight = result["cash_weight"]
    industry_weights: dict[str, float] = {}
    for symbol, weight in weights.items():
        assert weight <= config.portfolio.max_stock_weight + 1e-9, symbol
        industry = result["industry_by_symbol"][symbol]
        industry_weights[industry] = industry_weights.get(industry, 0.0) + weight
    assert stock_sum <= 1.0 - config.portfolio.cash_buffer + 1e-9
    assert cash_weight >= config.portfolio.cash_buffer - 1e-9
    for industry, weight in industry_weights.items():
        assert weight <= config.portfolio.max_industry_weight + 1e-9, industry
    assert abs(stock_sum + cash_weight - 1.0) <= 1e-6


def test_default_config_respects_stock_industry_and_cash_limits(tmp_path):
    config = ScreenerConfig()
    result = run_fixture_backtest(_load_fixture(), config, tmp_path / "market.duckdb")
    _assert_weight_constraints(result, config)


def test_constraints_hold_with_fewer_max_positions(tmp_path):
    base = ScreenerConfig()
    config = base.model_copy(update={
        "portfolio": base.portfolio.model_copy(update={"max_positions": 2})
    })
    result = run_fixture_backtest(_load_fixture(), config, tmp_path / "market.duckdb")
    _assert_weight_constraints(result, config)


def test_backtest_orders_do_not_exceed_stock_weight_cap(tmp_path):
    config = ScreenerConfig()
    result = run_fixture_backtest(_load_fixture(), config, tmp_path / "market.duckdb")
    portfolio_value = config.portfolio.portfolio_value
    for order in result["orders"]:
        notional = order["shares"] * order["price"]
        assert notional / portfolio_value <= config.portfolio.max_stock_weight + 1e-9
