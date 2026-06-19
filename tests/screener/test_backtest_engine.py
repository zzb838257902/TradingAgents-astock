from datetime import date

import pytest

from tradingagents.backtest.engine import BacktestEngine
from tradingagents.backtest.execution import ExecutionModel


def test_signal_at_close_executes_next_day_and_respects_t_plus_one():
    bars = {
        date(2026, 1, 2): {"600000": {"open": 10, "high": 10.5, "low": 9.8, "close": 10.2, "volume": 100000}},
        date(2026, 1, 5): {"600000": {"open": 10.3, "high": 10.8, "low": 10.1, "close": 10.7, "volume": 100000}},
        date(2026, 1, 6): {"600000": {"open": 10.8, "high": 11.0, "low": 10.5, "close": 10.9, "volume": 100000}},
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
        date(2026, 1, 2): {"600000": {"open": 10, "high": 10, "low": 10,
                                          "close": 10, "volume": 100000}},
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
            date(2026, 1, 2): {"600000": {"open": 10, "high": 10.5,
                "low": 9.8, "close": 10.2, "volume": 100000}},
            date(2026, 1, 5): {"600000": {"open": 10.3, "high": 10.8,
                "low": 10.1, "close": 10.7, "volume": 100000}},
            date(2026, 1, 6): {"600000": {"open": 8, "high": 8,
                "low": 8, "close": 8, "volume": 0}},
        },
        target_weights={date(2026, 1, 2): {"600000": 1.0}},
        delistings={date(2026, 1, 6): ["600000"]},
    )
    event = result.delisting_events[0]
    assert event.symbol == "600000"
    assert event.recovery_rate == 0.20
    assert result.positions == {}
