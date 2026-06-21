"""Build a screening fixture slice from repository data."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from tradingagents.market_data.market_hours import ensure_aware_shanghai, post_close_signal_time
from tradingagents.market_data.repository import MarketDataRepository
from tradingagents.market_data.sync_policy import shanghai_today
from tradingagents.screener.config import ScreenerConfig
from tradingagents.screener.pipeline import run_screen
from tradingagents.screener.report import RunReport, ScreeningStatus
from tradingagents.screener.universe_resolver import UniverseRequest, UniverseResolver


def bar_history_trading_dates(
    repo: MarketDataRepository,
    trade_date: date,
    config: ScreenerConfig,
) -> list[date]:
    count = max(30, config.universe.min_listing_days + 1, 20)
    open_dates = [day for day in repo.list_open_trade_dates() if day <= trade_date]
    if not open_dates:
        return []
    return open_dates[-count:]


def resolve_signal_trade_date(
    repo: MarketDataRepository,
    *,
    as_of: str | None,
    today: date | None = None,
) -> tuple[date | None, datetime | None, list[str]]:
    reference_today = today or shanghai_today()
    open_dates = repo.list_open_trade_dates()
    if not open_dates:
        return None, None, ["repository has no trade calendar data"]

    if as_of:
        signal_time = ensure_aware_shanghai(datetime.fromisoformat(as_of))
        trade_date = signal_time.date()
        if trade_date > reference_today:
            return None, None, [
                f"explicit as_of date {trade_date.isoformat()} is after today "
                f"{reference_today.isoformat()}",
            ]
        if trade_date not in open_dates:
            return None, None, [
                f"{trade_date.isoformat()} is not an open trade date in repository",
            ]
        return trade_date, signal_time, []

    eligible = [day for day in open_dates if day <= reference_today]
    if not eligible:
        return None, None, [
            f"no open trade date on or before {reference_today.isoformat()}",
        ]
    trade_date = eligible[-1]
    return trade_date, post_close_signal_time(trade_date), []


def _data_error_report(
    *,
    signal_time: datetime,
    universe_request: UniverseRequest,
    errors: list[str],
) -> RunReport:
    return RunReport(
        run_id=str(uuid.uuid4()),
        status=ScreeningStatus.DATA_ERROR,
        signal_time=signal_time,
        data_as_of=signal_time,
        universe_type=universe_request.universe_type.value,
        universe_code=universe_request.universe_code,
        errors=errors,
    )


def run_repository_screen(
    repo: MarketDataRepository,
    config: ScreenerConfig,
    db_path,
    universe_request: UniverseRequest,
    *,
    trade_date: date,
    signal_time: datetime,
) -> RunReport:
    request = universe_request.model_copy(update={"as_of": signal_time})
    trading_dates = bar_history_trading_dates(repo, trade_date, config)
    if len(trading_dates) < 2:
        return _data_error_report(
            signal_time=signal_time,
            universe_request=request,
            errors=["repository fixture slice requires at least two trading dates"],
        )

    resolved = UniverseResolver(repo).resolve(request)
    if not resolved.is_ok:
        return _data_error_report(
            signal_time=signal_time,
            universe_request=request,
            errors=resolved.errors,
        )
    if not resolved.symbols:
        return _data_error_report(
            signal_time=signal_time,
            universe_request=request,
            errors=["resolved universe is empty"],
        )

    try:
        fixture = build_fixture_from_repository(
            repo,
            resolved.symbols,
            trading_dates,
            signal_time,
        )
    except ValueError as exc:
        return _data_error_report(
            signal_time=signal_time,
            universe_request=request,
            errors=[str(exc)],
        )

    if len(fixture.get("bars", {})) < 2:
        return _data_error_report(
            signal_time=signal_time,
            universe_request=request,
            errors=["insufficient published bar history for screening"],
        )

    return run_screen(
        fixture,
        config,
        db_path,
        reload=False,
        universe_request=request,
    )


def build_fixture_from_repository(
    repo: MarketDataRepository,
    symbols: list[str],
    trading_dates: list[date],
    signal_time: datetime,
) -> dict:
    if len(trading_dates) < 2:
        raise ValueError("repository fixture slice requires at least two trading dates")
    bars: dict[str, dict] = {}
    for trade_date in trading_dates:
        available = post_close_signal_time(trade_date)
        if available > signal_time:
            continue
        day: dict[str, dict] = {}
        for row in repo.get_daily_bars(
            symbols,
            start=trade_date,
            end=trade_date,
            available_before=signal_time,
        ):
            day[row["symbol"]] = {
                "open": row["open"],
                "high": row["high"],
                "low": row["low"],
                "close": row["close"],
                "volume": row["volume"],
                "amount": row.get("amount", row["close"] * row["volume"]),
            }
        if day:
            bars[trade_date.isoformat()] = day

    securities = repo.get_effective_securities(signal_time.date(), signal_time)
    symbol_set = set(symbols)
    industry_labels = repo.get_symbol_industry_labels(
        symbols,
        signal_time.date(),
        signal_time,
    )
    financial_rows = repo.get_financials(symbols, available_before=signal_time)
    financials = [
        {
            "symbol": row["symbol"],
            "report_period": row["report_period"],
            "roe": row["roe"],
            "operating_cashflow": row["operating_cashflow"],
            "net_profit": row["net_profit"],
            "debt_ratio": row["debt_ratio"],
            "announcement_date": row["announcement_date"].isoformat()
            if hasattr(row["announcement_date"], "isoformat")
            else row["announcement_date"],
            "available_at": row["available_at"].isoformat()
            if hasattr(row["available_at"], "isoformat")
            else row["available_at"],
        }
        for row in financial_rows
    ]
    return {
        "version": 1,
        "datasets": {"daily_bars": "pit_required", "financials": "pit_required"},
        "symbols": [
            {
                "symbol": record.symbol,
                "industry": industry_labels.get(record.symbol, "未知"),
                "list_date": record.list_date.isoformat(),
            }
            for record in securities
            if record.symbol in symbol_set
        ],
        "bars": bars,
        "financials": financials,
    }
