"""CLI for local after-close scheduler jobs."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Optional

import typer

from tradingagents.paper.config import PaperPaths
from tradingagents.paper.jobs import run_open_job, run_paper_after_close_job
from tradingagents.paper.recovery import recover_paper_run
from tradingagents.paper.repository import PaperRepository
from tradingagents.market_data.config import MarketDataPaths
from tradingagents.market_data.fixture_store import load_fixture_into_repository
from tradingagents.market_data.providers.factory import create_resolved_provider
from tradingagents.market_data.repository import MarketDataRepository
from tradingagents.market_data.sync import MarketDataSync
from tradingagents.scheduler.jobs import config_hash, load_fixture_file, run_after_close, universe_hash
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
    provider: Optional[str] = typer.Option(None, "--provider"),
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
    fixture_data = load_fixture_file(fixture) if fixture else None
    if fixture_data is not None:
        load_fixture_into_repository(repo, fixture_data)
    resolved_provider = (
        "fixture"
        if fixture_data is not None and provider is None
        else provider
    )
    sync = MarketDataSync(
        repo,
        create_resolved_provider(
            cli_provider=resolved_provider,
            home_dir=home_dir,
            fixture=fixture_data,
        ),
        paths,
    )
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


def _build_universe_request(
    trade_date: date,
    *,
    universe: str,
    universe_code: Optional[str],
    symbols: Optional[str],
) -> UniverseRequest:
    custom_symbols = tuple(item.strip() for item in (symbols or "").split(",") if item.strip())
    return UniverseRequest(
        universe_type=UniverseType(universe),
        universe_code=universe_code,
        symbols=custom_symbols,
        as_of=__import__(
            "tradingagents.market_data.market_hours",
            fromlist=["post_close_signal_time"],
        ).post_close_signal_time(trade_date),
    )


def _setup_market_sync(
    home_dir: Path,
    fixture: Optional[Path],
    provider: Optional[str],
) -> tuple[MarketDataPaths, MarketDataRepository, MarketDataSync, dict | None]:
    paths = MarketDataPaths(home_dir=home_dir)
    repo = MarketDataRepository(paths.live_db_path, snapshot_dir=paths.snapshot_dir)
    fixture_data = load_fixture_file(fixture) if fixture else None
    if fixture_data is not None:
        load_fixture_into_repository(repo, fixture_data)
    resolved_provider = (
        "fixture"
        if fixture_data is not None and provider is None
        else provider
    )
    sync = MarketDataSync(
        repo,
        create_resolved_provider(
            cli_provider=resolved_provider,
            home_dir=home_dir,
            fixture=fixture_data,
        ),
        paths,
    )
    return paths, repo, sync, fixture_data


@app.command("run-open")
def run_open(
    trade_date: str = typer.Option(..., "--trade-date"),
    account_id: str = typer.Option(..., "--account-id"),
    home_dir: Path = typer.Option(Path("~/.tradingagents").expanduser(), "--home-dir"),
    config_path: Optional[Path] = typer.Option(None, "--config"),
    fixture: Optional[Path] = typer.Option(None, "--fixture"),
    provider: Optional[str] = typer.Option(None, "--provider"),
    universe: str = typer.Option("all", "--universe"),
    universe_code: Optional[str] = typer.Option(None, "--universe-code"),
    symbols: Optional[str] = typer.Option(None, "--symbols"),
) -> None:
    """Run the paper opening orchestrator for pending T+1 orders."""
    target = date.fromisoformat(trade_date)
    config = (
        ScreenerConfig.from_yaml(config_path)
        if config_path
        else ScreenerConfig(home_dir=home_dir)
    )
    request = _build_universe_request(
        target,
        universe=universe,
        universe_code=universe_code,
        symbols=symbols,
    )
    paths, market_repo, sync, _fixture_data = _setup_market_sync(home_dir, fixture, provider)
    paper_repo = PaperRepository(PaperPaths(home_dir=home_dir))
    try:
        result = run_open_job(
            paper_repo,
            account_id=account_id,
            trade_date=target,
            config=config,
            universe_hash=universe_hash(request),
            market_repo=market_repo,
            sync=sync,
        )
        typer.echo(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        raise typer.Exit(code=result.exit_code)
    finally:
        paper_repo.close()


@app.command("run-after-close")
def run_paper_after_close(
    trade_date: str = typer.Option(..., "--trade-date"),
    account_id: str = typer.Option(..., "--account-id"),
    home_dir: Path = typer.Option(Path("~/.tradingagents").expanduser(), "--home-dir"),
    config_path: Optional[Path] = typer.Option(None, "--config"),
    fixture: Optional[Path] = typer.Option(None, "--fixture"),
    provider: Optional[str] = typer.Option(None, "--provider"),
    universe: str = typer.Option("all", "--universe"),
    universe_code: Optional[str] = typer.Option(None, "--universe-code"),
    symbols: Optional[str] = typer.Option(None, "--symbols"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    """Run the paper after-close orchestrator."""
    target = date.fromisoformat(trade_date)
    config = (
        ScreenerConfig.from_yaml(config_path)
        if config_path
        else ScreenerConfig(home_dir=home_dir)
    )
    request = _build_universe_request(
        target,
        universe=universe,
        universe_code=universe_code,
        symbols=symbols,
    )
    paths, market_repo, sync, fixture_data = _setup_market_sync(home_dir, fixture, provider)
    paper_repo = PaperRepository(PaperPaths(home_dir=home_dir))
    try:
        result = run_paper_after_close_job(
            paper_repo,
            account_id=account_id,
            trade_date=target,
            config=config,
            universe_hash=universe_hash(request),
            market_repo=market_repo,
            sync=sync,
            universe_request=request,
            fixture=fixture_data,
            force=force,
            paths=paths,
        )
        typer.echo(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        raise typer.Exit(code=result.exit_code)
    finally:
        paper_repo.close()


@app.command("recover")
def recover_cmd(
    trade_date: str = typer.Option(..., "--trade-date"),
    account_id: str = typer.Option(..., "--account-id"),
    job_type: str = typer.Option("open", "--job-type"),
    home_dir: Path = typer.Option(Path("~/.tradingagents").expanduser(), "--home-dir"),
    config_path: Optional[Path] = typer.Option(None, "--config"),
    fixture: Optional[Path] = typer.Option(None, "--fixture"),
    provider: Optional[str] = typer.Option(None, "--provider"),
    universe: str = typer.Option("all", "--universe"),
    universe_code: Optional[str] = typer.Option(None, "--universe-code"),
    symbols: Optional[str] = typer.Option(None, "--symbols"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    """Recover a blocked or partially completed paper scheduler run."""
    target = date.fromisoformat(trade_date)
    config = (
        ScreenerConfig.from_yaml(config_path)
        if config_path
        else ScreenerConfig(home_dir=home_dir)
    )
    request = _build_universe_request(
        target,
        universe=universe,
        universe_code=universe_code,
        symbols=symbols,
    )
    paths, market_repo, sync, fixture_data = _setup_market_sync(home_dir, fixture, provider)
    paper_repo = PaperRepository(PaperPaths(home_dir=home_dir))
    try:
        result = recover_paper_run(
            paper_repo,
            account_id=account_id,
            trade_date=target,
            config=config,
            universe_hash=universe_hash(request),
            job_type=job_type,
            market_repo=market_repo,
            sync=sync,
            universe_request=request,
            fixture=fixture_data,
            force=force,
            paths=paths,
        )
        typer.echo(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        raise typer.Exit(code=result.exit_code)
    finally:
        paper_repo.close()


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
        target = date.fromisoformat(trade_date)
        universe_request = UniverseRequest(
            universe_type=UniverseType("all"),
            as_of=__import__(
                "tradingagents.market_data.market_hours",
                fromlist=["post_close_signal_time"],
            ).post_close_signal_time(target),
        )
        key = JobKey(
            job_name,
            target,
            config_hash(config),
            universe_hash(universe_request),
        )
        typer.echo(json.dumps({
            "run": store.load_run(key),
            "report": store.load_report(key),
        }, ensure_ascii=False, indent=2))
        return
    typer.echo(json.dumps(store.list_runs(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    app()
