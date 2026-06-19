"""CLI for local after-close scheduler jobs."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Optional

import typer

from tradingagents.market_data.config import MarketDataPaths
from tradingagents.market_data.providers.tushare import TushareProvider
from tradingagents.market_data.repository import MarketDataRepository
from tradingagents.market_data.sync import MarketDataSync
from tradingagents.scheduler.jobs import config_hash, load_fixture_file, run_after_close
from tradingagents.scheduler.state import JobKey, JobStateStore
from tradingagents.screener.config import ScreenerConfig
from tradingagents.screener.universe_resolver import UniverseRequest, UniverseType

app = typer.Typer(help="TradingAgents local after-close scheduler")


@app.command("after-close")
def after_close(
    trade_date: str = typer.Option(..., "--trade-date"),
    home_dir: Path = typer.Option(Path("~/.tradingagents").expanduser(), "--home-dir"),
    config_path: Optional[Path] = typer.Option(None, "--config"),
    fixture: Optional[Path] = typer.Option(None, "--fixture"),
    universe: str = typer.Option("all", "--universe"),
    universe_code: Optional[str] = typer.Option(None, "--universe-code"),
    symbols: Optional[str] = typer.Option(None, "--symbols"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    """Run post-close sync, screening, and persist a run report."""
    paths = MarketDataPaths(home_dir=home_dir)
    config = (
        ScreenerConfig.from_yaml(config_path)
        if config_path
        else ScreenerConfig(home_dir=home_dir)
    )
    repo = MarketDataRepository(paths.live_db_path, snapshot_dir=paths.snapshot_dir)
    sync = MarketDataSync(repo, TushareProvider(), paths)
    target = date.fromisoformat(trade_date)
    custom_symbols = tuple(item.strip() for item in (symbols or "").split(",") if item.strip())
    universe_request = UniverseRequest(
        universe_type=UniverseType(universe),
        universe_code=universe_code,
        symbols=custom_symbols,
        as_of=__import__(
            "tradingagents.market_data.market_hours",
            fromlist=["post_close_signal_time"],
        ).post_close_signal_time(target),
    )
    fixture_data = load_fixture_file(fixture) if fixture else None
    result = run_after_close(
        target,
        config,
        paths,
        sync,
        universe_request=universe_request,
        fixture=fixture_data,
        force=force,
    )
    payload = {
        "job_key": result.job_key.storage_id(),
        "status": result.status,
        "skipped": result.skipped,
        "sync_steps": result.sync_steps,
        "errors": result.errors,
        "report": result.report.to_output_dict() if result.report else None,
    }
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    if result.status == "error":
        raise typer.Exit(code=1)


@app.command("status")
def job_status(
    job_name: str = typer.Option("after_close", "--job-name"),
    trade_date: Optional[str] = typer.Option(None, "--trade-date"),
    home_dir: Path = typer.Option(Path("~/.tradingagents").expanduser(), "--home-dir"),
    config_path: Optional[Path] = typer.Option(None, "--config"),
) -> None:
    """List scheduler runs or fetch a specific job report."""
    paths = MarketDataPaths(home_dir=home_dir)
    config = (
        ScreenerConfig.from_yaml(config_path)
        if config_path
        else ScreenerConfig(home_dir=home_dir)
    )
    store = JobStateStore(paths.home_dir / "scheduler")
    if trade_date:
        key = JobKey(job_name, date.fromisoformat(trade_date), config_hash(config))
        typer.echo(json.dumps({
            "run": store.load_run(key),
            "report": store.load_report(key),
        }, ensure_ascii=False, indent=2))
        return
    typer.echo(json.dumps(store.list_runs(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    app()
