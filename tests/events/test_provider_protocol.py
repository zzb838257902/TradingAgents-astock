"""EventDataProvider protocol boundary tests (phase 5 Task 2)."""

from __future__ import annotations

import inspect
from datetime import date, datetime
from typing import get_type_hints
from zoneinfo import ZoneInfo

from tradingagents.events.providers import EventDataProvider, event_provider_methods
from tradingagents.market_data.contracts import DataResult, DataStatus, PITLevel, ProviderCapability
from tradingagents.market_data.providers.fixture import FixtureProvider
from tradingagents.market_data.providers.free_astock import FreeAStockProvider
from tradingagents.market_data.providers.tushare import TushareProvider

SHANGHAI = ZoneInfo("Asia/Shanghai")


class _StubEventProvider:
    name = "stub_events"

    def probe_event_capabilities(self):
        run_time = datetime(2026, 6, 20, 10, 0, tzinfo=SHANGHAI)
        return DataResult(
            data=[ProviderCapability(
                dataset="official_announcements",
                endpoint="sina.corp.vCB_AllBulletin",
                permitted=True,
                pit_level=PITLevel.PIT_REQUIRED,
                probed_at=run_time,
            )],
            status=DataStatus.OK,
            source=self.name,
            as_of=run_time,
            available_at=run_time,
            pit_level=PITLevel.PIT_REQUIRED,
        )

    def fetch_announcements(self, symbols, start, end):
        run_time = datetime(2026, 6, 20, 10, 0, tzinfo=SHANGHAI)
        return DataResult(
            data=[],
            status=DataStatus.SUCCESS_EMPTY,
            source=self.name,
            as_of=run_time,
            available_at=run_time,
            pit_level=PITLevel.PIT_REQUIRED,
        )

    def fetch_news(self, symbols, start, end):
        return self.fetch_announcements(symbols, start, end)

    def fetch_fund_flow_events(self, symbols, trade_date):
        return self.fetch_announcements(symbols, trade_date, trade_date)

    def fetch_hot_topics(self, trade_date):
        return self.fetch_announcements([], trade_date, trade_date)


def test_event_provider_protocol_surface():
    required = set(event_provider_methods())
    for method in required:
        assert hasattr(_StubEventProvider, method)
    hints = get_type_hints(EventDataProvider.probe_event_capabilities, include_extras=True)
    assert "return" in hints


def test_stub_provider_returns_data_result_wrappers():
    provider = _StubEventProvider()
    probe = provider.probe_event_capabilities()
    assert isinstance(probe, DataResult)
    assert probe.status == DataStatus.OK
    empty = provider.fetch_announcements(["600000"], date(2026, 1, 1), date(2026, 1, 31))
    assert empty.allows_empty_universe
    assert not empty.is_usable_for_screening


def test_market_data_providers_are_not_required_to_implement_event_provider():
    phase4_methods = {
        "list_securities",
        "get_trade_calendar",
        "get_daily_bars",
        "probe_capabilities",
    }
    event_methods = set(event_provider_methods())
    for provider_cls in (FreeAStockProvider, TushareProvider):
        for method in phase4_methods:
            assert hasattr(provider_cls, method)
        assert not event_methods.issubset(set(dir(provider_cls)))
    for method in phase4_methods:
        assert hasattr(FixtureProvider, method)


def test_event_provider_is_optional_protocol_not_base_class():
    assert inspect.isclass(EventDataProvider)
