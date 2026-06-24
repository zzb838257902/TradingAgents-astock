"""Report assembly and persistence tests for seven analysts (Task 5)."""

from __future__ import annotations

import cli.main as main_cli
from cli.analyst_registry import ANALYST_SPECS, collect_analyst_report_parts
from cli.models import AnalystType


def _sample_final_state() -> dict:
    state = {
        "investment_debate_state": {
            "bull_history": "bull case",
            "bear_history": "bear case",
            "judge_decision": "research verdict",
        },
        "trader_investment_plan": "trade plan",
        "risk_debate_state": {
            "aggressive_history": "aggressive",
            "conservative_history": "conservative",
            "neutral_history": "neutral",
            "judge_decision": "portfolio verdict",
        },
    }
    for spec in ANALYST_SPECS:
        state[spec.report_key] = f"{spec.key} body"
    return state


def test_message_buffer_initializes_selected_analyst_sections():
    buffer = main_cli.MessageBuffer()
    buffer.init_for_analysis(["policy", "market", "lockup"])

    assert buffer.selected_analysts == ["policy", "market", "lockup"]
    assert buffer.agent_status["Market Analyst"] == "pending"
    assert buffer.agent_status["Policy Analyst"] == "pending"
    assert buffer.agent_status["Lockup Analyst"] == "pending"
    assert "News Analyst" not in buffer.agent_status

    assert set(buffer.report_sections) == {
        "market_report",
        "policy_report",
        "lockup_report",
        "investment_plan",
        "trader_investment_plan",
        "final_trade_decision",
    }


def test_update_analyst_statuses_marks_completed_reports(tmp_path):
    del tmp_path
    buffer = main_cli.MessageBuffer()
    buffer.init_for_analysis(["market", "social"])

    main_cli.update_analyst_statuses(buffer, {"market_report": "market body"})
    assert buffer.agent_status["Market Analyst"] == "completed"
    assert buffer.agent_status["Social Analyst"] == "in_progress"

    main_cli.update_analyst_statuses(buffer, {"sentiment_report": "social body"})
    assert buffer.agent_status["Social Analyst"] == "completed"
    assert buffer.agent_status["Bull Researcher"] == "in_progress"


def test_save_report_to_disk_writes_seven_analyst_files_and_complete_report(tmp_path):
    final_state = _sample_final_state()
    save_path = tmp_path / "reports"

    main_cli.save_report_to_disk(final_state, "600001", save_path)

    analysts_dir = save_path / "1_analysts"
    for spec in ANALYST_SPECS:
        path = analysts_dir / spec.output_filename
        assert path.exists(), spec.key
        assert path.read_text(encoding="utf-8") == f"{spec.key} body"

    complete = (save_path / "complete_report.md").read_text(encoding="utf-8")
    for spec in ANALYST_SPECS:
        assert spec.graph_node_name in complete
        assert f"{spec.key} body" in complete
    assert "Research Manager" in complete
    assert "Portfolio Manager" in complete


def test_collect_analyst_report_parts_respects_registry_order():
    parts = collect_analyst_report_parts(_sample_final_state())
    assert [name for name, _ in parts] == [spec.graph_node_name for spec in ANALYST_SPECS]


def test_four_analyst_subset_still_saves_core_files(tmp_path):
    final_state = {
        "market_report": "market",
        "sentiment_report": "sentiment",
        "news_report": "news",
        "fundamentals_report": "fundamentals",
    }
    save_path = tmp_path / "subset"
    main_cli.save_report_to_disk(final_state, "SPY", save_path)

    analysts_dir = save_path / "1_analysts"
    assert (analysts_dir / "market.md").read_text(encoding="utf-8") == "market"
    assert (analysts_dir / "sentiment.md").read_text(encoding="utf-8") == "sentiment"
    assert not (analysts_dir / "policy.md").exists()

    complete = (save_path / "complete_report.md").read_text(encoding="utf-8")
    assert "Market Analyst" in complete
    assert "Policy Analyst" not in complete


def test_analyst_type_enum_exposes_all_seven_values():
    assert {item.value for item in AnalystType} == set(main_cli.ANALYST_ORDER)
