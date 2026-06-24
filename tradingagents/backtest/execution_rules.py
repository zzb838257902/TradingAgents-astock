"""Stateless A-share execution sizing and limit rules."""

from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal

LOT_SIZE = 100
_MONEY_QUANTUM = Decimal("0.01")


def _money(value: Decimal | int | float | str) -> Decimal:
    return Decimal(value).quantize(_MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def round_down_to_lot(quantity: int, lot_size: int = LOT_SIZE) -> int:
    if quantity <= 0:
        return 0
    return (quantity // lot_size) * lot_size


def participation_cap_shares(
    cumulative_volume: float | int,
    max_participation_rate: float,
    lot_size: int = LOT_SIZE,
) -> int:
    volume = float(cumulative_volume)
    if volume <= 0:
        return 0
    return int(math.floor(volume * max_participation_rate / lot_size)) * lot_size


def is_one_word_limit_up(
    open_price: float,
    high: float,
    low: float,
    close: float,
    limit_up: float,
) -> bool:
    return open_price == high == low == close == limit_up


def is_one_word_limit_down(
    open_price: float,
    high: float,
    low: float,
    close: float,
    limit_down: float,
) -> bool:
    return open_price == high == low == close == limit_down


def conservative_buy_limit_reject(open_cny: float, upper_limit_cny: float) -> bool:
    return open_cny >= upper_limit_cny


def conservative_sell_limit_reject(open_cny: float, lower_limit_cny: float) -> bool:
    return open_cny <= lower_limit_cny


def cap_requested_quantity(
    *,
    requested_quantity: int,
    sellable_shares: int,
    is_sell: bool,
    cumulative_volume_shares: float | int,
    max_participation_rate: float,
    lot_size: int = LOT_SIZE,
) -> int:
    quantity = requested_quantity
    if is_sell:
        quantity = min(quantity, sellable_shares)
        if quantity <= 0:
            return 0
    cap = participation_cap_shares(
        cumulative_volume_shares,
        max_participation_rate,
        lot_size,
    )
    quantity = min(quantity, cap)
    if not is_sell:
        quantity = round_down_to_lot(quantity, lot_size)
    return quantity


def resize_buy_for_cash(
    quantity: int,
    *,
    price: Decimal,
    available_cash: Decimal,
    commission_rate: Decimal,
    minimum_commission: Decimal,
    lot_size: int = LOT_SIZE,
) -> int:
    qty = round_down_to_lot(quantity, lot_size)
    while qty > 0:
        notional = _money(price * qty)
        commission = max(_money(notional * commission_rate), minimum_commission)
        if available_cash >= notional + commission:
            return qty
        qty -= lot_size
    return 0
