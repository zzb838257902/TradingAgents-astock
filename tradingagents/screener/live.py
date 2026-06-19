"""Build a screening fixture slice from repository data."""

from __future__ import annotations

from datetime import date, datetime

from tradingagents.market_data.market_hours import post_close_signal_time
from tradingagents.market_data.repository import MarketDataRepository


def build_fixture_from_repository(
    repo: MarketDataRepository,
    symbols: list[str],
    trading_dates: list[date],
    signal_time: datetime,
) -> dict:
    if len(trading_dates) < 2:
        raise ValueError("repository fixture slice requires at least two trading dates")
    bars: dict[str, dict] = {}
    for trade_date in trading_dates:
        available = post_close_signal_time(trade_date)
        if available > signal_time:
            continue
        day: dict[str, dict] = {}
        for row in repo.get_daily_bars(
            symbols,
            start=trade_date,
            end=trade_date,
            available_before=signal_time,
        ):
            day[row["symbol"]] = {
                "open": row["open"],
                "high": row["high"],
                "low": row["low"],
                "close": row["close"],
                "volume": row["volume"],
                "amount": row.get("amount", row["close"] * row["volume"]),
            }
        if day:
            bars[trade_date.isoformat()] = day

    securities = repo.get_effective_securities(signal_time.date(), signal_time)
    symbol_set = set(symbols)
    return {
        "version": 1,
        "datasets": {"daily_bars": "pit_required", "financials": "pit_required"},
        "symbols": [
            {
                "symbol": record.symbol,
                "industry": "未知",
                "list_date": record.list_date.isoformat(),
            }
            for record in securities
            if record.symbol in symbol_set
        ],
        "bars": bars,
        "financials": [],
    }
