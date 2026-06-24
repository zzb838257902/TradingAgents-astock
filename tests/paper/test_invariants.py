"""Account invariant tests (Stage 6A Task 2)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from tradingagents.paper.invariants import InvariantViolation, assert_account_invariants
from tests.paper.conftest import (
    TRADE_DATE,
    append_position_with_lease,
    position_entry,
    rebuild_projection_with_lease,
    seed_execution_orders,
)


def test_assert_account_invariants_passes_for_seeded_account(repo):
    repo.create_account("demo", Decimal("1000000.00"))
    assert_account_invariants(repo.connection, "demo")


def test_assert_account_invariants_passes_after_projection_seed(repo):
    repo.create_account("demo", Decimal("1000000.00"))
    append_position_with_lease(repo, position_entry())
    rebuild_projection_with_lease(repo, "demo")
    assert_account_invariants(repo.connection, "demo", as_of_date=TRADE_DATE)


def test_assert_account_invariants_passes_after_execution(repo):
    seed_execution_orders(repo)
    lease = repo.acquire_account_lease("demo", owner_id="executor")
    from tests.paper.conftest import EXECUTION_BATCH

    repo.apply_execution_batch(EXECUTION_BATCH, fencing_token=lease.token)
    assert_account_invariants(repo.connection, "demo", as_of_date=TRADE_DATE)


def test_negative_cash_raises(repo):
    repo.create_account("demo", Decimal("1000000.00"))
    repo.connection.execute(
        """
        INSERT INTO paper_cash_ledger (
            cash_entry_id, account_id, entry_type, amount_cny,
            source_type, source_id, component, occurred_at, created_at
        ) VALUES (?, ?, 'BUY', ?, 'TEST', 't1', 'NOTIONAL', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """,
        ["cash-negative", "demo", Decimal("-2000000.00")],
    )
    with pytest.raises(InvariantViolation, match="negative cash"):
        assert_account_invariants(repo.connection, "demo")


def test_lot_ledger_mismatch_raises(repo):
    repo.create_account("demo", Decimal("1000000.00"))
    append_position_with_lease(repo, position_entry())
    repo.connection.execute(
        "UPDATE paper_lots SET remaining_quantity = 0 WHERE account_id = ?",
        ["demo"],
    )
    with pytest.raises(InvariantViolation, match="lot quantity"):
        assert_account_invariants(repo.connection, "demo", as_of_date=TRADE_DATE)


def test_nav_equation_checked_when_present(repo):
    repo.create_account("demo", Decimal("1000000.00"))
    repo.connection.execute(
        """
        INSERT INTO paper_nav_snapshots (
            account_id, valuation_date, cash_cny, positions_value_cny,
            total_equity_cny, created_at
        ) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        ["demo", date(2026, 6, 23), Decimal("1000000.00"), Decimal("0.00"), Decimal("999999.00")],
    )
    with pytest.raises(InvariantViolation, match="NAV invariant"):
        assert_account_invariants(repo.connection, "demo")
