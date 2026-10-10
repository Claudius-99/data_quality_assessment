from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from data_quality.models import CheckSpec

ENV_PATTERN = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}")
SUPPORTED_SCHEMA = 1
DRIFT_KINDS = {"numeric", "categorical"}
VALIDITY_KEYS = {"column", "columns", "accepted_values", "min", "max", "pattern", "id"}


class ConfigError(ValueError):
    """Invalid YAML, missing env, or broken cross-references."""


@dataclass
class OutputConfig:
    dir: Path
    snapshot: Path
    history_limit: int = 50
    worst_columns: int = 10


@dataclass
class TableConfig:
    name: str
    schema_name: str | None = None


@dataclass
class AssessmentConfig:
    schema_version: int
    datasource_name: str
    url: str
    tables: dict[str, TableConfig]
    checks: list[CheckSpec]
    output: OutputConfig
    specimen: bool = False
    naive_timezone: str = "UTC"
    raw_for_fingerprint: dict[str, Any] = field(default_factory=dict)


def load_config(path: str | Path) -> AssessmentConfig:
    config_path = Path(path)
    if not config_path.is_file():
        raise ConfigError(f"config not found: {config_path}")
    try:
        raw = yaml.safe_load(config_path.read_text())
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("config root must be a mapping")
    raw = _resolve_env(raw)
    return parse_config(raw, base_dir=config_path.parent)


def parse_config(raw: dict[str, Any], base_dir: Path | None = None) -> AssessmentConfig:
    raw = _resolve_env(raw)
    unknown = set(raw) - {
        "schema_version",
        "datasource",
        "output",
        "tables",
        "relationships",
        "specimen",
        "naive_timezone",
    }
    if unknown:
        raise ConfigError(f"unknown top-level keys: {sorted(unknown)}")

    version = raw.get("schema_version")
    if version != SUPPORTED_SCHEMA:
        raise ConfigError(f"unsupported schema_version {version!r}; expected {SUPPORTED_SCHEMA}")

    datasource = raw.get("datasource") or {}
    if not isinstance(datasource, dict):
        raise ConfigError("datasource must be a mapping")
    ds_unknown = set(datasource) - {"name", "url"}
    if ds_unknown:
        raise ConfigError(f"unknown datasource keys: {sorted(ds_unknown)}")
    datasource_name = str(datasource.get("name") or "default")
    url = datasource.get("url") or "duckdb:///./warehouse.duckdb"
    if not isinstance(url, str) or not url.strip():
        raise ConfigError("datasource.url must be a non-empty string")

    output_raw = raw.get("output") or {}
    if not isinstance(output_raw, dict):
        raise ConfigError("output must be a mapping")
    out_unknown = set(output_raw) - {"dir", "snapshot", "history_limit", "worst_columns"}
    if out_unknown:
        raise ConfigError(f"unknown output keys: {sorted(out_unknown)}")
    out_dir = Path(output_raw.get("dir") or ".dq")
    snapshot = Path(output_raw.get("snapshot") or "dashboard/data/snapshot.json")
    if not out_dir.is_absolute():
        out_dir = (Path.cwd() / out_dir).resolve()
    if not snapshot.is_absolute():
        snapshot = (Path.cwd() / snapshot).resolve()
    history_limit = int(output_raw.get("history_limit") or 50)
    worst_columns = int(output_raw.get("worst_columns") or 10)
    if history_limit < 1 or worst_columns < 1:
        raise ConfigError("history_limit and worst_columns must be >= 1")

    tables_raw = raw.get("tables")
    if not isinstance(tables_raw, dict) or not tables_raw:
        raise ConfigError("tables must be a non-empty mapping of logical name -> checks")

    tables: dict[str, TableConfig] = {}
    checks: list[CheckSpec] = []
    seen_ids: set[str] = set()

    for table_name, body in tables_raw.items():
        _require_ident(table_name, "table name")
        if not isinstance(body, dict):
            raise ConfigError(f"tables.{table_name} must be a mapping")
        body_unknown = set(body) - {
            "schema",
            "completeness",
            "uniqueness",
            "validity",
            "freshness",
            "drift",
        }
        if body_unknown:
            raise ConfigError(f"tables.{table_name} unknown keys: {sorted(body_unknown)}")
        schema_name = body.get("schema")
        if schema_name is not None:
            _require_ident(str(schema_name), "schema")
        tables[table_name] = TableConfig(name=table_name, schema_name=schema_name)
        checks.extend(_table_checks(table_name, body, seen_ids))

    for rel in raw.get("relationships") or []:
        if not isinstance(rel, dict):
            raise ConfigError("each relationship must be a mapping")
        rel_unknown = set(rel) - {
            "name",
            "child",
            "child_columns",
            "parent",
            "parent_columns",
        }
        if rel_unknown:
            raise ConfigError(f"unknown relationship keys: {sorted(rel_unknown)}")
        name = rel.get("name")
        child = rel.get("child")
        parent = rel.get("parent")
        child_columns = _columns(rel.get("child_columns"))
        parent_columns = _columns(rel.get("parent_columns"))
        if not name or not isinstance(name, str):
            raise ConfigError("relationship.name is required")
        if child not in tables:
            raise ConfigError(f"relationship {name}: unknown child table {child!r}")
        if parent not in tables:
            raise ConfigError(f"relationship {name}: unknown parent table {parent!r}")
        if not child_columns or not parent_columns:
            raise ConfigError(f"relationship {name}: child_columns and parent_columns required")
        if len(child_columns) != len(parent_columns):
            raise ConfigError(f"relationship {name}: child/parent column counts differ")
        spec_id = f"referential_integrity.{name}"
        _register_id(spec_id, seen_ids)
        checks.append(
            CheckSpec(
                id=spec_id,
                table=str(child),
                dimension="referential_integrity",
                check_type="foreign_key",
                columns=child_columns,
                params={
                    "parent": parent,
                    "parent_columns": parent_columns,
                    "name": name,
                },
            )
        )

    if not checks:
        raise ConfigError("no checks configured")

    fingerprint_raw = {
        "schema_version": version,
        "datasource_name": datasource_name,
        "tables": raw.get("tables"),
        "relationships": raw.get("relationships") or [],
        "naive_timezone": raw.get("naive_timezone") or "UTC",
    }

    return AssessmentConfig(
        schema_version=int(version),
        datasource_name=datasource_name,
        url=url,
        tables=tables,
        checks=checks,
        output=OutputConfig(
            dir=out_dir,
            snapshot=snapshot,
            history_limit=history_limit,
            worst_columns=worst_columns,
        ),
        specimen=bool(raw.get("specimen", False)),
        naive_timezone=str(raw.get("naive_timezone") or "UTC"),
        raw_for_fingerprint=fingerprint_raw,
    )


def _table_checks(table: str, body: dict[str, Any], seen_ids: set[str]) -> list[CheckSpec]:
    specs: list[CheckSpec] = []
    for item in body.get("completeness") or []:
        columns = _item_columns(item, f"tables.{table}.completeness")
        max_null_ratio = float(item.get("max_null_ratio", 0.0))
        if not 0 <= max_null_ratio <= 1:
            raise ConfigError(f"{table} completeness max_null_ratio must be in [0, 1]")
        treat_blank = bool(item.get("treat_blank_as_null", True))
        for column in columns:
            spec_id = item.get("id") or f"completeness.{table}.{column}"
            _register_id(spec_id, seen_ids)
            specs.append(
                CheckSpec(
                    id=spec_id,
                    table=table,
                    dimension="completeness",
                    check_type="null_ratio",
                    columns=[column],
                    params={
                        "max_null_ratio": max_null_ratio,
                        "treat_blank_as_null": treat_blank,
                    },
                )
            )

    for item in body.get("uniqueness") or []:
        columns = _item_columns(item, f"tables.{table}.uniqueness", allow_multi=True)
        spec_id = item.get("id") or f"uniqueness.{table}.{'.'.join(columns)}"
        _register_id(spec_id, seen_ids)
        specs.append(
            CheckSpec(
                id=spec_id,
                table=table,
                dimension="uniqueness",
                check_type="unique_key",
                columns=columns,
                params={},
            )
        )

    for item in body.get("validity") or []:
        if not isinstance(item, dict):
            raise ConfigError(f"tables.{table}.validity items must be mappings")
        extra = set(item) - VALIDITY_KEYS
        if extra:
            raise ConfigError(f"tables.{table}.validity unknown keys: {sorted(extra)}")
        columns = _item_columns(item, f"tables.{table}.validity")
        accepted = item.get("accepted_values")
        minimum = item.get("min")
        maximum = item.get("max")
        pattern = item.get("pattern")
        kinds = [accepted is not None, minimum is not None or maximum is not None, pattern is not None]
        if sum(1 for flag in kinds if flag) != 1:
            raise ConfigError(
                f"{table} validity on {columns} needs exactly one of accepted_values, min/max, or pattern"
            )
        if accepted is not None:
            if not isinstance(accepted, list) or not accepted:
                raise ConfigError(f"{table} accepted_values must be a non-empty list")
            check_type = "accepted_values"
            params: dict[str, Any] = {"accepted_values": [str(v) for v in accepted]}
        elif pattern is not None:
            if not isinstance(pattern, str) or not pattern:
                raise ConfigError(f"{table} pattern must be a non-empty string")
            check_type = "pattern"
            params = {"pattern": pattern}
        else:
            if minimum is not None:
                minimum = float(minimum)
            if maximum is not None:
                maximum = float(maximum)
            if minimum is not None and maximum is not None and minimum > maximum:
                raise ConfigError(f"{table} min > max for {columns}")
            check_type = "range"
            params = {"min": minimum, "max": maximum}
        for column in columns:
            spec_id = item.get("id") or f"validity.{table}.{column}.{check_type}"
            _register_id(spec_id, seen_ids)
            specs.append(
                CheckSpec(
                    id=spec_id,
                    table=table,
                    dimension="validity",
                    check_type=check_type,
                    columns=[column],
                    params=params,
                )
            )

    for item in body.get("freshness") or []:
        column = item.get("column")
        if not column:
            raise ConfigError(f"{table} freshness requires column")
        max_age = float(item.get("max_age_hours"))
        if max_age <= 0:
            raise ConfigError(f"{table} max_age_hours must be > 0")
        spec_id = item.get("id") or f"freshness.{table}.{column}"
        _register_id(spec_id, seen_ids)
        specs.append(
            CheckSpec(
                id=spec_id,
                table=table,
                dimension="freshness",
                check_type="max_age",
                columns=[str(column)],
                params={
                    "max_age_hours": max_age,
                    "naive_timezone": item.get("naive_timezone"),
                },
            )
        )

    for item in body.get("drift") or []:
        column = item.get("column")
        kind = item.get("kind")
        if not column or kind not in DRIFT_KINDS:
            raise ConfigError(f"{table} drift requires column and kind in {sorted(DRIFT_KINDS)}")
        pass_below = float(item.get("pass_below", 0.10))
        warn_below = float(item.get("warn_below", 0.25))
        if not 0 <= pass_below <= warn_below:
            raise ConfigError(f"{table} drift thresholds must satisfy 0 <= pass_below <= warn_below")
        spec_id = item.get("id") or f"drift.{table}.{column}"
        _register_id(spec_id, seen_ids)
        specs.append(
            CheckSpec(
                id=spec_id,
                table=table,
                dimension="drift",
                check_type="psi",
                columns=[str(column)],
                params={
                    "kind": kind,
                    "pass_below": pass_below,
                    "warn_below": warn_below,
                    "bins": int(item.get("bins", 10)),
                    "max_categories": int(item.get("max_categories", 20)),
                },
            )
        )
    return specs


def _item_columns(item: Any, where: str, allow_multi: bool = True) -> list[str]:
    if not isinstance(item, dict):
        raise ConfigError(f"{where} items must be mappings")
    if "column" in item and "columns" in item:
        raise ConfigError(f"{where}: use column or columns, not both")
    if "column" in item:
        columns = [str(item["column"])]
    elif "columns" in item:
        columns = _columns(item["columns"])
    else:
        raise ConfigError(f"{where}: column or columns required")
    if not columns:
        raise ConfigError(f"{where}: at least one column required")
    if not allow_multi and len(columns) != 1:
        raise ConfigError(f"{where}: only a single column is allowed")
    for column in columns:
        _require_ident(column, "column")
    return columns


def _columns(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(v, str) and v for v in value):
        return list(value)
    raise ConfigError("columns must be a string or list of strings")


def _require_ident(value: str, label: str) -> None:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ConfigError(f"invalid {label} {value!r}")


def _register_id(spec_id: str, seen_ids: set[str]) -> None:
    if spec_id in seen_ids:
        raise ConfigError(f"duplicate check id {spec_id!r}")
    seen_ids.add(spec_id)


def _resolve_env(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _resolve_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env(v) for v in value]
    if isinstance(value, str):

        def repl(match: re.Match[str]) -> str:
            name = match.group(1)
            if name not in os.environ:
                raise ConfigError(f"missing environment variable {name}")
            return os.environ[name]

        return ENV_PATTERN.sub(repl, value)
    return value
