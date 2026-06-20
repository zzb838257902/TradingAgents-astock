"""Tests for live/historical sync date guards."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from tradingagents.market_data.sync_policy import (
    live_snapshot_date_error,
    security_snapshot_write_error,
    shanghai_today,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")


def test_live_snapshot_date_error_blocks_past(monkeypatch):
    monkeypatch.setattr(
        "tradingagents.market_data.sync_policy.shanghai_today",
        lambda: date(2026, 6, 19),
    )
    error = live_snapshot_date_error(date(2025, 1, 2), dataset="daily_bars")
    assert error is not None
    assert "historical date" in error


def test_security_snapshot_write_error_blocks_past(monkeypatch):
    monkeypatch.setattr(
        "tradingagents.market_data.sync_policy.shanghai_today",
        lambda: date(2026, 6, 19),
    )
    error = security_snapshot_write_error(
        date(2025, 1, 2),
        datetime(2026, 6, 19, 16, 0, tzinfo=SHANGHAI),
    )
    assert error is not None


def test_shanghai_today_returns_date():
    assert isinstance(shanghai_today(), date)
