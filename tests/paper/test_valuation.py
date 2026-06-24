"""Daily valuation tests (Stage 6A Task 5)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from tradingagents.market_data.repository import MarketDataRepository
from tradingagents.paper.contracts import PositionEntry, PositionSourceType
from tradingagents.paper.exceptions import PaperError
from tradingagents.paper.repository import PaperRepository
from tradingagents.paper.valuation import MarkToMarketService, ValuationStatus
from tests.paper.conftest import (
    append_position_with_lease,
    rebuild_projection_with_lease,
    seed_demo_account,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")


def market_repo(tmp_path) -> MarketDataRepository:
    return MarketDataRepository(tmp_path / "market.duckdb")


def seed_bar(
    repo: MarketDataRepository,
    *,
    symbol: str,
    trade_date: date,
    close: float,
    available_at: datetime | None = None,
) -> None:
    available = available_at or datetime.combine(
        trade_date, datetime.min.time(), tzinfo=SHANGHAI
    ).replace(hour=15)
    repo.upsert_daily_bars(
        [
            {
                "symbol": symbol,
                "trade_date": trade_date,
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": 1_000_000,
                "amount": close * 1_000_000,
                "available_at": available,
                "source": "fixture",
            }
        ]
    )


def seed_position(
    paper_repo: PaperRepository,
    *,
    symbol: str = "600000",
    quantity: int = 1000,
    effective_date: date,
    cost_cny: Decimal = Decimal("10000.00"),
    as_of_date: date | None = None,
) -> None:
    append_position_with_lease(
        paper_repo,
        PositionEntry(
            position_entry_id=f"pos-{symbol}",
            account_id="demo",
            symbol=symbol,
            quantity_delta=quantity,
            cost_delta_cny=cost_cny,
            effective_date=effective_date,
            source_type=PositionSourceType.ADJUSTMENT,
            source_id="seed",
            component="QUANTITY",
            business_key=f"demo:ADJUSTMENT:seed-{symbol}:QUANTITY",
        ),
    )
    rebuild_projection_with_lease(
        paper_repo,
        as_of_date=as_of_date or (effective_date + timedelta(days=1)),
    )
    paper_repo.expire_lease_for_test("demo")


def value_day(
    paper_repo: PaperRepository,
    market: MarketDataRepository,
    valuation_date: date,
    *,
    run_id: str = "val-run",
) -> MarkToMarketService:
    service = MarkToMarketService(paper_repo, market, owner_id="val-test")
    available_before = datetime.combine(
        valuation_date, datetime.min.time(), tzinfo=SHANGHAI
    ).replace(hour=16)
    lease = paper_repo.acquire_account_lease("demo", owner_id="val-test")
    return service.value_account(
        "demo",
        valuation_date=valuation_date,
        available_before=available_before,
        run_id=run_id,
        fencing_token=lease.token,
        owner_id=lease.owner_id,
    )


def test_valuation_equity_equals_cash_plus_positions(repo, tmp_path):
    seed_demo_account(repo)
    market = market_repo(tmp_path)
    trade_date = date(2026, 6, 23)
    seed_bar(market, symbol="600000", trade_date=trade_date, close=10.5)
    seed_position(repo, effective_date=trade_date)
    result = value_day(repo, market, trade_date)
    assert result.status == ValuationStatus.OK
    assert result.nav.total_equity_cny == money_sum(
        result.nav.cash_cny, result.nav.positions_value_cny
    )


def test_missing_price_returns_data_error(repo, tmp_path):
    seed_demo_account(repo)
    market = market_repo(tmp_path)
    seed_position(repo, effective_date=date(2026, 6, 23))
    with pytest.raises(PaperError, match="DATA_ERROR"):
        value_day(repo, market, date(2026, 6, 23))


def test_suspended_uses_stale_price(repo, tmp_path):
    seed_demo_account(repo)
    market = market_repo(tmp_path)
    last_trade = date(2026, 6, 20)
    val_date = date(2026, 6, 23)
    seed_bar(market, symbol="600000", trade_date=last_trade, close=9.8)
    market.upsert_suspension_events(
        [
            {
                "symbol": "600000",
                "start_date": date(2026, 6, 21),
                "end_date": None,
                "reason": "停牌",
                "available_at": datetime(2026, 6, 21, 9, 0, tzinfo=SHANGHAI),
                "source": "fixture",
            }
        ]
    )
    seed_position(repo, effective_date=date(2026, 6, 19))
    result = value_day(repo, market, val_date)
    assert result.status == ValuationStatus.OK
    assert result.sources[0].price_status == "STALE_SUSPENDED_PRICE"
    assert result.nav.positions_value_cny == Decimal("9800.00")


def test_five_day_nav_returns_cumulative_and_drawdown(repo, tmp_path):
    seed_demo_account(repo, account_id="demo", initial_cash=Decimal("10000.00"))
    market = market_repo(tmp_path)
    closes = [10.0, 10.5, 10.2, 9.8, 10.1]
    dates = [date(2026, 6, 19), date(2026, 6, 20), date(2026, 6, 23), date(2026, 6, 24), date(2026, 6, 25)]
    for trade_date, close in zip(dates, closes):
        seed_bar(market, symbol="600000", trade_date=trade_date, close=close)
    seed_position(repo, effective_date=dates[0], quantity=1000)

    navs = []
    for trade_date in dates:
        navs.append(value_day(repo, market, trade_date, run_id=f"val-{trade_date}").nav)

    assert navs[0].daily_return is None
    assert navs[0].cumulative_return == Decimal("0")
    assert navs[0].drawdown == Decimal("0")

    assert navs[1].daily_return == Decimal("0.0250000000")
    assert navs[1].cumulative_return == Decimal("0.0250000000")
    assert navs[1].drawdown == Decimal("0")

    assert navs[2].daily_return == Decimal("-0.0146341463")
    assert navs[2].drawdown == Decimal("-0.0146341463")

    assert navs[3].daily_return == Decimal("-0.0198019802")
    assert navs[3].drawdown == Decimal("-0.0341463415")

    assert navs[4].daily_return == Decimal("0.0151515152")
    assert navs[4].cumulative_return == Decimal("0.0050000000")
    assert navs[4].drawdown == Decimal("-0.0195121951")


def test_valuation_captures_run_inputs(repo, tmp_path):
    seed_demo_account(repo)
    market = market_repo(tmp_path)
    trade_date = date(2026, 6, 23)
    seed_bar(market, symbol="600000", trade_date=trade_date, close=10.0)
    seed_position(repo, effective_date=trade_date)
    value_day(repo, market, trade_date, run_id="capture-run")
    row = repo.connection.execute(
        """
        SELECT COUNT(*)
        FROM paper_run_inputs
        WHERE run_id = ? AND input_type = 'VALUATION_PRICE'
        """,
        ["capture-run"],
    ).fetchone()
    assert row[0] == 1


def money_sum(left: Decimal, right: Decimal) -> Decimal:
    from tradingagents.paper.contracts import money

    return money(left + right)
