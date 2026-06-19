"""Offline tests for TushareProvider with injected mock client."""

from __future__ import annotations

from datetime import date
from zoneinfo import ZoneInfo

import pandas as pd

from tradingagents.market_data.contracts import DataStatus, PITLevel
from tradingagents.market_data.providers.tushare import TushareProvider

SHANGHAI = ZoneInfo("Asia/Shanghai")


class _MockClient:
    def __init__(self, handlers: dict[str, object]):
        self._handlers = handlers

    def query(self, api_name: str, fields: str = "", **params):
        handler = self._handlers.get(api_name)
        if handler is None:
            return pd.DataFrame()
        return handler(fields=fields, **params)


def test_tushare_provider_requires_token_when_no_client():
    provider = TushareProvider(token=None, client=None)
    result = provider.probe_capabilities()
    assert result.status == DataStatus.PERMISSION_DENIED
    assert "TUSHARE_TOKEN" in result.errors[0]


def test_tushare_provider_maps_stock_basic():
    frame = pd.DataFrame([
        {
            "ts_code": "600000.SH",
            "name": "浦发银行",
            "list_date": "19991110",
            "delist_date": None,
            "market": "主板",
            "list_status": "L",
        }
    ])

    def stock_basic(**_kwargs):
        return frame

    client = _MockClient({"stock_basic": stock_basic})
    provider = TushareProvider(token="test-token", client=client)
    result = provider.list_securities(date(2026, 1, 3))
    assert result.status == DataStatus.OK
    assert result.data[0].symbol == "600000"
    assert result.data[0].name == "浦发银行"
    assert result.pit_level == PITLevel.PIT_REQUIRED


def test_tushare_provider_maps_trade_calendar():
    frame = pd.DataFrame([
        {"exchange": "SSE", "cal_date": "20260102", "is_open": 1},
        {"exchange": "SSE", "cal_date": "20260103", "is_open": 1},
    ])

    def trade_cal(**_kwargs):
        return frame

    client = _MockClient({"trade_cal": trade_cal})
    provider = TushareProvider(token="test-token", client=client)
    result = provider.get_trade_calendar(date(2026, 1, 1), date(2026, 1, 31))
    assert result.status == DataStatus.OK
    assert len(result.data) == 2
    assert result.data[0].trade_date == date(2026, 1, 2)


def test_tushare_provider_maps_daily_bars():
    frame = pd.DataFrame([
        {
            "ts_code": "600000.SH",
            "trade_date": "20260102",
            "open": 10.0,
            "high": 10.5,
            "low": 9.8,
            "close": 10.2,
            "vol": 1000.0,
            "amount": 10200.0,
        }
    ])

    def daily(**_kwargs):
        return frame

    client = _MockClient({"daily": daily})
    provider = TushareProvider(token="test-token", client=client)
    result = provider.get_daily_bars(["600000"], date(2026, 1, 2), date(2026, 1, 2))
    assert result.status == DataStatus.OK
    row = result.data[0]
    assert row["symbol"] == "600000"
    assert row["close"] == 10.2
    assert row["available_at"].tzinfo is not None


def test_tushare_provider_rate_limit_surfaces_status():
    def daily(**_kwargs):
        raise RuntimeError("抱歉，您每分钟最多访问该接口200次")

    client = _MockClient({"daily": daily})
    provider = TushareProvider(token="test-token", client=client)
    result = provider.get_daily_bars(["600000"], date(2026, 1, 2), date(2026, 1, 2))
    assert result.status == DataStatus.RATE_LIMITED
    assert result.data is None


def test_tushare_probe_capabilities_offline():
    handlers = {
        "stock_basic": lambda **_kwargs: pd.DataFrame([{"ts_code": "600000.SH"}]),
        "trade_cal": lambda **_kwargs: pd.DataFrame([{"cal_date": "20260102", "is_open": 1}]),
        "daily": lambda **_kwargs: pd.DataFrame([{"ts_code": "600000.SH", "trade_date": "20260102"}]),
    }
    client = _MockClient(handlers)
    provider = TushareProvider(token="test-token", client=client)
    result = provider.probe_capabilities()
    assert result.status == DataStatus.OK
    datasets = {item.dataset for item in result.data}
    assert "security_master" in datasets
    assert "trade_calendar" in datasets
    assert "daily_bars" in datasets
