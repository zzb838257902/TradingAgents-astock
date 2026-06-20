"""Financial record PIT helpers (announcement timing and revisions)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Iterable

from tradingagents.market_data.market_hours import SHANGHAI, ensure_aware_shanghai

DEFAULT_RECORD_TYPE = "indicator"

_NET_PROFIT_KEYS = (
    "归属于母公司所有者的净利润",
    "归属于母公司的净利润",
    "NETPARECOMPPROF",
    "净利润",
    "NETPROFIT",
)
_EQUITY_KEYS = (
    "归属于母公司股东的权益",
    "归属于母公司所有者权益合计",
    "PARESHARRIGHT",
    "股东权益合计",
    "股东权益",
    "TOTSHAREQUI",
)
_ROE_DIRECT_KEYS = (
    "净资产收益率",
    "加权净资产收益率",
    "roe",
    "ROE",
    "ROEWEIGHTED",
)


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

    available_at = financial_available_at(
        announcement_date,
        actual_time,
        open_dates=open_dates,
    )

    ann_source = row.get("announcement_date_source") or "reported"
    source_version = row.get("source_version")
    if ann_source != "reported":
        ann_tag = f"ann:{ann_source}"
        source_version = ann_tag if not source_version else f"{source_version};{ann_tag}"

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
        "update_flag": row.get("update_flag") or "",
        "source_version": source_version,
        "record_type": row.get("record_type") or DEFAULT_RECORD_TYPE,
        "source": row["source"],
        "ingested_at": row.get("ingested_at"),
        "dataset_version_id": row.get("dataset_version_id"),
    }


def roe_annualization_factor(report_period: str | None) -> float:
    """Annualize period ROE from quarterly / semi-annual report_period (YYYYMMDD)."""
    if not report_period or len(report_period) < 6 or not report_period[:6].isdigit():
        return 1.0
    month = int(report_period[4:6])
    if month == 12:
        return 1.0
    if month == 3:
        return 4.0
    if month == 6:
        return 2.0
    if month == 9:
        return 4.0 / 3.0
    return 1.0


def normalize_reported_roe(value: float) -> float:
    """Accept decimal ROE (0.12) or percentage points (12.0)."""
    if value == 0.0:
        return 0.0
    if abs(value) > 1.0:
        return value / 100.0
    return value


def derive_roe(
    *,
    direct_roe: float,
    net_profit: float,
    equity: float,
    report_period: str | None,
) -> float:
    """Fill ROE from income indicators or net_profit / equity (derived; not weighted avg)."""
    normalized = normalize_reported_roe(direct_roe)
    if normalized != 0.0:
        return normalized
    if equity <= 0:
        return 0.0
    return (net_profit / equity) * roe_annualization_factor(report_period)


def financial_row_passes_quality_gate(row: dict) -> bool:
    """Latest financial row must support quality factor scoring."""
    net_profit = float(row.get("net_profit") or 0.0)
    roe = float(row.get("roe") or 0.0)
    debt_ratio = float(row.get("debt_ratio") or 0.0)
    if debt_ratio <= 0:
        return False
    if net_profit != 0.0 and abs(roe) < 1e-12:
        return False
    return True


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
