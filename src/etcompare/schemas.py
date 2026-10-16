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

from etcompare.config import (
    DIRECTIONS,
    FOOTPRINT_VALUE_SEMANTICS,
    INTERVAL_LABELS,
    INTERVAL_METHODS,
    MULTIPLICITY_POLICIES,
    NETWORK_SUMMARY_TYPES,
    PERIOD_TYPES,
    PRODUCT_DAY_DEFINITIONS,
    STATION_DAY_DEFINITIONS,
    TEMPORAL_SUPPORTS,
)
from etcompare.config import ET_TOTAL_UNIT as ET_TOTAL_UNIT
from etcompare.config import ET_UNIT as ET_UNIT
from etcompare.config import REFERENCE_VARIANTS as REFERENCE_VARIANTS
from etcompare.config import TOTAL_PRODUCTS as TOTAL_PRODUCTS
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
DAY_ALIGNMENT: Final = ("declared", "unverified")
PERIODS: Final = ("day", "month")
SEVERITY: Final = ("error", "warning", "info")
SCALING_SOURCES: Final = ("declared", "from_header", "not_read")
#: Where a daily estimate comes from: a real cohort pair, an imputation, or nothing.
VALUE_SOURCES: Final = ("cohort", "imputed", "not_imputed")
#: Metric rows: daily pairs or period totals (``etcompare.metrics``).
TEMPORAL_SCALES: Final = ("daily", "period")
METRIC_PRODUCTS: Final = ("daily_pairs", *TOTAL_PRODUCTS)
#: score: may support rankings; diagnostic: never a ranking basis (bias-corrected RMSE,
#: MAPE); descriptive: regression coefficients; component: parts of a composite (KGE).
METRIC_ROLES: Final = ("score", "diagnostic", "descriptive", "component")
RANKING_SCOPES: Final = ("all_models", "pairwise", "none")
#: Bootstrap intervals (``etcompare.bootstrap``).
BLOCK_UNITS: Final = ("site_day", "period")
CONTRAST_DIMENSIONS: Final = ("none", "extraction_side", "reference_variant", "model")
INTERVAL_STATUSES: Final = ("ok", "insufficient_support", "estimate_undefined",
                            "insufficient_valid_replicates")
#: Rankings and model contrasts (``etcompare.ranking``). ``not_resampled``: no bootstrap
#: block length applies, so only the point estimate exists.
RANKING_STATUSES: Final = (*INTERVAL_STATUSES, "not_resampled")
LEADER_STATUSES: Final = ("unique_leader", "no_unique_leader", "not_assessed")
CONTRAST_BASES: Final = ("value", "absolute_value")
INTERVAL_POSITIONS: Final = ("above_zero", "below_zero", "includes_zero")
EQUIVALENCE_RESULTS: Final = ("not_assessed", "not_applicable", "no_interval", "equivalent",
                              "practically_different", "inconclusive")
INFERENCE_LABELS: Final = ("exploratory", "formal")
TEST_DECISIONS: Final = ("reject_zero", "not_rejected")
NETWORK_STATUSES: Final = ("ok", "single_site", "estimate_undefined")
SENSITIVITY_DIMENSIONS: Final = ("station", "year", "temporal_scale", "reference_variant",
                                 "extraction_method", "cohort")
#: Closure, coverage and environmental diagnostics (``etcompare.diagnostics``).
CLOSURE_DAY_SETS: Final = ("station_record", "cohort")
DIAGNOSTIC_PERIODS: Final = ("all", "month", "season")
CLOSURE_STATUSES: Final = ("ok", "no_days")
BIN_KINDS: Final = ("interval", "category", "sector", "outside_edges", "variable_missing",
                    "excluded_provenance")
BIN_SOURCES: Final = ("fixed_edges", "training_quantiles", "calendar", "sectors", "provenance")
#: ok: a value; sparse_bin / empty_bin: fewer than min_bin_days / no days; outside_edges,
#: variable_missing, excluded_provenance: days accounted for but not binned;
#: estimate_undefined: the metric is undefined on the bin sample (reason in status_detail).
BIN_STATUSES: Final = ("ok", "sparse_bin", "empty_bin", "outside_edges", "variable_missing",
                       "excluded_provenance", "estimate_undefined")
BIN_INTERVAL_STATUSES: Final = (*INTERVAL_STATUSES, "not_resampled", "no_estimate")
AVAILABILITY_STATUSES: Final = ("available", "not_available")
REGRESSION_STATUSES: Final = ("ok", "estimate_undefined")
RANK_CHANGE_EFFECTS: Final = ("closure", "closure_and_cohort_days", "gap_filling_and_cohort_days",
                              "closure_and_gap_filling", "other")


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
        _c("reference_et_mm", "float",
           "Daily reference ET (declared column, e.g. gridMET ETr); used only to impute",
           unit=ET_UNIT, required=False),
    ),
    primary_key=("site_id", "date"),
    allow_extra_columns=True,
)

CROP_HEIGHT = TableSchema(
    "crop_height", "1", "Daily crop height and the crop model's growing season.",
    (
        _key("site_id", "string", "Station"),
        _key("date", "date", "Station-day label"),
        _c("height_cm", "float", "Crop height", unit="cm"),
        _c("season_start", "date", "First day of this year's growing season", nullable=False),
        _c("season_end", "date", "Last day of this year's growing season", nullable=False),
        _c("in_season", "bool", "season_start <= date <= season_end", nullable=False),
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

COHORT_MEMBERSHIP = TableSchema(
    "cohort_membership", "1",
    "Every candidate site/date/model of a cohort with its inclusion decision and reason.",
    (
        _key("comparison_id", "string", "Comparison identifier"),
        _key("cohort_id", "string", "Cohort identifier"),
        _key("site_id", "string", "Station"),
        _key("date", "date", "Station-day label"),
        _key("model", "string", "Canonical model name"),
        _c("included", "bool", "Unit belongs to the cohort", nullable=False),
        _c("reason_code", "string", "Stable reason for exclusion; null when included"),
        _c("detail", "string", "Every failing side/reference, or the models that failed"),
    ),
    primary_key=("comparison_id", "cohort_id", "site_id", "date", "model"),
)

PERIOD_TOTALS = TableSchema(
    "period_totals", "1",
    "Period sums of a cohort's daily depths: matched-day partial sums, complete-calendar "
    "totals and optional estimated totals, with calendar completeness.",
    (
        _key("comparison_id", "string", "Comparison identifier"),
        _key("cohort_id", "string", "Cohort identifier"),
        _key("site_id", "string", "Station"),
        _key("model", "string", "Canonical model name"),
        _key("reference_variant", "string", "Reference variant", allowed=REFERENCE_VARIANTS),
        _key("extraction_method", "string", "Extraction method (daily values summed)"),
        _key("footprint_set_id", "string", "Footprint set of the extraction (or 'none')"),
        _key("period_type", "string", "Kind of period", allowed=PERIOD_TYPES),
        _key("period_label", "string", "e.g. 2025-06, 2025, WY2025, GS2025"),
        _key("product", "string", "Which total this row is", allowed=TOTAL_PRODUCTS),
        _c("period_start", "date", "First station day of the period", nullable=False),
        _c("period_end", "date", "Last station day of the period (inclusive)", nullable=False),
        _c("observed_total_mm", "float", "Station reference ET summed over the product's days; "
           "null when the product is unavailable", unit=ET_TOTAL_UNIT),
        _c("modeled_total_mm", "float", "Model ET summed over the same days", unit=ET_TOTAL_UNIT),
        _c("n_days_expected", "int", "Calendar station days in the period", nullable=False),
        _c("n_days_matched", "int", "Cohort days in the period (both sides accepted)",
           nullable=False),
        _c("n_days_matched_reference_filled", "int",
           "Matched days whose reference is gap-filled (corrected_filled)", nullable=False),
        _c("n_days_model_available", "int",
           "Days with a non-null model value on this side, before cohort rules", nullable=False),
        _c("n_days_reference_measured", "int",
           "Days with a reference value of this variant that is not gap-filled", nullable=False),
        _c("n_days_reference_filled", "int",
           "Days with a gap-filled reference value of this variant", nullable=False),
        _c("period_completeness", "float", "n_days_matched / n_days_expected", unit=FRACTION,
           nullable=False),
        _c("temporal_support", "string", "Declared temporal support of the model product",
           nullable=False, allowed=TEMPORAL_SUPPORTS),
        _c("estimation_method", "string", "Estimator of an estimated_total row"),
        _c("n_days_imputed", "int", "Imputed days in an estimated_total row (null otherwise, "
           "and for mean_daily_scaling)", required=False),
        _c("validity_reason", "string", "Why the totals are null, if they are"),
        _c("flags", "string", "';'-joined flags (daily flags of the period plus period flags)"),
    ),
    primary_key=("comparison_id", "cohort_id", "site_id", "model", "reference_variant",
                 "extraction_method", "footprint_set_id", "period_type", "period_label",
                 "product"),
)

DAILY_ESTIMATES = TableSchema(
    "daily_estimates", "1",
    "A cohort's daily pairs on every calendar day of the aggregated periods. Original "
    "values are kept; imputed values appear only in the *_estimated_mm columns.",
    (
        _key("comparison_id", "string", "Comparison identifier"),
        _key("cohort_id", "string", "Cohort identifier"),
        _key("site_id", "string", "Station"),
        _key("date", "date", "Station-day label"),
        _key("model", "string", "Canonical model name"),
        _key("reference_variant", "string", "Reference variant", allowed=REFERENCE_VARIANTS),
        _key("extraction_method", "string", "Extraction method"),
        _key("footprint_set_id", "string", "Footprint set of the extraction (or 'none')"),
        _c("observed_mm", "float", "Cohort reference ET; null on days outside the cohort",
           unit=ET_UNIT),
        _c("modeled_mm", "float", "Cohort model ET; null on days outside the cohort",
           unit=ET_UNIT),
        _c("observed_estimated_mm", "float", "observed_mm, or the imputed value",
           unit=ET_UNIT),
        _c("modeled_estimated_mm", "float", "modeled_mm, or the imputed value", unit=ET_UNIT),
        _c("value_source", "string", "cohort / imputed / not_imputed (both sides alike)",
           nullable=False, allowed=VALUE_SOURCES),
        _c("gap_length_days", "int", "Length of the run of non-cohort days containing this "
           "day (null on cohort days)"),
        _c("reference_et_mm", "float", "Reference ET used by reference_et_fraction",
           unit=ET_UNIT),
        _c("estimation_method", "string", "Imputation method", nullable=False),
    ),
    primary_key=("comparison_id", "cohort_id", "site_id", "date", "model", "reference_variant",
                 "extraction_method", "footprint_set_id"),
)

METRICS = TableSchema(
    "metrics", "2",
    "Performance metrics of one cohort on its common mask, with explicit sample counts and "
    "validity. Daily pairs and each period product are separate rows, never pooled.",
    (
        _key("comparison_id", "string", "Comparison identifier"),
        _key("cohort_id", "string", "Cohort identifier"),
        _key("metric_set_id", "string", "Declared metric set (tolerances, metric list)"),
        _key("scope", "string", "Site ID or group label the metric summarizes"),
        _c("scope_type", "string", "'site' or 'group'", nullable=False,
           allowed=("site", "group")),
        _key("temporal_scale", "string", "daily pairs or period totals",
             allowed=TEMPORAL_SCALES),
        _key("period_type", "string", "Period type of period totals; 'none' for daily",
             allowed=("none", *PERIOD_TYPES)),
        _key("aggregation_id", "string", "Period aggregation scored; 'none' for daily"),
        _key("product", "string", "daily_pairs, or the period product scored",
             allowed=METRIC_PRODUCTS),
        _key("model", "string", "Canonical model name"),
        _key("reference_variant", "string", "Reference variant", allowed=REFERENCE_VARIANTS),
        _key("extraction_method", "string", "Extraction method"),
        _key("footprint_set_id", "string", "Footprint set of the extraction (or 'none')"),
        _key("metric", "string", "Stable metric identifier (etcompare.metrics)"),
        _c("value", "float", "Metric value; null when undefined"),
        _c("units", "string", "Units of value", nullable=False),
        _c("valid", "bool", "value is defined", nullable=False),
        _c("validity_reason", "string", "Why value is null, if it is"),
        _c("metric_role", "string", "score / diagnostic / descriptive / component",
           nullable=False, allowed=METRIC_ROLES),
        _c("ranking_scope", "string", "Rankings the cohort supports (all_models / pairwise / "
           "none)", nullable=False, allowed=RANKING_SCOPES),
        _c("n_sites", "int", "Sites contributing", nullable=False),
        _c("n_days", "int", "Days contributing (matched days for period totals)",
           nullable=False),
        _c("n_days_reference_filled", "int", "Contributing days with gap-filled reference",
           nullable=False),
        _c("n_days_imputed", "int", "Imputed days inside scored estimated totals"),
        _c("n_periods", "int", "Periods contributing (period totals only)"),
        _c("period_completeness", "float", "Matched / expected days over the scored periods",
           unit=FRACTION),
        _c("temporal_support", "string", "Declared temporal support of the model product",
           allowed=TEMPORAL_SUPPORTS),
        _c("flags", "string", "';'-joined flags of the scored rows"),
    ),
    primary_key=("comparison_id", "cohort_id", "metric_set_id", "scope", "temporal_scale",
                 "period_type", "aggregation_id", "product", "model", "reference_variant",
                 "extraction_method", "footprint_set_id", "metric"),
)

CUMULATIVE_ERROR = TableSchema(
    "cumulative_error", "1",
    "Running sums of a cohort's paired daily values and errors (e = modeled - observed) over "
    "cohort days only; days outside the cohort are never filled, and the first day after a "
    "run of missing days is flagged.",
    (
        _key("comparison_id", "string", "Comparison identifier"),
        _key("cohort_id", "string", "Cohort identifier"),
        _key("site_id", "string", "Station"),
        _key("model", "string", "Canonical model name"),
        _key("reference_variant", "string", "Reference variant", allowed=REFERENCE_VARIANTS),
        _key("extraction_method", "string", "Extraction method"),
        _key("footprint_set_id", "string", "Footprint set of the extraction (or 'none')"),
        _key("date", "date", "Station-day label"),
        _c("observed_mm", "float", "Station reference ET", unit=ET_UNIT, nullable=False),
        _c("modeled_mm", "float", "Model ET", unit=ET_UNIT, nullable=False),
        _c("error_mm", "float", "modeled_mm - observed_mm", unit=ET_UNIT, nullable=False),
        _c("cumulative_observed_mm", "float", "Sum of observed_mm over cohort days so far",
           unit=ET_TOTAL_UNIT, nullable=False),
        _c("cumulative_modeled_mm", "float", "Sum of modeled_mm over cohort days so far",
           unit=ET_TOTAL_UNIT, nullable=False),
        _c("cumulative_error_mm", "float", "Sum of error_mm over cohort days so far",
           unit=ET_TOTAL_UNIT, nullable=False),
        _c("n_days_cumulated", "int", "Cohort days summed so far", nullable=False),
        _c("missing_days_before", "int", "Calendar days missing since the previous cohort "
           "day (0 when consecutive; null on the first day)"),
        _c("segment", "int", "Uninterrupted run number, starting at 1; increments after "
           "every missing period", nullable=False),
        _c("flags", "string", "';'-joined flags; missing_period_before marks a gap"),
    ),
    primary_key=("comparison_id", "cohort_id", "site_id", "model", "reference_variant",
                 "extraction_method", "footprint_set_id", "date"),
)

INTERVALS = TableSchema(
    "intervals", "2",
    "Paired block-bootstrap intervals of station-level metrics and paired contrasts of one "
    "cohort. One row per target, metric, block length and interval method; null bounds carry "
    "a status. Intervals are sampling intervals for one station, not rankings.",
    (
        _key("comparison_id", "string", "Comparison identifier"),
        _key("cohort_id", "string", "Cohort identifier"),
        _key("bootstrap_id", "string", "Declared bootstrap settings"),
        _key("metric_set_id", "string", "Metric set whose metrics are resampled"),
        _key("scope", "string", "Site ID the interval refers to (station level only)"),
        _c("scope_type", "string", "'site' (network summaries are step 11)", nullable=False,
           allowed=("site",)),
        _key("temporal_scale", "string", "daily pairs or period totals", allowed=TEMPORAL_SCALES),
        _key("period_type", "string", "Period type of period totals; 'none' for daily",
             allowed=("none", *PERIOD_TYPES)),
        _key("aggregation_id", "string", "Period aggregation; 'none' for daily"),
        _key("product", "string", "daily_pairs, or the period product", allowed=METRIC_PRODUCTS),
        _key("model", "string", "Model (the minuend of a contrast)"),
        _key("reference_variant", "string", "Reference variant (minuend of a contrast)",
             allowed=REFERENCE_VARIANTS),
        _key("extraction_method", "string", "Extraction method (minuend of a contrast)"),
        _key("footprint_set_id", "string", "Footprint set (minuend of a contrast) or 'none'"),
        _key("contrast_dimension", "string", "'none' for a metric; otherwise the dimension in "
             "which the contrast's two sides differ", allowed=CONTRAST_DIMENSIONS),
        _key("contrast_baseline", "string", "'none', or the subtrahend: a side label "
             "(method@footprint_set), reference variant or model"),
        _key("metric", "string", "Stable metric identifier (etcompare.metrics)"),
        _key("interval_method", "string", "percentile or basic", allowed=INTERVAL_METHODS),
        _key("block_unit", "string", "Resampling unit: site_day (daily pairs) or period",
             allowed=BLOCK_UNITS),
        _key("block_length", "int", "Nominal block length in block units (1 = iid units)"),
        _c("estimate", "float", "Step-9 point estimate on the cohort (for a contrast: "
           "metric(target) - metric(baseline)); null when undefined"),
        _c("lower", "float", "Lower bound; null unless status is ok"),
        _c("upper", "float", "Upper bound; null unless status is ok"),
        _c("units", "string", "Units of estimate and bounds", nullable=False),
        _c("confidence_level", "float", "Nominal two-sided level", unit=FRACTION,
           nullable=False),
        _c("seed", "int", "Declared random seed", nullable=False),
        _c("n_replicates_requested", "int", "Requested replicates", nullable=False),
        _c("n_replicates_valid", "int", "Replicates with a defined statistic", nullable=False),
        _c("n_segments", "int", "Independent uninterrupted segments of the resampled sample",
           nullable=False),
        _c("n_blocks", "int", "Disjoint full-length blocks the segments hold (the support "
           "compared with min_blocks)", nullable=False),
        _c("n_days", "int", "Days in the scored sample (matched days for period totals)",
           nullable=False),
        _c("n_periods", "int", "Periods in the scored sample (period totals only)"),
        _c("status", "string", "ok, or why no interval is given", nullable=False,
           allowed=INTERVAL_STATUSES),
        _c("status_detail", "string", "Reason behind a non-ok status"),
        _c("metric_role", "string", "Role of the metric (etcompare.metrics)", nullable=False,
           allowed=METRIC_ROLES),
        _c("ranking_scope", "string", "Rankings the cohort supports", nullable=False,
           allowed=RANKING_SCOPES),
        _c("flags", "string", "';'-joined flags (metric flags plus resampling flags)"),
    ),
    primary_key=("comparison_id", "cohort_id", "bootstrap_id", "metric_set_id", "scope",
                 "temporal_scale", "period_type", "aggregation_id", "product", "model",
                 "reference_variant", "extraction_method", "footprint_set_id",
                 "contrast_dimension", "contrast_baseline", "metric", "interval_method",
                 "block_unit", "block_length"),
)

RESIDUAL_AUTOCORRELATION = TableSchema(
    "residual_autocorrelation", "1",
    "Autocorrelation of daily errors e = modeled - observed of one cohort, at exact calendar "
    "lags (pairs of cohort days k days apart; missing days are never compressed). A "
    "diagnostic for choosing bootstrap block lengths, not a selection rule.",
    (
        _key("comparison_id", "string", "Comparison identifier"),
        _key("cohort_id", "string", "Cohort identifier"),
        _key("site_id", "string", "Station"),
        _key("model", "string", "Canonical model name"),
        _key("reference_variant", "string", "Reference variant", allowed=REFERENCE_VARIANTS),
        _key("extraction_method", "string", "Extraction method"),
        _key("footprint_set_id", "string", "Footprint set of the extraction (or 'none')"),
        _key("lag", "int", "Calendar lag in days"),
        _c("n_days", "int", "Cohort days of the series", nullable=False),
        _c("n_pairs", "int", "Pairs of cohort days exactly lag days apart", nullable=False),
        _c("autocorrelation", "float", "mean over pairs of d_t*d_(t+lag) / mean(d**2), "
           "d = e - mean(e); null without pairs or with constant errors", unit=FRACTION),
        _c("white_noise_bound", "float", "1.96 / sqrt(n_pairs): approximate 95% bound for an "
           "uncorrelated series", unit=FRACTION),
    ),
    primary_key=("comparison_id", "cohort_id", "site_id", "model", "reference_variant",
                 "extraction_method", "footprint_set_id", "lag"),
)

def _ranking_context(*, scope: bool = True) -> tuple[ColumnSpec, ...]:
    """Key columns that fix one frozen ranking context (shared by the step-11 tables)."""
    cols = [
        _key("ranking_id", "string", "Declared ranking (rankings[] in the config)"),
        _key("comparison_id", "string", "Comparison identifier"),
        _key("cohort_id", "string", "Cohort identifier"),
        _key("subset", "string", "'all', or the frozen sub-cohort ranked (e.g. 'year=2025')"),
        _key("metric_set_id", "string", "Metric set of the point estimates"),
        _key("bootstrap_id", "string", "Bootstrap whose replicates are used ('none' if none)"),
    ]
    if scope:
        cols.append(_key("scope", "string", "Site ID (station level)"))
    cols += [
        _key("temporal_scale", "string", "daily pairs or period totals", allowed=TEMPORAL_SCALES),
        _key("period_type", "string", "Period type of period totals; 'none' for daily",
             allowed=("none", *PERIOD_TYPES)),
        _key("aggregation_id", "string", "Period aggregation; 'none' for daily"),
        _key("product", "string", "daily_pairs, or the period product", allowed=METRIC_PRODUCTS),
        _key("reference_variant", "string", "Reference variant", allowed=REFERENCE_VARIANTS),
        _key("extraction_method", "string", "Extraction method"),
        _key("footprint_set_id", "string", "Footprint set of the extraction (or 'none')"),
    ]
    return tuple(cols)


_RESAMPLING = (
    _key("block_unit", "string", "Resampling unit: site_day (daily pairs) or period",
         allowed=BLOCK_UNITS),
    _key("block_length", "int", "Bootstrap block length in block units; 0 = not resampled"),
)
_CONTEXT_KEY = ("ranking_id", "comparison_id", "cohort_id", "subset", "metric_set_id",
                "bootstrap_id", "scope", "temporal_scale", "period_type", "aggregation_id",
                "product", "reference_variant", "extraction_method", "footprint_set_id")

MODEL_RANKINGS = TableSchema(
    "model_rankings", "1",
    "Station-level ranks of the models of one frozen ranking cohort, per context, metric and "
    "bootstrap block length, with rank stability from shared-index replicates. Ranks are "
    "descriptive; top_rank_frequency is a bootstrap stability measure, not the probability "
    "that a model is best.",
    (
        *_ranking_context(),
        _key("metric", "string", "Ranked metric (role score)"),
        _key("model", "string", "Canonical model name"),
        *_RESAMPLING,
        _c("scope_type", "string", "'site' (station level)", nullable=False, allowed=("site",)),
        _c("direction", "string", "Better direction of the metric (RANKING_DIRECTIONS)",
           nullable=False, allowed=DIRECTIONS),
        _c("value", "float", "Step-9 point estimate; null when undefined"),
        _c("units", "string", "Units of value", nullable=False),
        _c("rank", "float", "Point mid-rank (1 = best; tied models share the average of "
           "their positions); null unless every model's estimate is defined"),
        _c("n_models", "int", "Models ranked in the context", nullable=False),
        _c("n_tied", "int", "Models sharing this model's point value (including itself)"),
        _c("mean_rank", "float", "Mean replicate mid-rank; null unless status is ok"),
        _c("rank_lower", "float", "Lower rank bound: lower quantile of replicate mid-ranks"),
        _c("rank_upper", "float", "Upper rank bound: higher quantile of replicate mid-ranks"),
        _c("top_rank_frequency", "float", "Share of valid replicates in which the model holds "
           "position 1 (ties split it); a bootstrap stability measure", unit=FRACTION),
        _c("confidence_level", "float", "Level of the rank interval and of contrast "
           "intervals", unit=FRACTION),
        _c("leader_status", "string", "unique_leader only when the point-best model's "
           "contrast interval excludes zero in its favour against every other model",
           nullable=False, allowed=LEADER_STATUSES),
        _c("leading_models", "string", "';'-joined point-best model(s) and every model not "
           "separated from it by its contrast interval"),
        _c("leader_interval_method", "string", "Interval method used for leader_status",
           allowed=INTERVAL_METHODS),
        _c("seed", "int", "Bootstrap seed"),
        _c("n_replicates_requested", "int", "Requested replicates", nullable=False),
        _c("n_replicates_valid", "int", "Replicates in which every model's metric is defined",
           nullable=False),
        _c("n_sites", "int", "Sites in the context (1: station level)", nullable=False),
        _c("n_days", "int", "Days in the scored sample (matched days for period totals)",
           nullable=False),
        _c("n_days_reference_filled", "int", "Scored days with a gap-filled reference",
           nullable=False),
        _c("n_periods", "int", "Periods in the scored sample (period totals only)"),
        _c("n_segments", "int", "Uninterrupted segments of the resampled sample"),
        _c("n_blocks", "int", "Disjoint full-length blocks (the support compared with "
           "min_blocks)"),
        _c("status", "string", "ok, or why rank statistics are missing", nullable=False,
           allowed=RANKING_STATUSES),
        _c("status_detail", "string", "Reason behind a non-ok status"),
        _c("ranking_scope", "string", "all_models or pairwise", nullable=False,
           allowed=("all_models", "pairwise")),
        _c("cohort_membership_sha256", "string", "Cohort fingerprint", nullable=False),
        _c("flags", "string", "';'-joined flags of the ranked metric rows"),
    ),
    primary_key=(*_CONTEXT_KEY, "metric", "model", "block_unit", "block_length"),
)

RANK_DISTRIBUTION = TableSchema(
    "rank_distribution", "1",
    "Per-model distribution of rank positions over the valid bootstrap replicates of a "
    "model_rankings row with status ok. Tied models share their positions equally, so each "
    "model's frequencies and each position's frequencies sum to 1.",
    (
        *_ranking_context(),
        _key("metric", "string", "Ranked metric"),
        _key("model", "string", "Canonical model name"),
        *_RESAMPLING,
        _key("position", "int", "Rank position (1 = best)"),
        _c("frequency", "float", "Share of valid replicates at this position", unit=FRACTION,
           nullable=False),
        _c("n_replicates_valid", "int", "Valid replicates", nullable=False),
    ),
    primary_key=(*_CONTEXT_KEY, "metric", "model", "block_unit", "block_length", "position"),
)

MODEL_CONTRASTS = TableSchema(
    "model_contrasts", "1",
    "Paired model contrasts metric(model_a) - metric(model_b) inside one frozen ranking "
    "context, computed replicate by replicate on shared bootstrap indices, with practical "
    "equivalence and (optional) formal tests. model_b is the model declared earlier in the "
    "comparison.",
    (
        *_ranking_context(),
        _key("metric", "string", "Contrasted metric"),
        _key("model_a", "string", "Minuend model"),
        _key("model_b", "string", "Subtrahend (baseline) model, declared earlier"),
        _key("contrast_basis", "string", "value: metric_a - metric_b; absolute_value: "
             "|metric_a| - |metric_b| (closest_to_zero metrics)", allowed=CONTRAST_BASES),
        _key("interval_method", "string", "percentile or basic", allowed=INTERVAL_METHODS),
        *_RESAMPLING,
        _c("scope_type", "string", "'site' (station level)", nullable=False, allowed=("site",)),
        _c("direction", "string", "Better direction of the metric", nullable=False,
           allowed=DIRECTIONS),
        _c("ranking_basis", "bool", "This basis orders the models (false for the signed "
           "difference of a closest_to_zero metric)", nullable=False),
        _c("sign_meaning", "string", "How to read the sign of estimate", nullable=False),
        _c("estimate", "float", "Point contrast (practical effect size, in units); null when "
           "undefined"),
        _c("lower", "float", "Lower bound; null unless status is ok"),
        _c("upper", "float", "Upper bound; null unless status is ok"),
        _c("units", "string", "Units of estimate, bounds and margin", nullable=False),
        _c("interval_position", "string", "Where the interval lies relative to zero",
           allowed=INTERVAL_POSITIONS),
        _c("favoured_model", "string", "Model favoured by an interval excluding zero (ranking "
           "basis only); null otherwise"),
        _c("equivalence_margin", "float", "Predeclared practical-equivalence margin (+/-)"),
        _c("equivalence", "string", "Interval inside (-margin, margin): equivalent; entirely "
           "beyond it: practically_different; an interval crossing zero is not equivalence",
           nullable=False, allowed=EQUIVALENCE_RESULTS),
        _c("confidence_level", "float", "Nominal two-sided level", unit=FRACTION),
        _c("seed", "int", "Bootstrap seed"),
        _c("n_replicates_requested", "int", "Requested replicates", nullable=False),
        _c("n_replicates_valid", "int", "Replicates with both metrics defined", nullable=False),
        _c("n_sites", "int", "Sites (1: station level)", nullable=False),
        _c("n_days", "int", "Days in the scored sample", nullable=False),
        _c("n_periods", "int", "Periods in the scored sample (period totals only)"),
        _c("n_segments", "int", "Uninterrupted segments of the resampled sample"),
        _c("n_blocks", "int", "Disjoint full-length blocks"),
        _c("status", "string", "ok, or why no interval is given", nullable=False,
           allowed=RANKING_STATUSES),
        _c("status_detail", "string", "Reason behind a non-ok status"),
        _c("inference", "string", "formal only for a tested ranking-basis contrast under a "
           "declared multiplicity policy; everything else is exploratory", nullable=False,
           allowed=INFERENCE_LABELS),
        _c("p_value", "float", "Percentile-inversion bootstrap p-value (formal tests only)",
           unit=FRACTION),
        _c("p_value_adjusted", "float", "Multiplicity-adjusted p-value", unit=FRACTION),
        _c("multiplicity_policy", "string", "Declared policy (formal tests only)",
           allowed=MULTIPLICITY_POLICIES),
        _c("family_size", "int", "Contrasts in the multiplicity family"),
        _c("test_decision", "string", "reject_zero / not_rejected at 1 - confidence_level",
           allowed=TEST_DECISIONS),
        _c("ranking_scope", "string", "all_models or pairwise", nullable=False,
           allowed=("all_models", "pairwise")),
        _c("cohort_membership_sha256", "string", "Cohort fingerprint", nullable=False),
        _c("flags", "string", "';'-joined flags of both metric rows"),
    ),
    primary_key=(*_CONTEXT_KEY, "metric", "model_a", "model_b", "contrast_basis",
                 "interval_method", "block_unit", "block_length"),
)

NETWORK_SUMMARIES = TableSchema(
    "network_summaries", "1",
    "Summaries of station-level metrics across the sites of one frozen cohort. The "
    "equal-site mean is the primary network result; observation weightings are labelled "
    "alternatives, and pooled RMSE (over all site observations) is not the mean station "
    "RMSE. With n_sites = 1 a row is a single station, not network evidence. No interval: "
    "site-population inference needs hierarchical resampling (not implemented).",
    (
        *_ranking_context(scope=False),
        _key("model", "string", "Canonical model name"),
        _key("metric", "string", "Summarized metric"),
        _key("summary_type", "string", "How station metrics are combined",
             allowed=NETWORK_SUMMARY_TYPES),
        _c("scope_type", "string", "'network'", nullable=False, allowed=("network",)),
        _c("summary_label", "string", "Human-readable label of the summary", nullable=False),
        _c("primary", "bool", "True for the equal-site mean", nullable=False),
        _c("weighting", "string", "Site weight: equal, n_obs, sqrt_n_obs, or pooled",
           nullable=False, allowed=("equal", "n_obs", "sqrt_n_obs", "pooled")),
        _c("observation_unit", "string", "What n_obs counts: day (daily pairs) or period",
           nullable=False, allowed=("day", "period")),
        _c("value", "float", "Summary value; null unless every site's metric is defined"),
        _c("units", "string", "Units of value", nullable=False),
        _c("n_sites", "int", "Sites summarized", nullable=False),
        _c("sites", "string", "';'-joined site IDs", nullable=False),
        _c("n_days", "int", "Days over all sites", nullable=False),
        _c("n_days_reference_filled", "int", "Gap-filled reference days over all sites",
           nullable=False),
        _c("n_periods", "int", "Periods over all sites (period totals only)"),
        _c("network_evidence", "bool", "False when n_sites < 2", nullable=False),
        _c("status", "string", "ok, single_site or estimate_undefined", nullable=False,
           allowed=NETWORK_STATUSES),
        _c("status_detail", "string", "Reason behind a non-ok status"),
        _c("cohort_membership_sha256", "string", "Cohort fingerprint (if known)"),
        _c("flags", "string", "';'-joined flags"),
    ),
    primary_key=("ranking_id", "comparison_id", "cohort_id", "subset", "metric_set_id",
                 "bootstrap_id", "temporal_scale", "period_type", "aggregation_id", "product",
                 "reference_variant", "extraction_method", "footprint_set_id", "model",
                 "metric", "summary_type"),
)

RANKING_SENSITIVITY = TableSchema(
    "ranking_sensitivity", "1",
    "How point ranks change between two ranking contexts that differ in one dimension "
    "(station, year, temporal scale, reference variant, extraction method) or only in their "
    "frozen cohort. Each context is an internally frozen cohort; same_cohort says whether "
    "both use the same cohort (same days).",
    (
        _key("ranking_id", "string", "Declared ranking"),
        _key("dimension", "string", "The one dimension in which the contexts differ",
             allowed=SENSITIVITY_DIMENSIONS),
        _key("context_a", "string", "Context label: comparison|subset|scope|scale|variant|side"),
        _key("context_b", "string", "Context label of the other context"),
        _key("metric", "string", "Ranked metric"),
        _key("model", "string", "Canonical model name"),
        _c("rank_a", "float", "Point mid-rank in context_a", nullable=False),
        _c("rank_b", "float", "Point mid-rank in context_b", nullable=False),
        _c("rank_change", "float", "rank_b - rank_a (positive: worse in context_b)",
           nullable=False),
        _c("n_models", "int", "Models ranked in both contexts", nullable=False),
        _c("same_cohort", "bool", "Both contexts use one cohort fingerprint", nullable=False),
        _c("comparison_a", "string", "Comparison of context_a", nullable=False),
        _c("comparison_b", "string", "Comparison of context_b", nullable=False),
        _c("n_days_a", "int", "Days scored in context_a", nullable=False),
        _c("n_days_b", "int", "Days scored in context_b", nullable=False),
        _c("n_periods_a", "int", "Periods scored in context_a"),
        _c("n_periods_b", "int", "Periods scored in context_b"),
    ),
    primary_key=("ranking_id", "dimension", "context_a", "context_b", "metric", "model"),
)

_SERIES_KEY = (
    _key("model", "string", "Canonical model name"),
    _key("reference_variant", "string", "Reference variant", allowed=REFERENCE_VARIANTS),
    _key("extraction_method", "string", "Extraction method"),
    _key("footprint_set_id", "string", "Footprint set of the extraction (or 'none')"),
)

CLOSURE_MAGNITUDE = TableSchema(
    "closure_magnitude", "1",
    "Size of the existing energy-balance correction on corrected_from_raw days only (never "
    "recomputed): ET_corr - ET, ET_corr / ET and the closure factor, plus declared closure "
    "metadata, by period. Gap-filled days are excluded and counted. A correction magnitude is "
    "reference sensitivity, not a calibrated uncertainty envelope containing the truth.",
    (
        _key("diagnostics_id", "string", "Declared diagnostics (diagnostics[] in the config)"),
        _key("comparison_id", "string", "Comparison whose sites (and, for day_set cohort, "
             "days) are summarised"),
        _key("cohort_id", "string", "Cohort identifier; 'none' for the station record"),
        _key("site_id", "string", "Station"),
        _key("day_set", "string", "station_record: every corrected_from_raw station day of the "
             "comparison's period; cohort: those that are cohort days", allowed=CLOSURE_DAY_SETS),
        _key("period_type", "string", "all, month (YYYY-MM) or meteorological season "
             "(YYYY-DJF, December counted with the following year)", allowed=DIAGNOSTIC_PERIODS),
        _key("period_label", "string", "'all', 'YYYY-MM' or 'YYYY-SSS'"),
        _key("quantity", "string", "correction_mm (ET_corr - ET), correction_ratio "
             "(ET_corr / ET), closure_factor, or a declared closure-metadata variable"),
        _c("quantity_units", "string", "Units of the statistics", nullable=False),
        _c("quantity_definition", "string", "Definition or declared meaning", nullable=False),
        _c("n_days", "int", "corrected_from_raw days with a value", nullable=False),
        _c("n_days_filled_excluded", "int", "corrected_filled days in the period, excluded",
           nullable=False),
        _c("n_days_value_missing", "int", "corrected_from_raw days without a value (or with "
           "ET = 0 for the ratio)", nullable=False),
        _c("mean", "float", "Mean"),
        _c("std", "float", "Population standard deviation"),
        _c("min", "float", "Minimum"),
        _c("q05", "float", "5th percentile (numpy linear)"),
        _c("q25", "float", "25th percentile"),
        _c("median", "float", "Median"),
        _c("q75", "float", "75th percentile"),
        _c("q95", "float", "95th percentile"),
        _c("max", "float", "Maximum"),
        _c("status", "string", "ok or no_days", nullable=False, allowed=CLOSURE_STATUSES),
        _c("flags", "string", "';'-joined flags"),
    ),
    primary_key=("diagnostics_id", "comparison_id", "cohort_id", "site_id", "day_set",
                 "period_type", "period_label", "quantity"),
)

REFERENCE_PROVENANCE_PERIODS = TableSchema(
    "reference_provenance_periods", "1",
    "Reference provenance of a cohort's days per period: observation-dominated "
    "(corrected_from_raw) and gap-filled (corrected_filled) days and the gap-filled fraction, a "
    "conditioning variable. Gap-filled reference ET shares gridMET drivers with the OpenET "
    "interpolation (D-005).",
    (
        _key("diagnostics_id", "string", "Declared diagnostics"),
        _key("comparison_id", "string", "Comparison identifier"),
        _key("cohort_id", "string", "Cohort identifier"),
        _key("site_id", "string", "Station"),
        _key("reference_variant", "string", "Reference variant", allowed=REFERENCE_VARIANTS),
        _key("period_type", "string", "all, month or season", allowed=DIAGNOSTIC_PERIODS),
        _key("period_label", "string", "'all', 'YYYY-MM' or 'YYYY-SSS'"),
        _c("n_days", "int", "Cohort days in the period", nullable=False),
        _c("n_days_corrected_from_raw", "int", "Observation-dominated reference days",
           nullable=False),
        _c("n_days_corrected_filled", "int", "Gap-filled reference days", nullable=False),
        _c("gap_filled_fraction", "float", "n_days_corrected_filled / n_days", unit=FRACTION),
        _c("flags", "string", "';'-joined flags (shared_gridmet_drivers when any day is filled)"),
    ),
    primary_key=("diagnostics_id", "comparison_id", "cohort_id", "site_id", "reference_variant",
                 "period_type", "period_label"),
)

CONDITIONAL_ERRORS = TableSchema(
    "conditional_errors", "1",
    "Metrics of paired errors e = P - O per model inside bins of a declared conditioning "
    "variable, within one frozen cohort. Bins come from the configuration (fixed edges, training "
    "quantiles of the variable, calendar, sectors or reference provenance), never from the "
    "errors. Sparse bins carry a status, not a value. Conditional summaries describe "
    "associations, not causes.",
    (
        _key("diagnostics_id", "string", "Declared diagnostics"),
        _key("comparison_id", "string", "Comparison identifier"),
        _key("cohort_id", "string", "Cohort identifier"),
        _key("site_id", "string", "Station"),
        *_SERIES_KEY,
        _key("variable", "string", "Conditioning variable (declared name, season, month, "
             "gap_filled_fraction or reference_provenance)"),
        _key("bin_index", "int", "Bin position; negative for days accounted for but not binned "
             "(-1 outside edges, -2 variable missing, -3 excluded provenance)"),
        _key("metric", "string", "Metric (etcompare.metrics)"),
        _key("interval_method", "string", "percentile / basic, or 'none' without intervals"),
        _key("block_length", "int", "Bootstrap block length in days; 0 = not resampled"),
        _c("bin_label", "string", "e.g. '[0.5, 1)', 'JJA', 'corrected_filled'", nullable=False),
        _c("bin_lower", "float", "Lower edge (intervals and sectors)"),
        _c("bin_upper", "float", "Upper edge"),
        _c("bin_closed", "string", "Which edges belong to the bin: left or both"),
        _c("bin_kind", "string", "interval, category, sector, or a non-binned remainder",
           nullable=False, allowed=BIN_KINDS),
        _c("bin_source", "string", "Where the bins come from", nullable=False,
           allowed=BIN_SOURCES),
        _c("variable_column", "string", "Source column of the variable"),
        _c("variable_units", "string", "Declared units of the variable"),
        _c("variable_status", "string", "verified / provisional / derived"),
        _c("value", "float", "Metric on the bin sample (compute_metrics); null unless status ok"),
        _c("units", "string", "Units of value", nullable=False),
        _c("status", "string", "ok, or why there is no value", nullable=False,
           allowed=BIN_STATUSES),
        _c("status_detail", "string", "Reason behind a non-ok status"),
        _c("n_days", "int", "This model's days in the bin", nullable=False),
        _c("n_days_bin_cohort", "int", "Cohort days in the bin for any model", nullable=False),
        _c("n_days_reference_filled", "int", "This model's bin days with gap-filled reference",
           nullable=False),
        _c("n_days_series", "int", "This model's cohort days (all bins)", nullable=False),
        _c("min_bin_days", "int", "Declared minimum bin size", nullable=False),
        _c("bin_days_shared", "bool", "Every model of the cohort has the same days in this bin",
           nullable=False),
        _c("lower", "float", "Interval lower bound; null unless interval_status is ok"),
        _c("upper", "float", "Interval upper bound"),
        _c("confidence_level", "float", "Nominal two-sided level", unit=FRACTION),
        _c("n_segments", "int", "Uninterrupted runs of bin days"),
        _c("n_blocks", "int", "Disjoint full-length blocks of the bin's runs"),
        _c("n_replicates_valid", "int", "Replicates with a defined metric"),
        _c("interval_status", "string", "ok, or why no interval is given", nullable=False,
           allowed=BIN_INTERVAL_STATUSES),
        _c("interval_detail", "string", "Reason behind a non-ok interval status"),
        _c("flags", "string", "';'-joined flags"),
    ),
    primary_key=("diagnostics_id", "comparison_id", "cohort_id", "site_id", "model",
                 "reference_variant", "extraction_method", "footprint_set_id", "variable",
                 "bin_index", "metric", "interval_method", "block_length"),
)

ERROR_REGRESSIONS = TableSchema(
    "error_regressions", "1",
    "Ordinary least squares of daily error e = P - O on a declared predictor (net radiation) "
    "per model and reference variant. With an energy-balance-corrected reference the error "
    "and the predictor are mathematically coupled through the correction; the uncorrected "
    "reference version is reported alongside where the cohort has it. Descriptive only.",
    (
        _key("diagnostics_id", "string", "Declared diagnostics"),
        _key("comparison_id", "string", "Comparison identifier"),
        _key("cohort_id", "string", "Cohort identifier"),
        _key("site_id", "string", "Station"),
        *_SERIES_KEY,
        _key("predictor", "string", "Declared predictor variable"),
        _c("predictor_column", "string", "Source column", nullable=False),
        _c("predictor_units", "string", "Declared units", nullable=False),
        _c("predictor_status", "string", "verified / provisional", nullable=False),
        _c("slope", "float", "d e / d predictor; null when undefined"),
        _c("slope_units", "string", "mm d-1 per predictor unit", nullable=False),
        _c("intercept", "float", "e at predictor = 0", unit=ET_UNIT),
        _c("pearson_r", "float", "Correlation of e and the predictor", unit=FRACTION),
        _c("n_days", "int", "Days with a predictor value", nullable=False),
        _c("n_days_predictor_missing", "int", "Cohort days without a predictor value",
           nullable=False),
        _c("n_days_reference_filled", "int", "Used days with gap-filled reference",
           nullable=False),
        _c("status", "string", "ok or estimate_undefined", nullable=False,
           allowed=REGRESSION_STATUSES),
        _c("status_detail", "string", "Reason behind a non-ok status"),
        _c("coupling", "string", "energy_balance_corrected_reference (coupled), "
           "uncorrected_reference, or includes_gap_filled_days", nullable=False),
        _c("flags", "string", "';'-joined flags"),
    ),
    primary_key=("diagnostics_id", "comparison_id", "cohort_id", "site_id", "model",
                 "reference_variant", "extraction_method", "footprint_set_id", "predictor"),
)

DIAGNOSTIC_AVAILABILITY = TableSchema(
    "diagnostic_availability", "1",
    "Which diagnostics ran and which were skipped, with reasons. An undeclared or unverified "
    "variable, a missing column, an all-null column or absent metadata skips only the "
    "diagnostic that needs it.",
    (
        _key("diagnostics_id", "string", "Declared diagnostics"),
        _key("comparison_id", "string", "Comparison identifier"),
        _key("cohort_id", "string", "Cohort identifier, or 'all'"),
        _key("analysis", "string", "Diagnostic (closure_magnitude, conditional_error, ...)"),
        _key("variable", "string", "Variable it needs, or 'none'"),
        _key("reference_variant", "string", "Reference variant, or 'all'"),
        _key("extraction_method", "string", "Extraction method, or 'all'"),
        _key("footprint_set_id", "string", "Footprint set, or 'all'"),
        _c("status", "string", "available or not_available", nullable=False,
           allowed=AVAILABILITY_STATUSES),
        _c("reason_code", "string", "Stable reason when not_available"),
        _c("reason", "string", "Explanation (also for labelled available diagnostics)"),
        _c("variable_column", "string", "Declared source column"),
        _c("variable_units", "string", "Declared units"),
        _c("variable_status", "string", "Declared status"),
        _c("n_days_cohort", "int", "Cohort days considered"),
        _c("n_days_with_value", "int", "Of those, days with a variable value"),
        _c("flags", "string", "';'-joined flags"),
    ),
    primary_key=("diagnostics_id", "comparison_id", "cohort_id", "analysis", "variable",
                 "reference_variant", "extraction_method", "footprint_set_id"),
)

REFERENCE_RANK_CHANGES = TableSchema(
    "reference_rank_changes", "1",
    "Point-rank changes between reference variants, taken from ranking_sensitivity (never "
    "re-ranked), with the effect each pair isolates. raw vs corrected-measured on one cohort "
    "is closure sensitivity; corrected-measured vs corrected-inclusive mixes gap-filled days "
    "and different cohort days (D-004). Reference sensitivity, not an uncertainty bound.",
    (
        _key("diagnostics_id", "string", "Declared diagnostics"),
        _key("ranking_id", "string", "Declared ranking"),
        _key("context_a", "string", "Context label of ranking_sensitivity"),
        _key("context_b", "string", "Context label of ranking_sensitivity"),
        _key("metric", "string", "Ranked metric"),
        _key("model", "string", "Canonical model name"),
        _c("variant_a", "string", "Reference variant of context_a", nullable=False,
           allowed=REFERENCE_VARIANTS),
        _c("variant_b", "string", "Reference variant of context_b", nullable=False,
           allowed=REFERENCE_VARIANTS),
        _c("effect", "string", "What the pair isolates", nullable=False,
           allowed=RANK_CHANGE_EFFECTS),
        _c("rank_a", "float", "Point mid-rank in context_a", nullable=False),
        _c("rank_b", "float", "Point mid-rank in context_b", nullable=False),
        _c("rank_change", "float", "rank_b - rank_a (positive: worse in context_b)",
           nullable=False),
        _c("n_models", "int", "Models ranked", nullable=False),
        _c("same_cohort", "bool", "Both contexts use one cohort (same days)", nullable=False),
        _c("comparison_a", "string", "Comparison of context_a", nullable=False),
        _c("comparison_b", "string", "Comparison of context_b", nullable=False),
        _c("n_days_a", "int", "Days scored in context_a", nullable=False),
        _c("n_days_b", "int", "Days scored in context_b", nullable=False),
    ),
    primary_key=("diagnostics_id", "ranking_id", "context_a", "context_b", "metric", "model"),
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
                        FOOTPRINT_MANIFEST, EXTRACTIONS, PAIRED, COHORT_MEMBERSHIP,
                        PERIOD_TOTALS, DAILY_ESTIMATES, CROP_HEIGHT, METRICS,
                        CUMULATIVE_ERROR, INTERVALS, RESIDUAL_AUTOCORRELATION, MODEL_RANKINGS,
                        RANK_DISTRIBUTION, MODEL_CONTRASTS, NETWORK_SUMMARIES,
                        RANKING_SENSITIVITY, CLOSURE_MAGNITUDE, REFERENCE_PROVENANCE_PERIODS,
                        CONDITIONAL_ERRORS, ERROR_REGRESSIONS, DIAGNOSTIC_AVAILABILITY,
                        REFERENCE_RANK_CHANGES, EXCLUSIONS)
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
