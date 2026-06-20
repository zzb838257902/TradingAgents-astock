"""Data quality rules and machine-readable coverage reports."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from tradingagents.market_data.financials import next_open_trading_day


@dataclass(frozen=True)
class QualityIssue:
    rule: str
    severity: str
    detail: str
    symbol: str | None = None
    trade_date: date | None = None


@dataclass(frozen=True)
class CoverageReport:
    dataset: str
    status: str
    numerator: int
    denominator: int
    ratio: float
    threshold: float
    exclusions: list[str] = field(default_factory=list)
    details: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset,
            "status": self.status,
            "numerator": self.numerator,
            "denominator": self.denominator,
            "ratio": self.ratio,
            "threshold": self.threshold,
            "exclusions": self.exclusions,
            "details": self.details,
        }


def assess_daily_bar_quality(bars: list[dict]) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    for bar in bars:
        symbol = bar.get("symbol")
        trade_date = bar.get("trade_date")
        high = float(bar["high"])
        low = float(bar["low"])
        open_ = float(bar["open"])
        close = float(bar["close"])
        volume = float(bar["volume"])
        amount = float(bar["amount"])
        if high < max(open_, close) or low > min(open_, close) or high < low:
            issues.append(QualityIssue(
                rule="ohlc_invalid",
                severity="blocking",
                detail="high/low inconsistent with open/close",
                symbol=symbol,
                trade_date=trade_date,
            ))
        if volume < 0 or amount < 0:
            issues.append(QualityIssue(
                rule="negative_volume_or_amount",
                severity="blocking",
                detail="volume or amount is negative",
                symbol=symbol,
                trade_date=trade_date,
            ))
        if bar.get("available_at") is None:
            issues.append(QualityIssue(
                rule="missing_available_at",
                severity="blocking",
                detail="pit_required bar missing available_at",
                symbol=symbol,
                trade_date=trade_date,
            ))
    return issues


def build_daily_completeness_report(
    numerator: int,
    denominator: int,
    threshold: float,
    exclusions: list[str] | None = None,
    details: list[dict[str, Any]] | None = None,
) -> CoverageReport:
    ratio = numerator / denominator if denominator else 0.0
    status = "pass" if denominator and ratio >= threshold else "fail"
    return CoverageReport(
        dataset="daily_completeness",
        status=status,
        numerator=numerator,
        denominator=denominator,
        ratio=ratio,
        threshold=threshold,
        exclusions=exclusions or [],
        details=details or [],
    )


def build_backfill_completeness_report(
    bars: list[dict],
    symbols: list[str],
    open_dates: list[date],
    threshold: float,
) -> CoverageReport:
    expected = {(symbol, day) for symbol in symbols for day in open_dates}
    actual = {
        (bar.get("symbol"), bar.get("trade_date"))
        for bar in bars
        if bar.get("symbol") is not None and bar.get("trade_date") is not None
    }
    numerator = len(expected & actual)
    denominator = len(expected)
    ratio = numerator / denominator if denominator else 0.0
    status = "pass" if denominator and ratio >= threshold else "fail"
    missing = sorted(expected - actual)
    details = [
        {
            "symbol": symbol,
            "trade_date": day.isoformat(),
        }
        for symbol, day in missing[:20]
    ]
    return CoverageReport(
        dataset="daily_backfill_completeness",
        status=status,
        numerator=numerator,
        denominator=denominator,
        ratio=ratio,
        threshold=threshold,
        details=details,
    )


def effective_trade_calendar_start(requested_start: date) -> date:
    """First weekday on or after requested_start (weekends only; no holiday table)."""
    if requested_start.weekday() < 5:
        return requested_start
    return next_open_trading_day(requested_start - timedelta(days=1))


def effective_trade_calendar_end(requested_end: date) -> date:
    """Last weekday on or before requested_end (weekends only; no holiday table)."""
    candidate = requested_end
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


def _reference_open_bounds(
    reference_open_dates: list[date],
) -> tuple[date, date] | None:
    if not reference_open_dates:
        return None
    return min(reference_open_dates), max(reference_open_dates)


def effective_trade_calendar_start_bound(
    requested_start: date,
    *,
    reference_open_dates: list[date] | None = None,
) -> date:
    if reference_open_dates:
        bounds = _reference_open_bounds(reference_open_dates)
        if bounds is not None:
            reference_min, reference_max = bounds
            if reference_min <= requested_start <= reference_max:
                eligible = [
                    day for day in reference_open_dates if day >= requested_start
                ]
                if eligible:
                    return min(eligible)
    return effective_trade_calendar_start(requested_start)


def effective_trade_calendar_end_bound(
    requested_end: date,
    *,
    reference_open_dates: list[date] | None = None,
) -> date:
    if reference_open_dates:
        bounds = _reference_open_bounds(reference_open_dates)
        if bounds is not None:
            reference_min, reference_max = bounds
            if reference_min <= requested_end <= reference_max:
                eligible = [
                    day for day in reference_open_dates if day <= requested_end
                ]
                if eligible:
                    return max(eligible)
    return effective_trade_calendar_end(requested_end)


def build_trade_calendar_range_report(
    requested_start: date,
    requested_end: date,
    actual_open_dates: list[date],
    *,
    source_limit_bars: int | None = None,
    source_label: str | None = None,
    reference_open_dates: list[date] | None = None,
) -> CoverageReport:
    effective_start = effective_trade_calendar_start_bound(
        requested_start,
        reference_open_dates=reference_open_dates,
    )
    effective_end = effective_trade_calendar_end_bound(
        requested_end,
        reference_open_dates=reference_open_dates,
    )
    base_details = {
        "requested_start": requested_start.isoformat(),
        "requested_end": requested_end.isoformat(),
        "effective_start": effective_start.isoformat(),
        "effective_end": effective_end.isoformat(),
        "source_limit_bars": source_limit_bars,
        "source_label": source_label,
    }
    if not actual_open_dates:
        return CoverageReport(
            dataset="trade_calendar_range",
            status="fail",
            numerator=0,
            denominator=1,
            ratio=0.0,
            threshold=1.0,
            details=[base_details],
        )
    actual_start = min(actual_open_dates)
    actual_end = max(actual_open_dates)
    covers_start = actual_start <= effective_start
    covers_end = actual_end >= effective_end
    covers = covers_start and covers_end
    return CoverageReport(
        dataset="trade_calendar_range",
        status="pass" if covers else "fail",
        numerator=1 if covers else 0,
        denominator=1,
        ratio=1.0 if covers else 0.0,
        threshold=1.0,
        details=[{
            **base_details,
            "actual_start": actual_start.isoformat(),
            "actual_end": actual_end.isoformat(),
            "actual_count": len(actual_open_dates),
            "covers_start": covers_start,
            "covers_end": covers_end,
        }],
    )


def build_financial_field_quality_report(
    rows: list[dict],
    target_symbols: list[str],
    threshold: float,
) -> CoverageReport:
    from tradingagents.market_data.financials import (
        financial_row_passes_quality_gate,
        pick_latest_visible_financials,
    )

    latest = pick_latest_visible_financials(rows)
    latest_by_symbol = {row["symbol"]: row for row in latest}
    passing = [
        symbol
        for symbol in target_symbols
        if symbol in latest_by_symbol
        and financial_row_passes_quality_gate(latest_by_symbol[symbol])
    ]
    denominator = len(target_symbols)
    numerator = len(passing)
    ratio = numerator / denominator if denominator else 0.0
    status = "pass" if denominator and ratio >= threshold else "fail"
    failing = sorted(set(target_symbols) - set(passing))
    details = [
        {
            "symbol": symbol,
            "roe": latest_by_symbol.get(symbol, {}).get("roe"),
            "net_profit": latest_by_symbol.get(symbol, {}).get("net_profit"),
            "debt_ratio": latest_by_symbol.get(symbol, {}).get("debt_ratio"),
        }
        for symbol in failing[:20]
    ]
    return CoverageReport(
        dataset="financial_field_quality",
        status=status,
        numerator=numerator,
        denominator=denominator,
        ratio=ratio,
        threshold=threshold,
        exclusions=failing,
        details=details,
    )


def build_financial_symbol_coverage_report(
    rows: list[dict],
    target_symbols: list[str],
    threshold: float,
) -> CoverageReport:
    symbols_with_data = {row["symbol"] for row in rows if row.get("symbol")}
    denominator = len(target_symbols)
    numerator = len(symbols_with_data)
    ratio = numerator / denominator if denominator else 0.0
    status = "pass" if denominator and ratio >= threshold else "fail"
    return CoverageReport(
        dataset="financial_symbol_coverage",
        status=status,
        numerator=numerator,
        denominator=denominator,
        ratio=ratio,
        threshold=threshold,
    )


def build_security_coverage_report(
    numerator: int,
    denominator: int,
    threshold: float = 0.99,
    exclusions: list[str] | None = None,
) -> CoverageReport:
    ratio = numerator / denominator if denominator else 0.0
    status = "pass" if denominator and ratio >= threshold else "fail"
    return CoverageReport(
        dataset="security_coverage",
        status=status,
        numerator=numerator,
        denominator=denominator,
        ratio=ratio,
        threshold=threshold,
        exclusions=exclusions or [],
    )


def audit_price_limits(rows: list[dict], tolerance: float = 0.01) -> list[dict[str, Any]]:
    from tradingagents.backtest.limits import compute_limit_prices

    issues: list[dict[str, Any]] = []
    for row in rows:
        expected_up, expected_down = compute_limit_prices(
            float(row["prev_close"]),
            st_flag=bool(row.get("st_flag", False)),
            board=str(row.get("board", "main")),
        )
        supplier_up = float(row["supplier_limit_up"])
        supplier_down = float(row["supplier_limit_down"])
        if (
            abs(supplier_up - expected_up) > tolerance
            or abs(supplier_down - expected_down) > tolerance
        ):
            issues.append({
                "rule": "limit_price_mismatch",
                "symbol": row["symbol"],
                "trade_date": row["trade_date"].isoformat()
                if isinstance(row["trade_date"], date)
                else row["trade_date"],
                "expected_limit_up": expected_up,
                "expected_limit_down": expected_down,
                "supplier_limit_up": supplier_up,
                "supplier_limit_down": supplier_down,
            })
    return issues
