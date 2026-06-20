"""ROE derivation and financial field quality tests."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from tradingagents.market_data.financials import (
    derive_roe,
    financial_row_passes_quality_gate,
    normalize_reported_roe,
    roe_annualization_factor,
)
from tradingagents.market_data.quality import build_financial_field_quality_report

SHANGHAI = ZoneInfo("Asia/Shanghai")


@pytest.mark.parametrize(
    ("report_period", "expected"),
    [
        ("20251231", 1.0),
        ("20260331", 4.0),
        ("20260630", 2.0),
        ("20260930", pytest.approx(4.0 / 3.0)),
    ],
)
def test_roe_annualization_factor(report_period: str, expected: float):
    assert roe_annualization_factor(report_period) == expected


def test_normalize_reported_roe_accepts_decimal_and_percent():
    assert normalize_reported_roe(0.12) == 0.12
    assert normalize_reported_roe(12.0) == 0.12


def test_derive_roe_from_equity_when_income_missing_indicator():
    roe = derive_roe(
        direct_roe=0.0,
        net_profit=17_861_000_000.0,
        equity=833_771_000_000.0,
        report_period="20260331",
    )
    assert roe == pytest.approx(0.0856, rel=1e-2)


def test_financial_row_passes_quality_gate_rejects_zero_roe_with_profit():
    assert not financial_row_passes_quality_gate({
        "roe": 0.0,
        "net_profit": 100.0,
        "debt_ratio": 0.5,
        "operating_cashflow": 80.0,
    })
    assert financial_row_passes_quality_gate({
        "roe": 0.08,
        "net_profit": 100.0,
        "debt_ratio": 0.5,
        "operating_cashflow": 80.0,
    })


def test_build_financial_field_quality_report_counts_symbols():
    rows = [
        {
            "symbol": "600000",
            "report_period": "20260331",
            "roe": 0.08,
            "net_profit": 1.0,
            "debt_ratio": 0.9,
            "operating_cashflow": 1.0,
            "available_at": datetime(2026, 4, 1, 9, 0, tzinfo=SHANGHAI),
        },
        {
            "symbol": "000001",
            "report_period": "20260331",
            "roe": 0.0,
            "net_profit": 1.0,
            "debt_ratio": 0.9,
            "operating_cashflow": 1.0,
            "available_at": datetime(2026, 4, 1, 9, 0, tzinfo=SHANGHAI),
        },
    ]
    report = build_financial_field_quality_report(
        rows,
        ["600000", "000001"],
        threshold=1.0,
    )
    assert report.numerator == 1
    assert report.denominator == 2
    assert report.status == "fail"
    assert "000001" in report.exclusions
