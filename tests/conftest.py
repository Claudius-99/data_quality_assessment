from __future__ import annotations

from pathlib import Path

import pytest

from tests.helpers import duckdb_url


@pytest.fixture
def engine_url(tmp_path: Path) -> str:
    return duckdb_url(tmp_path / "src.duckdb")
