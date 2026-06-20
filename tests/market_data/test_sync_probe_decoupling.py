"""Probe decoupling: partial probe must not block unrelated datasets."""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from tradingagents.market_data.config import MarketDataPaths
from tradingagents.market_data.contracts import (
    DataStatus,
    ProviderCapability,
    PITLevel,
)
from tradingagents.market_data.providers.free_astock import FreeAStockProvider
from tradingagents.market_data.repository import MarketDataRepository
from tradingagents.market_data.sync import MarketDataSync, SyncStatus
from tests.market_data.test_free_astock_provider import _MockBackend

SHANGHAI = ZoneInfo("Asia/Shanghai")


class _PartialProbeProvider(FreeAStockProvider):
    def probe_capabilities(self):
        run_time = datetime(2026, 1, 2, 15, 30, tzinfo=SHANGHAI)
        capabilities = [
            ProviderCapability(
                dataset="security_master",
                endpoint="mootdx.stocks",
                permitted=True,
                pit_level=PITLevel.PIT_REQUIRED,
                license_note="ok",
                probed_at=run_time,
            ),
            ProviderCapability(
                dataset="trade_calendar",
                endpoint="sina.index_kline",
                permitted=True,
                pit_level=PITLevel.PIT_REQUIRED,
                license_note="ok",
                probed_at=run_time,
            ),
            ProviderCapability(
                dataset="daily_bars",
                endpoint="eastmoney",
                permitted=False,
                pit_level=PITLevel.PIT_REQUIRED,
                license_note="blocked",
                probed_at=run_time,
                error="eastmoney down",
            ),
        ]
        from tradingagents.market_data.providers.free_astock import _result

        return _result(
            capabilities,
            status=DataStatus.PARTIAL,
            source=self.name,
            as_of=run_time,
            available_at=run_time,
            pit_level=PITLevel.PIT_REQUIRED,
            errors=["daily_bars: eastmoney down"],
        )


def test_partial_probe_is_persisted_and_trade_calendar_syncs(tmp_path):
    paths = MarketDataPaths(home_dir=tmp_path)
    repo = MarketDataRepository(paths.live_db_path, snapshot_dir=paths.snapshot_dir)
    provider = _PartialProbeProvider(backend=_MockBackend())
    sync = MarketDataSync(repo, provider, paths)

    probe = sync.probe_capabilities()
    assert probe.status == SyncStatus.PUBLISHED
    assert probe.errors

    stored = repo.get_capability_probe()
    assert stored is not None
    assert stored["trade_calendar"]["permitted"] is True
    assert stored["daily_bars"]["permitted"] is False

    result = sync.sync_trade_calendar(date(2026, 1, 1), date(2026, 1, 3))
    assert result.status == SyncStatus.PUBLISHED
    assert repo.list_open_trade_dates()


def test_daily_sync_blocked_when_daily_bars_not_permitted(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "tradingagents.market_data.sync_policy.shanghai_today",
        lambda: date(2026, 1, 2),
    )
    paths = MarketDataPaths(home_dir=tmp_path)
    repo = MarketDataRepository(paths.live_db_path, snapshot_dir=paths.snapshot_dir)
    provider = _PartialProbeProvider(backend=_MockBackend())
    sync = MarketDataSync(repo, provider, paths)
    sync.probe_capabilities()

    result = sync.sync_daily(date(2026, 1, 2))
    assert result.status == SyncStatus.ERROR
    assert result.errors
    assert "eastmoney" in result.errors[0].lower()


def test_sina_financial_report_handles_null_result():
    from tradingagents.dataflows import a_stock

    class _Resp:
        def json(self):
            return {"result": None}

    with patch.object(a_stock._requests, "get", return_value=_Resp()):
        frame = a_stock._get_financial_report_sina("600000", "利润表", "quarterly")
    assert frame.empty


def test_sina_financial_report_parses_report_list_payload():
    from tradingagents.dataflows import a_stock

    payload = {
        "result": {
            "data": {
                "report_list": {
                    "20251231": {
                        "publish_date": "20260331",
                        "data": [
                            {
                                "item_field": "NETPROFIT",
                                "item_title": "净利润",
                                "item_value": "100.0",
                            },
                            {
                                "item_field": "MANANETR",
                                "item_title": "经营活动产生的现金流量净额",
                                "item_value": "200.0",
                            },
                        ],
                    }
                }
            }
        }
    }

    class _Resp:
        def json(self):
            return payload

    with patch.object(a_stock._requests, "get", return_value=_Resp()):
        frame = a_stock._get_financial_report_sina("600000", "利润表", "quarterly")
    assert not frame.empty
    assert float(frame.iloc[0]["净利润"]) == 100.0
    assert frame.iloc[0]["报告日"] == "20251231"


def test_sina_financial_report_rejects_stale_comparison_publish_date():
    from tradingagents.dataflows import a_stock

    payload = {
        "result": {
            "data": {
                "report_list": {
                    "20240630": {
                        "publish_date": "20250828",
                        "data": [
                            {
                                "item_field": "NETPROFIT",
                                "item_title": "净利润",
                                "item_value": "100.0",
                            },
                        ],
                    },
                    "20250630": {
                        "publish_date": "20250828",
                        "data": [
                            {
                                "item_field": "NETPROFIT",
                                "item_title": "净利润",
                                "item_value": "200.0",
                            },
                        ],
                    },
                }
            }
        }
    }

    class _Resp:
        def json(self):
            return payload

    with patch.object(a_stock._requests, "get", return_value=_Resp()):
        frame = a_stock._get_financial_report_sina("600000", "利润表", "quarterly")
    by_period = {str(row["报告日"]): row["公告日期"] for _, row in frame.iterrows()}
    assert by_period["20240630"] == "2024-08-31"
    assert by_period["20250630"] == "2025-08-28"
