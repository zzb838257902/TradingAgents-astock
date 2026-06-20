from pathlib import Path
from typing import Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from tradingagents.market_data.contracts import PriceBasis


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UniverseConfig(StrictModel):
    min_listing_days: int = Field(default=60, ge=1)
    min_avg_amount_20d: float = Field(default=50_000_000, ge=0)


class StrategyConfig(StrictModel):
    momentum_weight: float = Field(default=0.5, ge=0, le=1)
    quality_weight: float = Field(default=0.5, ge=0, le=1)
    price_basis: PriceBasis = PriceBasis.FORWARD_ADJUSTED

    @model_validator(mode="after")
    def validate_weights(self) -> Self:
        if abs(self.momentum_weight + self.quality_weight - 1.0) > 1e-9:
            raise ValueError("strategy weights must sum to 1")
        return self


class PortfolioConfig(StrictModel):
    portfolio_value: float = Field(default=1_000_000, gt=0)
    max_positions: int = Field(default=10, ge=1)
    max_stock_weight: float = Field(default=0.10, gt=0, le=1)
    max_industry_weight: float = Field(default=0.25, gt=0, le=1)
    cash_buffer: float = Field(default=0.10, ge=0, lt=1)
    max_participation_rate: float = Field(default=0.05, gt=0, le=1)


class ScreenerConfig(StrictModel):
    home_dir: Path = Path("~/.tradingagents").expanduser()
    universe: UniverseConfig = UniverseConfig()
    strategy: StrategyConfig = StrategyConfig()
    portfolio: PortfolioConfig = PortfolioConfig()

    @classmethod
    def from_yaml(cls, path: Path) -> "ScreenerConfig":
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls.model_validate(raw)
