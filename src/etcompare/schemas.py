"""Canonical table schemas and conformance checks (guide step 3).

Every canonical table is a pandas DataFrame whose columns, dtypes, units,
nullability and primary key are declared by a :class:`TableSchema`.
:func:`conform` validates a frame against its schema and returns a copy with
canonical dtypes; it raises instead of silently coercing ambiguous values.

Dates are **station-day labels**: naive ``datetime64[ns]`` values at midnight
in memory and Arrow ``date32`` on disk. A timestamp must be converted to a
station-day label using the station's declared day definition before it
enters a canonical table; :func:`conform` rejects timezone-aware values and
values with a time component rather than truncating them.

Sample counts are named (``n_sites``, ``n_days``, ``n_periods``); no table has
a bare ``n`` column.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Final, Literal

import numpy as np
import pandas as pd

from etcompare.config import ET_UNIT as ET_UNIT
from etcompare.config import (
    FOOTPRINT_VALUE_SEMANTICS,
    INTERVAL_LABELS,
    PRODUCT_DAY_DEFINITIONS,
    STATION_DAY_DEFINITIONS,
    TEMPORAL_SUPPORTS,
)
from etcompare.errors import DuplicateKeyError, SchemaError

Kind = Literal["string", "date", "float", "int", "bool"]

_PANDAS_DTYPE: Final[Mapping[str, str]] = MappingProxyType({
    "string": "string",
    "date": "datetime64[ns]",
    "float": "float64",
    "int": "Int64",
    "bool": "boolean",
})

#: Meaning attached to every ``date`` column; stored in Parquet metadata.
DATE_SEMANTICS: Final = "station_day_label"

FRACTION: Final = "1"

REFERENCE_PROVENANCE: Final = (
    "corrected_from_raw", "corrected_filled", "reference_missing", "inconsistent_flags",
)
REFERENCE_VARIANTS: Final = ("et_raw", "et_corrected_measured", "et_corrected_inclusive")
DAY_ALIGNMENT: Final = ("declared", "unverified")
PERIODS: Final = ("day", "month")
SEVERITY: Final = ("error", "warning", "info")
SCALING_SOURCES: Final = ("declared", "from_header", "not_read")


@dataclass(frozen=True)
class ColumnSpec:
    """One column: name, kind, unit, nullability and optional allowed values.

    ``required=False`` columns may be absent from input; :func:`conform` adds
    them filled with nulls.
    """

    name: str
    kind: Kind
    description: str
    unit: str | None = None
    nullable: bool = True
    required: bool = True
    allowed: tuple[str, ...] | None = None

    @property
    def dtype(self) -> str:
        return _PANDAS_DTYPE[self.kind]


@dataclass(frozen=True)
class TableSchema:
    """A canonical table: ordered columns, primary key and version."""

    name: str
    version: str
    description: str
    columns: tuple[ColumnSpec, ...]
    primary_key: tuple[str, ...] = ()
    allow_extra_columns: bool = False
    _by_name: Mapping[str, ColumnSpec] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        by_name = {c.name: c for c in self.columns}
        if len(by_name) != len(self.columns):
            raise ValueError(f"schema {self.name}: duplicate column names")
        for k in self.primary_key:
            if k not in by_name:
                raise ValueError(f"schema {self.name}: key column {k!r} not declared")
            if by_name[k].nullable:
                raise ValueError(f"schema {self.name}: key column {k!r} must be non-nullable")
        object.__setattr__(self, "_by_name", MappingProxyType(by_name))

    @property
    def names(self) -> list[str]:
        return [c.name for c in self.columns]

    def column(self, name: str) -> ColumnSpec:
        return self._by_name[name]

    def __contains__(self, name: object) -> bool:
        return name in self._by_name

    @property
    def units(self) -> dict[str, str]:
        return {c.name: c.unit for c in self.columns if c.unit is not None}

    def empty(self) -> pd.DataFrame:
        """A zero-row frame with canonical dtypes."""
        return pd.DataFrame({c.name: pd.Series(dtype=c.dtype) for c in self.columns})


def _c(name: str, kind: Kind, description: str, **kw: Any) -> ColumnSpec:
    return ColumnSpec(name, kind, description, **kw)


def _key(name: str, kind: Kind, description: str, **kw: Any) -> ColumnSpec:
    return ColumnSpec(name, kind, description, nullable=False, **kw)


# --------------------------------------------------------------------------- schemas

STATIONS = TableSchema(
    "stations", "1", "One row per station with its measurement-day definition.",
    (
        _key("site_id", "string", "Station identifier, preserved exactly"),
        _c("name", "string", "Station name"),
        _c("latitude", "float", "Latitude", unit="degree_north"),
        _c("longitude", "float", "Longitude", unit="degree_east"),
        _c("elevation_m", "float", "Elevation", unit="m"),
        _c("land_cover", "string", "Land-cover class (e.g. IGBP code)"),
        _c("timezone", "string", "Fixed offset or IANA zone of the station day", nullable=False),
        _c("day_definition", "string", "How the station day is bounded", nullable=False,
           allowed=STATION_DAY_DEFINITIONS),
        _c("interval_label", "string", "Whether subdaily stamps label interval start or end",
           nullable=False, allowed=INTERVAL_LABELS),
    ),
    primary_key=("site_id",),
)

STATION_DAILY = TableSchema(
    "station_daily", "1",
    "Daily station ET with reference provenance. Source columns pass through with prefix "
    "'src_'.",
    (
        _key("site_id", "string", "Station identifier"),
        _key("date", "date", "Station-day label"),
        _c("et_raw_mm", "float", "Uncorrected daily station ET", unit=ET_UNIT),
        _c("et_corrected_mm", "float",
           "Energy-balance-corrected ET; includes gap-filled values where gap_filled is True",
           unit=ET_UNIT),
        _c("gap_filled", "bool", "Source gap flag for the corrected series"),
        _c("closure_factor", "float", "Energy-balance closure correction factor", unit=FRACTION,
           required=False),
        _c("raw_available", "bool", "et_raw_mm is non-null", nullable=False),
        _c("reference_provenance", "string", "Derived reference category (contract 4.3)",
           nullable=False, allowed=REFERENCE_PROVENANCE),
        _c("subdaily_valid_fraction", "float", "Fraction of subdaily records observed",
           unit=FRACTION, required=False),
    ),
    primary_key=("site_id", "date"),
    allow_extra_columns=True,
)

MODEL_MANIFEST = TableSchema(
    "model_manifest", "1",
    "One row per OpenET raster asset; multi-site assets list every member site.",
    (
        _key("asset_id", "string", "Filename stem; unique"),
        _c("path", "string", "Local path when indexed", nullable=False),
        _c("model", "string", "Canonical model name", nullable=False),
        _c("product_version", "string", "Declared product/model version or 'unknown'",
           nullable=False),
        _c("variable", "string", "Variable token from the filename", nullable=False),
        _c("date", "date", "Period start label", nullable=False),
        _c("period", "string", "Temporal period of the asset", nullable=False, allowed=PERIODS),
        _c("member_sites", "string", "Sorted ';'-joined site IDs from the filename",
           nullable=False),
        _c("n_sites", "int", "Number of member sites", nullable=False),
        _c("site_aliases_applied", "string", "';'-joined alias=site mappings used"),
        _c("temporal_support", "string", "Declared temporal support", allowed=TEMPORAL_SUPPORTS),
        _c("day_definition", "string", "Declared product day definition",
           allowed=PRODUCT_DAY_DEFINITIONS),
        _c("units", "string", "Declared ET units"),
        _c("scale_factor", "float", "Raster scale factor"),
        _c("add_offset", "float", "Raster offset"),
        _c("nodata", "float", "Raster nodata value"),
        _c("scaling_source", "string", "Origin of scale/offset/nodata", nullable=False,
           allowed=SCALING_SOURCES),
        _c("crs", "string", "Raster CRS (null until the raster header is read)"),
    ),
    primary_key=("asset_id",),
)

ASSET_SITES = TableSchema(
    "asset_sites", "1", "Association of each raster asset with each member site.",
    (
        _key("asset_id", "string", "Asset identifier"),
        _key("site_id", "string", "Member site"),
        _c("model", "string", "Canonical model name", nullable=False),
        _c("date", "date", "Period start label", nullable=False),
    ),
    primary_key=("asset_id", "site_id"),
)

FOOTPRINT_MANIFEST = TableSchema(
    "footprint_manifest", "1", "One row per footprint raster within a declared footprint set.",
    (
        _key("footprint_set_id", "string", "Declared footprint set"),
        _key("asset_id", "string", "Filename stem; unique within the set"),
        _c("path", "string", "Local path", nullable=False),
        _c("site_id", "string", "Station", nullable=False),
        _c("date", "date", "Period start label", nullable=False),
        _c("period", "string", "Temporal period", nullable=False, allowed=PERIODS),
        _c("layer_token", "string", "Filename layer token (e.g. 'etw'); not a method claim",
           nullable=False),
        _c("weighting_method", "string", "Declared temporal weighting method", nullable=False),
        _c("value_semantics", "string", "Declared meaning of cell values", nullable=False,
           allowed=FOOTPRINT_VALUE_SEMANTICS),
        _c("domain_capture_frac", "float", "Original modeled-domain capture, if supplied",
           unit=FRACTION),
        _c("domain_capture_source", "string", "Where domain_capture_frac came from",
           required=False),
        _c("temporal_coverage_frac", "float", "Fraction of the period's timesteps used",
           unit=FRACTION),
    ),
    primary_key=("footprint_set_id", "asset_id"),
)

EXTRACTIONS = TableSchema(
    "extractions", "1", "Spatially extracted model ET, one row per site/date/model/method.",
    (
        _key("site_id", "string", "Station"),
        _key("date", "date", "Station-day label (period start)"),
        _key("period", "string", "Temporal period", allowed=PERIODS),
        _key("model", "string", "Canonical model name"),
        _key("extraction_method", "string", "Stable extraction-method identifier"),
        _key("footprint_set_id", "string", "Footprint set used (or 'none')"),
        _c("footprint_method", "string", "Declared footprint weighting method", nullable=False),
        _c("et_mm", "float", "Extracted model ET", unit=ET_UNIT),
        _c("fp_weight_sum", "float", "Sum of footprint weights over finite cells", unit=FRACTION),
        _c("fp_weight_retained_frac", "float", "Retained footprint weight", unit=FRACTION),
        _c("fp_weight_retained_denominator", "string", "Denominator of the retained fraction"),
        _c("support_id", "string", "Identifier of the valid-cell support, when known"),
        _c("fp_frac_in_model_extent", "float",
           "Footprint contribution inside the model raster extent / footprint grid total",
           unit=FRACTION, required=False),
        _c("fp_domain_capture_frac", "float",
           "Original modeled-domain capture of the footprint (separate denominator; never "
           "multiplied with the retained fractions)", unit=FRACTION, required=False),
        _c("n_cells_valid", "int", "Model cells contributing to et_mm", required=False),
        _c("model_grid_id", "string", "Identity of the model raster grid", required=False),
        _c("day_alignment", "string", "Whether day definitions are declared on both sides",
           nullable=False, allowed=DAY_ALIGNMENT),
        _c("model_asset_id", "string", "Model raster asset"),
        _c("footprint_asset_id", "string", "Footprint raster asset"),
        _c("source_path", "string", "Original source path (provenance only)"),
    ),
    primary_key=("site_id", "date", "period", "model", "extraction_method", "footprint_set_id"),
)

PAIRED = TableSchema(
    "paired", "2", "Observation-model pairs belonging to one cohort of one comparison.",
    (
        _key("comparison_id", "string", "Comparison identifier"),
        _key("cohort_id", "string", "Cohort identifier"),
        _key("site_id", "string", "Station"),
        _key("date", "date", "Station-day label"),
        _key("model", "string", "Canonical model name"),
        _key("reference_variant", "string", "Reference variant", allowed=REFERENCE_VARIANTS),
        _key("extraction_method", "string", "Extraction method"),
        _key("footprint_set_id", "string", "Footprint set of the extraction (or 'none')"),
        _c("observed_mm", "float", "Station reference ET", unit=ET_UNIT),
        _c("modeled_mm", "float", "Model ET", unit=ET_UNIT),
        _c("reference_provenance", "string", "Reference provenance", nullable=False,
           allowed=REFERENCE_PROVENANCE),
        _c("flags", "string", "';'-joined row flags"),
    ),
    primary_key=("comparison_id", "cohort_id", "site_id", "date", "model", "reference_variant",
                 "extraction_method", "footprint_set_id"),
)

METRICS = TableSchema(
    "metrics", "1", "Metric values with explicit sample counts and validity.",
    (
        _key("comparison_id", "string", "Comparison identifier"),
        _key("cohort_id", "string", "Cohort identifier"),
        _key("scope", "string", "Site ID or group label the metric summarizes"),
        _c("scope_type", "string", "'site' or 'group'", nullable=False,
           allowed=("site", "group")),
        _key("temporal_scale", "string", "e.g. daily, monthly"),
        _key("model", "string", "Canonical model name"),
        _key("reference_variant", "string", "Reference variant", allowed=REFERENCE_VARIANTS),
        _key("extraction_method", "string", "Extraction method"),
        _key("metric", "string", "Stable metric identifier"),
        _c("value", "float", "Metric value; null when undefined"),
        _c("units", "string", "Units of value"),
        _c("n_sites", "int", "Sites contributing", nullable=False),
        _c("n_days", "int", "Days contributing"),
        _c("n_periods", "int", "Periods contributing (for aggregated scales)"),
        _c("period_completeness", "float", "Accepted/expected days", unit=FRACTION),
        _c("validity_reason", "string", "Why value is null, if it is"),
    ),
    primary_key=("comparison_id", "cohort_id", "scope", "temporal_scale", "model",
                 "reference_variant", "extraction_method", "metric"),
)

INTERVALS = TableSchema(
    "intervals", "1", "Resampling intervals for metrics and contrasts.",
    (
        _key("comparison_id", "string", "Comparison identifier"),
        _key("metric", "string", "Metric identifier"),
        _key("target", "string", "Model name or contrast label (e.g. 'eemetric-ssebop')"),
        _c("estimate", "float", "Point estimate"),
        _c("lower", "float", "Lower bound"),
        _c("upper", "float", "Upper bound"),
        _c("confidence_level", "float", "Nominal level", unit=FRACTION, nullable=False),
        _c("method", "string", "Interval method", nullable=False),
        _c("seed", "int", "Random seed"),
        _c("block_length", "int", "Block length in daily samples"),
        _c("n_replicates_requested", "int", "Requested replicates"),
        _c("n_replicates_valid", "int", "Replicates with a defined statistic"),
        _c("n_segments", "int", "Independent uninterrupted segments"),
        _c("status", "string", "ok / insufficient_support / ...", nullable=False),
    ),
    primary_key=("comparison_id", "metric", "target"),
)

EXCLUSIONS = TableSchema(
    "exclusions", "1", "Rows removed or flagged, with stage and reason.",
    (
        _c("stage", "string", "Processing stage", nullable=False),
        _c("input_key", "string", "Key of the affected input row or file", nullable=False),
        _c("reason_code", "string", "Stable reason identifier", nullable=False),
        _c("severity", "string", "Severity", nullable=False, allowed=SEVERITY),
        _c("affected_comparison", "string", "Comparison affected, if specific"),
        _c("detail", "string", "Human-readable detail"),
    ),
)

SCHEMAS: Final[Mapping[str, TableSchema]] = MappingProxyType({
    s.name: s for s in (STATIONS, STATION_DAILY, MODEL_MANIFEST, ASSET_SITES,
                        FOOTPRINT_MANIFEST, EXTRACTIONS, PAIRED, METRICS, INTERVALS, EXCLUSIONS)
})


# --------------------------------------------------------------------------- checks

def check_primary_key(
    df: pd.DataFrame, keys: Sequence[str], *, table: str, max_examples: int = 5
) -> None:
    """Raise :class:`DuplicateKeyError` if ``keys`` are null or not unique in ``df``.

    Call before any join; a duplicated key would multiply rows silently.
    """
    keys = list(keys)
    missing = [k for k in keys if k not in df.columns]
    if missing:
        raise SchemaError(f"{table}: key column(s) {missing} missing")
    nulls = df[keys].isna().any(axis=1)
    if nulls.any():
        raise DuplicateKeyError(f"{table}: {int(nulls.sum())} row(s) with null key in {keys}")
    dup = df.duplicated(keys, keep=False)
    if dup.any():
        ex = df.loc[dup, keys].drop_duplicates().head(max_examples).to_dict("records")
        n = int(df.duplicated(keys).sum())
        raise DuplicateKeyError(f"{table}: {n} duplicated key(s) on {keys}; e.g. {ex}")


def _is_null_scalar(v: object) -> bool:
    return v is None or v is pd.NA or v is pd.NaT or (isinstance(v, float) and np.isnan(v))


def _coerce_string(s: pd.Series, where: str) -> pd.Series:
    if isinstance(s.dtype, pd.StringDtype):
        return s.astype("string")
    if s.dtype == object or pd.api.types.is_string_dtype(s.dtype):
        bad = s.map(lambda v: not (_is_null_scalar(v) or isinstance(v, str)))
        if bad.any():
            raise SchemaError(f"{where}: {int(bad.sum())} non-string value(s), e.g. "
                              f"{s[bad].iloc[0]!r}")
        return s.astype("string")
    if s.isna().all():
        return s.astype("string")
    raise SchemaError(f"{where}: expected strings, got dtype {s.dtype}")


def _coerce_date(s: pd.Series, where: str) -> pd.Series:
    if isinstance(s.dtype, pd.DatetimeTZDtype):
        raise SchemaError(
            f"{where}: timezone-aware timestamps; convert to station-day labels with the "
            "declared day definition before building a canonical table"
        )
    if pd.api.types.is_datetime64_dtype(s.dtype):
        out = s.astype("datetime64[ns]")
    elif s.isna().all():
        out = pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns]")
    elif s.dtype == object or pd.api.types.is_string_dtype(s.dtype):
        nonnull = s.dropna()
        if nonnull.map(lambda v: isinstance(v, pd.Timestamp) and v.tzinfo is not None).any():
            raise SchemaError(f"{where}: timezone-aware timestamps")
        if nonnull.map(lambda v: isinstance(v, str)).all():
            try:
                out = pd.to_datetime(s, format="%Y-%m-%d")
            except (ValueError, TypeError) as exc:
                raise SchemaError(f"{where}: date strings must be ISO YYYY-MM-DD: {exc}") from exc
        else:
            import datetime as _dt

            if not nonnull.map(lambda v: isinstance(v, _dt.date)).all():
                raise SchemaError(f"{where}: values are not dates")
            out = pd.to_datetime(s)
        out = out.astype("datetime64[ns]")
    else:
        raise SchemaError(f"{where}: expected dates, got dtype {s.dtype}")
    has_time = out.notna() & (out != out.dt.normalize())
    if has_time.any():
        raise SchemaError(
            f"{where}: {int(has_time.sum())} value(s) have a time of day; a station-day label "
            "must be midnight (dates are never truncated or shifted)"
        )
    return out


def _coerce_float(s: pd.Series, where: str) -> pd.Series:
    if pd.api.types.is_bool_dtype(s.dtype):
        raise SchemaError(f"{where}: expected numbers, got booleans")
    if pd.api.types.is_numeric_dtype(s.dtype):
        return pd.Series(s.to_numpy(dtype="float64", na_value=np.nan), index=s.index)
    if s.isna().all():
        return pd.Series(np.nan, index=s.index, dtype="float64")
    raise SchemaError(f"{where}: expected numbers, got dtype {s.dtype}")


def _coerce_int(s: pd.Series, where: str) -> pd.Series:
    if pd.api.types.is_bool_dtype(s.dtype):
        raise SchemaError(f"{where}: expected integers, got booleans")
    if pd.api.types.is_integer_dtype(s.dtype):
        return s.astype("Int64")
    if pd.api.types.is_float_dtype(s.dtype) or s.isna().all():
        f = s.astype("float64")
        nonint = f.notna() & (f != np.round(f))
        if nonint.any():
            raise SchemaError(f"{where}: {int(nonint.sum())} non-integer value(s)")
        return f.astype("Int64")
    raise SchemaError(f"{where}: expected integers, got dtype {s.dtype}")


def _coerce_bool(s: pd.Series, where: str) -> pd.Series:
    if pd.api.types.is_bool_dtype(s.dtype):
        return s.astype("boolean")
    if s.dtype == object or s.isna().all():
        bad = s.map(lambda v: not (_is_null_scalar(v) or isinstance(v, bool | np.bool_)))
        if bad.any():
            raise SchemaError(f"{where}: {int(bad.sum())} non-boolean value(s), e.g. "
                              f"{s[bad].iloc[0]!r}")
        return s.astype("boolean")
    raise SchemaError(f"{where}: expected booleans, got dtype {s.dtype}")


_COERCE = {"string": _coerce_string, "date": _coerce_date, "float": _coerce_float,
           "int": _coerce_int, "bool": _coerce_bool}


def conform(df: pd.DataFrame, schema: TableSchema, *, source: str | None = None) -> pd.DataFrame:
    """Validate ``df`` against ``schema``; return a copy with canonical dtypes and order.

    Raises :class:`SchemaError` for missing/extra columns, uncoercible values,
    nulls in non-nullable columns or values outside ``allowed``, and
    :class:`DuplicateKeyError` for duplicated or null primary keys.
    """
    label = f"{schema.name}" + (f" ({source})" if source else "")
    missing = [c.name for c in schema.columns if c.required and c.name not in df.columns]
    if missing:
        raise SchemaError(f"{label}: missing required column(s) {missing}")
    extra = [c for c in df.columns if c not in schema]
    if extra and not schema.allow_extra_columns:
        raise SchemaError(f"{label}: unexpected column(s) {extra}")
    if df.columns.duplicated().any():
        raise SchemaError(f"{label}: duplicated column names "
                          f"{list(df.columns[df.columns.duplicated()])}")

    out: dict[str, pd.Series] = {}
    index = pd.RangeIndex(len(df))
    for col in schema.columns:
        where = f"{label}.{col.name}"
        if col.name in df.columns:
            s = df[col.name].reset_index(drop=True)
        else:
            s = pd.Series([None] * len(df), index=index, dtype=object)
        s = _COERCE[col.kind](s, where)
        if not col.nullable and s.isna().any():
            raise SchemaError(f"{where}: {int(s.isna().sum())} null value(s) in a non-nullable "
                              "column")
        if col.allowed is not None:
            bad = s.notna() & ~s.isin(col.allowed)
            if bad.any():
                raise SchemaError(f"{where}: value(s) {sorted(set(s[bad]))} not in "
                                  f"{list(col.allowed)}")
        out[col.name] = s
    for c in extra:
        out[c] = df[c].reset_index(drop=True)
    result = pd.DataFrame(out, index=index)
    if schema.primary_key:
        check_primary_key(result, schema.primary_key, table=label)
    return result


@dataclass(frozen=True)
class CanonicalTable:
    """A conformed frame plus its schema and JSON-serializable attributes.

    ``attributes`` records provenance (source path, checksum, declarations);
    it travels with the table through Parquet round trips.
    """

    schema: TableSchema
    frame: pd.DataFrame
    attributes: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def build(
        cls, frame: pd.DataFrame, schema: TableSchema, attributes: Mapping[str, Any] | None = None,
        *, source: str | None = None,
    ) -> CanonicalTable:
        return cls(schema, conform(frame, schema, source=source), dict(attributes or {}))

    @property
    def units(self) -> dict[str, str]:
        return self.schema.units

    def __len__(self) -> int:
        return len(self.frame)
