"""CLI for market data initialization and synchronization."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import typer

from tradingagents.market_data.config import MarketDataPaths
from tradingagents.market_data.providers.factory import create_resolved_provider
from tradingagents.market_data.repository import MarketDataRepository
from tradingagents.market_data.sync import MarketDataSync
from tradingagents.market_data.market_hours import market_open_observed_at
from tradingagents.market_data.sync_policy import shanghai_today

app = typer.Typer(help="TradingAgents market data sync")


def _paths(home_dir: Path) -> MarketDataPaths:
    return MarketDataPaths(home_dir=home_dir.expanduser())


def _sync(home_dir: Path, provider: str | None = None) -> MarketDataSync:
    paths = _paths(home_dir)
    repo = MarketDataRepository(paths.live_db_path, snapshot_dir=paths.snapshot_dir)
    resolved = create_resolved_provider(cli_provider=provider, home_dir=home_dir)
    return MarketDataSync(repo, resolved, paths)


@app.command("init")
def init_market_data(
    home_dir: Path = typer.Option(Path("~/.tradingagents"), "--home-dir"),
    provider: Optional[str] = typer.Option(None, "--provider"),
) -> None:
    """Initialize live market database schema and run capability probe."""
    paths = _paths(home_dir)
    MarketDataRepository(paths.live_db_path, snapshot_dir=paths.snapshot_dir)
    result = _sync(home_dir, provider).probe_capabilities()
    typer.echo(json.dumps({
        "live_db": str(paths.live_db_path),
        "provider": provider or "resolved-default",
        "probe_status": result.status.value,
        "errors": result.errors,
    }, ensure_ascii=False))


@app.command("probe")
def probe_capabilities(
    home_dir: Path = typer.Option(Path("~/.tradingagents"), "--home-dir"),
    provider: Optional[str] = typer.Option(None, "--provider"),
) -> None:
    """Run provider capability probe and persist results."""
    result = _sync(home_dir, provider).probe_capabilities()
    typer.echo(json.dumps({
        "status": result.status.value,
        "errors": result.errors,
    }, ensure_ascii=False))


def _parse_symbols(symbols: str | None) -> list[str] | None:
    if not symbols:
        return None
    return [part.strip() for part in symbols.split(",") if part.strip()]


@app.command("sync")
def sync_dataset(
    dataset: str = typer.Option(..., "--dataset"),
    start: Optional[str] = typer.Option(None, "--start"),
    end: Optional[str] = typer.Option(None, "--end"),
    as_of: Optional[str] = typer.Option(None, "--as-of"),
    symbols: Optional[str] = typer.Option(
        None, "--symbols", help="comma-separated symbols for smoke/backfill sync"
    ),
    board_type: Optional[str] = typer.Option(None, "--board-type"),
    board_code: Optional[str] = typer.Option(None, "--board-code"),
    home_dir: Path = typer.Option(Path("~/.tradingagents"), "--home-dir"),
    provider: Optional[str] = typer.Option(None, "--provider"),
) -> None:
    """Synchronize a dataset into the live repository."""
    sync = _sync(home_dir, provider)
    symbol_list = _parse_symbols(symbols)
    if dataset in {"security-master", "security_master"}:
        target = date.fromisoformat(as_of or date.today().isoformat())
        result = sync.sync_security_master(target, symbols=symbol_list)
    elif dataset in {"trade-calendar", "trade_calendar"}:
        if not start or not end:
            raise typer.BadParameter("trade-calendar requires --start and --end")
        result = sync.sync_trade_calendar(date.fromisoformat(start), date.fromisoformat(end))
    elif dataset == "daily":
        if start and end:
            result = sync.sync_daily_backfill(
                date.fromisoformat(start),
                date.fromisoformat(end),
                symbols=symbol_list,
            )
        else:
            trade_date = date.fromisoformat(start or as_of or date.today().isoformat())
            result = sync.sync_daily(trade_date)
    elif dataset in {"daily-indicators", "daily_indicators"}:
        trade_date = date.fromisoformat(start or as_of or shanghai_today().isoformat())
        result = sync.sync_daily_indicators(trade_date)
    elif dataset in {"market-open-snapshots", "market_open_snapshots"}:
        trade_date = date.fromisoformat(start or as_of or shanghai_today().isoformat())
        if not symbols:
            raise typer.BadParameter("market-open-snapshots requires --symbols")
        observed_raw = end or as_of
        observed_at = (
            datetime.fromisoformat(observed_raw)
            if observed_raw
            else market_open_observed_at(trade_date)
        )
        result = sync.sync_market_open_snapshots(
            _parse_symbols(symbols),
            trade_date,
            observed_at,
        )
    elif dataset == "memberships":
        if not as_of or not board_type or not board_code:
            raise typer.BadParameter(
                "memberships requires --as-of, --board-type and --board-code"
            )
        signal_time = datetime.fromisoformat(as_of)
        result = sync.sync_board_memberships(board_type, board_code, signal_time)
    elif dataset in {"adjustment-factors", "adjustment_factors"}:
        target = date.fromisoformat(as_of or date.today().isoformat())
        result = sync.sync_adjustment_factors(symbol_list, as_of=target)
    elif dataset == "financials":
        if not as_of:
            raise typer.BadParameter("financials requires --as-of")
        result = sync.sync_financials(datetime.fromisoformat(as_of), symbols=symbol_list)
    elif dataset in {
        "events",
        "market_events",
        "announcements",
        "official_announcements",
    }:
        if not start or not end:
            raise typer.BadParameter("events requires --start and --end")
        if not symbol_list:
            raise typer.BadParameter("events requires --symbols")
        from tradingagents.events.service import EventSyncService

        paths = _paths(home_dir)
        repo = MarketDataRepository(paths.live_db_path, snapshot_dir=paths.snapshot_dir)
        provider = create_resolved_provider(cli_provider=provider, home_dir=home_dir)
        service = EventSyncService(repo, provider, paths)
        sync_result = service.sync_announcements(
            symbol_list,
            date.fromisoformat(start),
            date.fromisoformat(end),
            as_of=datetime.fromisoformat(as_of) if as_of else None,
        )
        typer.echo(json.dumps({
            "dataset": sync_result.dataset,
            "status": sync_result.status.value,
            "run_id": sync_result.run_id,
            "version_id": sync_result.version_id,
            "errors": sync_result.errors,
            "dedup_stats": (
                None if sync_result.dedup_stats is None else sync_result.dedup_stats.__dict__
            ),
        }, ensure_ascii=False, default=str))
        return
    else:
        raise typer.BadParameter(f"unsupported dataset: {dataset}")
    typer.echo(json.dumps({
        "dataset": result.dataset,
        "status": result.status.value,
        "run_id": result.run_id,
        "version_id": result.version_id,
        "content_hash": result.content_hash,
        "errors": result.errors,
        "coverage_reports": {
            key: report.to_dict() for key, report in result.coverage_reports.items()
        },
    }, ensure_ascii=False, default=str))


@app.command("status")
def sync_status(
    home_dir: Path = typer.Option(Path("~/.tradingagents"), "--home-dir"),
) -> None:
    """Show latest published dataset versions and capability probe."""
    paths = _paths(home_dir)
    repo = MarketDataRepository(paths.live_db_path, snapshot_dir=paths.snapshot_dir)
    typer.echo(json.dumps({
        "live_db": str(paths.live_db_path),
        "security_master": repo.get_latest_published_version("security_master"),
        "trade_calendar": repo.get_latest_published_version("trade_calendar"),
        "daily_bars": repo.get_latest_published_version("daily_bars"),
        "daily_indicators": repo.get_latest_published_version("daily_indicators"),
        "market_open_snapshots": repo.get_latest_published_version("market_open_snapshots"),
        "capability_probe": repo.get_capability_probe(),
    }, ensure_ascii=False, default=str))


if __name__ == "__main__":
    app()
