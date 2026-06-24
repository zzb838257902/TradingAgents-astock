"""Paper portfolio Pydantic contracts (Stage 6A)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

MONEY_QUANTUM = Decimal("0.01")
PRICE_QUANTUM = Decimal("0.000001")


def money(value: Decimal | str | int) -> Decimal:
    return Decimal(value).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


class TargetPortfolioMode(StrEnum):
    WEIGHTS = "weights"
    ALL_CASH = "all_cash"


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    BLOCKED = "blocked"
    DATA_ERROR = "data_error"
    FAILED = "failed"
    COMPLETED = "completed"
    COMPLETED_WITH_REJECTIONS = "completed_with_rejections"


class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    BLOCKED = "blocked"
    DATA_ERROR = "data_error"
    FAILED = "failed"


class AccountStatus(StrEnum):
    ACTIVE = "ACTIVE"
    FROZEN = "FROZEN"
    CLOSED = "CLOSED"


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(StrEnum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    PARTIALLY_FILLED_EXPIRED = "PARTIALLY_FILLED_EXPIRED"
    CANCELLED = "CANCELLED"


class CashEntryType(StrEnum):
    DEPOSIT = "DEPOSIT"
    BUY = "BUY"
    SELL = "SELL"
    COMMISSION = "COMMISSION"
    STAMP_TAX = "STAMP_TAX"
    DIVIDEND = "DIVIDEND"
    CORPORATE_ACTION = "CORPORATE_ACTION"
    ADJUSTMENT = "ADJUSTMENT"


class PositionSourceType(StrEnum):
    FILL = "FILL"
    CORPORATE_ACTION = "CORPORATE_ACTION"
    DELISTING = "DELISTING"
    ADJUSTMENT = "ADJUSTMENT"


class CorporateActionApplicationStatus(StrEnum):
    PENDING = "PENDING"
    APPLIED = "APPLIED"
    NEEDS_MANUAL_ACTION = "NEEDS_MANUAL_ACTION"
    ADJUSTED = "ADJUSTED"


class PaperAccount(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: str
    name: str
    base_currency: str = "CNY"
    initial_cash_cny: Decimal
    status: AccountStatus = AccountStatus.ACTIVE
    created_at: datetime | None = None
    updated_at: datetime | None = None


class FrozenScreenRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    screen_run_id: str
    screen_content_hash: str
    status: str
    signal_time: datetime
    target_portfolio_mode: TargetPortfolioMode
    target_weights_json: str
    cash_weight: Decimal
    dataset_versions_json: str = "{}"
    event_dataset_versions_json: str = "{}"
    run_report_json: str
    created_at: datetime | None = None


class PaperOrder(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_id: str
    rebalance_run_id: str
    account_id: str
    symbol: str
    side: OrderSide
    planned_quantity: int
    filled_quantity: int = 0
    remaining_quantity: int
    reference_price_cny: Decimal
    limit_price_cny: Decimal | None = None
    status: OrderStatus = OrderStatus.PENDING
    rejection_code: str | None = None
    rejection_detail: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class PaperFill(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fill_id: str
    fill_sequence: int = 1
    order_id: str
    account_id: str
    symbol: str
    execution_date: date
    execution_time: datetime
    quantity: int
    price_cny: Decimal
    commission_cny: Decimal = Field(default_factory=lambda: money(0))
    stamp_tax_cny: Decimal = Field(default_factory=lambda: money(0))
    other_fee_cny: Decimal = Field(default_factory=lambda: money(0))
    source_snapshot_key: str | None = None
    source_snapshot_version_id: str | None = None


class CashEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cash_entry_id: str
    account_id: str
    entry_type: CashEntryType
    amount_cny: Decimal
    source_type: str
    source_id: str
    component: str
    occurred_at: datetime
    balance_after_cny: Decimal | None = None
    created_at: datetime | None = None


class PositionEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    position_entry_id: str
    account_id: str
    symbol: str
    quantity_delta: int
    cost_delta_cny: Decimal
    effective_date: date
    source_type: PositionSourceType
    source_id: str
    component: str
    business_key: str
    created_at: datetime | None = None


class PaperLot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lot_id: str
    account_id: str
    symbol: str
    acquired_date: date
    source_type: str
    source_id: str
    original_quantity: int
    remaining_quantity: int
    original_cost_cny: Decimal
    remaining_cost_cny: Decimal
    created_at: datetime | None = None
    closed_at: datetime | None = None


class NavSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: str
    valuation_date: date
    cash_cny: Decimal
    positions_value_cny: Decimal
    total_equity_cny: Decimal
    daily_return: Decimal | None = None
    cumulative_return: Decimal | None = None
    drawdown: Decimal | None = None
    valuation_manifest_hash: str | None = None
    created_at: datetime | None = None


class RunStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    step_name: str
    status: StepStatus = StepStatus.PENDING
    input_hash: str | None = None
    output_json: str | None = None
    error_json: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
