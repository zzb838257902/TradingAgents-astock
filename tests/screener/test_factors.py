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


def test_momentum_cross_section_assigns_distinct_scores():
    import pandas as pd

    from tradingagents.screener.factors import compute_momentum, rank_score

    symbols = ["A", "B", "C"]
    raw = {}
    for idx, symbol in enumerate(symbols):
        closes = pd.Series(
            [10.0, 10.0 + idx, 10.0 + idx * 2],
            index=pd.date_range("2026-01-01", periods=3),
        )
        raw[symbol] = compute_momentum(closes, "2026-01-03", lookback=2)
    scores = rank_score(pd.Series(raw))
    assert scores.nunique() == 3
    assert scores["C"] > scores["B"] > scores["A"]


def test_blended_momentum_reweights_available_lookbacks():
    closes = pd.Series(
        [10.0, 10.5, 11.0, 11.5, 12.0],
        index=pd.date_range("2026-01-01", periods=5),
    )
    from tradingagents.screener.factors import compute_blended_momentum

    assert compute_blended_momentum(closes, "2026-01-05") == compute_momentum(
        closes,
        "2026-01-05",
        lookback=2,
    )


def test_valuation_prefers_lower_positive_multiples():
    from tradingagents.screener.factors import compute_valuation

    cheap = compute_valuation(pe_ttm=10.0, pb=1.0, turnover_pct=1.0)
    rich = compute_valuation(pe_ttm=40.0, pb=4.0, turnover_pct=1.0)
    assert cheap > rich

