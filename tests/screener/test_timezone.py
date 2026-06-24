"""A-share timestamps must use Asia/Shanghai session semantics."""

from datetime import date

from tradingagents.market_data.fixture_store import load_fixture_into_repository
from tradingagents.market_data.market_hours import bar_available_at, post_close_signal_time
from tradingagents.market_data.repository import MarketDataRepository


def test_signal_time_is_beijing_post_close():
    signal_time = post_close_signal_time(date(2026, 1, 6))
    assert signal_time.tzinfo is not None
    assert signal_time.hour == 15
    assert signal_time.minute == 30
    assert str(signal_time.tzinfo) == "Asia/Shanghai"


def test_signal_date_bar_visible_but_next_day_not(tmp_path):
    fixture = {
        "version": 1,
        "datasets": {"daily_bars": "pit_required", "financials": "pit_required"},
        "symbols": [{"symbol": "600000", "industry": "电子"}],
        "bars": {
            "2026-01-05": {"600000": {"open": 10, "high": 10.2, "low": 9.8, "close": 10.1, "volume": 1000}},
            "2026-01-06": {"600000": {"open": 10.2, "high": 10.4, "low": 10.0, "close": 10.3, "volume": 1000}},
        },
        "financials": [],
    }
    repo = MarketDataRepository(tmp_path / "market.duckdb")
    load_fixture_into_repository(repo, fixture)
    signal_time = post_close_signal_time(date(2026, 1, 5))
    rows = repo.get_daily_bars(["600000"], end=date(2026, 1, 6), available_before=signal_time)
    trade_dates = {row["trade_date"] for row in rows}
    assert date(2026, 1, 5) in trade_dates
    assert date(2026, 1, 6) not in trade_dates
    assert bar_available_at(date(2026, 1, 5)) <= signal_time
