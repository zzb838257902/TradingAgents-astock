"""Paper portfolio fee calculations."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from tradingagents.paper.contracts import OrderSide, money

DEFAULT_COMMISSION_RATE = Decimal("0.0003")
DEFAULT_STAMP_TAX_RATE = Decimal("0.0005")
DEFAULT_MINIMUM_COMMISSION_CNY = Decimal("5.00")


@dataclass(frozen=True)
class FeeConfig:
    commission_rate: Decimal = DEFAULT_COMMISSION_RATE
    stamp_tax_rate: Decimal = DEFAULT_STAMP_TAX_RATE
    minimum_commission_cny: Decimal = DEFAULT_MINIMUM_COMMISSION_CNY


@dataclass(frozen=True)
class FeeBreakdown:
    commission: Decimal
    stamp_tax: Decimal


def calculate_fees(
    notional: Decimal,
    side: OrderSide,
    config: FeeConfig,
) -> FeeBreakdown:
    commission = max(
        money(notional * config.commission_rate),
        config.minimum_commission_cny,
    )
    stamp_tax = (
        money(notional * config.stamp_tax_rate)
        if side == OrderSide.SELL
        else Decimal("0.00")
    )
    return FeeBreakdown(commission=commission, stamp_tax=stamp_tax)
