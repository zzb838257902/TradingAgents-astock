import pandas as pd


def score_candidates(
    frame: pd.DataFrame, momentum_weight: float, quality_weight: float
) -> pd.DataFrame:
    if abs(momentum_weight + quality_weight - 1.0) > 1e-9:
        raise ValueError("strategy weights must sum to 1")
    result = frame.copy()
    result["ensemble_score"] = (
        result["momentum"] * momentum_weight + result["quality"] * quality_weight
    )
    result["absolute_rank"] = result["ensemble_score"].rank(
        method="min", ascending=False
    ).astype(int)
    result["industry_rank"] = result.groupby("industry")["ensemble_score"].rank(
        method="min", ascending=False
    ).astype(int)
    return result.sort_values(["ensemble_score", "symbol"], ascending=[False, True])
