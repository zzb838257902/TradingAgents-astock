"""Build PIT-safe adjustment factors and corporate actions from xdxr events."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, time
from typing import Any, Iterable

from tradingagents.market_data.market_hours import SHANGHAI

_XDXR_DIVIDEND_CATEGORY = 1


def _parse_xdxr_date(row: dict[str, Any]) -> date | None:
    if row.get("date") is not None:
        value = row["date"]
        if isinstance(value, date):
            return value
        if hasattr(value, "date"):
            return value.date()
    year = row.get("year")
    month = row.get("month")
    day = row.get("day")
    if year is None or month is None or day is None:
        return None
    return date(int(year), int(month), int(day))


def _event_available_at(ex_date: date) -> datetime:
    return datetime.combine(ex_date, time(15, 0), tzinfo=SHANGHAI)


def forward_adjustment_ratio(
    fenhong: float,
    peigu: float,
    peigujia: float,
    songzhuangu: float,
    *,
    prev_close: float,
) -> float:
    """Forward-adjustment step ratio using pre-event close (A-share ex-right formula)."""
    if prev_close <= 0:
        return 1.0
    cash_per_share = fenhong / 10.0
    rights_per_share = peigu / 10.0
    bonus_per_share = songzhuangu / 10.0
    ex_price = (
        prev_close
        - cash_per_share
        + peigujia * rights_per_share
    ) / (1.0 + bonus_per_share + rights_per_share)
    return ex_price / prev_close


def build_pit_rows_from_xdxr(
    symbol: str,
    rows: Iterable[dict[str, Any]],
    *,
    source: str = "free_astock",
    prev_close_resolver: Callable[[date], float | None] | None = None,
    default_prev_close: float | None = None,
) -> tuple[list[dict], list[dict]]:
    """Return (adjustment_factor_rows, corporate_action_rows) from xdxr events."""
    events: list[tuple[date, dict[str, Any]]] = []
    for row in rows:
        if int(row.get("category", -1)) != _XDXR_DIVIDEND_CATEGORY:
            continue
        ex_date = _parse_xdxr_date(row)
        if ex_date is None:
            continue
        events.append((ex_date, row))
    events.sort(key=lambda item: item[0])

    factor_rows: list[dict] = []
    action_rows: list[dict] = []
    cumulative = 1.0
    for ex_date, row in events:
        fenhong = float(row.get("fenhong") or 0.0)
        peigu = float(row.get("peigu") or 0.0)
        peigujia = float(row.get("peigujia") or 0.0)
        songzhuangu = float(row.get("songzhuangu") or 0.0)
        prev_close = None
        if prev_close_resolver is not None:
            prev_close = prev_close_resolver(ex_date)
        if prev_close is None:
            raw_prev = row.get("prev_close", row.get("pre_close"))
            if raw_prev is not None:
                prev_close = float(raw_prev)
        if prev_close is None:
            prev_close = default_prev_close
        if prev_close is None or prev_close <= 0:
            raise ValueError(
                f"{symbol} xdxr on {ex_date.isoformat()} requires prev_close for adjustment"
            )
        step = forward_adjustment_ratio(
            fenhong,
            peigu,
            peigujia,
            songzhuangu,
            prev_close=prev_close,
        )
        cumulative *= step
        available_at = _event_available_at(ex_date)
        factor_rows.append({
            "symbol": symbol,
            "trade_date": ex_date,
            "factor": cumulative,
            "available_at": available_at,
            "source": source,
        })
        if fenhong > 0:
            action_rows.append({
                "symbol": symbol,
                "ex_date": ex_date,
                "action_type": "cash_div",
                "cash_div": fenhong / 10.0,
                "stock_div": None,
                "split_ratio": None,
                "rights_ratio": None,
                "available_at": available_at,
                "source": source,
            })
        if peigu > 0 or songzhuangu > 0:
            action_rows.append({
                "symbol": symbol,
                "ex_date": ex_date,
                "action_type": "stock_event",
                "cash_div": None,
                "stock_div": songzhuangu / 10.0 if songzhuangu else None,
                "split_ratio": None,
                "rights_ratio": peigu / 10.0 if peigu else None,
                "available_at": available_at,
                "source": source,
            })
    return factor_rows, action_rows


def latest_factor_on_or_before(
    rows: list[dict],
    trade_date: date,
    available_before: datetime,
) -> float | None:
    visible = [
        row for row in rows
        if row["trade_date"] <= trade_date and row["available_at"] <= available_before
    ]
    if not visible:
        return None
    return max(visible, key=lambda row: row["trade_date"])["factor"]


def forward_adjusted_close(
    raw_close: float,
    trade_date: date,
    *,
    latest_factor: float,
    factor_rows: list[dict],
    available_before: datetime,
) -> float:
    """Scale raw close to forward-adjusted basis anchored at signal_date."""
    factor_at_date = latest_factor_on_or_before(factor_rows, trade_date, available_before)
    if factor_at_date is None or factor_at_date <= 0:
        raise ValueError("forward_adjusted requires visible adjustment factor rows")
    if latest_factor <= 0:
        return raw_close
    return raw_close * latest_factor / factor_at_date


def build_forward_adjusted_closes(
    history: list[dict],
    factor_rows: list[dict],
    signal_date: date,
    available_before: datetime,
) -> list[float]:
    """Return forward-adjusted closes for bars on or before signal_date."""
    latest_factor = latest_factor_on_or_before(factor_rows, signal_date, available_before)
    if latest_factor is None:
        raise ValueError("forward_adjusted requires adjustment factors through signal_date")
    adjusted: list[float] = []
    for row in history:
        trade_date = row["trade_date"]
        if isinstance(trade_date, str):
            trade_date = date.fromisoformat(trade_date)
        if trade_date > signal_date:
            continue
        adjusted.append(forward_adjusted_close(
            float(row["close"]),
            trade_date,
            latest_factor=latest_factor,
            factor_rows=factor_rows,
            available_before=available_before,
        ))
    return adjusted


def resolve_prev_close_from_bars(
    bars: list[dict],
    ex_date: date,
) -> float | None:
    """Pick the last raw close strictly before ex_date from daily bar rows."""
    candidates: list[tuple[date, float]] = []
    for bar in bars:
        trade_date = bar["trade_date"]
        if isinstance(trade_date, str):
            trade_date = date.fromisoformat(trade_date)
        if trade_date < ex_date:
            candidates.append((trade_date, float(bar["close"])))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def baseline_factor_row(
    symbol: str,
    trade_date: date,
    *,
    available_at: datetime,
    source: str = "baseline",
) -> dict:
    """Explicit no-corporate-action factor anchor for formal forward_adjusted mode."""
    return {
        "symbol": symbol,
        "trade_date": trade_date,
        "factor": 1.0,
        "available_at": available_at,
        "source": source,
    }
