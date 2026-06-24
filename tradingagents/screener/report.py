"""Structured screening run report (phase 4.7)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from tradingagents.market_data.contracts import PITLevel


class ScreeningStatus(StrEnum):
    OK = "ok"
    EMPTY_UNIVERSE = "empty_universe"
    DATA_ERROR = "data_error"


class RunReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    status: ScreeningStatus
    signal_time: datetime
    data_as_of: datetime
    dataset_versions: dict[str, dict[str, Any] | None] = Field(default_factory=dict)
    data_sources: dict[str, str] = Field(default_factory=dict)
    data_quality: dict[str, Any] = Field(default_factory=dict)
    pit_level: str = PITLevel.PIT_REQUIRED.value
    universe_type: str = "all"
    universe_code: str | None = None
    universe_size: int = 0
    included_count: int = 0
    excluded_count: int = 0
    excluded_reasons: dict[str, list[str]] = Field(default_factory=dict)
    ranking: list[str] = Field(default_factory=list)
    factor_contributions: dict[str, dict[str, float]] = Field(default_factory=dict)
    target_weights: dict[str, float] = Field(default_factory=dict)
    cash_weight: float = 1.0
    industry_by_symbol: dict[str, str] = Field(default_factory=dict)
    industry_weights: dict[str, float] = Field(default_factory=dict)
    orders: list[dict[str, Any]] = Field(default_factory=list)
    metrics: dict[str, float] = Field(default_factory=dict)
    positions: int = 0
    top_symbol: str | None = None
    errors: list[str] = Field(default_factory=list)
    base_ranking: list[str] = Field(default_factory=list)
    event_ranking: list[str] = Field(default_factory=list)
    enhanced_ranking: list[str] = Field(default_factory=list)
    event_contributions: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    risk_flags: dict[str, list[str]] = Field(default_factory=dict)
    event_dataset_versions: dict[str, dict[str, Any] | None] = Field(default_factory=dict)
    event_data_sources: dict[str, str] = Field(default_factory=dict)
    event_degradations: dict[str, list[str]] = Field(default_factory=dict)
    event_pit_level: str = ""
    event_enrichment_errors: list[str] = Field(default_factory=list)

    def to_legacy_dict(self) -> dict[str, Any]:
        """Subset consumed by phase 0-3 tests and backtest-fixture CLI."""
        return {
            "metrics": self.metrics,
            "positions": self.positions,
            "orders": self.orders,
            "top_symbol": self.top_symbol,
            "ranking": self.ranking,
            "target_weights": self.target_weights,
            "cash_weight": self.cash_weight,
            "excluded_reasons": self.excluded_reasons,
            "industry_by_symbol": self.industry_by_symbol,
        }

    def to_output_dict(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload["status"] = self.status.value
        return payload


def compute_industry_weights(
    target_weights: dict[str, float],
    industry_by_symbol: dict[str, str],
) -> dict[str, float]:
    weights: dict[str, float] = {}
    for symbol, weight in target_weights.items():
        industry = industry_by_symbol.get(symbol, "未知")
        weights[industry] = round(weights.get(industry, 0.0) + weight, 10)
    return weights
