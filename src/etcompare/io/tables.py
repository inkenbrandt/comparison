"""Parquet round trips for canonical tables.

Files carry an ``etcompare`` key in the Arrow schema metadata with the schema
name and version, column units, date semantics and the table's attributes.
Date columns are stored as Arrow ``date32`` so the file itself says "calendar
label", not "instant". Pandas serialization metadata is kept for extra columns
but is not treated as scientific provenance.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from etcompare import __version__
from etcompare.errors import SchemaError
from etcompare.log import get_logger, log_event
from etcompare.schemas import DATE_SEMANTICS, SCHEMAS, CanonicalTable, TableSchema, conform

META_KEY = b"etcompare"
_log = get_logger(__name__)


def _metadata(table: CanonicalTable) -> dict[str, Any]:
    s = table.schema
    return {
        "schema": s.name,
        "schema_version": s.version,
        "primary_key": list(s.primary_key),
        "units": s.units,
        "date_columns": {c.name: DATE_SEMANTICS for c in s.columns if c.kind == "date"},
        "attributes": dict(table.attributes),
        "etcompare_version": __version__,
    }


def write_table(table: CanonicalTable, path: str | os.PathLike[str]) -> Path:
    """Write a canonical table to Parquet atomically (temp file, then rename)."""
    path = Path(path)
    frame = conform(table.frame, table.schema, source=str(path))
    arrow = pa.Table.from_pandas(frame, preserve_index=False)
    for col in table.schema.columns:
        if col.kind == "date":
            i = arrow.schema.get_field_index(col.name)
            arrow = arrow.set_column(i, pa.field(col.name, pa.date32()),
                                     arrow.column(i).cast(pa.date32()))
    meta = dict(arrow.schema.metadata or {})
    meta[META_KEY] = json.dumps(_metadata(table), default=str).encode("utf-8")
    arrow = arrow.replace_schema_metadata(meta)

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    os.close(fd)
    try:
        pq.write_table(arrow, tmp)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    log_event(_log, "table_written", table=table.schema.name, path=str(path),
              n_rows=len(frame))
    return path


def read_metadata(path: str | os.PathLike[str]) -> dict[str, Any] | None:
    """The ``etcompare`` metadata block of a Parquet file, or ``None`` if absent."""
    meta = pq.read_schema(path).metadata or {}
    raw = meta.get(META_KEY)
    return json.loads(raw) if raw else None


def read_table(
    path: str | os.PathLike[str], schema: TableSchema | None = None
) -> CanonicalTable:
    """Read a canonical table written by :func:`write_table`.

    ``schema`` is taken from the file's metadata when omitted; when given, it
    must match the file's schema name and version.
    """
    path = Path(path)
    meta = read_metadata(path)
    if meta is None:
        if schema is None:
            raise SchemaError(f"{path}: no etcompare metadata; pass schema= to read it")
        attributes: Mapping[str, Any] = {}
    else:
        stored = SCHEMAS.get(meta["schema"])
        if stored is None:
            raise SchemaError(f"{path}: unknown schema {meta['schema']!r}")
        if schema is not None and schema.name != stored.name:
            raise SchemaError(f"{path}: file holds {stored.name!r}, expected {schema.name!r}")
        schema = schema or stored
        if meta["schema_version"] != schema.version:
            raise SchemaError(f"{path}: schema version {meta['schema_version']} != "
                              f"{schema.version}")
        if meta.get("units", {}) != schema.units:
            raise SchemaError(f"{path}: stored units {meta.get('units')} differ from "
                              f"schema units {schema.units}")
        attributes = meta.get("attributes", {})
    frame = pq.read_table(path).to_pandas()
    return CanonicalTable(schema, conform(frame, schema, source=str(path)), dict(attributes))


def read_any(path: str | os.PathLike[str], **csv_kwargs: Any) -> pd.DataFrame:
    """Read a CSV or Parquet file into a DataFrame without interpretation."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(path, **csv_kwargs)
    if suffix in (".parquet", ".pq"):
        return pd.read_parquet(path)
    raise SchemaError(f"{path}: unsupported table format {suffix!r} (use .csv or .parquet)")
