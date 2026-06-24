"""Market open snapshot provider, sync, and repository tests (Stage 6A Task 1)."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import duckdb
import pytest

from tradingagents.market_data.config import MarketDataPaths
from tradingagents.market_data.contracts import DataResult, DataStatus, PITLevel
from tradingagents.market_data.fixture_store import load_fixture_into_repository
from tradingagents.market_data.migrations import CURRENT_SCHEMA_VERSION, apply_migrations
from tradingagents.market_data.providers.free_astock import FreeAStockProvider
from tradingagents.market_data.providers.free_astock_sources import (
    ProviderFetchError,
    normalize_tencent_open_snapshot_row,
    parse_tencent_open_snapshot_response,
)
from tradingagents.market_data.providers.fixture import FixtureProvider
from tradingagents.market_data.repository import MarketDataRepository
from tradingagents.market_data.sync import MarketDataSync, SyncStatus
from tradingagents.market_data.sync_policy import shanghai_today

SHANGHAI = ZoneInfo("Asia/Shanghai")
TRADE_DATE = date(2026, 6, 23)
OBSERVED_AT = datetime(2026, 6, 23, 9, 35, tzinfo=SHANGHAI)


def _snapshot_row(symbol: str = "600000") -> dict:
    return {
        "symbol": symbol,
        "trade_date": TRADE_DATE,
        "observed_at": OBSERVED_AT,
        "open_cny": 10.12,
        "prev_close_cny": 10.00,
        "last_cny": 10.15,
        "cumulative_volume_shares": 1_200_000,
        "quote_status": "trading",
        "upper_limit_cny": 11.00,
        "lower_limit_cny": 9.00,
        "available_at": OBSERVED_AT,
        "source": "fixture",
    }


class _OpenSnapshotProvider:
    name = "fixture"

    def __init__(self, result: DataResult[list[dict]]):
        self._result = result
        self.calls: list[tuple[list[str], date, datetime]] = []

    def get_market_open_snapshots(
        self,
        symbols: list[str],
        trade_date: date,
        observed_at: datetime,
    ) -> DataResult[list[dict]]:
        self.calls.append((list(symbols), trade_date, observed_at))
        return self._result


def _ok_result(rows: list[dict]) -> DataResult[list[dict]]:
    return DataResult(
        data=rows,
        status=DataStatus.OK,
        source="fixture",
        as_of=OBSERVED_AT,
        available_at=OBSERVED_AT,
        pit_level=PITLevel.PIT_REQUIRED,
    )


def _setup(tmp_path, provider: _OpenSnapshotProvider) -> MarketDataSync:
    fixture = {
        "symbols": [
            {"symbol": "600000", "board": "main", "list_date": "2020-01-01"},
            {"symbol": "000001", "board": "main", "list_date": "2020-01-01"},
        ],
        "bars": {TRADE_DATE.isoformat(): {}},
        "datasets": {"daily_bars": "pit_required", "financials": "pit_required"},
    }
    paths = MarketDataPaths(home_dir=tmp_path)
    repo = MarketDataRepository(paths.live_db_path, snapshot_dir=paths.snapshot_dir)
    load_fixture_into_repository(repo, fixture)
    return MarketDataSync(repo, provider, paths)


def test_fresh_database_has_open_snapshot_tables(tmp_path):
    db_path = tmp_path / "market.duckdb"
    apply_migrations(db_path)
    assert CURRENT_SCHEMA_VERSION == 12
    connection = duckdb.connect(str(db_path))
    try:
        tables = {row[0] for row in connection.execute("SHOW TABLES").fetchall()}
        assert "market_open_snapshots" in tables
        assert "staging_market_open_snapshots" in tables
    finally:
        connection.close()


def test_normalize_tencent_open_snapshot_converts_volume_to_shares():
    row = normalize_tencent_open_snapshot_row(
        "600000",
        TRADE_DATE,
        OBSERVED_AT,
        {
            "symbol": "600000",
            "last_cny": 10.15,
            "prev_close_cny": 10.00,
            "open_cny": 10.12,
            "volume_lots": 12000,
            "upper_limit_cny": 11.00,
            "lower_limit_cny": 9.00,
        },
        source="free_astock",
    )
    assert row["open_cny"] == 10.12
    assert row["prev_close_cny"] == 10.00
    assert row["last_cny"] == 10.15
    assert row["cumulative_volume_shares"] == 1_200_000
    assert row["upper_limit_cny"] == 11.00
    assert row["lower_limit_cny"] == 9.00
    assert row["quote_status"] == "trading"


def test_parse_tencent_open_snapshot_response_maps_quote_fields():
    vals = ["0"] * 49
    vals[2] = "600000"
    vals[3] = "10.15"
    vals[4] = "10.00"
    vals[5] = "10.12"
    vals[6] = "12000"
    vals[47] = "11.00"
    vals[48] = "9.00"
    raw = f'v_sh600000="{"~".join(vals)}";'
    parsed = parse_tencent_open_snapshot_response(raw)
    assert parsed[0]["volume_lots"] == 12000
    assert parsed[0]["upper_limit_cny"] == 11.00


def test_free_provider_historical_open_snapshot_is_not_available_yet():
    class _Backend:
        def fetch_tencent_open_snapshots(self, symbols: list[str]) -> list[dict[str, object]]:
            return []

    provider = FreeAStockProvider(_Backend())
    result = provider.get_market_open_snapshots(
        ["600000"],
        date(2020, 1, 2),
        OBSERVED_AT,
    )
    assert result.status == DataStatus.NOT_AVAILABLE_YET
    assert result.data is None


def test_free_provider_today_uses_tencent_quotes_only():
    class _Backend:
        calls = 0

        def fetch_tencent_open_snapshots(self, symbols: list[str]) -> list[dict[str, object]]:
            self.calls += 1
            return [{
                "symbol": "600000",
                "last_cny": 10.15,
                "prev_close_cny": 10.00,
                "open_cny": 10.12,
                "volume_lots": 12000,
                "upper_limit_cny": 11.00,
                "lower_limit_cny": 9.00,
            }]

    backend = _Backend()
    provider = FreeAStockProvider(backend)
    result = provider.get_market_open_snapshots(
        ["600000"],
        shanghai_today(),
        OBSERVED_AT,
    )
    assert result.status == DataStatus.OK
    assert backend.calls == 1
    assert result.data[0]["cumulative_volume_shares"] == 1_200_000
    assert "close" not in result.data[0]


def test_free_provider_network_error():
    class _Backend:
        def fetch_tencent_open_snapshots(self, symbols: list[str]) -> list[dict[str, object]]:
            raise ProviderFetchError("network_error", "network down")

    provider = FreeAStockProvider(_Backend())
    result = provider.get_market_open_snapshots(
        ["600000"],
        shanghai_today(),
        OBSERVED_AT,
    )
    assert result.status == DataStatus.NETWORK_ERROR


def test_fixture_provider_open_snapshots_success_empty():
    provider = FixtureProvider({"symbols": [{"symbol": "600000"}]})
    result = provider.get_market_open_snapshots(
        ["600000"],
        TRADE_DATE,
        OBSERVED_AT,
    )
    assert result.status == DataStatus.SUCCESS_EMPTY
    assert result.pit_level == PITLevel.PIT_REQUIRED


def test_sync_market_open_snapshots_publishes_rows(tmp_path):
    provider = _OpenSnapshotProvider(
        _ok_result([_snapshot_row("600000"), _snapshot_row("000001")])
    )
    sync = _setup(tmp_path, provider)
    result = sync.sync_market_open_snapshots(
        ["600000", "000001"],
        TRADE_DATE,
        OBSERVED_AT,
    )
    assert result.status == SyncStatus.PUBLISHED
    rows = sync.repository.get_market_open_snapshots(
        ["600000", "000001"],
        TRADE_DATE,
        OBSERVED_AT,
    )
    assert len(rows) == 2


def test_sync_market_open_snapshots_historical_blocked(tmp_path):
    provider = _OpenSnapshotProvider(DataResult(
        data=None,
        status=DataStatus.NOT_AVAILABLE_YET,
        source="free_astock",
        as_of=OBSERVED_AT,
        available_at=OBSERVED_AT,
        pit_level=PITLevel.PIT_REQUIRED,
        errors=["historical open snapshots unsupported on free path"],
    ))
    provider.name = "free_astock"
    sync = _setup(tmp_path, provider)
    result = sync.sync_market_open_snapshots(
        ["600000"],
        date(2020, 1, 2),
        OBSERVED_AT,
    )
    assert result.status == SyncStatus.BLOCKED
    assert sync.repository.get_latest_published_version("market_open_snapshots") is None


def test_sync_market_open_snapshots_missing_symbol_blocked(tmp_path):
    provider = _OpenSnapshotProvider(_ok_result([_snapshot_row("600000")]))
    sync = _setup(tmp_path, provider)
    result = sync.sync_market_open_snapshots(
        ["600000", "000001"],
        TRADE_DATE,
        OBSERVED_AT,
    )
    assert result.status == SyncStatus.BLOCKED


def test_unpublished_open_snapshots_not_visible(tmp_path):
    provider = _OpenSnapshotProvider(
        _ok_result([_snapshot_row("600000"), _snapshot_row("000001")])
    )
    sync = _setup(tmp_path, provider)
    sync.repository.connection.execute(
        """
        INSERT INTO market_open_snapshots (
            symbol, trade_date, observed_at, open_cny, prev_close_cny, last_cny,
            cumulative_volume_shares, quote_status, upper_limit_cny, lower_limit_cny,
            available_at, source
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            "600000",
            TRADE_DATE,
            OBSERVED_AT,
            10.12,
            10.00,
            10.15,
            1_200_000,
            "trading",
            11.00,
            9.00,
            OBSERVED_AT,
            "shadow",
        ],
    )
    assert sync.repository.get_market_open_snapshots(
        ["600000"],
        TRADE_DATE,
        OBSERVED_AT,
    ) == []
    sync.sync_market_open_snapshots(["600000", "000001"], TRADE_DATE, OBSERVED_AT)
    rows = sync.repository.get_market_open_snapshots(
        ["600000"],
        TRADE_DATE,
        OBSERVED_AT,
    )
    assert len(rows) == 1
    assert rows[0]["source"] == "fixture"


def test_staging_open_snapshots_invisible_until_published(tmp_path):
    provider = _OpenSnapshotProvider(
        _ok_result([_snapshot_row("600000"), _snapshot_row("000001")])
    )
    sync = _setup(tmp_path, provider)
    run_id = sync.repository.begin_ingestion_run(
        "market_open_snapshots",
        {
            "trade_date": TRADE_DATE.isoformat(),
            "observed_at": OBSERVED_AT.isoformat(),
            "symbols": ["600000", "000001"],
        },
    )
    sync.repository.upsert_staging_market_open_snapshots(
        run_id,
        [_snapshot_row("600000")],
    )
    with pytest.raises(ValueError, match="quality gate"):
        sync.repository.publish_dataset_version(run_id)
    assert sync.repository.get_market_open_snapshots(
        ["600000"],
        TRADE_DATE,
        OBSERVED_AT,
    ) == []
