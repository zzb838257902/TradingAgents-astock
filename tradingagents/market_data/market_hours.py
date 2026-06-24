"""A-share market session timestamps."""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")


def post_close_signal_time(signal_date: date) -> datetime:
    """Beijing 15:30 — post-close screening snapshot."""
    return datetime.combine(signal_date, time(15, 30), tzinfo=SHANGHAI)


def market_open_observed_at(trade_date: date) -> datetime:
    """Default T+1 opening snapshot observation time (09:35 Shanghai)."""
    return datetime.combine(trade_date, time(9, 35), tzinfo=SHANGHAI)


def bar_available_at(trade_date: date) -> datetime:
    """Daily bar visible after the cash session close."""
    return post_close_signal_time(trade_date)


def ensure_aware_shanghai(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=SHANGHAI)
    return value.astimezone(SHANGHAI)
