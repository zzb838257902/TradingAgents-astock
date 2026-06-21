"""Versioned DuckDB schema migrations for market data."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb

SHANGHAI = ZoneInfo("Asia/Shanghai")
CURRENT_SCHEMA_VERSION = 11


def _connect(path: Path) -> duckdb.DuckDBPyConnection:
    path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(path))


def _migration_steps() -> list[tuple[int, str]]:
    return [
        (1, """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL
            );
            CREATE TABLE IF NOT EXISTS securities (
                symbol VARCHAR NOT NULL,
                name VARCHAR NOT NULL,
                board VARCHAR NOT NULL,
                valid_from DATE NOT NULL,
                valid_to DATE,
                list_date DATE NOT NULL,
                delist_date DATE,
                status VARCHAR NOT NULL,
                st_flag BOOLEAN NOT NULL,
                available_at TIMESTAMPTZ NOT NULL,
                source VARCHAR NOT NULL,
                PRIMARY KEY(symbol, valid_from)
            );
            CREATE TABLE IF NOT EXISTS daily_bars (
                symbol VARCHAR NOT NULL,
                trade_date DATE NOT NULL,
                open DOUBLE NOT NULL,
                high DOUBLE NOT NULL,
                low DOUBLE NOT NULL,
                close DOUBLE NOT NULL,
                volume DOUBLE NOT NULL,
                amount DOUBLE NOT NULL,
                available_at TIMESTAMPTZ NOT NULL,
                source VARCHAR NOT NULL,
                PRIMARY KEY(symbol, trade_date, source)
            );
            CREATE TABLE IF NOT EXISTS financials (
                symbol VARCHAR NOT NULL,
                report_period VARCHAR NOT NULL,
                roe DOUBLE NOT NULL,
                operating_cashflow DOUBLE NOT NULL,
                net_profit DOUBLE NOT NULL,
                debt_ratio DOUBLE NOT NULL,
                available_at TIMESTAMPTZ NOT NULL,
                source VARCHAR NOT NULL,
                PRIMARY KEY(symbol, report_period, source)
            );
        """),
        (2, """
            ALTER TABLE securities ADD COLUMN IF NOT EXISTS exchange VARCHAR;
            ALTER TABLE securities ADD COLUMN IF NOT EXISTS ingested_at TIMESTAMPTZ;
            ALTER TABLE securities ADD COLUMN IF NOT EXISTS dataset_version_id VARCHAR;
            ALTER TABLE daily_bars ADD COLUMN IF NOT EXISTS prev_close DOUBLE;
            ALTER TABLE daily_bars ADD COLUMN IF NOT EXISTS ingested_at TIMESTAMPTZ;
            ALTER TABLE daily_bars ADD COLUMN IF NOT EXISTS dataset_version_id VARCHAR;
            ALTER TABLE financials ADD COLUMN IF NOT EXISTS announcement_date DATE;
            ALTER TABLE financials ADD COLUMN IF NOT EXISTS actual_announcement_time TIMESTAMPTZ;
            ALTER TABLE financials ADD COLUMN IF NOT EXISTS update_flag VARCHAR;
            ALTER TABLE financials ADD COLUMN IF NOT EXISTS source_version VARCHAR;
            ALTER TABLE financials ADD COLUMN IF NOT EXISTS record_type VARCHAR;
            ALTER TABLE financials ADD COLUMN IF NOT EXISTS ingested_at TIMESTAMPTZ;
            ALTER TABLE financials ADD COLUMN IF NOT EXISTS dataset_version_id VARCHAR;
        """),
        (3, """
            CREATE TABLE IF NOT EXISTS trade_calendar (
                exchange VARCHAR NOT NULL,
                trade_date DATE NOT NULL,
                is_open BOOLEAN NOT NULL,
                available_at TIMESTAMPTZ NOT NULL,
                source VARCHAR NOT NULL,
                ingested_at TIMESTAMPTZ,
                dataset_version_id VARCHAR,
                PRIMARY KEY(exchange, trade_date, source)
            );
            CREATE TABLE IF NOT EXISTS dataset_versions (
                version_id VARCHAR PRIMARY KEY,
                dataset VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                published_at TIMESTAMPTZ,
                ingestion_run_id VARCHAR,
                content_hash VARCHAR
            );
            CREATE TABLE IF NOT EXISTS ingestion_runs (
                run_id VARCHAR PRIMARY KEY,
                dataset VARCHAR NOT NULL,
                params_json VARCHAR NOT NULL,
                cursor_json VARCHAR,
                status VARCHAR NOT NULL,
                started_at TIMESTAMPTZ NOT NULL,
                finished_at TIMESTAMPTZ,
                error_summary VARCHAR
            );
            CREATE TABLE IF NOT EXISTS raw_snapshots (
                snapshot_id VARCHAR PRIMARY KEY,
                source VARCHAR NOT NULL,
                endpoint VARCHAR NOT NULL,
                request_hash VARCHAR NOT NULL,
                response_hash VARCHAR NOT NULL,
                file_path VARCHAR NOT NULL,
                ingested_at TIMESTAMPTZ NOT NULL,
                api_version VARCHAR
            );
            CREATE TABLE IF NOT EXISTS data_quality_events (
                event_id VARCHAR PRIMARY KEY,
                dataset VARCHAR NOT NULL,
                version_id VARCHAR,
                rule VARCHAR NOT NULL,
                severity VARCHAR NOT NULL,
                numerator DOUBLE,
                denominator DOUBLE,
                detail_json VARCHAR,
                created_at TIMESTAMPTZ NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sync_checkpoints (
                dataset VARCHAR NOT NULL,
                partition_key VARCHAR NOT NULL,
                cursor_json VARCHAR NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL,
                PRIMARY KEY(dataset, partition_key)
            );
            CREATE TABLE IF NOT EXISTS staging_daily_bars (
                run_id VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL,
                trade_date DATE NOT NULL,
                open DOUBLE NOT NULL,
                high DOUBLE NOT NULL,
                low DOUBLE NOT NULL,
                close DOUBLE NOT NULL,
                volume DOUBLE NOT NULL,
                amount DOUBLE NOT NULL,
                prev_close DOUBLE,
                available_at TIMESTAMPTZ NOT NULL,
                source VARCHAR NOT NULL,
                ingested_at TIMESTAMPTZ,
                PRIMARY KEY(run_id, symbol, trade_date, source)
            );
        """),
        (4, """
            CREATE TABLE IF NOT EXISTS adjustment_factors (
                symbol VARCHAR NOT NULL,
                trade_date DATE NOT NULL,
                factor DOUBLE NOT NULL,
                available_at TIMESTAMPTZ NOT NULL,
                source VARCHAR NOT NULL,
                ingested_at TIMESTAMPTZ,
                dataset_version_id VARCHAR,
                PRIMARY KEY(symbol, trade_date, source)
            );
            CREATE TABLE IF NOT EXISTS corporate_actions (
                symbol VARCHAR NOT NULL,
                ex_date DATE NOT NULL,
                action_type VARCHAR NOT NULL,
                cash_div DOUBLE,
                stock_div DOUBLE,
                split_ratio DOUBLE,
                rights_ratio DOUBLE,
                available_at TIMESTAMPTZ NOT NULL,
                source VARCHAR NOT NULL,
                ingested_at TIMESTAMPTZ,
                dataset_version_id VARCHAR,
                PRIMARY KEY(symbol, ex_date, action_type, source)
            );
            CREATE TABLE IF NOT EXISTS security_status_history (
                symbol VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                effective_from DATE NOT NULL,
                effective_to DATE,
                available_at TIMESTAMPTZ NOT NULL,
                source VARCHAR NOT NULL,
                ingested_at TIMESTAMPTZ,
                dataset_version_id VARCHAR,
                PRIMARY KEY(symbol, status, effective_from, source)
            );
            CREATE TABLE IF NOT EXISTS name_history (
                symbol VARCHAR NOT NULL,
                name VARCHAR NOT NULL,
                effective_from DATE NOT NULL,
                effective_to DATE,
                available_at TIMESTAMPTZ NOT NULL,
                source VARCHAR NOT NULL,
                ingested_at TIMESTAMPTZ,
                dataset_version_id VARCHAR,
                PRIMARY KEY(symbol, effective_from, source)
            );
            CREATE TABLE IF NOT EXISTS suspension_events (
                symbol VARCHAR NOT NULL,
                start_date DATE NOT NULL,
                end_date DATE,
                reason VARCHAR,
                available_at TIMESTAMPTZ NOT NULL,
                source VARCHAR NOT NULL,
                ingested_at TIMESTAMPTZ,
                dataset_version_id VARCHAR,
                PRIMARY KEY(symbol, start_date, source)
            );
            CREATE TABLE IF NOT EXISTS price_limits (
                symbol VARCHAR NOT NULL,
                trade_date DATE NOT NULL,
                limit_up DOUBLE NOT NULL,
                limit_down DOUBLE NOT NULL,
                available_at TIMESTAMPTZ NOT NULL,
                source VARCHAR NOT NULL,
                ingested_at TIMESTAMPTZ,
                dataset_version_id VARCHAR,
                PRIMARY KEY(symbol, trade_date, source)
            );
            CREATE TABLE IF NOT EXISTS board_definitions (
                board_type VARCHAR NOT NULL,
                board_code VARCHAR NOT NULL,
                name VARCHAR NOT NULL,
                pit_level VARCHAR NOT NULL,
                source VARCHAR NOT NULL,
                available_at TIMESTAMPTZ NOT NULL,
                ingested_at TIMESTAMPTZ,
                dataset_version_id VARCHAR,
                PRIMARY KEY(board_type, board_code, source)
            );
            CREATE TABLE IF NOT EXISTS board_memberships (
                board_type VARCHAR NOT NULL,
                board_code VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL,
                membership_mode VARCHAR NOT NULL,
                effective_from DATE,
                effective_to DATE,
                snapshot_date DATE,
                available_at TIMESTAMPTZ NOT NULL,
                source VARCHAR NOT NULL,
                ingested_at TIMESTAMPTZ,
                dataset_version_id VARCHAR,
                PRIMARY KEY(board_type, board_code, symbol, effective_from, source)
            );
        """),
        (5, """
            CREATE TABLE IF NOT EXISTS staging_securities (
                run_id VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL,
                name VARCHAR NOT NULL,
                board VARCHAR NOT NULL,
                valid_from DATE NOT NULL,
                valid_to DATE,
                list_date DATE NOT NULL,
                delist_date DATE,
                status VARCHAR NOT NULL,
                st_flag BOOLEAN NOT NULL,
                available_at TIMESTAMPTZ NOT NULL,
                source VARCHAR NOT NULL,
                ingested_at TIMESTAMPTZ,
                PRIMARY KEY(run_id, symbol, valid_from)
            );
            CREATE TABLE IF NOT EXISTS staging_trade_calendar (
                run_id VARCHAR NOT NULL,
                exchange VARCHAR NOT NULL,
                trade_date DATE NOT NULL,
                is_open BOOLEAN NOT NULL,
                available_at TIMESTAMPTZ NOT NULL,
                source VARCHAR NOT NULL,
                ingested_at TIMESTAMPTZ,
                PRIMARY KEY(run_id, exchange, trade_date, source)
            );
            CREATE TABLE IF NOT EXISTS sync_state (
                state_key VARCHAR PRIMARY KEY,
                value_json VARCHAR NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL
            );
        """),
        (6, """
            CREATE TABLE financials_v6 (
                symbol VARCHAR NOT NULL,
                report_period VARCHAR NOT NULL,
                roe DOUBLE NOT NULL,
                operating_cashflow DOUBLE NOT NULL,
                net_profit DOUBLE NOT NULL,
                debt_ratio DOUBLE NOT NULL,
                announcement_date DATE NOT NULL,
                actual_announcement_time TIMESTAMPTZ,
                available_at TIMESTAMPTZ NOT NULL,
                update_flag VARCHAR,
                source_version VARCHAR,
                record_type VARCHAR NOT NULL,
                source VARCHAR NOT NULL,
                ingested_at TIMESTAMPTZ,
                dataset_version_id VARCHAR,
                PRIMARY KEY(symbol, report_period, announcement_date, source, record_type)
            );
            INSERT INTO financials_v6 (
                symbol, report_period, roe, operating_cashflow, net_profit, debt_ratio,
                announcement_date, actual_announcement_time, available_at, update_flag,
                source_version, record_type, source, ingested_at, dataset_version_id
            )
            SELECT
                symbol,
                report_period,
                roe,
                operating_cashflow,
                net_profit,
                debt_ratio,
                COALESCE(announcement_date, CAST(available_at AS DATE)),
                actual_announcement_time,
                available_at,
                update_flag,
                source_version,
                COALESCE(record_type, 'indicator'),
                source,
                ingested_at,
                dataset_version_id
            FROM financials;
            DROP TABLE financials;
            ALTER TABLE financials_v6 RENAME TO financials;
        """),
        (7, """
            CREATE TABLE financials_v7 (
                symbol VARCHAR NOT NULL,
                report_period VARCHAR NOT NULL,
                roe DOUBLE NOT NULL,
                operating_cashflow DOUBLE NOT NULL,
                net_profit DOUBLE NOT NULL,
                debt_ratio DOUBLE NOT NULL,
                announcement_date DATE NOT NULL,
                actual_announcement_time TIMESTAMPTZ,
                available_at TIMESTAMPTZ NOT NULL,
                update_flag VARCHAR NOT NULL DEFAULT '',
                source_version VARCHAR,
                record_type VARCHAR NOT NULL,
                source VARCHAR NOT NULL,
                ingested_at TIMESTAMPTZ,
                dataset_version_id VARCHAR,
                PRIMARY KEY(
                    symbol, report_period, announcement_date,
                    source, record_type, update_flag
                )
            );
            INSERT INTO financials_v7 (
                symbol, report_period, roe, operating_cashflow, net_profit, debt_ratio,
                announcement_date, actual_announcement_time, available_at, update_flag,
                source_version, record_type, source, ingested_at, dataset_version_id
            )
            SELECT
                symbol,
                report_period,
                roe,
                operating_cashflow,
                net_profit,
                debt_ratio,
                announcement_date,
                actual_announcement_time,
                available_at,
                COALESCE(update_flag, ''),
                source_version,
                record_type,
                source,
                ingested_at,
                dataset_version_id
            FROM financials;
            DROP TABLE financials;
            ALTER TABLE financials_v7 RENAME TO financials;
        """),
        (8, """
            CREATE TABLE IF NOT EXISTS security_master_snapshots (
                snapshot_date DATE NOT NULL,
                symbol VARCHAR NOT NULL,
                name VARCHAR NOT NULL,
                board VARCHAR NOT NULL,
                list_date DATE NOT NULL,
                delist_date DATE,
                status VARCHAR NOT NULL,
                st_flag BOOLEAN NOT NULL,
                available_at TIMESTAMPTZ NOT NULL,
                source VARCHAR NOT NULL,
                ingested_at TIMESTAMPTZ NOT NULL,
                PRIMARY KEY(snapshot_date, symbol, source)
            );
        """),
        (9, """
            CREATE TABLE IF NOT EXISTS staging_financials (
                run_id VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL,
                report_period VARCHAR NOT NULL,
                roe DOUBLE NOT NULL,
                operating_cashflow DOUBLE NOT NULL,
                net_profit DOUBLE NOT NULL,
                debt_ratio DOUBLE NOT NULL,
                announcement_date DATE NOT NULL,
                actual_announcement_time TIMESTAMPTZ,
                available_at TIMESTAMPTZ NOT NULL,
                update_flag VARCHAR NOT NULL,
                source_version VARCHAR,
                record_type VARCHAR NOT NULL,
                source VARCHAR NOT NULL,
                ingested_at TIMESTAMPTZ,
                PRIMARY KEY(
                    run_id, symbol, report_period, announcement_date,
                    source, record_type, update_flag
                )
            );
            CREATE TABLE IF NOT EXISTS staging_adjustment_factors (
                run_id VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL,
                trade_date DATE NOT NULL,
                factor DOUBLE NOT NULL,
                available_at TIMESTAMPTZ NOT NULL,
                source VARCHAR NOT NULL,
                ingested_at TIMESTAMPTZ,
                PRIMARY KEY(run_id, symbol, trade_date, source)
            );
            CREATE TABLE IF NOT EXISTS staging_corporate_actions (
                run_id VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL,
                ex_date DATE NOT NULL,
                action_type VARCHAR NOT NULL,
                cash_div DOUBLE,
                stock_div DOUBLE,
                split_ratio DOUBLE,
                rights_ratio DOUBLE,
                available_at TIMESTAMPTZ NOT NULL,
                source VARCHAR NOT NULL,
                ingested_at TIMESTAMPTZ,
                PRIMARY KEY(run_id, symbol, ex_date, action_type, source)
            );
            CREATE TABLE IF NOT EXISTS staging_board_memberships (
                run_id VARCHAR NOT NULL,
                board_type VARCHAR NOT NULL,
                board_code VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL,
                membership_mode VARCHAR NOT NULL,
                effective_from DATE,
                effective_to DATE,
                snapshot_date DATE,
                available_at TIMESTAMPTZ NOT NULL,
                source VARCHAR NOT NULL,
                ingested_at TIMESTAMPTZ,
                PRIMARY KEY(run_id, board_type, board_code, symbol, effective_from, source)
            );
        """),
        (10, """
            CREATE TABLE IF NOT EXISTS market_events (
                event_id VARCHAR NOT NULL PRIMARY KEY,
                event_type VARCHAR NOT NULL,
                title VARCHAR NOT NULL,
                summary VARCHAR NOT NULL DEFAULT '',
                published_at TIMESTAMPTZ NOT NULL,
                available_at TIMESTAMPTZ,
                source VARCHAR NOT NULL,
                source_url VARCHAR NOT NULL DEFAULT '',
                source_record_id VARCHAR NOT NULL,
                source_version VARCHAR NOT NULL DEFAULT '',
                content_hash VARCHAR NOT NULL,
                pit_level VARCHAR NOT NULL,
                sentiment VARCHAR NOT NULL DEFAULT 'unknown',
                severity VARCHAR NOT NULL DEFAULT 'medium',
                announcement_date_source VARCHAR,
                raw_snapshot_id VARCHAR,
                dataset_version_id VARCHAR,
                ingestion_run_id VARCHAR,
                quality_status VARCHAR NOT NULL DEFAULT 'valid',
                supersedes_event_id VARCHAR,
                ingested_at TIMESTAMPTZ
            );
            CREATE TABLE IF NOT EXISTS staging_market_events (
                run_id VARCHAR NOT NULL,
                event_id VARCHAR NOT NULL,
                event_type VARCHAR NOT NULL,
                title VARCHAR NOT NULL,
                summary VARCHAR NOT NULL DEFAULT '',
                published_at TIMESTAMPTZ NOT NULL,
                available_at TIMESTAMPTZ,
                source VARCHAR NOT NULL,
                source_url VARCHAR NOT NULL DEFAULT '',
                source_record_id VARCHAR NOT NULL,
                source_version VARCHAR NOT NULL DEFAULT '',
                content_hash VARCHAR NOT NULL,
                pit_level VARCHAR NOT NULL,
                sentiment VARCHAR NOT NULL DEFAULT 'unknown',
                severity VARCHAR NOT NULL DEFAULT 'medium',
                announcement_date_source VARCHAR,
                raw_snapshot_id VARCHAR,
                ingestion_run_id VARCHAR,
                quality_status VARCHAR NOT NULL DEFAULT 'valid',
                supersedes_event_id VARCHAR,
                ingested_at TIMESTAMPTZ,
                PRIMARY KEY(run_id, event_id),
                UNIQUE(run_id, source, source_record_id, source_version)
            );
            CREATE TABLE IF NOT EXISTS event_symbol_links (
                event_id VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL,
                role VARCHAR NOT NULL DEFAULT 'primary',
                available_at TIMESTAMPTZ NOT NULL,
                source VARCHAR NOT NULL,
                dataset_version_id VARCHAR,
                PRIMARY KEY(event_id, symbol, role, source)
            );
            CREATE TABLE IF NOT EXISTS staging_event_symbol_links (
                run_id VARCHAR NOT NULL,
                event_id VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL,
                role VARCHAR NOT NULL DEFAULT 'primary',
                available_at TIMESTAMPTZ NOT NULL,
                source VARCHAR NOT NULL,
                PRIMARY KEY(run_id, event_id, symbol, role, source)
            );
            CREATE TABLE IF NOT EXISTS event_tags (
                event_id VARCHAR NOT NULL,
                tag_key VARCHAR NOT NULL,
                tag_value VARCHAR NOT NULL,
                dataset_version_id VARCHAR,
                PRIMARY KEY(event_id, tag_key)
            );
            CREATE TABLE IF NOT EXISTS staging_event_tags (
                run_id VARCHAR NOT NULL,
                event_id VARCHAR NOT NULL,
                tag_key VARCHAR NOT NULL,
                tag_value VARCHAR NOT NULL,
                PRIMARY KEY(run_id, event_id, tag_key)
            );
            CREATE TABLE IF NOT EXISTS board_aliases (
                board_type VARCHAR NOT NULL,
                board_code VARCHAR NOT NULL,
                alias VARCHAR NOT NULL,
                alias_normalized VARCHAR NOT NULL,
                source VARCHAR NOT NULL,
                PRIMARY KEY(board_type, alias_normalized, source)
            );
        """),
        (11, """
            CREATE TABLE IF NOT EXISTS daily_indicators (
                symbol VARCHAR NOT NULL,
                trade_date DATE NOT NULL,
                pe_ttm DOUBLE,
                pb DOUBLE,
                turnover_pct DOUBLE,
                total_market_cap_cny DOUBLE NOT NULL,
                float_market_cap_cny DOUBLE NOT NULL,
                available_at TIMESTAMPTZ NOT NULL,
                source VARCHAR NOT NULL,
                ingested_at TIMESTAMPTZ,
                dataset_version_id VARCHAR,
                PRIMARY KEY(symbol, trade_date, source)
            );
            CREATE TABLE IF NOT EXISTS staging_daily_indicators (
                run_id VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL,
                trade_date DATE NOT NULL,
                pe_ttm DOUBLE,
                pb DOUBLE,
                turnover_pct DOUBLE,
                total_market_cap_cny DOUBLE NOT NULL,
                float_market_cap_cny DOUBLE NOT NULL,
                available_at TIMESTAMPTZ NOT NULL,
                source VARCHAR NOT NULL,
                ingested_at TIMESTAMPTZ,
                PRIMARY KEY(run_id, symbol, trade_date, source)
            );
        """),
    ]


def apply_migrations(path: Path) -> int:
    connection = _connect(path)
    try:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL
            );
        """)
        applied = {
            row[0]
            for row in connection.execute("SELECT version FROM schema_migrations").fetchall()
        }
        now = datetime.now(tz=SHANGHAI)
        for version, sql in _migration_steps():
            if version in applied:
                continue
            connection.execute("BEGIN TRANSACTION")
            try:
                connection.execute(sql)
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    [version, now],
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return CURRENT_SCHEMA_VERSION
    finally:
        connection.close()
