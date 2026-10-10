from __future__ import annotations

import json
from pathlib import Path

from data_quality.demo import run_demo
from data_quality.models import EXIT_DATA, EXIT_OK
from data_quality.snapshot import rank_worst_columns
from data_quality.store import load_run


def test_demo_three_runs(tmp_path: Path):
    snapshot = tmp_path / "dashboard" / "snapshot.json"
    exits = run_demo(output_dir=tmp_path / "dq", snapshot_path=snapshot, force=True)
    assert exits[0] == EXIT_OK
    assert exits[1] == EXIT_DATA
    assert exits[2] == EXIT_OK
    payload = json.loads(snapshot.read_text())
    assert payload["schema_version"] == 1
    assert payload["specimen"] is True
    assert payload["specimen_label"] == "Specimen, not client data"
    assert len(payload["history"]) == 3
    assert payload["history"][0]["overall_status"] in {"pass", "warn"}
    assert payload["history"][1]["overall_status"] == "fail"
    assert payload["history"][2]["overall_status"] in {"pass", "warn"}
    dims = {c["dimension"] for table in payload["tables"] for c in table["checks"]}
    assert dims >= {
        "completeness",
        "uniqueness",
        "validity",
        "referential_integrity",
        "freshness",
        "drift",
    }
    degraded = load_run(tmp_path / "dq", "demo-02-degraded")
    for dimension in (
        "completeness",
        "uniqueness",
        "validity",
        "referential_integrity",
        "freshness",
        "drift",
    ):
        assert any(c["dimension"] == dimension and c["status"] == "fail" for c in degraded["checks"]), (
            dimension
        )
    assert rank_worst_columns(degraded["checks"], limit=10)
    assert payload["worst_columns"]
    text = snapshot.read_text() + (tmp_path / "dq" / "reports" / "demo-02-degraded" / "report.md").read_text()
    assert "person.001@example.com" not in text
    assert "TORPH" not in text
