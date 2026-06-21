"""Daily indicators provider tests (remediation Task 2)."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from tradingagents.market_data.contracts import DataStatus, PITLevel
from tradingagents.market_data.providers.free_astock import FreeAStockProvider
from tradingagents.market_data.providers.free_astock_sources import (
    ProviderFetchError,
    normalize_tencent_daily_indicator_row,
    normalize_tushare_daily_indicator_row,
)
from tradingagents.market_data.providers.fixture import FixtureProvider
from tradingagents.market_data.providers.tushare import TushareProvider
from tradingagents.market_data.sync_policy import shanghai_today

SHANGHAI = ZoneInfo("Asia/Shanghai")


class _IndicatorBackend:
    def __init__(self, rows: list[dict] | None = None, *, fail_times: int = 0):
        self.rows = rows or []
        self.fail_times = fail_times
        self.calls = 0
        self.symbols_seen: list[list[str]] = []

    def list_mootdx_stocks(self) -> list[dict]:
        return [{"symbol": "600000"}, {"symbol": "000001"}]

    def fetch_tencent_daily_indicators(self, symbols: list[str]) -> list[dict[str, object]]:
        self.calls += 1
        self.symbols_seen.append(list(symbols))
        if self.fail_times > 0:
            self.fail_times -= 1
            raise ProviderFetchError("network_error", "temporary network failure")
        return list(self.rows)


def test_normalize_tencent_row_converts_yi_to_cny():
    row = normalize_tencent_daily_indicator_row(
        "600000",
        date(2026, 6, 19),
        {
            "pe_ttm": 6.5,
            "pb": 0.7,
            "turnover_pct": 2.35,
            "mcap_yi": 320.0,
            "float_mcap_yi": 290.0,
        },
        source="free_astock",
    )
    assert row["total_market_cap_cny"] == 32_000_000_000
    assert row["float_market_cap_cny"] == 29_000_000_000
    assert row["turnover_pct"] == 2.35


def test_normalize_tencent_row_rejects_negative_market_cap():
    with pytest.raises(ValueError, match="total_market_cap_cny"):
        normalize_tencent_daily_indicator_row(
            "600000",
            date(2026, 6, 19),
            {"mcap_yi": -1.0, "float_mcap_yi": 1.0},
            source="free_astock",
        )


def test_normalize_tushare_row_converts_wan_to_cny():
    row = normalize_tushare_daily_indicator_row(
        {
            "ts_code": "600000.SH",
            "pe_ttm": 6.5,
            "pb": 0.7,
            "turnover_rate": 0.4,
            "total_mv": 32_000_000.0,
            "circ_mv": 29_000_000.0,
        },
        date(2026, 6, 19),
        source="tushare",
    )
    assert row["symbol"] == "600000"
    assert row["total_market_cap_cny"] == 320_000_000_000
    assert row["float_market_cap_cny"] == 290_000_000_000


def test_free_provider_today_success():
    backend = _IndicatorBackend([{
        "symbol": "600000",
        "pe_ttm": 6.5,
        "pb": 0.7,
        "turnover_pct": 0.4,
        "mcap_yi": 320.0,
        "float_mcap_yi": 290.0,
    }])
    provider = FreeAStockProvider(backend)
    result = provider.get_daily_indicators(shanghai_today())
    assert result.status == DataStatus.OK
    assert result.pit_level == PITLevel.BEST_EFFORT
    assert len(result.data) == 1
    assert result.data[0]["total_market_cap_cny"] == 32_000_000_000


def test_free_provider_historical_date_is_not_available_yet():
    backend = _IndicatorBackend()
    provider = FreeAStockProvider(backend)
    result = provider.get_daily_indicators(date(2020, 1, 2))
    assert result.status == DataStatus.NOT_AVAILABLE_YET
    assert result.pit_level == PITLevel.BEST_EFFORT
    assert backend.calls == 0


def test_free_provider_empty_tencent_response_is_parse_error():
    backend = _IndicatorBackend([])
    provider = FreeAStockProvider(backend)
    result = provider.get_daily_indicators(shanghai_today())
    assert result.status == DataStatus.PARSE_ERROR
    assert result.data is None
    assert "no quotes" in result.errors[0]


def test_free_provider_empty_symbol_list_is_error():
    class _EmptyUniverseBackend(_IndicatorBackend):
        def list_mootdx_stocks(self) -> list[dict]:
            return []

    backend = _EmptyUniverseBackend()
    provider = FreeAStockProvider(backend)
    result = provider.get_daily_indicators(shanghai_today())
    assert result.status == DataStatus.DATA_QUALITY_FAILED
    assert result.data is None
    assert backend.calls == 0


def test_free_provider_all_parse_failures_is_parse_error():
    backend = _IndicatorBackend([{
        "symbol": "600000",
        "mcap_yi": -1.0,
        "float_mcap_yi": 1.0,
    }])
    provider = FreeAStockProvider(backend)
    result = provider.get_daily_indicators(shanghai_today())
    assert result.status == DataStatus.PARSE_ERROR
    assert "failed validation" in result.errors[0]


def test_free_provider_default_batch_pause_is_positive():
    provider = FreeAStockProvider(_IndicatorBackend())
    assert provider._batch_pause > 0


def test_free_provider_batch_pause_adds_up_to_half_second_jitter():
    symbols = [f"{index:06d}" for index in range(100)]

    class _ManySymbolBackend(_IndicatorBackend):
        def list_mootdx_stocks(self) -> list[dict]:
            return [{"symbol": symbol} for symbol in symbols]

    sleeps: list[float] = []
    provider = FreeAStockProvider(
        _ManySymbolBackend(),
        batch_size=80,
        batch_pause=0.3,
        sleeper=sleeps.append,
        random_fn=lambda _low, high: high,
    )
    provider.get_daily_indicators(shanghai_today())
    assert sleeps == [0.8]


def test_free_provider_http_error():
    class _HttpBackend(_IndicatorBackend):
        def fetch_tencent_daily_indicators(self, symbols: list[str]) -> list[dict[str, object]]:
            raise ProviderFetchError("http_error", "HTTP 500")

    provider = FreeAStockProvider(_HttpBackend())
    result = provider.get_daily_indicators(shanghai_today())
    assert result.status == DataStatus.HTTP_ERROR
    assert result.data is None


def test_free_provider_rate_limited():
    class _RateBackend(_IndicatorBackend):
        def fetch_tencent_daily_indicators(self, symbols: list[str]) -> list[dict[str, object]]:
            raise ProviderFetchError("rate_limited", "HTTP 429")

    provider = FreeAStockProvider(_RateBackend())
    result = provider.get_daily_indicators(shanghai_today())
    assert result.status == DataStatus.RATE_LIMITED


def test_free_provider_retries_until_third_attempt_succeeds():
    backend = _IndicatorBackend([{
        "symbol": "600000",
        "pe_ttm": 1.0,
        "pb": 1.0,
        "turnover_pct": 1.0,
        "mcap_yi": 10.0,
        "float_mcap_yi": 9.0,
    }], fail_times=2)
    sleeps: list[float] = []
    provider = FreeAStockProvider(
        backend,
        sleeper=sleeps.append,
        retry_base_delay=0.0,
    )
    result = provider.get_daily_indicators(shanghai_today())
    assert result.status == DataStatus.OK
    assert backend.calls == 3
    assert sleeps == [0.0, 0.0]


def test_free_provider_batches_symbols_without_real_sleep():
    symbols = [f"{index:06d}" for index in range(100)]

    class _ManySymbolBackend(_IndicatorBackend):
        def list_mootdx_stocks(self) -> list[dict]:
            return [{"symbol": symbol} for symbol in symbols]

    many_backend = _ManySymbolBackend()
    sleeps: list[float] = []
    provider = FreeAStockProvider(
        many_backend,
        batch_size=80,
        batch_pause=0.5,
        sleeper=sleeps.append,
        random_fn=lambda _a, _b: 0.0,
    )
    provider.get_daily_indicators(shanghai_today())
    assert len(many_backend.symbols_seen) == 2
    assert len(many_backend.symbols_seen[0]) == 80
    assert len(many_backend.symbols_seen[1]) == 20
    assert sleeps == [0.5]


def test_fixture_provider_uses_best_effort_and_canonical_fields():
    provider = FixtureProvider({"symbols": [{"symbol": "600000"}]})
    result = provider.get_daily_indicators(date(2026, 6, 19))
    assert result.status == DataStatus.SUCCESS_EMPTY
    assert result.pit_level == PITLevel.BEST_EFFORT


def test_tushare_mapping_uses_canonical_fields(monkeypatch):
    from tradingagents.market_data.contracts import DataResult, DataStatus, PITLevel

    provider = TushareProvider(token="test-token")

    class _Frame:
        @staticmethod
        def to_dict(orient):
            _ = orient
            return [{
                "ts_code": "600000.SH",
                "pe_ttm": 6.5,
                "pb": 0.7,
                "turnover_rate": 0.4,
                "total_mv": 32000000.0,
                "circ_mv": 29000000.0,
            }]

    run_time = datetime(2026, 6, 19, 15, 30, tzinfo=SHANGHAI)

    def _wrap_call(call):
        rows = call()
        return DataResult(
            data=rows,
            status=DataStatus.OK,
            source=provider.name,
            as_of=run_time,
            available_at=run_time,
            pit_level=PITLevel.PIT_REQUIRED,
        )

    monkeypatch.setattr(provider, "_query", lambda *_args, **_kwargs: _Frame())
    monkeypatch.setattr(provider, "_wrap_call", _wrap_call)
    result = provider.get_daily_indicators(date(2026, 6, 19))
    row = result.data[0]
    assert row["pe_ttm"] == 6.5
    assert row["turnover_pct"] == 0.4
    assert row["total_market_cap_cny"] == 320_000_000_000
