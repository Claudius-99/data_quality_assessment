from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from data_quality.models import RunResult

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None


def fingerprint(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()[:16]


def history_path(output_dir: Path) -> Path:
    return output_dir / "history.json"


def run_path(output_dir: Path, run_id: str) -> Path:
    return output_dir / "runs" / f"{run_id}.json"


def load_history(output_dir: Path) -> list[dict[str, Any]]:
    path = history_path(output_dir)
    if not path.is_file():
        return []
    return json.loads(path.read_text())


def load_run(output_dir: Path, run_id: str) -> dict[str, Any]:
    return json.loads(run_path(output_dir, run_id).read_text())


def latest_baseline(
    output_dir: Path,
    *,
    datasource_name: str,
    config_fingerprint: str,
    check_id: str,
) -> dict[str, Any] | None:
    for entry in reversed(load_history(output_dir)):
        if entry.get("datasource_name") != datasource_name:
            continue
        if entry.get("config_fingerprint") != config_fingerprint:
            continue
        if entry.get("exit_code") == 2:
            continue
        stored = load_run(output_dir, entry["run_id"])
        for check in stored.get("checks", []):
            if check.get("id") == check_id and check.get("distribution"):
                dist = dict(check["distribution"])
                dist["run_id"] = stored["run_id"]
                dist["shares"] = dist.get("shares") or []
                return dist
    return None


def persist_run(result: RunResult, output_dir: Path, history_limit: int) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "runs").mkdir(parents=True, exist_ok=True)
    lock_path = output_dir / "history.lock"
    lock_path.touch(exist_ok=True)
    with lock_path.open("a+") as lock_file:
        _lock(lock_file)
        try:
            path = run_path(output_dir, result.run_id)
            atomic_write_json(path, result.to_dict())
            history = [entry for entry in load_history(output_dir) if entry.get("run_id") != result.run_id]
            history.append(
                {
                    "run_id": result.run_id,
                    "started_at": result.started_at,
                    "as_of": result.as_of,
                    "datasource_name": result.datasource_name,
                    "config_fingerprint": result.config_fingerprint,
                    "overall_status": result.overall_status,
                    "exit_code": result.exit_code,
                    "specimen": result.specimen,
                }
            )
            history = history[-history_limit:]
            atomic_write_json(history_path(output_dir), history)
            keep = {entry["run_id"] for entry in history}
            for leftover in (output_dir / "runs").glob("*.json"):
                if leftover.stem not in keep:
                    leftover.unlink(missing_ok=True)
            return path
        finally:
            _unlock(lock_file)


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=_json_default) + "\n")
    os.replace(tmp, path)


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def _json_default(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _lock(handle) -> None:
    if fcntl is not None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)


def _unlock(handle) -> None:
    if fcntl is not None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
