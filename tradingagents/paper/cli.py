"""CLI for paper portfolio operations (Stage 6A)."""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import typer

from tradingagents.market_data.config import MarketDataPaths
from tradingagents.market_data.contracts import CorporateActionRecord
from tradingagents.market_data.fixture_store import load_fixture_into_repository
from tradingagents.market_data.providers.factory import create_resolved_provider
from tradingagents.market_data.repository import MarketDataRepository
from tradingagents.market_data.sync import MarketDataSync
from tradingagents.paper.config import PaperPaths
from tradingagents.paper.contracts import RunStatus
from tradingagents.paper.corporate_actions import CorporateActionProcessor
from tradingagents.paper.exceptions import AccountNotFound, PaperError
from tradingagents.paper.jobs import run_open_job
from tradingagents.paper.planner import RebalancePlanner
from tradingagents.paper.reporting import (
    PaperReportRun,
    PaperReportWriter,
    build_report_run_from_rebalance,
)
from tradingagents.paper.repository import PaperRepository
from tradingagents.paper.valuation import MarkToMarketService, ValuationStatus
from tradingagents.scheduler.jobs import load_fixture_file, universe_hash
from tradingagents.screener.config import ScreenerConfig
from tradingagents.screener.universe_resolver import UniverseRequest, UniverseType

SHANGHAI = ZoneInfo("Asia/Shanghai")

app = typer.Typer(help="TradingAgents paper portfolio operations")


def _load_config(home_dir: Path, config_path: Optional[Path]) -> ScreenerConfig:
    if config_path is not None:
        return ScreenerConfig.from_yaml(config_path)
    return ScreenerConfig(home_dir=home_dir)


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
) -> tuple[MarketDataRepository, MarketDataSync, dict | None]:
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
    return repo, sync, fixture_data


def _exit_for_status(status: RunStatus | str) -> int:
    if isinstance(status, RunStatus):
        if status in {RunStatus.COMPLETED, RunStatus.COMPLETED_WITH_REJECTIONS}:
            return 0
        if status == RunStatus.BLOCKED:
            return 2
        return 1
    if status in {"completed", "completed_with_rejections", "ok"}:
        return 0
    if status == "blocked":
        return 2
    return 1


def _paper_repo(home_dir: Path) -> PaperRepository:
    return PaperRepository(PaperPaths(home_dir=home_dir))


def _status_payload(paper_repo: PaperRepository, account_id: str) -> dict[str, Any]:
    snapshot = paper_repo.load_account_snapshot(account_id)
    orders = paper_repo.list_orders(account_id)[-20:]
    recent_runs = paper_repo.list_recent_run_ids(account_id, limit=5)
    run_summaries: list[dict[str, Any]] = []
    for run_id in recent_runs:
        steps = paper_repo.list_run_steps(run_id)
        run_summaries.append(
            {
                "run_id": run_id,
                "steps": {step.step_name: step.status.value for step in steps},
            }
        )
    return {
        "account_id": account_id,
        "account_status": snapshot.account.status.value,
        "cash_cny": str(snapshot.cash_cny),
        "positions": {
            symbol: {
                "quantity": projection.quantity,
                "available_quantity": projection.available_quantity,
                "average_cost_cny": str(projection.average_cost_cny),
                "market_value_cny": str(projection.market_value_cny),
            }
            for symbol, projection in sorted(snapshot.positions.items())
        },
        "recent_orders": [
            {
                "order_id": order.order_id,
                "symbol": order.symbol,
                "side": order.side.value,
                "status": order.status.value,
                "remaining_quantity": order.remaining_quantity,
            }
            for order in orders
        ],
        "recent_runs": run_summaries,
    }


def _apply_close_corporate_actions(
    paper_repo: PaperRepository,
    market_repo: MarketDataRepository,
    *,
    account_id: str,
    trade_date: date,
    owner_id: str,
) -> list[str]:
    observed_at = datetime(
        trade_date.year,
        trade_date.month,
        trade_date.day,
        16,
        0,
        tzinfo=SHANGHAI,
    )
    snapshot = paper_repo.load_account_snapshot(account_id, as_of_date=trade_date)
    symbols = sorted(snapshot.positions)
    if not symbols:
        return []
    rows = market_repo.get_corporate_actions(
        symbols,
        end=trade_date,
        available_before=observed_at,
    )
    processor = CorporateActionProcessor(
        paper_repo,
        account_id=account_id,
        owner_id=owner_id,
    )
    applied: list[str] = []
    for row in rows:
        action = CorporateActionRecord.model_validate(row)
        if action.pay_date != trade_date and action.ex_date != trade_date:
            continue
        result = processor.apply(action)
        applied.append(result.corporate_action_id)
    return applied


@app.command("init")
def init_account(
    account_id: str = typer.Option(..., "--account-id"),
    initial_cash: str = typer.Option("1000000.00", "--initial-cash"),
    home_dir: Path = typer.Option(Path("~/.tradingagents").expanduser(), "--home-dir"),
    name: Optional[str] = typer.Option(None, "--name"),
) -> None:
    """Initialize a paper trading account."""
    paper_repo = _paper_repo(home_dir)
    try:
        account = paper_repo.create_account(
            account_id,
            Decimal(initial_cash),
            name=name,
        )
        payload = {
            "account_id": account.account_id,
            "initial_cash_cny": str(account.initial_cash_cny),
            "status": account.status.value,
        }
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    finally:
        paper_repo.close()


@app.command("plan")
def plan_rebalance(
    account_id: str = typer.Option(..., "--account-id"),
    screen_run_id: str = typer.Option(..., "--screen-run-id"),
    home_dir: Path = typer.Option(Path("~/.tradingagents").expanduser(), "--home-dir"),
    config_path: Optional[Path] = typer.Option(None, "--config"),
    universe: str = typer.Option("all", "--universe"),
    universe_code: Optional[str] = typer.Option(None, "--universe-code"),
    symbols: Optional[str] = typer.Option(None, "--symbols"),
    force_revision: bool = typer.Option(False, "--force-revision"),
) -> None:
    """Generate a rebalance plan from a frozen screen run."""
    config = _load_config(home_dir, config_path)
    request = _build_universe_request(
        date.today(),
        universe=universe,
        universe_code=universe_code,
        symbols=symbols,
    )
    paper_repo = _paper_repo(home_dir)
    market_paths = MarketDataPaths(home_dir=home_dir)
    market_repo = MarketDataRepository(
        market_paths.live_db_path,
        snapshot_dir=market_paths.snapshot_dir,
    )
    try:
        frozen = paper_repo.get_frozen_screen_run(screen_run_id)
        request = _build_universe_request(
            frozen.signal_time.date(),
            universe=universe,
            universe_code=universe_code,
            symbols=symbols,
        )
        planner = RebalancePlanner(paper_repo, market_repo=market_repo)
        plan = planner.plan(
            account_id,
            screen_run_id,
            config=config,
            universe_hash=universe_hash(request),
            owner_id="paper-plan",
            force_revision=force_revision,
        )
        payload = {
            "rebalance_run_id": plan.rebalance_run_id,
            "logical_run_key": plan.logical_run_key,
            "revision": plan.revision,
            "execution_date": plan.execution_date.isoformat(),
            "order_count": len(plan.orders),
            "orders": [
                {
                    "order_id": order.order_id,
                    "symbol": order.symbol,
                    "side": order.side.value,
                    "planned_quantity": order.planned_quantity,
                }
                for order in plan.orders
            ],
        }
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    except PaperError as exc:
        typer.echo(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        raise typer.Exit(code=1) from exc
    finally:
        paper_repo.close()
        market_repo.connection.close()


@app.command("execute")
def execute_orders(
    account_id: str = typer.Option(..., "--account-id"),
    trade_date: str = typer.Option(..., "--trade-date"),
    home_dir: Path = typer.Option(Path("~/.tradingagents").expanduser(), "--home-dir"),
    config_path: Optional[Path] = typer.Option(None, "--config"),
    fixture: Optional[Path] = typer.Option(None, "--fixture"),
    provider: Optional[str] = typer.Option(None, "--provider"),
    universe: str = typer.Option("all", "--universe"),
    universe_code: Optional[str] = typer.Option(None, "--universe-code"),
    symbols: Optional[str] = typer.Option(None, "--symbols"),
) -> None:
    """Execute pending T+1 orders for a trade date."""
    target = date.fromisoformat(trade_date)
    config = _load_config(home_dir, config_path)
    request = _build_universe_request(
        target,
        universe=universe,
        universe_code=universe_code,
        symbols=symbols,
    )
    market_repo, sync, _fixture_data = _setup_market_sync(home_dir, fixture, provider)
    paper_repo = _paper_repo(home_dir)
    try:
        result = run_open_job(
            paper_repo,
            account_id=account_id,
            trade_date=target,
            config=config,
            universe_hash=universe_hash(request),
            market_repo=market_repo,
            sync=sync,
            owner_id="paper-execute",
        )
        typer.echo(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        raise typer.Exit(code=result.exit_code)
    finally:
        paper_repo.close()
        market_repo.connection.close()


@app.command("close")
def close_day(
    account_id: str = typer.Option(..., "--account-id"),
    trade_date: str = typer.Option(..., "--trade-date"),
    home_dir: Path = typer.Option(Path("~/.tradingagents").expanduser(), "--home-dir"),
    fixture: Optional[Path] = typer.Option(None, "--fixture"),
    provider: Optional[str] = typer.Option(None, "--provider"),
) -> None:
    """Apply corporate actions and run end-of-day valuation."""
    target = date.fromisoformat(trade_date)
    market_repo, _sync, _fixture_data = _setup_market_sync(home_dir, fixture, provider)
    paper_repo = _paper_repo(home_dir)
    owner_id = "paper-close"
    try:
        lease = paper_repo.acquire_account_lease(account_id, owner_id=owner_id)
        applied = _apply_close_corporate_actions(
            paper_repo,
            market_repo,
            account_id=account_id,
            trade_date=target,
            owner_id=owner_id,
        )
        service = MarkToMarketService(paper_repo, market_repo, owner_id=owner_id)
        valuation = service.value_account(
            account_id,
            valuation_date=target,
            available_before=datetime(
                target.year,
                target.month,
                target.day,
                16,
                0,
                tzinfo=SHANGHAI,
            ),
            run_id=f"close:{account_id}:{target.isoformat()}",
            fencing_token=lease.token,
            owner_id=owner_id,
        )
        status = "completed" if valuation.status == ValuationStatus.OK else "data_error"
        payload = {
            "account_id": account_id,
            "trade_date": target.isoformat(),
            "corporate_actions_applied": applied,
            "valuation_status": valuation.status.value,
            "total_equity_cny": str(valuation.nav.total_equity_cny),
            "status": status,
        }
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        raise typer.Exit(code=_exit_for_status(status))
    except PaperError as exc:
        typer.echo(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        raise typer.Exit(code=1) from exc
    finally:
        paper_repo.close()
        market_repo.connection.close()


@app.command("status")
def status(
    account_id: str = typer.Option(..., "--account-id"),
    home_dir: Path = typer.Option(Path("~/.tradingagents").expanduser(), "--home-dir"),
) -> None:
    """Read-only account, order, and recent run status."""
    paper_repo = _paper_repo(home_dir)
    try:
        payload = _status_payload(paper_repo, account_id)
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    except AccountNotFound as exc:
        typer.echo(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        raise typer.Exit(code=1) from exc
    finally:
        paper_repo.close()


@app.command("report")
def report(
    account_id: str = typer.Option(..., "--account-id"),
    trade_date: str = typer.Option(..., "--trade-date"),
    logical_run_key: str = typer.Option(..., "--logical-run-key"),
    revision: int = typer.Option(..., "--revision"),
    rebalance_run_id: Optional[str] = typer.Option(None, "--rebalance-run-id"),
    home_dir: Path = typer.Option(Path("~/.tradingagents").expanduser(), "--home-dir"),
) -> None:
    """Regenerate a report revision without mutating ledger state."""
    target = date.fromisoformat(trade_date)
    paper_repo = _paper_repo(home_dir)
    writer = PaperReportWriter(home_dir)
    try:
        if rebalance_run_id:
            run = build_report_run_from_rebalance(paper_repo, rebalance_run_id)
            run = PaperReportRun(
                account_id=run.account_id,
                trade_date=target,
                logical_run_key=logical_run_key,
                revision=revision,
                screen_run_id=run.screen_run_id,
                rebalance_run_id=rebalance_run_id,
                signal_time=run.signal_time,
                execution_date=run.execution_date,
                config_hash=run.config_hash,
                universe_hash=run.universe_hash,
                strategy_version=run.strategy_version,
                dataset_versions=run.dataset_versions,
                event_dataset_versions=run.event_dataset_versions,
            )
        else:
            run = PaperReportRun(
                account_id=account_id,
                trade_date=target,
                logical_run_key=logical_run_key,
                revision=revision,
            )
        manifest_path = writer.write(run, paper_repo=paper_repo)
        typer.echo(
            json.dumps(
                {"manifest_path": str(manifest_path), "revision": revision},
                ensure_ascii=False,
                indent=2,
            )
        )
    except (PaperError, ValueError, FileExistsError) as exc:
        typer.echo(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        raise typer.Exit(code=1) from exc
    finally:
        paper_repo.close()


if __name__ == "__main__":
    app()
