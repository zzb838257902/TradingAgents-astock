"""Deterministic screening and fixture backtest pipeline."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from tradingagents.backtest.engine import BacktestEngine
from tradingagents.backtest.execution import ExecutionModel
from tradingagents.backtest.limits import enrich_bars_with_limits
from tradingagents.backtest.metrics import performance_metrics
from tradingagents.market_data.adjustments import (
    build_forward_adjusted_closes,
    latest_factor_on_or_before,
)
from tradingagents.market_data.contracts import PITLevel, PriceBasis
from tradingagents.market_data.fixture_store import load_fixture_into_repository
from tradingagents.market_data.market_hours import post_close_signal_time
from tradingagents.market_data.pit import require_pit_required
from tradingagents.market_data.repository import MarketDataRepository
from tradingagents.screener.config import ScreenerConfig
from tradingagents.screener.factors import compute_momentum, compute_quality, rank_score
from tradingagents.screener.models import CandidateInput
from tradingagents.screener.portfolio import construct_portfolio
from tradingagents.screener.report import (
    RunReport,
    ScreeningStatus,
    compute_industry_weights,
)
from tradingagents.screener.strategy import score_candidates
from tradingagents.screener.universe import filter_universe
from tradingagents.screener.universe_resolver import (
    UniverseRequest,
    UniverseResolver,
    UniverseType,
)


def _resolve_signal_date(trading_dates: list[date]) -> date:
    if len(trading_dates) < 2:
        raise ValueError("fixture requires at least two trading dates")
    return trading_dates[-2]


def _listing_trade_dates_for_screening(
    repo: MarketDataRepository,
    signal_date: date,
    min_listing_days: int,
) -> list[date] | None:
    """Read-only: screening must not fetch calendars over the network."""
    stored = sorted(day for day in repo.list_open_trade_dates() if day <= signal_date)
    if len(stored) > min_listing_days:
        return stored
    return None


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


def _collect_dataset_versions(repo: MarketDataRepository) -> dict[str, dict | None]:
    datasets = ("security_master", "daily_bars", "trade_calendar", "financials")
    return {name: repo.get_latest_published_version(name) for name in datasets}


def _collect_data_sources(fixture: dict, repo: MarketDataRepository, signal_time: datetime) -> dict[str, str]:
    sources: dict[str, str] = {}
    securities = repo.get_effective_securities_for_screening(signal_time.date(), signal_time)
    if securities:
        sources["security_master"] = securities[0].source
    symbol = None
    for item in fixture.get("symbols", []):
        symbol = item.get("symbol")
        if symbol:
            break
    if symbol:
        bars = repo.get_daily_bars(
            [symbol],
            end=signal_time.date(),
            available_before=signal_time,
        )
        if bars:
            sources["daily_bars"] = str(bars[-1].get("source", "unknown"))
        financials = repo.get_financials([symbol], available_before=signal_time)
        if financials:
            sources["financials"] = str(financials[-1].get("source", "unknown"))
    return sources


def _screening_pit_level(config: ScreenerConfig, resolved_pit_level: str) -> str:
    if config.strategy.price_basis == PriceBasis.RAW:
        return PITLevel.BEST_EFFORT.value
    return resolved_pit_level


def _data_quality(config: ScreenerConfig) -> dict[str, str | bool]:
    quality: dict[str, str | bool] = {"price_basis": config.strategy.price_basis.value}
    if config.strategy.price_basis == PriceBasis.RAW:
        quality["experimental_not_for_formal_evaluation"] = True
    return quality


def _base_report(
    *,
    run_id: str,
    signal_time: datetime,
    universe_request: UniverseRequest,
    universe_size: int = 0,
    pit_level: str = PITLevel.PIT_REQUIRED.value,
    dataset_versions: dict[str, dict | None] | None = None,
    data_sources: dict[str, str] | None = None,
    excluded_reasons: dict[str, list[str]] | None = None,
    status: ScreeningStatus = ScreeningStatus.OK,
    errors: list[str] | None = None,
    data_quality: dict | None = None,
) -> RunReport:
    excluded = excluded_reasons or {}
    return RunReport(
        run_id=run_id,
        status=status,
        signal_time=signal_time,
        data_as_of=signal_time,
        dataset_versions=dataset_versions or {},
        data_sources=data_sources or {},
        data_quality=data_quality or {},
        pit_level=pit_level,
        universe_type=universe_request.universe_type.value,
        universe_code=universe_request.universe_code,
        universe_size=universe_size,
        excluded_count=len(excluded),
        excluded_reasons=excluded,
        errors=errors or [],
    )


def run_screen(
    fixture: dict,
    config: ScreenerConfig,
    db_path: Path,
    *,
    reload: bool = True,
    universe_request: UniverseRequest | None = None,
    run_id: str | None = None,
) -> RunReport:
    run = run_id or str(uuid.uuid4())
    try:
        require_pit_required(fixture.get("datasets", {}).get("daily_bars", "pit_required"), "daily_bars")
        require_pit_required(fixture.get("datasets", {}).get("financials", "pit_required"), "financials")
    except ValueError as exc:
        trading_dates = sorted(
            date.fromisoformat(trade_date)
            for trade_date in fixture.get("bars", {})
        )
        signal_date = (
            _resolve_signal_date(trading_dates)
            if len(trading_dates) >= 2
            else None
        )
        signal_time = (
            post_close_signal_time(signal_date)
            if signal_date is not None
            else universe_request.as_of
            if universe_request and universe_request.as_of is not None
            else post_close_signal_time(date.today())
        )
        request = universe_request or UniverseRequest(
            universe_type=UniverseType.ALL,
            as_of=signal_time,
        )
        return _base_report(
            run_id=run,
            signal_time=signal_time,
            universe_request=request,
            status=ScreeningStatus.DATA_ERROR,
            errors=[str(exc)],
        )

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
    request = universe_request or UniverseRequest(
        universe_type=UniverseType.ALL,
        as_of=signal_time,
    )
    dataset_versions = _collect_dataset_versions(repo)
    data_sources = _collect_data_sources(fixture, repo, signal_time)
    data_quality = _data_quality(config)

    resolved = UniverseResolver(repo).resolve(request.model_copy(update={"as_of": signal_time}))
    pit_level = _screening_pit_level(
        config,
        resolved.pit_level.value if resolved.pit_level else PITLevel.PIT_REQUIRED.value,
    )
    if not resolved.is_ok:
        return _base_report(
            run_id=run,
            signal_time=signal_time,
            universe_request=request,
            universe_size=resolved.raw_member_count,
            pit_level=pit_level,
            dataset_versions=dataset_versions,
            data_sources=data_sources,
            status=ScreeningStatus.DATA_ERROR,
            errors=resolved.errors,
            data_quality=data_quality,
        )

    symbol_meta = {item["symbol"]: item for item in fixture["symbols"]}
    effective = repo.get_effective_securities_for_screening(signal_date, signal_time)
    allowed = set(resolved.symbols) if request.universe_type != UniverseType.ALL else None
    if allowed is not None:
        effective = [record for record in effective if record.symbol in allowed]
    universe_size = (
        resolved.raw_member_count
        if request.universe_type != UniverseType.ALL
        else len(effective)
    )

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

    listing_trade_dates = _listing_trade_dates_for_screening(
        repo,
        signal_date,
        config.universe.min_listing_days,
    )
    if listing_trade_dates is None:
        return _base_report(
            run_id=run,
            signal_time=signal_time,
            universe_request=request,
            universe_size=universe_size,
            pit_level=pit_level,
            dataset_versions=dataset_versions,
            data_sources=data_sources,
            status=ScreeningStatus.DATA_ERROR,
            errors=[
                "trade_calendar in repository is shorter than min_listing_days; "
                "sync trade-calendar before screening"
            ],
            data_quality=data_quality,
        )

    universe = filter_universe(
        candidates,
        as_of=signal_date,
        min_listing_days=config.universe.min_listing_days,
        min_avg_amount_20d=config.universe.min_avg_amount_20d,
        trading_dates=trading_dates,
        listing_trade_dates=listing_trade_dates,
    )
    if not universe.included:
        return _base_report(
            run_id=run,
            signal_time=signal_time,
            universe_request=request,
            universe_size=universe_size,
            pit_level=pit_level,
            dataset_versions=dataset_versions,
            data_sources=data_sources,
            excluded_reasons=universe.excluded_reasons,
            status=ScreeningStatus.EMPTY_UNIVERSE,
            data_quality=data_quality,
        )

    included_symbols = {item.symbol for item in universe.included}
    financial_rows = repo.get_financials(list(included_symbols), available_before=signal_time)
    fin_by_symbol = {row["symbol"]: row for row in financial_rows}

    factor_rows_by_symbol: dict[str, list[dict]] = {}
    if config.strategy.price_basis == PriceBasis.FORWARD_ADJUSTED:
        for row in repo.get_adjustment_factors(
            list(included_symbols),
            end=signal_date,
            available_before=signal_time,
        ):
            factor_rows_by_symbol.setdefault(row["symbol"], []).append(row)
        covered = {
            symbol
            for symbol in included_symbols
            if latest_factor_on_or_before(
                factor_rows_by_symbol.get(symbol, []),
                signal_date,
                signal_time,
            )
            is not None
        }
        missing = sorted(included_symbols - covered)
        if missing:
            return _base_report(
                run_id=run,
                signal_time=signal_time,
                universe_request=request,
                universe_size=universe_size,
                pit_level=pit_level,
                dataset_versions=dataset_versions,
                data_sources=data_sources,
                excluded_reasons=universe.excluded_reasons,
                status=ScreeningStatus.DATA_ERROR,
                errors=[
                    "forward_adjusted requires adjustment factor coverage for all included symbols; "
                    f"missing: {', '.join(missing)}"
                ],
                data_quality=data_quality,
            )

    raw_momentum: dict[str, float] = {}
    raw_quality: dict[str, float] = {}
    rows = []
    industry_by_symbol: dict[str, str] = {}
    for item in universe.included:
        symbol = item.symbol
        history = sorted(daily_by_symbol[symbol], key=lambda row: _parse_trade_date(row["trade_date"]))
        if len(history) < 3:
            continue
        trade_dates = [_parse_trade_date(row["trade_date"]) for row in history]
        if config.strategy.price_basis == PriceBasis.FORWARD_ADJUSTED:
            closes = build_forward_adjusted_closes(
                history,
                factor_rows_by_symbol.get(symbol, []),
                signal_date,
                signal_time,
            )
        else:
            closes = [row["close"] for row in history]
        close_series = pd.Series(
            closes,
            index=pd.to_datetime([day.isoformat() for day in trade_dates]),
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
        return _base_report(
            run_id=run,
            signal_time=signal_time,
            universe_request=request,
            universe_size=universe_size,
            pit_level=pit_level,
            dataset_versions=dataset_versions,
            data_sources=data_sources,
            excluded_reasons=universe.excluded_reasons,
            status=ScreeningStatus.EMPTY_UNIVERSE,
            data_quality=data_quality,
        )

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
    rounded_weights = {symbol: round(weight, 10) for symbol, weight in target_weights.items()}
    cash_weight = round(portfolio.cash / portfolio_value, 10)
    enriched_bars = enrich_bars_with_limits(bars_for_bt, fixture.get("symbols", []))

    engine = BacktestEngine(
        initial_cash=portfolio_value,
        execution=ExecutionModel(commission_rate=0.0003, stamp_tax_rate=0.0005),
        delisting_recovery_rate=0.20,
    )
    delistings = {
        date.fromisoformat(k): v for k, v in fixture.get("delistings", {}).items()
    }
    backtest = engine.run(
        bars=enriched_bars,
        target_weights={signal_date: target_weights},
        delistings=delistings,
    )
    equity = pd.Series(
        [point.equity for point in backtest.equity_curve],
        index=[point.trade_date for point in backtest.equity_curve],
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
        for order in backtest.orders
    ]
    factor_contributions = {
        symbol: {
            "momentum": float(momentum_scores.get(symbol, 0.0)),
            "quality": float(quality_scores.get(symbol, 0.0)),
        }
        for symbol in ranking
    }
    industry_weights = compute_industry_weights(rounded_weights, industry_by_symbol)

    return RunReport(
        run_id=run,
        status=ScreeningStatus.OK,
        signal_time=signal_time,
        data_as_of=signal_time,
        dataset_versions=dataset_versions,
        data_sources=data_sources,
        data_quality=data_quality,
        pit_level=pit_level,
        universe_type=request.universe_type.value,
        universe_code=request.universe_code,
        universe_size=universe_size,
        included_count=len(universe.included),
        excluded_count=len(universe.excluded_reasons),
        excluded_reasons=universe.excluded_reasons,
        ranking=ranking,
        factor_contributions=factor_contributions,
        target_weights=rounded_weights,
        cash_weight=cash_weight,
        industry_by_symbol=industry_by_symbol,
        industry_weights=industry_weights,
        orders=orders,
        metrics=metrics,
        positions=len(portfolio.positions),
        top_symbol=ranking[0] if ranking else None,
    )


def run_fixture_backtest(
    fixture: dict,
    config: ScreenerConfig,
    db_path: Path,
    *,
    reload: bool = True,
    universe_request: UniverseRequest | None = None,
) -> dict:
    report = run_screen(
        fixture,
        config,
        db_path,
        reload=reload,
        universe_request=universe_request,
    )
    if report.status == ScreeningStatus.DATA_ERROR:
        raise ValueError("; ".join(report.errors) or "screening data error")
    return report.to_legacy_dict()
