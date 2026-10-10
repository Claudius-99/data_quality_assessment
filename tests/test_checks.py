from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from tests.conftest import seed_engine

from data_quality.config import parse_config
from data_quality.runner import run_assessment


def _run(tmp_path: Path, url: str, tables: dict, extra: dict | None = None, as_of: datetime | None = None):
    raw = {
        "schema_version": 1,
        "datasource": {"name": "unit", "url": url},
        "output": {"dir": str(tmp_path / "out"), "snapshot": str(tmp_path / "snap.json")},
        "tables": tables,
    }
    if extra:
        raw.update(extra)
    config = parse_config(raw)
    return run_assessment(config, as_of=as_of or datetime(2026, 3, 15, tzinfo=timezone.utc))


def test_completeness_fail_and_pass(tmp_path: Path, engine_url: str):
    seed_engine(
        engine_url,
        [
            "CREATE TABLE customers (customer_id VARCHAR, email VARCHAR)",
            "INSERT INTO customers VALUES ('1', 'a@b.c'), ('2', NULL), ('3', '')",
        ],
    )
    result = _run(tmp_path, engine_url, {"customers": {"completeness": [{"column": "email"}]}})
    check = result.checks[0]
    assert check.status == "fail"
    assert check.observed["failed_count"] == 2
    assert "a@b.c" not in check.message


def test_uniqueness_counts_duplicate_rows(tmp_path: Path, engine_url: str):
    seed_engine(
        engine_url,
        [
            "CREATE TABLE customers (customer_id VARCHAR)",
            "INSERT INTO customers VALUES ('1'), ('1'), ('1'), ('2')",
        ],
    )
    result = _run(tmp_path, engine_url, {"customers": {"uniqueness": [{"columns": ["customer_id"]}]}})
    check = result.checks[0]
    assert check.status == "fail"
    assert check.observed["failed_count"] == 3
    assert check.observed["duplicate_groups"] == 1


def test_validity_range_and_accepted(tmp_path: Path, engine_url: str):
    seed_engine(
        engine_url,
        [
            "CREATE TABLE tx (amount DOUBLE, status VARCHAR)",
            "INSERT INTO tx VALUES (10, 'open'), (-4, 'active'), (3, 'active')",
        ],
    )
    result = _run(
        tmp_path,
        engine_url,
        {
            "tx": {
                "validity": [
                    {"column": "amount", "min": 0.0},
                    {"column": "status", "accepted_values": ["active"]},
                ]
            }
        },
    )
    by_id = {c.check_type: c for c in result.checks}
    assert by_id["range"].status == "fail"
    assert by_id["range"].observed["failed_count"] == 1
    assert by_id["accepted_values"].status == "fail"
    assert by_id["accepted_values"].observed["failed_count"] == 1


def test_referential_counts_child_rows(tmp_path: Path, engine_url: str):
    seed_engine(
        engine_url,
        [
            "CREATE TABLE accounts (account_id VARCHAR)",
            "CREATE TABLE tx (txn_id VARCHAR, account_id VARCHAR)",
            "INSERT INTO accounts VALUES ('A1')",
            "INSERT INTO tx VALUES ('T1', 'A1'), ('T2', 'A9'), ('T3', 'A9'), ('T4', NULL)",
        ],
    )
    result = _run(
        tmp_path,
        engine_url,
        {
            "accounts": {"uniqueness": [{"columns": ["account_id"]}]},
            "tx": {"uniqueness": [{"columns": ["txn_id"]}]},
        },
        extra={
            "relationships": [
                {
                    "name": "tx_account",
                    "child": "tx",
                    "child_columns": ["account_id"],
                    "parent": "accounts",
                    "parent_columns": ["account_id"],
                }
            ]
        },
    )
    ri = next(c for c in result.checks if c.dimension == "referential_integrity")
    assert ri.status == "fail"
    assert ri.observed["failed_count"] == 2
    assert ri.observed["distinct_orphans"] == 1


def test_freshness_uses_as_of(tmp_path: Path, engine_url: str):
    seed_engine(
        engine_url,
        [
            "CREATE TABLE tx (posted_at TIMESTAMP)",
            "INSERT INTO tx VALUES (TIMESTAMP '2026-03-01 00:00:00')",
        ],
    )
    fail = _run(
        tmp_path / "a",
        engine_url,
        {"tx": {"freshness": [{"column": "posted_at", "max_age_hours": 24}]}},
        as_of=datetime(2026, 3, 15, tzinfo=timezone.utc),
    )
    assert fail.checks[0].status == "fail"
    ok = _run(
        tmp_path / "b",
        engine_url,
        {"tx": {"freshness": [{"column": "posted_at", "max_age_hours": 24}]}},
        as_of=datetime(2026, 3, 1, 12, tzinfo=timezone.utc),
    )
    assert ok.checks[0].status == "pass"


def test_freshness_empty_fails(tmp_path: Path, engine_url: str):
    seed_engine(engine_url, ["CREATE TABLE tx (posted_at TIMESTAMP)"])
    result = _run(tmp_path, engine_url, {"tx": {"freshness": [{"column": "posted_at", "max_age_hours": 24}]}})
    assert result.checks[0].status == "fail"


def test_missing_table_is_runtime_error(tmp_path: Path, engine_url: str):
    seed_engine(engine_url, ["CREATE TABLE other (id INTEGER)"])
    try:
        _run(tmp_path, engine_url, {"customers": {"completeness": [{"column": "email"}]}})
        assert False, "expected SourceError"
    except Exception as exc:
        assert "not found" in str(exc).lower() or "customers" in str(exc)
