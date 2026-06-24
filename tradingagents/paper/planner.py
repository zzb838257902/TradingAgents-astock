"""Deterministic rebalance planning for Stage 6A paper operations."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from tradingagents.market_data.market_hours import ensure_aware_shanghai
from tradingagents.market_data.repository import MarketDataRepository
from tradingagents.paper.contracts import (
    OrderSide,
    OrderStatus,
    PaperOrder,
    RunStatus,
    TargetPortfolioMode,
    money,
)
from tradingagents.paper.exceptions import InvalidScreenRun, RevisionConflict
from tradingagents.paper.repository import AccountSnapshot, PaperRepository, RebalanceRevisionSpec
from tradingagents.paper.screening import STRATEGY_VERSION
from tradingagents.screener.config import ScreenerConfig
from tradingagents.screener.report import ScreeningStatus

WEIGHT_TOLERANCE = Decimal("0.00000001")
LOT_SIZE = 100
DEFAULT_COMMISSION_RATE = Decimal("0.0003")
DEFAULT_STAMP_TAX_RATE = Decimal("0.0005")
DEFAULT_MINIMUM_COMMISSION_CNY = Decimal("5.00")
PRICE_STATUS_ESTIMATED = "ESTIMATED"


@dataclass(frozen=True)
class FeeConfig:
    commission_rate: Decimal = DEFAULT_COMMISSION_RATE
    stamp_tax_rate: Decimal = DEFAULT_STAMP_TAX_RATE
    minimum_commission_cny: Decimal = DEFAULT_MINIMUM_COMMISSION_CNY


@dataclass(frozen=True)
class RebalancePlan:
    rebalance_run_id: str
    account_id: str
    screen_run_id: str
    logical_run_key: str
    target_hash: str
    revision: int
    signal_date: date
    execution_date: date
    orders: list[PaperOrder]
    reference_prices: dict[str, Decimal]
    price_status: str = PRICE_STATUS_ESTIMATED


def compute_logical_run_key(
    *,
    account_id: str,
    signal_date: date,
    execution_date: date,
    universe_hash: str,
    config_hash: str,
    strategy_version: str = STRATEGY_VERSION,
) -> str:
    return (
        f"{account_id}:{signal_date.isoformat()}:{execution_date.isoformat()}:"
        f"{universe_hash}:{config_hash}:{strategy_version}"
    )


def compute_target_hash(
    *,
    screen_content_hash: str,
    target_portfolio_mode: TargetPortfolioMode,
    target_weights_json: str,
    account_snapshot: AccountSnapshot,
    reference_prices: dict[str, Decimal],
) -> str:
    positions = {
        symbol: {
            "quantity": projection.quantity,
            "available_quantity": projection.available_quantity,
            "average_cost_cny": str(money(projection.average_cost_cny)),
        }
        for symbol, projection in sorted(account_snapshot.positions.items())
    }
    payload = {
        "screen_content_hash": screen_content_hash,
        "target_portfolio_mode": target_portfolio_mode.value,
        "target_weights_json": target_weights_json,
        "cash_cny": str(money(account_snapshot.cash_cny)),
        "positions": positions,
        "reference_prices": {
            symbol: str(money(price))
            for symbol, price in sorted(reference_prices.items())
        },
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def stable_rebalance_run_id(logical_run_key: str, revision: int) -> str:
    digest = hashlib.sha256(f"{logical_run_key}:r{revision}".encode()).hexdigest()
    return f"reb-{digest[:12]}"


def stable_order_id(*, side: OrderSide, symbol: str, rebalance_run_id: str) -> str:
    return f"ord-{side.name.lower()}-{symbol}-{rebalance_run_id}"


def next_execution_date(market_repo: MarketDataRepository, signal_date: date) -> date:
    open_dates = market_repo.list_open_trade_dates()
    for trade_date in open_dates:
        if trade_date > signal_date:
            return trade_date
    raise InvalidScreenRun(
        f"no execution date after signal date {signal_date.isoformat()}"
    )


def load_reference_closes(
    paper_repo: PaperRepository,
    *,
    screen_run_id: str,
    signal_date: date,
    symbols: set[str],
) -> dict[str, Decimal]:
    rows = paper_repo.connection.execute(
        """
        SELECT scope_key, row_json
        FROM paper_run_inputs
        WHERE run_id = ? AND input_type = 'DAILY_BAR'
        """,
        [screen_run_id],
    ).fetchall()
    suffix = f":{signal_date.isoformat()}"
    closes: dict[str, Decimal] = {}
    for scope_key, row_json in rows:
        if not str(scope_key).endswith(suffix):
            continue
        row = json.loads(row_json)
        symbol = str(row["symbol"])
        if symbol not in symbols:
            continue
        closes[symbol] = money(row["close"])
    missing = sorted(symbol for symbol in symbols if symbol not in closes)
    if missing:
        raise InvalidScreenRun(
            f"missing signal-date close prices for: {', '.join(missing)}"
        )
    return closes


def _estimate_commission(notional: Decimal, fee_config: FeeConfig) -> Decimal:
    return max(
        money(notional * fee_config.commission_rate),
        fee_config.minimum_commission_cny,
    )


def _investable_equity(
    snapshot: AccountSnapshot,
    close_prices: dict[str, Decimal],
) -> Decimal:
    positions_value = Decimal("0.00")
    for symbol, projection in snapshot.positions.items():
        price = close_prices.get(symbol, projection.last_price_cny or projection.average_cost_cny)
        positions_value += money(price * projection.quantity)
    return money(snapshot.cash_cny + positions_value)


def _validate_screen_run(
    *,
    status: str,
    target_mode: TargetPortfolioMode,
    target_weights: dict[str, float],
    cash_weight: Decimal,
) -> None:
    if status != ScreeningStatus.OK.value:
        raise InvalidScreenRun(f"screen status {status!r} is not plannable")
    if target_mode == TargetPortfolioMode.WEIGHTS:
        weight_sum = sum(Decimal(str(weight)) for weight in target_weights.values())
        total = weight_sum + cash_weight
        if abs(total - Decimal("1")) > WEIGHT_TOLERANCE:
            raise InvalidScreenRun(
                f"target weights must sum to 1 with cash_weight; got {total}"
            )


def plan_orders(
    *,
    account_id: str,
    rebalance_run_id: str,
    snapshot: AccountSnapshot,
    target_mode: TargetPortfolioMode,
    target_weights: dict[str, float],
    close_prices: dict[str, Decimal],
    fee_config: FeeConfig | None = None,
    sellable_snapshot: AccountSnapshot | None = None,
) -> list[PaperOrder]:
    fee_config = fee_config or FeeConfig()
    sellable_snapshot = sellable_snapshot or snapshot
    equity = _investable_equity(snapshot, close_prices)
    symbols = sorted(set(snapshot.positions) | set(target_weights))
    target_quantities: dict[str, int] = {}

    if target_mode == TargetPortfolioMode.ALL_CASH:
        for symbol in symbols:
            target_quantities[symbol] = 0
    else:
        for symbol in symbols:
            weight = Decimal(str(target_weights.get(symbol, 0.0)))
            if weight <= 0:
                target_quantities[symbol] = 0
                continue
            price = close_prices[symbol]
            if price <= 0:
                raise InvalidScreenRun(f"non-positive reference price for {symbol}")
            raw_shares = (equity * weight) / price
            target_quantities[symbol] = int(raw_shares // LOT_SIZE) * LOT_SIZE

    orders: list[PaperOrder] = []
    for symbol in symbols:
        current = snapshot.positions.get(symbol)
        current_qty = current.quantity if current is not None else 0
        sellable = sellable_snapshot.positions.get(symbol)
        available_qty = sellable.available_quantity if sellable is not None else 0
        target_qty = target_quantities.get(symbol, 0)
        if current_qty > target_qty:
            sell_qty = min(current_qty - target_qty, available_qty)
            if sell_qty <= 0:
                continue
            orders.append(
                PaperOrder(
                    order_id=stable_order_id(
                        side=OrderSide.SELL, symbol=symbol, rebalance_run_id=rebalance_run_id
                    ),
                    rebalance_run_id=rebalance_run_id,
                    account_id=account_id,
                    symbol=symbol,
                    side=OrderSide.SELL,
                    planned_quantity=sell_qty,
                    remaining_quantity=sell_qty,
                    reference_price_cny=close_prices[symbol],
                    status=OrderStatus.PENDING,
                )
            )

    projected_cash = snapshot.cash_cny
    for order in orders:
        if order.side == OrderSide.SELL:
            notional = money(order.planned_quantity * order.reference_price_cny)
            commission = _estimate_commission(notional, fee_config)
            stamp_tax = money(notional * fee_config.stamp_tax_rate)
            projected_cash += notional - commission - stamp_tax

    for symbol in symbols:
        current = snapshot.positions.get(symbol)
        current_qty = current.quantity if current is not None else 0
        target_qty = target_quantities.get(symbol, 0)
        if target_qty <= current_qty:
            continue
        buy_qty = ((target_qty - current_qty) // LOT_SIZE) * LOT_SIZE
        if buy_qty <= 0:
            continue
        price = close_prices[symbol]
        notional = money(buy_qty * price)
        commission = _estimate_commission(notional, fee_config)
        while buy_qty > 0 and projected_cash < notional + commission:
            buy_qty -= LOT_SIZE
            if buy_qty <= 0:
                break
            notional = money(buy_qty * price)
            commission = _estimate_commission(notional, fee_config)
        if buy_qty <= 0:
            continue
        projected_cash -= notional + commission
        orders.append(
            PaperOrder(
                order_id=stable_order_id(
                    side=OrderSide.BUY, symbol=symbol, rebalance_run_id=rebalance_run_id
                ),
                rebalance_run_id=rebalance_run_id,
                account_id=account_id,
                symbol=symbol,
                side=OrderSide.BUY,
                planned_quantity=buy_qty,
                remaining_quantity=buy_qty,
                reference_price_cny=price,
                status=OrderStatus.PENDING,
            )
        )

    return sorted(
        orders,
        key=lambda order: (0 if order.side == OrderSide.SELL else 1, order.symbol, order.order_id),
    )


class RebalancePlanner:
    def __init__(
        self,
        paper_repo: PaperRepository,
        *,
        market_repo: MarketDataRepository | None = None,
        fee_config: FeeConfig | None = None,
    ) -> None:
        self.paper_repo = paper_repo
        self.market_repo = market_repo
        self.fee_config = fee_config or FeeConfig()

    def plan(
        self,
        account_id: str,
        screen_run_id: str,
        *,
        config: ScreenerConfig,
        universe_hash: str,
        owner_id: str = "planner",
        force_revision: bool = False,
    ) -> RebalancePlan:
        frozen = self.paper_repo.get_frozen_screen_run(screen_run_id)
        target_weights = json.loads(frozen.target_weights_json)
        _validate_screen_run(
            status=frozen.status,
            target_mode=frozen.target_portfolio_mode,
            target_weights=target_weights,
            cash_weight=frozen.cash_weight,
        )

        signal_time = ensure_aware_shanghai(frozen.signal_time)
        signal_date = signal_time.date()
        if self.market_repo is None:
            raise InvalidScreenRun("market repository is required for planning")
        execution_date = next_execution_date(self.market_repo, signal_date)
        config_hash = config.stage4_config_hash()
        logical_run_key = compute_logical_run_key(
            account_id=account_id,
            signal_date=signal_date,
            execution_date=execution_date,
            universe_hash=universe_hash,
            config_hash=config_hash,
        )

        snapshot = self.paper_repo.load_account_snapshot(
            account_id,
            as_of_date=signal_date,
        )
        sellable_snapshot = self.paper_repo.load_account_snapshot(
            account_id,
            as_of_date=execution_date,
        )
        symbols = set(snapshot.positions) | set(target_weights)
        if frozen.target_portfolio_mode == TargetPortfolioMode.ALL_CASH:
            symbols = set(snapshot.positions)
        reference_prices = load_reference_closes(
            self.paper_repo,
            screen_run_id=screen_run_id,
            signal_date=signal_date,
            symbols=symbols,
        )
        target_hash = compute_target_hash(
            screen_content_hash=frozen.screen_content_hash,
            target_portfolio_mode=frozen.target_portfolio_mode,
            target_weights_json=frozen.target_weights_json,
            account_snapshot=snapshot,
            reference_prices=reference_prices,
        )

        active = self.paper_repo.get_active_rebalance_revision(logical_run_key)
        if active is not None:
            if active.screen_content_hash != frozen.screen_content_hash:
                if not force_revision:
                    raise RevisionConflict(
                        "screen content changed; use force_revision to create a new revision"
                    )
            elif active.target_hash == target_hash:
                return RebalancePlan(
                    rebalance_run_id=active.rebalance_run_id,
                    account_id=account_id,
                    screen_run_id=screen_run_id,
                    logical_run_key=logical_run_key,
                    target_hash=target_hash,
                    revision=active.revision,
                    signal_date=signal_date,
                    execution_date=execution_date,
                    orders=self.paper_repo.list_orders_for_rebalance(active.rebalance_run_id),
                    reference_prices=reference_prices,
                )
            if self.paper_repo.rebalance_has_fills(active.rebalance_run_id):
                raise RevisionConflict(
                    f"rebalance revision {active.rebalance_run_id} already has fills"
                )
            if not force_revision:
                raise RevisionConflict(
                    "target hash changed; use force_revision to create a new revision"
                )

        revision = 1 if active is None else active.revision + 1
        rebalance_run_id = stable_rebalance_run_id(logical_run_key, revision)
        orders = plan_orders(
            account_id=account_id,
            rebalance_run_id=rebalance_run_id,
            snapshot=snapshot,
            target_mode=frozen.target_portfolio_mode,
            target_weights=target_weights,
            close_prices=reference_prices,
            fee_config=self.fee_config,
            sellable_snapshot=sellable_snapshot,
        )
        spec = RebalanceRevisionSpec(
            rebalance_run_id=rebalance_run_id,
            account_id=account_id,
            screen_run_id=screen_run_id,
            screen_content_hash=frozen.screen_content_hash,
            target_hash=target_hash,
            signal_date=signal_date,
            signal_time=signal_time,
            execution_date=execution_date,
            universe_hash=universe_hash,
            config_hash=config_hash,
            strategy_version=STRATEGY_VERSION,
            target_weights_json=frozen.target_weights_json,
            logical_run_key=logical_run_key,
            revision=revision,
            status=RunStatus.PENDING,
        )
        lease = self.paper_repo.acquire_account_lease(account_id, owner_id=owner_id)
        self.paper_repo.create_rebalance_revision(
            spec,
            fencing_token=lease.token,
            owner_id=lease.owner_id,
        )
        if orders:
            self.paper_repo.insert_orders(
                orders,
                fencing_token=lease.token,
                owner_id=lease.owner_id,
            )
        self.paper_repo.expire_lease_for_test(account_id)

        return RebalancePlan(
            rebalance_run_id=rebalance_run_id,
            account_id=account_id,
            screen_run_id=screen_run_id,
            logical_run_key=logical_run_key,
            target_hash=target_hash,
            revision=revision,
            signal_date=signal_date,
            execution_date=execution_date,
            orders=orders,
            reference_prices=reference_prices,
        )
