from datetime import date

from tradingagents.screener.models import CandidateInput
from tradingagents.screener.universe import filter_universe


def candidate(symbol: str, **changes):
    values = {
        "symbol": symbol,
        "name": symbol,
        "industry": "电子",
        "list_date": date(2020, 1, 1),
        "st_flag": False,
        "suspended": False,
        "avg_amount_20d": 100_000_000,
    }
    values.update(changes)
    return CandidateInput(**values)


def test_filters_st_new_suspended_and_illiquid_stocks():
    result = filter_universe(
        [
            candidate("A"),
            candidate("B", st_flag=True),
            candidate("C", list_date=date(2025, 12, 20)),
            candidate("D", suspended=True),
            candidate("E", avg_amount_20d=10_000),
        ],
        as_of=date(2026, 1, 5),
        min_listing_days=60,
        min_avg_amount_20d=50_000_000,
    )
    assert [item.symbol for item in result.included] == ["A"]
    assert result.excluded_reasons == {
        "B": ["st"], "C": ["new_listing"], "D": ["suspended"],
        "E": ["illiquid"]
    }
