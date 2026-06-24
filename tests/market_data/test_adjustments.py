"""Tests for PIT adjustment factor construction."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from tradingagents.market_data.adjustments import (
    build_forward_adjusted_closes,
    build_pit_rows_from_xdxr,
    ensure_factor_baseline,
    forward_adjustment_ratio,
    latest_factor_on_or_before,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")


def test_build_pit_rows_from_xdxr_creates_factor_and_actions():
    rows = [{
        "category": 1,
        "year": 2025,
        "month": 6,
        "day": 15,
        "fenhong": 1.0,
        "peigu": 0.0,
        "peigujia": 0.0,
        "songzhuangu": 0.0,
        "pre_close": 10.0,
    }]
    factors, actions = build_pit_rows_from_xdxr("600000", rows)
    assert len(factors) == 1
    assert factors[0]["trade_date"] == date(2025, 6, 15)
    assert factors[0]["factor"] == 0.99
    assert factors[0]["available_at"] == datetime(2025, 6, 15, 15, 0, tzinfo=SHANGHAI)
    assert any(action["action_type"] == "cash_div" for action in actions)


def test_forward_adjustment_ratio_ten_yuan_dividend_on_ten_yuan_close():
    """每10股派10元 on 10元昨收 → 除权因子 0.9"""
    ratio = forward_adjustment_ratio(10.0, 0.0, 0.0, 0.0, prev_close=10.0)
    assert ratio == 0.9


def test_forward_adjustment_ratio_depends_on_prev_close():
    low_price = forward_adjustment_ratio(1.0, 0.0, 0.0, 0.0, prev_close=10.0)
    high_price = forward_adjustment_ratio(1.0, 0.0, 0.0, 0.0, prev_close=20.0)
    assert low_price == pytest.approx(0.99)
    assert high_price == pytest.approx(0.995)


def test_build_forward_adjusted_closes_removes_dividend_step():
    history = [
        {"trade_date": date(2026, 1, 1), "close": 10.0},
        {"trade_date": date(2026, 1, 2), "close": 10.0},
        {"trade_date": date(2026, 1, 3), "close": 5.0},
    ]
    factors = [
        {
            "symbol": "600000",
            "trade_date": date(2026, 1, 1),
            "factor": 1.0,
            "available_at": datetime(2026, 1, 1, 15, 0, tzinfo=SHANGHAI),
            "source": "test",
        },
        {
            "symbol": "600000",
            "trade_date": date(2026, 1, 3),
            "factor": 0.5,
            "available_at": datetime(2026, 1, 3, 15, 0, tzinfo=SHANGHAI),
            "source": "test",
        },
    ]
    available = datetime(2026, 1, 3, 16, 0, tzinfo=SHANGHAI)
    adjusted = build_forward_adjusted_closes(history, factors, date(2026, 1, 3), available)
    assert adjusted == [5.0, 5.0, 5.0]


def test_build_pit_rows_from_xdxr_skips_events_without_prev_close():
    rows = [
        {
            "category": 1,
            "year": 2000,
            "month": 7,
            "day": 6,
            "fenhong": 1.0,
            "peigu": 0.0,
            "peigujia": 0.0,
            "songzhuangu": 0.0,
        },
        {
            "category": 1,
            "year": 2025,
            "month": 6,
            "day": 15,
            "fenhong": 1.0,
            "peigu": 0.0,
            "peigujia": 0.0,
            "songzhuangu": 0.0,
            "pre_close": 10.0,
        },
    ]
    factors, _actions = build_pit_rows_from_xdxr("600000", rows)
    assert len(factors) == 1
    assert factors[0]["trade_date"] == date(2025, 6, 15)


def test_ensure_factor_baseline_covers_history_before_first_ex_date():
    anchor = date(2026, 6, 12)
    available = datetime(2026, 6, 12, 15, 30, tzinfo=SHANGHAI)
    ex_row = {
        "symbol": "600000",
        "trade_date": date(2026, 6, 17),
        "factor": 0.9,
        "available_at": datetime(2026, 6, 17, 15, 30, tzinfo=SHANGHAI),
        "source": "test",
    }
    rows = ensure_factor_baseline([ex_row], "600000", anchor, available_at=available, source="test")
    assert rows[0]["trade_date"] == anchor
    assert rows[0]["factor"] == 1.0
    history = [
        {"trade_date": date(2026, 6, 12), "close": 10.0},
        {"trade_date": date(2026, 6, 17), "close": 9.0},
    ]
    adjusted = build_forward_adjusted_closes(
        history,
        rows,
        date(2026, 6, 17),
        datetime(2026, 6, 17, 16, 0, tzinfo=SHANGHAI),
    )
    assert len(adjusted) == 2


def test_latest_factor_on_or_before_respects_available_at():
    rows = [
        {
            "symbol": "600000",
            "trade_date": date(2025, 6, 15),
            "factor": 0.9,
            "available_at": datetime(2025, 6, 15, 15, 0, tzinfo=SHANGHAI),
            "source": "test",
        }
    ]
    assert latest_factor_on_or_before(
        rows,
        date(2025, 6, 20),
        datetime(2025, 6, 20, 16, 0, tzinfo=SHANGHAI),
    ) == 0.9
    assert latest_factor_on_or_before(
        rows,
        date(2025, 6, 20),
        datetime(2025, 6, 15, 14, 0, tzinfo=SHANGHAI),
    ) is None
