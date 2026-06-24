"""Paper execution engine tests (Stage 6A Task 4)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from tradingagents.market_data.contracts import MarketOpenSnapshot, QuoteStatus
from tradingagents.paper.contracts import OrderSide, OrderStatus, PaperOrder
from tradingagents.paper.exceptions import IdempotencyConflict, OrderNotFound
from tradingagents.paper.execution import (
    ExecutionAccountState,
    PaperExecutionEngine,
    ensure_open_snapshots_frozen,
    load_open_snapshots_from_inputs,
)
from tradingagents.paper.fees import FeeConfig, calculate_fees
from tradingagents.paper.repository import ExecutionBatch, OrderRejectionSpec
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


def test_execute_rebalance_freezes_open_snapshots(repo):
    seed_execution_orders(repo)
    repo.expire_lease_for_test("demo")
    engine = PaperExecutionEngine()
    lease = repo.acquire_account_lease("demo", owner_id="executor")
    engine.execute_rebalance(
        repo,
        rebalance_run_id="reb-1",
        execution_date=TRADE_DATE,
        execution_time=EXECUTION_TIME,
        snapshots={"600000": snapshot()},
        fencing_token=lease.token,
        owner_id=lease.owner_id,
    )
    row = repo.connection.execute(
        """
        SELECT COUNT(*)
        FROM paper_run_inputs
        WHERE run_id = ? AND input_type = 'OPEN_SNAPSHOT'
        """,
        ["reb-1"],
    ).fetchone()
    assert int(row[0]) == 1


def test_execute_rebalance_replay_uses_frozen_snapshot_not_incoming(repo):
    seed_execution_orders(repo)
    repo.expire_lease_for_test("demo")
    engine = PaperExecutionEngine()
    lease = repo.acquire_account_lease("demo", owner_id="executor")
    engine.execute_rebalance(
        repo,
        rebalance_run_id="reb-1",
        execution_date=TRADE_DATE,
        execution_time=EXECUTION_TIME,
        snapshots={"600000": snapshot(open_cny=10.0)},
        fencing_token=lease.token,
        owner_id=lease.owner_id,
    )
    fill_row = repo.connection.execute(
        "SELECT price_cny FROM paper_fills WHERE account_id = ?",
        ["demo"],
    ).fetchone()
    assert Decimal(str(fill_row[0])) == Decimal("10.000000")
    tampered = snapshot(open_cny=99.0)
    with pytest.raises(IdempotencyConflict):
        ensure_open_snapshots_frozen(
            repo,
            "reb-1",
            {"600000": tampered},
        )
    frozen = load_open_snapshots_from_inputs(repo, "reb-1")
    assert frozen["600000"].open_cny == 10.0


def test_rejection_replay_conflicts_on_different_reason(repo):
    seed_execution_orders(repo)
    repo.expire_lease_for_test("demo")
    batch = ExecutionBatch(
        account_id="demo",
        rebalance_run_id="reb-1",
        execution_date=TRADE_DATE,
        execution_time=EXECUTION_TIME,
        owner_id="executor",
        fills=[],
        rejections=[
            OrderRejectionSpec(
                order_id="ord-buy-600000",
                rejection_code="MISSING_SNAPSHOT",
                rejection_detail="first",
            )
        ],
    )
    lease = repo.acquire_account_lease("demo", owner_id="executor")
    repo.apply_execution_batch(batch, fencing_token=lease.token)
    lease2 = repo.acquire_account_lease("demo", owner_id="executor")
    replay = ExecutionBatch(
        account_id="demo",
        rebalance_run_id="reb-1",
        execution_date=TRADE_DATE,
        execution_time=EXECUTION_TIME,
        owner_id="executor",
        fills=[],
        rejections=[
            OrderRejectionSpec(
                order_id="ord-buy-600000",
                rejection_code="QUOTE_STATUS",
                rejection_detail="second",
            )
        ],
    )
    with pytest.raises(IdempotencyConflict):
        repo.apply_execution_batch(replay, fencing_token=lease2.token)


def test_rejection_missing_order_raises(repo):
    seed_execution_orders(repo)
    repo.expire_lease_for_test("demo")
    batch = ExecutionBatch(
        account_id="demo",
        rebalance_run_id="reb-1",
        execution_date=TRADE_DATE,
        execution_time=EXECUTION_TIME,
        owner_id="executor",
        fills=[],
        rejections=[
            OrderRejectionSpec(
                order_id="missing-order",
                rejection_code="MISSING_SNAPSHOT",
                rejection_detail="first",
            )
        ],
    )
    lease = repo.acquire_account_lease("demo", owner_id="executor")
    with pytest.raises(OrderNotFound):
        repo.apply_execution_batch(batch, fencing_token=lease.token)
