"""Canonical registry for selectable analyst agents and report mappings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

from cli.models import AnalystType


@dataclass(frozen=True)
class AnalystSpec:
    key: str
    graph_node_name: str
    report_key: str
    title: str
    output_filename: str


ANALYST_SPECS: tuple[AnalystSpec, ...] = (
    AnalystSpec(
        "market",
        "Market Analyst",
        "market_report",
        "Market Analysis",
        "market.md",
    ),
    AnalystSpec(
        "social",
        "Social Analyst",
        "sentiment_report",
        "Social Sentiment",
        "sentiment.md",
    ),
    AnalystSpec(
        "news",
        "News Analyst",
        "news_report",
        "News Analysis",
        "news.md",
    ),
    AnalystSpec(
        "fundamentals",
        "Fundamentals Analyst",
        "fundamentals_report",
        "Fundamentals Analysis",
        "fundamentals.md",
    ),
    AnalystSpec(
        "policy",
        "Policy Analyst",
        "policy_report",
        "Policy Analysis",
        "policy.md",
    ),
    AnalystSpec(
        "hot_money",
        # Must match tradingagents/graph/setup.py: analyst_type.capitalize() + " Analyst".
        # Do not switch to title(); "hot_money".title() -> "Hot_Money Analyst" breaks the graph.
        "Hot_money Analyst",
        "hot_money_report",
        "Hot Money / Capital Flow",
        "hot_money.md",
    ),
    AnalystSpec(
        "lockup",
        "Lockup Analyst",
        "lockup_report",
        "Lockup Expiry / Insider Reduction",
        "lockup.md",
    ),
)

ANALYST_ORDER: list[str] = [spec.key for spec in ANALYST_SPECS]
ANALYST_AGENT_NAMES: dict[str, str] = {
    spec.key: spec.graph_node_name for spec in ANALYST_SPECS
}
ANALYST_TEAM_AGENT_NAMES: tuple[str, ...] = tuple(
    spec.graph_node_name for spec in ANALYST_SPECS
)
ANALYST_REPORT_MAP: dict[str, str] = {
    spec.key: spec.report_key for spec in ANALYST_SPECS
}
ANALYST_SECTION_TITLES: dict[str, str] = {
    spec.report_key: spec.title for spec in ANALYST_SPECS
}
ANALYST_OUTPUT_FILENAMES: dict[str, str] = {
    spec.key: spec.output_filename for spec in ANALYST_SPECS
}
ANALYST_REPORT_FILENAMES: dict[str, str] = {
    spec.report_key: spec.output_filename for spec in ANALYST_SPECS
}

ANALYST_CHOICES: list[tuple[str, AnalystType]] = [
    (spec.graph_node_name, AnalystType(spec.key)) for spec in ANALYST_SPECS
]

FIXED_REPORT_SECTIONS: dict[str, tuple[None, str]] = {
    "investment_plan": (None, "Research Manager"),
    "trader_investment_plan": (None, "Trader"),
    "final_trade_decision": (None, "Portfolio Manager"),
}

FIXED_SECTION_TITLES: dict[str, str] = {
    "investment_plan": "Research Team Decision",
    "trader_investment_plan": "Trading Team Plan",
    "final_trade_decision": "Portfolio Management Decision",
}

ORIGINAL_FOUR_ANALYST_KEYS: tuple[str, ...] = (
    "market",
    "social",
    "news",
    "fundamentals",
)


def iter_analyst_specs() -> Iterator[AnalystSpec]:
    return iter(ANALYST_SPECS)


def analyst_report_sections() -> dict[str, tuple[str, str]]:
    return {
        spec.report_key: (spec.key, spec.graph_node_name)
        for spec in ANALYST_SPECS
    }


def all_report_sections() -> dict[str, tuple[str | None, str]]:
    return {**analyst_report_sections(), **FIXED_REPORT_SECTIONS}


def collect_analyst_report_parts(final_state: dict) -> list[tuple[str, str]]:
    parts: list[tuple[str, str]] = []
    for spec in ANALYST_SPECS:
        content = final_state.get(spec.report_key)
        if content:
            parts.append((spec.graph_node_name, content))
    return parts


def normalize_selected_analyst_keys(selected: list[str | AnalystType]) -> list[str]:
    selected_set = {
        item.value if isinstance(item, AnalystType) else str(item).lower()
        for item in selected
    }
    return [key for key in ANALYST_ORDER if key in selected_set]


def report_section_output_filename(section_name: str) -> str:
    return ANALYST_REPORT_FILENAMES.get(section_name, f"{section_name}.md")
