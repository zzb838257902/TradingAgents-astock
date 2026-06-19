import pandas as pd


def performance_metrics(equity: pd.Series, periods_per_year: int = 252) -> dict[str, float]:
    returns = equity.pct_change().dropna()
    total_return = float(equity.iloc[-1] / equity.iloc[0] - 1)
    annualized = float((1 + total_return) ** (periods_per_year / max(len(returns), 1)) - 1)
    drawdown = equity / equity.cummax() - 1
    volatility = float(returns.std(ddof=1) * periods_per_year ** 0.5) if len(returns) > 1 else 0.0
    sharpe = float(annualized / volatility) if volatility else 0.0
    return {
        "total_return": total_return,
        "annualized_return": annualized,
        "max_drawdown": float(drawdown.min()),
        "annualized_volatility": volatility,
        "sharpe": sharpe,
    }
