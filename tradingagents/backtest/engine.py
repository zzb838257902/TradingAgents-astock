from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from tradingagents.backtest.execution import ExecutionModel
from tradingagents.backtest.limits import bar_from_dict
from tradingagents.backtest.models import Order, Side


class EquityPoint(BaseModel):
    trade_date: date
    equity: float


class ExecutedOrder(BaseModel):
    trade_date: date
    symbol: str
    side: Side
    shares: int
    price: float
    commission: float
    stamp_tax: float


class DelistingEvent(BaseModel):
    trade_date: date
    symbol: str
    recovery_rate: float
    proceeds: float


class BacktestResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    orders: list[ExecutedOrder] = Field(default_factory=list)
    equity_curve: list[EquityPoint] = Field(default_factory=list)
    positions: dict[str, int] = Field(default_factory=dict)
    delisting_events: list[DelistingEvent] = Field(default_factory=list)
    config_snapshot: dict = Field(default_factory=dict)
    input_snapshot_id: str = ""


class _Lot(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    shares: int
    acquired_date: date


class BacktestEngine:
    def __init__(
        self,
        initial_cash: float,
        execution: ExecutionModel,
        delisting_recovery_rate: float = 0.0,
    ):
        self.initial_cash = initial_cash
        self.execution = execution
        self.delisting_recovery_rate = delisting_recovery_rate

    def run(
        self,
        bars: dict[date, dict[str, dict]],
        target_weights: dict[date, dict[str, float]],
        delistings: dict[date, list[str]] | None = None,
    ) -> BacktestResult:
        delistings = delistings or {}
        trading_dates = sorted(bars.keys())
        cash = self.initial_cash
        lots: dict[str, list[_Lot]] = {}
        orders: list[ExecutedOrder] = []
        equity_curve: list[EquityPoint] = []
        delisting_events: list[DelistingEvent] = []
        pending_targets: dict[str, float] | None = None

        for trade_date in trading_dates:
            day_bars = bars[trade_date]
            prev_day_bars = bars.get(self._previous_trading_date(trading_dates, trade_date), {})

            if pending_targets is not None:
                cash = self._execute_targets(
                    trade_date,
                    day_bars,
                    prev_day_bars,
                    pending_targets,
                    cash,
                    lots,
                    orders,
                )
                pending_targets = None

            for symbol in delistings.get(trade_date, []):
                symbol_lots = lots.get(symbol, [])
                total_shares = sum(lot.shares for lot in symbol_lots)
                if total_shares <= 0:
                    continue
                bar_data = day_bars.get(symbol) or prev_day_bars.get(symbol, {})
                price = bar_data.get("close", 0.0)
                proceeds = total_shares * price * self.delisting_recovery_rate
                cash += proceeds
                delisting_events.append(DelistingEvent(
                    trade_date=trade_date,
                    symbol=symbol,
                    recovery_rate=self.delisting_recovery_rate,
                    proceeds=proceeds,
                ))
                lots[symbol] = []

            held_symbols = [s for s, ls in lots.items() if sum(lot.shares for lot in ls) > 0]
            for symbol in held_symbols:
                if symbol in delistings.get(trade_date, []):
                    continue
                if symbol not in day_bars:
                    raise ValueError(f"missing bar for held symbol {symbol}")

            if trade_date in target_weights:
                pending_targets = target_weights[trade_date]

            equity = self._portfolio_equity(cash, lots, day_bars)
            equity_curve.append(EquityPoint(trade_date=trade_date, equity=equity))

        final_positions = {
            symbol: sum(lot.shares for lot in symbol_lots)
            for symbol, symbol_lots in lots.items()
            if sum(lot.shares for lot in symbol_lots) > 0
        }

        return BacktestResult(
            orders=orders,
            equity_curve=equity_curve,
            positions=final_positions,
            delisting_events=delisting_events,
            config_snapshot={
                "initial_cash": self.initial_cash,
                "delisting_recovery_rate": self.delisting_recovery_rate,
            },
            input_snapshot_id="",
        )

    def _previous_trading_date(
        self, trading_dates: list[date], trade_date: date
    ) -> date | None:
        index = trading_dates.index(trade_date)
        if index == 0:
            return None
        return trading_dates[index - 1]

    def _sellable_shares(self, symbol: str, lots: list[_Lot], trade_date: date) -> int:
        return sum(lot.shares for lot in lots if lot.acquired_date < trade_date)

    def _portfolio_equity(
        self,
        cash: float,
        lots: dict[str, list[_Lot]],
        day_bars: dict[str, dict],
    ) -> float:
        equity = cash
        for symbol, symbol_lots in lots.items():
            total = sum(lot.shares for lot in symbol_lots)
            if total > 0 and symbol in day_bars:
                equity += total * day_bars[symbol]["close"]
        return equity

    def _portfolio_equity_at_prev_close(
        self,
        cash: float,
        lots: dict[str, list[_Lot]],
        prev_day_bars: dict[str, dict],
    ) -> float:
        equity = cash
        for symbol, symbol_lots in lots.items():
            total = sum(lot.shares for lot in symbol_lots)
            if total > 0 and symbol in prev_day_bars:
                equity += total * prev_day_bars[symbol]["close"]
        return equity

    def _execute_targets(
        self,
        trade_date: date,
        day_bars: dict[str, dict],
        prev_day_bars: dict[str, dict],
        targets: dict[str, float],
        cash: float,
        lots: dict[str, list[_Lot]],
        orders: list[ExecutedOrder],
    ) -> float:
        symbols = sorted(set(lots.keys()) | set(targets.keys()))
        for symbol in symbols:
            current = sum(lot.shares for lot in lots.get(symbol, []))
            if symbol not in day_bars and (current > 0 or targets.get(symbol, 0) > 0):
                raise ValueError(f"missing bar for held symbol {symbol}")

        equity = self._portfolio_equity_at_prev_close(cash, lots, prev_day_bars)

        for symbol in symbols:
            if symbol not in day_bars:
                continue
            bar_data = day_bars[symbol]
            prev_close = bar_data.get("prev_close")
            if prev_close is None and symbol in prev_day_bars:
                prev_close = prev_day_bars[symbol]["close"]
            if prev_close is None:
                raise ValueError(f"missing prev_close for strict sizing on {symbol}")

            bar = bar_from_dict(
                bar_data,
                prev_close=prev_close,
                st_flag=bar_data.get("st_flag", False),
                board=bar_data.get("board", "main"),
            )
            current_shares = sum(lot.shares for lot in lots.get(symbol, []))
            target_shares = int(equity * targets.get(symbol, 0.0) / prev_close / 100) * 100
            delta = target_shares - current_shares
            if delta == 0:
                continue

            if delta > 0:
                order = Order(symbol=symbol, side=Side.BUY, shares=delta)
                sellable = 0
            else:
                sellable = self._sellable_shares(symbol, lots.get(symbol, []), trade_date)
                order = Order(symbol=symbol, side=Side.SELL, shares=-delta)

            fill = self.execution.fill(order, bar, sellable_shares=sellable)
            if fill is None:
                continue

            notional = fill.price * fill.shares
            if fill.side == Side.BUY:
                cost = notional + fill.commission + fill.stamp_tax
                if cost > cash:
                    continue
                cash -= cost
                lots.setdefault(symbol, []).append(_Lot(shares=fill.shares, acquired_date=trade_date))
            else:
                cash += notional - fill.commission - fill.stamp_tax
                remaining = fill.shares
                kept: list[_Lot] = []
                for lot in lots.get(symbol, []):
                    if remaining <= 0 or lot.acquired_date >= trade_date:
                        kept.append(lot)
                        continue
                    if lot.shares <= remaining:
                        remaining -= lot.shares
                    else:
                        kept.append(_Lot(shares=lot.shares - remaining, acquired_date=lot.acquired_date))
                        remaining = 0
                lots[symbol] = kept

            orders.append(ExecutedOrder(
                trade_date=trade_date,
                symbol=fill.symbol,
                side=fill.side,
                shares=fill.shares,
                price=fill.price,
                commission=fill.commission,
                stamp_tax=fill.stamp_tax,
            ))

        return cash
