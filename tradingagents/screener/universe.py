from datetime import date

from tradingagents.screener.models import CandidateInput, UniverseResult


def filter_universe(
    candidates: list[CandidateInput], as_of: date,
    min_listing_days: int, min_avg_amount_20d: float,
) -> UniverseResult:
    included = []
    excluded: dict[str, list[str]] = {}
    for item in candidates:
        reasons = []
        if item.st_flag:
            reasons.append("st")
        if (as_of - item.list_date).days < min_listing_days:
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
