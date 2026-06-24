"""Paper DuckDB schema migration tests (Stage 6A Task 1)."""

from __future__ import annotations

import duckdb
import pytest

from tradingagents.paper.migrations import CURRENT_PAPER_SCHEMA_VERSION, apply_paper_migrations

PAPER_TABLES = (
    "frozen_screen_runs",
    "paper_accounts",
    "paper_account_locks",
    "paper_positions",
    "paper_lots",
    "paper_position_ledger",
    "paper_run_inputs",
    "rebalance_runs",
    "paper_orders",
    "paper_fills",
    "paper_cash_ledger",
    "paper_nav_snapshots",
    "paper_valuation_sources",
    "paper_corporate_action_applications",
    "paper_run_steps",
)


def test_fresh_paper_database_has_all_tables(tmp_path):
    db_path = tmp_path / "paper.duckdb"
    version = apply_paper_migrations(db_path)
    assert version == CURRENT_PAPER_SCHEMA_VERSION == 1
    connection = duckdb.connect(str(db_path))
    try:
        tables = {row[0] for row in connection.execute("SHOW TABLES").fetchall()}
        for name in PAPER_TABLES:
            assert name in tables
        assert "paper_schema_migrations" in tables
    finally:
        connection.close()


def test_paper_orders_status_check_allows_all_statuses(tmp_path):
    db_path = tmp_path / "paper.duckdb"
    apply_paper_migrations(db_path)
    connection = duckdb.connect(str(db_path))
    try:
        for status in (
            "PENDING",
            "FILLED",
            "PARTIALLY_FILLED",
            "REJECTED",
            "EXPIRED",
            "PARTIALLY_FILLED_EXPIRED",
            "CANCELLED",
        ):
            connection.execute(
                """
                INSERT INTO paper_orders (
                    order_id, rebalance_run_id, account_id, symbol, side,
                    planned_quantity, filled_quantity, remaining_quantity,
                    reference_price_cny, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                [f"ord-{status}", "run-1", "demo", "600000", "BUY", 100, 0, 100, 10.0, status],
            )
            connection.execute("DELETE FROM paper_orders WHERE order_id = ?", [f"ord-{status}"])
        with pytest.raises(duckdb.ConstraintException):
            connection.execute(
                """
                INSERT INTO paper_orders (
                    order_id, rebalance_run_id, account_id, symbol, side,
                    planned_quantity, filled_quantity, remaining_quantity,
                    reference_price_cny, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                ["ord-bad", "run-1", "demo", "600000", "BUY", 100, 0, 100, 10.0, "INVALID"],
            )
    finally:
        connection.close()


def test_paper_corporate_action_only_one_active_revision(tmp_path):
    db_path = tmp_path / "paper.duckdb"
    apply_paper_migrations(db_path)
    connection = duckdb.connect(str(db_path))
    try:
        connection.execute(
            """
            INSERT INTO paper_corporate_action_applications (
                account_id, corporate_action_id, revision, entitlement_quantity,
                entitlement_source_hash, status, is_active_revision, active_revision_slot
            ) VALUES (?, ?, ?, ?, ?, ?, TRUE, 0)
            """,
            ["demo", "ca-1", 1, 1000, "hash-a", "PENDING"],
        )
        with pytest.raises(duckdb.ConstraintException):
            connection.execute(
                """
                INSERT INTO paper_corporate_action_applications (
                    account_id, corporate_action_id, revision, entitlement_quantity,
                    entitlement_source_hash, status, is_active_revision, active_revision_slot
                ) VALUES (?, ?, ?, ?, ?, ?, TRUE, 0)
                """,
                ["demo", "ca-1", 2, 1000, "hash-b", "PENDING"],
            )
    finally:
        connection.close()


def test_failed_paper_migration_rolls_back(monkeypatch, tmp_path):
    db_path = tmp_path / "rollback.duckdb"
    apply_paper_migrations(db_path)
    connection = duckdb.connect(str(db_path))
    try:
        connection.execute("DELETE FROM paper_schema_migrations WHERE version = 1")
        connection.execute("DROP TABLE IF EXISTS paper_accounts")
    finally:
        connection.close()

    from tradingagents.paper import migrations as mig

    original_steps = mig._migration_steps()

    def broken_steps():
        steps = list(original_steps)
        for index, (version, sql) in enumerate(steps):
            if version == 1:
                steps[index] = (version, sql + "\nSELECT * FROM __broken_paper_table__;")
        return steps

    monkeypatch.setattr(mig, "_migration_steps", broken_steps)

    with pytest.raises(duckdb.CatalogException):
        apply_paper_migrations(db_path)

    connection = duckdb.connect(str(db_path))
    try:
        applied = {
            row[0]
            for row in connection.execute(
                "SELECT version FROM paper_schema_migrations"
            ).fetchall()
        }
        assert 1 not in applied
        tables = {row[0] for row in connection.execute("SHOW TABLES").fetchall()}
        assert "paper_accounts" not in tables
    finally:
        connection.close()
