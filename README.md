# data quality

SQL checks for completeness, uniqueness, validity, referential integrity, freshness, and distribution drift. DuckDB is the default engine. Other SQLAlchemy URLs work when the dialect is installed.

The dashboard and demo reports are labelled **Specimen, not client data**. Rows are generated.

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Demo

```bash
data-quality demo --force
python -m http.server --directory dashboard 8000
```

Open `http://127.0.0.1:8000`. The demo writes three runs: a clean baseline, a degraded ledger, then a partial recovery. Artifacts land in `.dq/`. The dashboard reads `dashboard/data/snapshot.json`.

Open the HTML over a local server. Browsers block `fetch()` of JSON from `file://`.

## Your own tables

Copy `examples/quality.yml` and point `datasource.url` at a SQLAlchemy URL (`duckdb:///./warehouse.duckdb` or `postgresql+psycopg2://${DQ_DATABASE_URL}` style `${ENV}` substitution). List tables and checks. Do not put passwords in the file.

```bash
data-quality validate --config examples/quality.yml
data-quality run --config quality.yml
```

Missing tables or columns fail closed (exit 2). Data failures write reports and still exit 1.

| Exit | Meaning |
| --- | --- |
| 0 | completed, no failed checks (warnings are allowed) |
| 1 | completed, at least one failed check |
| 2 | config, connection, missing objects, or artifact error |

`--as-of` pins the freshness clock. The first drift run is skipped (`no_baseline`); later runs compute PSI against the latest comparable completed run.

Reports do not include raw failed values.

## Dashboard JSON

`dashboard/schema.json` describes `dashboard/data/snapshot.json` for a later site embed. Generate a fresh snapshot with `data-quality demo --force` or any `data-quality run`.

## Tests

```bash
pytest
ruff check src tests
ruff format --check src tests
```
