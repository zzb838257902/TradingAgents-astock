"""Versioned DuckDB schema migrations for paper portfolio ledger."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb

SHANGHAI = ZoneInfo("Asia/Shanghai")
CURRENT_PAPER_SCHEMA_VERSION = 1


def _connect(path: Path) -> duckdb.DuckDBPyConnection:
    path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(path))


def _migration_steps() -> list[tuple[int, str]]:
    return [
        (1, """
            CREATE TABLE IF NOT EXISTS paper_schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL
            );

            CREATE TABLE IF NOT EXISTS frozen_screen_runs (
                screen_run_id VARCHAR NOT NULL PRIMARY KEY,
                screen_content_hash VARCHAR NOT NULL UNIQUE,
                status VARCHAR NOT NULL,
                signal_time TIMESTAMPTZ NOT NULL,
                target_portfolio_mode VARCHAR NOT NULL,
                target_weights_json VARCHAR NOT NULL,
                cash_weight DECIMAL(18, 10) NOT NULL,
                dataset_versions_json VARCHAR NOT NULL DEFAULT '{}',
                event_dataset_versions_json VARCHAR NOT NULL DEFAULT '{}',
                run_report_json VARCHAR NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS paper_accounts (
                account_id VARCHAR NOT NULL PRIMARY KEY,
                name VARCHAR NOT NULL,
                base_currency VARCHAR NOT NULL DEFAULT 'CNY',
                initial_cash_cny DECIMAL(20, 4) NOT NULL,
                status VARCHAR NOT NULL DEFAULT 'ACTIVE',
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS paper_account_locks (
                account_id VARCHAR NOT NULL PRIMARY KEY,
                current_fencing_token BIGINT NOT NULL DEFAULT 0,
                owner_id VARCHAR,
                owner_pid INTEGER,
                acquired_at TIMESTAMPTZ,
                lease_until TIMESTAMPTZ,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS paper_positions (
                account_id VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL,
                quantity BIGINT NOT NULL,
                available_quantity BIGINT NOT NULL,
                average_cost_cny DECIMAL(20, 6) NOT NULL,
                last_price_cny DECIMAL(20, 6),
                market_value_cny DECIMAL(20, 4) NOT NULL DEFAULT 0,
                realized_pnl_cny DECIMAL(20, 4) NOT NULL DEFAULT 0,
                unrealized_pnl_cny DECIMAL(20, 4) NOT NULL DEFAULT 0,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                version BIGINT NOT NULL DEFAULT 0,
                PRIMARY KEY(account_id, symbol)
            );

            CREATE TABLE IF NOT EXISTS paper_lots (
                lot_id VARCHAR NOT NULL PRIMARY KEY,
                account_id VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL,
                acquired_date DATE NOT NULL,
                source_type VARCHAR NOT NULL,
                source_id VARCHAR NOT NULL,
                original_quantity BIGINT NOT NULL,
                remaining_quantity BIGINT NOT NULL,
                original_cost_cny DECIMAL(20, 4) NOT NULL,
                remaining_cost_cny DECIMAL(20, 4) NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                closed_at TIMESTAMPTZ
            );

            CREATE TABLE IF NOT EXISTS paper_position_ledger (
                position_entry_id VARCHAR NOT NULL PRIMARY KEY,
                account_id VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL,
                quantity_delta BIGINT NOT NULL,
                cost_delta_cny DECIMAL(20, 4) NOT NULL,
                effective_date DATE NOT NULL,
                source_type VARCHAR NOT NULL,
                source_id VARCHAR NOT NULL,
                component VARCHAR NOT NULL,
                business_key VARCHAR NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(account_id, source_type, source_id, component)
            );

            CREATE TABLE IF NOT EXISTS paper_run_inputs (
                run_id VARCHAR NOT NULL,
                input_type VARCHAR NOT NULL,
                scope_key VARCHAR NOT NULL,
                row_content_hash VARCHAR NOT NULL,
                row_json VARCHAR NOT NULL,
                source_dataset_version_id VARCHAR,
                source_available_at TIMESTAMPTZ,
                captured_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(run_id, input_type, scope_key)
            );

            CREATE TABLE IF NOT EXISTS rebalance_runs (
                rebalance_run_id VARCHAR NOT NULL PRIMARY KEY,
                account_id VARCHAR NOT NULL,
                screen_run_id VARCHAR NOT NULL,
                screen_content_hash VARCHAR NOT NULL,
                target_hash VARCHAR NOT NULL,
                signal_date DATE NOT NULL,
                signal_time TIMESTAMPTZ NOT NULL,
                execution_date DATE NOT NULL,
                universe_hash VARCHAR NOT NULL,
                config_hash VARCHAR NOT NULL,
                strategy_version VARCHAR NOT NULL,
                target_weights_json VARCHAR NOT NULL,
                logical_run_key VARCHAR NOT NULL,
                revision INTEGER NOT NULL,
                is_active_revision BOOLEAN NOT NULL DEFAULT TRUE,
                active_revision_slot INTEGER NOT NULL,
                status VARCHAR NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMPTZ,
                UNIQUE(logical_run_key, revision),
                UNIQUE(logical_run_key, active_revision_slot)
            );

            CREATE TABLE IF NOT EXISTS paper_orders (
                order_id VARCHAR NOT NULL PRIMARY KEY,
                rebalance_run_id VARCHAR NOT NULL,
                account_id VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL,
                side VARCHAR NOT NULL,
                planned_quantity BIGINT NOT NULL,
                filled_quantity BIGINT NOT NULL DEFAULT 0,
                remaining_quantity BIGINT NOT NULL,
                reference_price_cny DECIMAL(20, 6) NOT NULL,
                limit_price_cny DECIMAL(20, 6),
                status VARCHAR NOT NULL DEFAULT 'PENDING',
                rejection_code VARCHAR,
                rejection_detail VARCHAR,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(rebalance_run_id, symbol, side),
                CHECK(status IN (
                    'PENDING', 'FILLED', 'PARTIALLY_FILLED', 'REJECTED',
                    'EXPIRED', 'PARTIALLY_FILLED_EXPIRED', 'CANCELLED'
                ))
            );

            CREATE TABLE IF NOT EXISTS paper_fills (
                fill_id VARCHAR NOT NULL PRIMARY KEY,
                fill_sequence INTEGER NOT NULL DEFAULT 1,
                order_id VARCHAR NOT NULL,
                account_id VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL,
                execution_date DATE NOT NULL,
                execution_time TIMESTAMPTZ NOT NULL,
                quantity BIGINT NOT NULL,
                price_cny DECIMAL(20, 6) NOT NULL,
                commission_cny DECIMAL(20, 4) NOT NULL DEFAULT 0,
                stamp_tax_cny DECIMAL(20, 4) NOT NULL DEFAULT 0,
                other_fee_cny DECIMAL(20, 4) NOT NULL DEFAULT 0,
                source_snapshot_key VARCHAR,
                source_snapshot_version_id VARCHAR,
                UNIQUE(order_id, execution_date, fill_sequence)
            );

            CREATE TABLE IF NOT EXISTS paper_cash_ledger (
                cash_entry_id VARCHAR NOT NULL PRIMARY KEY,
                account_id VARCHAR NOT NULL,
                entry_type VARCHAR NOT NULL,
                amount_cny DECIMAL(20, 4) NOT NULL,
                source_type VARCHAR NOT NULL,
                source_id VARCHAR NOT NULL,
                component VARCHAR NOT NULL,
                occurred_at TIMESTAMPTZ NOT NULL,
                balance_after_cny DECIMAL(20, 4),
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(account_id, source_type, source_id, component)
            );

            CREATE TABLE IF NOT EXISTS paper_nav_snapshots (
                account_id VARCHAR NOT NULL,
                valuation_date DATE NOT NULL,
                cash_cny DECIMAL(20, 4) NOT NULL,
                positions_value_cny DECIMAL(20, 4) NOT NULL,
                total_equity_cny DECIMAL(20, 4) NOT NULL,
                daily_return DECIMAL(18, 10),
                cumulative_return DECIMAL(18, 10),
                drawdown DECIMAL(18, 10),
                valuation_manifest_hash VARCHAR,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(account_id, valuation_date)
            );

            CREATE TABLE IF NOT EXISTS paper_valuation_sources (
                account_id VARCHAR NOT NULL,
                valuation_date DATE NOT NULL,
                symbol VARCHAR NOT NULL,
                quantity BIGINT NOT NULL,
                price_cny DECIMAL(20, 6) NOT NULL,
                price_status VARCHAR NOT NULL,
                source_row_key VARCHAR NOT NULL,
                dataset_version_id VARCHAR,
                row_content_hash VARCHAR NOT NULL,
                available_at TIMESTAMPTZ NOT NULL,
                PRIMARY KEY(account_id, valuation_date, symbol)
            );

            CREATE TABLE IF NOT EXISTS paper_corporate_action_applications (
                account_id VARCHAR NOT NULL,
                corporate_action_id VARCHAR NOT NULL,
                revision INTEGER NOT NULL,
                entitlement_quantity BIGINT NOT NULL,
                entitlement_source_hash VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                position_entry_id VARCHAR,
                cash_entry_id VARCHAR,
                applied_at TIMESTAMPTZ,
                is_active_revision BOOLEAN NOT NULL DEFAULT TRUE,
                active_revision_slot INTEGER NOT NULL,
                PRIMARY KEY(account_id, corporate_action_id, revision),
                UNIQUE(account_id, corporate_action_id, active_revision_slot)
            );

            CREATE TABLE IF NOT EXISTS paper_run_steps (
                run_id VARCHAR NOT NULL,
                step_name VARCHAR NOT NULL,
                status VARCHAR NOT NULL DEFAULT 'pending',
                input_hash VARCHAR,
                output_json VARCHAR,
                error_json VARCHAR,
                started_at TIMESTAMPTZ,
                finished_at TIMESTAMPTZ,
                PRIMARY KEY(run_id, step_name)
            );
        """),
    ]


def apply_paper_migrations(path: Path) -> int:
    connection = _connect(path)
    try:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS paper_schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL
            );
        """)
        applied = {
            row[0]
            for row in connection.execute(
                "SELECT version FROM paper_schema_migrations"
            ).fetchall()
        }
        now = datetime.now(tz=SHANGHAI)
        for version, sql in _migration_steps():
            if version in applied:
                continue
            connection.execute("BEGIN")
            try:
                connection.execute(sql)
                connection.execute(
                    "INSERT INTO paper_schema_migrations VALUES (?, ?)",
                    [version, now],
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return CURRENT_PAPER_SCHEMA_VERSION
    finally:
        connection.close()
