from datetime import date, datetime, timezone

from tradingagents.market_data.contracts import SecurityRecord
from tradingagents.market_data.repository import MarketDataRepository


def security(symbol: str, valid_from: date, valid_to: date | None = None):
    return SecurityRecord(
        symbol=symbol,
        name=symbol,
        board="main",
        valid_from=valid_from,
        valid_to=valid_to,
        list_date=valid_from,
        delist_date=valid_to,
        status="listed",
        st_flag=False,
        available_at=datetime.combine(valid_from, datetime.min.time(), timezone.utc),
        source="fixture",
    )


def test_historical_security_query_includes_later_delisted_stock(tmp_path):
    repo = MarketDataRepository(tmp_path / "market.duckdb")
    repo.upsert_security_records([
        security("600001", date(2010, 1, 1), date(2024, 1, 1)),
        security("600002", date(2010, 1, 1)),
    ])
    assert repo.list_effective_symbols(date(2023, 1, 3)) == ["600001", "600002"]
    assert repo.list_effective_symbols(date(2025, 1, 3)) == ["600002"]


def test_daily_bar_query_cannot_return_future_rows(tmp_path):
    repo = MarketDataRepository(tmp_path / "market.duckdb")
    repo.upsert_daily_bars([
        {"symbol": "600002", "trade_date": date(2025, 1, 2), "close": 10.0,
         "open": 9.8, "high": 10.2, "low": 9.7, "volume": 1000,
         "amount": 10000.0, "available_at": datetime(2025, 1, 2, 7, 0, tzinfo=timezone.utc),
         "source": "fixture"},
        {"symbol": "600002", "trade_date": date(2025, 1, 3), "close": 11.0,
         "open": 10.0, "high": 11.2, "low": 9.9, "volume": 1200,
         "amount": 12000.0, "available_at": datetime(2025, 1, 3, 7, 0, tzinfo=timezone.utc),
         "source": "fixture"},
    ])
    rows = repo.get_daily_bars(["600002"], end=date(2025, 1, 2))
    assert [row["trade_date"] for row in rows] == [date(2025, 1, 2)]
