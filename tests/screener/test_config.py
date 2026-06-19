from pathlib import Path

import pytest

from tradingagents.screener.config import ScreenerConfig


def test_loads_mvp_config(tmp_path: Path):
    path = tmp_path / "screener.yaml"
    path.write_text(
        """
home_dir: /tmp/tradingagents-test
universe:
  min_listing_days: 60
  min_avg_amount_20d: 50000000
strategy:
  momentum_weight: 0.5
  quality_weight: 0.5
portfolio:
  portfolio_value: 1000000
  max_positions: 10
  max_stock_weight: 0.10
  max_industry_weight: 0.25
  cash_buffer: 0.10
""",
        encoding="utf-8",
    )
    config = ScreenerConfig.from_yaml(path)
    assert config.universe.min_listing_days == 60
    assert config.strategy.momentum_weight + config.strategy.quality_weight == 1
    assert config.portfolio.portfolio_value == 1_000_000


def test_rejects_unknown_config_key(tmp_path: Path):
    path = tmp_path / "bad.yaml"
    path.write_text("unknown_key: true\n", encoding="utf-8")
    with pytest.raises(ValueError):
        ScreenerConfig.from_yaml(path)


def test_rejects_strategy_weights_that_do_not_sum_to_one():
    with pytest.raises(ValueError, match="sum to 1"):
        ScreenerConfig.model_validate({
            "strategy": {"momentum_weight": 0.8, "quality_weight": 0.5}
        })
