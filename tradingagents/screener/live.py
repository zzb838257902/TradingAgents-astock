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
    industry_labels = repo.get_symbol_industry_labels(
        symbols,
        signal_time.date(),
        signal_time,
    )
    financial_rows = repo.get_financials(symbols, available_before=signal_time)
    financials = [
        {
            "symbol": row["symbol"],
            "report_period": row["report_period"],
            "roe": row["roe"],
            "operating_cashflow": row["operating_cashflow"],
            "net_profit": row["net_profit"],
            "debt_ratio": row["debt_ratio"],
            "announcement_date": row["announcement_date"].isoformat()
            if hasattr(row["announcement_date"], "isoformat")
            else row["announcement_date"],
            "available_at": row["available_at"].isoformat()
            if hasattr(row["available_at"], "isoformat")
            else row["available_at"],
        }
        for row in financial_rows
    ]
    return {
        "version": 1,
        "datasets": {"daily_bars": "pit_required", "financials": "pit_required"},
        "symbols": [
            {
                "symbol": record.symbol,
                "industry": industry_labels.get(record.symbol, "未知"),
                "list_date": record.list_date.isoformat(),
            }
            for record in securities
            if record.symbol in symbol_set
        ],
        "bars": bars,
        "financials": financials,
    }
