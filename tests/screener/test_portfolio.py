import pandas as pd

from tradingagents.screener.portfolio import construct_portfolio


def test_enforces_cash_stock_industry_and_lot_constraints():
    candidates = pd.DataFrame([
        {"symbol": "A", "industry": "电子", "score": 100, "price": 10, "avg_volume": 1_000_000},
        {"symbol": "B", "industry": "电子", "score": 90, "price": 20, "avg_volume": 1_000_000},
        {"symbol": "C", "industry": "银行", "score": 80, "price": 5, "avg_volume": 1_000_000},
    ])
    result = construct_portfolio(
        candidates,
        portfolio_value=100_000,
        max_positions=3,
        max_stock_weight=0.40,
        max_industry_weight=0.60,
        cash_buffer=0.10,
        max_participation_rate=0.05,
    )
    assert all(position.shares % 100 == 0 for position in result.positions)
    assert sum(position.market_value for position in result.positions) <= 90_000
    assert result.cash >= 10_000
    electronic = sum(p.market_value for p in result.positions if p.industry == "电子")
    assert electronic <= 60_000


def test_small_account_returns_all_cash():
    candidates = pd.DataFrame([
        {"symbol": "A", "industry": "电子", "score": 100,
         "price": 20, "avg_volume": 1_000_000},
    ])
    result = construct_portfolio(
        candidates,
        portfolio_value=1_000,
        max_positions=1,
        max_stock_weight=0.10,
        max_industry_weight=0.25,
        cash_buffer=0.10,
        max_participation_rate=0.05,
    )
    assert result.positions == []
    assert result.cash == 1_000
