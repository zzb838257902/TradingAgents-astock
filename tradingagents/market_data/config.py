"""Database path configuration for market data storage."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class MarketDataPaths(BaseModel):
    model_config = ConfigDict(extra="forbid")

    home_dir: Path = Field(default_factory=lambda: Path("~/.tradingagents").expanduser())

    @property
    def live_db_path(self) -> Path:
        return self.home_dir / "data" / "market_live.duckdb"

    @property
    def fixture_db_path(self) -> Path:
        return self.home_dir / "data" / "market.duckdb"

    @property
    def snapshot_dir(self) -> Path:
        return self.home_dir / "data" / "raw_snapshots"
