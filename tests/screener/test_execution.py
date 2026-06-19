from tradingagents.backtest.execution import ExecutionModel
from tradingagents.backtest.models import Bar, Order, Side


def test_cannot_buy_one_word_limit_up():
    model = ExecutionModel(commission_rate=0.0003, stamp_tax_rate=0.0005)
    bar = Bar(open=11, high=11, low=11, close=11, volume=100000, limit_up=11, limit_down=9)
    fill = model.fill(Order("600000", Side.BUY, 1000), bar, sellable_shares=0)
    assert fill is None


def test_t_plus_one_blocks_same_day_sale():
    model = ExecutionModel(commission_rate=0.0003, stamp_tax_rate=0.0005)
    bar = Bar(open=10, high=10.5, low=9.8, close=10.2, volume=100000, limit_up=11, limit_down=9)
    fill = model.fill(Order("600000", Side.SELL, 1000), bar, sellable_shares=0)
    assert fill is None


def test_participation_rate_limits_fill_quantity():
    model = ExecutionModel(
        commission_rate=0.0003, stamp_tax_rate=0.0005,
        max_participation_rate=0.05,
    )
    bar = Bar(open=10, high=10.5, low=9.8, close=10.2, volume=10_000, limit_up=11, limit_down=9)
    fill = model.fill(Order("600000", Side.BUY, 1000), bar, sellable_shares=0)
    assert fill.shares == 500
