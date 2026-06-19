"""Financial record PIT helpers (announcement timing and revisions)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Iterable

from tradingagents.market_data.market_hours import SHANGHAI, ensure_aware_shanghai

DEFAULT_RECORD_TYPE = "indicator"


def next_open_trading_day(after: date, open_dates: Iterable[date] | None = None) -> date:
    if open_dates is not None:
        for day in sorted(day for day in open_dates if day > after):
            return day
    candidate = after + timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


def financial_available_at(
    announcement_date: date,
    actual_announcement_time: datetime | None = None,
    *,
    open_dates: Iterable[date] | None = None,
) -> datetime:
    if actual_announcement_time is not None:
        return ensure_aware_shanghai(actual_announcement_time)
    next_day = next_open_trading_day(announcement_date, open_dates)
    return datetime.combine(next_day, time(9, 0), tzinfo=SHANGHAI)


def normalize_financial_row(row: dict, *, open_dates: Iterable[date] | None = None) -> dict:
    announcement_date = row.get("announcement_date")
    if isinstance(announcement_date, str):
        announcement_date = date.fromisoformat(announcement_date)
    if announcement_date is None:
        available = row.get("available_at")
        if isinstance(available, datetime):
            announcement_date = ensure_aware_shanghai(available).date()
        elif isinstance(available, str):
            announcement_date = ensure_aware_shanghai(datetime.fromisoformat(available)).date()
        else:
            raise ValueError("financial row requires announcement_date or available_at")

    actual_time = row.get("actual_announcement_time")
    if isinstance(actual_time, str):
        actual_time = ensure_aware_shanghai(datetime.fromisoformat(actual_time))

    available_at = row.get("available_at")
    if available_at is None:
        available_at = financial_available_at(
            announcement_date,
            actual_time,
            open_dates=open_dates,
        )
    elif isinstance(available_at, str):
        available_at = ensure_aware_shanghai(datetime.fromisoformat(available_at))
    else:
        available_at = ensure_aware_shanghai(available_at)

    return {
        "symbol": row["symbol"],
        "report_period": row["report_period"],
        "roe": float(row["roe"]),
        "operating_cashflow": float(row["operating_cashflow"]),
        "net_profit": float(row["net_profit"]),
        "debt_ratio": float(row["debt_ratio"]),
        "announcement_date": announcement_date,
        "actual_announcement_time": actual_time,
        "available_at": available_at,
        "update_flag": row.get("update_flag"),
        "source_version": row.get("source_version"),
        "record_type": row.get("record_type") or DEFAULT_RECORD_TYPE,
        "source": row["source"],
        "ingested_at": row.get("ingested_at"),
        "dataset_version_id": row.get("dataset_version_id"),
    }


def pick_latest_visible_financials(rows: list[dict]) -> list[dict]:
    best: dict[str, dict] = {}
    for record in rows:
        symbol = record["symbol"]
        current = best.get(symbol)
        if current is None:
            best[symbol] = record
            continue
        if record["report_period"] > current["report_period"]:
            best[symbol] = record
        elif (
            record["report_period"] == current["report_period"]
            and record["available_at"] > current["available_at"]
        ):
            best[symbol] = record
    return list(best.values())
