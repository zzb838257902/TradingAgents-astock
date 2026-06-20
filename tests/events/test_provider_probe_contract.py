"""Event provider capability matrix and probe contract (phase 5 Task 1)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from tradingagents.events.provider_capabilities import (
    REQUIRED_EVENT_DATASETS,
    REQUIRED_PROBE_FIELDS,
    core_announcement_gate_status,
    load_event_capability_matrix,
    validate_event_capability_matrix,
)

RECORDED_DIR = Path("tests/fixtures/events/recorded")
MATRIX_PATH = Path("docs/data/data-capability-matrix.yaml")


def test_event_capability_matrix_file_exists():
    matrix = load_event_capability_matrix()
    assert "event_datasets" in matrix
    assert REQUIRED_EVENT_DATASETS <= set(matrix["event_datasets"])


@pytest.mark.parametrize("field", REQUIRED_PROBE_FIELDS)
def test_each_event_dataset_declares_probe_fields(field: str):
    matrix = load_event_capability_matrix()
    for name, definition in matrix["event_datasets"].items():
        primary = definition["primary_source"]
        assert field in primary, f"{name}.primary_source missing {field}"


def test_official_announcements_covers_four_boards():
    matrix = load_event_capability_matrix()
    boards = set(matrix["event_datasets"]["official_announcements"]["markets"])
    assert boards == {"sse_main", "szse_main", "chinext", "star"}


def test_official_announcements_primary_is_not_news_source():
    matrix = load_event_capability_matrix()
    primary_id = matrix["event_datasets"]["official_announcements"]["primary_source"]["id"]
    forbidden = matrix["event_datasets"]["official_announcements"]["forbidden_substitutes"]
    assert primary_id not in forbidden
    assert "eastmoney.search.cmsArticleWebOld" in forbidden
    assert "sina.corp.vCB_AllNewsStock" in forbidden


def test_eastmoney_is_optional_enhancement_not_core_requirement():
    matrix = load_event_capability_matrix()
    ann = matrix["event_datasets"]["official_announcements"]
    optional = ann.get("optional_enhancement") or {}
    assert optional.get("id", "").startswith("eastmoney")
    assert optional.get("blocks_core_on_failure") is False
    news = matrix["event_datasets"]["event_news"]
    assert news["primary_source"]["id"].startswith("eastmoney") or "eastmoney" in news["primary_source"]["id"]


def test_core_announcement_gate_passes_with_documented_free_source():
    matrix = load_event_capability_matrix()
    assert validate_event_capability_matrix(matrix) == []
    assert core_announcement_gate_status(matrix) == "PASS"


def test_network_errors_must_not_map_to_success_empty():
    matrix = load_event_capability_matrix()
    for name, definition in matrix["event_datasets"].items():
        semantics = definition["primary_source"]["empty_semantics"]
        assert semantics != "treat_network_error_as_empty", name
        assert "NETWORK_ERROR" in definition["primary_source"]["error_types"], name


def test_recorded_fixture_contract_catalog_exists():
    readme = RECORDED_DIR / "README.md"
    assert readme.is_file()
    text = readme.read_text(encoding="utf-8")
    for scenario in (
        "with_announcements",
        "no_announcements",
        "revised_announcement",
        "pagination",
        "rate_limited",
        "network_error",
    ):
        assert scenario in text


def test_recorded_metadata_fixtures_are_desensitized():
    meta_files = sorted(RECORDED_DIR.glob("*_meta.json"))
    assert len(meta_files) >= 4
    for path in meta_files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload.get("desensitized") is True
        assert payload.get("full_text_saved") is False
        if payload.get("error_type"):
            assert payload.get("must_not_map_to") == "SUCCESS_EMPTY"
        else:
            assert "response_sha256" in payload


def test_free_path_does_not_require_tushare_token():
    matrix = yaml.safe_load(MATRIX_PATH.read_text(encoding="utf-8"))
    assert matrix["providers"]["free"]["requires_token"] is False
    for name in REQUIRED_EVENT_DATASETS:
        primary = matrix["event_datasets"][name]["primary_source"]
        assert primary["requires_token"] is False, name
