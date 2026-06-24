"""Guards against labeling live/current provider data as historical."""

from __future__ import annotations

from datetime import date, datetime

from tradingagents.market_data.market_hours import SHANGHAI


def shanghai_today() -> date:
    return datetime.now(tz=SHANGHAI).date()


def live_snapshot_date_error(requested: date, *, dataset: str) -> str | None:
    """Return error when a current-only provider endpoint is asked for a past date."""
    today = shanghai_today()
    if requested < today:
        return (
            f"{dataset} live snapshot cannot be synced for historical date "
            f"{requested.isoformat()}; use a historical backfill provider path"
        )
    if requested > today:
        return (
            f"{dataset} cannot be synced for future date {requested.isoformat()}"
        )
    return None


def security_snapshot_write_error(snapshot_date: date, captured_at: datetime) -> str | None:
    """Block writing a daily security snapshot unless it is today's live sync."""
    _ = captured_at
    return live_snapshot_date_error(snapshot_date, dataset="security_master")
