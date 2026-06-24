from datetime import date

from tradingagents.screener.models import CandidateInput, UniverseResult


def count_trading_days_after(
    list_date: date, as_of: date, trading_dates: list[date]
) -> int:
    return sum(1 for value in trading_dates if list_date < value <= as_of)


def count_listing_trading_days(
    list_date: date,
    as_of: date,
    open_trade_dates: list[date],
) -> int:
    """Count open trade days strictly after list_date through as_of."""
    return sum(1 for value in open_trade_dates if list_date < value <= as_of)


def filter_universe(
    candidates: list[CandidateInput],
    as_of: date,
    min_listing_days: int,
    min_avg_amount_20d: float,
    trading_dates: list[date] | None = None,
    *,
    listing_trade_dates: list[date] | None = None,
) -> UniverseResult:
    included = []
    excluded: dict[str, list[str]] = {}
    listing_calendar: list[date] | None
    if listing_trade_dates:
        listing_calendar = sorted(listing_trade_dates)
    elif trading_dates:
        listing_calendar = sorted(trading_dates)
    else:
        listing_calendar = None
    for item in candidates:
        reasons = []
        if item.st_flag:
            reasons.append("st")
        if listing_calendar is not None:
            if count_listing_trading_days(item.list_date, as_of, listing_calendar) < min_listing_days:
                reasons.append("new_listing")
        elif (as_of - item.list_date).days < min_listing_days:
            reasons.append("new_listing")
        if item.suspended:
            reasons.append("suspended")
        if item.avg_amount_20d < min_avg_amount_20d:
            reasons.append("illiquid")
        if reasons:
            excluded[item.symbol] = reasons
        else:
            included.append(item)
    return UniverseResult(included=included, excluded_reasons=excluded)
