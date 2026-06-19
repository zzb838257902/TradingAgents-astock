import pandas as pd

from tradingagents.screener.factors import compute_momentum, compute_quality, rank_score


def test_momentum_uses_only_rows_up_to_signal_date():
    closes = pd.Series([10.0, 11.0, 12.0, 50.0], index=pd.date_range("2026-01-01", periods=4))
    assert compute_momentum(closes, "2026-01-03", lookback=2) == 0.2


def test_quality_rewards_roe_and_cash_conversion_and_penalizes_leverage():
    good = compute_quality(roe=0.18, operating_cashflow=120, net_profit=100, debt_ratio=0.30)
    weak = compute_quality(roe=0.05, operating_cashflow=20, net_profit=100, debt_ratio=0.80)
    assert good > weak


def test_rank_score_maps_cross_section_to_zero_and_one_hundred():
    scores = rank_score(pd.Series({"A": 1.0, "B": 2.0, "C": 3.0}))
    assert scores.to_dict() == {"A": 0.0, "B": 50.0, "C": 100.0}
