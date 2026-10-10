from __future__ import annotations

import argparse
import sys
from pathlib import Path

from data_quality.config import ConfigError, load_config
from data_quality.demo import DemoError, run_demo, write_example_config
from data_quality.models import EXIT_ERROR, EXIT_INTERRUPT, EXIT_OK
from data_quality.runner import parse_as_of, run_assessment
from data_quality.source import SourceError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="data-quality",
        description="Run completeness, uniqueness, validity, referential, freshness, and drift checks.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    validate = sub.add_parser("validate", help="load and check a YAML config")
    validate.add_argument("--config", required=True)

    run = sub.add_parser("run", help="evaluate configured tables")
    run.add_argument("--config", required=True)
    run.add_argument("--as-of", dest="as_of", default=None, help="UTC ISO-8601 clock, default now")

    demo = sub.add_parser("demo", help="build a synthetic ledger and three assessment runs")
    demo.add_argument("--force", action="store_true")
    demo.add_argument("--output", default=".dq")
    demo.add_argument("--snapshot", default="dashboard/data/snapshot.json")

    args = parser.parse_args(argv)
    try:
        if args.cmd == "validate":
            return cmd_validate(args.config)
        if args.cmd == "run":
            return cmd_run(args.config, args.as_of)
        if args.cmd == "demo":
            return cmd_demo(Path(args.output), Path(args.snapshot), args.force)
        parser.error("unknown command")
        return EXIT_ERROR
    except KeyboardInterrupt:
        return EXIT_INTERRUPT
    except (ConfigError, SourceError, DemoError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR


def cmd_validate(config_path: str) -> int:
    config = load_config(config_path)
    print(f"ok: {len(config.tables)} tables, {len(config.checks)} checks, url dialect reserved until connect")
    return EXIT_OK


def cmd_run(config_path: str, as_of: str | None) -> int:
    config = load_config(config_path)
    result = run_assessment(config, as_of=parse_as_of(as_of))
    _print_result(result)
    return result.exit_code


def cmd_demo(output_dir: Path, snapshot: Path, force: bool) -> int:
    example = Path("examples/quality.yml")
    exits = run_demo(
        output_dir=output_dir,
        snapshot_path=snapshot,
        force=force,
        example_path=example if not example.exists() or force else None,
    )
    if not example.exists():
        write_example_config(example)
    print("demo runs:")
    labels = ("baseline", "degraded", "recovery")
    for label, code in zip(labels, exits):
        print(f"  {label}: exit {code}")
    print(f"reports: {output_dir.resolve() / 'reports'}")
    print(f"snapshot: {snapshot.resolve()}")
    print("serve dashboard: python -m http.server --directory dashboard 8000")
    return EXIT_OK


def _print_result(result) -> None:
    print(f"run {result.run_id}  {result.overall_status}  exit {result.exit_code}")
    print(f"datasource {result.datasource_name}  {result.datasource_url}")
    print(f"as_of {result.as_of}")
    for table in result.tables:
        print(f"  {table.name}: {table.status} ({table.row_count} rows)")
    failed = [c for c in result.checks if c.status in {"fail", "error"}]
    if failed:
        print("failed checks:")
        for check in failed:
            print(f"  {check.id}: {check.message}")
