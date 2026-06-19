"""CLI for automatic stock screening MVP."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Optional

import typer
import yaml

from tradingagents.market_data.repository import MarketDataRepository
from tradingagents.screener.config import ScreenerConfig
from tradingagents.screener.pipeline import run_fixture_backtest

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


if __name__ == "__main__":
    app()
