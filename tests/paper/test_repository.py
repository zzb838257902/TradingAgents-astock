"""Paper repository tests (Stage 6A Task 2)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from tradingagents.paper.config import PaperPaths
from tradingagents.paper.exceptions import IdempotencyConflict, StaleFencingToken
from tradingagents.paper.invariants import assert_account_invariants
from tradingagents.paper.repository import ValuationWriteSpec
from tests.paper.conftest import (
    EXECUTION_BATCH,
    TRADE_DATE,
    cash_entry,
    make_execution_batch,
    position_entry,
    seed_execution_orders,
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
    repo.append_cash_entry(cash_entry())
    repo.append_position_entry(position_entry())
    rebuilt = repo.rebuild_account_projection("demo", as_of_date=TRADE_DATE)
    assert rebuilt.cash_cny == Decimal("1000000.00")
    assert rebuilt.positions["600000"].quantity == 1000


def test_duplicate_business_key_same_payload_is_idempotent(repo):
    repo.create_account("demo", Decimal("1000000.00"))
    first = repo.append_cash_entry(
        cash_entry(entry_id="cash-a", component="BONUS", source_id="promo-1", amount_cny=Decimal("100.00"))
    )
    second = repo.append_cash_entry(
        cash_entry(entry_id="cash-b", component="BONUS", source_id="promo-1", amount_cny=Decimal("100.00"))
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
    repo.append_cash_entry(
        cash_entry(entry_id="cash-a", component="BONUS", source_id="promo-1", amount_cny=Decimal("100.00"))
    )
    with pytest.raises(IdempotencyConflict):
        repo.append_cash_entry(
            cash_entry(entry_id="cash-b", component="BONUS", source_id="promo-1", amount_cny=Decimal("200.00"))
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
    projection = repo.rebuild_account_projection("demo", as_of_date=TRADE_DATE)
    assert projection.positions["600000"].quantity == 1000
    assert projection.cash_cny == Decimal("989995.00")
    assert_account_invariants(repo.connection, "demo", as_of_date=TRADE_DATE)


def test_apply_execution_batch_is_idempotent(repo):
    seed_execution_orders(repo)
    lease = repo.acquire_account_lease("demo", owner_id="executor")
    repo.apply_execution_batch(EXECUTION_BATCH, fencing_token=lease.token)
    before = repo.count_rows()
    repo.apply_execution_batch(EXECUTION_BATCH, fencing_token=lease.token)
    after = repo.count_rows()
    assert after == before


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
