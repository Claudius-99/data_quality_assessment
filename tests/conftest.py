from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, text


def duckdb_url(path: Path) -> str:
    return "duckdb:///" + path.resolve().as_posix()


@pytest.fixture
def engine_url(tmp_path: Path) -> str:
    return duckdb_url(tmp_path / "src.duckdb")


def seed_engine(url: str, statements: list[str]) -> None:
    engine = create_engine(url)
    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))
    engine.dispose()
