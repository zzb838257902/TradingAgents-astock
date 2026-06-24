"""Parity tests between backtest and paper execution rules."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from tradingagents.backtest.execution import ExecutionModel
from tradingagents.backtest.models import Bar, Order, Side
from tradingagents.market_data.contracts import MarketOpenSnapshot, QuoteStatus
from tradingagents.paper.contracts import OrderSide, OrderStatus, PaperOrder, money
from tradingagents.paper.execution import ExecutionAccountState, PaperExecutionEngine
from tests.paper.conftest import TRADE_DATE

SHANGHAI = ZoneInfo("Asia/Shanghai")


def test_participation_cap_matches_backtest():
    model = ExecutionModel(
        commission_rate=0.0003,
        stamp_tax_rate=0.0005,
        max_participation_rate=0.05,
    )
    bar = Bar(
        open=10,
        high=10.5,
        low=9.8,
        close=10.2,
        volume=10_000,
        limit_up=11,
        limit_down=9,
    )
    backtest_fill = model.fill(Order("600000", Side.BUY, 1000), bar, sellable_shares=0)

    observed_at = datetime(2026, 6, 23, 9, 35, tzinfo=SHANGHAI)
    open_snapshot = MarketOpenSnapshot(
        symbol="600000",
        trade_date=TRADE_DATE,
        observed_at=observed_at,
        open_cny=10.0,
        prev_close_cny=10.0,
        last_cny=10.0,
        cumulative_volume_shares=10_000,
        quote_status=QuoteStatus.TRADING,
        upper_limit_cny=11.0,
        lower_limit_cny=9.0,
        source="fixture",
        available_at=observed_at,
    )
    engine = PaperExecutionEngine(max_participation_rate=0.05)
    paper_result = engine.execute(
        PaperOrder(
            order_id="ord-buy-600000",
            rebalance_run_id="reb-1",
            account_id="demo",
            symbol="600000",
            side=OrderSide.BUY,
            planned_quantity=1000,
            remaining_quantity=1000,
            reference_price_cny=Decimal("10.00"),
            status=OrderStatus.PENDING,
        ),
        open_snapshot,
        ExecutionAccountState(cash_cny=Decimal("100000.00")),
    )

    assert backtest_fill is not None
    assert paper_result.fill is not None
    assert backtest_fill.shares == paper_result.fill.quantity
    assert paper_result.fill.price_cny == money(backtest_fill.price)


def test_limit_up_rejection_matches_backtest():
    model = ExecutionModel(commission_rate=0.0003, stamp_tax_rate=0.0005)
    bar = Bar(open=11, high=11, low=11, close=11, volume=100_000, limit_up=11, limit_down=9)
    assert model.fill(Order("600000", Side.BUY, 1000), bar, sellable_shares=0) is None

    observed_at = datetime(2026, 6, 23, 9, 35, tzinfo=SHANGHAI)
    snapshot = MarketOpenSnapshot(
        symbol="600000",
        trade_date=TRADE_DATE,
        observed_at=observed_at,
        open_cny=11.0,
        prev_close_cny=10.0,
        last_cny=11.0,
        cumulative_volume_shares=100_000,
        quote_status=QuoteStatus.TRADING,
        upper_limit_cny=11.0,
        lower_limit_cny=9.0,
        source="fixture",
        available_at=observed_at,
    )
    engine = PaperExecutionEngine()
    result = engine.execute(
        PaperOrder(
            order_id="ord-buy-600000",
            rebalance_run_id="reb-1",
            account_id="demo",
            symbol="600000",
            side=OrderSide.BUY,
            planned_quantity=1000,
            remaining_quantity=1000,
            reference_price_cny=Decimal("11.00"),
            status=OrderStatus.PENDING,
        ),
        snapshot,
        ExecutionAccountState(cash_cny=Decimal("100000.00")),
    )
    assert result.order_status == OrderStatus.REJECTED
    assert result.rejection_code == "LIMIT_UP"
