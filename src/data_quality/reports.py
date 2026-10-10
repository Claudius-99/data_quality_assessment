from __future__ import annotations

import html
from collections import defaultdict

from data_quality.models import DIMENSIONS, RunResult

CSS = """
:root {
  --stock: #f8f8f6;
  --stock-deep: #efeee9;
  --ink: #101010;
  --ink-soft: #2b2b29;
  --muted: #5b5b57;
  --rule: #cfcfc8;
  --teal: #0d4a4c;
  --ochre: #c47a2c;
  --fail: #7a2a2a;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--stock);
  color: var(--ink);
  font-family: "Iowan Old Style", Palatino, "Palatino Linotype", Georgia, serif;
  line-height: 1.45;
}
main { max-width: 960px; margin: 0 auto; padding: 2.5rem 1.25rem 4rem; }
header { border-bottom: 3px solid var(--ink); padding-bottom: 1.25rem; }
.kicker { letter-spacing: 0.16em; text-transform: uppercase; font-size: 0.72rem; color: var(--muted); }
h1 { font-size: 2.2rem; font-weight: 400; margin: 0.4rem 0 0.6rem; }
.specimen { border: 1px solid var(--ink); display: inline-block; padding: 0.15rem 0.5rem; font-size: 0.8rem; }
.meta { color: var(--ink-soft); margin-top: 0.75rem; }
table { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums lining-nums; }
th, td { text-align: left; padding: 0.45rem 0.4rem; border-bottom: 1px solid var(--rule); vertical-align: top; }
th { font-size: 0.72rem; letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted); font-weight: 500; }
.status-pass { color: var(--teal); }
.status-warn { color: var(--ochre); }
.status-fail, .status-error { color: var(--fail); }
.status-skipped, .status-not_evaluated { color: var(--muted); }
h2 { font-weight: 400; margin: 2.2rem 0 0.8rem; border-top: 3px solid var(--ink); padding-top: 0.8rem; }
"""


def render_markdown(result: RunResult) -> str:
    lines = [
        f"# Data quality run `{result.run_id}`",
        "",
    ]
    if result.specimen:
        lines.extend(["Specimen, not client data.", ""])
    lines.extend(
        [
            f"- Overall: **{result.overall_status}** (exit {result.exit_code})",
            f"- Datasource: {result.datasource_name} (`{result.datasource_url}`)",
            f"- As of: {result.as_of}",
            f"- Config: `{result.config_fingerprint}`",
            "",
            "## Tables",
            "",
            "| Table | Rows | Status | Completeness | Uniqueness | Validity | RI | Freshness | Drift |",
            "| --- | ---: | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for table in result.tables:
        dim = table.dimensions
        lines.append(
            "| {name} | {rows} | {status} | {c} | {u} | {v} | {r} | {f} | {d} |".format(
                name=table.name,
                rows=table.row_count,
                status=table.status,
                c=dim.get("completeness", "not_evaluated"),
                u=dim.get("uniqueness", "not_evaluated"),
                v=dim.get("validity", "not_evaluated"),
                r=dim.get("referential_integrity", "not_evaluated"),
                f=dim.get("freshness", "not_evaluated"),
                d=dim.get("drift", "not_evaluated"),
            )
        )
    lines.extend(["", "## Checks", ""])
    grouped: dict[str, list] = defaultdict(list)
    for check in result.checks:
        grouped[check.table].append(check)
    for table_name, checks in grouped.items():
        lines.append(f"### {table_name}")
        lines.append("")
        for check in checks:
            cols = ", ".join(check.columns)
            lines.append(f"- `{check.id}` [{check.status}] {check.dimension}/{check.check_type} ({cols})")
            lines.append(f"  {check.message}")
            if check.skip_reason:
                lines.append(f"  skip: {check.skip_reason}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_html(result: RunResult) -> str:
    specimen = '<p class="specimen">Specimen, not client data</p>' if result.specimen else ""
    rows = []
    for table in result.tables:
        dim = table.dimensions
        rows.append(
            "<tr>"
            f"<td>{_h(table.name)}</td><td>{table.row_count}</td>"
            f"<td>{_status(table.status)}</td>"
            + "".join(f"<td>{_status(dim.get(dimension, 'not_evaluated'))}</td>" for dimension in DIMENSIONS)
            + "</tr>"
        )
    checks = []
    for check in result.checks:
        checks.append(
            "<tr>"
            f"<td>{_h(check.table)}</td>"
            f"<td>{_h(check.dimension)}</td>"
            f"<td>{_h(', '.join(check.columns))}</td>"
            f"<td>{_status(check.status)}</td>"
            f"<td>{_h(check.message)}</td>"
            "</tr>"
        )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Quality run {html.escape(result.run_id)}</title>
  <style>{CSS}</style>
</head>
<body>
<main>
<header>
  <div class="kicker">Quality report</div>
  <h1>{html.escape(result.run_id)}</h1>
  {specimen}
  <p class="meta">Overall {_h(result.overall_status)} · {html.escape(result.datasource_name)} · as of {html.escape(result.as_of)}</p>
</header>
<h2>Tables</h2>
<table>
  <thead><tr><th>Table</th><th>Rows</th><th>Overall</th><th>Completeness</th><th>Uniqueness</th><th>Validity</th><th>RI</th><th>Freshness</th><th>Drift</th></tr></thead>
  <tbody>{"".join(rows)}</tbody>
</table>
<h2>Checks</h2>
<table>
  <thead><tr><th>Table</th><th>Dimension</th><th>Columns</th><th>Status</th><th>Detail</th></tr></thead>
  <tbody>{"".join(checks)}</tbody>
</table>
</main>
</body>
</html>
"""


def _h(value: object) -> str:
    return html.escape(str(value))


def _status(value: str) -> str:
    return f'<span class="status-{html.escape(value)}">{html.escape(value)}</span>'
