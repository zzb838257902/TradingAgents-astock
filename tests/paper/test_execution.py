"""Paper execution engine tests (Stage 6A Task 4)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from tradingagents.market_data.contracts import MarketOpenSnapshot, QuoteStatus
from tradingagents.paper.contracts import OrderSide, OrderStatus, PaperOrder
from tradingagents.paper.execution import ExecutionAccountState, PaperExecutionEngine
from tradingagents.paper.fees import FeeConfig, calculate_fees
from tests.paper.conftest import (
    EXECUTION_TIME,
    TRADE_DATE,
    seed_execution_orders,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")


def snapshot(
    *,
    open_cny: float = 10.0,
    prev_close_cny: float = 10.0,
    quote_status: QuoteStatus = QuoteStatus.TRADING,
    cumulative_volume_shares: int = 1_000_000,
    symbol: str = "600000",
    upper_limit_cny: float | None = None,
    lower_limit_cny: float | None = None,
) -> MarketOpenSnapshot:
    upper = upper_limit_cny if upper_limit_cny is not None else round(prev_close_cny * 1.1, 2)
    lower = lower_limit_cny if lower_limit_cny is not None else round(prev_close_cny * 0.9, 2)
    observed_at = datetime(2026, 6, 23, 9, 35, tzinfo=SHANGHAI)
    return MarketOpenSnapshot(
        symbol=symbol,
        trade_date=TRADE_DATE,
        observed_at=observed_at,
        open_cny=open_cny,
        prev_close_cny=prev_close_cny,
        last_cny=open_cny,
        cumulative_volume_shares=cumulative_volume_shares,
        quote_status=quote_status,
        upper_limit_cny=upper,
        lower_limit_cny=lower,
        source="fixture",
        available_at=observed_at,
    )


def buy_order(quantity: int, *, symbol: str = "600000") -> PaperOrder:
    return PaperOrder(
        order_id=f"ord-buy-{symbol}",
        rebalance_run_id="reb-1",
        account_id="demo",
        symbol=symbol,
        side=OrderSide.BUY,
        planned_quantity=quantity,
        remaining_quantity=quantity,
        reference_price_cny=Decimal("10.00"),
        status=OrderStatus.PENDING,
    )


@pytest.mark.parametrize("status", ["suspended", "halted", "unknown"])
def test_non_trading_snapshot_rejects_order(status):
    engine = PaperExecutionEngine()
    result = engine.execute(
        buy_order(1000),
        snapshot(quote_status=QuoteStatus(status)),
        ExecutionAccountState(cash_cny=Decimal("100000.00")),
    )
    assert result.order_status == OrderStatus.REJECTED
    assert result.rejection_code == "QUOTE_STATUS"


def test_buy_is_resized_after_gap_up_and_never_makes_cash_negative():
    engine = PaperExecutionEngine()
    result = engine.execute(
        buy_order(10_000),
        snapshot(open_cny=25.0, prev_close_cny=10.0, upper_limit_cny=27.5),
        ExecutionAccountState(cash_cny=Decimal("100000.00")),
    )
    assert result.fill is not None
    assert result.fill.quantity % 100 == 0
    assert result.cash_after >= Decimal("0.00")


def test_calculate_fees_uses_minimum_commission():
    fees = calculate_fees(Decimal("1000.00"), OrderSide.BUY, FeeConfig())
    assert fees.commission == Decimal("5.00")
    assert fees.stamp_tax == Decimal("0.00")


def test_calculate_fees_applies_stamp_tax_on_sell():
    fees = calculate_fees(Decimal("10000.00"), OrderSide.SELL, FeeConfig())
    assert fees.commission == Decimal("5.00")
    assert fees.stamp_tax == Decimal("5.00")


def test_execute_rebalance_is_idempotent(repo):
    seed_execution_orders(repo)
    engine = PaperExecutionEngine()
    open_snap = snapshot()
    batch = engine.build_execution_batch(
        repo,
        rebalance_run_id="reb-1",
        execution_date=TRADE_DATE,
        execution_time=EXECUTION_TIME,
        snapshots={"600000": open_snap},
        owner_id="executor",
    )
    lease = repo.acquire_account_lease("demo", owner_id="executor")
    first = repo.apply_execution_batch(batch, fencing_token=lease.token)
    fill_count = repo.connection.execute(
        "SELECT COUNT(*) FROM paper_fills WHERE account_id = ?",
        ["demo"],
    ).fetchone()
    lease2 = repo.acquire_account_lease("demo", owner_id="executor")
    second = repo.apply_execution_batch(batch, fencing_token=lease2.token)
    fill_count_after = repo.connection.execute(
        "SELECT COUNT(*) FROM paper_fills WHERE account_id = ?",
        ["demo"],
    ).fetchone()
    assert len(first) == 1
    assert second == first
    assert int(fill_count_after[0]) == int(fill_count[0])
