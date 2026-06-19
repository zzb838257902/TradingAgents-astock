"""Deterministic screening and fixture backtest pipeline."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from tradingagents.backtest.engine import BacktestEngine
from tradingagents.backtest.execution import ExecutionModel
from tradingagents.backtest.limits import enrich_bars_with_limits
from tradingagents.backtest.metrics import performance_metrics
from tradingagents.market_data.fixture_store import load_fixture_into_repository
from tradingagents.market_data.market_hours import post_close_signal_time
from tradingagents.market_data.pit import require_pit_required
from tradingagents.market_data.repository import MarketDataRepository
from tradingagents.screener.config import ScreenerConfig
from tradingagents.screener.factors import compute_momentum, compute_quality, rank_score
from tradingagents.screener.models import CandidateInput
from tradingagents.screener.portfolio import construct_portfolio
from tradingagents.screener.strategy import score_candidates
from tradingagents.screener.universe import filter_universe


def _resolve_signal_date(trading_dates: list[date]) -> date:
    if len(trading_dates) < 2:
        raise ValueError("fixture requires at least two trading dates")
    return trading_dates[-2]


def _portfolio_target_weights(portfolio, portfolio_value: float) -> dict[str, float]:
    if portfolio_value <= 0:
        return {}
    return {
        position.symbol: position.market_value / portfolio_value
        for position in portfolio.positions
    }


def _parse_trade_date(value: date | str) -> date:
    if isinstance(value, str):
        return date.fromisoformat(value)
    return value


def _avg_amount_20d(rows: list[dict], signal_date: date) -> float:
    amounts: list[float] = []
    for row in sorted(rows, key=lambda item: _parse_trade_date(item["trade_date"])):
        trade_date = _parse_trade_date(row["trade_date"])
        if trade_date <= signal_date:
            amounts.append(float(row.get("amount", row["close"] * row["volume"])))
    window = amounts[-20:]
    return sum(window) / len(window) if window else 0.0


def _empty_result(excluded_reasons: dict[str, list[str]] | None = None) -> dict:
    return {
        "metrics": {},
        "positions": 0,
        "orders": [],
        "top_symbol": None,
        "ranking": [],
        "target_weights": {},
        "cash_weight": 1.0,
        "excluded_reasons": excluded_reasons or {},
        "industry_by_symbol": {},
    }


def run_fixture_backtest(
    fixture: dict,
    config: ScreenerConfig,
    db_path: Path,
    *,
    reload: bool = True,
) -> dict:
    require_pit_required(fixture.get("datasets", {}).get("daily_bars", "pit_required"), "daily_bars")
    require_pit_required(fixture.get("datasets", {}).get("financials", "pit_required"), "financials")

    repo = MarketDataRepository(db_path)
    if reload:
        load_fixture_into_repository(repo, fixture)

    bars_for_bt: dict[date, dict[str, dict]] = {}
    for trade_date_str, day_bars in fixture["bars"].items():
        bars_for_bt[date.fromisoformat(trade_date_str)] = day_bars

    trading_dates = sorted(bars_for_bt.keys())
    signal_date = _resolve_signal_date(trading_dates)
    signal_time = post_close_signal_time(signal_date)
    portfolio_value = config.portfolio.portfolio_value

    symbol_meta = {item["symbol"]: item for item in fixture["symbols"]}
    effective = repo.get_effective_securities(signal_date, signal_time)

    all_symbols = [record.symbol for record in effective]
    daily_by_symbol: dict[str, list[dict]] = {symbol: [] for symbol in all_symbols}
    for row in repo.get_daily_bars(all_symbols, end=signal_date, available_before=signal_time):
        daily_by_symbol[row["symbol"]].append(row)

    signal_day_bars = bars_for_bt.get(signal_date, {})
    candidates: list[CandidateInput] = []
    for record in effective:
        symbol = record.symbol
        meta = symbol_meta.get(symbol, {})
        industry = meta.get("industry", "未知")
        history = daily_by_symbol.get(symbol, [])
        if not history:
            continue
        signal_bar = signal_day_bars.get(symbol, history[-1])
        suspended = repo.is_suspended_on(symbol, signal_date, signal_time) or bool(
            signal_bar.get("suspended", signal_bar.get("volume", 0) <= 0)
        )
        st_flag = repo.is_st_on(
            symbol,
            signal_date,
            signal_time,
            fallback=record.st_flag,
        )
        candidates.append(CandidateInput(
            symbol=symbol,
            name=record.name,
            industry=industry,
            list_date=record.list_date,
            st_flag=st_flag,
            suspended=suspended,
            avg_amount_20d=_avg_amount_20d(history, signal_date),
        ))

    universe = filter_universe(
        candidates,
        as_of=signal_date,
        min_listing_days=config.universe.min_listing_days,
        min_avg_amount_20d=config.universe.min_avg_amount_20d,
        trading_dates=trading_dates,
    )
    if not universe.included:
        return _empty_result(universe.excluded_reasons)

    included_symbols = {item.symbol for item in universe.included}
    financial_rows = repo.get_financials(list(included_symbols), available_before=signal_time)
    fin_by_symbol = {row["symbol"]: row for row in financial_rows}

    raw_momentum: dict[str, float] = {}
    raw_quality: dict[str, float] = {}
    rows = []
    industry_by_symbol: dict[str, str] = {}
    for item in universe.included:
        symbol = item.symbol
        history = sorted(daily_by_symbol[symbol], key=lambda row: _parse_trade_date(row["trade_date"]))
        closes = [row["close"] for row in history]
        if len(closes) < 3:
            continue
        close_series = pd.Series(
            closes,
            index=pd.to_datetime([_parse_trade_date(row["trade_date"]).isoformat() for row in history]),
        )
        signal_key = signal_date.isoformat()
        raw_momentum[symbol] = compute_momentum(close_series, signal_key, lookback=2)
        fin = fin_by_symbol.get(symbol)
        raw_quality[symbol] = (
            compute_quality(fin["roe"], fin["operating_cashflow"], fin["net_profit"], fin["debt_ratio"])
            if fin else 0.0
        )
        last_bar = history[-1]
        industry_by_symbol[symbol] = item.industry
        rows.append({
            "symbol": symbol,
            "industry": item.industry,
            "price": last_bar["close"],
            "avg_volume": last_bar["volume"],
        })

    if not rows:
        return _empty_result(universe.excluded_reasons)

    momentum_scores = rank_score(pd.Series(raw_momentum))
    quality_scores = rank_score(pd.Series(raw_quality))

    frame = pd.DataFrame(rows)
    frame["momentum"] = frame["symbol"].map(momentum_scores)
    frame["quality"] = frame["symbol"].map(quality_scores)

    scored = score_candidates(
        frame,
        momentum_weight=config.strategy.momentum_weight,
        quality_weight=config.strategy.quality_weight,
    )
    portfolio = construct_portfolio(
        scored.rename(columns={"ensemble_score": "score"}),
        portfolio_value=portfolio_value,
        max_positions=config.portfolio.max_positions,
        max_stock_weight=config.portfolio.max_stock_weight,
        max_industry_weight=config.portfolio.max_industry_weight,
        cash_buffer=config.portfolio.cash_buffer,
        max_participation_rate=config.portfolio.max_participation_rate,
    )

    target_weights = _portfolio_target_weights(portfolio, portfolio_value)
    cash_weight = portfolio.cash / portfolio_value
    enriched_bars = enrich_bars_with_limits(bars_for_bt, fixture.get("symbols", []))

    engine = BacktestEngine(
        initial_cash=portfolio_value,
        execution=ExecutionModel(commission_rate=0.0003, stamp_tax_rate=0.0005),
        delisting_recovery_rate=0.20,
    )
    delistings = {
        date.fromisoformat(k): v for k, v in fixture.get("delistings", {}).items()
    }
    result = engine.run(
        bars=enriched_bars,
        target_weights={signal_date: target_weights},
        delistings=delistings,
    )
    equity = pd.Series(
        [point.equity for point in result.equity_curve],
        index=[point.trade_date for point in result.equity_curve],
    )
    metrics = performance_metrics(equity) if len(equity) > 1 else {}
    ranking = scored.sort_values("ensemble_score", ascending=False)["symbol"].tolist()
    ranking = [symbol for symbol in ranking if symbol in included_symbols]
    orders = [
        {
            "symbol": order.symbol,
            "shares": order.shares,
            "price": order.price,
            "trade_date": order.trade_date.isoformat(),
        }
        for order in result.orders
    ]
    return {
        "metrics": metrics,
        "positions": len(portfolio.positions),
        "orders": orders,
        "top_symbol": ranking[0] if ranking else None,
        "ranking": ranking,
        "target_weights": {symbol: round(weight, 10) for symbol, weight in target_weights.items()},
        "cash_weight": round(cash_weight, 10),
        "excluded_reasons": universe.excluded_reasons,
        "industry_by_symbol": industry_by_symbol,
    }
