"""Database path configuration for paper portfolio storage."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class PaperPaths(BaseModel):
    model_config = ConfigDict(extra="forbid")

    home_dir: Path = Field(default_factory=lambda: Path("~/.tradingagents").expanduser())

    @property
    def paper_db_path(self) -> Path:
        return self.home_dir / "data" / "paper.duckdb"
