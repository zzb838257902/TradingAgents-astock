import pandas as pd

from tradingagents.screener.strategy import score_candidates


def test_fixed_ensemble_keeps_absolute_and_industry_rank():
    frame = pd.DataFrame([
        {"symbol": "A", "industry": "电子", "momentum": 80, "quality": 60},
        {"symbol": "B", "industry": "电子", "momentum": 60, "quality": 80},
        {"symbol": "C", "industry": "银行", "momentum": 20, "quality": 100},
    ])
    result = score_candidates(frame, momentum_weight=0.5, quality_weight=0.5)
    assert result.set_index("symbol").loc["A", "ensemble_score"] == 70
    assert set(result.columns) >= {"absolute_rank", "industry_rank"}


def test_rejects_weights_not_summing_to_one():
    frame = pd.DataFrame([{"symbol": "A", "industry": "电子", "momentum": 80, "quality": 60}])
    try:
        score_candidates(frame, momentum_weight=0.8, quality_weight=0.8)
    except ValueError as exc:
        assert "sum to 1" in str(exc)
    else:
        raise AssertionError("expected invalid weights to fail")
