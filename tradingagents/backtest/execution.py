import math

from tradingagents.backtest.models import Bar, Fill, Order, Side


class ExecutionModel:
    def __init__(
        self,
        commission_rate: float = 0.0003,
        stamp_tax_rate: float = 0.0005,
        max_participation_rate: float = 0.05,
        slippage_rate: float = 0.0,
    ):
        self.commission_rate = commission_rate
        self.stamp_tax_rate = stamp_tax_rate
        self.max_participation_rate = max_participation_rate
        self.slippage_rate = slippage_rate

    def fill(
        self, order: Order, bar: Bar, sellable_shares: int
    ) -> Fill | None:
        if bar.suspended or bar.volume <= 0:
            return None

        is_one_word_limit_up = (
            bar.open == bar.high == bar.low == bar.close == bar.limit_up
        )
        is_one_word_limit_down = (
            bar.open == bar.high == bar.low == bar.close == bar.limit_down
        )

        if order.side == Side.BUY and is_one_word_limit_up:
            return None
        if order.side == Side.SELL and is_one_word_limit_down:
            return None

        quantity = order.shares
        if order.side == Side.SELL:
            quantity = min(quantity, sellable_shares)
            if quantity <= 0:
                return None

        max_shares = int(math.floor(bar.volume * self.max_participation_rate / 100)) * 100
        quantity = min(quantity, max_shares)
        if quantity <= 0:
            return None

        if order.side == Side.BUY:
            price = bar.open * (1 + self.slippage_rate)
        else:
            price = bar.open * (1 - self.slippage_rate)

        notional = price * quantity
        commission = notional * self.commission_rate
        stamp_tax = notional * self.stamp_tax_rate if order.side == Side.SELL else 0.0

        return Fill(
            symbol=order.symbol,
            side=order.side,
            shares=quantity,
            price=price,
            commission=commission,
            stamp_tax=stamp_tax,
        )
