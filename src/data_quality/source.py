from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    create_engine,
    inspect,
    text,
)
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import SQLAlchemyError

from data_quality.config import AssessmentConfig, TableConfig


class SourceError(RuntimeError):
    """Connection, reflection, or missing-object failure."""


@dataclass
class BoundTable:
    name: str
    schema_name: str | None
    table: Table
    columns: set[str]


def redact_url(url: str) -> str:
    try:
        parsed = make_url(url)
        return parsed.render_as_string(hide_password=True)
    except Exception:
        if "://" not in url:
            return url
        scheme, rest = url.split("://", 1)
        if "@" in rest:
            rest = "***@" + rest.rsplit("@", 1)[1]
        return f"{scheme}://{rest}"


def create_source(url: str) -> Engine:
    try:
        engine = create_engine(url)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return engine
    except SQLAlchemyError as exc:
        raise SourceError(f"cannot connect to {redact_url(url)}: {exc.__class__.__name__}") from exc
    except Exception as exc:
        raise SourceError(f"cannot connect to {redact_url(url)}: {exc.__class__.__name__}") from exc


def reflect_tables(engine: Engine, config: AssessmentConfig) -> dict[str, BoundTable]:
    bound: dict[str, BoundTable] = {}
    metadata = MetaData()
    for logical, table_cfg in config.tables.items():
        schema = table_cfg.schema_name
        if not _table_exists(engine, table_cfg):
            qualified = f"{schema}.{logical}" if schema else logical
            raise SourceError(f"configured table not found: {qualified}")
        try:
            columns = _column_defs(engine, logical, schema)
            table = Table(logical, metadata, *columns, schema=schema)
        except SourceError:
            raise
        except SQLAlchemyError as exc:
            raise SourceError(f"failed to reflect {logical}: {exc.__class__.__name__}") from exc
        bound[logical] = BoundTable(
            name=logical,
            schema_name=schema,
            table=table,
            columns={col.name for col in table.columns},
        )
    missing: list[str] = []
    for spec in config.checks:
        table = bound[spec.table]
        for column in spec.columns:
            if column not in table.columns:
                missing.append(f"{spec.table}.{column}")
        parent = spec.params.get("parent")
        if parent:
            parent_table = bound[str(parent)]
            for column in spec.params["parent_columns"]:
                if column not in parent_table.columns:
                    missing.append(f"{parent}.{column}")
    if missing:
        raise SourceError("configured columns not found: " + ", ".join(sorted(set(missing))))
    return bound


def dialect_name(engine: Engine) -> str:
    return engine.dialect.name


def _table_exists(engine: Engine, table_cfg: TableConfig) -> bool:
    if dialect_name(engine) == "duckdb":
        sql = "SELECT 1 FROM information_schema.tables WHERE lower(table_name) = lower(:name)"
        params = {"name": table_cfg.name}
        if table_cfg.schema_name:
            sql += " AND lower(table_schema) = lower(:schema)"
            params["schema"] = table_cfg.schema_name
        with engine.connect() as conn:
            return conn.execute(text(sql), params).first() is not None
    inspector = inspect(engine)
    try:
        return inspector.has_table(table_cfg.name, schema=table_cfg.schema_name)
    except TypeError:
        return inspector.has_table(table_cfg.name)


def _column_defs(engine: Engine, name: str, schema: str | None) -> list[Column]:
    if dialect_name(engine) == "duckdb":
        sql = (
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE lower(table_name) = lower(:name)"
        )
        params = {"name": name}
        if schema:
            sql += " AND lower(table_schema) = lower(:schema)"
            params["schema"] = schema
        sql += " ORDER BY ordinal_position"
        with engine.connect() as conn:
            rows = conn.execute(text(sql), params).fetchall()
        if not rows:
            raise SourceError(f"no columns reflected for {name}")
        return [Column(str(row[0]), _duckdb_type(str(row[1]))) for row in rows]
    inspector = inspect(engine)
    reflected = inspector.get_columns(name, schema=schema)
    return [Column(col["name"], col["type"]) for col in reflected]


def _duckdb_type(type_name: str):
    key = type_name.upper().split("(")[0].strip()
    mapping = {
        "VARCHAR": String(),
        "TEXT": Text(),
        "STRING": String(),
        "DOUBLE": Float(),
        "FLOAT": Float(),
        "REAL": Float(),
        "INTEGER": Integer(),
        "INT": Integer(),
        "BIGINT": BigInteger(),
        "HUGEINT": BigInteger(),
        "BOOLEAN": Boolean(),
        "BOOL": Boolean(),
        "TIMESTAMP": DateTime(),
        "TIMESTAMP_NS": DateTime(),
        "TIMESTAMP_MS": DateTime(),
        "TIMESTAMP_S": DateTime(),
        "TIMESTAMP WITH TIME ZONE": DateTime(timezone=True),
        "TIMESTAMPTZ": DateTime(timezone=True),
        "DATE": Date(),
        "DECIMAL": Numeric(),
        "NUMERIC": Numeric(),
    }
    return mapping.get(key, String())
