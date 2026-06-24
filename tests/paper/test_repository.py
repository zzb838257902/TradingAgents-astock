"""Paper repository tests (Stage 6A Task 2)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from tradingagents.paper.config import PaperPaths
from tradingagents.paper.contracts import FrozenScreenRun, TargetPortfolioMode
from tradingagents.paper.exceptions import IdempotencyConflict, LeaseNotHeld, StaleFencingToken
from tradingagents.paper.invariants import assert_account_invariants
from tradingagents.paper.repository import ValuationWriteSpec
from tests.paper.conftest import (
    EXECUTION_BATCH,
    SIGNAL_TIME,
    TRADE_DATE,
    append_cash_with_lease,
    append_position_with_lease,
    cash_entry,
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
    repo.create_account("demo", Decimal("1000000.00"))
    repo.create_account("demo", Decimal("1000000.00"))
    snapshot = repo.load_account_snapshot("demo")
    assert snapshot.cash_cny == Decimal("1000000.00")
    rows = repo.connection.execute(
        "SELECT COUNT(*) FROM paper_cash_ledger WHERE account_id = ? AND component = 'INITIAL_CASH'",
        ["demo"],
    ).fetchone()
    assert int(rows[0]) == 1


def test_cash_and_position_projection_rebuild_from_ledgers(repo):
    repo.create_account("demo", Decimal("1000000.00"))
    append_cash_with_lease(repo, cash_entry())
    append_position_with_lease(repo, position_entry())
    rebuilt = rebuild_projection_with_lease(repo, "demo")
    assert rebuilt.cash_cny == Decimal("1000000.00")
    assert rebuilt.positions["600000"].quantity == 1000


def test_append_cash_without_fencing_raises(repo):
    repo.create_account("demo", Decimal("1000000.00"))
    with pytest.raises(LeaseNotHeld):
        repo.append_cash_entry(cash_entry(entry_id="cash-x", component="BONUS", source_id="x"))


def test_append_position_without_fencing_raises(repo):
    repo.create_account("demo", Decimal("1000000.00"))
    with pytest.raises(LeaseNotHeld):
        repo.append_position_entry(position_entry(entry_id="pos-x"))


def test_rebuild_projection_without_fencing_raises(repo):
    repo.create_account("demo", Decimal("1000000.00"))
    with pytest.raises(LeaseNotHeld):
        repo.rebuild_account_projection("demo", as_of_date=TRADE_DATE)


def test_duplicate_business_key_same_payload_is_idempotent(repo):
    repo.create_account("demo", Decimal("1000000.00"))
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
    repo.create_account("demo", Decimal("1000000.00"))
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
    repo.create_account("demo", Decimal("1000000.00"))
    nav = repo.write_valuation(
        ValuationWriteSpec(
            account_id="demo",
            valuation_date=date(2026, 6, 23),
            cash_cny=Decimal("1000000.00"),
            positions_value_cny=Decimal("0.00"),
            total_equity_cny=Decimal("1000000.00"),
        )
    )
    assert nav.total_equity_cny == Decimal("1000000.00")
    assert_account_invariants(repo.connection, "demo")


def test_apply_corporate_action_stub(repo):
    repo.create_account("demo", Decimal("1000000.00"))
    from tradingagents.paper.contracts import CorporateActionApplicationStatus
    from tradingagents.paper.repository import CorporateActionApplicationSpec

    key = repo.apply_corporate_action(
        CorporateActionApplicationSpec(
            account_id="demo",
            corporate_action_id="ca-div-1",
            revision=1,
            entitlement_quantity=1000,
            entitlement_source_hash="hash-1",
            status=CorporateActionApplicationStatus.PENDING,
        )
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
    with pytest.raises(IdempotencyConflict):
        repo.insert_orders([conflicting])
