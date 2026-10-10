from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from tests.conftest import seed_engine

from data_quality.config import parse_config
from data_quality.models import EXIT_DATA, EXIT_OK
from data_quality.runner import run_assessment
from data_quality.source import SourceError


def _cfg(tmp_path: Path, url: str, tables: dict):
    return parse_config(
        {
            "schema_version": 1,
            "datasource": {"name": "unit", "url": url},
            "output": {"dir": str(tmp_path / "out"), "snapshot": str(tmp_path / "snap.json")},
            "tables": tables,
        }
    )


def test_drift_skips_then_fails(tmp_path: Path, engine_url: str):
    seed_engine(
        engine_url,
        [
            "CREATE TABLE tx (amount DOUBLE)",
            "INSERT INTO tx VALUES (10), (11), (12), (10), (11), (9), (13), (10)",
        ],
    )
    tables = {"tx": {"drift": [{"column": "amount", "kind": "numeric"}]}}
    first = run_assessment(
        _cfg(tmp_path, engine_url, tables),
        as_of=datetime(2026, 1, 1, tzinfo=timezone.utc),
        run_id="r1",
    )
    drift = first.checks[0]
    assert drift.status == "skipped"
    assert drift.skip_reason == "no_baseline"
    assert first.exit_code == EXIT_OK

    seed_engine(
        engine_url,
        [
            "DROP TABLE tx",
            "CREATE TABLE tx (amount DOUBLE)",
            "INSERT INTO tx VALUES (400), (410), (420), (430), (440), (450), (460), (470)",
        ],
    )
    second = run_assessment(
        _cfg(tmp_path, engine_url, tables),
        as_of=datetime(2026, 1, 2, tzinfo=timezone.utc),
        run_id="r2",
    )
    drift = second.checks[0]
    assert drift.status == "fail"
    assert drift.observed["psi"] >= 0.25
    assert second.exit_code == EXIT_DATA


def test_warning_does_not_fail_exit(tmp_path: Path, engine_url: str):
    seed_engine(
        engine_url,
        [
            "CREATE TABLE tx (channel VARCHAR)",
            "INSERT INTO tx VALUES ('card'), ('card'), ('ach'), ('ach'), ('wire')",
        ],
    )
    tables = {
        "tx": {
            "drift": [
                {
                    "column": "channel",
                    "kind": "categorical",
                    "pass_below": 0.02,
                    "warn_below": 0.9,
                }
            ]
        }
    }
    run_assessment(_cfg(tmp_path, engine_url, tables), run_id="h1")
    seed_engine(
        engine_url,
        [
            "DROP TABLE tx",
            "CREATE TABLE tx (channel VARCHAR)",
            "INSERT INTO tx VALUES ('card'), ('card'), ('card'), ('ach'), ('wire')",
        ],
    )
    second = run_assessment(_cfg(tmp_path, engine_url, tables), run_id="h2")
    assert second.checks[0].status in {"warn", "pass"}
    if second.checks[0].status == "warn":
        assert second.exit_code == EXIT_OK


def test_missing_column_exit_error(tmp_path: Path, engine_url: str):
    seed_engine(engine_url, ["CREATE TABLE tx (amount DOUBLE)"])
    with pytest.raises(SourceError, match="columns not found"):
        run_assessment(_cfg(tmp_path, engine_url, {"tx": {"completeness": [{"column": "missing"}]}}))


def test_zero_checks_rejected():
    with pytest.raises(Exception, match="no checks"):
        parse_config(
            {
                "schema_version": 1,
                "datasource": {"url": "duckdb:///:memory:"},
                "tables": {"tx": {}},
            }
        )
