"""Unit tests for free_astock_sources field parsing."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

from tradingagents.market_data.providers.free_astock_sources import LiveFreeAStockSourceBackend


def test_eastmoney_daily_snapshot_maps_ohlc_fields():
    backend = LiveFreeAStockSourceBackend()
    payload = {
        "data": {
            "diff": [{
                "f12": "600000",
                "f2": 11.5,
                "f5": 1000,
                "f6": 11500,
                "f15": 11.8,
                "f16": 11.2,
                "f17": 11.3,
                "f18": 11.0,
            }],
            "total": 1,
        },
    }
    response = MagicMock()
    response.json.return_value = payload
    with patch(
        "tradingagents.market_data.providers.free_astock_sources.shanghai_today",
        return_value=date(2026, 6, 18),
    ), patch(
        "tradingagents.dataflows.a_stock._em_get",
        return_value=response,
    ):
        rows = backend.fetch_eastmoney_daily_snapshot(date(2026, 6, 18))
    assert len(rows) == 1
    row = rows[0]
    assert row["open"] == 11.3
    assert row["high"] == 11.8
    assert row["low"] == 11.2
    assert row["close"] == 11.5
    assert row["pre_close"] == 11.0
