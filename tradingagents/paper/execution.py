"""T+1 opening-snapshot execution engine for Stage 6A paper operations."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from tradingagents.backtest.execution_rules import (
    cap_requested_quantity,
    conservative_buy_limit_reject,
    conservative_sell_limit_reject,
    resize_buy_for_cash,
)
from tradingagents.market_data.contracts import MarketOpenSnapshot, QuoteStatus
from tradingagents.paper.contracts import OrderSide, OrderStatus, PaperOrder, money
from tradingagents.paper.exceptions import InvalidExecutionBatch
from tradingagents.paper.fees import FeeConfig, calculate_fees
from tradingagents.paper.repository import (
    ExecutionBatch,
    FillSpec,
    OrderRejectionSpec,
    PaperRepository,
    RunInputCapture,
)


@dataclass
class ExecutionAccountState:
    cash_cny: Decimal
    sellable_by_symbol: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class OrderExecutionResult:
    order_status: OrderStatus
    cash_after: Decimal
    fill: FillSpec | None = None
    rejection_code: str | None = None
    rejection_detail: str | None = None


@dataclass(frozen=True)
class RebalanceExecutionResult:
    rebalance_run_id: str
    fill_ids: list[str]


def snapshot_scope_key(snapshot: MarketOpenSnapshot) -> str:
    return f"{snapshot.symbol}:{snapshot.trade_date.isoformat()}"


def open_snapshot_capture(snapshot: MarketOpenSnapshot, *, run_id: str) -> RunInputCapture:
    row_json = snapshot.model_dump_json()
    return RunInputCapture(
        run_id=run_id,
        input_type="OPEN_SNAPSHOT",
        scope_key=snapshot_scope_key(snapshot),
        row_content_hash=hashlib.sha256(row_json.encode()).hexdigest(),
        row_json=row_json,
        source_dataset_version_id=snapshot.dataset_version_id,
        source_available_at=snapshot.available_at,
    )


def load_open_snapshots_from_inputs(
    paper_repo: PaperRepository,
    run_id: str,
) -> dict[str, MarketOpenSnapshot]:
    rows = paper_repo.connection.execute(
        """
        SELECT row_json
        FROM paper_run_inputs
        WHERE run_id = ? AND input_type = 'OPEN_SNAPSHOT'
        """,
        [run_id],
    ).fetchall()
    snapshots: dict[str, MarketOpenSnapshot] = {}
    for (row_json,) in rows:
        snapshot = MarketOpenSnapshot.model_validate(json.loads(row_json))
        snapshots[snapshot.symbol] = snapshot
    return snapshots


class PaperExecutionEngine:
    def __init__(
        self,
        *,
        fee_config: FeeConfig | None = None,
        max_participation_rate: float = 0.05,
    ) -> None:
        self.fee_config = fee_config or FeeConfig()
        self.max_participation_rate = max_participation_rate

    def execute(
        self,
        order: PaperOrder,
        snapshot: MarketOpenSnapshot,
        account: ExecutionAccountState,
    ) -> OrderExecutionResult:
        if snapshot.quote_status != QuoteStatus.TRADING:
            return OrderExecutionResult(
                order_status=OrderStatus.REJECTED,
                cash_after=account.cash_cny,
                rejection_code="QUOTE_STATUS",
                rejection_detail=snapshot.quote_status.value,
            )

        if order.side == OrderSide.BUY and conservative_buy_limit_reject(
            snapshot.open_cny,
            snapshot.upper_limit_cny,
        ):
            return OrderExecutionResult(
                order_status=OrderStatus.REJECTED,
                cash_after=account.cash_cny,
                rejection_code="LIMIT_UP",
                rejection_detail="open at upper limit",
            )

        if order.side == OrderSide.SELL and conservative_sell_limit_reject(
            snapshot.open_cny,
            snapshot.lower_limit_cny,
        ):
            return OrderExecutionResult(
                order_status=OrderStatus.REJECTED,
                cash_after=account.cash_cny,
                rejection_code="LIMIT_DOWN",
                rejection_detail="open at lower limit",
            )

        sellable = (
            account.sellable_by_symbol.get(order.symbol, 0)
            if order.side == OrderSide.SELL
            else 0
        )
        quantity = cap_requested_quantity(
            requested_quantity=order.remaining_quantity,
            sellable_shares=sellable,
            is_sell=order.side == OrderSide.SELL,
            cumulative_volume_shares=snapshot.cumulative_volume_shares,
            max_participation_rate=self.max_participation_rate,
        )

        price = money(snapshot.open_cny)
        if order.side == OrderSide.BUY:
            quantity = resize_buy_for_cash(
                quantity,
                price=price,
                available_cash=account.cash_cny,
                commission_rate=self.fee_config.commission_rate,
                minimum_commission=self.fee_config.minimum_commission_cny,
            )
            if quantity <= 0:
                return OrderExecutionResult(
                    order_status=OrderStatus.REJECTED,
                    cash_after=account.cash_cny,
                    rejection_code="INSUFFICIENT_CASH",
                    rejection_detail="buy resized to zero",
                )
        elif quantity <= 0:
            return OrderExecutionResult(
                order_status=OrderStatus.REJECTED,
                cash_after=account.cash_cny,
                rejection_code="LIQUIDITY",
                rejection_detail="no sellable or participation capacity",
            )

        notional = money(price * quantity)
        fees = calculate_fees(notional, order.side, self.fee_config)
        cash_after = account.cash_cny
        if order.side == OrderSide.BUY:
            cash_after = money(account.cash_cny - notional - fees.commission)
        else:
            cash_after = money(
                account.cash_cny + notional - fees.commission - fees.stamp_tax
            )

        fill = FillSpec(
            fill_id=f"fill-{order.order_id}",
            order_id=order.order_id,
            account_id=order.account_id,
            symbol=order.symbol,
            quantity=quantity,
            price_cny=price,
            commission_cny=fees.commission,
            stamp_tax_cny=fees.stamp_tax,
            source_snapshot_key=snapshot_scope_key(snapshot),
            source_snapshot_version_id=snapshot.dataset_version_id,
        )
        order_status = (
            OrderStatus.FILLED
            if quantity >= order.remaining_quantity
            else OrderStatus.PARTIALLY_FILLED
        )
        return OrderExecutionResult(
            order_status=order_status,
            cash_after=cash_after,
            fill=fill,
        )

    def build_execution_batch(
        self,
        paper_repo: PaperRepository,
        *,
        rebalance_run_id: str,
        execution_date: date,
        execution_time: datetime,
        snapshots: dict[str, MarketOpenSnapshot],
        owner_id: str,
    ) -> ExecutionBatch:
        orders = paper_repo.list_pending_orders_for_rebalance(rebalance_run_id)
        if not orders:
            raise InvalidExecutionBatch(f"no pending orders for {rebalance_run_id}")

        account_id = orders[0].account_id
        snapshot = paper_repo.load_account_snapshot(account_id, as_of_date=execution_date)
        account = ExecutionAccountState(
            cash_cny=snapshot.cash_cny,
            sellable_by_symbol={
                symbol: projection.available_quantity
                for symbol, projection in snapshot.positions.items()
            },
        )

        fills: list[FillSpec] = []
        rejections: list[OrderRejectionSpec] = []
        for order in orders:
            open_snapshot = snapshots.get(order.symbol)
            if open_snapshot is None:
                rejections.append(
                    OrderRejectionSpec(
                        order_id=order.order_id,
                        rejection_code="MISSING_SNAPSHOT",
                        rejection_detail=f"no open snapshot for {order.symbol}",
                    )
                )
                continue

            result = self.execute(order, open_snapshot, account)
            if result.fill is not None:
                fills.append(result.fill)
                account.cash_cny = result.cash_after
                if order.side == OrderSide.SELL:
                    account.sellable_by_symbol[order.symbol] = max(
                        account.sellable_by_symbol.get(order.symbol, 0) - result.fill.quantity,
                        0,
                    )
            elif result.order_status == OrderStatus.REJECTED:
                rejections.append(
                    OrderRejectionSpec(
                        order_id=order.order_id,
                        rejection_code=result.rejection_code or "REJECTED",
                        rejection_detail=result.rejection_detail,
                    )
                )

        return ExecutionBatch(
            account_id=account_id,
            rebalance_run_id=rebalance_run_id,
            execution_date=execution_date,
            execution_time=execution_time,
            fills=fills,
            owner_id=owner_id,
            rejections=rejections,
        )

    def execute_rebalance(
        self,
        paper_repo: PaperRepository,
        *,
        rebalance_run_id: str,
        execution_date: date,
        execution_time: datetime,
        snapshots: dict[str, MarketOpenSnapshot],
        fencing_token: int,
        owner_id: str,
    ) -> RebalanceExecutionResult:
        batch = self.build_execution_batch(
            paper_repo,
            rebalance_run_id=rebalance_run_id,
            execution_date=execution_date,
            execution_time=execution_time,
            snapshots=snapshots,
            owner_id=owner_id,
        )
        fill_ids = paper_repo.apply_execution_batch(
            batch,
            fencing_token=fencing_token,
        )
        return RebalanceExecutionResult(
            rebalance_run_id=rebalance_run_id,
            fill_ids=fill_ids,
        )
