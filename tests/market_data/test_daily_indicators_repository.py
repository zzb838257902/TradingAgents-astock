"""DailyIndicator schema and repository table tests (remediation Task 1)."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import duckdb
import pytest

from tradingagents.market_data.contracts import DailyIndicator
from tradingagents.market_data.migrations import CURRENT_SCHEMA_VERSION, apply_migrations

SHANGHAI = ZoneInfo("Asia/Shanghai")


def test_daily_indicator_uses_canonical_cny_units():
    row = DailyIndicator(
        symbol="600000",
        trade_date=date(2026, 6, 19),
        pe_ttm=6.5,
        pb=0.7,
        turnover_pct=0.4,
        total_market_cap_cny=320_000_000_000,
        float_market_cap_cny=290_000_000_000,
        available_at=datetime(2026, 6, 19, 15, 30, tzinfo=SHANGHAI),
        source="fixture",
    )
    assert row.total_market_cap_cny == 320_000_000_000
    assert row.float_market_cap_cny == 290_000_000_000


def test_daily_indicator_rejects_non_canonical_unit_fields():
    with pytest.raises(Exception):
        DailyIndicator(
            symbol="600000",
            trade_date=date(2026, 6, 19),
            mcap_yi=32.0,
            total_market_cap_cny=320_000_000_000,
            float_market_cap_cny=290_000_000_000,
            available_at=datetime(2026, 6, 19, 15, 30, tzinfo=SHANGHAI),
            source="fixture",
        )


def test_fresh_database_has_daily_indicator_tables(tmp_path):
    db_path = tmp_path / "market.duckdb"
    apply_migrations(db_path)
    assert CURRENT_SCHEMA_VERSION == 11
    connection = duckdb.connect(str(db_path))
    try:
        tables = {row[0] for row in connection.execute("SHOW TABLES").fetchall()}
        assert "daily_indicators" in tables
        assert "staging_daily_indicators" in tables
    finally:
        connection.close()


def test_upgrade_from_v10_adds_daily_indicator_tables(tmp_path):
    db_path = tmp_path / "v10.duckdb"
    apply_migrations(db_path)
    connection = duckdb.connect(str(db_path))
    try:
        connection.execute("DELETE FROM schema_migrations WHERE version = 11")
        connection.execute("DROP TABLE IF EXISTS daily_indicators")
        connection.execute("DROP TABLE IF EXISTS staging_daily_indicators")
    finally:
        connection.close()

    version = apply_migrations(db_path)
    assert version == 11
    connection = duckdb.connect(str(db_path))
    try:
        tables = {row[0] for row in connection.execute("SHOW TABLES").fetchall()}
        assert "daily_indicators" in tables
        assert "staging_daily_indicators" in tables
        applied = {
            row[0]
            for row in connection.execute("SELECT version FROM schema_migrations").fetchall()
        }
        assert 11 in applied
    finally:
        connection.close()


def test_daily_indicators_primary_key_is_symbol_trade_date_source(tmp_path):
    db_path = tmp_path / "market.duckdb"
    apply_migrations(db_path)
    connection = duckdb.connect(str(db_path))
    try:
        connection.execute(
            """
            INSERT INTO daily_indicators (
                symbol, trade_date, pe_ttm, pb, turnover_pct,
                total_market_cap_cny, float_market_cap_cny,
                available_at, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                "600000",
                date(2026, 6, 19),
                6.5,
                0.7,
                0.4,
                320_000_000_000.0,
                290_000_000_000.0,
                datetime(2026, 6, 19, 15, 30, tzinfo=SHANGHAI),
                "tencent",
            ],
        )
        with pytest.raises(duckdb.ConstraintException):
            connection.execute(
                """
                INSERT INTO daily_indicators (
                    symbol, trade_date, pe_ttm, pb, turnover_pct,
                    total_market_cap_cny, float_market_cap_cny,
                    available_at, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    "600000",
                    date(2026, 6, 19),
                    7.0,
                    0.8,
                    0.5,
                    330_000_000_000.0,
                    300_000_000_000.0,
                    datetime(2026, 6, 19, 16, 0, tzinfo=SHANGHAI),
                    "tencent",
                ],
            )
    finally:
        connection.close()


def test_staging_daily_indicators_primary_key_includes_run_id(tmp_path):
    db_path = tmp_path / "market.duckdb"
    apply_migrations(db_path)
    connection = duckdb.connect(str(db_path))
    try:
        row = [
            "run-a",
            "600000",
            date(2026, 6, 19),
            6.5,
            0.7,
            0.4,
            320_000_000_000.0,
            290_000_000_000.0,
            datetime(2026, 6, 19, 15, 30, tzinfo=SHANGHAI),
            "tencent",
        ]
        connection.execute(
            """
            INSERT INTO staging_daily_indicators (
                run_id, symbol, trade_date, pe_ttm, pb, turnover_pct,
                total_market_cap_cny, float_market_cap_cny,
                available_at, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            row,
        )
        connection.execute(
            """
            INSERT INTO staging_daily_indicators (
                run_id, symbol, trade_date, pe_ttm, pb, turnover_pct,
                total_market_cap_cny, float_market_cap_cny,
                available_at, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ["run-b", *row[1:]],
        )
        with pytest.raises(duckdb.ConstraintException):
            connection.execute(
                """
                INSERT INTO staging_daily_indicators (
                    run_id, symbol, trade_date, pe_ttm, pb, turnover_pct,
                    total_market_cap_cny, float_market_cap_cny,
                    available_at, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                row,
            )
    finally:
        connection.close()


def test_failed_migration_does_not_advance_schema_version(tmp_path, monkeypatch):
    db_path = tmp_path / "rollback.duckdb"
    apply_migrations(db_path)
    connection = duckdb.connect(str(db_path))
    try:
        connection.execute("DELETE FROM schema_migrations WHERE version = 11")
        connection.execute("DROP TABLE IF EXISTS daily_indicators")
        connection.execute("DROP TABLE IF EXISTS staging_daily_indicators")
    finally:
        connection.close()

    from tradingagents.market_data import migrations as mig

    original_steps = mig._migration_steps()

    def broken_steps():
        steps = list(original_steps)
        for index, (version, sql) in enumerate(steps):
            if version == 11:
                steps[index] = (version, sql + "\nSELECT * FROM __broken_migration_table__;")
        return steps

    monkeypatch.setattr(mig, "_migration_steps", broken_steps)

    with pytest.raises(duckdb.CatalogException):
        apply_migrations(db_path)

    connection = duckdb.connect(str(db_path))
    try:
        applied = {
            row[0]
            for row in connection.execute("SELECT version FROM schema_migrations").fetchall()
        }
        assert 11 not in applied
        tables = {row[0] for row in connection.execute("SHOW TABLES").fetchall()}
        assert "daily_indicators" not in tables
        assert "staging_daily_indicators" not in tables
    finally:
        connection.close()
