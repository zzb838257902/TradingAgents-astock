"""Regression: execution sizing must not use same-day close."""

from datetime import date

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


def test_changing_execution_day_close_does_not_change_fill_shares():
    base_close = 50.0
    bars_base = {
        date(2026, 1, 2): {"600000": _bar(10, 10.2, 10.0)},
        date(2026, 1, 5): {"600000": _bar(10, 10.7, 10.2)},
    }
    bars_high_close = {
        date(2026, 1, 2): {"600000": _bar(10, 10.2, 10.0)},
        date(2026, 1, 5): {"600000": _bar(10, base_close, 10.2)},
    }
    signals = {date(2026, 1, 2): {"600000": 1.0}}
    engine = BacktestEngine(
        initial_cash=100_000,
        execution=ExecutionModel(commission_rate=0, stamp_tax_rate=0),
    )
    first = engine.run(bars=bars_base, target_weights=signals)
    second = engine.run(bars=bars_high_close, target_weights=signals)
    assert first.orders[0].shares == second.orders[0].shares
