from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select

from data_quality.checks import evaluate_check
from data_quality.config import AssessmentConfig
from data_quality.models import (
    EXIT_DATA,
    EXIT_ERROR,
    EXIT_OK,
    CheckResult,
    RunResult,
    TableSummary,
    display_status,
    worst_status,
)
from data_quality.reports import render_html, render_markdown
from data_quality.snapshot import build_snapshot
from data_quality.source import BoundTable, create_source, dialect_name, redact_url, reflect_tables
from data_quality.store import (
    atomic_write_json,
    atomic_write_text,
    fingerprint,
    latest_baseline,
    persist_run,
)


def parse_as_of(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def run_assessment(
    config: AssessmentConfig,
    *,
    as_of: datetime | None = None,
    run_id: str | None = None,
) -> RunResult:
    started = datetime.now(timezone.utc)
    clock = as_of or started
    run_id = run_id or _new_run_id(started)
    engine = create_source(config.url)
    try:
        tables = reflect_tables(engine, config)
        dialect = dialect_name(engine)
        config_fp = fingerprint(config.raw_for_fingerprint)
        checks: list[CheckResult] = []
        with engine.connect() as conn:
            for spec in config.checks:
                baseline = None
                if spec.dimension == "drift":
                    baseline = latest_baseline(
                        config.output.dir,
                        datasource_name=config.datasource_name,
                        config_fingerprint=config_fp,
                        check_id=spec.id,
                    )
                checks.append(
                    evaluate_check(
                        conn,
                        spec,
                        tables,
                        as_of=clock,
                        naive_timezone=config.naive_timezone,
                        dialect=dialect,
                        baseline=baseline,
                    )
                )
            table_summaries = [_table_summary(conn, name, bound, checks) for name, bound in tables.items()]
        statuses = [check.status for check in checks]
        overall = display_status(statuses)
        raw_worst = worst_status(statuses)
        evaluated = [status for status in statuses if status != "skipped"]
        baseline_only = checks and all(
            check.status == "skipped" and check.skip_reason == "no_baseline" for check in checks
        )
        if raw_worst == "error":
            exit_code = EXIT_ERROR
            overall = "error"
        elif not checks or (not evaluated and not baseline_only):
            exit_code = EXIT_ERROR
            overall = "not_evaluated"
        elif raw_worst == "fail":
            exit_code = EXIT_DATA
        else:
            exit_code = EXIT_OK
            if baseline_only:
                overall = "pass"
        finished = datetime.now(timezone.utc)
        result = RunResult(
            schema_version=config.schema_version,
            run_id=run_id,
            started_at=started.isoformat(),
            finished_at=finished.isoformat(),
            as_of=clock.isoformat(),
            datasource_name=config.datasource_name,
            datasource_url=redact_url(config.url),
            config_fingerprint=config_fp,
            overall_status=overall if overall != "not_evaluated" else overall,
            exit_code=exit_code,
            specimen=config.specimen,
            tables=table_summaries,
            checks=checks,
        )
        persist_run(result, config.output.dir, config.output.history_limit)
        reports_dir = config.output.dir / "reports" / run_id
        atomic_write_text(reports_dir / "report.md", render_markdown(result))
        atomic_write_text(reports_dir / "report.html", render_html(result))
        snapshot = build_snapshot(config.output.dir, worst_columns=config.output.worst_columns)
        atomic_write_json(config.output.snapshot, snapshot)
        return result
    finally:
        engine.dispose()


def _table_summary(conn, name: str, bound: BoundTable, checks: list[CheckResult]) -> TableSummary:
    row_count = conn.execute(select(func.count()).select_from(bound.table)).scalar() or 0
    related = [check for check in checks if check.table == name]
    dim_status = {}
    for dimension in (
        "completeness",
        "uniqueness",
        "validity",
        "referential_integrity",
        "freshness",
        "drift",
    ):
        subset = [check.status for check in related if check.dimension == dimension]
        dim_status[dimension] = display_status(subset) if subset else "not_evaluated"
    return TableSummary(
        name=name,
        schema_name=bound.schema_name,
        row_count=int(row_count),
        status=display_status([check.status for check in related]),
        dimensions=dim_status,
    )


def _new_run_id(started: datetime) -> str:
    stamp = started.strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{uuid.uuid4().hex[:8]}"
