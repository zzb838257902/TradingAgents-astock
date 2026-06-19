from datetime import date

from tradingagents.market_data.contracts import DataStatus, PITLevel
from tradingagents.market_data.providers.existing_astock import ExistingAStockProvider


def test_daily_bars_are_normalized_and_pit(monkeypatch):
    csv = "Date,Open,High,Low,Close,Volume\n2026-06-18,10,11,9,10.5,1000\n"
    monkeypatch.setattr(
        "tradingagents.market_data.providers.existing_astock.get_stock_data",
        lambda symbol, start, end: "# source\n" + csv,
    )
    result = ExistingAStockProvider().get_daily_bars(
        ["600000"], date(2026, 6, 18), date(2026, 6, 18)
    )
    assert result.status == DataStatus.OK
    assert result.pit_level == PITLevel.PIT_REQUIRED
    assert result.data[0]["symbol"] == "600000"
    assert result.data[0]["close"] == 10.5


def test_error_text_becomes_error_status(monkeypatch):
    monkeypatch.setattr(
        "tradingagents.market_data.providers.existing_astock.get_stock_data",
        lambda *args: "K线数据获取失败：source unavailable",
    )
    result = ExistingAStockProvider().get_daily_bars(
        ["600000"], date(2026, 6, 18), date(2026, 6, 18)
    )
    assert result.status == DataStatus.ERROR
    assert result.data is None


def test_malformed_row_rejects_complete_symbol_batch(monkeypatch):
    csv = "Date,Open,High,Low,Close,Volume\n2026-06-18,10,11,9,bad,1000\n"
    monkeypatch.setattr(
        "tradingagents.market_data.providers.existing_astock.get_stock_data",
        lambda *args: "# source\n" + csv,
    )
    result = ExistingAStockProvider().get_daily_bars(
        ["600000"], date(2026, 6, 18), date(2026, 6, 18)
    )
    assert result.status == DataStatus.ERROR
    assert result.data is None
    assert "600000 row 2" in result.errors[0]
