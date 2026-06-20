"""Optional event data provider protocol (phase 5)."""

from __future__ import annotations

from datetime import date
from typing import Protocol, Sequence

from tradingagents.events.contracts import MarketEvent
from tradingagents.market_data.contracts import DataResult, ProviderCapability


def event_provider_methods() -> tuple[str, ...]:
    return (
        "probe_event_capabilities",
        "fetch_announcements",
        "fetch_news",
        "fetch_fund_flow_events",
        "fetch_hot_topics",
    )


class EventDataProvider(Protocol):
    """Composable optional protocol; MarketDataProvider is not required to implement this."""

    name: str

    def probe_event_capabilities(self) -> DataResult[list[ProviderCapability]]:
        ...

    def fetch_announcements(
        self,
        symbols: Sequence[str],
        start: date,
        end: date,
    ) -> DataResult[list[MarketEvent]]:
        ...

    def fetch_news(
        self,
        symbols: Sequence[str],
        start: date,
        end: date,
    ) -> DataResult[list[MarketEvent]]:
        ...

    def fetch_fund_flow_events(
        self,
        symbols: Sequence[str],
        trade_date: date,
    ) -> DataResult[list[MarketEvent]]:
        ...

    def fetch_hot_topics(
        self,
        trade_date: date,
    ) -> DataResult[list[MarketEvent]]:
        ...
