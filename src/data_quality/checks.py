from __future__ import annotations

import math
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Float, String, and_, case, cast, func, select
from sqlalchemy.engine import Connection
from sqlalchemy.sql import ColumnElement

from data_quality.models import CheckResult, CheckSpec
from data_quality.source import BoundTable, SourceError

PSI_EPS = 1e-4


class CheckError(RuntimeError):
    pass


def evaluate_check(
    conn: Connection,
    spec: CheckSpec,
    tables: dict[str, BoundTable],
    *,
    as_of: datetime,
    naive_timezone: str,
    dialect: str,
    baseline: dict[str, Any] | None,
) -> CheckResult:
    try:
        if spec.dimension == "completeness":
            return _completeness(conn, spec, tables)
        if spec.dimension == "uniqueness":
            return _uniqueness(conn, spec, tables)
        if spec.dimension == "validity":
            return _validity(conn, spec, tables, dialect)
        if spec.dimension == "referential_integrity":
            return _referential(conn, spec, tables)
        if spec.dimension == "freshness":
            return _freshness(conn, spec, tables, as_of, naive_timezone)
        if spec.dimension == "drift":
            return _drift(conn, spec, tables, dialect, baseline)
        raise CheckError(f"unknown dimension {spec.dimension}")
    except SourceError:
        raise
    except CheckError as exc:
        return _error(spec, str(exc))
    except Exception as exc:
        return _error(spec, f"{exc.__class__.__name__}")


def _completeness(conn: Connection, spec: CheckSpec, tables: dict[str, BoundTable]) -> CheckResult:
    table = tables[spec.table].table
    column = table.c[spec.columns[0]]
    row_count = _scalar(conn, select(func.count()).select_from(table))
    treat_blank = bool(spec.params.get("treat_blank_as_null", True))
    missing_expr = column.is_(None)
    if treat_blank:
        blank = func.trim(cast(column, String)) == ""
        missing_expr = missing_expr | blank
    missing = _scalar(
        conn,
        select(func.coalesce(func.sum(case((missing_expr, 1), else_=0)), 0)).select_from(table),
    )
    missing = int(missing)
    ratio = (missing / row_count) if row_count else 1.0
    threshold = float(spec.params["max_null_ratio"])
    status = "pass" if row_count and ratio <= threshold else "fail"
    if row_count == 0:
        message = "table is empty"
    else:
        message = f"{missing} of {row_count} rows missing {spec.columns[0]}"
    return _result(
        spec,
        status,
        observed={"row_count": row_count, "failed_count": missing, "failed_ratio": round(ratio, 6)},
        threshold={"max_null_ratio": threshold},
        message=message,
    )


def _uniqueness(conn: Connection, spec: CheckSpec, tables: dict[str, BoundTable]) -> CheckResult:
    table = tables[spec.table].table
    key_cols = [table.c[name] for name in spec.columns]
    row_count = _scalar(conn, select(func.count()).select_from(table))
    not_null = and_(*[col.is_not(None) for col in key_cols])
    grouped = (
        select(*key_cols, func.count().label("n"))
        .where(not_null)
        .group_by(*key_cols)
        .having(func.count() > 1)
        .subquery()
    )
    duplicate_groups = _scalar(conn, select(func.count()).select_from(grouped))
    duplicate_rows = _scalar(conn, select(func.coalesce(func.sum(grouped.c.n), 0)))
    duplicate_groups = int(duplicate_groups)
    duplicate_rows = int(duplicate_rows)
    ratio = (duplicate_rows / row_count) if row_count else 0.0
    status = "fail" if duplicate_rows else "pass"
    return _result(
        spec,
        status,
        observed={
            "row_count": row_count,
            "failed_count": duplicate_rows,
            "failed_ratio": round(ratio, 6),
            "duplicate_groups": duplicate_groups,
        },
        threshold={"duplicate_rows": 0},
        message=f"{duplicate_rows} rows in {duplicate_groups} duplicate key groups",
    )


def _validity(conn: Connection, spec: CheckSpec, tables: dict[str, BoundTable], dialect: str) -> CheckResult:
    table = tables[spec.table].table
    column = table.c[spec.columns[0]]
    populated = column.is_not(None)
    populated_count = _scalar(conn, select(func.count()).select_from(table).where(populated))
    if spec.check_type == "accepted_values":
        allowed = [str(v) for v in spec.params["accepted_values"]]
        invalid_expr = populated & ~cast(column, String).in_(allowed)
        threshold: dict[str, Any] = {"accepted_values": allowed}
    elif spec.check_type == "pattern":
        pattern = spec.params["pattern"]
        matched = _regex_match(cast(column, String), pattern, dialect)
        invalid_expr = populated & ~matched
        threshold = {"pattern": pattern}
    elif spec.check_type == "range":
        numeric = _try_numeric(column, dialect)
        lower = spec.params.get("min")
        upper = spec.params.get("max")
        out_of_range = numeric.is_(None)
        if lower is not None:
            out_of_range = out_of_range | (numeric < float(lower))
        if upper is not None:
            out_of_range = out_of_range | (numeric > float(upper))
        invalid_expr = populated & out_of_range
        threshold = {"min": lower, "max": upper}
    else:
        raise CheckError(f"unknown validity type {spec.check_type}")
    failed = int(_scalar(conn, select(func.count()).select_from(table).where(invalid_expr)))
    ratio = (failed / populated_count) if populated_count else 0.0
    status = "fail" if failed else "pass"
    return _result(
        spec,
        status,
        observed={
            "row_count": populated_count,
            "failed_count": failed,
            "failed_ratio": round(ratio, 6),
        },
        threshold=threshold,
        message=f"{failed} of {populated_count} non-null values failed {spec.check_type}",
    )


def _referential(conn: Connection, spec: CheckSpec, tables: dict[str, BoundTable]) -> CheckResult:
    child = tables[spec.table].table
    parent = tables[str(spec.params["parent"])].table
    child_cols = [child.c[name] for name in spec.columns]
    parent_cols = [parent.c[name] for name in spec.params["parent_columns"]]
    join_on = and_(*[left == right for left, right in zip(child_cols, parent_cols)])
    child_present = and_(*[col.is_not(None) for col in child_cols])
    parent_missing = and_(*[col.is_(None) for col in parent_cols])
    joined = child.outerjoin(parent, join_on)
    failed = int(
        _scalar(
            conn,
            select(func.count()).select_from(joined).where(child_present, parent_missing),
        )
    )
    child_keys = int(_scalar(conn, select(func.count()).select_from(child).where(child_present)))
    orphan_keys = (
        select(*child_cols).select_from(joined).where(child_present, parent_missing).distinct().subquery()
    )
    distinct_orphans = int(_scalar(conn, select(func.count()).select_from(orphan_keys)))
    ratio = (failed / child_keys) if child_keys else 0.0
    status = "fail" if failed else "pass"
    return _result(
        spec,
        status,
        observed={
            "row_count": child_keys,
            "failed_count": failed,
            "failed_ratio": round(ratio, 6),
            "distinct_orphans": distinct_orphans,
        },
        threshold={"orphan_rows": 0},
        message=f"{failed} child rows missing parent {spec.params['parent']}",
    )


def _freshness(
    conn: Connection,
    spec: CheckSpec,
    tables: dict[str, BoundTable],
    as_of: datetime,
    default_tz: str,
) -> CheckResult:
    table = tables[spec.table].table
    column = table.c[spec.columns[0]]
    row_count = _scalar(conn, select(func.count()).select_from(table))
    non_null = _scalar(conn, select(func.count()).select_from(table).where(column.is_not(None)))
    max_raw = conn.execute(select(func.max(column)).where(column.is_not(None))).scalar()
    max_age = float(spec.params["max_age_hours"])
    tz_name = spec.params.get("naive_timezone") or default_tz
    as_of_utc = _as_utc(as_of, tz_name)
    threshold = {"max_age_hours": max_age, "as_of": as_of_utc.isoformat()}
    if row_count == 0 or non_null == 0 or max_raw is None:
        return _result(
            spec,
            "fail",
            observed={"row_count": row_count, "failed_count": 1, "failed_ratio": 1.0, "max_timestamp": None},
            threshold=threshold,
            message="no timestamp values to assess freshness",
        )
    max_ts = _as_utc(max_raw, tz_name)
    age_hours = (as_of_utc - max_ts).total_seconds() / 3600.0
    status = "pass" if age_hours <= max_age else "fail"
    failed = 0 if status == "pass" else 1
    return _result(
        spec,
        status,
        observed={
            "row_count": int(non_null),
            "failed_count": failed,
            "failed_ratio": float(failed),
            "max_timestamp": max_ts.isoformat(),
            "age_hours": round(age_hours, 4),
        },
        threshold=threshold,
        message=f"newest {spec.columns[0]} is {age_hours:.1f}h before as_of (limit {max_age:g}h)",
    )


def _drift(
    conn: Connection,
    spec: CheckSpec,
    tables: dict[str, BoundTable],
    dialect: str,
    baseline: dict[str, Any] | None,
) -> CheckResult:
    table = tables[spec.table].table
    column = table.c[spec.columns[0]]
    kind = spec.params["kind"]
    pass_below = float(spec.params["pass_below"])
    warn_below = float(spec.params["warn_below"])
    threshold = {"pass_below": pass_below, "warn_below": warn_below, "kind": kind}
    if kind == "numeric":
        current = _numeric_distribution(
            conn,
            column,
            dialect,
            bins=int(spec.params["bins"]),
            edges=None if not baseline else baseline.get("edges"),
        )
    else:
        current = _categorical_distribution(
            conn,
            column,
            max_categories=int(spec.params["max_categories"]),
            labels=None if not baseline else baseline.get("labels"),
        )
    if current is None:
        return _result(
            spec,
            "skipped",
            observed={"row_count": 0, "failed_count": 0, "failed_ratio": 0.0},
            threshold=threshold,
            message="no values available for drift",
            skip_reason="empty_column",
        )
    if baseline is None:
        return _result(
            spec,
            "skipped",
            observed={"row_count": current["n"], "failed_count": 0, "failed_ratio": 0.0},
            threshold=threshold,
            message="no comparable baseline; stored this run as reference",
            skip_reason="no_baseline",
            distribution=current,
        )
    try:
        psi = _psi(baseline["shares"], current["shares"])
    except Exception:
        return _result(
            spec,
            "skipped",
            observed={"row_count": current["n"], "failed_count": 0, "failed_ratio": 0.0},
            threshold=threshold,
            message="baseline shape does not match this check",
            skip_reason="incompatible_baseline",
            distribution=current,
        )
    if psi < pass_below:
        status = "pass"
    elif psi < warn_below:
        status = "warn"
    else:
        status = "fail"
    failed = 1 if status == "fail" else 0
    return _result(
        spec,
        status,
        observed={
            "row_count": current["n"],
            "failed_count": failed,
            "failed_ratio": float(failed),
            "psi": round(psi, 6),
            "baseline_run_id": baseline.get("run_id"),
        },
        threshold=threshold,
        message=f"PSI {psi:.3f} vs previous comparable run",
        distribution=current,
    )


def _numeric_distribution(
    conn: Connection,
    column: ColumnElement,
    dialect: str,
    bins: int,
    edges: list[float] | None,
) -> dict[str, Any] | None:
    numeric = _try_numeric(column, dialect)
    populated = numeric.is_not(None)
    n = int(_scalar(conn, select(func.count()).where(populated)))
    if n == 0:
        return None
    if edges is None:
        edges = _quantile_edges(conn, numeric, populated, bins)
        if edges is None:
            return None
    counts = []
    for index in range(len(edges) + 1):
        if index == 0:
            condition = populated & (numeric <= edges[0])
        elif index == len(edges):
            condition = populated & (numeric > edges[-1])
        else:
            condition = populated & (numeric > edges[index - 1]) & (numeric <= edges[index])
        counts.append(int(_scalar(conn, select(func.count()).where(condition))))
    return {
        "kind": "numeric",
        "edges": [float(v) for v in edges],
        "counts": counts,
        "shares": _shares(counts),
        "n": n,
    }


def _quantile_edges(
    conn: Connection, numeric: ColumnElement, populated: ColumnElement, bins: int
) -> list[float] | None:
    probs = [i / bins for i in range(1, bins)]
    edges: list[float] = []
    for prob in probs:
        try:
            value = conn.execute(select(func.quantile_cont(numeric, prob)).where(populated)).scalar()
        except Exception:
            value = None
        if value is None:
            return _equal_width_edges(conn, numeric, populated, bins)
        edges.append(float(value))
    unique: list[float] = []
    for edge in edges:
        if not unique or edge > unique[-1]:
            unique.append(edge)
    if len(unique) < 2:
        return _equal_width_edges(conn, numeric, populated, bins)
    return unique


def _equal_width_edges(
    conn: Connection, numeric: ColumnElement, populated: ColumnElement, bins: int
) -> list[float] | None:
    lo = conn.execute(select(func.min(numeric)).where(populated)).scalar()
    hi = conn.execute(select(func.max(numeric)).where(populated)).scalar()
    if lo is None or hi is None or float(lo) == float(hi):
        return None
    lo_f, hi_f = float(lo), float(hi)
    step = (hi_f - lo_f) / bins
    return [lo_f + step * i for i in range(1, bins)]


def _categorical_distribution(
    conn: Connection,
    column: ColumnElement,
    max_categories: int,
    labels: list[str] | None,
) -> dict[str, Any] | None:
    populated = column.is_not(None)
    n = int(_scalar(conn, select(func.count()).where(populated)))
    if n == 0:
        return None
    rows = conn.execute(
        select(cast(column, String), func.count())
        .where(populated)
        .group_by(cast(column, String))
        .order_by(func.count().desc())
    ).fetchall()
    tallies = {str(label): int(count) for label, count in rows}
    if labels is None:
        ranked = sorted(tallies.items(), key=lambda item: (-item[1], item[0]))
        labels = [label for label, _ in ranked[:max_categories]]
        if len(ranked) > max_categories and "OTHER" not in labels:
            labels = labels  # OTHER is implicit
    counts = [tallies.get(label, 0) for label in labels]
    other = n - sum(counts)
    counts.append(other)
    return {
        "kind": "categorical",
        "labels": labels,
        "counts": counts,
        "shares": _shares(counts),
        "n": n,
    }


def _shares(counts: list[int]) -> list[float]:
    total = sum(counts) or 1
    return [count / total for count in counts]


def _psi(expected: list[float], actual: list[float]) -> float:
    if len(expected) != len(actual):
        raise ValueError("length mismatch")
    total = 0.0
    for exp, act in zip(expected, actual):
        exp = max(exp, PSI_EPS)
        act = max(act, PSI_EPS)
        total += (act - exp) * math.log(act / exp)
    return total


def _regex_match(column: ColumnElement, pattern: str, dialect: str) -> ColumnElement:
    if dialect in {"duckdb", "postgresql", "postgres"}:
        if dialect == "duckdb":
            return func.regexp_matches(column, pattern)
        return column.op("~")(pattern)
    raise CheckError(f"regex validity is not supported on dialect {dialect}")


def _try_numeric(column: ColumnElement, dialect: str) -> ColumnElement:
    del dialect
    return cast(column, Float)


def _as_utc(value: Any, naive_timezone: str) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=ZoneInfo(naive_timezone)).astimezone(timezone.utc)
        return value.astimezone(timezone.utc)
    if hasattr(value, "year") and hasattr(value, "month") and hasattr(value, "day"):
        return datetime(value.year, value.month, value.day, tzinfo=ZoneInfo(naive_timezone)).astimezone(
            timezone.utc
        )
    text = str(value)
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(naive_timezone))
    return parsed.astimezone(timezone.utc)


def _scalar(conn: Connection, stmt) -> int:
    value = conn.execute(stmt).scalar()
    if value is None:
        return 0
    if isinstance(value, Decimal):
        return int(value)
    return int(value)


def _result(
    spec: CheckSpec,
    status: str,
    *,
    observed: dict[str, Any],
    threshold: dict[str, Any],
    message: str,
    skip_reason: str | None = None,
    distribution: dict[str, Any] | None = None,
) -> CheckResult:
    return CheckResult(
        id=spec.id,
        table=spec.table,
        dimension=spec.dimension,
        check_type=spec.check_type,
        columns=list(spec.columns),
        status=status,
        observed=observed,
        threshold=threshold,
        message=message,
        skip_reason=skip_reason,
        distribution=distribution,
    )


def _error(spec: CheckSpec, message: str) -> CheckResult:
    return CheckResult(
        id=spec.id,
        table=spec.table,
        dimension=spec.dimension,
        check_type=spec.check_type,
        columns=list(spec.columns),
        status="error",
        observed={},
        threshold={},
        message=message,
    )
