from datetime import date
from typing import Protocol, Sequence

from tradingagents.market_data.contracts import DataResult, SecurityRecord


class MarketDataProvider(Protocol):
    name: str

    def list_securities(self, as_of: date) -> DataResult[list[SecurityRecord]]:
        ...

    def get_daily_bars(
        self, symbols: Sequence[str], start: date, end: date
    ) -> DataResult[list[dict]]:
        ...

    def get_financials(
        self, symbols: Sequence[str], available_before: date
    ) -> DataResult[list[dict]]:
        ...
