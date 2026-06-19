from datetime import date

import pytest

from tradingagents.backtest.engine import BacktestEngine
from tradingagents.backtest.execution import ExecutionModel


def _bar(open_: float, close: float, prev_close: float, volume: float = 100_000):
    return {
        "open": open_,
        "high": max(open_, close) * 1.01,
        "low": min(open_, close) * 0.99,
        "close": close,
        "volume": volume,
        "prev_close": prev_close,
        "limit_up": round(prev_close * 1.1, 2),
        "limit_down": round(prev_close * 0.9, 2),
    }


def test_signal_at_close_executes_next_day_and_respects_t_plus_one():
    bars = {
        date(2026, 1, 2): {"600000": _bar(10, 10.2, 10.0)},
        date(2026, 1, 5): {"600000": _bar(10.3, 10.7, 10.2)},
        date(2026, 1, 6): {"600000": _bar(10.8, 10.9, 10.7)},
    }
    signals = {date(2026, 1, 2): {"600000": 1.0}, date(2026, 1, 5): {}}
    engine = BacktestEngine(
        initial_cash=100_000,
        execution=ExecutionModel(commission_rate=0, stamp_tax_rate=0),
    )
    result = engine.run(bars=bars, target_weights=signals)
    assert result.orders[0].trade_date == date(2026, 1, 5)
    assert result.equity_curve[-1].equity > 100_000


def test_missing_bar_for_held_symbol_stops_run():
    engine = BacktestEngine(
        initial_cash=100_000,
        execution=ExecutionModel(commission_rate=0, stamp_tax_rate=0),
    )
    bars = {
        date(2026, 1, 2): {"600000": _bar(10, 10, 10.0)},
        date(2026, 1, 5): {},
    }
    with pytest.raises(ValueError, match="missing bar for held symbol 600000"):
        engine.run(bars=bars, target_weights={date(2026, 1, 2): {"600000": 1.0}})


def test_delisting_uses_configured_recovery_price():
    engine = BacktestEngine(
        initial_cash=100_000,
        execution=ExecutionModel(commission_rate=0, stamp_tax_rate=0),
        delisting_recovery_rate=0.20,
    )
    result = engine.run(
        bars={
            date(2026, 1, 2): {"600000": _bar(10, 10.2, 10.0)},
            date(2026, 1, 5): {"600000": _bar(10.3, 10.7, 10.2)},
            date(2026, 1, 6): {"600000": _bar(8, 8, 10.7, volume=0)},
        },
        target_weights={date(2026, 1, 2): {"600000": 1.0}},
        delistings={date(2026, 1, 6): ["600000"]},
    )
    event = result.delisting_events[0]
    assert event.symbol == "600000"
    assert event.recovery_rate == 0.20
    assert result.positions == {}


def test_delisting_without_bar_does_not_raise_missing_bar():
    engine = BacktestEngine(
        initial_cash=100_000,
        execution=ExecutionModel(commission_rate=0, stamp_tax_rate=0),
        delisting_recovery_rate=0.20,
    )
    result = engine.run(
        bars={
            date(2026, 1, 2): {"600000": _bar(10, 10.2, 10.0)},
            date(2026, 1, 5): {"600000": _bar(10.3, 10.7, 10.2)},
            date(2026, 1, 6): {},
        },
        target_weights={date(2026, 1, 2): {"600000": 1.0}},
        delistings={date(2026, 1, 6): ["600000"]},
    )
    assert result.delisting_events[0].symbol == "600000"
    assert result.positions == {}
