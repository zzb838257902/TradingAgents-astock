"""Tests for the seven-analyst CLI registry (remediation Task 5)."""

from __future__ import annotations

import cli.main as main_cli
from cli.analyst_registry import (
    ANALYST_AGENT_NAMES,
    ANALYST_CHOICES,
    ANALYST_ORDER,
    ANALYST_OUTPUT_FILENAMES,
    ANALYST_REPORT_MAP,
    ANALYST_SPECS,
    ANALYST_TEAM_AGENT_NAMES,
    ORIGINAL_FOUR_ANALYST_KEYS,
    analyst_report_sections,
    normalize_selected_analyst_keys,
    report_section_output_filename,
)
from cli.models import AnalystType
from cli.utils import ANALYST_CHOICES as UTILS_ANALYST_CHOICES


def test_registry_defines_seven_analysts_in_fixed_order():
    assert ANALYST_ORDER == [
        "market",
        "social",
        "news",
        "fundamentals",
        "policy",
        "hot_money",
        "lockup",
    ]
    assert len(ANALYST_SPECS) == 7


def test_registry_maps_graph_nodes_and_report_keys():
    expected = {
        "market": ("Market Analyst", "market_report", "market.md"),
        "social": ("Social Analyst", "sentiment_report", "sentiment.md"),
        "news": ("News Analyst", "news_report", "news.md"),
        "fundamentals": (
            "Fundamentals Analyst",
            "fundamentals_report",
            "fundamentals.md",
        ),
        "policy": ("Policy Analyst", "policy_report", "policy.md"),
        "hot_money": ("Hot_money Analyst", "hot_money_report", "hot_money.md"),
        "lockup": ("Lockup Analyst", "lockup_report", "lockup.md"),
    }
    for key, (node_name, report_key, filename) in expected.items():
        spec = next(item for item in ANALYST_SPECS if item.key == key)
        assert spec.graph_node_name == node_name
        assert spec.report_key == report_key
        assert spec.output_filename == filename
        assert ANALYST_AGENT_NAMES[key] == node_name
        assert ANALYST_REPORT_MAP[key] == report_key
        assert ANALYST_OUTPUT_FILENAMES[key] == filename


def test_utils_and_main_import_registry_derivatives():
    assert UTILS_ANALYST_CHOICES == ANALYST_CHOICES
    assert main_cli.ANALYST_ORDER == ANALYST_ORDER
    assert main_cli.ANALYST_AGENT_NAMES == ANALYST_AGENT_NAMES
    assert main_cli.ANALYST_REPORT_MAP == ANALYST_REPORT_MAP


def test_original_four_analyst_mappings_regression():
    assert ORIGINAL_FOUR_ANALYST_KEYS == ("market", "social", "news", "fundamentals")
    assert ANALYST_REPORT_MAP["social"] == "sentiment_report"
    assert ANALYST_AGENT_NAMES["social"] == "Social Analyst"


def test_normalize_selected_analyst_keys_preserves_registry_order():
    selected = normalize_selected_analyst_keys([
        AnalystType.LOCKUP,
        AnalystType.MARKET,
        AnalystType.POLICY,
    ])
    assert selected == ["market", "policy", "lockup"]


def test_analyst_report_sections_cover_all_report_keys():
    sections = analyst_report_sections()
    assert set(sections) == {spec.report_key for spec in ANALYST_SPECS}
    assert sections["hot_money_report"] == ("hot_money", "Hot_money Analyst")


def test_report_section_output_filename_uses_registry_names():
    assert report_section_output_filename("policy_report") == "policy.md"
    assert report_section_output_filename("hot_money_report") == "hot_money.md"
    assert report_section_output_filename("investment_plan") == "investment_plan.md"


def test_analyst_team_agent_names_lists_all_seven_graph_nodes():
    assert len(ANALYST_TEAM_AGENT_NAMES) == 7
    assert "Policy Analyst" in ANALYST_TEAM_AGENT_NAMES
    assert "Hot_money Analyst" in ANALYST_TEAM_AGENT_NAMES
    assert "Lockup Analyst" in ANALYST_TEAM_AGENT_NAMES
    assert list(ANALYST_AGENT_NAMES.values()) == list(ANALYST_TEAM_AGENT_NAMES)


def test_message_buffer_unknown_section_title_falls_back_to_key():
    buffer = main_cli.MessageBuffer()
    buffer.report_sections["plugin_section"] = "plugin content"
    buffer._update_current_report()
    assert buffer.current_report == "### plugin_section\nplugin content"
