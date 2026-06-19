"""CLI for automatic stock screening MVP."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd
import typer
import yaml

from tradingagents.backtest.engine import BacktestEngine
from tradingagents.backtest.execution import ExecutionModel
from tradingagents.backtest.metrics import performance_metrics
from tradingagents.market_data.repository import MarketDataRepository
from tradingagents.screener.config import ScreenerConfig
from tradingagents.screener.factors import compute_momentum, compute_quality, rank_score
from tradingagents.screener.portfolio import construct_portfolio
from tradingagents.screener.strategy import score_candidates

app = typer.Typer(help="TradingAgents automatic stock screening MVP")


def _config_hash(config: ScreenerConfig) -> str:
    payload = config.model_dump_json()
    return hashlib.sha256(payload.encode()).hexdigest()


def _fixture_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@app.command("data-health")
def data_health() -> None:
    """Validate and print the data capability matrix."""
    matrix_path = Path("docs/data/data-capability-matrix.yaml")
    matrix = yaml.safe_load(matrix_path.read_text(encoding="utf-8"))
    for name, definition in sorted(matrix["datasets"].items()):
        typer.echo(f"{name}: {definition['pit_level']} ({definition['source']})")


@app.command("init-db")
def init_db(home_dir: Path = typer.Option(Path("~/.tradingagents").expanduser(), "--home-dir")) -> None:
    """Create or migrate the configured DuckDB file."""
    db_path = home_dir / "data" / "market.duckdb"
    MarketDataRepository(db_path)
    typer.echo(f"initialized {db_path}")


def _load_fixture(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _run_fixture_backtest(fixture: dict, config: ScreenerConfig) -> dict:
    rows = []
    for symbol_meta in fixture["symbols"]:
        symbol = symbol_meta["symbol"]
        industry = symbol_meta["industry"]
        closes = []
        dates = []
        last_bar = None
        for trade_date_str, day_bars in sorted(fixture["bars"].items()):
            if symbol not in day_bars:
                continue
            last_bar = day_bars[symbol]
            dates.append(trade_date_str)
            closes.append(day_bars[symbol]["close"])
        if len(closes) < 3:
            continue
        close_series = pd.Series(closes, index=pd.to_datetime(dates))
        signal_date = dates[-1]
        momentum = rank_score(pd.Series({symbol: compute_momentum(close_series, signal_date, 2)})).iloc[0]
        fin = next((f for f in fixture["financials"] if f["symbol"] == symbol), None)
        if fin:
            quality_raw = compute_quality(
                fin["roe"], fin["operating_cashflow"], fin["net_profit"], fin["debt_ratio"]
            )
        else:
            quality_raw = 0.0
        rows.append({
            "symbol": symbol,
            "industry": industry,
            "momentum": momentum,
            "quality": quality_raw,
            "price": closes[-1],
            "avg_volume": last_bar["volume"],
        })

    if not rows:
        return {"metrics": {}, "positions": 0}

    frame = pd.DataFrame(rows)
    frame["quality"] = rank_score(frame["quality"])
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

    bars_for_bt: dict[date, dict[str, dict]] = {}
    for trade_date_str, day_bars in fixture["bars"].items():
        bars_for_bt[date.fromisoformat(trade_date_str)] = day_bars

    sorted_dates = sorted(bars_for_bt.keys())
    signal_date = sorted_dates[-2]
    targets = {signal_date: {p.symbol: 1.0 / len(portfolio.positions) for p in portfolio.positions}}

    engine = BacktestEngine(
        initial_cash=config.portfolio.portfolio_value,
        execution=ExecutionModel(commission_rate=0.0003, stamp_tax_rate=0.0005),
        delisting_recovery_rate=0.20,
    )
    delistings = {
        date.fromisoformat(k): v for k, v in fixture.get("delistings", {}).items()
    }
    result = engine.run(bars=bars_for_bt, target_weights=targets, delistings=delistings)
    equity = pd.Series(
        [point.equity for point in result.equity_curve],
        index=[point.trade_date for point in result.equity_curve],
    )
    metrics = performance_metrics(equity) if len(equity) > 1 else {}
    return {
        "metrics": metrics,
        "positions": len(portfolio.positions),
        "orders": len(result.orders),
        "top_symbol": scored.iloc[0]["symbol"] if len(scored) else None,
    }


@app.command("backtest-fixture")
def backtest_fixture(
    fixture: Path = typer.Option(..., "--fixture"),
    home_dir: Path = typer.Option(Path("~/.tradingagents").expanduser(), "--home-dir"),
    config_path: Optional[Path] = typer.Option(None, "--config"),
) -> None:
    """Load a deterministic fixture and print sorted JSON metrics."""
    config = (
        ScreenerConfig.from_yaml(config_path)
        if config_path
        else ScreenerConfig(home_dir=home_dir)
    )
    fixture_data = _load_fixture(fixture)
    output = {
        "config_hash": _config_hash(config),
        "fixture_sha256": _fixture_sha256(fixture),
        "home_dir": str(config.home_dir),
        **_run_fixture_backtest(fixture_data, config),
    }
    typer.echo(json.dumps(output, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    app()
