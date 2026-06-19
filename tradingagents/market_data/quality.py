"""Data quality rules and machine-readable coverage reports."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any


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
