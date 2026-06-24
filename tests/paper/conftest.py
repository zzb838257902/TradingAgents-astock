"""Shared fixtures for paper repository tests."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from tradingagents.paper.config import PaperPaths
from tradingagents.paper.contracts import (
    CashEntry,
    CashEntryType,
    FrozenScreenRun,
    OrderSide,
    OrderStatus,
    PaperOrder,
    PositionEntry,
    PositionSourceType,
    RunStatus,
    TargetPortfolioMode,
)
from tradingagents.paper.repository import (
    ExecutionBatch,
    FillSpec,
    PaperRepository,
    RebalanceRevisionSpec,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")
SIGNAL_TIME = datetime(2026, 6, 22, 16, 0, tzinfo=SHANGHAI)
TRADE_DATE = date(2026, 6, 23)
EXECUTION_TIME = datetime(2026, 6, 23, 9, 35, tzinfo=SHANGHAI)


@pytest.fixture
def repo(tmp_path) -> PaperRepository:
    repository = PaperRepository(PaperPaths(home_dir=tmp_path))
    yield repository
    repository.close()


def make_execution_batch(
    *,
    account_id: str = "demo",
    owner_id: str = "executor",
    order_id: str = "ord-buy-600000",
    fill_id: str = "fill-1",
    rebalance_run_id: str = "reb-1",
) -> ExecutionBatch:
    return ExecutionBatch(
        account_id=account_id,
        rebalance_run_id=rebalance_run_id,
        execution_date=TRADE_DATE,
        execution_time=EXECUTION_TIME,
        owner_id=owner_id,
        fills=[
            FillSpec(
                fill_id=fill_id,
                order_id=order_id,
                account_id=account_id,
                symbol="600000",
                quantity=1000,
                price_cny=Decimal("10.00"),
                commission_cny=Decimal("5.00"),
            )
        ],
    )


EXECUTION_BATCH = make_execution_batch()


def seed_demo_account(repo: PaperRepository, *, account_id: str = "demo") -> None:
    repo.create_account(account_id, Decimal("1000000.00"))


def seed_execution_orders(
    repo: PaperRepository,
    *,
    account_id: str = "demo",
    owner_id: str = "executor",
    rebalance_run_id: str = "reb-1",
    order_id: str = "ord-buy-600000",
) -> None:
    seed_demo_account(repo, account_id=account_id)
    repo.freeze_screen_run(
        FrozenScreenRun(
            screen_run_id="screen-1",
            screen_content_hash="hash-screen-1",
            status="OK",
            signal_time=SIGNAL_TIME,
            target_portfolio_mode=TargetPortfolioMode.WEIGHTS,
            target_weights_json='{"600000": 0.1}',
            cash_weight=Decimal("0.9"),
            run_report_json="{}",
        )
    )
    repo.create_rebalance_revision(
        RebalanceRevisionSpec(
            rebalance_run_id=rebalance_run_id,
            account_id=account_id,
            screen_run_id="screen-1",
            screen_content_hash="hash-screen-1",
            target_hash="hash-target-1",
            signal_date=SIGNAL_TIME.date(),
            signal_time=SIGNAL_TIME,
            execution_date=TRADE_DATE,
            universe_hash="uni-1",
            config_hash="cfg-1",
            strategy_version="v1",
            target_weights_json='{"600000": 0.1}',
            logical_run_key=f"{account_id}:{TRADE_DATE}:uni-1",
            revision=1,
            status=RunStatus.PENDING,
        )
    )
    repo.insert_orders(
        [
            PaperOrder(
                order_id=order_id,
                rebalance_run_id=rebalance_run_id,
                account_id=account_id,
                symbol="600000",
                side=OrderSide.BUY,
                planned_quantity=1000,
                remaining_quantity=1000,
                reference_price_cny=Decimal("10.00"),
                status=OrderStatus.PENDING,
            )
        ]
    )


def cash_entry(
    *,
    account_id: str = "demo",
    amount_cny: Decimal = Decimal("1000000.00"),
    component: str = "INITIAL_CASH",
    source_id: str = "demo",
    entry_id: str = "cash-test-1",
) -> CashEntry:
    return CashEntry(
        cash_entry_id=entry_id,
        account_id=account_id,
        entry_type=CashEntryType.DEPOSIT,
        amount_cny=amount_cny,
        source_type="ACCOUNT",
        source_id=source_id,
        component=component,
        occurred_at=SIGNAL_TIME,
    )


def position_entry(
    *,
    account_id: str = "demo",
    symbol: str = "600000",
    quantity_delta: int = 1000,
    cost_delta_cny: Decimal = Decimal("10000.00"),
    entry_id: str = "pos-test-1",
) -> PositionEntry:
    return PositionEntry(
        position_entry_id=entry_id,
        account_id=account_id,
        symbol=symbol,
        quantity_delta=quantity_delta,
        cost_delta_cny=cost_delta_cny,
        effective_date=TRADE_DATE,
        source_type=PositionSourceType.ADJUSTMENT,
        source_id="seed",
        component="QUANTITY",
        business_key=f"{account_id}:ADJUSTMENT:seed:QUANTITY",
    )
