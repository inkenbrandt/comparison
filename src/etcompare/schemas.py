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
    ENSEMBLE_DESIGNS,
    EVENT_CATEGORIES,
    EVIDENCE_CLASSES,
    FOOTPRINT_VALUE_SEMANTICS,
    INTERVAL_LABELS,
    INTERVAL_METHODS,
    MANAGEMENT_KINDS,
    MULTIPLICITY_POLICIES,
    NETWORK_SUMMARY_TYPES,
    PERIOD_TYPES,
    PRODUCT_DAY_DEFINITIONS,
    SCENARIO_CATEGORIES,
    SPATIAL_SUPPORTS,
    STATION_DAY_DEFINITIONS,
    TEMPORAL_SUPPORTS,
    TERM_QUANTITIES,
    TERM_SOURCES,
    TERM_STATUSES,
    WATER_BALANCE_TERMS,
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
#: Landscape representativeness (``etcompare.representativeness``).
LAYER_KIND_VALUES: Final = ("categorical", "continuous")
#: past: the asset's reference period ended before the day; contains_day: the day lies in
#: it; future: it starts after the day. contains_day and future rows use later information.
TEMPORAL_RELATIONS: Final = ("past", "contains_day", "future")
CLASS_ROLES: Final = ("class", "nodata")
LANDSCAPE_QUANTITY_KINDS: Final = ("class_fraction", "nodata_fraction", "index_mean",
                                   "index_std")
LABEL_SOURCES: Final = ("station_metadata", "fluxfootprints")
SUPPORT_STATUSES: Final = ("clear_observation", "interpolated", "unknown")
#: outside_edges / variable_missing: remainder days of a binned variable, accounted for but
#: not binned (no value).
SUBSET_STATUSES: Final = ("ok", "sparse_subset", "empty_subset", "estimate_undefined",
                          "outside_edges", "variable_missing")
#: Station events (``etcompare.adapters.event_log``). ``overlap_only`` events (instrument
#: and confounder) are kept visible next to management events but never analysed as
#: treatments.
EVENT_ROLES: Final = ("management_event", "overlap_only")
#: Event responses (``etcompare.events``).
EVENT_SERIES: Final = ("observed", "modeled", "modeled_minus_observed")
#: Only an uncontrolled before/after design is implemented; it is never causal evidence.
EVENT_DESIGNS: Final = ("before_after_uncontrolled",)
EVENT_STATUSES: Final = ("ok", "incomplete_baseline", "incomplete_post_window",
                         "incomplete_baseline_and_post_window")
RECOVERY_STATUSES: Final = ("recovered", "not_recovered_within_horizon",
                            "not_identifiable_gap", "interrupted_by_management_event",
                            "baseline_unavailable", "baseline_below_min", "not_applicable")
OVERLAP_WINDOWS: Final = ("pre", "event", "post", "recovery")
#: Anomalies (``etcompare.anomalies``).
ANOMALY_DAY_STATUSES: Final = ("ok", "baseline_unavailable")
ANOMALY_METRIC_STATUSES: Final = ("ok", "sparse_sample", "empty_sample", "estimate_undefined",
                                  "insufficient_training_years")
#: Water balances (``etcompare.water_balance``). A period total exists only when complete.
TERM_TOTAL_STATUSES: Final = ("complete", "incomplete_period", "unknown_term")
#: ``measured_modeled``: only measured and modeled terms; ``scenario``: at least one
#: scenario term. The two are always separate rows.
RESIDUAL_KINDS: Final = ("measured_modeled", "scenario")
RESIDUAL_STATUSES: Final = ("computed", "unknown", "not_applicable")
VOLUME_STATUSES: Final = ("computed", "not_requested", "area_unknown", "support_mismatch",
                          "no_value")
#: Scenario results (``etcompare.scenarios``).
SCENARIO_STATUSES: Final = ("computed", "unknown")
#: Held-out ensembles (``etcompare.ensemble``). ``baseline`` holds the products without
#: fitted parameters (official, mean, median); each design holds its fitted product.
ENSEMBLE_SCOPES: Final = ("baseline", *ENSEMBLE_DESIGNS)
ENSEMBLE_PRODUCT_KINDS: Final = ("official", "mean", "median", "fitted")
#: evaluated: baseline products compared, nothing fitted; held_out: at least one fold has
#: held-out predictions; declined / exploratory: too little independent data;
#: not_available: the design cannot exist for these inputs (e.g. one station).
ENSEMBLE_STATUSES: Final = ("evaluated", "held_out", "declined", "exploratory",
                            "not_available")
FOLD_STATUSES: Final = ("fitted", "declined", "exploratory_in_sample")
TRAINING_ROLES: Final = ("training", "held_out", "buffer_excluded", "training_fold_too_small",
                         "members_incomplete", "gap_filled_reference", "not_eligible")
IMPROVEMENT_STATUSES: Final = ("supported", "not_supported", "not_assessed")
#: Workflow units (``etcompare.workflow``). completed: ran now; cached: outputs of an earlier
#: run reused because nothing it depends on changed; skipped: switched off (``run.skip``),
#: not declared, or a dependency was skipped; not_available: a needed optional input or
#: dependency is missing; failed: raised an error; not_run: declared but never run;
#: stale: an earlier run's outputs exist but an input changed since.
UNIT_STATUSES: Final = ("completed", "cached", "skipped", "not_available", "failed",
                        "not_run", "stale")
#: Where a skipped-analyses row comes from: a workflow unit, or a status/availability
#: column of an analysis table.
SKIP_SOURCES: Final = ("workflow", "table")


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

_LANDSCAPE_KEY = (
    _key("representativeness_id", "string", "Declared representativeness analysis"),
    _key("site_id", "string", "Station"),
    _key("date", "date", "Footprint station-day label"),
    _key("day_set", "string", "Footprint set whose usable days this row belongs to (fixed "
         "supports are evaluated on those frozen days)"),
    _key("extraction_method", "string", "footprint_conservative, pixel_window_<n>x<n>, "
         "nearest_tower_pixel or polygon_<id> (D-029 naming)"),
    _key("footprint_set_id", "string", "Footprint set of the support, 'none' for fixed supports"),
    _key("layer_id", "string", "Declared landscape layer"),
    _key("matching_policy", "string", "Declared policy matching layer dates to the day"),
    _key("domain", "string", "full_domain (footprint grid), contour_<level> (footprint "
         "cumulative-contribution contour) or full_support (fixed supports)"),
)

_MATCH_COLUMNS = (
    _c("layer_asset", "string", "File of the layer used for this day", nullable=False),
    _c("layer_vintage", "int", "Declared vintage year (categorical maps)"),
    _c("layer_reference_start", "date", "Start of the asset's reference period",
       nullable=False),
    _c("layer_reference_end", "date", "End of the asset's reference period", nullable=False),
    _c("days_from_layer", "int", "day - reference_end (past, > 0), 0 inside the period, "
       "day - reference_start (future, < 0)", unit="d", nullable=False),
    _c("temporal_relation", "string", "past, contains_day or future", nullable=False,
       allowed=TEMPORAL_RELATIONS),
    _c("footprint_year", "int", "Calendar year of the day", nullable=False),
    _c("layer_year", "int", "Calendar year of the asset's reference end", nullable=False),
    _c("year_mismatch", "int", "footprint_year - layer_year", nullable=False),
    _c("retrospective", "bool", "The asset's reference period ends after the day, so the "
       "row uses later information", nullable=False),
)

_DENOMINATORS = (
    _c("weight_denominator", "string", "What weight_grid_total is: e.g. "
       "normalized_truncated_footprint (D-008), window_pixels, polygon_area", nullable=False),
    _c("weight_grid_total", "float", "Support total: footprint-grid total, or 1 for fixed "
       "supports", unit=FRACTION, nullable=False),
    _c("weight_domain", "float", "Weight inside the domain (= total for full_domain; contour "
       "mass for contours)", unit=FRACTION, nullable=False),
    _c("weight_in_layer_extent", "float", "Domain weight on cells of the layer raster window",
       unit=FRACTION, nullable=False),
    _c("weight_valid", "float", "Domain weight on valid (non-nodata) layer cells",
       unit=FRACTION, nullable=False),
    _c("weight_nodata", "float", "Domain weight on nodata layer cells inside the extent",
       unit=FRACTION, nullable=False),
    _c("valid_frac_of_grid_total", "float", "weight_valid / weight_grid_total (separate "
       "denominator; never multiplied with domain capture)", unit=FRACTION, nullable=False),
    _c("nodata_frac_of_grid_total", "float", "weight_nodata / weight_grid_total",
       unit=FRACTION, nullable=False),
    _c("outside_layer_frac_of_grid_total", "float",
       "(weight_domain - weight_in_layer_extent) / weight_grid_total", unit=FRACTION,
       nullable=False),
    _c("domain_frac_of_grid_total", "float", "weight_domain / weight_grid_total (contour mass "
       "for contours)", unit=FRACTION, nullable=False),
    _c("n_cells_valid", "int", "Layer cells with positive weight and a valid value",
       nullable=False),
)

LANDSCAPE_CLASS_FRACTIONS = TableSchema(
    "landscape_class_fractions", "1",
    "Weight of a support (footprint, pixel window, nearest pixel, polygon) on each class of a "
    "categorical map, moved onto the map's native grid by exact area overlap (classes are "
    "never resampled). class_frac_of_valid is conditional on valid cells and sums to 1 over "
    "the class rows; nodata weight is a separate row. Landscape description only: not an ET "
    "accuracy guarantee.",
    (
        *_LANDSCAPE_KEY,
        _key("class_code", "string", "Legend code, or 'nodata'"),
        _c("class_role", "string", "class or nodata", nullable=False, allowed=CLASS_ROLES),
        _c("class_name", "string", "Legend name ('nodata' for the nodata row)", nullable=False),
        _c("class_weight", "float", "Domain weight on this class (or on nodata)", unit=FRACTION,
           nullable=False),
        _c("class_frac_of_valid", "float", "class_weight / weight_valid; null for nodata",
           unit=FRACTION),
        _c("class_frac_of_grid_total", "float", "class_weight / weight_grid_total",
           unit=FRACTION, nullable=False),
        *_DENOMINATORS,
        *_MATCH_COLUMNS,
        _c("contour_level", "float", "Declared contour level of a contour domain", unit=FRACTION),
        _c("flags", "string", "';'-joined flags"),
    ),
    primary_key=tuple(c.name for c in _LANDSCAPE_KEY) + ("class_code",),
)

LANDSCAPE_INDEX_SUMMARIES = TableSchema(
    "landscape_index_summaries", "1",
    "Support-weighted mean and variability of a continuous vegetation index on its native "
    "grid. weighted_std = sqrt(sum c_j (v_j - m)^2 / sum c_j) over valid cells (population "
    "form, no small-sample correction); n_eff = (sum c_j)^2 / sum c_j^2 (Kish effective number "
    "of cells). Landscape description only: not an ET accuracy guarantee.",
    (
        *_LANDSCAPE_KEY,
        _c("index_units", "string", "Declared units of the index", nullable=False),
        _c("weighted_mean", "float", "sum c_j v_j / sum c_j over valid cells", nullable=False),
        _c("weighted_std", "float", "Weighted population standard deviation", nullable=False),
        _c("std_estimator", "string", "Exact estimator of weighted_std", nullable=False),
        _c("n_eff", "float", "Effective number of contributing cells", nullable=False),
        *_DENOMINATORS,
        *_MATCH_COLUMNS,
        _c("contour_level", "float", "Declared contour level of a contour domain", unit=FRACTION),
        _c("flags", "string", "';'-joined flags"),
    ),
    primary_key=tuple(c.name for c in _LANDSCAPE_KEY),
)

LANDSCAPE_METHOD_DIFFERENCES = TableSchema(
    "landscape_method_differences", "1",
    "Footprint minus fixed-support landscape quantities on frozen footprint days: the same "
    "day, layer asset and matching policy on both sides. A difference describes what the "
    "supports see; it is not an ET error. ET_mean (source_region_mean) is a varying source "
    "region (D-007) and never appears as a fixed support.",
    (
        _key("representativeness_id", "string", "Declared representativeness analysis"),
        _key("site_id", "string", "Station"),
        _key("date", "date", "Footprint station-day label"),
        _key("footprint_set_id", "string", "Footprint set (baseline side)"),
        _key("layer_id", "string", "Declared landscape layer"),
        _key("matching_policy", "string", "Declared matching policy"),
        _key("domain", "string", "Footprint domain: full_domain or contour_<level>"),
        _key("compared_method", "string", "Fixed support compared with the footprint"),
        _key("quantity", "string", "class_frac_of_valid:<code>, nodata_frac_of_grid_total, "
             "index_mean or index_std"),
        _c("quantity_kind", "string", "Kind of quantity", nullable=False,
           allowed=LANDSCAPE_QUANTITY_KINDS),
        _c("footprint_value", "float", "Footprint side (0 for a class absent on that side)",
           nullable=False),
        _c("method_value", "float", "Fixed-support side", nullable=False),
        _c("difference", "float", "footprint_value - method_value", nullable=False),
        _c("units", "string", "Units of the quantity", nullable=False),
        _c("layer_asset", "string", "Asset used on both sides", nullable=False),
        _c("retrospective", "bool", "The asset uses information from after the day",
           nullable=False),
        _c("flags", "string", "';'-joined flags"),
    ),
    primary_key=("representativeness_id", "site_id", "date", "footprint_set_id", "layer_id",
                 "matching_policy", "domain", "compared_method", "quantity"),
)

REPRESENTATIVENESS_LABELS = TableSchema(
    "representativeness_labels", "1",
    "Qualitative labels kept next to the quantitative fractions: the station's metadata land "
    "cover (a site label, not a footprint-weighted fraction) and optional fluxfootprints "
    "HIGH/MEDIUM/LOW classes. A label never enters a metric or threshold and is not a "
    "quantitative ET accuracy guarantee.",
    (
        _key("representativeness_id", "string", "Declared representativeness analysis"),
        _key("site_id", "string", "Station"),
        _key("date_label", "string", "ISO day, or 'all' for a station-level label"),
        _key("day_set", "string", "Footprint set, or 'none'"),
        _key("layer_id", "string", "Layer the label describes, or 'station_metadata'"),
        _key("matching_policy", "string", "Matching policy, or 'none'"),
        _key("domain", "string", "Footprint domain, or 'none'"),
        _key("label_source", "string", "station_metadata or fluxfootprints",
             allowed=LABEL_SOURCES),
        _key("radius_m", "float", "Target-area radius of a fluxfootprints label; 0 otherwise",
             unit="m"),
        _c("label", "string", "The label (e.g. IGBP 'CRO', or 'high'/'medium'/'low')",
           nullable=False),
        _c("label_meaning", "string", "What the label states and what it does not",
           nullable=False),
        _c("dominant_class", "string", "fluxfootprints dominant footprint class"),
        _c("p_footprint", "float", "fluxfootprints dominant-class footprint share",
           unit=FRACTION),
        _c("p_target", "float", "Its share of the target disc", unit=FRACTION),
        _c("chi2", "float", "fluxfootprints chi-square statistic"),
        _c("p_value", "float", "Its p-value (pseudo-counts; see fluxfootprints)"),
        _c("source_version", "string", "Version and commit of the label source",
           nullable=False),
        _c("source_parameters", "string", "Upstream thresholds and parameters (JSON)"),
        _c("layer_asset", "string", "Layer asset used"),
        _c("flags", "string", "';'-joined flags (always label_not_accuracy_guarantee)"),
    ),
    primary_key=("representativeness_id", "site_id", "date_label", "day_set", "layer_id",
                 "matching_policy", "domain", "label_source", "radius_m"),
)

SATELLITE_SUPPORT_ERRORS = TableSchema(
    "satellite_support_errors", "1",
    "Metrics (compute_metrics) of e = P - O on clear-observation and interpolated days of one "
    "frozen cohort, identical across models, plus declared acquisition-timing bins. Support "
    "comes only from authoritative product metadata; unknown support is never counted as "
    "either subset.",
    (
        _key("satellite_support_id", "string", "Declared satellite-support analysis"),
        _key("comparison_id", "string", "Comparison identifier"),
        _key("cohort_id", "string", "Cohort identifier"),
        _key("site_id", "string", "Station"),
        *_SERIES_KEY,
        _key("variable", "string", "support_status, or an acquisition-gap variable"),
        _key("subset", "string", "all_cohort_days, clear_observation, interpolated, or a bin "
             "label"),
        _key("metric", "string", "Metric (etcompare.metrics)"),
        _c("bin_index", "int", "Bin position for gap variables; null for support subsets"),
        _c("bin_lower", "float", "Lower edge (days)", unit="d"),
        _c("bin_upper", "float", "Upper edge (days)", unit="d"),
        _c("value", "float", "Metric on the subset; null unless status ok"),
        _c("units", "string", "Units of value", nullable=False),
        _c("status", "string", "ok, or why there is no value", nullable=False,
           allowed=SUBSET_STATUSES),
        _c("status_detail", "string", "Reason behind a non-ok status"),
        _c("n_days", "int", "Days in the subset (identical for every model)", nullable=False),
        _c("n_days_cohort", "int", "Cohort days", nullable=False),
        _c("n_days_mixed_support", "int", "Cohort days whose support differs across models "
           "(excluded from both subsets)", nullable=False),
        _c("n_days_unknown_support", "int", "Cohort days with no support record for some model",
           nullable=False),
        _c("n_days_model_clear", "int", "This model's clear days in the cohort", nullable=False),
        _c("n_days_model_interpolated", "int", "This model's interpolated days", nullable=False),
        _c("n_days_model_unknown", "int", "This model's days without a support record",
           nullable=False),
        _c("min_subset_days", "int", "Declared minimum subset size", nullable=False),
        _c("support_sources", "string", "Support source IDs and authorities used",
           nullable=False),
        _c("retrospective", "bool", "The variable uses acquisitions after the day",
           nullable=False),
        _c("flags", "string", "';'-joined flags"),
    ),
    primary_key=("satellite_support_id", "comparison_id", "cohort_id", "site_id", "model",
                 "reference_variant", "extraction_method", "footprint_set_id", "variable",
                 "subset", "metric"),
)

REPRESENTATIVENESS_AVAILABILITY = TableSchema(
    "representativeness_availability", "1",
    "Which landscape and satellite-support analyses ran, per day where relevant, and which "
    "were not available, with reasons. A day without an admissible layer asset is "
    "not_available, never filled.",
    (
        _key("analysis_id", "string", "Declared representativeness or satellite-support ID"),
        _key("site_id", "string", "Station"),
        _key("analysis", "string", "landscape_class_fractions, landscape_index_summaries, "
             "method_difference, fluxfootprints_labels, satellite_support, ..."),
        _key("layer_id", "string", "Layer, or 'none'"),
        _key("extraction_method", "string", "Support, or 'all'"),
        _key("day_set", "string", "Footprint set or comparison, or 'all'"),
        _key("matching_policy", "string", "Matching policy, or 'all'"),
        _key("domain", "string", "Domain, or 'all'"),
        _key("date_label", "string", "ISO day, or 'all'"),
        _c("status", "string", "available or not_available", nullable=False,
           allowed=AVAILABILITY_STATUSES),
        _c("reason_code", "string", "Stable reason when not_available"),
        _c("reason", "string", "Explanation (also for labelled available analyses)"),
        _c("n_days", "int", "Days concerned"),
        _c("flags", "string", "';'-joined flags"),
    ),
    primary_key=("analysis_id", "site_id", "analysis", "layer_id", "extraction_method",
                 "day_set", "matching_policy", "domain", "date_label"),
)

STATION_EVENTS = TableSchema(
    "station_events", "1",
    "Events of a declared station event log, one row per event inside the station record. "
    "Events come only from the log, never from model residuals or agreement. Source columns "
    "pass through with prefix 'src_'.",
    (
        _key("site_id", "string", "Station (from the declared site mapping)"),
        _key("event_id", "string", "Source event ID, preserved as text"),
        _c("event_table", "string", "Declared event table name", nullable=False),
        _c("source_station_id", "string", "Station ID as written in the log", nullable=False),
        _c("start_date", "date", "Station day of the event timestamp (declared day rule)",
           nullable=False),
        _c("end_date", "date", "Last station day; equals start_date without an end column",
           nullable=False),
        _c("start_time", "string", "Time of day as logged (HH:MM:SS), null for date-only "
           "entries (logged at exactly 00:00)"),
        _c("event_type", "string", "Source event_type, preserved exactly", nullable=False),
        _c("category", "string", "Declared category of the event type", nullable=False,
           allowed=EVENT_CATEGORIES),
        _c("management_kind", "string", "Declared kind for management events; null otherwise",
           allowed=MANAGEMENT_KINDS),
        _c("analysis_role", "string", "management_event, or overlap_only (instrument and "
           "confounder events: never a treatment)", nullable=False, allowed=EVENT_ROLES),
        _c("event_source", "string", "Source event_source, preserved exactly (null when "
           "unlabelled)"),
        _c("evidence_class", "string", "Declared evidence class of event_source (unknown when "
           "unlabelled)", nullable=False, allowed=EVIDENCE_CLASSES),
        _c("observer", "string", "Observer as logged"),
        _c("event_notes", "string", "Notes as logged"),
        _c("approximate", "bool", "A declared approximate-date pattern matches the notes",
           nullable=False),
        _c("approximate_rule", "string", "The pattern that matched, if any"),
        _c("management_record", "bool", "A declared management record confirms the event "
           "(event_source and evidence_class stay as logged)", nullable=False),
        _c("management_record_source", "string", "The confirming record, who confirmed it and "
           "when"),
    ),
    primary_key=("site_id", "event_id"),
    allow_extra_columns=True,
)

_SERIES_KEYS: Final = (
    _key("comparison_id", "string", "Comparison"),
    _key("cohort_id", "string", "Cohort"),
    _key("site_id", "string", "Station"),
)
_SIDE_KEYS: Final = (
    _key("model", "string", "Model"),
    _key("reference_variant", "string", "Reference variant", allowed=REFERENCE_VARIANTS),
    _key("extraction_method", "string", "Extraction method"),
    _key("footprint_set_id", "string", "Footprint set, or 'none'"),
)

EVENT_RESPONSES = TableSchema(
    "event_responses", "1",
    "Before/after response of each cohort series to each management event: baseline, change, "
    "cumulative response and recovery for the observed and modeled series and their "
    "difference. An uncontrolled before/after difference is not causal evidence.",
    (
        _key("event_analysis_id", "string", "Declared event analysis"),
        *_SERIES_KEYS,
        _key("event_id", "string", "Event (from the station event log)"),
        *_SIDE_KEYS,
        _key("series", "string", "observed, modeled or modeled_minus_observed",
             allowed=EVENT_SERIES),
        _c("event_start_date", "date", "Event station day", nullable=False),
        _c("event_end_date", "date", "Last event station day", nullable=False),
        _c("management_kind", "string", "Management kind", nullable=False),
        _c("evidence_class", "string", "Evidence class of the logged source", nullable=False),
        _c("management_record", "bool", "Confirmed by a management record", nullable=False),
        _c("approximate", "bool", "The log calls the date approximate", nullable=False),
        _c("design", "string", "Comparison design", nullable=False, allowed=EVENT_DESIGNS),
        _c("pre_start", "date", "First baseline day", nullable=False),
        _c("pre_end", "date", "Last baseline day", nullable=False),
        _c("post_start", "date", "First post day", nullable=False),
        _c("post_end", "date", "Last post day", nullable=False),
        _c("n_pre_days_expected", "int", "Calendar days of the baseline window", nullable=False),
        _c("n_pre_days", "int", "Cohort days in the baseline window", nullable=False),
        _c("n_post_days_expected", "int", "Calendar days of the post window", nullable=False),
        _c("n_post_days", "int", "Cohort days in the post window", nullable=False),
        _c("n_pre_days_reference_filled", "int", "Gap-filled reference days in the baseline",
           nullable=False),
        _c("n_post_days_reference_filled", "int", "Gap-filled reference days after the event",
           nullable=False),
        _c("baseline_mean_mm", "float", "Mean daily ET of the baseline window", unit=ET_UNIT),
        _c("post_mean_mm", "float", "Mean daily ET of the post window", unit=ET_UNIT),
        _c("change_mm", "float", "post_mean - baseline_mean", unit=ET_UNIT),
        _c("relative_change_percent", "float", "100 * change / baseline", unit="%"),
        _c("cumulative_response_mm", "float", "Sum over the post window of (ET - baseline); "
           "only with every post day present", unit=ET_TOTAL_UNIT),
        _c("baseline_fraction", "float", "Baseline mean of ET / normalising variable",
           unit=FRACTION),
        _c("post_fraction", "float", "Post-window mean of ET / normalising variable",
           unit=FRACTION),
        _c("fraction_change", "float", "post_fraction - baseline_fraction", unit=FRACTION),
        _c("recovery_days", "int", "Days after the event until the smoothed ET reaches "
           "recovery_fraction of the baseline (difference rows: modeled - observed)", unit="d"),
        _c("recovery_status", "string", "Recovery outcome", nullable=False,
           allowed=RECOVERY_STATUSES),
        _c("status", "string", "ok, or why the change is not computed", nullable=False,
           allowed=EVENT_STATUSES),
        _c("status_detail", "string", "Further detail"),
        _c("overlapping_event_ids", "string", "';'-joined IDs of other events in the windows"),
        _c("flags", "string", "';'-joined flags"),
    ),
    primary_key=("event_analysis_id", "comparison_id", "cohort_id", "site_id", "event_id",
                 "model", "reference_variant", "extraction_method", "footprint_set_id",
                 "series"),
)

EVENT_OVERLAPS = TableSchema(
    "event_overlaps", "1",
    "Every other logged event (management, instrument or confounder) inside a management "
    "event's windows. Kept visible; instrument and confounder events are never treatments.",
    (
        _key("event_analysis_id", "string", "Declared event analysis"),
        _key("site_id", "string", "Station"),
        _key("event_id", "string", "Analysed management event"),
        _key("other_event_id", "string", "Overlapping event"),
        _c("other_event_type", "string", "Logged type", nullable=False),
        _c("other_category", "string", "Declared category", nullable=False,
           allowed=EVENT_CATEGORIES),
        _c("other_analysis_role", "string", "management_event or overlap_only", nullable=False,
           allowed=EVENT_ROLES),
        _c("other_start_date", "date", "Overlapping event's station day", nullable=False),
        _c("offset_days", "int", "Other start minus this event's start", unit="d",
           nullable=False),
        _c("window", "string", "pre, event, post or recovery (where the other event starts)",
           nullable=False, allowed=OVERLAP_WINDOWS),
        _c("other_event_notes", "string", "Notes as logged"),
    ),
    primary_key=("event_analysis_id", "site_id", "event_id", "other_event_id"),
)

EVENT_CONDITIONS = TableSchema(
    "event_conditions", "1",
    "Declared weather variables summarised over each event's windows on the station "
    "record, so weather confounding stays visible next to the responses.",
    (
        _key("event_analysis_id", "string", "Declared event analysis"),
        _key("site_id", "string", "Station"),
        _key("event_id", "string", "Management event"),
        _key("variable", "string", "Declared weather variable"),
        _key("window", "string", "pre or post", allowed=("pre", "post")),
        _c("column", "string", "station_daily column", nullable=False),
        _c("aggregation", "string", "mean or sum", nullable=False),
        _c("value", "float", "Window summary (null when no day has a value)"),
        _c("units", "string", "Declared units (sums: per window)", nullable=False),
        _c("n_days", "int", "Days with a value", nullable=False),
        _c("n_days_expected", "int", "Calendar days of the window", nullable=False),
    ),
    primary_key=("event_analysis_id", "site_id", "event_id", "variable", "window"),
)

ANOMALY_DAILY = TableSchema(
    "anomaly_daily", "1",
    "Daily anomalies of a cohort: each value minus its baseline (held-out climatology or "
    "exploratory moving mean), fitted separately to the observed and modeled series.",
    (
        _key("anomaly_id", "string", "Declared anomaly analysis"),
        *_SERIES_KEYS,
        _key("date", "date", "Station-day label"),
        *_SIDE_KEYS,
        _c("method", "string", "Anomaly method", nullable=False),
        _c("fold", "string", "Held-out fold label, or 'in_sample'", nullable=False),
        _c("observed_mm", "float", "Observed ET", unit=ET_UNIT),
        _c("modeled_mm", "float", "Modeled ET", unit=ET_UNIT),
        _c("observed_baseline_mm", "float", "Baseline fitted to the observed series",
           unit=ET_UNIT),
        _c("modeled_baseline_mm", "float", "Baseline fitted to the modeled series",
           unit=ET_UNIT),
        _c("observed_anomaly_mm", "float", "observed - observed baseline", unit=ET_UNIT),
        _c("modeled_anomaly_mm", "float", "modeled - modeled baseline", unit=ET_UNIT),
        _c("status", "string", "ok or baseline_unavailable", nullable=False,
           allowed=ANOMALY_DAY_STATUSES),
        _c("n_training_values", "int", "Values behind the baseline"),
        _c("n_training_years", "int", "Distinct training folds behind the climatology"),
        _c("reference_provenance", "string", "Reference provenance", allowed=REFERENCE_PROVENANCE),
    ),
    primary_key=("anomaly_id", "comparison_id", "cohort_id", "site_id", "date", "model",
                 "reference_variant", "extraction_method", "footprint_set_id"),
)

ANOMALY_METRICS = TableSchema(
    "anomaly_metrics", "1",
    "Metrics of raw values and of anomalies on the same days, per fold and pooled. Raw bias "
    "is reported next to anomaly skill because demeaning removes part of the bias.",
    (
        _key("anomaly_id", "string", "Declared anomaly analysis"),
        *_SERIES_KEYS,
        *_SIDE_KEYS,
        _key("fold", "string", "Held-out fold, 'all_folds' (pooled held-out days) or "
             "'in_sample'"),
        _key("series_kind", "string", "raw or anomaly", allowed=("raw", "anomaly")),
        _key("metric", "string", "Metric ID"),
        _c("method", "string", "Anomaly method", nullable=False),
        _c("inference", "string", "held_out or exploratory", nullable=False,
           allowed=("held_out", "exploratory")),
        _c("value", "float", "Metric value; null with a status when undefined"),
        _c("units", "string", "Units", nullable=False),
        _c("status", "string", "Status", nullable=False, allowed=ANOMALY_METRIC_STATUSES),
        _c("status_detail", "string", "Further detail"),
        _c("n_days", "int", "Days in the sample", nullable=False),
        _c("n_days_cohort", "int", "Cohort days of the series", nullable=False),
        _c("n_days_reference_filled", "int", "Gap-filled reference days in the sample",
           nullable=False),
        _c("training_folds", "string", "';'-joined training folds (held-out rows)"),
        _c("flags", "string", "';'-joined flags"),
    ),
    primary_key=("anomaly_id", "comparison_id", "cohort_id", "site_id", "model",
                 "reference_variant", "extraction_method", "footprint_set_id", "fold",
                 "series_kind", "metric"),
)

_BALANCE_KEYS = (
    _key("water_balance_id", "string", "Declared water balance"),
    _key("site_id", "string", "Station"),
    _key("period_type", "string", "Period type of the step-8 aggregation",
         allowed=PERIOD_TYPES),
    _key("period_label", "string", "Period label (e.g. 2025-07, WY2025, GS2025)"),
)
_PERIOD_BOUNDS = (
    _c("period_start", "date", "First calendar day of the period", nullable=False),
    _c("period_end", "date", "Last calendar day of the period", nullable=False),
    _c("boundary_id", "string", "Declared boundary every term must cover", nullable=False),
)

WATER_BALANCE_TERM_TOTALS = TableSchema(
    "water_balance_terms", "1",
    "Period totals of each declared water-balance term. A total exists only when every "
    "calendar day (or both storage states) of the period has a value; otherwise the partial "
    "sum is reported with its day count and is never a full-period total.",
    (
        *_BALANCE_KEYS,
        _key("term", "string", "Water-balance term", allowed=WATER_BALANCE_TERMS),
        *_PERIOD_BOUNDS,
        _c("status", "string", "measured, modeled, scenario or unknown", nullable=False,
           allowed=TERM_STATUSES),
        _c("source_kind", "string", "Where the values come from", nullable=False,
           allowed=TERM_SOURCES),
        _c("source", "string", "Column, table, reference variant or scenario value"),
        _c("quantity", "string", "flux (summed) or storage_state (differenced)",
           nullable=False, allowed=TERM_QUANTITIES),
        _c("spatial_support", "string", "Declared spatial support of the values",
           allowed=SPATIAL_SUPPORTS),
        _c("support_matches_boundary", "bool",
           "The term covers exactly the boundary (null for an unknown term)"),
        _c("day_definition", "string", "Declared day definition of the term's values"),
        _c("sign", "int", "+1 inflow, -1 outflow or storage increase", nullable=False),
        _c("n_days_expected", "int", "Calendar days of the period", nullable=False),
        _c("n_values_required", "int",
           "Values a complete total needs: every day (flux) or 2 states (storage)"),
        _c("n_values_present", "int", "Values present among those required"),
        _c("period_completeness", "float", "n_values_present / n_values_required",
           unit=FRACTION),
        _c("partial_sum_mm", "float",
           "Sum over the n_values_present days only (flux); never a full-period total",
           unit=ET_TOTAL_UNIT),
        _c("total_mm", "float", "Full-period total; null unless total_status is complete",
           unit=ET_TOTAL_UNIT),
        _c("signed_total_mm", "float", "sign x total_mm", unit=ET_TOTAL_UNIT),
        _c("total_status", "string", "complete, incomplete_period or unknown_term",
           nullable=False, allowed=TERM_TOTAL_STATUSES),
        _c("total_detail", "string", "Further detail"),
        _c("volume_m3", "float", "total_mm x boundary area (known area and matching support)",
           unit="m3"),
        _c("volume_status", "string", "Why a volume is or is not given", nullable=False,
           allowed=VOLUME_STATUSES),
        _c("reference_variant", "string", "Station ET reference variant (ET term)",
           allowed=REFERENCE_VARIANTS),
        _c("n_days_et_gap_filled", "int", "Gap-filled station ET days among the present days"),
        _c("flags", "string", "';'-joined flags"),
    ),
    primary_key=("water_balance_id", "site_id", "period_type", "period_label", "term"),
)

WATER_BALANCE_RESIDUALS = TableSchema(
    "water_balance_residuals", "1",
    "Water-balance residuals per period: a plausibility diagnostic holding every unmeasured "
    "exchange and the error of every term; never ET error and never a basis for choosing a "
    "model, reference, filter or footprint. Residuals from measured/modeled terms and "
    "scenario residuals are separate rows.",
    (
        *_BALANCE_KEYS,
        _key("residual_kind", "string", "measured_modeled or scenario", allowed=RESIDUAL_KINDS),
        *_PERIOD_BOUNDS,
        _c("equation", "string", "Declared equation and sign convention", nullable=False),
        _c("status", "string", "computed, unknown or not_applicable", nullable=False,
           allowed=RESIDUAL_STATUSES),
        _c("residual_mm", "float", "Residual depth; null unless computed", unit=ET_TOTAL_UNIT),
        _c("residual_volume_m3", "float", "Residual volume over the boundary area", unit="m3"),
        _c("volume_status", "string", "Why a volume is or is not given", nullable=False,
           allowed=VOLUME_STATUSES),
        _c("reason", "string", "Why the residual is unknown or not applicable"),
        _c("unknown_terms", "string", "';'-joined terms with no input"),
        _c("incomplete_terms", "string", "';'-joined terms without a complete period total"),
        _c("scenario_terms", "string", "';'-joined scenario terms"),
        _c("support_mismatch_terms", "string",
           "';'-joined terms whose spatial support differs from the boundary"),
        _c("n_days_expected", "int", "Calendar days of the period", nullable=False),
        _c("et_reference_variant", "string", "Station ET reference variant",
           allowed=REFERENCE_VARIANTS),
        _c("n_days_et_gap_filled", "int", "Gap-filled station ET days in the period"),
        _c("interpretation", "string", "How the residual may be read", nullable=False),
        _c("flags", "string", "';'-joined flags"),
    ),
    primary_key=("water_balance_id", "site_id", "period_type", "period_label",
                 "residual_kind"),
)

SCENARIO_RESULTS = TableSchema(
    "scenario_results", "1",
    "Declared scenario uncertainty of a water-balance residual: one uncertainty category per "
    "scenario (measurement, footprint_parameter or method_choice), never merged and never a "
    "bootstrap interval (sampling uncertainty is the step-10 intervals table).",
    (
        _key("scenario_id", "string", "Declared scenario"),
        _key("site_id", "string", "Station"),
        _key("period_type", "string", "Period type", allowed=PERIOD_TYPES),
        _key("period_label", "string", "Period label"),
        _key("statistic", "string",
             "base, mean, sd, q<level>, min, median, max, alternative=<name> or "
             "realization=<name>"),
        _c("water_balance_id", "string", "Water balance", nullable=False),
        _c("uncertainty_category", "string", "Uncertainty category of the scenario",
           nullable=False, allowed=SCENARIO_CATEGORIES),
        _c("uncertainty_label", "string", "Human-readable category label", nullable=False),
        _c("period_start", "date", "First calendar day of the period", nullable=False),
        _c("period_end", "date", "Last calendar day of the period", nullable=False),
        _c("residual_kind", "string", "Kind of the base residual", nullable=False,
           allowed=RESIDUAL_KINDS),
        _c("value", "float", "Residual statistic; null unless computed", unit=ET_TOTAL_UNIT),
        _c("status", "string", "computed or unknown", nullable=False, allowed=SCENARIO_STATUSES),
        _c("status_detail", "string", "Further detail (e.g. which terms are unknown)"),
        _c("n_draws", "int", "Draws (measurement) or alternatives incl. the declared one",
           nullable=False),
        _c("n_valid_draws", "int", "Draws or alternatives with a computed residual"),
        _c("seed", "int", "Declared seed (measurement scenarios)"),
        _c("scenario_sha256", "string", "SHA-256 of the scenario declaration", nullable=False),
        _c("interpretation", "string", "How the value may be read", nullable=False),
        _c("flags", "string", "';'-joined flags"),
    ),
    primary_key=("scenario_id", "site_id", "period_type", "period_label", "statistic"),
)

_ENSEMBLE_KEY = _key("ensemble_id", "string", "Declared ensemble experiment")
_DESIGN_KEY = _key("design", "string", "Fold design", allowed=ENSEMBLE_DESIGNS)

ENSEMBLE_DAYS = TableSchema(
    "ensemble_days", "1",
    "Member availability, eligibility under the declared missing-member policy, and member "
    "disagreement for every candidate day of a post-extraction ensemble experiment. Member "
    "spread is disagreement between models, never a confidence interval.",
    (
        _ENSEMBLE_KEY,
        _key("site_id", "string", "Station"),
        _key("date", "date", "Station-day label"),
        _c("observed_mm", "float", "Station reference ET of the declared variant", unit=ET_UNIT),
        _c("reference_provenance", "string", "Reference provenance (null: no reference)",
           allowed=REFERENCE_PROVENANCE),
        _c("n_members_declared", "int", "Declared members", nullable=False),
        _c("n_members_available", "int", "Members with an extracted value", nullable=False),
        _c("members_available", "string", "';'-joined members with a value"),
        _c("members_missing", "string", "';'-joined members without a value"),
        _c("official_available", "bool", "The official ensemble has a value (null: none "
           "declared)"),
        _c("eligible", "bool", "Usable under the missing-member policy, with a reference",
           nullable=False),
        _c("eligibility_reason", "string", "Why the day is not eligible"),
        _c("reweighted", "bool", "Combinations use only the available members (policy "
           "reweight_available)", nullable=False),
        _c("member_range_mm", "float", "max - min of the available members: disagreement, "
           "not a confidence interval", unit=ET_UNIT),
        _c("member_sd_mm", "float", "Population SD of the available members: disagreement, "
           "not a confidence interval", unit=ET_UNIT),
        _c("flags", "string", "';'-joined flags"),
    ),
    primary_key=("ensemble_id", "site_id", "date"),
)

ENSEMBLE_PREDICTIONS = TableSchema(
    "ensemble_predictions", "1",
    "Daily values of each compared product. 'baseline' rows (official, mean, median) have no "
    "fitted parameters; fitted rows are held-out predictions from weights fitted on other "
    "folds only.",
    (
        _ENSEMBLE_KEY,
        _key("scope", "string", "baseline, or the fold design of the fitted product",
             allowed=ENSEMBLE_SCOPES),
        _key("site_id", "string", "Station"),
        _key("date", "date", "Station-day label"),
        _key("product", "string", "Product ID (the official model, member_mean, member_median "
             "or fitted_weighted)"),
        _c("product_kind", "string", "official, mean, median or fitted", nullable=False,
           allowed=ENSEMBLE_PRODUCT_KINDS),
        _c("value_mm", "float", "Product ET", unit=ET_UNIT, nullable=False),
        _c("observed_mm", "float", "Station reference ET", unit=ET_UNIT),
        _c("reference_provenance", "string", "Reference provenance",
           allowed=REFERENCE_PROVENANCE),
        _c("fold", "string", "Held-out fold of a fitted prediction"),
        _c("inference", "string", "no_fitted_parameters or held_out", nullable=False,
           allowed=("no_fitted_parameters", "held_out")),
        _c("n_members_used", "int", "Members combined (null for the official product)"),
        _c("reweighted", "bool", "Weights renormalised over the available members",
           nullable=False),
        _c("weights_used", "string", "';'-joined member=weight actually applied (fitted)"),
        _c("flags", "string", "';'-joined flags"),
    ),
    primary_key=("ensemble_id", "scope", "site_id", "date", "product"),
)

ENSEMBLE_FOLDS = TableSchema(
    "ensemble_folds", "1",
    "One row per held-out fold: training membership counts, buffer, member availability, "
    "preprocessing and the regularisation chosen by inner grouped validation on training "
    "folds only, or why the fold was declined.",
    (
        _ENSEMBLE_KEY,
        _DESIGN_KEY,
        _key("fold", "string", "Held-out fold (year, water year or site); 'in_sample' for an "
             "exploratory fit"),
        _c("status", "string", "fitted, declined or exploratory_in_sample", nullable=False,
           allowed=FOLD_STATUSES),
        _c("status_detail", "string", "Why the fold was declined"),
        _c("held_out_start", "date", "First calendar day of a temporal fold"),
        _c("held_out_end", "date", "Last calendar day of a temporal fold"),
        _c("training_folds", "string", "';'-joined training folds used"),
        _c("n_training_folds", "int", "Training folds used", nullable=False),
        _c("n_training_days", "int", "Training site-days", nullable=False),
        _c("n_training_days_gap_filled", "int", "Gap-filled reference training site-days",
           nullable=False),
        _c("n_buffer_days_removed", "int", "Candidate training site-days removed by the buffer",
           nullable=False),
        _c("n_small_fold_days_excluded", "int",
           "Site-days of training folds below min_training_days_per_fold", nullable=False),
        _c("n_test_days", "int", "Held-out site-days", nullable=False),
        _c("n_test_days_reweighted", "int", "Held-out site-days with reweighted members",
           nullable=False),
        _c("n_inner_folds", "int", "Inner validation folds", nullable=False),
        _c("alpha_selected", "float", "Shrinkage toward equal weights chosen by inner "
           "validation (1 = simple mean)", unit=FRACTION),
        _c("alpha_grid", "string", "';'-joined candidate alphas", nullable=False),
        _c("selection_metric", "string", "Inner validation criterion"),
        _c("method", "string", "Weight estimator"),
        _c("regularisation", "string", "Regularisation toward equal weights"),
        _c("preprocessing", "string", "Feature preprocessing fitted on training rows"),
        _c("buffer_days", "int", "Declared buffer (days each side of a held-out fold)"),
        _c("member_availability", "string", "';'-joined member=days with a value among the "
           "fold's candidate days", nullable=False),
        _c("flags", "string", "';'-joined flags"),
    ),
    primary_key=("ensemble_id", "design", "fold"),
)

ENSEMBLE_WEIGHTS = TableSchema(
    "ensemble_weights", "1",
    "Member weights of each fitted fold (nonnegative, summing to one), before and after "
    "shrinkage toward equal weights; exploratory in-sample weights are labelled so and have "
    "no selected weight.",
    (
        _ENSEMBLE_KEY,
        _DESIGN_KEY,
        _key("fold", "string", "Held-out fold, or 'in_sample'"),
        _key("member", "string", "Member product"),
        _c("weight_unregularised", "float", "Simplex least-squares weight on training rows",
           unit=FRACTION, nullable=False),
        _c("alpha", "float", "Selected shrinkage", unit=FRACTION),
        _c("weight", "float", "(1 - alpha) * weight_unregularised + alpha / K", unit=FRACTION),
        _c("inference", "string", "held_out_fold_weights or exploratory_in_sample",
           nullable=False, allowed=("held_out_fold_weights", "exploratory_in_sample")),
        _c("n_training_days", "int", "Training site-days", nullable=False),
        _c("flags", "string", "';'-joined flags"),
    ),
    primary_key=("ensemble_id", "design", "fold", "member"),
)

ENSEMBLE_INNER_SCORES = TableSchema(
    "ensemble_inner_scores", "1",
    "Inner grouped leave-one-fold-out validation of each shrinkage candidate, on training "
    "folds only (held-out rows never enter).",
    (
        _ENSEMBLE_KEY,
        _DESIGN_KEY,
        _key("fold", "string", "Outer held-out fold"),
        _key("alpha", "float", "Shrinkage candidate", unit=FRACTION),
        _c("selection_metric", "string", "Criterion", nullable=False),
        _c("value", "float", "Pooled criterion over all inner held-out days"),
        _c("units", "string", "Units of value", nullable=False),
        _c("n_days", "int", "Inner held-out site-days", nullable=False),
        _c("n_inner_folds", "int", "Inner folds", nullable=False),
        _c("status", "string", "ok or estimate_undefined", nullable=False,
           allowed=("ok", "estimate_undefined")),
        _c("selected", "bool", "The chosen candidate (ties go to the larger alpha)",
           nullable=False),
    ),
    primary_key=("ensemble_id", "design", "fold", "alpha"),
)

ENSEMBLE_TRAINING_MEMBERSHIP = TableSchema(
    "ensemble_training_membership", "1",
    "Role of every candidate site-day in every fold: training, held out, or excluded with "
    "the reason.",
    (
        _ENSEMBLE_KEY,
        _DESIGN_KEY,
        _key("fold", "string", "Held-out fold"),
        _key("site_id", "string", "Station"),
        _key("date", "date", "Station-day label"),
        _c("group", "string", "Fold group of the day", nullable=False),
        _c("role", "string", "Role in this fold", nullable=False, allowed=TRAINING_ROLES),
        _c("in_training", "bool", "Row used to fit this fold's weights", nullable=False),
    ),
    primary_key=("ensemble_id", "design", "fold", "site_id", "date"),
)

ENSEMBLE_SUMMARY = TableSchema(
    "ensemble_summary", "1",
    "Status of each ensemble scope: the no-fit baseline comparison and each fold design "
    "(held_out, declined, exploratory or not_available, with the reason). Every ensemble here "
    "is built after spatial extraction, not at pixel level; one station is not network "
    "evidence.",
    (
        _ENSEMBLE_KEY,
        _key("scope", "string", "baseline or a fold design", allowed=ENSEMBLE_SCOPES),
        _key("subset", "string", "'all' days of the scope, or a declared month subset"),
        _c("status", "string", "Status of the scope", nullable=False,
           allowed=ENSEMBLE_STATUSES),
        _c("status_detail", "string", "Reason behind the status"),
        _c("ensemble_level", "string", "Where the ensemble is built", nullable=False,
           allowed=("post_extraction",)),
        _c("products", "string", "';'-joined compared products"),
        _c("members", "string", "';'-joined members", nullable=False),
        _c("missing_member_policy", "string", "Declared missing-member policy",
           nullable=False),
        _c("reference_variant", "string", "Reference variant", nullable=False,
           allowed=REFERENCE_VARIANTS),
        _c("extraction_method", "string", "Extraction method of the members", nullable=False),
        _c("footprint_set_id", "string", "Footprint set of the members", nullable=False),
        _c("n_sites", "int", "Stations with eligible days", nullable=False),
        _c("n_folds", "int", "Folds with eligible days"),
        _c("n_folds_fitted", "int", "Folds with fitted held-out weights"),
        _c("n_days_compared", "int", "Site-days of the frozen comparison cohort"),
        _c("comparison_id", "string", "Comparison of the frozen product cohort"),
        _c("cohort_membership_sha256", "string", "Fingerprint of that cohort"),
        _c("interpretation", "string", "How the scope may be read", nullable=False),
        _c("flags", "string", "';'-joined flags"),
    ),
    primary_key=("ensemble_id", "scope", "subset"),
)

ENSEMBLE_IMPROVEMENT = TableSchema(
    "ensemble_improvement", "1",
    "Held-out contrasts of the fitted ensemble against each unfitted product, taken from the "
    "step-11 contrasts on the frozen cohort. An improvement is 'supported' only when the "
    "interval excludes zero in the fitted ensemble's favour on held-out days.",
    (
        _ENSEMBLE_KEY,
        _key("scope", "string", "Fold design", allowed=ENSEMBLE_DESIGNS),
        _key("subset", "string", "'all' held-out days, or a declared month subset"),
        _key("site_id", "string", "Station (contrasts are station level)"),
        _key("metric", "string", "Contrasted metric"),
        _key("baseline_product", "string", "Product the fitted ensemble is compared with"),
        _key("block_unit", "string", "Resampling unit", allowed=BLOCK_UNITS),
        _key("block_length", "int", "Bootstrap block length; 0 = not resampled"),
        _key("interval_method", "string", "Interval method", allowed=INTERVAL_METHODS),
        _c("contrast_basis", "string", "value or absolute_value", nullable=False,
           allowed=CONTRAST_BASES),
        _c("estimate", "float", "metric(fitted) - metric(baseline) on held-out days"),
        _c("lower", "float", "Lower bound"),
        _c("upper", "float", "Upper bound"),
        _c("units", "string", "Units", nullable=False),
        _c("sign_meaning", "string", "How to read the sign", nullable=False),
        _c("status", "string", "Interval status", nullable=False, allowed=RANKING_STATUSES),
        _c("favoured_model", "string", "Product favoured by an interval excluding zero"),
        _c("held_out_improvement", "string", "supported, not_supported or not_assessed",
           nullable=False, allowed=IMPROVEMENT_STATUSES),
        _c("n_days", "int", "Held-out site-days compared", nullable=False),
        _c("flags", "string", "';'-joined flags"),
    ),
    primary_key=("ensemble_id", "scope", "subset", "site_id", "metric", "baseline_product",
                 "block_unit", "block_length", "interval_method"),
)

ANALYSIS_STATUS = TableSchema(
    "analysis_status", "1",
    "One row per workflow unit: what ran, what was reused, skipped or unavailable, and why.",
    (
        _key("stage", "string", "Workflow stage"),
        _key("unit_id", "string", "Unit identifier (analysis:ids)"),
        _c("analysis", "string", "Analysis kind", nullable=False),
        _c("status", "string", "Unit status", nullable=False, allowed=UNIT_STATUSES),
        _c("reason_code", "string", "Stable reason identifier"),
        _c("reason", "string", "Human-readable reason"),
        _c("cache_key", "string", "SHA-256 of everything the outputs depend on"),
        _c("run_id", "string", "Run that produced the outputs"),
        _c("finished_utc", "string", "When the unit finished (ISO 8601, UTC)"),
        _c("elapsed_s", "float", "Wall time", unit="s"),
        _c("n_outputs", "int", "Output files"),
    ),
    primary_key=("stage", "unit_id"),
)

SKIPPED_ANALYSES = TableSchema(
    "skipped_analyses", "1",
    "Every analysis, or part of one, that produced no result, with its reason.",
    (
        _c("source", "string", "workflow unit or analysis table", nullable=False,
           allowed=SKIP_SOURCES),
        _c("stage", "string", "Workflow stage"),
        _c("unit_id", "string", "Workflow unit"),
        _c("analysis", "string", "Analysis", nullable=False),
        _c("detail", "string", "Part of the analysis (variable, layer, design, ...)"),
        _c("status", "string", "Status as written by the analysis", nullable=False),
        _c("reason_code", "string", "Stable reason identifier"),
        _c("reason", "string", "Reason"),
        _c("table", "string", "Table the row was read from"),
        _c("n_rows", "int", "Rows of that table with this status and reason"),
    ),
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
                        REFERENCE_RANK_CHANGES, LANDSCAPE_CLASS_FRACTIONS,
                        LANDSCAPE_INDEX_SUMMARIES, LANDSCAPE_METHOD_DIFFERENCES,
                        REPRESENTATIVENESS_LABELS, SATELLITE_SUPPORT_ERRORS,
                        REPRESENTATIVENESS_AVAILABILITY, STATION_EVENTS, EVENT_RESPONSES,
                        EVENT_OVERLAPS, EVENT_CONDITIONS, ANOMALY_DAILY, ANOMALY_METRICS,
                        WATER_BALANCE_TERM_TOTALS, WATER_BALANCE_RESIDUALS, SCENARIO_RESULTS,
                        ENSEMBLE_DAYS, ENSEMBLE_PREDICTIONS, ENSEMBLE_FOLDS, ENSEMBLE_WEIGHTS,
                        ENSEMBLE_INNER_SCORES, ENSEMBLE_TRAINING_MEMBERSHIP, ENSEMBLE_SUMMARY,
                        ENSEMBLE_IMPROVEMENT, ANALYSIS_STATUS, SKIPPED_ANALYSES,
                        EXCLUSIONS)
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
