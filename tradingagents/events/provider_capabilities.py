"""Load and validate frozen free-path event provider capabilities."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

MATRIX_PATH = Path("docs/data/data-capability-matrix.yaml")

REQUIRED_EVENT_DATASETS = frozenset({
    "official_announcements",
    "event_news",
    "event_fund_flow",
    "event_hot_topics",
})

REQUIRED_PROBE_FIELDS = (
    "id",
    "platform",
    "endpoint",
    "requires_token",
    "license",
    "pagination",
    "history_range",
    "rate_limit",
    "time_precision",
    "empty_semantics",
    "error_types",
    "sample_response_sha256",
    "failover_condition",
)


def load_event_capability_matrix(path: Path | None = None) -> dict[str, Any]:
    matrix_path = path or MATRIX_PATH
    matrix = yaml.safe_load(matrix_path.read_text(encoding="utf-8"))
    if not isinstance(matrix, dict) or "event_datasets" not in matrix:
        raise ValueError("capability matrix missing event_datasets")
    return matrix


def validate_event_capability_matrix(matrix: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    datasets = matrix.get("event_datasets")
    if not isinstance(datasets, dict):
        return ["event_datasets must be a mapping"]

    missing = REQUIRED_EVENT_DATASETS - set(datasets)
    if missing:
        errors.append(f"missing event datasets: {sorted(missing)}")

    for name, definition in datasets.items():
        if not isinstance(definition, dict):
            errors.append(f"{name}: definition must be a mapping")
            continue
        primary = definition.get("primary_source")
        if not isinstance(primary, dict):
            errors.append(f"{name}: primary_source required")
            continue
        for field in REQUIRED_PROBE_FIELDS:
            if field not in primary:
                errors.append(f"{name}.primary_source missing {field}")
        if primary.get("requires_token"):
            errors.append(f"{name}.primary_source must not require token on free path")
        if primary.get("empty_semantics") == "treat_network_error_as_empty":
            errors.append(f"{name}: network errors must not masquerade as empty")

        pit_level = definition.get("pit_level")
        if pit_level not in {"pit_required", "current_only", "best_effort"}:
            errors.append(f"{name}: invalid pit_level {pit_level!r}")

        if name == "official_announcements":
            markets = set(definition.get("markets") or [])
            expected = {"sse_main", "szse_main", "chinext", "star"}
            if markets != expected:
                errors.append(f"{name}: markets must be {sorted(expected)}")
            forbidden = set(definition.get("forbidden_substitutes") or [])
            if "sina.corp.vCB_AllNewsStock" not in forbidden:
                errors.append(f"{name}: news source must be forbidden substitute")
            primary_id = primary.get("id", "")
            if primary_id in forbidden:
                errors.append(f"{name}: primary source cannot be a forbidden substitute")
            optional = definition.get("optional_enhancement") or {}
            if optional and optional.get("blocks_core_on_failure"):
                errors.append(f"{name}: optional enhancement must not block core path")

    return errors


def core_announcement_gate_status(matrix: dict[str, Any]) -> str:
    """Return PASS only when free core announcement probe is frozen as permitted."""
    errors = validate_event_capability_matrix(matrix)
    if errors:
        return "BLOCKED"
    ann = matrix["event_datasets"]["official_announcements"]
    probe = ann.get("probe_status")
    primary = ann["primary_source"]
    if probe != "PASS":
        return "BLOCKED"
    if primary.get("requires_token"):
        return "BLOCKED"
    if primary.get("time_precision") not in {
        "datetime",
        "date_only_conservative_next_open",
    }:
        return "BLOCKED"
    return "PASS"
