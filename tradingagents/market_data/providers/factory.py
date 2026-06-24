"""Instantiate market data providers without silent fallback."""

from __future__ import annotations

from typing import Any

from tradingagents.market_data.provider_config import ProviderName, resolve_provider_name
from tradingagents.market_data.providers.base import MarketDataProvider
from tradingagents.market_data.providers.fixture import FixtureProvider
from tradingagents.market_data.providers.free_astock import FreeAStockProvider


def create_market_data_provider(
    name: ProviderName | str,
    *,
    fixture: dict[str, Any] | None = None,
    free_backend: Any | None = None,
    tushare_token: str | None = None,
    tushare_client: Any | None = None,
) -> MarketDataProvider:
    normalized = str(name).strip().lower()
    if normalized == "free":
        return FreeAStockProvider(backend=free_backend)
    if normalized == "fixture":
        if fixture is None:
            raise ValueError("fixture provider requires fixture data")
        return FixtureProvider(fixture)
    if normalized == "tushare":
        from tradingagents.market_data.providers.tushare import TushareProvider

        return TushareProvider(token=tushare_token, client=tushare_client)
    raise ValueError(f"unknown market data provider: {name!r}")


def create_resolved_provider(
    *,
    cli_provider: str | None = None,
    home_dir: Any | None = None,
    fixture: dict[str, Any] | None = None,
    free_backend: Any | None = None,
) -> MarketDataProvider:
    from pathlib import Path

    home = Path(home_dir).expanduser() if home_dir is not None else None
    name = resolve_provider_name(cli_provider=cli_provider, home_dir=home)
    if name == "fixture" and fixture is None:
        raise ValueError("fixture provider requires --fixture data")
    return create_market_data_provider(
        name,
        fixture=fixture,
        free_backend=free_backend,
    )
