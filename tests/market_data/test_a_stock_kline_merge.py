"""mootdx + sina gap-fill merge for daily bars."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from tradingagents.dataflows.a_stock import (
    _normalize_mootdx_daily_frame,
    _normalize_sina_daily_frame,
    _supplement_kline_from_sina,
)


def test_supplement_kline_from_sina_fills_missing_dates(monkeypatch):
    mootdx_df = pd.DataFrame([
        {
            "Date": "2026-06-17",
            "Open": 10.0,
            "High": 10.5,
            "Low": 9.8,
            "Close": 10.2,
            "Volume": 1000,
        }
    ])
    sina_df = pd.DataFrame([
        {
            "Date": "2026-06-17",
            "Open": 9.0,
            "High": 9.5,
            "Low": 8.8,
            "Close": 9.2,
            "Volume": 900,
        },
        {
            "Date": "2026-06-18",
            "Open": 10.1,
            "High": 10.6,
            "Low": 9.9,
            "Close": 10.3,
            "Volume": 1100,
        },
    ])

    def _fake_sina(code, start_date, end_date):
        return sina_df

    monkeypatch.setattr(
        "tradingagents.dataflows.a_stock._sina_kline_fallback",
        _fake_sina,
    )
    merged, supplemented = _supplement_kline_from_sina(
        mootdx_df,
        "600000",
        "2026-06-17",
        "2026-06-18",
    )
    assert supplemented is True
    dates = [d.date() for d in pd.to_datetime(merged["Date"])]
    assert dates == [date(2026, 6, 17), date(2026, 6, 18)]
    assert float(merged.iloc[0]["Close"]) == 10.2
    assert float(merged.iloc[1]["Close"]) == 10.3


def test_supplement_kline_from_sina_noop_when_dates_complete(monkeypatch):
    mootdx_df = pd.DataFrame([
        {
            "Date": "2026-06-18",
            "Open": 10.0,
            "High": 10.5,
            "Low": 9.8,
            "Close": 10.2,
            "Volume": 1000,
        }
    ])
    monkeypatch.setattr(
        "tradingagents.dataflows.a_stock._sina_kline_fallback",
        lambda *args, **kwargs: mootdx_df.copy(),
    )
    merged, supplemented = _supplement_kline_from_sina(
        mootdx_df,
        "600000",
        "2026-06-18",
        "2026-06-18",
    )
    assert supplemented is False
    assert len(merged) == 1


def test_normalize_mootdx_volume_converts_lots_to_shares():
    frame = _normalize_mootdx_daily_frame(pd.DataFrame([
        {"Date": "2026-06-17", "Open": 10.0, "High": 10.5, "Low": 9.8, "Close": 10.0, "Volume": 1000},
    ]))
    assert float(frame.iloc[0]["Volume"]) == pytest.approx(100_000.0)
    assert float(frame.iloc[0]["Amount"]) == pytest.approx(1_000_000.0)


def test_mootdx_sina_merge_preserves_comparable_amount_scale(monkeypatch):
    mootdx_df = pd.DataFrame([
        {
            "Date": "2026-06-17",
            "Open": 10.0,
            "High": 10.5,
            "Low": 9.8,
            "Close": 10.0,
            "Volume": 1000,
        }
    ])
    sina_df = pd.DataFrame([
        {
            "Date": "2026-06-18",
            "Open": 10.1,
            "High": 10.6,
            "Low": 9.9,
            "Close": 10.0,
            "Volume": 100_000,
        }
    ])

    monkeypatch.setattr(
        "tradingagents.dataflows.a_stock._sina_kline_fallback",
        lambda *args, **kwargs: sina_df,
    )
    merged, supplemented = _supplement_kline_from_sina(
        _normalize_mootdx_daily_frame(mootdx_df),
        "600000",
        "2026-06-17",
        "2026-06-18",
    )
    assert supplemented is True
    amounts = [float(value) for value in merged["Amount"]]
    assert min(amounts) > 500_000
    assert max(amounts) / min(amounts) == pytest.approx(1.0, rel=0.2)
