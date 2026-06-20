"""Event enrichment config validation (phase 5 Task 0)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tradingagents.screener.config import EventEnrichmentConfig, ScreenerConfig


def test_event_enrichment_defaults_disabled():
    config = ScreenerConfig()
    assert config.event_enrichment.enabled is False
    assert config.event_enrichment.candidate_limit == 100
    assert config.event_enrichment.event_weight == 0.20
    assert config.event_enrichment.event_half_life_days == 7


def test_rejects_unknown_event_enrichment_key():
    with pytest.raises(ValueError):
        ScreenerConfig.model_validate({
            "event_enrichment": {"enabled": False, "unknown_key": True},
        })


def test_rejects_event_weight_above_one():
    with pytest.raises(ValueError):
        EventEnrichmentConfig.model_validate({"event_weight": 1.5})


def test_rejects_negative_event_half_life_days():
    with pytest.raises(ValueError):
        EventEnrichmentConfig.model_validate({"event_half_life_days": -1})


def test_rejects_candidate_limit_below_max_positions():
    with pytest.raises(ValueError, match="candidate_limit"):
        ScreenerConfig.model_validate({
            "portfolio": {"max_positions": 20},
            "event_enrichment": {"candidate_limit": 10},
        })


def test_loads_event_enrichment_from_yaml(tmp_path: Path):
    path = tmp_path / "screener.yaml"
    path.write_text(
        """
home_dir: /tmp/tradingagents-test
event_enrichment:
  enabled: false
  candidate_limit: 100
  max_event_age_days: 30
  event_weight: 0.20
  event_half_life_days: 7
  hard_risk_filter: true
  require_announcements: false
  require_news: false
  require_fund_flow: false
""",
        encoding="utf-8",
    )
    config = ScreenerConfig.from_yaml(path)
    assert config.event_enrichment.enabled is False
    assert config.event_enrichment.require_fund_flow is False


def test_stage4_hash_ignores_disabled_event_enrichment_metadata():
    base = ScreenerConfig()
    with_event = ScreenerConfig.model_validate({
        **base.model_dump(),
        "event_enrichment": {
            "enabled": False,
            "candidate_limit": 50,
            "event_weight": 0.10,
        },
    })
    assert base.stage4_config_hash() == with_event.stage4_config_hash()


def test_event_enrichment_hash_differs_when_enabled():
    disabled = ScreenerConfig()
    enabled = ScreenerConfig.model_validate({
        **disabled.model_dump(),
        "event_enrichment": {"enabled": True},
    })
    assert disabled.stage4_config_hash() != enabled.stage4_config_hash()
