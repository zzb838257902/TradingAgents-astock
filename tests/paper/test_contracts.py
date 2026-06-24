"""Paper ledger contract tests (Stage 6A Task 1)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from tradingagents.market_data.contracts import MarketOpenSnapshot, QuoteStatus
from tradingagents.paper.contracts import (
    MONEY_QUANTUM,
    PaperAccount,
    TargetPortfolioMode,
    money,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")


def test_open_snapshot_contains_only_observed_fields():
    row = MarketOpenSnapshot(
        symbol="600000",
        trade_date=date(2026, 6, 23),
        observed_at=datetime(2026, 6, 23, 9, 35, tzinfo=SHANGHAI),
        open_cny=10.12,
        prev_close_cny=10.00,
        last_cny=10.15,
        cumulative_volume_shares=1_200_000,
        quote_status=QuoteStatus.TRADING,
        upper_limit_cny=11.00,
        lower_limit_cny=9.00,
        source="fixture",
        available_at=datetime(2026, 6, 23, 9, 35, tzinfo=SHANGHAI),
    )
    assert not hasattr(row, "close")
    assert row.cumulative_volume_shares == 1_200_000


def test_paper_money_uses_decimal():
    account = PaperAccount(
        account_id="demo",
        name="Demo",
        initial_cash_cny=Decimal("1000000.00"),
    )
    assert isinstance(account.initial_cash_cny, Decimal)


def test_money_quantizes_to_cny_cents():
    assert money(Decimal("1.005")) == Decimal("1.01")
    assert money("1000000") == Decimal("1000000.00")
    assert MONEY_QUANTUM == Decimal("0.01")


def test_target_portfolio_mode_values():
    assert TargetPortfolioMode.WEIGHTS == "weights"
    assert TargetPortfolioMode.ALL_CASH == "all_cash"


def test_market_open_snapshot_rejects_extra_fields():
    with pytest.raises(ValidationError):
        MarketOpenSnapshot(
            symbol="600000",
            trade_date=date(2026, 6, 23),
            observed_at=datetime(2026, 6, 23, 9, 35, tzinfo=SHANGHAI),
            open_cny=10.12,
            prev_close_cny=10.00,
            last_cny=10.15,
            cumulative_volume_shares=1_200_000,
            quote_status=QuoteStatus.TRADING,
            upper_limit_cny=11.00,
            lower_limit_cny=9.00,
            source="fixture",
            available_at=datetime(2026, 6, 23, 9, 35, tzinfo=SHANGHAI),
            close=10.20,
        )
