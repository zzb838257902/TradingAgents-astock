"""FreeAStockProvider event fetch tests (phase 5 Task 5)."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

from tradingagents.market_data.contracts import DataStatus, PITLevel
from tradingagents.market_data.providers.free_astock import FreeAStockProvider
from tradingagents.market_data.providers.free_astock_sources import (
    ProviderFetchError,
    parse_sina_bulletin_html,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")
SAMPLE_HTML = Path("tests/fixtures/events/sina_bulletin_sample.html").read_text(encoding="utf-8")


class _EventBackend:
    def list_mootdx_stocks(self):
        return [{"symbol": "600000", "name": "浦发银行", "board": "main", "list_date": None}]

    def fetch_sse_trade_dates(self, start, end):
        return [date(2026, 6, 5), date(2026, 6, 8)]

    def fetch_eastmoney_daily_snapshot(self, trade_date):
        return []

    def fetch_eastmoney_board_members(self, board_code):
        return ["600000"]

    def fetch_sina_financial_rows(self, symbol, announced_before):
        return []

    def fetch_xdxr_frame(self, symbol):
        return []

    def fetch_sina_bulletin_rows(self, symbol: str, page: int = 1):
        if symbol == "600999":
            raise ProviderFetchError("network_error", "connection reset")
        return parse_sina_bulletin_html(SAMPLE_HTML, symbol)

    def fetch_eastmoney_news_rows(self, symbol: str):
        return []

    def fetch_eastmoney_fund_flow_row(self, symbol: str, trade_date: date):
        return None

    def fetch_ths_hot_topic_rows(self, trade_date: date):
        return []


def test_parse_sina_bulletin_html_extracts_rows():
    rows = parse_sina_bulletin_html(SAMPLE_HTML, "600000")
    assert len(rows) == 2
    assert rows[0]["source_record_id"] == "900001"
    assert rows[1]["title"].startswith("关于收到")


def test_free_provider_fetch_announcements_offline():
    provider = FreeAStockProvider(backend=_EventBackend())
    result = provider.fetch_announcements(
        ["600000"],
        date(2026, 6, 1),
        date(2026, 6, 30),
    )
    assert result.status == DataStatus.OK
    assert len(result.data or []) == 1
    assert result.pit_level == PITLevel.PIT_REQUIRED


def test_free_provider_network_error_is_not_success_empty():
    provider = FreeAStockProvider(backend=_EventBackend())
    result = provider.fetch_announcements(
        ["600999"],
        date(2026, 6, 1),
        date(2026, 6, 30),
    )
    assert result.status == DataStatus.NETWORK_ERROR
    assert result.data is None


def test_free_provider_probe_event_capabilities_offline():
    provider = FreeAStockProvider(backend=_EventBackend())
    result = provider.probe_event_capabilities()
    datasets = {item.dataset for item in result.data or []}
    assert "official_announcements" in datasets
