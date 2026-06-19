from decimal import Decimal, ROUND_DOWN

import pandas as pd

from tradingagents.screener.models import PortfolioSuggestion, PositionSuggestion

LOT_SIZE = 100


def _to_decimal(value: float) -> Decimal:
    return Decimal(str(value))


def construct_portfolio(
    candidates: pd.DataFrame,
    portfolio_value: float,
    max_positions: int,
    max_stock_weight: float,
    max_industry_weight: float,
    cash_buffer: float,
    max_participation_rate: float,
) -> PortfolioSuggestion:
    total = _to_decimal(portfolio_value)
    cash = (total * _to_decimal(cash_buffer)).quantize(Decimal("0.01"))
    investable = total - cash
    industry_budget: dict[str, Decimal] = {}
    positions: list[PositionSuggestion] = []

    sorted_candidates = candidates.sort_values(
        ["score", "symbol"], ascending=[False, True]
    )

    for _, row in sorted_candidates.iterrows():
        if len(positions) >= max_positions:
            break
        if investable <= 0:
            break

        symbol = row["symbol"]
        industry = row["industry"]
        price = _to_decimal(row["price"])
        avg_volume = _to_decimal(row["avg_volume"])

        industry_used = industry_budget.get(industry, Decimal("0"))
        industry_remaining = _to_decimal(max_industry_weight) * total - industry_used
        if industry_remaining <= 0:
            continue

        stock_cap = _to_decimal(max_stock_weight) * total
        participation_cap = (
            avg_volume * _to_decimal(max_participation_rate) * price
        )
        budget = min(investable, stock_cap, industry_remaining, participation_cap)
        if budget < price * LOT_SIZE:
            continue

        shares = int(
            (budget / price / LOT_SIZE).to_integral_value(rounding=ROUND_DOWN)
        ) * LOT_SIZE
        if shares <= 0:
            continue

        market_value = (price * shares).quantize(Decimal("0.01"))
        if market_value > investable:
            shares = int(
                (investable / price / LOT_SIZE).to_integral_value(rounding=ROUND_DOWN)
            ) * LOT_SIZE
            if shares <= 0:
                continue
            market_value = (price * shares).quantize(Decimal("0.01"))

        positions.append(PositionSuggestion(
            symbol=symbol,
            industry=industry,
            shares=shares,
            price=float(price),
            market_value=float(market_value),
        ))
        investable -= market_value
        industry_budget[industry] = industry_used + market_value

    final_cash = float((cash + investable).quantize(Decimal("0.01")))
    return PortfolioSuggestion(positions=positions, cash=final_cash)
