from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from data_quality.models import DIMENSIONS, STATUS_RANK, Status
from data_quality.store import load_history, load_run

SNAPSHOT_SCHEMA = 1


def build_snapshot(output_dir: Path, *, worst_columns: int = 10) -> dict[str, Any]:
    history = load_history(output_dir)
    if not history:
        return {
            "schema_version": SNAPSHOT_SCHEMA,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "specimen": True,
            "specimen_label": "Specimen, not client data",
            "current_run": None,
            "tables": [],
            "history": [],
            "worst_columns": [],
        }
    current_meta = history[-1]
    current = load_run(output_dir, current_meta["run_id"])
    tables = []
    for table in current["tables"]:
        dim_entries = {}
        related = [c for c in current["checks"] if c["table"] == table["name"]]
        for dimension in DIMENSIONS:
            subset = [c for c in related if c["dimension"] == dimension]
            dim_entries[dimension] = {
                "status": table["dimensions"].get(dimension, "not_evaluated"),
                "check_count": len(subset),
                "fail_count": sum(1 for c in subset if c["status"] == "fail"),
                "warn_count": sum(1 for c in subset if c["status"] == "warn"),
                "pass_count": sum(1 for c in subset if c["status"] == "pass"),
            }
        tables.append(
            {
                "name": table["name"],
                "row_count": table["row_count"],
                "status": table["status"],
                "dimensions": dim_entries,
                "checks": [
                    {
                        "id": check["id"],
                        "dimension": check["dimension"],
                        "check_type": check["check_type"],
                        "columns": check["columns"],
                        "status": check["status"],
                        "observed": {
                            k: check["observed"].get(k)
                            for k in ("row_count", "failed_count", "failed_ratio", "psi", "age_hours")
                            if k in check.get("observed", {})
                        },
                        "message": check["message"],
                        "skip_reason": check.get("skip_reason"),
                    }
                    for check in related
                ],
            }
        )
    history_view = []
    for entry in history:
        stored = load_run(output_dir, entry["run_id"])
        fail_count = sum(1 for c in stored["checks"] if c["status"] == "fail")
        warn_count = sum(1 for c in stored["checks"] if c["status"] == "warn")
        history_view.append(
            {
                "run_id": stored["run_id"],
                "as_of": stored["as_of"],
                "started_at": stored["started_at"],
                "overall_status": stored["overall_status"],
                "exit_code": stored["exit_code"],
                "fail_count": fail_count,
                "warn_count": warn_count,
                "tables": {t["name"]: t["status"] for t in stored["tables"]},
            }
        )
    return {
        "schema_version": SNAPSHOT_SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "specimen": bool(current.get("specimen", True)),
        "specimen_label": "Specimen, not client data",
        "current_run": {
            "run_id": current["run_id"],
            "as_of": current["as_of"],
            "started_at": current["started_at"],
            "finished_at": current["finished_at"],
            "datasource_name": current["datasource_name"],
            "datasource_url": current["datasource_url"],
            "config_fingerprint": current["config_fingerprint"],
            "overall_status": current["overall_status"],
            "exit_code": current["exit_code"],
        },
        "tables": tables,
        "history": history_view,
        "worst_columns": _worst_from_history(output_dir, history, current, worst_columns),
    }


def _worst_from_history(
    output_dir: Path,
    history: list[dict[str, Any]],
    current: dict[str, Any],
    limit: int,
) -> list[dict[str, Any]]:
    current_worst = rank_worst_columns(current["checks"], limit=limit)
    if current_worst:
        return current_worst
    for entry in reversed(history[:-1]):
        stored = load_run(output_dir, entry["run_id"])
        ranked = rank_worst_columns(stored["checks"], limit=limit)
        if ranked:
            for row in ranked:
                row["run_id"] = stored["run_id"]
            return ranked
    return []


def rank_worst_columns(checks: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for check in checks:
        if not check.get("columns"):
            continue
        if check["status"] in {"skipped", "pass"}:
            continue
        key = (check["table"], check["columns"][0])
        grouped[key].append(check)
    ranked = []
    for (table, column), items in grouped.items():
        status = max(items, key=lambda c: STATUS_RANK[Status(c["status"])])["status"]
        failed_count = sum(int(c.get("observed", {}).get("failed_count") or 0) for c in items)
        ratios = [float(c.get("observed", {}).get("failed_ratio") or 0) for c in items]
        failed_ratio = max(ratios) if ratios else 0.0
        ranked.append(
            {
                "table": table,
                "column": column,
                "status": status,
                "failed_ratio": round(failed_ratio, 6),
                "failed_count": failed_count,
                "checks": [
                    {
                        "id": c["id"],
                        "dimension": c["dimension"],
                        "status": c["status"],
                        "message": c["message"],
                    }
                    for c in items
                ],
            }
        )
    ranked.sort(
        key=lambda row: (
            -STATUS_RANK[Status(row["status"])],
            -row["failed_ratio"],
            -row["failed_count"],
            row["table"],
            row["column"],
        )
    )
    return ranked[:limit]
