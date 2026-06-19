import pytest

from tradingagents.backtest.limits import bar_from_dict, compute_limit_prices


def test_main_board_limit_is_ten_percent():
    up, down = compute_limit_prices(10.0, board="main")
    assert up == 11.0
    assert down == 9.0


def test_st_limit_is_five_percent():
    up, down = compute_limit_prices(10.0, st_flag=True)
    assert up == 10.5
    assert down == 9.5


def test_strict_bar_requires_limit_or_prev_close():
    with pytest.raises(ValueError, match="strict backtest"):
        bar_from_dict({"open": 1, "high": 1, "low": 1, "close": 1, "volume": 100}, prev_close=None)
