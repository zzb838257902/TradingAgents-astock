"""Shared event fetch helpers without sync/repository dependencies."""

from __future__ import annotations

import time
from datetime import date
from typing import Any, Callable

from tradingagents.events.dedup import EventBundle
from tradingagents.events.normalizer import normalize_announcement_row
from tradingagents.market_data.contracts import DataStatus
from tradingagents.market_data.providers.free_astock_sources import ProviderFetchError


def retry_fetch(
    operation: Callable[[], Any],
    *,
    max_attempts: int = 3,
) -> Any:
    last_error: Exception | None = None
    for attempt in range(max_attempts):
        try:
            return operation()
        except ProviderFetchError as exc:
            if exc.status not in {"network_error", "rate_limited"}:
                raise
            last_error = exc
            if attempt + 1 >= max_attempts:
                raise
            time.sleep(0.5 * (2 ** attempt))
    if last_error is not None:
        raise last_error
    raise RuntimeError("retry_fetch exhausted without result")


def collect_announcement_bundles(
    backend,
    symbols: list[str],
    start: date,
    end: date,
    *,
    open_dates: list[date] | None = None,
    source: str = "free_astock",
) -> tuple[list[EventBundle], DataStatus, list[str]]:
    bundles: list[EventBundle] = []
    errors: list[str] = []
    for symbol in symbols:
        try:
            rows = retry_fetch(lambda: backend.fetch_sina_bulletin_rows(symbol, page=1))
        except ProviderFetchError as exc:
            status = DataStatus(exc.status)
            return [], status, [exc.message]
        for row in rows:
            published = row.get("published_date")
            if not isinstance(published, date):
                continue
            if published < start or published > end:
                continue
            event, link = normalize_announcement_row(
                row,
                open_dates=open_dates,
                source=source,
            )
            bundles.append(EventBundle(event=event, links=(link,)))
    status = DataStatus.OK if bundles else DataStatus.SUCCESS_EMPTY
    return bundles, status, errors
