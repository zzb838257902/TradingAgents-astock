"""Transactional DuckDB repository for paper portfolio ledger."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Callable

import duckdb

from tradingagents.paper.config import PaperPaths
from tradingagents.paper.contracts import (
    CashEntry,
    CashEntryType,
    CorporateActionApplicationStatus,
    FrozenScreenRun,
    NavSnapshot,
    OrderSide,
    OrderStatus,
    PaperAccount,
    PaperFill,
    PaperOrder,
    PositionEntry,
    PositionSourceType,
    RunStatus,
    RunStep,
    StepStatus,
    money,
)
from tradingagents.paper.exceptions import (
    AccountNotFound,
    IdempotencyConflict,
    InvalidExecutionBatch,
    OrderNotFound,
    PaperError,
    StaleFencingToken,
)
from tradingagents.paper.invariants import assert_account_invariants
from tradingagents.paper.locking import (
    AccountLease,
    acquire_account_lease,
    account_transaction_lock,
    assert_fencing_commit_guard,
    take_over_expired_lease,
    validate_fencing,
)
from tradingagents.paper.migrations import SHANGHAI, apply_paper_migrations

FaultHook = Callable[[], None]


@dataclass(frozen=True)
class FillSpec:
    fill_id: str
    order_id: str
    account_id: str
    symbol: str
    quantity: int
    price_cny: Decimal
    commission_cny: Decimal = field(default_factory=lambda: money(0))
    stamp_tax_cny: Decimal = field(default_factory=lambda: money(0))
    other_fee_cny: Decimal = field(default_factory=lambda: money(0))
    fill_sequence: int = 1
    source_snapshot_key: str | None = None
    source_snapshot_version_id: str | None = None


@dataclass(frozen=True)
class OrderRejectionSpec:
    order_id: str
    rejection_code: str
    rejection_detail: str | None = None


@dataclass(frozen=True)
class ExecutionBatch:
    account_id: str
    rebalance_run_id: str
    execution_date: date
    execution_time: datetime
    fills: list[FillSpec]
    owner_id: str
    rejections: list[OrderRejectionSpec] = field(default_factory=list)


@dataclass(frozen=True)
class PositionProjection:
    symbol: str
    quantity: int
    available_quantity: int
    average_cost_cny: Decimal
    market_value_cny: Decimal = field(default_factory=lambda: money(0))
    last_price_cny: Decimal | None = None


@dataclass(frozen=True)
class AccountProjection:
    account_id: str
    cash_cny: Decimal
    positions: dict[str, PositionProjection]


@dataclass(frozen=True)
class AccountSnapshot:
    account: PaperAccount
    cash_cny: Decimal
    positions: dict[str, PositionProjection]


@dataclass(frozen=True)
class RunInputCapture:
    run_id: str
    input_type: str
    scope_key: str
    row_content_hash: str
    row_json: str
    source_dataset_version_id: str | None = None
    source_available_at: datetime | None = None


@dataclass(frozen=True)
class RunStepWriteSpec:
    run_id: str
    step_name: str
    status: StepStatus
    input_hash: str | None = None
    output_json: str | None = None
    error_json: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


@dataclass(frozen=True)
class RebalanceRevisionSpec:
    rebalance_run_id: str
    account_id: str
    screen_run_id: str
    screen_content_hash: str
    target_hash: str
    signal_date: date
    signal_time: datetime
    execution_date: date
    universe_hash: str
    config_hash: str
    strategy_version: str
    target_weights_json: str
    logical_run_key: str
    revision: int = 1
    status: RunStatus = RunStatus.PENDING


@dataclass(frozen=True)
class CorporateActionApplicationSpec:
    account_id: str
    corporate_action_id: str
    revision: int
    entitlement_quantity: int
    entitlement_source_hash: str
    status: CorporateActionApplicationStatus = CorporateActionApplicationStatus.PENDING


@dataclass(frozen=True)
class ValuationWriteSpec:
    account_id: str
    valuation_date: date
    cash_cny: Decimal
    positions_value_cny: Decimal
    total_equity_cny: Decimal
    sources: list[dict[str, Any]] = field(default_factory=list)
    daily_return: Decimal | None = None
    cumulative_return: Decimal | None = None
    drawdown: Decimal | None = None
    valuation_manifest_hash: str | None = None


@dataclass(frozen=True)
class NavHistoryContext:
    latest: NavSnapshot | None
    peak_equity_cny: Decimal
    initial_equity_cny: Decimal


def _decimal(value: object) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _business_key(account_id: str, source_type: str, source_id: str, component: str) -> str:
    return f"{account_id}:{source_type}:{source_id}:{component}"


def _hash_payload(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _cash_payload_hash(entry: CashEntry) -> str:
    occurred_at = entry.occurred_at
    occurred_iso = (
        occurred_at.isoformat()
        if isinstance(occurred_at, datetime)
        else str(occurred_at)
    )
    return _hash_payload(
        {
            "entry_type": entry.entry_type.value,
            "amount_cny": str(money(entry.amount_cny)),
            "occurred_at": occurred_iso,
        }
    )


def _cash_entry_payload_from_row(
    row: tuple[object, ...],
    *,
    account_id: str,
    source_type: str,
    source_id: str,
    component: str,
) -> CashEntry:
    return CashEntry(
        cash_entry_id=str(row[0]),
        account_id=account_id,
        entry_type=CashEntryType(str(row[1])),
        amount_cny=_decimal(row[2]),
        source_type=source_type,
        source_id=source_id,
        component=component,
        occurred_at=row[3],  # type: ignore[arg-type]
    )


def _position_payload_hash(entry: PositionEntry) -> str:
    return _hash_payload(
        {
            "symbol": entry.symbol,
            "quantity_delta": entry.quantity_delta,
            "cost_delta_cny": str(money(entry.cost_delta_cny)),
            "effective_date": entry.effective_date.isoformat(),
        }
    )


def _screen_run_payload_hash(screen_run: FrozenScreenRun) -> str:
    return _hash_payload(
        {
            "screen_content_hash": screen_run.screen_content_hash,
            "status": screen_run.status,
            "signal_time": screen_run.signal_time.isoformat(),
            "target_portfolio_mode": screen_run.target_portfolio_mode.value,
            "target_weights_json": screen_run.target_weights_json,
            "cash_weight": str(screen_run.cash_weight),
            "dataset_versions_json": screen_run.dataset_versions_json,
            "event_dataset_versions_json": screen_run.event_dataset_versions_json,
            "run_report_json": screen_run.run_report_json,
        }
    )


def _run_input_payload_hash(capture: RunInputCapture) -> str:
    return _hash_payload(
        {
            "row_content_hash": capture.row_content_hash,
            "row_json": capture.row_json,
            "source_dataset_version_id": capture.source_dataset_version_id,
            "source_available_at": (
                capture.source_available_at.isoformat()
                if capture.source_available_at is not None
                else None
            ),
        }
    )


def _rebalance_spec_payload_hash(spec: RebalanceRevisionSpec) -> str:
    return _hash_payload(
        {
            "account_id": spec.account_id,
            "screen_run_id": spec.screen_run_id,
            "screen_content_hash": spec.screen_content_hash,
            "target_hash": spec.target_hash,
            "signal_date": spec.signal_date.isoformat(),
            "signal_time": spec.signal_time.isoformat(),
            "execution_date": spec.execution_date.isoformat(),
            "universe_hash": spec.universe_hash,
            "config_hash": spec.config_hash,
            "strategy_version": spec.strategy_version,
            "target_weights_json": spec.target_weights_json,
            "logical_run_key": spec.logical_run_key,
            "revision": spec.revision,
        }
    )


def _order_creation_payload_hash(order: PaperOrder) -> str:
    return _hash_payload(
        {
            "rebalance_run_id": order.rebalance_run_id,
            "account_id": order.account_id,
            "symbol": order.symbol,
            "side": order.side.value,
            "planned_quantity": order.planned_quantity,
            "reference_price_cny": str(money(order.reference_price_cny)),
            "limit_price_cny": (
                str(money(order.limit_price_cny))
                if order.limit_price_cny is not None
                else None
            ),
        }
    )


def _fill_payload_hash(
    fill: FillSpec, execution_date: date, execution_time: datetime
) -> str:
    return _hash_payload(
        {
            "account_id": fill.account_id,
            "symbol": fill.symbol,
            "execution_date": execution_date.isoformat(),
            "execution_time": execution_time.isoformat(),
            "quantity": fill.quantity,
            "price_cny": str(money(fill.price_cny)),
            "commission_cny": str(money(fill.commission_cny)),
            "stamp_tax_cny": str(money(fill.stamp_tax_cny)),
            "other_fee_cny": str(money(fill.other_fee_cny)),
            "source_snapshot_key": fill.source_snapshot_key,
            "source_snapshot_version_id": fill.source_snapshot_version_id,
        }
    )


class PaperRepository:
    def __init__(self, paths: PaperPaths):
        self.paths = paths
        apply_paper_migrations(paths.paper_db_path)
        self.connection = duckdb.connect(str(paths.paper_db_path))

    def close(self) -> None:
        self.connection.close()

    def create_account(
        self,
        account_id: str,
        initial_cash_cny: Decimal | str | int,
        *,
        name: str | None = None,
        opened_at: datetime | None = None,
    ) -> PaperAccount:
        now = opened_at or datetime.now(tz=SHANGHAI)
        initial = money(initial_cash_cny)
        display_name = name or account_id
        self.connection.execute("BEGIN")
        try:
            existing = self.connection.execute(
                "SELECT account_id FROM paper_accounts WHERE account_id = ?",
                [account_id],
            ).fetchone()
            if existing is None:
                self.connection.execute(
                    """
                    INSERT INTO paper_accounts (
                        account_id, name, base_currency, initial_cash_cny,
                        status, created_at, updated_at
                    ) VALUES (?, ?, 'CNY', ?, 'ACTIVE', ?, ?)
                    """,
                    [account_id, display_name, initial, now, now],
                )
                self.connection.execute(
                    """
                    INSERT INTO paper_account_locks (
                        account_id, current_fencing_token, updated_at
                    ) VALUES (?, 0, ?)
                    ON CONFLICT (account_id) DO NOTHING
                    """,
                    [account_id, now],
                )
                self._append_cash_entry_in_tx(
                    CashEntry(
                        cash_entry_id=_new_id("cash"),
                        account_id=account_id,
                        entry_type=CashEntryType.DEPOSIT,
                        amount_cny=initial,
                        source_type="ACCOUNT",
                        source_id=account_id,
                        component="INITIAL_CASH",
                        occurred_at=now,
                    ),
                )
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise
        return PaperAccount(
            account_id=account_id,
            name=display_name,
            initial_cash_cny=initial,
            created_at=now,
            updated_at=now,
        )

    def append_cash_entry(
        self,
        entry: CashEntry,
        *,
        fencing_token: int,
        owner_id: str,
    ) -> str:
        with account_transaction_lock(self.paths.home_dir, entry.account_id):
            self.connection.execute("BEGIN")
            try:
                validate_fencing(
                    self.connection,
                    account_id=entry.account_id,
                    fencing_token=fencing_token,
                    owner_id=owner_id,
                )
                entry_id = self._append_cash_entry_in_tx(entry)
                assert_fencing_commit_guard(
                    self.connection,
                    account_id=entry.account_id,
                    fencing_token=fencing_token,
                    owner_id=owner_id,
                )
                self.connection.execute("COMMIT")
            except Exception:
                self.connection.execute("ROLLBACK")
                raise
        return entry_id

    def _append_cash_entry_in_tx(self, entry: CashEntry) -> str:
        payload_hash = _cash_payload_hash(entry)
        existing = self.connection.execute(
            """
            SELECT cash_entry_id, entry_type, amount_cny, occurred_at
            FROM paper_cash_ledger
            WHERE account_id = ? AND source_type = ? AND source_id = ? AND component = ?
            """,
            [entry.account_id, entry.source_type, entry.source_id, entry.component],
        ).fetchone()
        if existing is not None:
            existing_entry = _cash_entry_payload_from_row(
                existing,
                account_id=entry.account_id,
                source_type=entry.source_type,
                source_id=entry.source_id,
                component=entry.component,
            )
            if _cash_payload_hash(existing_entry) != payload_hash:
                raise IdempotencyConflict(
                    f"cash entry conflict for {entry.account_id}/{entry.component}"
                )
            return str(existing[0])

        created_at = entry.created_at or datetime.now(tz=SHANGHAI)
        balance_rows = self.connection.execute(
            """
            SELECT COALESCE(SUM(amount_cny), 0)
            FROM paper_cash_ledger
            WHERE account_id = ?
            """,
            [entry.account_id],
        ).fetchone()
        balance_after = money(_decimal(balance_rows[0]) + entry.amount_cny)
        self.connection.execute(
            """
            INSERT INTO paper_cash_ledger (
                cash_entry_id, account_id, entry_type, amount_cny,
                source_type, source_id, component, occurred_at,
                balance_after_cny, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                entry.cash_entry_id,
                entry.account_id,
                entry.entry_type.value,
                money(entry.amount_cny),
                entry.source_type,
                entry.source_id,
                entry.component,
                entry.occurred_at,
                balance_after,
                created_at,
            ],
        )
        return entry.cash_entry_id

    def append_position_entry(
        self,
        entry: PositionEntry,
        *,
        fencing_token: int,
        owner_id: str,
    ) -> str:
        with account_transaction_lock(self.paths.home_dir, entry.account_id):
            self.connection.execute("BEGIN")
            try:
                validate_fencing(
                    self.connection,
                    account_id=entry.account_id,
                    fencing_token=fencing_token,
                    owner_id=owner_id,
                )
                entry_id = self._append_position_entry_in_tx(entry)
                assert_fencing_commit_guard(
                    self.connection,
                    account_id=entry.account_id,
                    fencing_token=fencing_token,
                    owner_id=owner_id,
                )
                self.connection.execute("COMMIT")
            except Exception:
                self.connection.execute("ROLLBACK")
                raise
        return entry_id

    def _append_position_entry_in_tx(self, entry: PositionEntry) -> str:
        payload_hash = _position_payload_hash(entry)
        existing = self.connection.execute(
            """
            SELECT position_entry_id, symbol, quantity_delta, cost_delta_cny, effective_date
            FROM paper_position_ledger
            WHERE account_id = ? AND source_type = ? AND source_id = ? AND component = ?
            """,
            [
                entry.account_id,
                entry.source_type.value,
                entry.source_id,
                entry.component,
            ],
        ).fetchone()
        if existing is not None:
            existing_hash = _hash_payload(
                {
                    "symbol": existing[1],
                    "quantity_delta": int(existing[2]),
                    "cost_delta_cny": str(money(_decimal(existing[3]))),
                    "effective_date": existing[4].isoformat()
                    if hasattr(existing[4], "isoformat")
                    else str(existing[4]),
                }
            )
            if existing_hash != payload_hash:
                raise IdempotencyConflict(
                    f"position entry conflict for {entry.account_id}/{entry.component}"
                )
            return str(existing[0])

        created_at = entry.created_at or datetime.now(tz=SHANGHAI)
        business_key = entry.business_key or _business_key(
            entry.account_id,
            entry.source_type.value,
            entry.source_id,
            entry.component,
        )
        self.connection.execute(
            """
            INSERT INTO paper_position_ledger (
                position_entry_id, account_id, symbol, quantity_delta,
                cost_delta_cny, effective_date, source_type, source_id,
                component, business_key, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                entry.position_entry_id,
                entry.account_id,
                entry.symbol,
                entry.quantity_delta,
                money(entry.cost_delta_cny),
                entry.effective_date,
                entry.source_type.value,
                entry.source_id,
                entry.component,
                business_key,
                created_at,
            ],
        )
        if entry.quantity_delta > 0:
            self.connection.execute(
                """
                INSERT INTO paper_lots (
                    lot_id, account_id, symbol, acquired_date, source_type, source_id,
                    original_quantity, remaining_quantity, original_cost_cny,
                    remaining_cost_cny, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    _new_id("lot"),
                    entry.account_id,
                    entry.symbol,
                    entry.effective_date,
                    entry.source_type.value,
                    entry.source_id,
                    entry.quantity_delta,
                    entry.quantity_delta,
                    money(entry.cost_delta_cny),
                    money(entry.cost_delta_cny),
                    created_at,
                ],
            )
        return entry.position_entry_id

    def freeze_screen_run(
        self,
        screen_run: FrozenScreenRun,
        *,
        captured_inputs: list[RunInputCapture] | None = None,
    ) -> FrozenScreenRun:
        created_at = screen_run.created_at or datetime.now(tz=SHANGHAI)
        payload_hash = _screen_run_payload_hash(screen_run)
        self.connection.execute("BEGIN")
        try:
            existing = self.connection.execute(
                """
                SELECT screen_content_hash, status, signal_time, target_portfolio_mode,
                       target_weights_json, cash_weight, dataset_versions_json,
                       event_dataset_versions_json, run_report_json
                FROM frozen_screen_runs
                WHERE screen_run_id = ?
                """,
                [screen_run.screen_run_id],
            ).fetchone()
            if existing is not None:
                existing_run = FrozenScreenRun(
                    screen_run_id=screen_run.screen_run_id,
                    screen_content_hash=existing[0],
                    status=existing[1],
                    signal_time=existing[2],
                    target_portfolio_mode=existing[3],
                    target_weights_json=existing[4],
                    cash_weight=_decimal(existing[5]),
                    dataset_versions_json=existing[6],
                    event_dataset_versions_json=existing[7],
                    run_report_json=existing[8],
                )
                if _screen_run_payload_hash(existing_run) != payload_hash:
                    raise IdempotencyConflict(
                        f"screen run conflict for {screen_run.screen_run_id}"
                    )
            else:
                self.connection.execute(
                    """
                    INSERT INTO frozen_screen_runs (
                        screen_run_id, screen_content_hash, status, signal_time,
                        target_portfolio_mode, target_weights_json, cash_weight,
                        dataset_versions_json, event_dataset_versions_json,
                        run_report_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        screen_run.screen_run_id,
                        screen_run.screen_content_hash,
                        screen_run.status,
                        screen_run.signal_time,
                        screen_run.target_portfolio_mode.value,
                        screen_run.target_weights_json,
                        screen_run.cash_weight,
                        screen_run.dataset_versions_json,
                        screen_run.event_dataset_versions_json,
                        screen_run.run_report_json,
                        created_at,
                    ],
                )
            for item in captured_inputs or []:
                self.capture_run_inputs(item, _in_transaction=True)
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise
        return screen_run.model_copy(update={"created_at": created_at})

    def capture_run_inputs(
        self, capture: RunInputCapture, *, _in_transaction: bool = False
    ) -> None:
        captured_at = datetime.now(tz=SHANGHAI)
        payload_hash = _run_input_payload_hash(capture)
        if not _in_transaction:
            self.connection.execute("BEGIN")
        try:
            existing = self.connection.execute(
                """
                SELECT row_content_hash, row_json, source_dataset_version_id, source_available_at
                FROM paper_run_inputs
                WHERE run_id = ? AND input_type = ? AND scope_key = ?
                """,
                [capture.run_id, capture.input_type, capture.scope_key],
            ).fetchone()
            if existing is not None:
                existing_capture = RunInputCapture(
                    run_id=capture.run_id,
                    input_type=capture.input_type,
                    scope_key=capture.scope_key,
                    row_content_hash=existing[0],
                    row_json=existing[1],
                    source_dataset_version_id=existing[2],
                    source_available_at=existing[3],
                )
                if _run_input_payload_hash(existing_capture) != payload_hash:
                    raise IdempotencyConflict(
                        f"run input conflict for {capture.run_id}/{capture.scope_key}"
                    )
            else:
                self.connection.execute(
                    """
                    INSERT INTO paper_run_inputs (
                        run_id, input_type, scope_key, row_content_hash, row_json,
                        source_dataset_version_id, source_available_at, captured_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        capture.run_id,
                        capture.input_type,
                        capture.scope_key,
                        capture.row_content_hash,
                        capture.row_json,
                        capture.source_dataset_version_id,
                        capture.source_available_at,
                        captured_at,
                    ],
                )
            if not _in_transaction:
                self.connection.execute("COMMIT")
        except Exception:
            if not _in_transaction:
                self.connection.execute("ROLLBACK")
            raise

    def create_rebalance_revision(
        self,
        spec: RebalanceRevisionSpec,
        *,
        fencing_token: int,
        owner_id: str,
    ) -> str:
        with account_transaction_lock(self.paths.home_dir, spec.account_id):
            self.connection.execute("BEGIN")
            try:
                validate_fencing(
                    self.connection,
                    account_id=spec.account_id,
                    fencing_token=fencing_token,
                    owner_id=owner_id,
                )
                rebalance_run_id = self._create_rebalance_revision_in_tx(spec)
                assert_fencing_commit_guard(
                    self.connection,
                    account_id=spec.account_id,
                    fencing_token=fencing_token,
                    owner_id=owner_id,
                )
                self.connection.execute("COMMIT")
            except Exception:
                self.connection.execute("ROLLBACK")
                raise
        return rebalance_run_id

    def _create_rebalance_revision_in_tx(self, spec: RebalanceRevisionSpec) -> str:
        created_at = datetime.now(tz=SHANGHAI)
        payload_hash = _rebalance_spec_payload_hash(spec)
        existing = self.connection.execute(
            """
            SELECT account_id, screen_run_id, screen_content_hash, target_hash,
                   signal_date, signal_time, execution_date, universe_hash,
                   config_hash, strategy_version, target_weights_json,
                   logical_run_key, revision, status
            FROM rebalance_runs
            WHERE rebalance_run_id = ?
            """,
            [spec.rebalance_run_id],
        ).fetchone()
        if existing is not None:
            existing_spec = RebalanceRevisionSpec(
                rebalance_run_id=spec.rebalance_run_id,
                account_id=existing[0],
                screen_run_id=existing[1],
                screen_content_hash=existing[2],
                target_hash=existing[3],
                signal_date=existing[4],
                signal_time=existing[5],
                execution_date=existing[6],
                universe_hash=existing[7],
                config_hash=existing[8],
                strategy_version=existing[9],
                target_weights_json=existing[10],
                logical_run_key=existing[11],
                revision=int(existing[12]),
                status=RunStatus(existing[13]),
            )
            if _rebalance_spec_payload_hash(existing_spec) != payload_hash:
                raise IdempotencyConflict(
                    f"rebalance revision conflict for {spec.rebalance_run_id}"
                )
        else:
            self.connection.execute(
                """
                UPDATE rebalance_runs
                SET is_active_revision = FALSE,
                    active_revision_slot = revision
                WHERE logical_run_key = ? AND is_active_revision = TRUE
                """,
                [spec.logical_run_key],
            )
            self.connection.execute(
                """
                INSERT INTO rebalance_runs (
                    rebalance_run_id, account_id, screen_run_id, screen_content_hash,
                    target_hash, signal_date, signal_time, execution_date,
                    universe_hash, config_hash, strategy_version, target_weights_json,
                    logical_run_key, revision, is_active_revision, active_revision_slot,
                    status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, TRUE, 0, ?, ?)
                """,
                [
                    spec.rebalance_run_id,
                    spec.account_id,
                    spec.screen_run_id,
                    spec.screen_content_hash,
                    spec.target_hash,
                    spec.signal_date,
                    spec.signal_time,
                    spec.execution_date,
                    spec.universe_hash,
                    spec.config_hash,
                    spec.strategy_version,
                    spec.target_weights_json,
                    spec.logical_run_key,
                    spec.revision,
                    spec.status.value,
                    created_at,
                ],
            )
        return spec.rebalance_run_id

    def insert_orders(
        self,
        orders: list[PaperOrder],
        *,
        fencing_token: int,
        owner_id: str,
    ) -> list[str]:
        if not orders:
            return []
        account_ids = {order.account_id for order in orders}
        if len(account_ids) != 1:
            raise InvalidExecutionBatch("insert_orders requires a single account_id")
        account_id = next(iter(account_ids))
        with account_transaction_lock(self.paths.home_dir, account_id):
            self.connection.execute("BEGIN")
            try:
                validate_fencing(
                    self.connection,
                    account_id=account_id,
                    fencing_token=fencing_token,
                    owner_id=owner_id,
                )
                inserted = self._insert_orders_in_tx(orders)
                assert_fencing_commit_guard(
                    self.connection,
                    account_id=account_id,
                    fencing_token=fencing_token,
                    owner_id=owner_id,
                )
                self.connection.execute("COMMIT")
            except Exception:
                self.connection.execute("ROLLBACK")
                raise
        return inserted

    def _insert_orders_in_tx(self, orders: list[PaperOrder]) -> list[str]:
        now = datetime.now(tz=SHANGHAI)
        inserted: list[str] = []
        for order in orders:
            payload_hash = _order_creation_payload_hash(order)
            existing = self.connection.execute(
                """
                SELECT rebalance_run_id, account_id, symbol, side,
                       planned_quantity, filled_quantity, remaining_quantity,
                       reference_price_cny, limit_price_cny, status,
                       rejection_code, rejection_detail
                FROM paper_orders
                WHERE order_id = ?
                """,
                [order.order_id],
            ).fetchone()
            if existing is not None:
                existing_order = PaperOrder(
                    order_id=order.order_id,
                    rebalance_run_id=existing[0],
                    account_id=existing[1],
                    symbol=existing[2],
                    side=OrderSide(existing[3]),
                    planned_quantity=int(existing[4]),
                    filled_quantity=int(existing[5]),
                    remaining_quantity=int(existing[6]),
                    reference_price_cny=_decimal(existing[7]),
                    limit_price_cny=_decimal(existing[8]) if existing[8] is not None else None,
                    status=OrderStatus(existing[9]),
                    rejection_code=existing[10],
                    rejection_detail=existing[11],
                )
                if _order_creation_payload_hash(existing_order) != payload_hash:
                    raise IdempotencyConflict(
                        f"order conflict for {order.order_id}"
                    )
                inserted.append(order.order_id)
                continue
            created_at = order.created_at or now
            updated_at = order.updated_at or now
            self.connection.execute(
                """
                INSERT INTO paper_orders (
                    order_id, rebalance_run_id, account_id, symbol, side,
                    planned_quantity, filled_quantity, remaining_quantity,
                    reference_price_cny, limit_price_cny, status,
                    rejection_code, rejection_detail, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    order.order_id,
                    order.rebalance_run_id,
                    order.account_id,
                    order.symbol,
                    order.side.value,
                    order.planned_quantity,
                    order.filled_quantity,
                    order.remaining_quantity,
                    order.reference_price_cny,
                    order.limit_price_cny,
                    order.status.value,
                    order.rejection_code,
                    order.rejection_detail,
                    created_at,
                    updated_at,
                ],
            )
            inserted.append(order.order_id)
        return inserted

    def list_orders(self, account_id: str) -> list[PaperOrder]:
        rows = self.connection.execute(
            """
            SELECT order_id, rebalance_run_id, account_id, symbol, side,
                   planned_quantity, filled_quantity, remaining_quantity,
                   reference_price_cny, limit_price_cny, status,
                   rejection_code, rejection_detail, created_at, updated_at
            FROM paper_orders
            WHERE account_id = ?
            ORDER BY created_at, order_id
            """,
            [account_id],
        ).fetchall()
        return [
            PaperOrder(
                order_id=row[0],
                rebalance_run_id=row[1],
                account_id=row[2],
                symbol=row[3],
                side=OrderSide(row[4]),
                planned_quantity=int(row[5]),
                filled_quantity=int(row[6]),
                remaining_quantity=int(row[7]),
                reference_price_cny=_decimal(row[8]),
                limit_price_cny=_decimal(row[9]) if row[9] is not None else None,
                status=OrderStatus(row[10]),
                rejection_code=row[11],
                rejection_detail=row[12],
                created_at=row[13],
                updated_at=row[14],
            )
            for row in rows
        ]

    def get_frozen_screen_run(self, screen_run_id: str) -> FrozenScreenRun:
        row = self.connection.execute(
            """
            SELECT screen_run_id, screen_content_hash, status, signal_time,
                   target_portfolio_mode, target_weights_json, cash_weight,
                   dataset_versions_json, event_dataset_versions_json,
                   run_report_json, created_at
            FROM frozen_screen_runs
            WHERE screen_run_id = ?
            """,
            [screen_run_id],
        ).fetchone()
        if row is None:
            raise PaperError(f"screen run {screen_run_id} not found")
        return FrozenScreenRun(
            screen_run_id=row[0],
            screen_content_hash=row[1],
            status=row[2],
            signal_time=row[3],
            target_portfolio_mode=row[4],
            target_weights_json=row[5],
            cash_weight=_decimal(row[6]),
            dataset_versions_json=row[7],
            event_dataset_versions_json=row[8],
            run_report_json=row[9],
            created_at=row[10],
        )

    def get_active_rebalance_revision(
        self, logical_run_key: str
    ) -> RebalanceRevisionSpec | None:
        row = self.connection.execute(
            """
            SELECT rebalance_run_id, account_id, screen_run_id, screen_content_hash,
                   target_hash, signal_date, signal_time, execution_date,
                   universe_hash, config_hash, strategy_version,
                   target_weights_json, logical_run_key, revision, status
            FROM rebalance_runs
            WHERE logical_run_key = ? AND is_active_revision = TRUE
            """,
            [logical_run_key],
        ).fetchone()
        if row is None:
            return None
        return RebalanceRevisionSpec(
            rebalance_run_id=row[0],
            account_id=row[1],
            screen_run_id=row[2],
            screen_content_hash=row[3],
            target_hash=row[4],
            signal_date=row[5],
            signal_time=row[6],
            execution_date=row[7],
            universe_hash=row[8],
            config_hash=row[9],
            strategy_version=row[10],
            target_weights_json=row[11],
            logical_run_key=row[12],
            revision=int(row[13]),
            status=RunStatus(row[14]),
        )

    def rebalance_has_fills(self, rebalance_run_id: str) -> bool:
        row = self.connection.execute(
            """
            SELECT 1
            FROM paper_fills AS fill
            JOIN paper_orders AS ord ON ord.order_id = fill.order_id
            WHERE ord.rebalance_run_id = ?
            LIMIT 1
            """,
            [rebalance_run_id],
        ).fetchone()
        return row is not None

    def list_orders_for_rebalance(self, rebalance_run_id: str) -> list[PaperOrder]:
        rows = self.connection.execute(
            """
            SELECT order_id, rebalance_run_id, account_id, symbol, side,
                   planned_quantity, filled_quantity, remaining_quantity,
                   reference_price_cny, limit_price_cny, status,
                   rejection_code, rejection_detail, created_at, updated_at
            FROM paper_orders
            WHERE rebalance_run_id = ?
            ORDER BY side DESC, symbol, order_id
            """,
            [rebalance_run_id],
        ).fetchall()
        return [
            PaperOrder(
                order_id=row[0],
                rebalance_run_id=row[1],
                account_id=row[2],
                symbol=row[3],
                side=OrderSide(row[4]),
                planned_quantity=int(row[5]),
                filled_quantity=int(row[6]),
                remaining_quantity=int(row[7]),
                reference_price_cny=_decimal(row[8]),
                limit_price_cny=_decimal(row[9]) if row[9] is not None else None,
                status=OrderStatus(row[10]),
                rejection_code=row[11],
                rejection_detail=row[12],
                created_at=row[13],
                updated_at=row[14],
            )
            for row in rows
        ]

    def list_pending_orders_for_rebalance(self, rebalance_run_id: str) -> list[PaperOrder]:
        rows = self.connection.execute(
            """
            SELECT order_id, rebalance_run_id, account_id, symbol, side,
                   planned_quantity, filled_quantity, remaining_quantity,
                   reference_price_cny, limit_price_cny, status,
                   rejection_code, rejection_detail, created_at, updated_at
            FROM paper_orders
            WHERE rebalance_run_id = ? AND status = ?
            ORDER BY side DESC, symbol, order_id
            """,
            [rebalance_run_id, OrderStatus.PENDING.value],
        ).fetchall()
        return [
            PaperOrder(
                order_id=row[0],
                rebalance_run_id=row[1],
                account_id=row[2],
                symbol=row[3],
                side=OrderSide(row[4]),
                planned_quantity=int(row[5]),
                filled_quantity=int(row[6]),
                remaining_quantity=int(row[7]),
                reference_price_cny=_decimal(row[8]),
                limit_price_cny=_decimal(row[9]) if row[9] is not None else None,
                status=OrderStatus(row[10]),
                rejection_code=row[11],
                rejection_detail=row[12],
                created_at=row[13],
                updated_at=row[14],
            )
            for row in rows
        ]

    def acquire_account_lease(
        self,
        account_id: str,
        *,
        owner_id: str,
        lease_seconds: int = 300,
        lock_timeout_seconds: float = 5.0,
    ) -> AccountLease:
        return acquire_account_lease(
            self.connection,
            home_dir=self.paths.home_dir,
            account_id=account_id,
            owner_id=owner_id,
            lease_seconds=lease_seconds,
            lock_timeout_seconds=lock_timeout_seconds,
        )

    def take_over_expired_lease(
        self,
        account_id: str,
        *,
        owner_id: str,
        lease_seconds: int = 300,
        lock_timeout_seconds: float = 5.0,
    ) -> AccountLease:
        return take_over_expired_lease(
            self.connection,
            home_dir=self.paths.home_dir,
            account_id=account_id,
            owner_id=owner_id,
            lease_seconds=lease_seconds,
            lock_timeout_seconds=lock_timeout_seconds,
        )

    def expire_lease_for_test(self, account_id: str) -> None:
        expired_at = datetime.now(tz=SHANGHAI) - timedelta(seconds=1)
        self.connection.execute(
            """
            UPDATE paper_account_locks
            SET lease_until = ?, updated_at = ?
            WHERE account_id = ?
            """,
            [expired_at, expired_at, account_id],
        )

    def apply_execution_batch(
        self,
        batch: ExecutionBatch,
        *,
        fencing_token: int,
        fault_injection: dict[str, FaultHook] | None = None,
    ) -> list[str]:
        fill_ids: list[str] = []
        hooks = fault_injection or {}
        with account_transaction_lock(self.paths.home_dir, batch.account_id):
            self.connection.execute("BEGIN")
            try:
                if "before_validate" in hooks:
                    hooks["before_validate"]()
                validate_fencing(
                    self.connection,
                    account_id=batch.account_id,
                    fencing_token=fencing_token,
                    owner_id=batch.owner_id,
                )
                if "after_validate" in hooks:
                    hooks["after_validate"]()
                for fill in batch.fills:
                    if fill.account_id != batch.account_id:
                        raise InvalidExecutionBatch("fill account_id mismatch")
                    order_row = self.connection.execute(
                        """
                        SELECT order_id, symbol, side, remaining_quantity, filled_quantity,
                               planned_quantity, status
                        FROM paper_orders
                        WHERE order_id = ? AND account_id = ?
                        """,
                        [fill.order_id, batch.account_id],
                    ).fetchone()
                    if order_row is None:
                        raise OrderNotFound(f"order {fill.order_id} not found")

                    _, symbol, side, remaining_qty, filled_qty, planned_qty, status = order_row
                    side = OrderSide(side)
                    remaining_qty = int(remaining_qty)
                    filled_qty = int(filled_qty)
                    planned_qty = int(planned_qty)

                    existing_fill = self.connection.execute(
                        """
                        SELECT fill_id, account_id, symbol, quantity, price_cny,
                               commission_cny, stamp_tax_cny, other_fee_cny,
                               source_snapshot_key, source_snapshot_version_id,
                               execution_time
                        FROM paper_fills
                        WHERE order_id = ? AND execution_date = ? AND fill_sequence = ?
                        """,
                        [fill.order_id, batch.execution_date, fill.fill_sequence],
                    ).fetchone()
                    if existing_fill is None:
                        self.connection.execute(
                            """
                            INSERT INTO paper_fills (
                                fill_id, fill_sequence, order_id, account_id, symbol,
                                execution_date, execution_time, quantity, price_cny,
                                commission_cny, stamp_tax_cny, other_fee_cny,
                                source_snapshot_key, source_snapshot_version_id
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            [
                                fill.fill_id,
                                fill.fill_sequence,
                                fill.order_id,
                                fill.account_id,
                                fill.symbol,
                                batch.execution_date,
                                batch.execution_time,
                                fill.quantity,
                                fill.price_cny,
                                money(fill.commission_cny),
                                money(fill.stamp_tax_cny),
                                money(fill.other_fee_cny),
                                fill.source_snapshot_key,
                                fill.source_snapshot_version_id,
                            ],
                        )
                        fill_ids.append(fill.fill_id)
                        self._apply_fill_ledger_effects(batch, fill, side)
                        if "after_fill" in hooks:
                            hooks["after_fill"]()

                        new_filled = filled_qty + fill.quantity
                        new_remaining = max(planned_qty - new_filled, 0)
                        new_status = (
                            OrderStatus.FILLED
                            if new_remaining == 0
                            else OrderStatus.PARTIALLY_FILLED
                        )
                        if fill.quantity == 0:
                            new_status = OrderStatus(status)
                        self.connection.execute(
                            """
                            UPDATE paper_orders
                            SET filled_quantity = ?,
                                remaining_quantity = ?,
                                status = ?,
                                updated_at = ?
                            WHERE order_id = ?
                            """,
                            [
                                new_filled,
                                new_remaining,
                                new_status.value,
                                datetime.now(tz=SHANGHAI),
                                fill.order_id,
                            ],
                        )
                    else:
                        existing_execution_time = existing_fill[10]
                        existing_spec = FillSpec(
                            fill_id=str(existing_fill[0]),
                            order_id=fill.order_id,
                            account_id=str(existing_fill[1]),
                            symbol=str(existing_fill[2]),
                            quantity=int(existing_fill[3]),
                            price_cny=_decimal(existing_fill[4]),
                            commission_cny=_decimal(existing_fill[5]),
                            stamp_tax_cny=_decimal(existing_fill[6]),
                            other_fee_cny=_decimal(existing_fill[7]),
                            fill_sequence=fill.fill_sequence,
                            source_snapshot_key=existing_fill[8],
                            source_snapshot_version_id=existing_fill[9],
                        )
                        if _fill_payload_hash(
                            existing_spec, batch.execution_date, existing_execution_time
                        ) != _fill_payload_hash(
                            fill, batch.execution_date, batch.execution_time
                        ):
                            raise IdempotencyConflict(
                                f"fill conflict for {fill.order_id}/{batch.execution_date}/{fill.fill_sequence}"
                            )
                        fill_ids.append(str(existing_fill[0]))

                now = datetime.now(tz=SHANGHAI)
                has_rejections = False
                has_partial = False
                for rejection in batch.rejections:
                    if self._apply_order_rejection_in_tx(batch, rejection, now):
                        has_rejections = True

                partial_rows = self.connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM paper_orders
                    WHERE rebalance_run_id = ? AND status = ?
                    """,
                    [batch.rebalance_run_id, OrderStatus.PARTIALLY_FILLED.value],
                ).fetchone()
                if partial_rows is not None and int(partial_rows[0]) > 0:
                    has_partial = True

                if "after_cash" in hooks:
                    hooks["after_cash"]()

                self._rebuild_positions_projection(batch.account_id, batch.execution_date)
                if "before_projection" in hooks:
                    hooks["before_projection"]()

                assert_account_invariants(
                    self.connection,
                    batch.account_id,
                    as_of_date=batch.execution_date,
                )
                final_status = RunStatus.COMPLETED
                if has_rejections or has_partial:
                    final_status = RunStatus.COMPLETED_WITH_REJECTIONS
                self.connection.execute(
                    """
                    UPDATE rebalance_runs
                    SET status = ?, completed_at = ?
                    WHERE rebalance_run_id = ?
                    """,
                    [
                        final_status.value,
                        datetime.now(tz=SHANGHAI),
                        batch.rebalance_run_id,
                    ],
                )
                assert_fencing_commit_guard(
                    self.connection,
                    account_id=batch.account_id,
                    fencing_token=fencing_token,
                    owner_id=batch.owner_id,
                )
                self.connection.execute("COMMIT")
            except StaleFencingToken:
                self.connection.execute("ROLLBACK")
                raise
            except Exception:
                self.connection.execute("ROLLBACK")
                raise
        return fill_ids

    def _apply_order_rejection_in_tx(
        self,
        batch: ExecutionBatch,
        rejection: OrderRejectionSpec,
        now: datetime,
    ) -> bool:
        order_row = self.connection.execute(
            """
            SELECT status, rejection_code, rejection_detail, rebalance_run_id
            FROM paper_orders
            WHERE order_id = ? AND account_id = ?
            """,
            [rejection.order_id, batch.account_id],
        ).fetchone()
        if order_row is None:
            raise OrderNotFound(f"order {rejection.order_id} not found")

        status, rejection_code, rejection_detail, rebalance_run_id = order_row
        if rebalance_run_id != batch.rebalance_run_id:
            raise InvalidExecutionBatch(
                f"order {rejection.order_id} belongs to {rebalance_run_id}, "
                f"not {batch.rebalance_run_id}"
            )

        current_status = OrderStatus(status)
        if current_status == OrderStatus.REJECTED:
            if (
                rejection_code == rejection.rejection_code
                and rejection_detail == rejection.rejection_detail
            ):
                return True
            raise IdempotencyConflict(
                f"rejection conflict for {rejection.order_id}: "
                f"{rejection_code}/{rejection_detail} != "
                f"{rejection.rejection_code}/{rejection.rejection_detail}"
            )

        if current_status != OrderStatus.PENDING:
            raise InvalidExecutionBatch(
                f"order {rejection.order_id} cannot be rejected from status {current_status.value}"
            )

        self.connection.execute(
            """
            UPDATE paper_orders
            SET status = ?,
                rejection_code = ?,
                rejection_detail = ?,
                updated_at = ?
            WHERE order_id = ? AND account_id = ? AND status = ?
            """,
            [
                OrderStatus.REJECTED.value,
                rejection.rejection_code,
                rejection.rejection_detail,
                now,
                rejection.order_id,
                batch.account_id,
                OrderStatus.PENDING.value,
            ],
        )
        updated = self.connection.execute(
            """
            SELECT status
            FROM paper_orders
            WHERE order_id = ? AND account_id = ?
            """,
            [rejection.order_id, batch.account_id],
        ).fetchone()
        if updated is None or OrderStatus(updated[0]) != OrderStatus.REJECTED:
            raise InvalidExecutionBatch(
                f"failed to reject order {rejection.order_id}"
            )
        return True

    def _apply_fill_ledger_effects(
        self, batch: ExecutionBatch, fill: FillSpec, side: OrderSide
    ) -> None:
        notional = money(_decimal(fill.price_cny) * Decimal(fill.quantity))
        if side == OrderSide.BUY:
            lot_id = _new_id("lot")
            now = datetime.now(tz=SHANGHAI)
            total_cost = money(notional + fill.commission_cny + fill.other_fee_cny)
            self.connection.execute(
                """
                INSERT INTO paper_lots (
                    lot_id, account_id, symbol, acquired_date, source_type, source_id,
                    original_quantity, remaining_quantity, original_cost_cny,
                    remaining_cost_cny, created_at
                ) VALUES (?, ?, ?, ?, 'FILL', ?, ?, ?, ?, ?, ?)
                """,
                [
                    lot_id,
                    fill.account_id,
                    fill.symbol,
                    batch.execution_date,
                    fill.fill_id,
                    fill.quantity,
                    fill.quantity,
                    total_cost,
                    total_cost,
                    now,
                ],
            )
            self._insert_position_entry_in_tx(
                PositionEntry(
                    position_entry_id=_new_id("pos"),
                    account_id=fill.account_id,
                    symbol=fill.symbol,
                    quantity_delta=fill.quantity,
                    cost_delta_cny=total_cost,
                    effective_date=batch.execution_date,
                    source_type=PositionSourceType.FILL,
                    source_id=fill.fill_id,
                    component="QUANTITY",
                    business_key=_business_key(
                        fill.account_id, PositionSourceType.FILL.value, fill.fill_id, "QUANTITY"
                    ),
                )
            )
            self._insert_cash_entry_in_tx(
                CashEntry(
                    cash_entry_id=_new_id("cash"),
                    account_id=fill.account_id,
                    entry_type=CashEntryType.BUY,
                    amount_cny=money(-notional),
                    source_type="FILL",
                    source_id=fill.fill_id,
                    component="NOTIONAL",
                    occurred_at=batch.execution_time,
                )
            )
            if fill.commission_cny != 0:
                self._insert_cash_entry_in_tx(
                    CashEntry(
                        cash_entry_id=_new_id("cash"),
                        account_id=fill.account_id,
                        entry_type=CashEntryType.COMMISSION,
                        amount_cny=money(-fill.commission_cny),
                        source_type="FILL",
                        source_id=fill.fill_id,
                        component="COMMISSION",
                        occurred_at=batch.execution_time,
                    )
                )
            if fill.other_fee_cny != 0:
                self._insert_cash_entry_in_tx(
                    CashEntry(
                        cash_entry_id=_new_id("cash"),
                        account_id=fill.account_id,
                        entry_type=CashEntryType.ADJUSTMENT,
                        amount_cny=money(-fill.other_fee_cny),
                        source_type="FILL",
                        source_id=fill.fill_id,
                        component="OTHER_FEE",
                        occurred_at=batch.execution_time,
                    )
                )
        else:
            self._consume_fifo_lots(fill, batch.execution_date)
            disposed_cost = self._fifo_disposed_cost(fill)
            self._insert_position_entry_in_tx(
                PositionEntry(
                    position_entry_id=_new_id("pos"),
                    account_id=fill.account_id,
                    symbol=fill.symbol,
                    quantity_delta=-fill.quantity,
                    cost_delta_cny=money(-disposed_cost),
                    effective_date=batch.execution_date,
                    source_type=PositionSourceType.FILL,
                    source_id=fill.fill_id,
                    component="QUANTITY",
                    business_key=_business_key(
                        fill.account_id, PositionSourceType.FILL.value, fill.fill_id, "QUANTITY"
                    ),
                )
            )
            self._insert_cash_entry_in_tx(
                CashEntry(
                    cash_entry_id=_new_id("cash"),
                    account_id=fill.account_id,
                    entry_type=CashEntryType.SELL,
                    amount_cny=money(notional),
                    source_type="FILL",
                    source_id=fill.fill_id,
                    component="NOTIONAL",
                    occurred_at=batch.execution_time,
                )
            )
            if fill.commission_cny != 0:
                self._insert_cash_entry_in_tx(
                    CashEntry(
                        cash_entry_id=_new_id("cash"),
                        account_id=fill.account_id,
                        entry_type=CashEntryType.COMMISSION,
                        amount_cny=money(-fill.commission_cny),
                        source_type="FILL",
                        source_id=fill.fill_id,
                        component="COMMISSION",
                        occurred_at=batch.execution_time,
                    )
                )
            if fill.stamp_tax_cny != 0:
                self._insert_cash_entry_in_tx(
                    CashEntry(
                        cash_entry_id=_new_id("cash"),
                        account_id=fill.account_id,
                        entry_type=CashEntryType.STAMP_TAX,
                        amount_cny=money(-fill.stamp_tax_cny),
                        source_type="FILL",
                        source_id=fill.fill_id,
                        component="STAMP_TAX",
                        occurred_at=batch.execution_time,
                    )
                )
            if fill.other_fee_cny != 0:
                self._insert_cash_entry_in_tx(
                    CashEntry(
                        cash_entry_id=_new_id("cash"),
                        account_id=fill.account_id,
                        entry_type=CashEntryType.ADJUSTMENT,
                        amount_cny=money(-fill.other_fee_cny),
                        source_type="FILL",
                        source_id=fill.fill_id,
                        component="OTHER_FEE",
                        occurred_at=batch.execution_time,
                    )
                )

    def _fifo_disposed_cost(self, fill: FillSpec) -> Decimal:
        rows = self.connection.execute(
            """
            SELECT remaining_quantity, remaining_cost_cny
            FROM paper_lots
            WHERE account_id = ? AND symbol = ? AND remaining_quantity > 0
            ORDER BY acquired_date, created_at, lot_id
            """,
            [fill.account_id, fill.symbol],
        ).fetchall()
        remaining = fill.quantity
        disposed = Decimal("0")
        for qty, cost in rows:
            lot_qty = int(qty)
            lot_cost = _decimal(cost)
            if lot_qty <= 0:
                continue
            take = min(remaining, lot_qty)
            unit_cost = lot_cost / Decimal(lot_qty)
            disposed += money(unit_cost * Decimal(take))
            remaining -= take
            if remaining == 0:
                break
        return money(disposed)

    def _consume_fifo_lots(self, fill: FillSpec, execution_date: date) -> None:
        rows = self.connection.execute(
            """
            SELECT lot_id, remaining_quantity, remaining_cost_cny
            FROM paper_lots
            WHERE account_id = ? AND symbol = ? AND remaining_quantity > 0
            ORDER BY acquired_date, created_at, lot_id
            """,
            [fill.account_id, fill.symbol],
        ).fetchall()
        remaining = fill.quantity
        now = datetime.now(tz=SHANGHAI)
        for lot_id, qty, cost in rows:
            if remaining <= 0:
                break
            lot_qty = int(qty)
            lot_cost = _decimal(cost)
            take = min(remaining, lot_qty)
            new_qty = lot_qty - take
            unit_cost = lot_cost / Decimal(lot_qty)
            new_cost = money(unit_cost * Decimal(new_qty))
            closed_at = now if new_qty == 0 else None
            self.connection.execute(
                """
                UPDATE paper_lots
                SET remaining_quantity = ?, remaining_cost_cny = ?, closed_at = ?
                WHERE lot_id = ?
                """,
                [new_qty, new_cost, closed_at, lot_id],
            )
            remaining -= take

    def _insert_cash_entry_in_tx(self, entry: CashEntry) -> None:
        payload_hash = _cash_payload_hash(entry)
        existing = self.connection.execute(
            """
            SELECT cash_entry_id, entry_type, amount_cny, occurred_at
            FROM paper_cash_ledger
            WHERE account_id = ? AND source_type = ? AND source_id = ? AND component = ?
            """,
            [entry.account_id, entry.source_type, entry.source_id, entry.component],
        ).fetchone()
        if existing is not None:
            existing_entry = _cash_entry_payload_from_row(
                existing,
                account_id=entry.account_id,
                source_type=entry.source_type,
                source_id=entry.source_id,
                component=entry.component,
            )
            if _cash_payload_hash(existing_entry) != payload_hash:
                raise IdempotencyConflict(
                    f"cash entry conflict for {entry.account_id}/{entry.component}"
                )
            return
        balance_rows = self.connection.execute(
            """
            SELECT COALESCE(SUM(amount_cny), 0)
            FROM paper_cash_ledger
            WHERE account_id = ?
            """,
            [entry.account_id],
        ).fetchone()
        balance_after = money(_decimal(balance_rows[0]) + entry.amount_cny)
        created_at = entry.created_at or datetime.now(tz=SHANGHAI)
        self.connection.execute(
            """
            INSERT INTO paper_cash_ledger (
                cash_entry_id, account_id, entry_type, amount_cny,
                source_type, source_id, component, occurred_at,
                balance_after_cny, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                entry.cash_entry_id,
                entry.account_id,
                entry.entry_type.value,
                money(entry.amount_cny),
                entry.source_type,
                entry.source_id,
                entry.component,
                entry.occurred_at,
                balance_after,
                created_at,
            ],
        )

    def _insert_position_entry_in_tx(self, entry: PositionEntry) -> None:
        payload_hash = _position_payload_hash(entry)
        existing = self.connection.execute(
            """
            SELECT position_entry_id, symbol, quantity_delta, cost_delta_cny, effective_date
            FROM paper_position_ledger
            WHERE account_id = ? AND source_type = ? AND source_id = ? AND component = ?
            """,
            [
                entry.account_id,
                entry.source_type.value,
                entry.source_id,
                entry.component,
            ],
        ).fetchone()
        if existing is not None:
            existing_hash = _hash_payload(
                {
                    "symbol": existing[1],
                    "quantity_delta": int(existing[2]),
                    "cost_delta_cny": str(money(_decimal(existing[3]))),
                    "effective_date": existing[4].isoformat()
                    if hasattr(existing[4], "isoformat")
                    else str(existing[4]),
                }
            )
            if existing_hash != payload_hash:
                raise IdempotencyConflict(
                    f"position entry conflict for {entry.account_id}/{entry.component}"
                )
            return
        created_at = entry.created_at or datetime.now(tz=SHANGHAI)
        business_key = entry.business_key or _business_key(
            entry.account_id,
            entry.source_type.value,
            entry.source_id,
            entry.component,
        )
        self.connection.execute(
            """
            INSERT INTO paper_position_ledger (
                position_entry_id, account_id, symbol, quantity_delta,
                cost_delta_cny, effective_date, source_type, source_id,
                component, business_key, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                entry.position_entry_id,
                entry.account_id,
                entry.symbol,
                entry.quantity_delta,
                money(entry.cost_delta_cny),
                entry.effective_date,
                entry.source_type.value,
                entry.source_id,
                entry.component,
                business_key,
                created_at,
            ],
        )

    def _sum_cash_as_of(self, account_id: str, as_of_date: date) -> Decimal:
        cash_row = self.connection.execute(
            """
            SELECT COALESCE(SUM(amount_cny), 0)
            FROM paper_cash_ledger
            WHERE account_id = ? AND CAST(occurred_at AS DATE) <= ?
            """,
            [account_id, as_of_date],
        ).fetchone()
        return money(_decimal(cash_row[0]))

    def _position_symbols_as_of(self, account_id: str, as_of_date: date) -> list[str]:
        rows = self.connection.execute(
            """
            SELECT DISTINCT symbol
            FROM paper_position_ledger
            WHERE account_id = ? AND effective_date <= ?
            """,
            [account_id, as_of_date],
        ).fetchall()
        return sorted({row[0] for row in rows})

    def _position_quantity_as_of(
        self,
        account_id: str,
        symbol: str,
        as_of_date: date,
    ) -> int:
        qty_row = self.connection.execute(
            """
            SELECT COALESCE(SUM(quantity_delta), 0)
            FROM paper_position_ledger
            WHERE account_id = ? AND symbol = ? AND effective_date <= ?
            """,
            [account_id, symbol, as_of_date],
        ).fetchone()
        return int(qty_row[0])

    def _lot_totals_as_of(
        self,
        account_id: str,
        symbol: str,
        as_of_date: date,
    ) -> tuple[int, Decimal]:
        lot_row = self.connection.execute(
            """
            SELECT COALESCE(SUM(remaining_quantity), 0),
                   COALESCE(SUM(remaining_cost_cny), 0)
            FROM paper_lots
            WHERE account_id = ? AND symbol = ?
              AND acquired_date <= ?
              AND (closed_at IS NULL OR CAST(closed_at AS DATE) > ?)
            """,
            [account_id, symbol, as_of_date, as_of_date],
        ).fetchone()
        return int(lot_row[0]), money(_decimal(lot_row[1]))

    def _available_quantity_as_of(
        self,
        account_id: str,
        symbol: str,
        as_of_date: date,
    ) -> int:
        available_row = self.connection.execute(
            """
            SELECT COALESCE(SUM(remaining_quantity), 0)
            FROM paper_lots
            WHERE account_id = ? AND symbol = ?
              AND acquired_date < ?
              AND (closed_at IS NULL OR CAST(closed_at AS DATE) > ?)
            """,
            [account_id, symbol, as_of_date, as_of_date],
        ).fetchone()
        return int(available_row[0])

    def _compute_account_projection(self, account_id: str, as_of_date: date) -> AccountProjection:
        cash_cny = self._sum_cash_as_of(account_id, as_of_date)
        positions: dict[str, PositionProjection] = {}
        for symbol in self._position_symbols_as_of(account_id, as_of_date):
            quantity = self._position_quantity_as_of(account_id, symbol, as_of_date)
            if quantity == 0:
                continue
            lot_qty, lot_cost = self._lot_totals_as_of(account_id, symbol, as_of_date)
            available_quantity = self._available_quantity_as_of(account_id, symbol, as_of_date)
            average_cost = money(lot_cost / Decimal(quantity)) if quantity > 0 else money(0)
            if lot_qty != quantity and lot_qty > 0:
                average_cost = money(lot_cost / Decimal(lot_qty))
            positions[symbol] = PositionProjection(
                symbol=symbol,
                quantity=quantity,
                available_quantity=available_quantity,
                average_cost_cny=average_cost,
            )
        return AccountProjection(
            account_id=account_id,
            cash_cny=cash_cny,
            positions=positions,
        )

    def _rebuild_positions_projection(self, account_id: str, as_of_date: date) -> None:
        now = datetime.now(tz=SHANGHAI)
        symbol_set = set(self._position_symbols_as_of(account_id, as_of_date))
        existing = self.connection.execute(
            "SELECT symbol FROM paper_positions WHERE account_id = ?",
            [account_id],
        ).fetchall()
        symbol_set.update(row[0] for row in existing)

        for symbol in sorted(symbol_set):
            quantity = self._position_quantity_as_of(account_id, symbol, as_of_date)
            lot_qty, lot_cost = self._lot_totals_as_of(account_id, symbol, as_of_date)
            available_quantity = self._available_quantity_as_of(account_id, symbol, as_of_date)
            average_cost = money(lot_cost / Decimal(quantity)) if quantity > 0 else money(0)
            if quantity == 0:
                self.connection.execute(
                    "DELETE FROM paper_positions WHERE account_id = ? AND symbol = ?",
                    [account_id, symbol],
                )
                continue
            if lot_qty != quantity:
                average_cost = money(lot_cost / Decimal(lot_qty)) if lot_qty > 0 else money(0)
            self.connection.execute(
                """
                INSERT INTO paper_positions (
                    account_id, symbol, quantity, available_quantity,
                    average_cost_cny, market_value_cny, realized_pnl_cny,
                    unrealized_pnl_cny, updated_at, version
                ) VALUES (?, ?, ?, ?, ?, 0, 0, 0, ?, 0)
                ON CONFLICT (account_id, symbol) DO UPDATE SET
                    quantity = excluded.quantity,
                    available_quantity = excluded.available_quantity,
                    average_cost_cny = excluded.average_cost_cny,
                    updated_at = excluded.updated_at,
                    version = paper_positions.version + 1
                """,
                [
                    account_id,
                    symbol,
                    quantity,
                    available_quantity,
                    average_cost,
                    now,
                ],
            )

    def rebuild_account_projection(
        self,
        account_id: str,
        *,
        as_of_date: date | None = None,
        fencing_token: int,
        owner_id: str,
    ) -> AccountProjection:
        as_of = as_of_date or datetime.now(tz=SHANGHAI).date()
        with account_transaction_lock(self.paths.home_dir, account_id):
            self.connection.execute("BEGIN")
            try:
                validate_fencing(
                    self.connection,
                    account_id=account_id,
                    fencing_token=fencing_token,
                    owner_id=owner_id,
                )
                self._rebuild_positions_projection(account_id, as_of)
                assert_fencing_commit_guard(
                    self.connection,
                    account_id=account_id,
                    fencing_token=fencing_token,
                    owner_id=owner_id,
                )
                self.connection.execute("COMMIT")
            except Exception:
                self.connection.execute("ROLLBACK")
                raise
        return self._compute_account_projection(account_id, as_of)

    def load_account_snapshot(self, account_id: str, *, as_of_date: date | None = None) -> AccountSnapshot:
        row = self.connection.execute(
            """
            SELECT account_id, name, base_currency, initial_cash_cny, status,
                   created_at, updated_at
            FROM paper_accounts
            WHERE account_id = ?
            """,
            [account_id],
        ).fetchone()
        if row is None:
            raise AccountNotFound(f"account {account_id} not found")
        account = PaperAccount(
            account_id=row[0],
            name=row[1],
            base_currency=row[2],
            initial_cash_cny=money(_decimal(row[3])),
            status=row[4],
            created_at=row[5],
            updated_at=row[6],
        )
        as_of = as_of_date or datetime.now(tz=SHANGHAI).date()
        projection = self._compute_account_projection(account_id, as_of)
        return AccountSnapshot(
            account=account,
            cash_cny=projection.cash_cny,
            positions=projection.positions,
        )

    def apply_corporate_action(
        self,
        spec: CorporateActionApplicationSpec,
        *,
        fencing_token: int,
        owner_id: str,
        position_entry: PositionEntry | None = None,
        cash_entry: CashEntry | None = None,
        lot_multiplier: Decimal | None = None,
        lot_target_total: int | None = None,
        effective_date: date | None = None,
    ) -> str:
        applied_at = datetime.now(tz=SHANGHAI)
        position_entry_id: str | None = None
        cash_entry_id: str | None = None
        with account_transaction_lock(self.paths.home_dir, spec.account_id):
            self.connection.execute("BEGIN")
            try:
                validate_fencing(
                    self.connection,
                    account_id=spec.account_id,
                    fencing_token=fencing_token,
                    owner_id=owner_id,
                )
                existing = self.connection.execute(
                    """
                    SELECT revision, status, entitlement_quantity, entitlement_source_hash
                    FROM paper_corporate_action_applications
                    WHERE account_id = ? AND corporate_action_id = ?
                      AND is_active_revision = TRUE
                    """,
                    [spec.account_id, spec.corporate_action_id],
                ).fetchone()
                if existing is not None:
                    existing_revision = int(existing[0])
                    if (
                        existing_revision == spec.revision
                        and existing[1] == spec.status.value
                        and int(existing[2]) == spec.entitlement_quantity
                        and existing[3] == spec.entitlement_source_hash
                    ):
                        self.connection.execute("COMMIT")
                        return f"{spec.account_id}:{spec.corporate_action_id}:{existing_revision}"
                    if existing_revision == spec.revision:
                        raise IdempotencyConflict(
                            f"corporate action conflict for {spec.corporate_action_id} revision {spec.revision}"
                        )
                    if spec.revision < existing_revision:
                        raise IdempotencyConflict(
                            f"corporate action revision {spec.revision} superseded by {existing_revision}"
                        )

                self.connection.execute(
                    """
                    UPDATE paper_corporate_action_applications
                    SET is_active_revision = FALSE,
                        active_revision_slot = revision
                    WHERE account_id = ? AND corporate_action_id = ? AND is_active_revision = TRUE
                    """,
                    [spec.account_id, spec.corporate_action_id],
                )

                if (
                    spec.status == CorporateActionApplicationStatus.APPLIED
                    and lot_multiplier is not None
                    and lot_target_total is not None
                    and position_entry is not None
                ):
                    self._adjust_lots_for_multiplier(
                        spec.account_id,
                        position_entry.symbol,
                        multiplier=lot_multiplier,
                        target_total=lot_target_total,
                    )
                    position_entry_id = self._insert_position_entry_in_tx(position_entry)
                    self._rebuild_positions_projection(
                        spec.account_id,
                        effective_date or position_entry.effective_date,
                    )

                if (
                    spec.status == CorporateActionApplicationStatus.APPLIED
                    and cash_entry is not None
                ):
                    cash_entry_id = self._append_cash_entry_in_tx(cash_entry)
                    rebuild_date = effective_date or cash_entry.occurred_at.date()
                    self._rebuild_positions_projection(spec.account_id, rebuild_date)

                self.connection.execute(
                    """
                    INSERT INTO paper_corporate_action_applications (
                        account_id, corporate_action_id, revision, entitlement_quantity,
                        entitlement_source_hash, status, position_entry_id, cash_entry_id,
                        applied_at, is_active_revision, active_revision_slot
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, TRUE, 0)
                    ON CONFLICT (account_id, corporate_action_id, revision) DO NOTHING
                    """,
                    [
                        spec.account_id,
                        spec.corporate_action_id,
                        spec.revision,
                        spec.entitlement_quantity,
                        spec.entitlement_source_hash,
                        spec.status.value,
                        position_entry_id,
                        cash_entry_id,
                        applied_at
                        if spec.status == CorporateActionApplicationStatus.APPLIED
                        else None,
                    ],
                )
                if spec.status == CorporateActionApplicationStatus.APPLIED:
                    assert_account_invariants(
                        self.connection,
                        spec.account_id,
                        as_of_date=effective_date,
                    )
                assert_fencing_commit_guard(
                    self.connection,
                    account_id=spec.account_id,
                    fencing_token=fencing_token,
                    owner_id=owner_id,
                )
                self.connection.execute("COMMIT")
            except Exception:
                self.connection.execute("ROLLBACK")
                raise
        return f"{spec.account_id}:{spec.corporate_action_id}:{spec.revision}"

    def _adjust_lots_for_multiplier(
        self,
        account_id: str,
        symbol: str,
        *,
        multiplier: Decimal,
        target_total: int,
    ) -> None:
        rows = self.connection.execute(
            """
            SELECT lot_id, remaining_quantity
            FROM paper_lots
            WHERE account_id = ? AND symbol = ? AND closed_at IS NULL
            ORDER BY acquired_date, created_at, lot_id
            """,
            [account_id, symbol],
        ).fetchall()
        if not rows:
            raise InvalidExecutionBatch(f"no open lots for {symbol}")
        allocations: list[tuple[str, int, int]] = []
        allocated = 0
        for lot_id, remaining in rows:
            remaining = int(remaining)
            new_qty = int(Decimal(remaining) * multiplier)
            allocations.append((lot_id, remaining, new_qty))
            allocated += new_qty
        remainder = target_total - allocated
        if remainder < 0:
            raise InvalidExecutionBatch(
                f"lot adjustment overshoot for {symbol}: {allocated} > {target_total}"
            )
        if remainder > 0:
            lot_id, remaining, new_qty = allocations[0]
            allocations[0] = (lot_id, remaining, new_qty + remainder)
        now = datetime.now(tz=SHANGHAI)
        for lot_id, old_qty, new_qty in allocations:
            if new_qty == old_qty:
                continue
            if new_qty <= 0:
                self.connection.execute(
                    """
                    UPDATE paper_lots
                    SET remaining_quantity = 0, closed_at = ?
                    WHERE lot_id = ?
                    """,
                    [now, lot_id],
                )
            else:
                self.connection.execute(
                    """
                    UPDATE paper_lots
                    SET remaining_quantity = ?
                    WHERE lot_id = ?
                    """,
                    [new_qty, lot_id],
                )

    def corporate_action_cash_total(self, account_id: str, corporate_action_id: str) -> Decimal:
        row = self.connection.execute(
            """
            SELECT COALESCE(SUM(amount_cny), 0)
            FROM paper_cash_ledger
            WHERE account_id = ? AND source_type = ? AND source_id = ?
            """,
            [account_id, "CORPORATE_ACTION", corporate_action_id],
        ).fetchone()
        return money(_decimal(row[0]))

    def cash_on(self, account_id: str, as_of_date: date) -> Decimal:
        row = self.connection.execute(
            """
            SELECT COALESCE(SUM(amount_cny), 0)
            FROM paper_cash_ledger
            WHERE account_id = ? AND CAST(occurred_at AS DATE) <= ?
            """,
            [account_id, as_of_date],
        ).fetchone()
        return money(_decimal(row[0]))

    def cash_entries_for(self, account_id: str, source_id: str) -> list[CashEntry]:
        rows = self.connection.execute(
            """
            SELECT cash_entry_id, account_id, entry_type, amount_cny, source_type,
                   source_id, component, occurred_at, balance_after_cny, created_at
            FROM paper_cash_ledger
            WHERE account_id = ? AND source_id = ?
            ORDER BY occurred_at, cash_entry_id
            """,
            [account_id, source_id],
        ).fetchall()
        return [
            CashEntry(
                cash_entry_id=row[0],
                account_id=row[1],
                entry_type=row[2],
                amount_cny=money(_decimal(row[3])),
                source_type=row[4],
                source_id=row[5],
                component=row[6],
                occurred_at=row[7],
                balance_after_cny=money(_decimal(row[8])) if row[8] is not None else None,
                created_at=row[9],
            )
            for row in rows
        ]

    def position_quantity_on(
        self,
        account_id: str,
        symbol: str,
        as_of_date: date,
    ) -> int:
        row = self.connection.execute(
            """
            SELECT COALESCE(SUM(quantity_delta), 0)
            FROM paper_position_ledger
            WHERE account_id = ? AND symbol = ? AND effective_date <= ?
            """,
            [account_id, symbol, as_of_date],
        ).fetchone()
        return int(row[0])

    def get_active_corporate_action_application(
        self,
        account_id: str,
        corporate_action_id: str,
    ) -> dict[str, object] | None:
        row = self.connection.execute(
            """
            SELECT revision, status, entitlement_quantity, entitlement_source_hash
            FROM paper_corporate_action_applications
            WHERE account_id = ? AND corporate_action_id = ? AND is_active_revision = TRUE
            """,
            [account_id, corporate_action_id],
        ).fetchone()
        if row is None:
            return None
        return {
            "revision": row[0],
            "status": row[1],
            "entitlement_quantity": row[2],
            "entitlement_source_hash": row[3],
        }

    def get_nav_history_context(
        self,
        account_id: str,
        valuation_date: date,
    ) -> NavHistoryContext:
        rows = self.connection.execute(
            """
            SELECT valuation_date, cash_cny, positions_value_cny, total_equity_cny,
                   daily_return, cumulative_return, drawdown,
                   valuation_manifest_hash, created_at
            FROM paper_nav_snapshots
            WHERE account_id = ? AND valuation_date < ?
            ORDER BY valuation_date
            """,
            [account_id, valuation_date],
        ).fetchall()
        latest: NavSnapshot | None = None
        peak = Decimal("0")
        initial = Decimal("0")
        for index, row in enumerate(rows):
            nav = NavSnapshot(
                account_id=account_id,
                valuation_date=row[0],
                cash_cny=money(_decimal(row[1])),
                positions_value_cny=money(_decimal(row[2])),
                total_equity_cny=money(_decimal(row[3])),
                daily_return=_decimal(row[4]) if row[4] is not None else None,
                cumulative_return=_decimal(row[5]) if row[5] is not None else None,
                drawdown=_decimal(row[6]) if row[6] is not None else None,
                valuation_manifest_hash=row[7],
                created_at=row[8],
            )
            latest = nav
            peak = max(peak, nav.total_equity_cny)
            if index == 0:
                initial = nav.total_equity_cny
        if latest is None:
            account_row = self.connection.execute(
                "SELECT initial_cash_cny FROM paper_accounts WHERE account_id = ?",
                [account_id],
            ).fetchone()
            initial = money(_decimal(account_row[0])) if account_row else money(0)
            peak = initial
        return NavHistoryContext(
            latest=latest,
            peak_equity_cny=money(peak),
            initial_equity_cny=money(initial),
        )

    def initial_equity(self, account_id: str) -> Decimal:
        row = self.connection.execute(
            """
            SELECT total_equity_cny
            FROM paper_nav_snapshots
            WHERE account_id = ?
            ORDER BY valuation_date ASC
            LIMIT 1
            """,
            [account_id],
        ).fetchone()
        if row is not None:
            return money(_decimal(row[0]))
        account_row = self.connection.execute(
            "SELECT initial_cash_cny FROM paper_accounts WHERE account_id = ?",
            [account_id],
        ).fetchone()
        if account_row is None:
            raise AccountNotFound(f"account {account_id} not found")
        return money(_decimal(account_row[0]))

    def update_position_marks(
        self,
        account_id: str,
        *,
        valuation_date: date,
        marks: dict[str, Decimal],
        fencing_token: int,
        owner_id: str,
    ) -> None:
        now = datetime.now(tz=SHANGHAI)
        with account_transaction_lock(self.paths.home_dir, account_id):
            self.connection.execute("BEGIN")
            try:
                validate_fencing(
                    self.connection,
                    account_id=account_id,
                    fencing_token=fencing_token,
                    owner_id=owner_id,
                )
                for symbol, price in marks.items():
                    row = self.connection.execute(
                        """
                        SELECT quantity, average_cost_cny
                        FROM paper_positions
                        WHERE account_id = ? AND symbol = ?
                        """,
                        [account_id, symbol],
                    ).fetchone()
                    if row is None:
                        continue
                    quantity = int(row[0])
                    average_cost = money(_decimal(row[1]))
                    market_value = money(price * Decimal(quantity))
                    cost_basis = money(average_cost * Decimal(quantity))
                    unrealized = money(market_value - cost_basis)
                    self.connection.execute(
                        """
                        UPDATE paper_positions
                        SET last_price_cny = ?,
                            market_value_cny = ?,
                            unrealized_pnl_cny = ?,
                            updated_at = ?
                        WHERE account_id = ? AND symbol = ?
                        """,
                        [price, market_value, unrealized, now, account_id, symbol],
                    )
                assert_fencing_commit_guard(
                    self.connection,
                    account_id=account_id,
                    fencing_token=fencing_token,
                    owner_id=owner_id,
                )
                self.connection.execute("COMMIT")
            except Exception:
                self.connection.execute("ROLLBACK")
                raise

    def write_valuation(
        self,
        spec: ValuationWriteSpec,
        *,
        fencing_token: int,
        owner_id: str,
    ) -> NavSnapshot:
        created_at = datetime.now(tz=SHANGHAI)
        with account_transaction_lock(self.paths.home_dir, spec.account_id):
            self.connection.execute("BEGIN")
            try:
                validate_fencing(
                    self.connection,
                    account_id=spec.account_id,
                    fencing_token=fencing_token,
                    owner_id=owner_id,
                )
                self.connection.execute(
                    """
                    INSERT INTO paper_nav_snapshots (
                        account_id, valuation_date, cash_cny, positions_value_cny,
                        total_equity_cny, daily_return, cumulative_return, drawdown,
                        valuation_manifest_hash, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (account_id, valuation_date) DO UPDATE SET
                        cash_cny = excluded.cash_cny,
                        positions_value_cny = excluded.positions_value_cny,
                        total_equity_cny = excluded.total_equity_cny,
                        daily_return = excluded.daily_return,
                        cumulative_return = excluded.cumulative_return,
                        drawdown = excluded.drawdown,
                        valuation_manifest_hash = excluded.valuation_manifest_hash,
                        created_at = excluded.created_at
                    """,
                    [
                        spec.account_id,
                        spec.valuation_date,
                        money(spec.cash_cny),
                        money(spec.positions_value_cny),
                        money(spec.total_equity_cny),
                        spec.daily_return,
                        spec.cumulative_return,
                        spec.drawdown,
                        spec.valuation_manifest_hash,
                        created_at,
                    ],
                )
                for source in spec.sources:
                    self.connection.execute(
                        """
                        INSERT INTO paper_valuation_sources (
                            account_id, valuation_date, symbol, quantity, price_cny,
                            price_status, source_row_key, dataset_version_id,
                            row_content_hash, available_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT (account_id, valuation_date, symbol) DO UPDATE SET
                            quantity = excluded.quantity,
                            price_cny = excluded.price_cny,
                            price_status = excluded.price_status,
                            source_row_key = excluded.source_row_key,
                            dataset_version_id = excluded.dataset_version_id,
                            row_content_hash = excluded.row_content_hash,
                            available_at = excluded.available_at
                        """,
                        [
                            spec.account_id,
                            spec.valuation_date,
                            source["symbol"],
                            source["quantity"],
                            source["price_cny"],
                            source["price_status"],
                            source["source_row_key"],
                            source.get("dataset_version_id"),
                            source["row_content_hash"],
                            source["available_at"],
                        ],
                    )
                assert_fencing_commit_guard(
                    self.connection,
                    account_id=spec.account_id,
                    fencing_token=fencing_token,
                    owner_id=owner_id,
                )
                self.connection.execute("COMMIT")
            except Exception:
                self.connection.execute("ROLLBACK")
                raise
        return NavSnapshot(
            account_id=spec.account_id,
            valuation_date=spec.valuation_date,
            cash_cny=money(spec.cash_cny),
            positions_value_cny=money(spec.positions_value_cny),
            total_equity_cny=money(spec.total_equity_cny),
            daily_return=spec.daily_return,
            cumulative_return=spec.cumulative_return,
            drawdown=spec.drawdown,
            valuation_manifest_hash=spec.valuation_manifest_hash,
            created_at=created_at,
        )

    def find_active_rebalance_for_execution(
        self, account_id: str, execution_date: date
    ) -> str | None:
        row = self.connection.execute(
            """
            SELECT rebalance_run_id
            FROM rebalance_runs
            WHERE account_id = ? AND execution_date = ? AND is_active_revision = TRUE
            ORDER BY revision DESC
            LIMIT 1
            """,
            [account_id, execution_date],
        ).fetchone()
        return row[0] if row is not None else None

    def get_rebalance_revision(self, rebalance_run_id: str) -> RebalanceRevisionSpec | None:
        row = self.connection.execute(
            """
            SELECT rebalance_run_id, account_id, screen_run_id, screen_content_hash,
                   target_hash, signal_date, signal_time, execution_date,
                   universe_hash, config_hash, strategy_version,
                   target_weights_json, logical_run_key, revision, status
            FROM rebalance_runs
            WHERE rebalance_run_id = ?
            """,
            [rebalance_run_id],
        ).fetchone()
        if row is None:
            return None
        return RebalanceRevisionSpec(
            rebalance_run_id=row[0],
            account_id=row[1],
            screen_run_id=row[2],
            screen_content_hash=row[3],
            target_hash=row[4],
            signal_date=row[5],
            signal_time=row[6],
            execution_date=row[7],
            universe_hash=row[8],
            config_hash=row[9],
            strategy_version=row[10],
            target_weights_json=row[11],
            logical_run_key=row[12],
            revision=int(row[13]),
            status=RunStatus(row[14]),
        )

    def list_fills(
        self,
        account_id: str,
        *,
        rebalance_run_id: str | None = None,
        execution_date: date | None = None,
    ) -> list[PaperFill]:
        query = """
            SELECT fill.fill_id, fill.fill_sequence, fill.order_id, fill.account_id,
                   fill.symbol, fill.execution_date, fill.execution_time, fill.quantity,
                   fill.price_cny, fill.commission_cny, fill.stamp_tax_cny,
                   fill.other_fee_cny, fill.source_snapshot_key, fill.source_snapshot_version_id
            FROM paper_fills AS fill
        """
        params: list[Any] = []
        clauses = ["fill.account_id = ?"]
        params.append(account_id)
        if rebalance_run_id is not None:
            query += " JOIN paper_orders AS ord ON ord.order_id = fill.order_id"
            clauses.append("ord.rebalance_run_id = ?")
            params.append(rebalance_run_id)
        if execution_date is not None:
            clauses.append("fill.execution_date = ?")
            params.append(execution_date)
        query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY fill.execution_date, fill.execution_time, fill.fill_id"
        rows = self.connection.execute(query, params).fetchall()
        return [
            PaperFill(
                fill_id=row[0],
                fill_sequence=int(row[1]),
                order_id=row[2],
                account_id=row[3],
                symbol=row[4],
                execution_date=row[5],
                execution_time=row[6],
                quantity=int(row[7]),
                price_cny=_decimal(row[8]),
                commission_cny=_decimal(row[9]),
                stamp_tax_cny=_decimal(row[10]),
                other_fee_cny=_decimal(row[11]),
                source_snapshot_key=row[12],
                source_snapshot_version_id=row[13],
            )
            for row in rows
        ]

    def get_nav_snapshot(self, account_id: str, valuation_date: date) -> NavSnapshot | None:
        row = self.connection.execute(
            """
            SELECT account_id, valuation_date, cash_cny, positions_value_cny,
                   total_equity_cny, daily_return, cumulative_return, drawdown,
                   valuation_manifest_hash, created_at
            FROM paper_nav_snapshots
            WHERE account_id = ? AND valuation_date = ?
            """,
            [account_id, valuation_date],
        ).fetchone()
        if row is None:
            return None
        return NavSnapshot(
            account_id=row[0],
            valuation_date=row[1],
            cash_cny=money(_decimal(row[2])),
            positions_value_cny=money(_decimal(row[3])),
            total_equity_cny=money(_decimal(row[4])),
            daily_return=_decimal(row[5]) if row[5] is not None else None,
            cumulative_return=_decimal(row[6]) if row[6] is not None else None,
            drawdown=_decimal(row[7]) if row[7] is not None else None,
            valuation_manifest_hash=row[8],
            created_at=row[9],
        )

    def list_recent_run_ids(self, account_id: str, *, limit: int = 5) -> list[str]:
        rows = self.connection.execute(
            """
            SELECT run_id, MAX(started_at) AS last_started
            FROM paper_run_steps
            WHERE run_id LIKE ?
            GROUP BY run_id
            ORDER BY last_started DESC
            LIMIT ?
            """,
            [f"%:{account_id}:%", limit],
        ).fetchall()
        return [row[0] for row in rows]

    def save_run_step(self, spec: RunStepWriteSpec) -> None:
        now = datetime.now(tz=SHANGHAI)
        started_at = spec.started_at or now
        finished_at = spec.finished_at
        if finished_at is None and spec.status != StepStatus.RUNNING:
            finished_at = now
        existing = self.get_run_step(spec.run_id, spec.step_name)
        if existing is not None and existing.status == StepStatus.SUCCESS:
            if spec.input_hash and existing.input_hash != spec.input_hash:
                raise IdempotencyConflict(
                    f"run step input hash conflict for {spec.run_id}/{spec.step_name}"
                )
            if spec.status == StepStatus.SUCCESS:
                return
        self.connection.execute(
            """
            INSERT INTO paper_run_steps (
                run_id, step_name, status, input_hash, output_json, error_json,
                started_at, finished_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (run_id, step_name) DO UPDATE SET
                status = excluded.status,
                input_hash = excluded.input_hash,
                output_json = excluded.output_json,
                error_json = excluded.error_json,
                started_at = COALESCE(paper_run_steps.started_at, excluded.started_at),
                finished_at = excluded.finished_at
            """,
            [
                spec.run_id,
                spec.step_name,
                spec.status.value,
                spec.input_hash,
                spec.output_json,
                spec.error_json,
                started_at,
                finished_at,
            ],
        )

    def get_run_step(self, run_id: str, step_name: str) -> RunStep | None:
        row = self.connection.execute(
            """
            SELECT run_id, step_name, status, input_hash, output_json, error_json,
                   started_at, finished_at
            FROM paper_run_steps
            WHERE run_id = ? AND step_name = ?
            """,
            [run_id, step_name],
        ).fetchone()
        if row is None:
            return None
        return RunStep(
            run_id=row[0],
            step_name=row[1],
            status=StepStatus(row[2]),
            input_hash=row[3],
            output_json=row[4],
            error_json=row[5],
            started_at=row[6],
            finished_at=row[7],
        )

    def list_run_steps(self, run_id: str) -> list[RunStep]:
        rows = self.connection.execute(
            """
            SELECT run_id, step_name, status, input_hash, output_json, error_json,
                   started_at, finished_at
            FROM paper_run_steps
            WHERE run_id = ?
            ORDER BY started_at, step_name
            """,
            [run_id],
        ).fetchall()
        return [
            RunStep(
                run_id=row[0],
                step_name=row[1],
                status=StepStatus(row[2]),
                input_hash=row[3],
                output_json=row[4],
                error_json=row[5],
                started_at=row[6],
                finished_at=row[7],
            )
            for row in rows
        ]

    def count_fills(self, account_id: str | None = None) -> int:
        if account_id is None:
            row = self.connection.execute("SELECT COUNT(*) FROM paper_fills").fetchone()
        else:
            row = self.connection.execute(
                "SELECT COUNT(*) FROM paper_fills WHERE account_id = ?",
                [account_id],
            ).fetchone()
        return int(row[0])

    def count_rows(self) -> dict[str, int]:
        tables = (
            "paper_fills",
            "paper_cash_ledger",
            "paper_position_ledger",
            "paper_lots",
            "paper_positions",
            "paper_orders",
        )
        counts: dict[str, int] = {}
        for table in tables:
            row = self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            counts[table] = int(row[0])
        return counts
