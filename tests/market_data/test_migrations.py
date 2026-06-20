"""Repository migration and staging/publish tests for phase 4.2."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo


from tradingagents.market_data.contracts import SecurityRecord
from tradingagents.market_data.migrations import CURRENT_SCHEMA_VERSION, apply_migrations
from tradingagents.market_data.repository import MarketDataRepository

SHANGHAI = ZoneInfo("Asia/Shanghai")


def test_apply_migrations_is_idempotent(tmp_path):
    db_path = tmp_path / "market.duckdb"
    first = apply_migrations(db_path)
    second = apply_migrations(db_path)
    assert first == CURRENT_SCHEMA_VERSION
    assert second == CURRENT_SCHEMA_VERSION
    assert CURRENT_SCHEMA_VERSION == 9


def test_fresh_repository_has_phase4_tables(tmp_path):
    repo = MarketDataRepository(tmp_path / "market.duckdb")
    tables = {
        row[0]
        for row in repo.connection.execute("SHOW TABLES").fetchall()
    }
    expected = {
        "schema_migrations",
        "securities",
        "daily_bars",
        "financials",
        "trade_calendar",
        "dataset_versions",
        "ingestion_runs",
        "raw_snapshots",
        "data_quality_events",
        "sync_checkpoints",
        "staging_daily_bars",
        "staging_securities",
        "staging_trade_calendar",
        "sync_state",
        "adjustment_factors",
        "corporate_actions",
        "security_status_history",
        "name_history",
        "suspension_events",
        "price_limits",
        "board_definitions",
        "board_memberships",
        "security_master_snapshots",
        "staging_financials",
        "staging_adjustment_factors",
        "staging_corporate_actions",
        "staging_board_memberships",
    }
    assert expected.issubset(tables)


def test_legacy_fixture_upsert_still_works(tmp_path):
    repo = MarketDataRepository(tmp_path / "market.duckdb")
    record = SecurityRecord(
        symbol="600001",
        name="600001",
        board="main",
        valid_from=date(2020, 1, 1),
        valid_to=None,
        list_date=date(2020, 1, 1),
        delist_date=None,
        status="listed",
        st_flag=False,
        available_at=datetime(2020, 1, 1, 9, 0, tzinfo=SHANGHAI),
        source="fixture",
    )
    repo.upsert_security_records([record])
    rows = repo.get_effective_securities(
        date(2023, 1, 3),
        datetime(2023, 1, 3, 15, 30, tzinfo=SHANGHAI),
    )
    assert len(rows) == 1
    assert rows[0].symbol == "600001"


def test_staging_rows_are_invisible_until_published(tmp_path):
    repo = MarketDataRepository(tmp_path / "market.duckdb")
    run_id = repo.begin_ingestion_run("daily_bars", {"trade_date": "2026-01-02"})
    repo.upsert_staging_daily_bars(run_id, [{
        "symbol": "600001",
        "trade_date": date(2026, 1, 2),
        "open": 10.0,
        "high": 10.5,
        "low": 9.8,
        "close": 10.2,
        "volume": 1000.0,
        "amount": 10200.0,
        "prev_close": 10.0,
        "available_at": datetime(2026, 1, 2, 15, 30, tzinfo=SHANGHAI),
        "source": "tushare",
    }])
    available = datetime(2026, 1, 2, 16, 0, tzinfo=SHANGHAI)
    assert repo.get_daily_bars(["600001"], end=date(2026, 1, 2), available_before=available) == []

    version_id = repo.publish_dataset_version(run_id)
    rows = repo.get_daily_bars(["600001"], end=date(2026, 1, 2), available_before=available)
    assert len(rows) == 1
    assert rows[0]["close"] == 10.2
    published = repo.get_latest_published_version("daily_bars")
    assert published is not None
    assert published["version_id"] == version_id
    assert published["status"] == "PUBLISHED"


def test_failed_publish_does_not_replace_previous_published_version(tmp_path):
    repo = MarketDataRepository(tmp_path / "market.duckdb")
    first_run = repo.begin_ingestion_run("daily_bars", {"trade_date": "2026-01-02"})
    repo.upsert_staging_daily_bars(first_run, [{
        "symbol": "600001",
        "trade_date": date(2026, 1, 2),
        "open": 10.0,
        "high": 10.5,
        "low": 9.8,
        "close": 10.2,
        "volume": 1000.0,
        "amount": 10200.0,
        "prev_close": 10.0,
        "available_at": datetime(2026, 1, 2, 15, 30, tzinfo=SHANGHAI),
        "source": "tushare",
    }])
    first_version = repo.publish_dataset_version(first_run)

    second_run = repo.begin_ingestion_run("daily_bars", {"trade_date": "2026-01-03"})
    repo.upsert_staging_daily_bars(second_run, [{
        "symbol": "600001",
        "trade_date": date(2026, 1, 3),
        "open": 11.0,
        "high": 11.5,
        "low": 10.8,
        "close": 11.2,
        "volume": 1000.0,
        "amount": 11200.0,
        "prev_close": 10.2,
        "available_at": datetime(2026, 1, 3, 15, 30, tzinfo=SHANGHAI),
        "source": "tushare",
    }])
    repo.mark_ingestion_failed(second_run, "quality gate failed")

    latest = repo.get_latest_published_version("daily_bars")
    assert latest["version_id"] == first_version
    available = datetime(2026, 1, 3, 16, 0, tzinfo=SHANGHAI)
    rows = repo.get_daily_bars(["600001"], end=date(2026, 1, 3), available_before=available)
    assert [row["trade_date"] for row in rows] == [date(2026, 1, 2)]


def test_raw_snapshot_round_trip(tmp_path):
    repo = MarketDataRepository(tmp_path / "market.duckdb", snapshot_dir=tmp_path / "snapshots")
    snapshot_id = repo.save_raw_snapshot(
        source="tushare",
        endpoint="daily",
        request_params={"ts_code": "600000.SH", "trade_date": "20260102"},
        response_body={"ok": True},
        api_version="1.2.89",
    )
    row = repo.get_raw_snapshot(snapshot_id)
    assert row is not None
    assert row["endpoint"] == "daily"
    assert row["request_hash"]
    assert row["response_hash"]


def test_default_live_db_path_is_separate_from_fixture(tmp_path):
    from tradingagents.market_data.config import MarketDataPaths

    paths = MarketDataPaths(home_dir=tmp_path)
    assert paths.live_db_path.name == "market_live.duckdb"
    assert paths.fixture_db_path.name == "market.duckdb"
    assert paths.live_db_path != paths.fixture_db_path
