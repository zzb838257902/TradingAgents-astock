"""CLI for automatic stock screening MVP."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import typer
import yaml

from tradingagents.market_data.config import MarketDataPaths
from tradingagents.market_data.market_hours import post_close_signal_time
from tradingagents.market_data.repository import MarketDataRepository
from tradingagents.screener.config import ScreenerConfig
from tradingagents.screener.pipeline import run_fixture_backtest, run_screen
from tradingagents.screener.report import ScreeningStatus
from tradingagents.screener.universe_resolver import UniverseRequest, UniverseType

app = typer.Typer(help="TradingAgents automatic stock screening MVP")


def _config_hash(config: ScreenerConfig) -> str:
    return config.stage4_config_hash()


def _fixture_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@app.command("data-health")
def data_health() -> None:
    """Validate and print the data capability matrix."""
    matrix_path = Path("docs/data/data-capability-matrix.yaml")
    matrix = yaml.safe_load(matrix_path.read_text(encoding="utf-8"))
    for name, definition in sorted(matrix["datasets"].items()):
        source = definition.get("source") or definition.get("free_source", "unknown")
        typer.echo(f"{name}: {definition['pit_level']} ({source})")


@app.command("init-db")
def init_db(home_dir: Path = typer.Option(Path("~/.tradingagents").expanduser(), "--home-dir")) -> None:
    """Create or migrate the fixture/test DuckDB file (not live market data)."""
    paths = MarketDataPaths(home_dir=home_dir)
    MarketDataRepository(paths.fixture_db_path)
    typer.echo(f"initialized {paths.fixture_db_path}")


def _load_fixture(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


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
        **run_fixture_backtest(fixture_data, config, config.home_dir / "data" / "market.duckdb"),
    }
    typer.echo(json.dumps(output, ensure_ascii=False, sort_keys=True))


def _emit_report(report, extra: dict | None = None) -> None:
    payload = report.to_output_dict()
    if extra:
        payload = {**extra, **payload}
    typer.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    if report.status == ScreeningStatus.DATA_ERROR:
        raise typer.Exit(code=1)


@app.command("screen")
def screen(
    fixture: Path = typer.Option(..., "--fixture"),
    universe: str = typer.Option("all", "--universe"),
    universe_code: Optional[str] = typer.Option(None, "--universe-code"),
    symbols: Optional[str] = typer.Option(None, "--symbols"),
    as_of: Optional[str] = typer.Option(None, "--as-of"),
    home_dir: Path = typer.Option(Path("~/.tradingagents").expanduser(), "--home-dir"),
    config_path: Optional[Path] = typer.Option(None, "--config"),
    event_enrichment: Optional[bool] = typer.Option(
        None,
        "--event-enrichment/--no-event-enrichment",
        help="override event_enrichment.enabled in config",
    ),
) -> None:
    """Screen a fixture universe (all/industry/index/custom) and print JSON."""
    config = (
        ScreenerConfig.from_yaml(config_path)
        if config_path
        else ScreenerConfig(home_dir=home_dir)
    )
    if event_enrichment is not None:
        config = config.model_copy(update={
            "event_enrichment": config.event_enrichment.model_copy(
                update={"enabled": event_enrichment},
            ),
        })
    fixture_data = _load_fixture(fixture)
    trading_dates = sorted(fixture_data["bars"])
    signal_date = date.fromisoformat(trading_dates[-2])
    signal_time = (
        datetime.fromisoformat(as_of).astimezone()
        if as_of
        else post_close_signal_time(signal_date)
    )
    custom_symbols = tuple(
        item.strip() for item in (symbols or "").split(",") if item.strip()
    )
    universe_request = UniverseRequest(
        universe_type=UniverseType(universe),
        universe_code=universe_code,
        symbols=custom_symbols,
        as_of=signal_time,
    )
    report = run_screen(
        fixture_data,
        config,
        config.home_dir / "data" / "market.duckdb",
        universe_request=universe_request,
    )
    _emit_report(report, extra={
        "config_hash": _config_hash(config),
        "fixture_sha256": _fixture_sha256(fixture),
    })


if __name__ == "__main__":
    app()
