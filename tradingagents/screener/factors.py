import pandas as pd


def compute_momentum(closes: pd.Series, signal_date: str, lookback: int) -> float:
    visible = closes.loc[:signal_date]
    if len(visible) < lookback + 1:
        raise ValueError("insufficient price history")
    return round(float(visible.iloc[-1] / visible.iloc[-lookback - 1] - 1), 10)


def compute_quality(
    roe: float, operating_cashflow: float, net_profit: float, debt_ratio: float
) -> float:
    cash_conversion = operating_cashflow / max(abs(net_profit), 1e-9)
    return 0.45 * roe + 0.35 * cash_conversion - 0.20 * debt_ratio


def rank_score(values: pd.Series) -> pd.Series:
    if values.isna().any():
        raise ValueError("factor values must be imputed or excluded before ranking")
    if len(values) == 1:
        return pd.Series(50.0, index=values.index)
    return (values.rank(method="average") - 1) / (len(values) - 1) * 100
