"""Portfolio weights must flow into backtest targets."""

from __future__ import annotations

import json
from pathlib import Path

from tradingagents.screener.config import ScreenerConfig
from tradingagents.screener.pipeline import run_fixture_backtest


def test_target_weights_follow_portfolio_not_equal_weight(tmp_path):
    fixture = json.loads(Path("tests/fixtures/screener/mvp_market.json").read_text(encoding="utf-8"))
    base = ScreenerConfig()
    config = base.model_copy(update={
        "portfolio": base.portfolio.model_copy(update={
            "max_stock_weight": 0.50,
            "max_industry_weight": 0.80,
            "cash_buffer": 0.05,
        })
    })
    result = run_fixture_backtest(fixture, config, tmp_path / "market.duckdb")
    weights = list(result["target_weights"].values())
    assert len(weights) >= 2
    assert max(weights) - min(weights) > 1e-6
