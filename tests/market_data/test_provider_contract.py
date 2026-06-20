"""Provider contract tests for phase 4.1."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from tradingagents.market_data.contracts import (
    DataResult,
    DataStatus,
    Membership,
    MembershipMode,
    PITLevel,
    ProviderCapability,
    TradingDay,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")


def test_success_empty_allows_empty_universe_only():
    result = DataResult[list[str]](
        data=[],
        status=DataStatus.SUCCESS_EMPTY,
        source="test",
        as_of=datetime(2026, 6, 19, 15, 30, tzinfo=SHANGHAI),
        available_at=datetime(2026, 6, 19, 15, 30, tzinfo=SHANGHAI),
        pit_level=PITLevel.PIT_REQUIRED,
    )
    assert result.allows_empty_universe
    assert not result.is_usable_for_screening


@pytest.mark.parametrize(
    "status",
    [
        DataStatus.ERROR,
        DataStatus.NETWORK_ERROR,
        DataStatus.PERMISSION_DENIED,
        DataStatus.RATE_LIMITED,
        DataStatus.STALE,
        DataStatus.PARTIAL,
        DataStatus.DATA_QUALITY_FAILED,
        DataStatus.NOT_AVAILABLE_YET,
    ],
)
def test_blocking_statuses_do_not_allow_empty_universe(status: DataStatus):
    result = DataResult[None](
        data=None,
        status=status,
        source="test",
        as_of=datetime(2026, 6, 19, 15, 30, tzinfo=SHANGHAI),
        available_at=datetime(2026, 6, 19, 15, 30, tzinfo=SHANGHAI),
        pit_level=PITLevel.PIT_REQUIRED,
        errors=["blocked"],
    )
    assert not result.allows_empty_universe
    assert not result.is_usable_for_screening


def test_ok_result_is_usable_for_screening():
    result = DataResult[list[int]](
        data=[1],
        status=DataStatus.OK,
        source="test",
        as_of=datetime(2026, 6, 19, 15, 30, tzinfo=SHANGHAI),
        available_at=datetime(2026, 6, 19, 15, 30, tzinfo=SHANGHAI),
        ingested_at=datetime(2026, 6, 19, 16, 0, tzinfo=SHANGHAI),
        run_time=datetime(2026, 6, 19, 16, 0, tzinfo=SHANGHAI),
        pit_level=PITLevel.PIT_REQUIRED,
    )
    assert result.is_usable_for_screening
    assert result.usable_in_historical_mode


def test_membership_requires_mode():
    membership = Membership(
        board_type="industry",
        board_code="801080.SI",
        symbol="600001",
        membership_mode=MembershipMode.EFFECTIVE_INTERVAL,
        effective_from=date(2025, 1, 1),
        effective_to=None,
        snapshot_date=None,
        available_at=datetime(2025, 1, 1, 9, 0, tzinfo=SHANGHAI),
        source="fixture",
    )
    assert membership.was_member_on(date(2026, 1, 2))


def test_dated_snapshot_pit_member_on_matches_trade_date_only():
    membership = Membership(
        board_type="concept",
        board_code="BK1184.DC",
        symbol="600001",
        membership_mode=MembershipMode.DATED_SNAPSHOT,
        snapshot_date=date(2026, 1, 2),
        available_at=datetime(2026, 1, 2, 15, 30, tzinfo=SHANGHAI),
        source="fixture",
    )
    assert membership.pit_member_on(date(2026, 1, 2))
    assert not membership.pit_member_on(date(2026, 1, 3))


def test_provider_capability_records_probe_fields():
    cap = ProviderCapability(
        dataset="daily_bars",
        endpoint="daily",
        permitted=True,
        history_start=date(1990, 1, 1),
        max_rows_per_call=6000,
        rate_limit_per_minute=200,
        pit_level=PITLevel.PIT_REQUIRED,
        license_note="personal research",
        probed_at=datetime(2026, 6, 19, 10, 0, tzinfo=SHANGHAI),
    )
    assert cap.permitted


def test_market_data_provider_protocol_surface():
    """All providers must expose the phase 4 provider surface."""
    from tradingagents.market_data.providers.fixture import FixtureProvider
    from tradingagents.market_data.providers.free_astock import FreeAStockProvider
    from tradingagents.market_data.providers.tushare import TushareProvider

    required = {
        "list_securities",
        "get_trade_calendar",
        "get_daily_bars",
        "get_daily_indicators",
        "get_financials",
        "get_industry_members",
        "get_concept_members",
        "get_index_members",
        "probe_capabilities",
    }
    for provider_cls in (FixtureProvider, FreeAStockProvider, TushareProvider):
        for method in required:
            assert hasattr(provider_cls, method), method
        assert getattr(provider_cls, "name", None)


def test_trading_day_model():
    day = TradingDay(
        exchange="SSE",
        trade_date=date(2026, 1, 2),
        is_open=True,
        available_at=datetime(2026, 1, 2, 9, 0, tzinfo=SHANGHAI),
        source="fixture",
    )
    assert day.is_open
