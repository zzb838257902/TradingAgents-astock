"""Resolve market data provider selection (default: free)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict

ProviderName = Literal["free", "tushare", "fixture"]

_VALID_PROVIDERS = frozenset({"free", "tushare", "fixture"})


class MarketDataSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: ProviderName = "free"


def resolve_provider_name(
    *,
    cli_provider: str | None = None,
    home_dir: Path | None = None,
) -> ProviderName:
    """Resolve provider with priority: CLI → env → YAML → default free."""
    if cli_provider:
        normalized = cli_provider.strip().lower()
        if normalized not in _VALID_PROVIDERS:
            raise ValueError(
                f"unknown provider {cli_provider!r}; "
                f"expected one of {sorted(_VALID_PROVIDERS)}"
            )
        return normalized  # type: ignore[return-value]

    env_value = os.environ.get("TRADINGAGENTS_MARKET_DATA_PROVIDER")
    if env_value:
        normalized = env_value.strip().lower()
        if normalized not in _VALID_PROVIDERS:
            raise ValueError(
                f"invalid TRADINGAGENTS_MARKET_DATA_PROVIDER={env_value!r}"
            )
        return normalized  # type: ignore[return-value]

    if home_dir is not None:
        yaml_path = home_dir.expanduser() / "config.yaml"
        if yaml_path.is_file():
            raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
            market_data = raw.get("market_data") or {}
            if isinstance(market_data, dict) and market_data.get("provider"):
                normalized = str(market_data["provider"]).strip().lower()
                if normalized not in _VALID_PROVIDERS:
                    raise ValueError(
                        f"invalid market_data.provider in {yaml_path}: {normalized!r}"
                    )
                return normalized  # type: ignore[return-value]

    return "free"


def load_market_data_settings(home_dir: Path) -> MarketDataSettings:
    return MarketDataSettings(provider=resolve_provider_name(home_dir=home_dir))
