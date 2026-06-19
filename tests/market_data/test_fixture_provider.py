"""Offline tests for FixtureProvider."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from tradingagents.market_data.contracts import DataStatus, MembershipMode, PITLevel
from tradingagents.market_data.providers.fixture import FixtureProvider

FIXTURE_PATH = Path("tests/fixtures/market_data/provider_mini.json")
SHANGHAI = ZoneInfo("Asia/Shanghai")


def _load_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_fixture_provider_lists_securities():
    provider = FixtureProvider(_load_fixture())
    result = provider.list_securities(date(2026, 1, 3))
    assert result.status == DataStatus.OK
    assert result.pit_level == PITLevel.PIT_REQUIRED
    symbols = {item.symbol for item in result.data or []}
    assert symbols == {"600001", "600002"}


def test_fixture_provider_trade_calendar():
    provider = FixtureProvider(_load_fixture())
    result = provider.get_trade_calendar(date(2026, 1, 1), date(2026, 1, 31))
    assert result.status == DataStatus.OK
    assert len(result.data or []) == 2


def test_fixture_provider_daily_bars_respects_range():
    provider = FixtureProvider(_load_fixture())
    result = provider.get_daily_bars(["600001"], date(2026, 1, 2), date(2026, 1, 3))
    assert result.status == DataStatus.OK
    assert len(result.data or []) == 2
    assert all(row["symbol"] == "600001" for row in result.data or [])


def test_fixture_provider_financials_pit():
    provider = FixtureProvider(_load_fixture())
    before = datetime(2025, 10, 28, 12, 0, tzinfo=SHANGHAI)
    after = datetime(2025, 10, 30, 12, 0, tzinfo=SHANGHAI)
    assert provider.get_financials(["600001"], before).status == DataStatus.SUCCESS_EMPTY
    visible = provider.get_financials(["600001"], after)
    assert visible.status == DataStatus.OK
    assert len(visible.data or []) == 1


def test_fixture_provider_industry_members():
    provider = FixtureProvider(_load_fixture())
    result = provider.get_industry_members(
        "801080.SI",
        datetime(2026, 1, 3, 15, 30, tzinfo=SHANGHAI),
    )
    assert result.status == DataStatus.OK
    assert result.data[0].membership_mode == MembershipMode.EFFECTIVE_INTERVAL
    assert result.data[0].symbol == "600001"


def test_fixture_provider_probe_capabilities_offline():
    provider = FixtureProvider(_load_fixture())
    result = provider.probe_capabilities()
    assert result.status == DataStatus.OK
    datasets = {item.dataset for item in result.data or []}
    assert "daily_bars" in datasets
    assert "financials" in datasets
