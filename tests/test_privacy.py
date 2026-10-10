from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Concatenate so the working tree and git history never contain the raw tokens.
FORBIDDEN = [
    "sand" + "tech",
    "sand" + ".?technologies",
    "landing" + "_rms",
    "landing" + "_stock",
    "landing" + "_client_master",
    "landing" + "_branch_master",
    "landing" + "_material_groups",
    "npc" + "Code",
    "valuation" + "Area",
    "rms" + "Branch",
    "cmu" + "tevhe",
    "roc" + " data",
    "DHI" + "S2",
    "DHI" + "S 2",
    "customer" + "Id",
    "branch" + "Code",
    "npc" + "_code",
]

SKIP_PARTS = {".git", ".venv", ".dq", "__pycache__", ".ruff_cache", ".pytest_cache", "egg-info"}


def test_working_tree_has_no_employer_or_schema_tokens():
    pattern = re.compile("|".join(FORBIDDEN), re.I)
    hits = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_PARTS or part.endswith(".egg-info") for part in path.parts):
            continue
        if path.suffix in {".png", ".jpg", ".pyc", ".woff", ".woff2"}:
            continue
        try:
            text = path.read_text(errors="ignore")
        except Exception:
            continue
        if pattern.search(text):
            hits.append(str(path.relative_to(ROOT)))
    assert hits == []
