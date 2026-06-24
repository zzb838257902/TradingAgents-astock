from datetime import date, datetime
from typing import Protocol, Sequence

from tradingagents.market_data.contracts import (
    DataResult,
    Membership,
    ProviderCapability,
    SecurityRecord,
    TradingDay,
)


class MarketDataProvider(Protocol):
    name: str

    def list_securities(self, as_of: date) -> DataResult[list[SecurityRecord]]:
        ...

    def get_trade_calendar(self, start: date, end: date) -> DataResult[list[TradingDay]]:
        ...

    def get_daily_bars(
        self, symbols: Sequence[str], start: date, end: date
    ) -> DataResult[list[dict]]:
        ...

    def get_daily_by_trade_date(self, trade_date: date) -> DataResult[list[dict]]:
        ...

    def get_daily_indicators(self, trade_date: date) -> DataResult[list[dict]]:
        ...

    def get_market_open_snapshots(
        self,
        symbols: Sequence[str],
        trade_date: date,
        observed_at: datetime,
    ) -> DataResult[list[dict]]:
        ...

    def get_financials(
        self, symbols: Sequence[str], announced_before: datetime
    ) -> DataResult[list[dict]]:
        ...

    def get_industry_members(
        self, code: str, as_of: datetime
    ) -> DataResult[list[Membership]]:
        ...

    def get_concept_members(
        self, code: str, as_of: datetime
    ) -> DataResult[list[Membership]]:
        ...

    def get_index_members(
        self, code: str, as_of: datetime
    ) -> DataResult[list[Membership]]:
        ...

    def probe_capabilities(self) -> DataResult[list[ProviderCapability]]:
        ...
