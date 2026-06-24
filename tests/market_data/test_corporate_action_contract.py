"""Corporate action contract extension tests (Stage 6A Task 1)."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import duckdb
import pytest
from pydantic import ValidationError

from tradingagents.market_data.contracts import CorporateActionRecord
from tradingagents.market_data.migrations import apply_migrations

SHANGHAI = ZoneInfo("Asia/Shanghai")


def test_corporate_action_record_allows_missing_record_and_pay_dates():
    row = CorporateActionRecord(
        corporate_action_id="ca-600000-20260625-cash_div-free_astock",
        symbol="600000",
        ex_date=date(2026, 6, 25),
        action_type="cash_div",
        cash_div=0.12,
        available_at=datetime(2026, 6, 20, 15, 0, tzinfo=SHANGHAI),
        source="free_astock",
        announcement_at=datetime(2026, 6, 18, 18, 0, tzinfo=SHANGHAI),
        record_date=None,
        pay_date=None,
        source_version="v0",
        supersedes_action_id=None,
    )
    assert row.record_date is None
    assert row.pay_date is None
    assert row.supersedes_action_id is None


def test_corporate_action_record_accepts_full_dates():
    row = CorporateActionRecord(
        corporate_action_id="ca-full",
        symbol="600000",
        ex_date=date(2026, 6, 25),
        action_type="cash_div",
        cash_div=0.12,
        available_at=datetime(2026, 6, 20, 15, 0, tzinfo=SHANGHAI),
        source="free_astock",
        announcement_at=datetime(2026, 6, 18, 18, 0, tzinfo=SHANGHAI),
        record_date=date(2026, 6, 24),
        pay_date=date(2026, 6, 27),
        source_version="v1",
        supersedes_action_id="ca-old",
    )
    assert row.record_date == date(2026, 6, 24)
    assert row.pay_date == date(2026, 6, 27)


def test_corporate_action_record_requires_corporate_action_id():
    with pytest.raises(ValidationError):
        CorporateActionRecord(
            symbol="600000",
            ex_date=date(2026, 6, 25),
            action_type="cash_div",
            available_at=datetime(2026, 6, 20, 15, 0, tzinfo=SHANGHAI),
            source="free_astock",
        )


def test_v12_migration_adds_corporate_action_columns(tmp_path):
    db_path = tmp_path / "market.duckdb"
    apply_migrations(db_path)
    connection = duckdb.connect(str(db_path))
    try:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info('corporate_actions')").fetchall()
        }
        for name in (
            "corporate_action_id",
            "announcement_at",
            "record_date",
            "pay_date",
            "source_version",
            "supersedes_action_id",
        ):
            assert name in columns
        staging_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info('staging_corporate_actions')"
            ).fetchall()
        }
        for name in (
            "corporate_action_id",
            "announcement_at",
            "record_date",
            "pay_date",
            "source_version",
            "supersedes_action_id",
        ):
            assert name in staging_columns
    finally:
        connection.close()
