"""Phase 4.5 financial PIT, revisions, and sync."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd

from tradingagents.market_data.config import MarketDataPaths
from tradingagents.market_data.financials import (
    financial_available_at,
    pick_latest_visible_financials,
)
from tradingagents.market_data.fixture_store import load_fixture_into_repository
from tradingagents.market_data.market_hours import post_close_signal_time
from tradingagents.market_data.providers.fixture import FixtureProvider
from tradingagents.market_data.providers.tushare import map_financial_frame
from tradingagents.market_data.repository import MarketDataRepository
from tradingagents.market_data.sync import MarketDataSync

SHANGHAI = ZoneInfo("Asia/Shanghai")


def test_conservative_available_at_hides_announcement_day_signal():
    ann = date(2025, 10, 29)
    available = financial_available_at(ann, open_dates=[date(2025, 10, 30), date(2025, 10, 31)])
    assert available == datetime(2025, 10, 30, 9, 0, tzinfo=SHANGHAI)
    same_day_signal = post_close_signal_time(ann)
    assert available > same_day_signal


def test_revision_versions_coexist_and_latest_visible_wins(tmp_path):
    repo = MarketDataRepository(tmp_path / "market.duckdb")
    repo.upsert_financials([
        {
            "symbol": "600001",
            "report_period": "2025-09-30",
            "roe": 0.10,
            "operating_cashflow": 1.0,
            "net_profit": 1.0,
            "debt_ratio": 0.30,
            "announcement_date": date(2025, 10, 29),
            "available_at": datetime(2025, 10, 30, 9, 0, tzinfo=SHANGHAI),
            "update_flag": "0",
            "record_type": "indicator",
            "source": "fixture",
        },
        {
            "symbol": "600001",
            "report_period": "2025-09-30",
            "roe": 0.20,
            "operating_cashflow": 2.0,
            "net_profit": 2.0,
            "debt_ratio": 0.25,
            "announcement_date": date(2025, 11, 15),
            "available_at": datetime(2025, 11, 17, 9, 0, tzinfo=SHANGHAI),
            "update_flag": "1",
            "record_type": "indicator",
            "source": "fixture",
        },
    ])
    before_revision = datetime(2025, 11, 10, 15, 30, tzinfo=SHANGHAI)
    after_revision = datetime(2025, 11, 20, 15, 30, tzinfo=SHANGHAI)
    early = repo.get_financials(["600001"], before_revision)
    late = repo.get_financials(["600001"], after_revision)
    assert early[0]["roe"] == 0.10
    assert late[0]["roe"] == 0.20
    stored = repo.connection.execute(
        "SELECT COUNT(*) FROM financials WHERE symbol = '600001'"
    ).fetchone()[0]
    assert stored == 2


def test_pick_latest_visible_financials_prefers_newer_report_period():
    rows = [
        {
            "symbol": "600001",
            "report_period": "2025-06-30",
            "available_at": datetime(2025, 8, 30, 9, 0, tzinfo=SHANGHAI),
        },
        {
            "symbol": "600001",
            "report_period": "2025-09-30",
            "available_at": datetime(2025, 10, 30, 9, 0, tzinfo=SHANGHAI),
        },
    ]
    picked = pick_latest_visible_financials(rows)
    assert picked[0]["report_period"] == "2025-09-30"


def test_map_financial_frame_applies_conservative_available_at():
    frame = pd.DataFrame([
        {
            "ts_code": "600001.SH",
            "end_date": "20250930",
            "ann_date": "20251029",
            "roe": 0.12,
            "ocfps": 1.1,
            "netprofit_yoy": 2.2,
            "debt_to_assets": 0.3,
            "update_flag": 0,
        }
    ])
    rows = map_financial_frame(frame, "tushare", open_dates=[date(2025, 10, 30)])
    assert rows[0]["announcement_date"] == date(2025, 10, 29)
    assert rows[0]["available_at"] == datetime(2025, 10, 30, 9, 0, tzinfo=SHANGHAI)


def test_sync_financials_from_fixture_provider(tmp_path):
    import json
    from pathlib import Path

    fixture = json.loads(Path("tests/fixtures/market_data/provider_mini.json").read_text())
    repo = MarketDataRepository(tmp_path / "market.duckdb")
    paths = MarketDataPaths(home_dir=tmp_path)
    provider = FixtureProvider(fixture)
    sync = MarketDataSync(repo, provider, paths)
    sync.probe_capabilities()
    as_of = datetime(2026, 1, 3, 15, 30, tzinfo=SHANGHAI)
    result = sync.sync_financials(as_of, symbols=["600001"])
    assert result.status.value == "published"
    rows = repo.get_financials(["600001"], as_of)
    assert len(rows) == 1
    assert rows[0]["symbol"] == "600001"


def test_legacy_fixture_financials_still_load(tmp_path):
    import json
    from pathlib import Path

    fixture = json.loads(Path("tests/fixtures/market_data/provider_mini.json").read_text())
    repo = MarketDataRepository(tmp_path / "market.duckdb")
    load_fixture_into_repository(repo, fixture)
    before = datetime(2025, 10, 28, 12, 0, tzinfo=SHANGHAI)
    after = datetime(2025, 10, 30, 12, 0, tzinfo=SHANGHAI)
    assert repo.get_financials(["600001"], before) == []
    assert len(repo.get_financials(["600001"], after)) == 1
