"""Paper repository tests (Stage 6A Task 2)."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from tradingagents.paper.config import PaperPaths
from tradingagents.paper.contracts import (
    FrozenScreenRun,
    OrderSide,
    OrderStatus,
    PaperOrder,
    PositionEntry,
    PositionSourceType,
    RunStatus,
    TargetPortfolioMode,
)
from tradingagents.paper.exceptions import IdempotencyConflict, StaleFencingToken
from tradingagents.paper.invariants import assert_account_invariants
from tradingagents.paper.repository import ExecutionBatch, RebalanceRevisionSpec, ValuationWriteSpec
from tests.paper.conftest import (
    ACCOUNT_OPENED_AT,
    EXECUTION_BATCH,
    EXECUTION_TIME,
    SIGNAL_TIME,
    TRADE_DATE,
    acquire_test_lease,
    append_cash_with_lease,
    append_position_with_lease,
    cash_entry,
    insert_orders_with_lease,
    make_execution_batch,
    make_partial_execution_batch,
    position_entry,
    rebuild_projection_with_lease,
    seed_execution_orders,
    seed_partial_execution_orders,
)


def _raise_after_fill() -> None:
    raise RuntimeError("after_fill")


def _raise_after_cash() -> None:
    raise RuntimeError("after_cash")


def _raise_before_projection() -> None:
    raise RuntimeError("before_projection")


def test_paper_paths_use_separate_paper_db(tmp_path):
    paths = PaperPaths(home_dir=tmp_path)
    assert paths.paper_db_path == tmp_path / "data" / "paper.duckdb"


def test_create_account_appends_initial_cash_idempotently(repo):
    repo.create_account("demo", Decimal("1000000.00"), opened_at=ACCOUNT_OPENED_AT)
    repo.create_account("demo", Decimal("1000000.00"), opened_at=ACCOUNT_OPENED_AT)
    snapshot = repo.load_account_snapshot("demo")
    assert snapshot.cash_cny == Decimal("1000000.00")
    rows = repo.connection.execute(
        "SELECT COUNT(*) FROM paper_cash_ledger WHERE account_id = ? AND component = 'INITIAL_CASH'",
        ["demo"],
    ).fetchone()
    assert int(rows[0]) == 1


def test_load_account_snapshot_excludes_future_positions(repo):
    repo.create_account("demo", Decimal("1000000.00"), opened_at=ACCOUNT_OPENED_AT)
    append_position_with_lease(
        repo,
        PositionEntry(
            position_entry_id="pos-future",
            account_id="demo",
            symbol="600000",
            quantity_delta=1000,
            cost_delta_cny=Decimal("10000.00"),
            effective_date=date(2026, 6, 30),
            source_type=PositionSourceType.ADJUSTMENT,
            source_id="seed",
            component="QUANTITY",
            business_key="demo:ADJUSTMENT:seed-future:QUANTITY",
        ),
    )
    snapshot = repo.load_account_snapshot("demo", as_of_date=date(2026, 6, 24))
    assert snapshot.cash_cny == Decimal("1000000.00")
    assert "600000" not in snapshot.positions


def test_cash_and_position_projection_rebuild_from_ledgers(repo):
    repo.create_account("demo", Decimal("1000000.00"), opened_at=ACCOUNT_OPENED_AT)
    append_position_with_lease(repo, position_entry())
    rebuilt = rebuild_projection_with_lease(repo, "demo")
    assert rebuilt.cash_cny == Decimal("1000000.00")
    assert rebuilt.positions["600000"].quantity == 1000


def test_append_cash_without_fencing_raises(repo):
    repo.create_account("demo", Decimal("1000000.00"), opened_at=ACCOUNT_OPENED_AT)
    with pytest.raises(TypeError):
        repo.append_cash_entry(cash_entry(entry_id="cash-x", component="BONUS", source_id="x"))


def test_append_position_without_fencing_raises(repo):
    repo.create_account("demo", Decimal("1000000.00"), opened_at=ACCOUNT_OPENED_AT)
    with pytest.raises(TypeError):
        repo.append_position_entry(position_entry(entry_id="pos-x"))


def test_rebuild_projection_without_fencing_raises(repo):
    repo.create_account("demo", Decimal("1000000.00"), opened_at=ACCOUNT_OPENED_AT)
    with pytest.raises(TypeError):
        repo.rebuild_account_projection("demo", as_of_date=TRADE_DATE)


def test_public_in_transaction_bypass_removed(repo):
    repo.create_account("demo", Decimal("1000000.00"), opened_at=ACCOUNT_OPENED_AT)
    with pytest.raises(TypeError):
        repo.append_cash_entry(
            cash_entry(entry_id="cash-x", component="BONUS", source_id="x"),
            _in_transaction=True,  # type: ignore[call-arg]
        )
    with pytest.raises(TypeError):
        repo.rebuild_account_projection(
            "demo",
            as_of_date=TRADE_DATE,
            _in_transaction=True,  # type: ignore[call-arg]
        )


def test_duplicate_business_key_same_payload_is_idempotent(repo):
    repo.create_account("demo", Decimal("1000000.00"), opened_at=ACCOUNT_OPENED_AT)
    entry = cash_entry(entry_id="cash-a", component="BONUS", source_id="promo-1", amount_cny=Decimal("100.00"))
    first = append_cash_with_lease(repo, entry)
    second = append_cash_with_lease(
        repo,
        cash_entry(entry_id="cash-b", component="BONUS", source_id="promo-1", amount_cny=Decimal("100.00")),
    )
    assert first == second
    count = repo.connection.execute(
        """
        SELECT COUNT(*) FROM paper_cash_ledger
        WHERE account_id = ? AND source_id = ? AND component = 'BONUS'
        """,
        ["demo", "promo-1"],
    ).fetchone()
    assert int(count[0]) == 1


def test_duplicate_business_key_different_payload_raises(repo):
    repo.create_account("demo", Decimal("1000000.00"), opened_at=ACCOUNT_OPENED_AT)
    append_cash_with_lease(
        repo,
        cash_entry(entry_id="cash-a", component="BONUS", source_id="promo-1", amount_cny=Decimal("100.00")),
    )
    with pytest.raises(IdempotencyConflict):
        append_cash_with_lease(
            repo,
            cash_entry(entry_id="cash-b", component="BONUS", source_id="promo-1", amount_cny=Decimal("200.00")),
        )


def test_stale_fencing_token_cannot_commit(repo):
    seed_execution_orders(repo)
    first = repo.acquire_account_lease("demo", owner_id="one")
    repo.expire_lease_for_test("demo")
    second = repo.take_over_expired_lease("demo", owner_id="two")
    with pytest.raises(StaleFencingToken):
        repo.apply_execution_batch(EXECUTION_BATCH, fencing_token=first.token)
    fill_ids = repo.apply_execution_batch(
        make_execution_batch(owner_id="two"),
        fencing_token=second.token,
    )
    assert fill_ids == ["fill-1"]


def test_apply_execution_batch_updates_orders_positions_and_cash(repo):
    seed_execution_orders(repo)
    lease = repo.acquire_account_lease("demo", owner_id="executor")
    repo.apply_execution_batch(EXECUTION_BATCH, fencing_token=lease.token)
    orders = repo.list_orders("demo")
    assert orders[0].status.value == "FILLED"
    assert orders[0].filled_quantity == 1000
    projection = repo.rebuild_account_projection(
        "demo",
        as_of_date=TRADE_DATE,
        fencing_token=lease.token,
        owner_id=lease.owner_id,
    )
    assert projection.positions["600000"].quantity == 1000
    assert projection.cash_cny == Decimal("989995.00")
    assert_account_invariants(repo.connection, "demo", as_of_date=TRADE_DATE)


def test_apply_execution_batch_is_idempotent(repo):
    seed_execution_orders(repo)
    lease = repo.acquire_account_lease("demo", owner_id="executor")
    repo.apply_execution_batch(EXECUTION_BATCH, fencing_token=lease.token)
    before = repo.count_rows()
    orders_before = repo.list_orders("demo")
    repo.apply_execution_batch(EXECUTION_BATCH, fencing_token=lease.token)
    after = repo.count_rows()
    orders_after = repo.list_orders("demo")
    assert after == before
    assert orders_after[0].filled_quantity == orders_before[0].filled_quantity == 1000
    assert orders_after[0].remaining_quantity == orders_before[0].remaining_quantity == 0
    assert orders_after[0].status == orders_before[0].status


def test_partial_fill_rerun_preserves_order_state(repo):
    seed_partial_execution_orders(repo, planned_quantity=2000)
    batch = make_partial_execution_batch(quantity=1000)
    lease = repo.acquire_account_lease("demo", owner_id="executor")
    repo.apply_execution_batch(batch, fencing_token=lease.token)
    repo.apply_execution_batch(batch, fencing_token=lease.token)
    order = repo.list_orders("demo")[0]
    assert order.filled_quantity == 1000
    assert order.remaining_quantity == 1000
    assert order.status.value == "PARTIALLY_FILLED"
    fill_count = repo.connection.execute(
        "SELECT COALESCE(SUM(quantity), 0) FROM paper_fills WHERE order_id = ?",
        ["ord-buy-600000"],
    ).fetchone()
    assert int(fill_count[0]) == 1000
    projection = repo.rebuild_account_projection(
        "demo",
        as_of_date=TRADE_DATE,
        fencing_token=lease.token,
        owner_id=lease.owner_id,
    )
    assert projection.positions["600000"].quantity == 1000


def test_fault_injection_after_fill_rolls_back(repo):
    seed_execution_orders(repo)
    lease = repo.acquire_account_lease("demo", owner_id="executor")
    before = repo.count_rows()
    with pytest.raises(RuntimeError, match="after_fill"):
        repo.apply_execution_batch(
            EXECUTION_BATCH,
            fencing_token=lease.token,
            fault_injection={"after_fill": _raise_after_fill},
        )
    assert repo.count_rows() == before


def test_fault_injection_after_cash_rolls_back(repo):
    seed_execution_orders(repo)
    lease = repo.acquire_account_lease("demo", owner_id="executor")
    before = repo.count_rows()
    with pytest.raises(RuntimeError, match="after_cash"):
        repo.apply_execution_batch(
            EXECUTION_BATCH,
            fencing_token=lease.token,
            fault_injection={"after_cash": _raise_after_cash},
        )
    assert repo.count_rows() == before


def test_fault_injection_before_projection_rolls_back(repo):
    seed_execution_orders(repo)
    lease = repo.acquire_account_lease("demo", owner_id="executor")
    before = repo.count_rows()
    with pytest.raises(RuntimeError, match="before_projection"):
        repo.apply_execution_batch(
            EXECUTION_BATCH,
            fencing_token=lease.token,
            fault_injection={"before_projection": _raise_before_projection},
        )
    assert repo.count_rows() == before


def test_write_valuation_persists_nav(repo):
    repo.create_account("demo", Decimal("1000000.00"), opened_at=ACCOUNT_OPENED_AT)
    lease = acquire_test_lease(repo)
    nav = repo.write_valuation(
        ValuationWriteSpec(
            account_id="demo",
            valuation_date=date(2026, 6, 23),
            cash_cny=Decimal("1000000.00"),
            positions_value_cny=Decimal("0.00"),
            total_equity_cny=Decimal("1000000.00"),
        ),
        fencing_token=lease.token,
        owner_id=lease.owner_id,
    )
    assert nav.total_equity_cny == Decimal("1000000.00")
    assert_account_invariants(repo.connection, "demo")


def test_apply_corporate_action_stub(repo):
    repo.create_account("demo", Decimal("1000000.00"), opened_at=ACCOUNT_OPENED_AT)
    from tradingagents.paper.contracts import CorporateActionApplicationStatus
    from tradingagents.paper.repository import CorporateActionApplicationSpec

    lease = acquire_test_lease(repo)
    key = repo.apply_corporate_action(
        CorporateActionApplicationSpec(
            account_id="demo",
            corporate_action_id="ca-div-1",
            revision=1,
            entitlement_quantity=1000,
            entitlement_source_hash="hash-1",
            status=CorporateActionApplicationStatus.PENDING,
        ),
        fencing_token=lease.token,
        owner_id=lease.owner_id,
    )
    assert key == "demo:ca-div-1:1"


def test_freeze_screen_run_conflict_raises(repo):
    run = FrozenScreenRun(
        screen_run_id="screen-x",
        screen_content_hash="hash-a",
        status="OK",
        signal_time=SIGNAL_TIME,
        target_portfolio_mode=TargetPortfolioMode.WEIGHTS,
        target_weights_json="{}",
        cash_weight=Decimal("1.0"),
        run_report_json="{}",
    )
    repo.freeze_screen_run(run)
    conflicting = run.model_copy(update={"screen_content_hash": "hash-b"})
    with pytest.raises(IdempotencyConflict):
        repo.freeze_screen_run(conflicting)


def test_insert_orders_conflict_raises(repo):
    seed_execution_orders(repo)
    orders = repo.list_orders("demo")
    conflicting = orders[0].model_copy(update={"planned_quantity": 2000})
    lease = repo.acquire_account_lease("demo", owner_id="executor")
    with pytest.raises(IdempotencyConflict):
        repo.insert_orders(
            [conflicting],
            fencing_token=lease.token,
            owner_id=lease.owner_id,
        )


def test_create_rebalance_without_fencing_raises(repo):
    repo.create_account("demo", Decimal("1000000.00"), opened_at=ACCOUNT_OPENED_AT)
    spec = RebalanceRevisionSpec(
        rebalance_run_id="reb-x",
        account_id="demo",
        screen_run_id="screen-1",
        screen_content_hash="hash",
        target_hash="target",
        signal_date=SIGNAL_TIME.date(),
        signal_time=SIGNAL_TIME,
        execution_date=TRADE_DATE,
        universe_hash="uni",
        config_hash="cfg",
        strategy_version="v1",
        target_weights_json="{}",
        logical_run_key="demo:run",
    )
    with pytest.raises(TypeError):
        repo.create_rebalance_revision(spec)


def test_insert_orders_without_fencing_raises(repo):
    seed_execution_orders(repo)
    with pytest.raises(TypeError):
        repo.insert_orders(repo.list_orders("demo"))


def test_insert_orders_idempotent_after_execution(repo):
    seed_execution_orders(repo)
    original_order = PaperOrder(
        order_id="ord-buy-600000",
        rebalance_run_id="reb-1",
        account_id="demo",
        symbol="600000",
        side=OrderSide.BUY,
        planned_quantity=1000,
        remaining_quantity=1000,
        reference_price_cny=Decimal("10.00"),
        status=OrderStatus.PENDING,
    )
    lease = repo.acquire_account_lease("demo", owner_id="executor")
    repo.apply_execution_batch(EXECUTION_BATCH, fencing_token=lease.token)
    assert repo.list_orders("demo")[0].status == OrderStatus.FILLED
    lease2 = repo.acquire_account_lease("demo", owner_id="executor")
    repo.insert_orders(
        [original_order],
        fencing_token=lease2.token,
        owner_id=lease2.owner_id,
    )


def test_rebalance_revision_idempotent_after_completed(repo):
    seed_execution_orders(repo)
    original_spec = RebalanceRevisionSpec(
        rebalance_run_id="reb-1",
        account_id="demo",
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
        logical_run_key=f"demo:{TRADE_DATE}:uni-1",
        revision=1,
        status=RunStatus.PENDING,
    )
    lease = repo.acquire_account_lease("demo", owner_id="executor")
    repo.apply_execution_batch(EXECUTION_BATCH, fencing_token=lease.token)
    lease2 = repo.acquire_account_lease("demo", owner_id="executor")
    repo.create_rebalance_revision(
        original_spec,
        fencing_token=lease2.token,
        owner_id=lease2.owner_id,
    )


def test_order_idempotent_after_rejection_metadata(repo):
    seed_execution_orders(repo)
    original_order = PaperOrder(
        order_id="ord-buy-600000",
        rebalance_run_id="reb-1",
        account_id="demo",
        symbol="600000",
        side=OrderSide.BUY,
        planned_quantity=1000,
        remaining_quantity=1000,
        reference_price_cny=Decimal("10.00"),
        status=OrderStatus.PENDING,
    )
    repo.connection.execute(
        """
        UPDATE paper_orders
        SET status = 'REJECTED', rejection_code = 'LIMIT', rejection_detail = 'hit limit'
        WHERE order_id = ?
        """,
        ["ord-buy-600000"],
    )
    insert_orders_with_lease(repo, [original_order])


def test_cash_entry_different_occurred_at_raises(repo):
    repo.create_account("demo", Decimal("1000000.00"), opened_at=ACCOUNT_OPENED_AT)
    append_cash_with_lease(
        repo,
        cash_entry(
            entry_id="cash-a",
            component="BONUS",
            source_id="promo-1",
            amount_cny=Decimal("100.00"),
        ),
    )
    with pytest.raises(IdempotencyConflict):
        append_cash_with_lease(
            repo,
            cash_entry(
                entry_id="cash-b",
                component="BONUS",
                source_id="promo-1",
                amount_cny=Decimal("100.00"),
                occurred_at=SIGNAL_TIME + timedelta(days=1),
            ),
        )


def test_fill_different_execution_time_raises(repo):
    seed_execution_orders(repo)
    lease = repo.acquire_account_lease("demo", owner_id="executor")
    repo.apply_execution_batch(EXECUTION_BATCH, fencing_token=lease.token)
    conflicting = ExecutionBatch(
        account_id="demo",
        rebalance_run_id="reb-1",
        execution_date=TRADE_DATE,
        execution_time=EXECUTION_TIME + timedelta(hours=1),
        owner_id="executor",
        fills=EXECUTION_BATCH.fills,
    )
    with pytest.raises(IdempotencyConflict):
        repo.apply_execution_batch(conflicting, fencing_token=lease.token)


def test_duplicate_fill_key_different_payload_raises(repo):
    seed_execution_orders(repo)
    lease = repo.acquire_account_lease("demo", owner_id="executor")
    first_batch = make_execution_batch(quantity=1000, price_cny=Decimal("10.00"))
    repo.apply_execution_batch(first_batch, fencing_token=lease.token)
    conflicting = make_execution_batch(
        fill_id="fill-conflict",
        quantity=500,
        price_cny=Decimal("11.00"),
    )
    with pytest.raises(IdempotencyConflict):
        repo.apply_execution_batch(conflicting, fencing_token=lease.token)


def test_load_account_snapshot_does_not_mutate_projection(repo):
    repo.create_account("demo", Decimal("1000000.00"), opened_at=ACCOUNT_OPENED_AT)
    append_position_with_lease(repo, position_entry())
    before = repo.connection.execute(
        "SELECT COUNT(*) FROM paper_positions WHERE account_id = ?",
        ["demo"],
    ).fetchone()
    repo.load_account_snapshot("demo", as_of_date=TRADE_DATE)
    after = repo.connection.execute(
        "SELECT COUNT(*) FROM paper_positions WHERE account_id = ?",
        ["demo"],
    ).fetchone()
    assert before == after
    snapshot = repo.load_account_snapshot("demo", as_of_date=TRADE_DATE)
    assert snapshot.cash_cny == Decimal("1000000.00")
    assert snapshot.positions["600000"].quantity == 1000
