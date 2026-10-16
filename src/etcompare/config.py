"""Declarations of stations, products and inputs.

Each mandatory semantic in input_contract.md section 9 is a required field here.
Leaving one out raises :class:`~etcompare.errors.ContractError` naming the
configuration key; no field is filled in from data magnitude or filenames.
Optional fields left blank stay ``None`` (unknown).

Configuration files are YAML (see ``examples/us_utj.yaml``) and are loaded
with :func:`load_config`. Relative paths resolve against the file's directory.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

from etcompare.errors import ContractError

STATION_DAY_DEFINITIONS: Final = ("local_standard_time", "civil_time")
PRODUCT_DAY_DEFINITIONS: Final = ("local_standard_time", "civil_time", "utc", "unknown")
INTERVAL_LABELS: Final = ("start", "end")
TEMPORAL_SUPPORTS: Final = ("interpolated_daily", "overpass_only", "unknown")
FOOTPRINT_VALUE_SEMANTICS: Final = ("normalized_cell_weight", "cell_contribution", "density_per_m2")
REFERENCE_VARIANT_SOURCES: Final = ("et_raw", "et_corrected")
#: Reference variants built from those sources (decisions.md D-004; :mod:`etcompare.references`).
REFERENCE_VARIANTS: Final = ("et_raw", "et_corrected_measured", "et_corrected_inclusive")
RASTER_SCALING: Final = ("declared", "from_header")
EXTRACTION_METHODS: Final = (
    "footprint", "footprint_common_support", "pixel_window", "nearest_tower_pixel",
)
#: Kind of temporal weight used to aggregate per-timestep footprints (guide step 6).
#: ``measured_et_weighted`` uses the target station measurements, so comparisons with
#: it are sensitivity experiments. ``modeled_et_weighted`` covers reanalysis/land-model
#: ET (e.g. NLDAS); ``reference_et_weighted`` covers ETo/ETr.
TEMPORAL_WEIGHTINGS: Final = (
    "time_mean", "reference_et_weighted", "modeled_et_weighted", "measured_et_weighted",
    "unknown",
)
AGGREGATION_BASES: Final = ("original_contribution", "renormalized_per_timestep")
NEGATIVE_WEIGHT_POLICIES: Final = ("exclude_timestep", "reject_period", "keep")
GAP_POLICIES: Final = ("exclude_timestep", "reject_period")
NIGHT_POLICIES: Final = ("include", "exclude")
#: Cohort rules of a comparison (guide step 7; see :class:`ComparisonSpec`).
COHORT_KINDS: Final = (
    "common_date", "pairwise_model", "model_available", "paired_method", "paired_reference",
)

#: Periods that daily cohorts can be summed into (guide step 8; see :class:`PeriodAggregationSpec`).
PERIOD_TYPES: Final = ("day", "month", "growing_season", "year", "water_year")
#: Optional full-period estimators; never presented as observed totals.
ESTIMATION_METHODS: Final = ("mean_daily_scaling", "linear_interpolation",
                              "reference_et_fraction")
#: Estimators that impute daily values (and so need ``estimation_max_gap_days``).
IMPUTATION_METHODS: Final = ("linear_interpolation", "reference_et_fraction")
#: The three period products of guide step 8 (``etcompare.aggregation``).
TOTAL_PRODUCTS: Final = ("partial_sum", "complete_total", "estimated_total")
#: Supported crop-height units and their factor to centimetres.
HEIGHT_UNITS: Final = {"cm": 1.0, "m": 100.0}

#: Metrics computed when a metric set lists none (guide step 9; definitions in
#: ``etcompare.metrics``). Daily MAPE is deliberately not a default.
DEFAULT_METRICS: Final = (
    "mbe", "mae", "rmse", "bias_corrected_rmse", "percent_bias", "nrmse_mean",
    "pearson_r", "pearson_r_squared", "ols_slope", "ols_intercept",
)
#: Metrics computed only when listed. ``kge_2009`` also emits its three components.
OPTIONAL_METRICS: Final = ("nrmse_mean_percent", "nse", "kge_2009", "mape")
METRIC_IDS: Final = DEFAULT_METRICS + OPTIONAL_METRICS

#: Bootstrap interval methods (guide step 10; ``etcompare.bootstrap``).
INTERVAL_METHODS: Final = ("percentile", "basic")
#: Season-year boundaries that close a resampling segment.
SEGMENT_BOUNDARIES: Final = ("none", "year", "water_year")

#: Canonical unit of every daily ET column: depth per declared station day.
ET_UNIT: Final = "mm d-1"
#: Unit of a period total: depth summed over the period's station days.
ET_TOTAL_UNIT: Final = "mm"
_ET_UNIT_SYNONYMS: Final = frozenset(
    {"mm d-1", "mm/day", "mm/d", "mm day-1", "mm per day", "mm d^-1", "mm day^-1"}
)

_FIXED_OFFSET = re.compile(r"^UTC(?:[+-](?:0\d|1[0-4]):[0-5]\d)?$")
_METHOD_ID = re.compile(r"^[a-z][a-z0-9_]*$")


def normalize_et_units(
    value: object, *, input: str, field: str, config_key: str
) -> str:
    """Return :data:`ET_UNIT` for a declared daily-depth unit, else raise.

    No conversion is implemented: inputs in other units must be converted
    upstream or declared with a conversion in a later extension.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ContractError(
            f"ET units for column '{field}' not declared; magnitude is not evidence",
            input=input, field=field, config_key=config_key,
        )
    text = " ".join(str(value).strip().lower().split())
    if text in _ET_UNIT_SYNONYMS:
        return ET_UNIT
    if text == "mm":
        raise ContractError(
            f"ET units for column '{field}' declared as 'mm'; declare the depth per period, "
            "e.g. 'mm/day'",
            input=input, field=field, config_key=config_key,
        )
    raise ContractError(
        f"ET units '{value}' for column '{field}' are not supported; etcompare does not "
        "convert ET units. Supply mm/day values",
        input=input, field=field, config_key=config_key,
    )


def validate_timezone(tz: object, *, input: str, config_key: str) -> str:
    """Accept a fixed offset ``UTC±HH:MM`` or a loadable IANA zone name."""
    if tz is None or (isinstance(tz, str) and not tz.strip()):
        raise ContractError(
            f"{input}: timezone not declared", input=input, field="timezone",
            config_key=config_key,
        )
    text = str(tz).strip()
    if _FIXED_OFFSET.match(text):
        return text
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo(text)
    except Exception as exc:  # ZoneInfoNotFoundError, ValueError
        raise ContractError(
            f"{input}: timezone {text!r} is neither a fixed offset like 'UTC-07:00' nor a "
            "loadable IANA zone (install 'tzdata' on Windows for IANA names)",
            input=input, field="timezone", config_key=config_key,
        ) from exc
    return text


def _require(value: object, *, input: str, field: str, config_key: str) -> None:
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ContractError(
            f"{input}: {field} not declared", input=input, field=field, config_key=config_key
        )


def _choice(
    value: object, allowed: tuple[str, ...], *, input: str, field: str, config_key: str
) -> None:
    _require(value, input=input, field=field, config_key=config_key)
    if value not in allowed:
        raise ContractError(
            f"{input}: {field}={value!r} is not one of {list(allowed)}",
            input=input, field=field, config_key=config_key,
        )


def _frozen_map(m: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return MappingProxyType(dict(m or {}))


@dataclass(frozen=True)
class StationSpec:
    """A station and its measurement-day definition (contract sections 2-3)."""

    site_id: str
    timezone: str
    day_definition: str
    interval_label: str
    name: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    elevation_m: float | None = None
    land_cover: str | None = None

    def __post_init__(self) -> None:
        if not self.site_id or not str(self.site_id).strip():
            raise ContractError("station site_id not declared", field="site_id",
                                config_key="stations.<site_id>")
        who = f"station {self.site_id}"
        key = f"stations.{self.site_id}"
        object.__setattr__(
            self, "timezone",
            validate_timezone(self.timezone, input=who, config_key=f"{key}.timezone"),
        )
        _choice(self.day_definition, STATION_DAY_DEFINITIONS, input=who,
                field="day_definition", config_key=f"{key}.day_definition")
        _choice(self.interval_label, INTERVAL_LABELS, input=who,
                field="interval_label", config_key=f"{key}.interval_label")
        if self.latitude is not None and not -90 <= self.latitude <= 90:
            raise ContractError(f"{who}: latitude {self.latitude} outside [-90, 90]",
                                config_key=f"{key}.latitude")
        if self.longitude is not None and not -180 <= self.longitude <= 180:
            raise ContractError(f"{who}: longitude {self.longitude} outside [-180, 180]",
                                config_key=f"{key}.longitude")


@dataclass(frozen=True)
class ProductSpec:
    """An OpenET model product.

    ``day_definition`` and ``temporal_support`` are mandatory but may be
    ``"unknown"``; outputs from an ``unknown`` day definition are flagged
    ``day_alignment=unverified``.
    """

    model: str
    units: str
    day_definition: str
    temporal_support: str
    version: str = "unknown"
    is_ensemble: bool = False
    #: Raster scaling, needed only for raster extraction: ``"declared"`` (use
    #: ``scale_factor``/``add_offset``/``nodata`` below) or ``"from_header"``
    #: (GDAL band scale/offset/nodata; GDAL reports 1/0 when the header has no
    #: scale tags). ``None`` means unknown, and raster extraction refuses to run.
    raster_scaling: str | None = None
    scale_factor: float | None = None
    add_offset: float | None = None
    nodata: float | None = None

    def __post_init__(self) -> None:
        who = f"product {self.model}"
        key = f"products.{self.model}"
        _require(self.model, input="product", field="model", config_key="products.<model>")
        object.__setattr__(
            self, "units",
            normalize_et_units(self.units, input=who, field="ET", config_key=f"{key}.units"),
        )
        _choice(self.day_definition, PRODUCT_DAY_DEFINITIONS, input=who,
                field="day_definition", config_key=f"{key}.day_definition")
        _choice(self.temporal_support, TEMPORAL_SUPPORTS, input=who,
                field="temporal_support", config_key=f"{key}.temporal_support")
        if self.raster_scaling is not None:
            _choice(self.raster_scaling, RASTER_SCALING, input=who, field="raster_scaling",
                    config_key=f"{key}.raster_scaling")
        if self.raster_scaling == "declared":
            for f in ("scale_factor", "add_offset"):
                _require(getattr(self, f), input=who, field=f, config_key=f"{key}.{f}")

    def require_scaling(self) -> None:
        """Raise :class:`ContractError` unless raster scaling is declared."""
        if self.raster_scaling is None:
            raise ContractError(
                f"product {self.model}: raster scaling unknown; declare raster_scaling "
                "('declared' with scale_factor/add_offset/nodata, or 'from_header')",
                field="raster_scaling", config_key=f"products.{self.model}.raster_scaling",
            )


@dataclass(frozen=True)
class StationTableSpec:
    """Column mapping and units for a daily station table (contract section 4).

    ``reference_columns`` maps each reference source (``et_raw``,
    ``et_corrected``) to a column; ``units`` maps each of those columns to its
    declared unit. ``path`` may be ``None`` when a DataFrame is passed directly.
    """

    site_id: str
    date_column: str
    reference_columns: Mapping[str, str]
    gap_flag_column: str
    units: Mapping[str, str]
    path: Path | None = None
    date_format: str = "%Y-%m-%d"
    closure_factor_column: str | None = None
    #: Daily reference ET (e.g. gridMET ETr) used by ``reference_et_fraction`` imputation;
    #: its unit must be declared in ``units``.
    reference_et_column: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "reference_columns", _frozen_map(self.reference_columns))
        object.__setattr__(self, "units", _frozen_map(self.units))
        where = str(self.path) if self.path else "station table"
        if not self.site_id:
            raise ContractError(
                f"station table {where} has no site column; pass site_id=",
                input=where, field="site_id", config_key="station_tables[].site_id",
            )
        key = f"station_tables.{self.site_id}"
        _require(self.date_column, input=where, field="date_column",
                 config_key=f"{key}.date_column")
        unknown = set(self.reference_columns) - set(REFERENCE_VARIANT_SOURCES)
        if unknown:
            raise ContractError(
                f"unknown reference source(s) {sorted(unknown)}; expected "
                f"{list(REFERENCE_VARIANT_SOURCES)}", config_key=f"{key}.reference_columns",
            )
        for variant in REFERENCE_VARIANT_SOURCES:
            if not self.reference_columns.get(variant):
                raise ContractError(
                    f"reference variant '{variant}' has no source column", input=where,
                    field=variant, config_key=f"{key}.reference_columns.{variant}",
                )
        _require(self.gap_flag_column, input=where, field="gap_flag_column",
                 config_key=f"{key}.gap_flag_column")
        normalized = {}
        for col in (*self.reference_columns.values(),
                    *([self.reference_et_column] if self.reference_et_column else [])):
            normalized[col] = normalize_et_units(
                self.units.get(col), input=where, field=col, config_key=f"{key}.units.{col}"
            )
        object.__setattr__(self, "units", MappingProxyType(normalized))


@dataclass(frozen=True)
class ExtractedTableSpec:
    """Mapping for a pre-extracted OpenET table (contract section 5).

    ``value_columns`` maps each ET column to its extraction-method label and
    ``units`` maps each ET column to its declared unit. The footprint set and
    weighting method are declared, never read from filenames.
    """

    name: str
    site_column: str
    model_column: str
    date_column: str
    value_columns: Mapping[str, str]
    units: Mapping[str, str]
    footprint_set_id: str
    footprint_method: str
    path: Path | None = None
    weight_sum_column: str | None = None
    retained_weight_column: str | None = None
    retained_weight_denominator: str | None = None
    model_asset_column: str | None = None
    footprint_asset_column: str | None = None
    source_path_column: str | None = None
    weight_sum_tolerance: float = 1e-6

    def __post_init__(self) -> None:
        object.__setattr__(self, "value_columns", _frozen_map(self.value_columns))
        key = f"extracted_tables.{self.name}"
        where = str(self.path) if self.path else f"extracted table {self.name}"
        for f in ("site_column", "model_column", "date_column", "footprint_set_id",
                  "footprint_method"):
            _require(getattr(self, f), input=where, field=f, config_key=f"{key}.{f}")
        if not self.value_columns:
            raise ContractError(f"{where}: no ET value columns declared",
                                config_key=f"{key}.value_columns")
        for col, label in self.value_columns.items():
            if not label:
                raise ContractError(
                    f"column '{col}' needs an extraction_method label", input=where,
                    field=col, config_key=f"{key}.value_columns.{col}",
                )
            if not _METHOD_ID.match(label):
                raise ContractError(
                    f"extraction_method label {label!r} for column '{col}' must be snake_case",
                    config_key=f"{key}.value_columns.{col}",
                )
        labels = list(self.value_columns.values())
        if len(set(labels)) != len(labels):
            raise ContractError(f"{where}: extraction_method labels must be unique: {labels}",
                                config_key=f"{key}.value_columns")
        units = _frozen_map(self.units)
        object.__setattr__(self, "units", MappingProxyType({
            col: normalize_et_units(units.get(col), input=where, field=col,
                                    config_key=f"{key}.units.{col}")
            for col in self.value_columns
        }))
        if self.retained_weight_column and not self.retained_weight_denominator:
            raise ContractError(
                f"{where}: retained-weight column '{self.retained_weight_column}' needs a "
                "denominator label (e.g. 'normalized_truncated_footprint')",
                config_key=f"{key}.retained_weight_denominator",
            )
        if self.weight_sum_tolerance < 0:
            raise ContractError("weight_sum_tolerance must be >= 0",
                                config_key=f"{key}.weight_sum_tolerance")


@dataclass(frozen=True)
class OpenETRasterSource:
    """A directory of OpenET GeoTIFFs to index (discovery recurses by default)."""

    root: Path
    recursive: bool = True


@dataclass(frozen=True)
class FootprintSetSpec:
    """A footprint raster set. Weighting and value semantics are mandatory.

    Discovery does not recurse by default: footprint set A (top level) and set B
    (``weighted/``) share filenames, so recursing would mix sets (D-016).
    """

    set_id: str
    root: Path
    weighting_method: str
    value_semantics: str
    recursive: bool = False
    layers: tuple[str, ...] | None = None
    #: Optional table of original (pre-renormalization) domain capture per day,
    #: e.g. fluxfootprints ``<site>_daily_domain_results.csv``. ``domain_capture_column``
    #: names the column and ``domain_capture_units`` is ``percent`` or ``fraction``.
    domain_capture_csv: Path | None = None
    domain_capture_column: str | None = None
    domain_capture_units: str = "percent"
    domain_capture_date_column: str = "time"
    #: Controlled kind of temporal weight (:data:`TEMPORAL_WEIGHTINGS`). Kept apart
    #: from the free-text ``weighting_method`` so that an unverified label never
    #: becomes a claim. ``unknown`` is the default.
    temporal_weighting: str = "unknown"

    @property
    def target_dependence(self) -> str:
        """``yes`` when the weights use the target measurements, ``no``, or ``unknown``."""
        return {"measured_et_weighted": "yes", "unknown": "unknown"}.get(
            self.temporal_weighting, "no")

    def __post_init__(self) -> None:
        key = f"footprint_sets.{self.set_id}"
        _require(self.set_id, input="footprint set", field="set_id",
                 config_key="footprint_sets[].set_id")
        who = f"footprint set {self.set_id}"
        _choice(self.temporal_weighting, TEMPORAL_WEIGHTINGS, input=who,
                field="temporal_weighting", config_key=f"{key}.temporal_weighting")
        if not self.value_semantics:
            raise ContractError("footprint value semantics not declared", input=who,
                                field="value_semantics", config_key=f"{key}.value_semantics")
        _choice(self.value_semantics, FOOTPRINT_VALUE_SEMANTICS, input=who,
                field="value_semantics", config_key=f"{key}.value_semantics")
        _require(self.weighting_method, input=who, field="weighting_method",
                 config_key=f"{key}.weighting_method")
        if self.layers is not None:
            object.__setattr__(self, "layers", tuple(self.layers))
        if self.domain_capture_csv is not None:
            _require(self.domain_capture_column, input=who, field="domain_capture_column",
                     config_key=f"{key}.domain_capture_column")
            _choice(self.domain_capture_units, ("percent", "fraction"), input=who,
                    field="domain_capture_units", config_key=f"{key}.domain_capture_units")


@dataclass(frozen=True)
class RasterExtractionSpec:
    """Raster extraction run for one station (guide step 5).

    ``methods`` is a subset of :data:`EXTRACTION_METHODS`:

    - ``footprint``: footprint contribution transferred conservatively onto the
      native model grid; each model uses its own valid cells.
    - ``footprint_common_support``: as above, restricted to cells valid in
      every model available that day.
    - ``pixel_window``: unweighted mean of an odd ``n x n`` block of whole model
      pixels centred on the pixel containing the tower (``window_sizes``).
    - ``nearest_tower_pixel``: the model pixel containing the tower (diagnostic).

    ``negative_weight_tolerance`` is the largest negative-to-positive footprint
    mass ratio treated as round-off: those negatives are zeroed and reported.
    Anything larger rejects the footprint for that day.
    """

    name: str
    site_id: str
    methods: tuple[str, ...]
    footprint_set_id: str | None = None
    window_sizes: tuple[int, ...] = (3,)
    negative_weight_tolerance: float = 1e-9
    models: tuple[str, ...] | None = None
    #: Inclusive date-label period to extract (ISO dates); ``None`` means unbounded.
    start: str | None = None
    end: str | None = None

    def __post_init__(self) -> None:
        key = f"raster_extractions.{self.name}"
        object.__setattr__(self, "methods", tuple(self.methods))
        object.__setattr__(self, "window_sizes", tuple(self.window_sizes))
        if self.models is not None:
            object.__setattr__(self, "models", tuple(self.models))
        _require(self.site_id, input=key, field="site_id", config_key=f"{key}.site_id")
        if not self.methods:
            raise ContractError(f"{key}: no extraction methods declared",
                                config_key=f"{key}.methods")
        bad = set(self.methods) - set(EXTRACTION_METHODS)
        if bad:
            raise ContractError(f"{key}: unknown method(s) {sorted(bad)}; expected "
                                f"{list(EXTRACTION_METHODS)}", config_key=f"{key}.methods")
        if {"footprint", "footprint_common_support"} & set(self.methods) and \
                not self.footprint_set_id:
            raise ContractError(f"{key}: footprint methods need footprint_set_id",
                                config_key=f"{key}.footprint_set_id")
        if any(n < 1 or n % 2 == 0 for n in self.window_sizes):
            raise ContractError(f"{key}: window sizes must be odd and positive",
                                config_key=f"{key}.window_sizes")
        for f in ("start", "end"):
            v = getattr(self, f)
            if v is not None:
                import datetime as _dt

                try:
                    object.__setattr__(self, f, _dt.date.fromisoformat(str(v)).isoformat())
                except ValueError as exc:
                    raise ContractError(f"{key}: {f}={v!r} is not an ISO date",
                                        config_key=f"{key}.{f}") from exc
        if self.start and self.end and self.start > self.end:
            raise ContractError(f"{key}: start {self.start} is after end {self.end}",
                                config_key=f"{key}.start")
        if not 0 <= self.negative_weight_tolerance < 1:
            raise ContractError(f"{key}: negative_weight_tolerance must be in [0, 1)",
                                config_key=f"{key}.negative_weight_tolerance")


@dataclass(frozen=True)
class TemporalAggregationSpec:
    """How per-timestep footprints become one period footprint (guide step 6).

    ``W_i = sum_t a_t f_it / sum_t a_t`` over eligible timesteps, with
    ``a_t = s_t * d_t`` when ``include_duration`` (``d_t`` the interval length)
    and ``a_t = s_t`` otherwise. ``s_t`` is the temporal weight series (1 for
    ``time_mean``). Every choice is declared; none has a default:

    - ``basis``: ``original_contribution`` aggregates the cell contributions as
      modeled, so the aggregate keeps the original domain capture;
      ``renormalized_per_timestep`` divides each timestep by its own total
      first. The two need not agree.
    - ``negative_weight_policy``: what a negative ``s_t`` (e.g. negative ET at
      night) does: ``exclude_timestep``, ``reject_period`` or ``keep`` (which
      can produce negative cells, later rejected beyond round-off).
    - ``gap_policy``: a missing ``s_t`` or an unusable footprint timestep
      (no positive mass, negative cells): ``exclude_timestep`` or ``reject_period``.
    - ``night_policy``: ``include`` or ``exclude`` (needs a daytime flag).

    ``min_temporal_coverage`` is the smallest eligible duration / period
    duration accepted; ``None`` accepts any coverage (it is still reported).
    """

    method_id: str
    temporal_weighting: str
    include_duration: bool
    basis: str
    negative_weight_policy: str
    gap_policy: str
    night_policy: str
    min_temporal_coverage: float | None = None
    #: Name of the weight series ``s_t`` in the per-timestep input (required unless
    #: ``time_mean``), e.g. ``nldas_eto_mm_h`` or ``le_w_m2``.
    weight_column: str | None = None

    def __post_init__(self) -> None:
        key = f"footprint_aggregations.{self.method_id}"
        who = f"footprint aggregation {self.method_id}"
        if self.temporal_weighting != "time_mean" and not self.weight_column:
            raise ContractError(f"{who}: {self.temporal_weighting} needs weight_column",
                                config_key=f"{key}.weight_column")
        if self.temporal_weighting == "time_mean" and self.weight_column:
            raise ContractError(f"{who}: time_mean takes no weight_column",
                                config_key=f"{key}.weight_column")
        _require(self.method_id, input="footprint aggregation", field="method_id",
                 config_key="footprint_aggregations[].method_id")
        if not _METHOD_ID.match(str(self.method_id)):
            raise ContractError(f"{who}: method_id must be snake_case", config_key=key)
        _choice(self.temporal_weighting, TEMPORAL_WEIGHTINGS[:-1], input=who,
                field="temporal_weighting", config_key=f"{key}.temporal_weighting")
        if not isinstance(self.include_duration, bool):
            raise ContractError(f"{who}: include_duration must be declared true or false",
                                config_key=f"{key}.include_duration")
        for f, allowed in (("basis", AGGREGATION_BASES),
                           ("negative_weight_policy", NEGATIVE_WEIGHT_POLICIES),
                           ("gap_policy", GAP_POLICIES), ("night_policy", NIGHT_POLICIES)):
            _choice(getattr(self, f), allowed, input=who, field=f, config_key=f"{key}.{f}")
        m = self.min_temporal_coverage
        if m is not None and not 0 <= m <= 1:
            raise ContractError(f"{who}: min_temporal_coverage must be in [0, 1]",
                                config_key=f"{key}.min_temporal_coverage")

    @property
    def target_dependence(self) -> str:
        return "yes" if self.temporal_weighting == "measured_et_weighted" else "no"


@dataclass(frozen=True)
class ExtractionSide:
    """One extraction product in a comparison: method plus footprint set (``none`` if unused)."""

    extraction_method: str
    footprint_set_id: str

    @property
    def label(self) -> str:
        return f"{self.extraction_method}@{self.footprint_set_id}"


def _iso_date(value: object, *, key: str, field: str) -> str | None:
    if value is None:
        return None
    import datetime as _dt

    try:
        return _dt.date.fromisoformat(str(value)).isoformat()
    except ValueError as exc:
        raise ContractError(f"{key}: {field}={value!r} is not an ISO date",
                            config_key=f"{key}.{field}") from exc


@dataclass(frozen=True)
class ComparisonSpec:
    """A declared comparison and the cohort rule that fixes its sample (guide step 7).

    A candidate unit is a site/date/model. It is eligible when **every** declared
    side has a usable modeled value there (non-null, and passing
    ``min_retained_frac`` for footprint sides) and **every** declared reference
    variant has an observation that day. ``kind`` decides what else is frozen:

    - ``common_date``: all ``models`` on the same days (all-model rankings).
    - ``pairwise_model``: exactly two models on their common days (pairwise
      rankings; may use different days from the all-model cohort).
    - ``model_available``: each model on its own eligible days (coverage
      summaries only; one side and one variant).
    - ``paired_method``: two or more sides on the same days, per model, or
      across all models with ``common_models``.
    - ``paired_reference``: two or more reference variants on the same days, per
      model, or across all models with ``common_models``.

    ``min_retained_frac`` is a quality rule declared before scoring and applied
    identically to every model and footprint side; it never applies to sides
    whose footprint set is ``none``. ``sources`` names the configured inputs
    (``raster_extractions`` or ``extracted_tables``) that runners load.
    """

    comparison_id: str
    kind: str
    models: tuple[str, ...]
    reference_variants: tuple[str, ...]
    sides: tuple[ExtractionSide, ...]
    sources: tuple[str, ...] = ()
    common_models: bool = False
    min_retained_frac: float | None = None
    sites: tuple[str, ...] | None = None
    start: str | None = None
    end: str | None = None
    description: str | None = None

    @property
    def freeze_models(self) -> bool:
        """True when one set of days is shared by every model."""
        return self.kind in ("common_date", "pairwise_model") or self.common_models

    @property
    def ranking_scope(self) -> str:
        """``all_models``, ``pairwise`` or ``none``: which rankings this cohort can support."""
        return {"common_date": "all_models", "pairwise_model": "pairwise"}.get(self.kind, "none")

    def __post_init__(self) -> None:
        cid = self.comparison_id
        _require(cid, input="comparison", field="comparison_id",
                 config_key="comparisons[].comparison_id")
        key = f"comparisons.{cid}"
        if not _METHOD_ID.match(str(cid)):
            raise ContractError(f"{key}: comparison_id must be snake_case", config_key=key)
        _choice(self.kind, COHORT_KINDS, input=key, field="kind", config_key=f"{key}.kind")
        sides = tuple(s if isinstance(s, ExtractionSide) else ExtractionSide(
            s.get("extraction_method"), s.get("footprint_set_id")) for s in self.sides)
        object.__setattr__(self, "sides", sides)
        for f in ("models", "reference_variants", "sources"):
            object.__setattr__(self, f, tuple(getattr(self, f)))
        if self.sites is not None:
            object.__setattr__(self, "sites", tuple(self.sites))
        for f, values in (("models", self.models), ("reference_variants",
                          self.reference_variants), ("sides", sides)):
            if not values:
                raise ContractError(f"{key}: no {f} declared", config_key=f"{key}.{f}")
            if len(set(values)) != len(values):
                raise ContractError(f"{key}: duplicate {f}", config_key=f"{key}.{f}")
        for s in sides:
            for f in ("extraction_method", "footprint_set_id"):
                _require(getattr(s, f), input=key, field=f, config_key=f"{key}.sides[].{f}")
        bad = set(self.reference_variants) - set(REFERENCE_VARIANTS)
        if bad:
            raise ContractError(f"{key}: unknown reference variant(s) {sorted(bad)}; expected "
                                f"{list(REFERENCE_VARIANTS)}",
                                config_key=f"{key}.reference_variants")
        k, n_models, n_sides, n_vars = (self.kind, len(self.models), len(sides),
                                        len(self.reference_variants))
        rules = {
            "common_date": (n_models >= 2, "needs at least two models"),
            "pairwise_model": (n_models == 2, "needs exactly two models"),
            "model_available": (n_sides == 1 and n_vars == 1,
                                "takes exactly one side and one reference variant"),
            "paired_method": (n_sides >= 2, "needs at least two sides"),
            "paired_reference": (n_vars >= 2, "needs at least two reference variants"),
        }
        ok, why = rules[k]
        if not ok:
            raise ContractError(f"{key}: kind {k} {why}", config_key=key)
        if self.common_models and k not in ("paired_method", "paired_reference"):
            raise ContractError(f"{key}: common_models applies only to paired_method and "
                                f"paired_reference (kind {k} fixes it)",
                                config_key=f"{key}.common_models")
        m = self.min_retained_frac
        if m is not None and not 0 <= m <= 1:
            raise ContractError(f"{key}: min_retained_frac must be in [0, 1]",
                                config_key=f"{key}.min_retained_frac")
        object.__setattr__(self, "start", _iso_date(self.start, key=key, field="start"))
        object.__setattr__(self, "end", _iso_date(self.end, key=key, field="end"))
        if self.start and self.end and self.start > self.end:
            raise ContractError(f"{key}: start {self.start} is after end {self.end}",
                                config_key=f"{key}.start")


_MONTH_DAY = re.compile(r"^(\d\d)-(\d\d)$")


def _month_day(value: object, *, key: str) -> tuple[int, int]:
    """Parse ``MM-DD``; 02-29 is refused because it does not exist every year."""
    import datetime as _dt

    m = _MONTH_DAY.match(str(value)) if value is not None else None
    if m is None:
        raise ContractError(f"{key}: {value!r} is not a month-day 'MM-DD'", config_key=key)
    md = (int(m.group(1)), int(m.group(2)))
    if md == (2, 29):
        raise ContractError(f"{key}: 02-29 does not occur every year; use 02-28 or 03-01",
                            config_key=key)
    try:
        _dt.date(2001, *md)
    except ValueError as exc:
        raise ContractError(f"{key}: {value!r} is not a valid month-day", config_key=key) from exc
    return md


@dataclass(frozen=True)
class GrowingSeasonRule:
    """A month-day rule applied to every year (and every site, unless ``sites``).

    ``start`` and ``end`` are inclusive ``MM-DD``. When ``end`` falls before
    ``start`` the season crosses the year boundary and is labelled
    ``GS<start year>-<end year>``; otherwise ``GS<year>``.
    """

    start: str
    end: str
    sites: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        _month_day(self.start, key="growing_season_rule.start")
        _month_day(self.end, key="growing_season_rule.end")
        if self.sites is not None:
            object.__setattr__(self, "sites", tuple(self.sites))

    @property
    def crosses_year(self) -> bool:
        return _month_day(self.end, key="end") < _month_day(self.start, key="start")


@dataclass(frozen=True)
class GrowingSeasonWindow:
    """One declared growing season of one site (site/year metadata), inclusive dates."""

    site_id: str
    label: str
    start: str
    end: str

    def __post_init__(self) -> None:
        key = f"growing_season_windows.{self.site_id}.{self.label}"
        for f in ("site_id", "label"):
            _require(getattr(self, f), input=key, field=f, config_key=key)
        object.__setattr__(self, "start", _iso_date(self.start, key=key, field="start"))
        object.__setattr__(self, "end", _iso_date(self.end, key=key, field="end"))
        if self.start is None or self.end is None:
            raise ContractError(f"{key}: start and end are required", config_key=key)
        if self.start > self.end:
            raise ContractError(f"{key}: start {self.start} is after end {self.end}",
                                config_key=key)


@dataclass(frozen=True)
class CropHeightTableSpec:
    """A daily crop-height table with the crop model's growing season.

    ``season_start_column`` / ``season_end_column`` hold the season of each day's
    year (inclusive dates); they define the growing season. ``active_column``,
    when given, must be non-null exactly inside the season (checked on load).
    """

    name: str
    path: Path | None
    date_column: str
    height_column: str
    height_units: str
    season_start_column: str
    season_end_column: str
    site_id: str | None = None
    site_column: str | None = None
    active_column: str | None = None
    date_format: str = "%Y-%m-%d"

    def __post_init__(self) -> None:
        key = f"crop_height_tables.{self.name}"
        _require(self.name, input="crop height table", field="name",
                 config_key="crop_height_tables[].name")
        for f in ("date_column", "height_column", "season_start_column", "season_end_column"):
            _require(getattr(self, f), input=key, field=f, config_key=f"{key}.{f}")
        _choice(self.height_units, tuple(HEIGHT_UNITS), input=key, field="height_units",
                config_key=f"{key}.height_units")
        if (self.site_id is None) == (self.site_column is None):
            raise ContractError(f"{key}: declare exactly one of site_id and site_column",
                                config_key=f"{key}.site_id")


@dataclass(frozen=True)
class PeriodAggregationSpec:
    """How daily cohorts are summed into periods (guide step 8).

    Daily ET is depth per station day, so a period total is a **sum** of daily
    depths. Three products are reported (``etcompare.aggregation``): matched-day
    partial sums, complete-calendar totals (every calendar day of the period
    accepted), and, only when ``estimation_method`` is declared, estimated
    full-period totals. Partial sums are never scaled by default.

    - ``water_year_start_month``: first month of the water year (default 10, the
      USGS convention). The water year is labelled by the calendar year in which
      it ends (``WY2025`` = 2024-10-01 to 2025-09-30).
    - Growing seasons come from site/year metadata (``growing_season_windows``),
      a crop-height table (``growing_season_source``, the name of a
      ``crop_height_tables`` entry; its windows are attached at run time with
      :meth:`with_growing_seasons`) or an explicit rule (``growing_season_rule``).
      There is no default season.
    - Estimation (``estimated_total`` rows) is opt-in:

      - ``mean_daily_scaling`` scales each partial sum by expected/matched days.
      - ``linear_interpolation`` imputes each missing day by linear interpolation
        of daily ET between the nearest cohort days.
      - ``reference_et_fraction`` interpolates ET / reference ET linearly and
        multiplies by that day's reference ET (``reference_et_mm`` of the station
        table).

      Imputation fills only gaps of at most ``estimation_max_gap_days``
      consecutive missing days bracketed by cohort days on both sides; it never
      extrapolates. ``estimation_min_completeness`` is the minimum fraction of
      real cohort days in a period.
    - ``comparisons`` limits which declared comparisons runners aggregate (all
      when omitted).
    """

    aggregation_id: str
    periods: tuple[str, ...]
    comparisons: tuple[str, ...] | None = None
    water_year_start_month: int = 10
    growing_season_rule: GrowingSeasonRule | None = None
    growing_season_windows: tuple[GrowingSeasonWindow, ...] = ()
    growing_season_source: str | None = None
    estimation_method: str | None = None
    estimation_min_completeness: float | None = None
    estimation_max_gap_days: int | None = None
    description: str | None = None

    @property
    def imputes(self) -> bool:
        """True when estimated totals are built from imputed daily values."""
        return self.estimation_method in IMPUTATION_METHODS

    def with_growing_seasons(self, windows: tuple[GrowingSeasonWindow, ...] | list[
            GrowingSeasonWindow]) -> PeriodAggregationSpec:
        """A copy with the windows read from ``growing_season_source`` attached."""
        from dataclasses import replace

        if self.growing_season_source is None:
            raise ContractError(f"period_aggregations.{self.aggregation_id}: no "
                                "growing_season_source to attach windows to")
        return replace(self, growing_season_windows=tuple(windows))

    def __post_init__(self) -> None:
        aid = self.aggregation_id
        _require(aid, input="period aggregation", field="aggregation_id",
                 config_key="period_aggregations[].aggregation_id")
        key = f"period_aggregations.{aid}"
        if not _METHOD_ID.match(str(aid)):
            raise ContractError(f"{key}: aggregation_id must be snake_case", config_key=key)
        object.__setattr__(self, "periods", tuple(self.periods or ()))
        if not self.periods:
            raise ContractError(f"{key}: no periods declared", config_key=f"{key}.periods")
        if len(set(self.periods)) != len(self.periods):
            raise ContractError(f"{key}: duplicate periods", config_key=f"{key}.periods")
        for p in self.periods:
            _choice(p, PERIOD_TYPES, input=key, field="periods", config_key=f"{key}.periods")
        if self.comparisons is not None:
            object.__setattr__(self, "comparisons", tuple(self.comparisons))
        m = self.water_year_start_month
        if isinstance(m, bool) or not isinstance(m, int) or not 1 <= m <= 12:
            raise ContractError(f"{key}: water_year_start_month must be an integer 1-12",
                                config_key=f"{key}.water_year_start_month")
        rule = self.growing_season_rule
        if isinstance(rule, Mapping):
            _check_keys(rule, set(GrowingSeasonRule.__dataclass_fields__),
                        f"{key}.growing_season_rule")
            rule = GrowingSeasonRule(**rule)
            object.__setattr__(self, "growing_season_rule", rule)
        windows = []
        for w in self.growing_season_windows or ():
            if isinstance(w, Mapping):
                _check_keys(w, set(GrowingSeasonWindow.__dataclass_fields__),
                            f"{key}.growing_season_windows[]")
                w = GrowingSeasonWindow(**w)
            windows.append(w)
        object.__setattr__(self, "growing_season_windows", tuple(windows))
        source = self.growing_season_source
        if rule is not None and (windows or source):
            raise ContractError(f"{key}: declare one of growing_season_rule, "
                                "growing_season_windows or growing_season_source", config_key=key)
        if "growing_season" in self.periods and rule is None and not windows and not source:
            raise ContractError(
                f"{key}: growing_season requested but no season declared; give site/year "
                "growing_season_windows, a growing_season_source (crop-height table) or a "
                "growing_season_rule (there is no default season)",
                config_key=f"{key}.growing_season_rule")
        by_site: dict[str, list[GrowingSeasonWindow]] = {}
        for w in windows:
            by_site.setdefault(w.site_id, []).append(w)
        for site, ws in by_site.items():
            labels = [w.label for w in ws]
            if len(set(labels)) != len(labels):
                raise ContractError(f"{key}: duplicate growing-season label for {site}",
                                    config_key=f"{key}.growing_season_windows")
            ws = sorted(ws, key=lambda w: w.start)
            for a, b in zip(ws, ws[1:], strict=False):
                if b.start <= a.end:
                    raise ContractError(
                        f"{key}: growing seasons {a.label} and {b.label} of {site} overlap",
                        config_key=f"{key}.growing_season_windows")
        if self.estimation_method is not None:
            _choice(self.estimation_method, ESTIMATION_METHODS, input=key,
                    field="estimation_method", config_key=f"{key}.estimation_method")
            c = self.estimation_min_completeness
            if c is None or isinstance(c, bool) or not 0 < c <= 1:
                raise ContractError(
                    f"{key}: estimation_method needs estimation_min_completeness in (0, 1], "
                    "declared before scoring", config_key=f"{key}.estimation_min_completeness")
        elif self.estimation_min_completeness is not None:
            raise ContractError(f"{key}: estimation_min_completeness without estimation_method",
                                config_key=f"{key}.estimation_min_completeness")
        g = self.estimation_max_gap_days
        if self.imputes:
            if g is None or isinstance(g, bool) or not isinstance(g, int) or g < 1:
                raise ContractError(
                    f"{key}: {self.estimation_method} needs estimation_max_gap_days, a positive "
                    "integer declared before scoring", config_key=f"{key}.estimation_max_gap_days")
        elif g is not None:
            raise ContractError(f"{key}: estimation_max_gap_days applies only to "
                                f"{list(IMPUTATION_METHODS)}",
                                config_key=f"{key}.estimation_max_gap_days")


@dataclass(frozen=True)
class NearZeroTolerance:
    """Absolute near-zero tolerances for metric denominators, in the scored values' unit.

    - ``mean``: a sample whose ``|mean(O)|`` is at most this has a near-zero
      observed mean (equivalently a near-zero observed total ``sum(O)``);
      percent bias, normalized RMSE and the KGE mean ratio are then undefined.
    - ``std``: a series whose population standard deviation is at most this is
      treated as constant; correlation, regression, NSE and the KGE
      standard-deviation ratio are then undefined.

    The unit is ``mm d-1`` for daily pairs and ``mm`` for period totals.
    """

    mean: float
    std: float

    def check(self, key: str) -> None:
        for f in ("mean", "std"):
            v = getattr(self, f)
            if isinstance(v, bool) or not isinstance(v, int | float) or not v >= 0:
                raise ContractError(f"{key}.{f} must be a non-negative number",
                                    config_key=f"{key}.{f}")


@dataclass(frozen=True)
class MetricSpec:
    """Which performance metrics are computed and how undefinedness is decided (guide step 9).

    Metrics are computed by ``etcompare.metrics`` on a cohort's common mask only:
    daily paired rows and, per declared ``period_aggregations``, each period
    product (``partial_sum``, ``complete_total``, ``estimated_total``) separately.

    - ``metrics``: stable IDs from :data:`METRIC_IDS`; :data:`DEFAULT_METRICS`
      when omitted. Optional metrics (NSE, original KGE, MAPE, NRMSE x 100) are
      computed only when listed.
    - ``daily_tolerance`` (``mm d-1``) is mandatory; ``period_tolerance`` (``mm``)
      is mandatory when period products are scored. Both are declared before
      scoring; they decide only when a denominator is too close to zero.
    - ``comparisons`` limits which comparisons runners score (all when omitted).
    - ``cumulative_error`` writes the running sum of daily errors with gap flags.
    """

    metric_set_id: str
    daily_tolerance: NearZeroTolerance
    metrics: tuple[str, ...] = DEFAULT_METRICS
    period_tolerance: NearZeroTolerance | None = None
    daily: bool = True
    period_aggregations: tuple[str, ...] = ()
    period_products: tuple[str, ...] = ()
    period_types: tuple[str, ...] | None = None
    comparisons: tuple[str, ...] | None = None
    cumulative_error: bool = True
    description: str | None = None

    def __post_init__(self) -> None:
        mid = self.metric_set_id
        _require(mid, input="metric set", field="metric_set_id",
                 config_key="metric_sets[].metric_set_id")
        key = f"metric_sets.{mid}"
        if not _METHOD_ID.match(str(mid)):
            raise ContractError(f"{key}: metric_set_id must be snake_case", config_key=key)
        for f in ("metrics", "period_aggregations", "period_products"):
            object.__setattr__(self, f, tuple(getattr(self, f) or ()))
        for f in ("period_types", "comparisons"):
            if getattr(self, f) is not None:
                object.__setattr__(self, f, tuple(getattr(self, f)))
        if not self.metrics:
            object.__setattr__(self, "metrics", DEFAULT_METRICS)
        for f, values, allowed in (("metrics", self.metrics, METRIC_IDS),
                                   ("period_products", self.period_products, TOTAL_PRODUCTS),
                                   ("period_types", self.period_types or (), PERIOD_TYPES)):
            if len(set(values)) != len(values):
                raise ContractError(f"{key}: duplicate {f}", config_key=f"{key}.{f}")
            for v in values:
                _choice(v, allowed, input=key, field=f, config_key=f"{key}.{f}")
        for f in ("daily_tolerance", "period_tolerance"):
            tol = getattr(self, f)
            if isinstance(tol, Mapping):
                _check_keys(tol, {"mean", "std"}, f"{key}.{f}")
                given: dict[str, Any] = {k: tol.get(k) for k in ("mean", "std")}
                tol = NearZeroTolerance(**given)
                object.__setattr__(self, f, tol)
            if tol is not None:
                tol.check(f"{key}.{f}")
        if self.daily_tolerance is None:
            raise ContractError(f"{key}: daily_tolerance (mean, std in mm d-1) not declared; "
                                "near-zero denominators need a tolerance declared before scoring",
                                config_key=f"{key}.daily_tolerance")
        periods = bool(self.period_aggregations)
        if periods != bool(self.period_products):
            raise ContractError(f"{key}: declare period_aggregations and period_products together",
                                config_key=f"{key}.period_products")
        if periods and self.period_tolerance is None:
            raise ContractError(f"{key}: period_tolerance (mean, std in mm) not declared; "
                                "period totals need their own near-zero tolerance",
                                config_key=f"{key}.period_tolerance")
        if not periods and self.period_types is not None:
            raise ContractError(f"{key}: period_types without period_aggregations",
                                config_key=f"{key}.period_types")
        if not (self.daily or periods or self.cumulative_error):
            raise ContractError(f"{key}: nothing to compute", config_key=key)


def _positive_int(value: object, *, key: str, field: str, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ContractError(f"{key}: {field} must be an integer >= {minimum}, got {value!r}",
                            config_key=f"{key}.{field}")
    return value


@dataclass(frozen=True)
class BootstrapSpec:
    """Paired moving-block bootstrap intervals for one metric set (guide step 10).

    ``etcompare.bootstrap`` resamples whole cohort days (every model, side and
    reference variant with identical indices) in blocks of consecutive calendar
    days that never cross a missing day, a site or a ``segment_boundary``, and
    recomputes the metric set's metrics in every replicate.

    - ``block_lengths`` (daily samples) are sensitivity candidates, never a
      standard; 1 is the iid-day bootstrap. ``period_block_lengths`` (whole
      periods) resample period-total metrics; when empty, period metrics get no
      intervals.
    - ``seed``, ``n_replicates``, ``confidence_level`` and ``min_blocks`` (the
      minimum number of disjoint full-length blocks) are mandatory.
    - ``segment_boundary``: ``none``, ``year`` or ``water_year``
      (``water_year_start_month``) closes a segment at that boundary.
    - ``min_valid_fraction``: the share of replicates whose metric is defined
      below which no interval is given.
    - ``acf_max_lag``: lags of the residual autocorrelation diagnostic
      (default: the longest block length).
    - ``contrasts``: paired side, reference-variant and pairwise-model contrasts.
    """

    bootstrap_id: str
    metric_set: str
    block_lengths: tuple[int, ...]
    seed: int
    n_replicates: int
    confidence_level: float
    min_blocks: int
    segment_boundary: str
    interval_methods: tuple[str, ...] = ("percentile",)
    period_block_lengths: tuple[int, ...] = ()
    water_year_start_month: int = 10
    min_valid_fraction: float = 0.9
    acf_max_lag: int | None = None
    contrasts: bool = True
    comparisons: tuple[str, ...] | None = None
    description: str | None = None

    def __post_init__(self) -> None:
        bid = self.bootstrap_id
        _require(bid, input="bootstrap", field="bootstrap_id",
                 config_key="bootstrap[].bootstrap_id")
        key = f"bootstrap.{bid}"
        if not _METHOD_ID.match(str(bid)):
            raise ContractError(f"{key}: bootstrap_id must be snake_case", config_key=key)
        _require(self.metric_set, input=key, field="metric_set", config_key=f"{key}.metric_set")
        for f in ("block_lengths", "period_block_lengths", "interval_methods"):
            object.__setattr__(self, f, tuple(getattr(self, f) or ()))
        if self.comparisons is not None:
            object.__setattr__(self, "comparisons", tuple(self.comparisons))
        if not self.block_lengths:
            raise ContractError(f"{key}: no block_lengths declared (daily samples; candidates "
                                "such as 7, 14, 28 are compared, not adopted as standards)",
                                config_key=f"{key}.block_lengths")
        for f in ("block_lengths", "period_block_lengths", "interval_methods"):
            values = getattr(self, f)
            if len(set(values)) != len(values):
                raise ContractError(f"{key}: duplicate {f}", config_key=f"{key}.{f}")
        for f in ("block_lengths", "period_block_lengths"):
            for b in getattr(self, f):
                _positive_int(b, key=key, field=f)
        if not self.interval_methods:
            raise ContractError(f"{key}: no interval_methods", config_key=f"{key}.interval_methods")
        for m in self.interval_methods:
            _choice(m, INTERVAL_METHODS, input=key, field="interval_methods",
                    config_key=f"{key}.interval_methods")
        if self.seed is None:
            raise ContractError(f"{key}: seed not declared; intervals must be reproducible",
                                config_key=f"{key}.seed")
        _positive_int(self.seed, key=key, field="seed", minimum=0)
        if self.n_replicates is None:
            raise ContractError(f"{key}: n_replicates not declared",
                                config_key=f"{key}.n_replicates")
        _positive_int(self.n_replicates, key=key, field="n_replicates")
        if self.min_blocks is None:
            raise ContractError(f"{key}: min_blocks not declared (minimum disjoint full-length "
                                "blocks for an interval)", config_key=f"{key}.min_blocks")
        _positive_int(self.min_blocks, key=key, field="min_blocks")
        lvl = self.confidence_level
        if isinstance(lvl, bool) or not isinstance(lvl, int | float) or not 0 < lvl < 1:
            raise ContractError(f"{key}: confidence_level must be in (0, 1), got {lvl!r}",
                                config_key=f"{key}.confidence_level")
        v = self.min_valid_fraction
        if isinstance(v, bool) or not isinstance(v, int | float) or not 0 < v <= 1:
            raise ContractError(f"{key}: min_valid_fraction must be in (0, 1], got {v!r}",
                                config_key=f"{key}.min_valid_fraction")
        _choice(self.segment_boundary, SEGMENT_BOUNDARIES, input=key, field="segment_boundary",
                config_key=f"{key}.segment_boundary")
        wy = self.water_year_start_month
        if isinstance(wy, bool) or not isinstance(wy, int) or not 1 <= wy <= 12:
            raise ContractError(f"{key}: water_year_start_month must be 1..12",
                                config_key=f"{key}.water_year_start_month")
        if self.acf_max_lag is not None:
            _positive_int(self.acf_max_lag, key=key, field="acf_max_lag")

    @property
    def max_lag(self) -> int:
        """Largest lag of the residual autocorrelation diagnostic."""
        return self.acf_max_lag or max(self.block_lengths)


@dataclass(frozen=True)
class Config:
    """A complete analysis declaration."""

    stations: Mapping[str, StationSpec] = field(default_factory=dict)
    products: Mapping[str, ProductSpec] = field(default_factory=dict)
    model_aliases: Mapping[str, str] = field(default_factory=dict)
    site_aliases: Mapping[str, str] = field(default_factory=dict)
    station_tables: tuple[StationTableSpec, ...] = ()
    extracted_tables: tuple[ExtractedTableSpec, ...] = ()
    openet_rasters: tuple[OpenETRasterSource, ...] = ()
    footprint_sets: tuple[FootprintSetSpec, ...] = ()
    raster_extractions: tuple[RasterExtractionSpec, ...] = ()
    footprint_aggregations: tuple[TemporalAggregationSpec, ...] = ()
    comparisons: tuple[ComparisonSpec, ...] = ()
    period_aggregations: tuple[PeriodAggregationSpec, ...] = ()
    crop_height_tables: tuple[CropHeightTableSpec, ...] = ()
    metric_sets: tuple[MetricSpec, ...] = ()
    bootstrap: tuple[BootstrapSpec, ...] = ()
    output_dir: Path | None = None
    source_path: Path | None = None

    def __post_init__(self) -> None:
        for name in ("stations", "products", "model_aliases", "site_aliases"):
            object.__setattr__(self, name, _frozen_map(getattr(self, name)))
        for t in self.station_tables:
            if t.site_id not in self.stations:
                raise ContractError(
                    f"station table for {t.site_id} has no station declaration",
                    config_key=f"stations.{t.site_id}",
                )
        ids = [f.set_id for f in self.footprint_sets]
        if len(set(ids)) != len(ids):
            raise ContractError(f"duplicate footprint set_id in {ids}",
                                config_key="footprint_sets")
        for x in self.raster_extractions:
            key = f"raster_extractions.{x.name}"
            if x.site_id not in self.stations:
                raise ContractError(f"{key}: station {x.site_id} not declared",
                                    config_key=f"stations.{x.site_id}")
            if x.footprint_set_id and x.footprint_set_id not in ids:
                raise ContractError(f"{key}: footprint set {x.footprint_set_id} not declared",
                                    config_key="footprint_sets")
            unknown = set(x.models or ()) - set(self.products)
            if unknown:
                raise ContractError(f"{key}: models {sorted(unknown)} not declared",
                                    config_key="products")
        self._check_comparisons(ids)
        self._check_period_aggregations()
        self._check_metric_sets()
        self._check_bootstrap()

    def _check_bootstrap(self) -> None:
        bids = [b.bootstrap_id for b in self.bootstrap]
        if len(set(bids)) != len(bids):
            raise ContractError(f"duplicate bootstrap_id in {bids}", config_key="bootstrap")
        sets = {m.metric_set_id: m for m in self.metric_sets}
        cids = {c.comparison_id for c in self.comparisons}
        for b in self.bootstrap:
            key = f"bootstrap.{b.bootstrap_id}"
            ms = sets.get(b.metric_set)
            if ms is None:
                raise ContractError(f"{key}: metric_set {b.metric_set!r} not declared in "
                                    "metric_sets", config_key=f"{key}.metric_set")
            unknown = set(b.comparisons or ()) - cids
            if unknown:
                raise ContractError(f"{key}: comparisons {sorted(unknown)} not declared",
                                    config_key=f"{key}.comparisons")
            if ms.comparisons is not None and b.comparisons is not None:
                outside = set(b.comparisons) - set(ms.comparisons)
                if outside:
                    raise ContractError(f"{key}: comparisons {sorted(outside)} are not scored by "
                                        f"metric set {ms.metric_set_id}",
                                        config_key=f"{key}.comparisons")
            if b.period_block_lengths and not ms.period_products:
                raise ContractError(f"{key}: period_block_lengths given but metric set "
                                    f"{ms.metric_set_id} scores no period products",
                                    config_key=f"{key}.period_block_lengths")

    def bootstrap_spec(self, bootstrap_id: str) -> BootstrapSpec:
        for b in self.bootstrap:
            if b.bootstrap_id == bootstrap_id:
                return b
        raise KeyError(bootstrap_id)

    def _check_metric_sets(self) -> None:
        mids = [m.metric_set_id for m in self.metric_sets]
        if len(set(mids)) != len(mids):
            raise ContractError(f"duplicate metric_set_id in {mids}", config_key="metric_sets")
        cids = {c.comparison_id for c in self.comparisons}
        aggs = {a.aggregation_id: a for a in self.period_aggregations}
        for m in self.metric_sets:
            key = f"metric_sets.{m.metric_set_id}"
            for f, unknown, where in (
                    ("comparisons", set(m.comparisons or ()) - cids, "comparisons"),
                    ("period_aggregations", set(m.period_aggregations) - set(aggs),
                     "period_aggregations")):
                if unknown:
                    raise ContractError(f"{key}: {f} {sorted(unknown)} not declared in {where}",
                                        config_key=f"{key}.{f}")
            for p in m.period_products:
                if p == "estimated_total" and not any(
                        aggs[a].estimation_method for a in m.period_aggregations):
                    raise ContractError(f"{key}: estimated_total requested but no listed "
                                        "aggregation declares an estimation_method",
                                        config_key=f"{key}.period_products")

    def metric_set(self, metric_set_id: str) -> MetricSpec:
        for m in self.metric_sets:
            if m.metric_set_id == metric_set_id:
                return m
        raise KeyError(metric_set_id)

    def _check_period_aggregations(self) -> None:
        aids = [a.aggregation_id for a in self.period_aggregations]
        if len(set(aids)) != len(aids):
            raise ContractError(f"duplicate aggregation_id in {aids}",
                                config_key="period_aggregations")
        cids = {c.comparison_id for c in self.comparisons}
        crops = [t.name for t in self.crop_height_tables]
        if len(set(crops)) != len(crops):
            raise ContractError(f"duplicate crop height table name in {crops}",
                                config_key="crop_height_tables")
        for t in self.crop_height_tables:
            if t.site_id is not None and t.site_id not in self.stations:
                raise ContractError(f"crop_height_tables.{t.name}: station {t.site_id} not "
                                    "declared", config_key=f"stations.{t.site_id}")
        for a in self.period_aggregations:
            if a.growing_season_source is not None and a.growing_season_source not in crops:
                raise ContractError(
                    f"period_aggregations.{a.aggregation_id}: growing_season_source "
                    f"{a.growing_season_source!r} is not a declared crop_height_tables name",
                    config_key=f"period_aggregations.{a.aggregation_id}.growing_season_source")
            key = f"period_aggregations.{a.aggregation_id}"
            unknown = set(a.comparisons or ()) - cids
            if unknown:
                raise ContractError(f"{key}: comparisons {sorted(unknown)} not declared",
                                    config_key=f"{key}.comparisons")
            sites = {w.site_id for w in a.growing_season_windows} | set(
                (a.growing_season_rule.sites or ()) if a.growing_season_rule else ())
            unknown = sites - set(self.stations)
            if unknown:
                raise ContractError(f"{key}: growing-season sites {sorted(unknown)} not declared",
                                    config_key="stations")

    def crop_height_table(self, name: str) -> CropHeightTableSpec:
        for t in self.crop_height_tables:
            if t.name == name:
                return t
        raise KeyError(name)

    def period_aggregation(self, aggregation_id: str) -> PeriodAggregationSpec:
        for a in self.period_aggregations:
            if a.aggregation_id == aggregation_id:
                return a
        raise KeyError(aggregation_id)

    def _check_comparisons(self, footprint_set_ids: list[str]) -> None:
        cids = [c.comparison_id for c in self.comparisons]
        if len(set(cids)) != len(cids):
            raise ContractError(f"duplicate comparison_id in {cids}", config_key="comparisons")
        sources = {x.name for x in self.raster_extractions} | {
            t.name for t in self.extracted_tables}
        known_sets = set(footprint_set_ids) | {t.footprint_set_id for t in
                                               self.extracted_tables} | {"none"}
        for c in self.comparisons:
            key = f"comparisons.{c.comparison_id}"
            checks = (("models", set(c.models) - set(self.products), "products"),
                      ("sources", set(c.sources) - sources,
                       "raster_extractions / extracted_tables"),
                      ("sites", set(c.sites or ()) - set(self.stations), "stations"),
                      ("sides", {s.footprint_set_id for s in c.sides} - known_sets,
                       "footprint_sets"))
            for f, unknown, where in checks:
                if unknown:
                    raise ContractError(f"{key}: {f} {sorted(unknown)} not declared in {where}",
                                        config_key=f"{key}.{f}")

    def footprint_set(self, set_id: str) -> FootprintSetSpec:
        for f in self.footprint_sets:
            if f.set_id == set_id:
                return f
        raise KeyError(set_id)

    def comparison(self, comparison_id: str) -> ComparisonSpec:
        for c in self.comparisons:
            if c.comparison_id == comparison_id:
                return c
        raise KeyError(comparison_id)

    def model_registry(self):  # -> ModelRegistry (import deferred to avoid a cycle)
        from etcompare.registry import ModelRegistry

        return ModelRegistry.from_products(self.products, aliases=self.model_aliases)


def to_plain(obj: Any) -> Any:
    """JSON-friendly copy of a spec: dataclasses and mappings become dicts, paths strings."""
    from dataclasses import fields, is_dataclass

    if is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_plain(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, Mapping):
        return {str(k): to_plain(v) for k, v in obj.items()}
    if isinstance(obj, tuple | list):
        return [to_plain(v) for v in obj]
    if isinstance(obj, Path):
        return str(obj)
    return obj


# --------------------------------------------------------------------------- loading

_STATION_KEYS = {f for f in StationSpec.__dataclass_fields__} - {"site_id"}
_PRODUCT_KEYS = {f for f in ProductSpec.__dataclass_fields__} - {"model"}


def _check_keys(d: Mapping[str, Any], allowed: set[str], where: str) -> None:
    if not isinstance(d, Mapping):
        raise ContractError(f"{where} must be a mapping, got {type(d).__name__}",
                            config_key=where)
    extra = set(d) - allowed
    if extra:
        raise ContractError(f"unknown configuration key(s) {sorted(extra)}", config_key=where)


def _path(value: Any, base: Path | None) -> Path | None:
    if value is None:
        return None
    p = Path(str(value)).expanduser()
    return p if p.is_absolute() or base is None else base / p


def config_from_dict(data: Mapping[str, Any], *, base_dir: Path | None = None) -> Config:
    """Build a :class:`Config` from plain data (e.g. parsed YAML)."""
    top = {"stations", "products", "model_aliases", "site_aliases", "station_tables",
           "extracted_tables", "openet_rasters", "footprint_sets", "raster_extractions",
           "footprint_aggregations", "comparisons", "period_aggregations",
           "crop_height_tables", "metric_sets", "bootstrap", "output_dir"}
    # Top-level keys starting with "x-" only hold YAML anchors and are ignored.
    data = {k: v for k, v in data.items() if not str(k).startswith("x-")}
    _check_keys(data, top, "<root>")

    stations = {}
    for sid, body in (data.get("stations") or {}).items():
        _check_keys(body, _STATION_KEYS, f"stations.{sid}")
        stations[sid] = StationSpec(site_id=sid, **{k: body.get(k) for k in
                                    ("timezone", "day_definition", "interval_label")},
                                    **{k: v for k, v in body.items() if k not in
                                       ("timezone", "day_definition", "interval_label")})

    products = {}
    for model, body in (data.get("products") or {}).items():
        _check_keys(body, _PRODUCT_KEYS, f"products.{model}")
        products[model] = ProductSpec(
            model=model, units=body.get("units"), day_definition=body.get("day_definition"),
            temporal_support=body.get("temporal_support"),
            **{k: v for k, v in body.items()
               if k not in ("units", "day_definition", "temporal_support")},
        )

    st_fields = set(StationTableSpec.__dataclass_fields__)
    station_tables = []
    for i, body in enumerate(data.get("station_tables") or []):
        _check_keys(body, st_fields, f"station_tables[{i}]")
        kw = dict(body)
        kw["path"] = _path(kw.get("path"), base_dir)
        for req in ("site_id", "date_column", "gap_flag_column"):
            kw.setdefault(req, None)
        kw.setdefault("reference_columns", {})
        kw.setdefault("units", {})
        station_tables.append(StationTableSpec(**kw))

    ex_fields = set(ExtractedTableSpec.__dataclass_fields__)
    extracted = []
    for i, body in enumerate(data.get("extracted_tables") or []):
        _check_keys(body, ex_fields, f"extracted_tables[{i}]")
        kw = dict(body)
        kw["path"] = _path(kw.get("path"), base_dir)
        kw.setdefault("name", f"extracted_{i}")
        for req in ("site_column", "model_column", "date_column", "footprint_set_id",
                    "footprint_method"):
            kw.setdefault(req, None)
        kw.setdefault("value_columns", {})
        kw.setdefault("units", {})
        extracted.append(ExtractedTableSpec(**kw))

    rasters = []
    for i, body in enumerate(data.get("openet_rasters") or []):
        _check_keys(body, set(OpenETRasterSource.__dataclass_fields__), f"openet_rasters[{i}]")
        _require(body.get("root"), input="openet raster source", field="root",
                 config_key=f"openet_rasters[{i}].root")
        rasters.append(OpenETRasterSource(root=_path(body["root"], base_dir),  # type: ignore[arg-type]
                                          recursive=bool(body.get("recursive", True))))

    footprints = []
    for i, body in enumerate(data.get("footprint_sets") or []):
        _check_keys(body, set(FootprintSetSpec.__dataclass_fields__), f"footprint_sets[{i}]")
        _require(body.get("root"), input="footprint set", field="root",
                 config_key=f"footprint_sets[{i}].root")
        kw = dict(body)
        kw["root"] = _path(kw["root"], base_dir)
        kw["domain_capture_csv"] = _path(kw.get("domain_capture_csv"), base_dir)
        for req in ("set_id", "weighting_method", "value_semantics"):
            kw.setdefault(req, None)
        if kw.get("layers") is not None:
            kw["layers"] = tuple(kw["layers"])
        footprints.append(FootprintSetSpec(**kw))

    extractions = []
    for i, body in enumerate(data.get("raster_extractions") or []):
        _check_keys(body, set(RasterExtractionSpec.__dataclass_fields__),
                    f"raster_extractions[{i}]")
        kw = dict(body)
        kw.setdefault("name", f"raster_extraction_{i}")
        kw.setdefault("site_id", None)
        kw.setdefault("methods", ())
        extractions.append(RasterExtractionSpec(**kw))

    aggregations = []
    agg_fields = set(TemporalAggregationSpec.__dataclass_fields__)
    for i, body in enumerate(data.get("footprint_aggregations") or []):
        _check_keys(body, agg_fields, f"footprint_aggregations[{i}]")
        kw = {f: body.get(f) for f in ("method_id", "temporal_weighting", "include_duration",
                                       "basis", "negative_weight_policy", "gap_policy",
                                       "night_policy")}
        kw.update({k: v for k, v in body.items() if k not in kw})
        aggregations.append(TemporalAggregationSpec(**kw))
    agg_ids = [a.method_id for a in aggregations]
    if len(set(agg_ids)) != len(agg_ids):
        raise ContractError(f"duplicate footprint aggregation method_id in {agg_ids}",
                            config_key="footprint_aggregations")

    comparisons = []
    cmp_fields = set(ComparisonSpec.__dataclass_fields__)
    for i, body in enumerate(data.get("comparisons") or []):
        _check_keys(body, cmp_fields, f"comparisons[{i}]")
        kw = {f: body.get(f) for f in ("comparison_id", "kind")}
        kw.update({f: body.get(f) or () for f in ("models", "reference_variants", "sides")})
        kw.update({k: v for k, v in body.items() if k not in kw})
        for j, side in enumerate(kw["sides"]):
            _check_keys(side, set(ExtractionSide.__dataclass_fields__),
                        f"comparisons[{i}].sides[{j}]")
        comparisons.append(ComparisonSpec(**kw))

    period_aggs = []
    pa_fields = set(PeriodAggregationSpec.__dataclass_fields__)
    for i, body in enumerate(data.get("period_aggregations") or []):
        _check_keys(body, pa_fields, f"period_aggregations[{i}]")
        kw = {f: body.get(f) for f in ("aggregation_id", "periods")}
        kw.update({k: v for k, v in body.items() if k not in kw})
        period_aggs.append(PeriodAggregationSpec(**kw))

    crop_tables = []
    ch_fields = set(CropHeightTableSpec.__dataclass_fields__)
    for i, body in enumerate(data.get("crop_height_tables") or []):
        _check_keys(body, ch_fields, f"crop_height_tables[{i}]")
        kw = {f: body.get(f) for f in ("name", "path", "date_column", "height_column",
                                       "height_units", "season_start_column",
                                       "season_end_column")}
        kw.update({k: v for k, v in body.items() if k not in kw})
        kw["path"] = _path(kw.get("path"), base_dir)
        crop_tables.append(CropHeightTableSpec(**kw))

    metric_sets = []
    ms_fields = set(MetricSpec.__dataclass_fields__)
    for i, body in enumerate(data.get("metric_sets") or []):
        _check_keys(body, ms_fields, f"metric_sets[{i}]")
        kw = {f: body.get(f) for f in ("metric_set_id", "daily_tolerance")}
        kw.update({k: v for k, v in body.items() if k not in kw})
        metric_sets.append(MetricSpec(**kw))

    bootstraps = []
    bs_fields = set(BootstrapSpec.__dataclass_fields__)
    for i, body in enumerate(data.get("bootstrap") or []):
        _check_keys(body, bs_fields, f"bootstrap[{i}]")
        kw = {f: body.get(f) for f in ("bootstrap_id", "metric_set", "block_lengths", "seed",
                                       "n_replicates", "confidence_level", "min_blocks",
                                       "segment_boundary")}
        kw.update({k: v for k, v in body.items() if k not in kw})
        bootstraps.append(BootstrapSpec(**kw))

    return Config(
        stations=stations,
        products=products,
        model_aliases=dict(data.get("model_aliases") or {}),
        site_aliases=dict(data.get("site_aliases") or {}),
        station_tables=tuple(station_tables),
        extracted_tables=tuple(extracted),
        openet_rasters=tuple(rasters),
        footprint_sets=tuple(footprints),
        raster_extractions=tuple(extractions),
        footprint_aggregations=tuple(aggregations),
        comparisons=tuple(comparisons),
        period_aggregations=tuple(period_aggs),
        crop_height_tables=tuple(crop_tables),
        metric_sets=tuple(metric_sets),
        bootstrap=tuple(bootstraps),
        output_dir=_path(data.get("output_dir"), base_dir),
    )


def load_config(path: str | Path) -> Config:
    """Load and validate a YAML configuration file."""
    import yaml

    path = Path(path)
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    cfg = config_from_dict(data, base_dir=path.parent.resolve())
    object.__setattr__(cfg, "source_path", path.resolve())
    return cfg
