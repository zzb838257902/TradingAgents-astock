"""Offline tests for FreeAStockProvider and provider factory."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from tradingagents.market_data.contracts import DataStatus, MembershipMode, PITLevel
from tradingagents.market_data.provider_config import resolve_provider_name
from tradingagents.market_data.providers.factory import create_market_data_provider
from tradingagents.market_data.providers.free_astock import FreeAStockProvider
from tradingagents.market_data.providers.tushare import TushareProvider

SHANGHAI = ZoneInfo("Asia/Shanghai")


class _MockBackend:
    def list_mootdx_stocks(self) -> list[dict]:
        return [
            {
                "symbol": "600000",
                "name": "浦发银行",
                "board": "main",
                "list_date": date(1999, 11, 10),
            },
            {
                "symbol": "000001",
                "name": "平安银行",
                "board": "main",
                "list_date": date(1991, 4, 3),
            },
        ]

    def fetch_sse_trade_dates(self, start: date, end: date) -> list[date]:
        return [date(2026, 1, 2), date(2026, 1, 3)]

    def fetch_eastmoney_daily_snapshot(self, trade_date: date) -> list[dict]:
        return [{
            "symbol": "600000",
            "trade_date": trade_date,
            "open": 10.0,
            "high": 10.5,
            "low": 9.8,
            "close": 10.2,
            "volume": 1000.0,
            "amount": 10200.0,
            "pre_close": 10.0,
            "available_at": datetime(2026, 1, 2, 15, 30, tzinfo=SHANGHAI),
            "source": "free_astock",
        }]

    def fetch_eastmoney_board_members(self, board_code: str) -> list[str]:
        return ["600000"]

    def fetch_sina_financial_rows(
        self,
        symbol: str,
        announced_before: datetime,
        open_dates: list[date] | None = None,
    ) -> list[dict]:
        from tradingagents.market_data.financials import financial_available_at

        announcement_date = date(2026, 1, 2)
        available_at = financial_available_at(
            announcement_date,
            None,
            open_dates=open_dates or [date(2026, 1, 2), date(2026, 1, 3)],
        )
        if available_at > announced_before:
            return []
        return [{
            "symbol": symbol,
            "report_period": "20251231",
            "roe": 0.12,
            "operating_cashflow": 1_000_000.0,
            "net_profit": 500_000.0,
            "debt_ratio": 0.4,
            "announcement_date": announcement_date,
            "available_at": available_at,
            "source": "free_astock",
            "record_type": "indicator",
        }]

    def fetch_xdxr_frame(self, symbol: str) -> list[dict]:
        return [{
            "category": 1,
            "year": 2025,
            "month": 12,
            "day": 18,
            "fenhong": 1.0,
            "peigu": 0.0,
            "peigujia": 0.0,
            "songzhuangu": 0.0,
            "pre_close": 10.0,
        }]


def test_resolve_provider_defaults_to_free(monkeypatch, tmp_path):
    monkeypatch.delenv("TRADINGAGENTS_MARKET_DATA_PROVIDER", raising=False)
    assert resolve_provider_name(home_dir=tmp_path) == "free"


def test_resolve_provider_cli_overrides_env(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADINGAGENTS_MARKET_DATA_PROVIDER", "tushare")
    assert resolve_provider_name(cli_provider="free", home_dir=tmp_path) == "free"


def test_create_free_provider_without_token():
    provider = create_market_data_provider("free", free_backend=_MockBackend())
    assert provider.name == "free_astock"


def test_create_tushare_provider_requires_token_when_probing():
    provider = create_market_data_provider("tushare")
    assert isinstance(provider, TushareProvider)
    result = provider.probe_capabilities()
    assert result.status == DataStatus.PERMISSION_DENIED


def test_free_provider_probe_ok_with_mock_backend(monkeypatch):
    monkeypatch.setattr(
        "tradingagents.market_data.sync_policy.shanghai_today",
        lambda: date(2026, 1, 2),
    )
    provider = FreeAStockProvider(backend=_MockBackend())
    result = provider.probe_capabilities()
    assert result.status == DataStatus.OK
    datasets = {item.dataset: item.pit_level for item in result.data or []}
    assert datasets["industry_members"] == PITLevel.CURRENT_ONLY
    assert datasets["daily_bars"] == PITLevel.PIT_REQUIRED


def test_free_provider_list_securities_and_daily_snapshot(monkeypatch):
    monkeypatch.setattr(
        "tradingagents.market_data.sync_policy.shanghai_today",
        lambda: date(2026, 1, 2),
    )
    provider = FreeAStockProvider(backend=_MockBackend())
    securities = provider.list_securities(date(2026, 1, 2))
    assert securities.status == DataStatus.OK
    assert {row.symbol for row in securities.data or []} == {"600000", "000001"}

    daily = provider.get_daily_by_trade_date(date(2026, 1, 2))
    assert daily.status == DataStatus.OK
    assert daily.data[0]["symbol"] == "600000"


def test_free_provider_rejects_historical_daily_snapshot(monkeypatch):
    monkeypatch.setattr(
        "tradingagents.market_data.sync_policy.shanghai_today",
        lambda: date(2026, 6, 19),
    )
    provider = FreeAStockProvider(backend=_MockBackend())
    result = provider.get_daily_by_trade_date(date(2025, 1, 2))
    assert result.status == DataStatus.DATA_QUALITY_FAILED


def test_free_provider_board_members_are_current_only():
    provider = FreeAStockProvider(backend=_MockBackend())
    as_of = datetime(2026, 1, 2, 15, 30, tzinfo=SHANGHAI)
    result = provider.get_industry_members("BK0475", as_of)
    assert result.pit_level == PITLevel.CURRENT_ONLY
    assert result.data[0].membership_mode == MembershipMode.CURRENT_ONLY
    assert result.data[0].pit_member_on(date(2026, 1, 2))
    assert not result.data[0].pit_member_on(date(2026, 1, 1))


def test_free_provider_financials_require_announcement_date():
    provider = FreeAStockProvider(backend=_MockBackend())
    before_open = datetime(2026, 1, 2, 16, 0, tzinfo=SHANGHAI)
    result = provider.get_financials(["600000"], before_open)
    assert result.status == DataStatus.SUCCESS_EMPTY

    after_next_open = datetime(2026, 1, 5, 10, 0, tzinfo=SHANGHAI)
    result = provider.get_financials(["600000"], after_next_open)
    assert result.status == DataStatus.OK
    assert result.data[0]["announcement_date"] == date(2026, 1, 2)
    assert result.data[0]["available_at"] == datetime(2026, 1, 3, 9, 0, tzinfo=SHANGHAI)


def test_free_provider_fetch_adjustment_factor_rows():
    provider = FreeAStockProvider(backend=_MockBackend())
    result = provider.fetch_adjustment_factor_rows(["600000"])
    assert result.status == DataStatus.OK
    factors, actions = result.data
    assert factors
    assert actions


@pytest.mark.parametrize("method", [
    "list_securities",
    "get_trade_calendar",
    "get_daily_bars",
    "get_daily_indicators",
    "get_financials",
    "get_industry_members",
    "get_concept_members",
    "get_index_members",
    "probe_capabilities",
])
def test_free_provider_exposes_protocol_surface(method: str):
    provider = FreeAStockProvider(backend=_MockBackend())
    assert hasattr(provider, method)
