from __future__ import annotations

import pytest

from data_quality.config import ConfigError, parse_config


def _base(**overrides):
    raw = {
        "schema_version": 1,
        "datasource": {"name": "t", "url": "duckdb:///:memory:"},
        "tables": {
            "customers": {
                "completeness": [{"column": "email"}],
                "uniqueness": [{"columns": ["customer_id"]}],
            }
        },
    }
    raw.update(overrides)
    return raw


def test_rejects_bad_version():
    with pytest.raises(ConfigError, match="schema_version"):
        parse_config(_base(schema_version=2))


def test_rejects_unknown_keys():
    raw = _base()
    raw["tables"]["customers"]["accuracy"] = [{"column": "x"}]
    with pytest.raises(ConfigError, match="unknown"):
        parse_config(raw)


def test_rejects_duplicate_ids():
    raw = _base()
    raw["tables"]["customers"]["completeness"] = [
        {"column": "email", "id": "same"},
        {"column": "status", "id": "same"},
    ]
    with pytest.raises(ConfigError, match="duplicate"):
        parse_config(raw)


def test_relationship_must_reference_known_tables():
    raw = _base()
    raw["relationships"] = [
        {
            "name": "bad",
            "child": "transactions",
            "child_columns": ["account_id"],
            "parent": "accounts",
            "parent_columns": ["account_id"],
        }
    ]
    with pytest.raises(ConfigError, match="unknown child"):
        parse_config(raw)


def test_env_substitution(monkeypatch):
    monkeypatch.setenv("DQ_DATABASE_URL", "duckdb:///./x.duckdb")
    raw = _base()
    raw["datasource"]["url"] = "${DQ_DATABASE_URL}"
    config = parse_config(raw)
    assert config.url.endswith("x.duckdb")


def test_missing_env():
    raw = _base()
    raw["datasource"]["url"] = "${DOES_NOT_EXIST_DQ}"
    with pytest.raises(ConfigError, match="DOES_NOT_EXIST_DQ"):
        parse_config(raw)
