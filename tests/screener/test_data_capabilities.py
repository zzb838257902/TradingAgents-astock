from pathlib import Path

import yaml


def test_every_dataset_declares_pit_capability():
    path = Path("docs/data/data-capability-matrix.yaml")
    matrix = yaml.safe_load(path.read_text(encoding="utf-8"))
    required = {"daily_bars", "adjustment_factors", "security_master", "financials"}
    assert required <= set(matrix["datasets"])
    for name, definition in matrix["datasets"].items():
        assert definition["pit_level"] in {
            "pit_required", "current_only", "best_effort"
        }, name
        assert definition["history_start"], name
        assert definition["source"], name
