from datetime import date

from tradingagents.screener.models import CandidateInput, UniverseResult


def count_trading_days_after(
    list_date: date, as_of: date, trading_dates: list[date]
) -> int:
    return sum(1 for value in trading_dates if list_date < value <= as_of)


def filter_universe(
    candidates: list[CandidateInput],
    as_of: date,
    min_listing_days: int,
    min_avg_amount_20d: float,
    trading_dates: list[date] | None = None,
) -> UniverseResult:
    included = []
    excluded: dict[str, list[str]] = {}
    calendar = sorted(trading_dates) if trading_dates else None
    for item in candidates:
        reasons = []
        if item.st_flag:
            reasons.append("st")
        if calendar is not None:
            if count_trading_days_after(item.list_date, as_of, calendar) < min_listing_days:
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
