from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb
import yaml

from data_quality.config import load_config
from data_quality.runner import run_assessment

DEMO_SEED = 99
N_CUSTOMERS = 200
N_ACCOUNTS = 280
N_TXNS = 1200
AS_OF = (
    datetime(2026, 3, 15, tzinfo=timezone.utc),
    datetime(2026, 3, 22, tzinfo=timezone.utc),
    datetime(2026, 3, 25, tzinfo=timezone.utc),
)
RUN_IDS = ("demo-01-baseline", "demo-02-degraded", "demo-03-recovery")
CHANNELS = ("card", "ach", "wire", "cash")
PRODUCTS = ("checking", "savings", "credit")
STATUSES = ("active", "closed")


class DemoError(RuntimeError):
    pass


def demo_yaml() -> dict:
    return {
        "schema_version": 1,
        "specimen": True,
        "datasource": {"name": "demo_ledger", "url": "duckdb:///./warehouse.duckdb"},
        "output": {
            "dir": ".dq",
            "snapshot": "dashboard/data/snapshot.json",
            "history_limit": 50,
            "worst_columns": 10,
        },
        "tables": {
            "customers": {
                "completeness": [{"column": "email", "max_null_ratio": 0.0}],
                "uniqueness": [{"columns": ["customer_id"]}],
                "validity": [
                    {"column": "status", "accepted_values": ["active", "closed"]},
                    {"column": "email", "pattern": "^[^@]+@[^@]+\\.[^@]+$"},
                ],
                "freshness": [{"column": "updated_at", "max_age_hours": 72}],
            },
            "accounts": {
                "completeness": [{"column": "product", "max_null_ratio": 0.0}],
                "uniqueness": [{"columns": ["account_id"]}],
                "validity": [{"column": "status", "accepted_values": ["active", "closed"]}],
            },
            "transactions": {
                "completeness": [{"column": "amount", "max_null_ratio": 0.0}],
                "uniqueness": [{"columns": ["txn_id"]}],
                "validity": [{"column": "amount", "min": 0.01, "max": 100000}],
                "freshness": [{"column": "posted_at", "max_age_hours": 168}],
                "drift": [
                    {"column": "amount", "kind": "numeric"},
                    {"column": "channel", "kind": "categorical"},
                ],
            },
        },
        "relationships": [
            {
                "name": "accounts_customer",
                "child": "accounts",
                "child_columns": ["customer_id"],
                "parent": "customers",
                "parent_columns": ["customer_id"],
            },
            {
                "name": "transactions_account",
                "child": "transactions",
                "child_columns": ["account_id"],
                "parent": "accounts",
                "parent_columns": ["account_id"],
            },
        ],
    }


def write_example_config(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(demo_yaml(), sort_keys=False))


def run_demo(
    *,
    output_dir: Path,
    snapshot_path: Path,
    warehouse: Path | None = None,
    force: bool = False,
    example_path: Path | None = None,
) -> list[int]:
    output_dir = output_dir.resolve()
    snapshot_path = snapshot_path.resolve()
    warehouse = (warehouse or output_dir / "demo.duckdb").resolve()
    if warehouse.exists() and not force:
        raise DemoError(f"{warehouse} already exists; pass --force to rebuild")
    if output_dir.exists() and not force:
        history = output_dir / "history.json"
        if history.exists():
            raise DemoError(f"{output_dir} already has history; pass --force to rebuild")
    if warehouse.exists():
        warehouse.unlink()
    if output_dir.exists() and force:
        _reset_output(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    raw = demo_yaml()
    raw["datasource"]["url"] = _duckdb_url(warehouse)
    raw["output"]["dir"] = str(output_dir)
    raw["output"]["snapshot"] = str(snapshot_path)
    config_path = output_dir / "quality.yml"
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False))
    if example_path:
        write_example_config(example_path)

    rng = random.Random(DEMO_SEED)
    customers, accounts, txns = _healthy(rng, AS_OF[0])
    _write_warehouse(warehouse, customers, accounts, txns)

    config = load_config(config_path)
    exits = []
    result = run_assessment(config, as_of=AS_OF[0], run_id=RUN_IDS[0])
    exits.append(result.exit_code)

    customers, accounts, txns = _degrade(rng, customers, accounts, txns, AS_OF[1])
    _write_warehouse(warehouse, customers, accounts, txns)
    config = load_config(config_path)
    result = run_assessment(config, as_of=AS_OF[1], run_id=RUN_IDS[1])
    exits.append(result.exit_code)

    customers, accounts, txns = _recover(rng, customers, accounts, txns, AS_OF[2])
    _write_warehouse(warehouse, customers, accounts, txns)
    config = load_config(config_path)
    result = run_assessment(config, as_of=AS_OF[2], run_id=RUN_IDS[2])
    exits.append(result.exit_code)
    return exits


def _healthy(rng: random.Random, as_of: datetime):
    customers = []
    for i in range(1, N_CUSTOMERS + 1):
        cid = f"C{i:04d}"
        customers.append(
            (
                cid,
                f"person.{i:03d}@example.com",
                STATUSES[0] if i % 17 else STATUSES[1],
                as_of - timedelta(hours=rng.randint(1, 36)),
            )
        )
    accounts = []
    for i in range(1, N_ACCOUNTS + 1):
        owner = customers[(i - 1) % N_CUSTOMERS][0]
        accounts.append(
            (
                f"A{i:04d}",
                owner,
                PRODUCTS[i % len(PRODUCTS)],
                as_of - timedelta(days=rng.randint(30, 400)),
                STATUSES[0] if i % 23 else STATUSES[1],
            )
        )
    txns = []
    for i in range(1, N_TXNS + 1):
        account = accounts[(i - 1) % N_ACCOUNTS][0]
        amount = round(abs(rng.lognormvariate(3.6, 0.45)), 2)
        amount = min(max(amount, 0.50), 8000.0)
        channel = (
            CHANNELS[0]
            if i % 10 < 4
            else CHANNELS[1]
            if i % 10 < 7
            else CHANNELS[2]
            if i % 10 < 9
            else CHANNELS[3]
        )
        txns.append(
            (
                f"T{i:05d}",
                account,
                amount,
                as_of - timedelta(hours=rng.randint(1, 120)),
                channel,
            )
        )
    return customers, accounts, txns


def _degrade(rng: random.Random, customers, accounts, txns, as_of: datetime):
    customers = [list(row) for row in customers]
    accounts = [list(row) for row in accounts]
    txns = [list(row) for row in txns]
    for row in customers[10:50]:
        row[1] = None
    extras = []
    for row in customers[:8]:
        extras.append([row[0], row[1], row[2], row[3]])
    customers.extend(extras)
    for row in txns[0:90]:
        row[2] = 0.0
    for row in txns[90:]:
        row[2] = round(float(row[2]) * 4.0, 2) if row[2] is not None else row[2]
        row[3] = as_of - timedelta(days=21, hours=rng.randint(0, 10))
        if rng.random() < 0.72:
            row[4] = "card"
    for i in range(1, 16):
        txns.append(
            (
                f"TORPH{i:02d}",
                f"A9{i:03d}",
                25.0,
                as_of - timedelta(days=21),
                "wire",
            )
        )
    return customers, accounts, txns


def _recover(rng: random.Random, customers, accounts, txns, as_of: datetime):
    customers = [list(row) for row in customers[:N_CUSTOMERS]]
    for index, row in enumerate(customers, start=1):
        row[1] = f"person.{index:03d}@example.com"
        row[3] = as_of - timedelta(hours=rng.randint(2, 24))
    txns = [list(row) for row in txns if not str(row[1]).startswith("A9")]
    account_ids = {row[0] for row in accounts}
    cleaned = []
    for row in txns:
        if row[1] not in account_ids:
            continue
        amount = float(row[2])
        if amount < 0.01:
            amount = 0.50
        row[2] = round(amount, 2)
        row[3] = as_of - timedelta(hours=rng.randint(1, 48))
        cleaned.append(row)
    return customers, accounts, cleaned


def _write_warehouse(path: Path, customers, accounts, txns) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    con = duckdb.connect(str(path))
    try:
        con.execute(
            """
            CREATE TABLE customers (
                customer_id VARCHAR,
                email VARCHAR,
                status VARCHAR,
                updated_at TIMESTAMP
            )
            """
        )
        con.execute(
            """
            CREATE TABLE accounts (
                account_id VARCHAR,
                customer_id VARCHAR,
                product VARCHAR,
                opened_at TIMESTAMP,
                status VARCHAR
            )
            """
        )
        con.execute(
            """
            CREATE TABLE transactions (
                txn_id VARCHAR,
                account_id VARCHAR,
                amount DOUBLE,
                posted_at TIMESTAMP,
                channel VARCHAR
            )
            """
        )
        con.executemany("INSERT INTO customers VALUES (?, ?, ?, ?)", customers)
        con.executemany("INSERT INTO accounts VALUES (?, ?, ?, ?, ?)", accounts)
        con.executemany("INSERT INTO transactions VALUES (?, ?, ?, ?, ?)", txns)
    finally:
        con.close()


def _duckdb_url(path: Path) -> str:
    return "duckdb:///" + path.resolve().as_posix()


def _reset_output(output_dir: Path) -> None:
    for child in output_dir.rglob("*"):
        if child.is_file():
            child.unlink()
    for child in sorted(output_dir.rglob("*"), reverse=True):
        if child.is_dir():
            child.rmdir()
