"""Deterministic screening and fixture backtest pipeline."""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from pathlib import Path

import pandas as pd

from tradingagents.backtest.engine import BacktestEngine
from tradingagents.backtest.execution import ExecutionModel
from tradingagents.backtest.limits import enrich_bars_with_limits
from tradingagents.backtest.metrics import performance_metrics
from tradingagents.market_data.fixture_store import load_fixture_into_repository
from tradingagents.market_data.pit import require_pit_required
from tradingagents.market_data.repository import MarketDataRepository
from tradingagents.screener.config import ScreenerConfig
from tradingagents.screener.factors import compute_momentum, compute_quality, rank_score
from tradingagents.screener.portfolio import construct_portfolio
from tradingagents.screener.strategy import score_candidates


def _post_close_signal_time(signal_date: date) -> datetime:
    return datetime.combine(signal_date, time(15, 0), tzinfo=timezone.utc)


def _resolve_signal_date(trading_dates: list[date]) -> date:
    if len(trading_dates) < 2:
        raise ValueError("fixture requires at least two trading dates")
    return trading_dates[-2]


def _portfolio_target_weights(portfolio) -> dict[str, float]:
    invested = sum(position.market_value for position in portfolio.positions)
    if invested <= 0:
        return {}
    return {
        position.symbol: position.market_value / invested
        for position in portfolio.positions
    }


def run_fixture_backtest(
    fixture: dict,
    config: ScreenerConfig,
    db_path: Path,
) -> dict:
    require_pit_required(fixture.get("datasets", {}).get("daily_bars", "pit_required"), "daily_bars")
    require_pit_required(fixture.get("datasets", {}).get("financials", "pit_required"), "financials")

    repo = MarketDataRepository(db_path)
    load_fixture_into_repository(repo, fixture)

    bars_for_bt: dict[date, dict[str, dict]] = {}
    for trade_date_str, day_bars in fixture["bars"].items():
        bars_for_bt[date.fromisoformat(trade_date_str)] = day_bars

    trading_dates = sorted(bars_for_bt.keys())
    signal_date = _resolve_signal_date(trading_dates)
    signal_time = _post_close_signal_time(signal_date)

    symbols = [item["symbol"] for item in fixture["symbols"]]
    daily_rows = repo.get_daily_bars(symbols, end=signal_date, available_before=signal_time)
    financial_rows = repo.get_financials(symbols, available_before=signal_time)

    closes_by_symbol: dict[str, list[tuple[date, float]]] = {symbol: [] for symbol in symbols}
    last_bar_by_symbol: dict[str, dict] = {}
    for row in daily_rows:
        trade_date = row["trade_date"]
        if isinstance(trade_date, str):
            trade_date = date.fromisoformat(trade_date)
        closes_by_symbol[row["symbol"]].append((trade_date, row["close"]))
        last_bar_by_symbol[row["symbol"]] = row

    fin_by_symbol = {row["symbol"]: row for row in financial_rows}

    raw_momentum: dict[str, float] = {}
    raw_quality: dict[str, float] = {}
    rows = []
    for symbol_meta in fixture["symbols"]:
        symbol = symbol_meta["symbol"]
        industry = symbol_meta["industry"]
        history = sorted(closes_by_symbol.get(symbol, []), key=lambda item: item[0])
        if len(history) < 3:
            continue
        close_series = pd.Series(
            [value for _, value in history],
            index=pd.to_datetime([d.isoformat() for d, _ in history]),
        )
        signal_key = signal_date.isoformat()
        raw_momentum[symbol] = compute_momentum(close_series, signal_key, lookback=2)
        fin = fin_by_symbol.get(symbol)
        if fin:
            raw_quality[symbol] = compute_quality(
                fin["roe"], fin["operating_cashflow"], fin["net_profit"], fin["debt_ratio"]
            )
        else:
            raw_quality[symbol] = 0.0
        last_bar = last_bar_by_symbol[symbol]
        rows.append({
            "symbol": symbol,
            "industry": industry,
            "price": last_bar["close"],
            "avg_volume": last_bar["volume"],
        })

    if not rows:
        return {
            "metrics": {},
            "positions": 0,
            "orders": 0,
            "top_symbol": None,
            "ranking": [],
            "target_weights": {},
        }

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
        portfolio_value=config.portfolio.portfolio_value,
        max_positions=config.portfolio.max_positions,
        max_stock_weight=config.portfolio.max_stock_weight,
        max_industry_weight=config.portfolio.max_industry_weight,
        cash_buffer=config.portfolio.cash_buffer,
        max_participation_rate=config.portfolio.max_participation_rate,
    )

    target_weights = _portfolio_target_weights(portfolio)
    enriched_bars = enrich_bars_with_limits(bars_for_bt, fixture.get("symbols", []))

    engine = BacktestEngine(
        initial_cash=config.portfolio.portfolio_value,
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
    return {
        "metrics": metrics,
        "positions": len(portfolio.positions),
        "orders": len(result.orders),
        "top_symbol": ranking[0] if ranking else None,
        "ranking": ranking,
        "target_weights": {symbol: round(weight, 10) for symbol, weight in target_weights.items()},
    }
