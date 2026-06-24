import pandas as pd

_DEFAULT_MOMENTUM_LOOKBACKS = (2, 20, 60)
_DEFAULT_MOMENTUM_WEIGHTS = (0.2, 0.35, 0.45)


def compute_momentum(closes: pd.Series, signal_date: str, lookback: int) -> float:
    visible = closes.loc[:signal_date]
    if len(visible) < lookback + 1:
        raise ValueError("insufficient price history")
    return round(float(visible.iloc[-1] / visible.iloc[-lookback - 1] - 1), 10)


def compute_blended_momentum(
    closes: pd.Series,
    signal_date: str,
    *,
    lookbacks: tuple[int, ...] = _DEFAULT_MOMENTUM_LOOKBACKS,
    weights: tuple[float, ...] = _DEFAULT_MOMENTUM_WEIGHTS,
) -> float:
    if len(lookbacks) != len(weights):
        raise ValueError("lookbacks and weights must have the same length")
    visible = closes.loc[:signal_date]
    weighted_total = 0.0
    weight_sum = 0.0
    for lookback, weight in zip(lookbacks, weights):
        if len(visible) < lookback + 1:
            continue
        weighted_total += compute_momentum(closes, signal_date, lookback) * weight
        weight_sum += weight
    if weight_sum <= 0:
        raise ValueError("insufficient price history")
    return round(weighted_total / weight_sum, 10)


def compute_quality(
    roe: float, operating_cashflow: float, net_profit: float, debt_ratio: float
) -> float:
    cash_conversion = operating_cashflow / max(abs(net_profit), 1e-9)
    return 0.45 * roe + 0.35 * cash_conversion - 0.20 * debt_ratio


def compute_valuation(
    pe_ttm: float | None,
    pb: float | None,
    turnover_pct: float | None,
) -> float:
    """Raw valuation signal; higher is better before cross-sectional rank_score."""
    score = 0.0
    weight = 0.0
    if pe_ttm is not None and pe_ttm > 0:
        score += 1.0 / pe_ttm
        weight += 0.45
    if pb is not None and pb > 0:
        score += 1.0 / pb
        weight += 0.45
    if turnover_pct is not None and turnover_pct >= 0:
        score += turnover_pct
        weight += 0.10
    if weight <= 0:
        return 0.0
    return score / weight


def rank_score(values: pd.Series) -> pd.Series:
    if values.isna().any():
        raise ValueError("factor values must be imputed or excluded before ranking")
    if len(values) == 1:
        return pd.Series(50.0, index=values.index)
    return (values.rank(method="average") - 1) / (len(values) - 1) * 100
