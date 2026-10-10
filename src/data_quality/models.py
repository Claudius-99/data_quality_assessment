from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class Status(StrEnum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    SKIPPED = "skipped"
    ERROR = "error"


class Dimension(StrEnum):
    COMPLETENESS = "completeness"
    UNIQUENESS = "uniqueness"
    VALIDITY = "validity"
    REFERENTIAL_INTEGRITY = "referential_integrity"
    FRESHNESS = "freshness"
    DRIFT = "drift"


STATUS_RANK = {
    Status.SKIPPED: 0,
    Status.PASS: 1,
    Status.WARN: 2,
    Status.FAIL: 3,
    Status.ERROR: 4,
}

DIMENSIONS = tuple(item.value for item in Dimension)

EXIT_OK = 0
EXIT_DATA = 1
EXIT_ERROR = 2
EXIT_INTERRUPT = 130


def worst_status(statuses: list[str]) -> str:
    if not statuses:
        return "not_evaluated"
    ranked = [Status(s) if not isinstance(s, Status) else s for s in statuses]
    return max(ranked, key=lambda s: STATUS_RANK[s]).value


def display_status(statuses: list[str]) -> str:
    meaningful = [s for s in statuses if s != Status.SKIPPED.value]
    if not meaningful:
        return "not_evaluated" if statuses else "not_evaluated"
    return worst_status(meaningful)


@dataclass
class CheckSpec:
    id: str
    table: str
    dimension: str
    check_type: str
    columns: list[str]
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class CheckResult:
    id: str
    table: str
    dimension: str
    check_type: str
    columns: list[str]
    status: str
    observed: dict[str, Any]
    threshold: dict[str, Any]
    message: str
    skip_reason: str | None = None
    distribution: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if payload["skip_reason"] is None:
            del payload["skip_reason"]
        if payload["distribution"] is None:
            del payload["distribution"]
        return payload


@dataclass
class TableSummary:
    name: str
    schema_name: str | None
    row_count: int
    status: str
    dimensions: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RunResult:
    schema_version: int
    run_id: str
    started_at: str
    finished_at: str
    as_of: str
    datasource_name: str
    datasource_url: str
    config_fingerprint: str
    overall_status: str
    exit_code: int
    specimen: bool
    tables: list[TableSummary]
    checks: list[CheckResult]
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "as_of": self.as_of,
            "datasource_name": self.datasource_name,
            "datasource_url": self.datasource_url,
            "config_fingerprint": self.config_fingerprint,
            "overall_status": self.overall_status,
            "exit_code": self.exit_code,
            "specimen": self.specimen,
            "tables": [table.to_dict() for table in self.tables],
            "checks": [check.to_dict() for check in self.checks],
            "notes": self.notes,
        }
