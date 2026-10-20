"""Declarations of stations, products and inputs.

Each mandatory semantic in input_contract.md section 9 is a required field here.
Leaving one out raises :class:`~etcompare.errors.ContractError` naming the
configuration key; no field is filled in from data magnitude or filenames.
Optional fields left blank stay ``None`` (unknown).

Configuration files are YAML (see ``examples/us_utj.yaml``) and are loaded
with :func:`load_config`. Relative paths resolve against the file's directory.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, ClassVar, Final

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
#: Same-site/model/day raster conflicts (D-021, D-069).
ON_CONFLICT_POLICIES: Final = ("error", "report", "collapse_identical")
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

#: The one declaration of which metrics may be ranked and in which direction (guide
#: step 11; ``etcompare.ranking``). Only metrics with role ``score`` appear here:
#: diagnostic (bias-corrected RMSE, MAPE), descriptive (OLS coefficients) and component
#: (KGE parts) metrics are never a ranking basis. ``closest_to_zero`` metrics are ranked
#: by their absolute value.
RANKING_DIRECTIONS: Final[Mapping[str, str]] = MappingProxyType({
    "mae": "lower", "rmse": "lower", "nrmse_mean": "lower", "nrmse_mean_percent": "lower",
    "pearson_r": "higher", "pearson_r_squared": "higher", "nse": "higher", "kge_2009": "higher",
    "mbe": "closest_to_zero", "percent_bias": "closest_to_zero",
})
DIRECTIONS: Final = ("lower", "higher", "closest_to_zero")
#: Network summaries of station metrics; ``equal_site_mean`` is the primary one.
NETWORK_SUMMARY_TYPES: Final = ("equal_site_mean", "observation_weighted_mean",
                                "sqrt_observation_weighted_mean", "pooled_rmse")
#: Multiplicity adjustments for formal contrast tests.
MULTIPLICITY_POLICIES: Final = ("holm", "bonferroni")

#: Where a diagnostic variable is read (guide step 12; ``etcompare.diagnostics``):
#: a ``station_daily`` column (``src_*`` source columns, D-020, or canonical ones) or an
#: ``extractions`` column of the cohort's side (e.g. ``fp_weight_retained_frac``).
DIAGNOSTIC_SOURCES: Final = ("station", "extraction")
#: How far a variable's meaning and units are established. ``unverified`` variables are
#: declared only to record why their diagnostic is ``not_available``; they are never used.
VARIABLE_STATUSES: Final = ("verified", "provisional", "unverified")
#: ``continuous``: binned by declared edges or training quantiles; ``direction``: degrees
#: clockwise from north, binned into sectors that wrap at 0/360.
VARIABLE_KINDS: Final = ("continuous", "direction")
#: Calendar conditions (meteorological seasons DJF/MAM/JJA/SON; month of year).
CALENDAR_CONDITIONS: Final = ("season", "month")
#: Metrics that may be summarised per bin (each bin needs only its own sample).
DIAGNOSTIC_METRICS: Final = ("mbe", "mae", "rmse", "bias_corrected_rmse", "percent_bias",
                             "pearson_r")

#: Landscape layers (guide step 13; ``etcompare.representativeness``): a categorical
#: land-cover map or a continuous vegetation index.
LAYER_KINDS: Final = ("categorical", "continuous")
#: How a layer's reference period is matched to a footprint day. ``previous_only`` never
#: uses information from after the day; the others can, and such rows are retrospective.
MATCHING_POLICIES: Final = ("nearest", "previous_only", "within_n_days")
#: Supports whose landscape is described: the footprint, and the fixed supports of D-029
#: (pixel window and nearest pixel of the model grid) and declared polygons.
LANDSCAPE_METHODS: Final = ("footprint", "pixel_window", "nearest_tower_pixel", "polygon")
#: Satellite-support sources (``etcompare.satellite``). Only authoritative product
#: metadata; scene-level cloud cover is never a clear-observation flag (D-011).
SUPPORT_KINDS: Final = ("per_pixel_flag", "per_site_flag", "per_site_count")
#: Binned acquisition-timing variables of the satellite-support analysis.
ACQUISITION_GAP_VARIABLES: Final = ("days_to_nearest_clear", "days_since_previous_clear",
                                    "enclosing_gap_days")

#: Station event logs (guide step 14; ``etcompare.adapters.event_log``). Only
#: ``management`` events can be analysed as events. ``instrument`` events (station visits,
#: program updates, sensor moves) and ``confounder`` events (e.g. weather notes) stay
#: visible as overlaps and are never analysed as treatments.
EVENT_CATEGORIES: Final = ("management", "instrument", "confounder")
#: Kinds of management event. A cutting is never an irrigation cutoff.
MANAGEMENT_KINDS: Final = ("cutting", "irrigation", "irrigation_cutoff", "planting", "other")
#: What an event's ``event_source`` is evidence of:
#: ``management_record``: a farm or management record (only when confirmed as such);
#: ``photo_observation``: a camera or site photo; ``remote_sensing_inference``: inferred
#: from satellite imagery (Planet, Sentinel); ``station_log``: the station team's record of
#: an action on the station (e.g. the logger a program update went to); ``unknown``: the
#: source is unlabelled. An unlabelled source is always ``unknown``.
EVIDENCE_CLASSES: Final = ("management_record", "photo_observation",
                           "remote_sensing_inference", "station_log", "unknown")
#: How a log timestamp becomes a station-day label. ``station_local_date``: the timestamp is
#: declared to be in the station's own timezone, and its calendar date is the station day.
#: No other rule exists, so a log in another timezone is refused rather than shifted.
EVENT_DAY_RULES: Final = ("station_local_date",)
#: Columns an event table must map (``end_timestamp`` is optional: without it an event
#: lasts one station day).
EVENT_COLUMNS: Final = ("event_id", "site", "timestamp", "event_type", "event_source",
                        "observer", "notes")
EVENT_OPTIONAL_COLUMNS: Final = ("end_timestamp",)
#: What happens to events whose date the log calls approximate.
APPROXIMATE_POLICIES: Final = ("flag", "exclude")
#: How a weather variable is summarised over an event window.
WINDOW_AGGREGATIONS: Final = ("mean", "sum")
#: Anomaly methods (``etcompare.anomalies``). ``held_out_climatology``: a day-of-year
#: climatology fitted on the other years and applied to the held-out year.
#: ``exploratory_moving_mean``: an in-sample centred moving mean; exploratory only, never a
#: claim of seasonal generalisation.
ANOMALY_METHODS: Final = ("held_out_climatology", "exploratory_moving_mean")
#: Hold-out unit of the climatology folds.
FOLD_UNITS: Final = ("year", "water_year")
#: Leap-day rule: 29 February takes 28 February's day of year in a 365-day calendar.
LEAP_DAY_RULES: Final = ("as_feb28",)

#: Held-out ensemble experiments (guide step 16; ``etcompare.ensemble``). Ensembles are built
#: after spatial extraction from per-member extracted values; none is a pixel-level ensemble.
#: ``leave_one_year_out``: temporal transfer, folds by ``fold_unit``; ``leave_one_site_out``:
#: spatial transfer, folds by station.
ENSEMBLE_DESIGNS: Final = ("leave_one_year_out", "leave_one_site_out")
#: ``require_all``: a day counts only when every member has a value; ``reweight_available``:
#: weights are renormalised over the available members (at least ``min_members``), and every
#: such row is reported as reweighted.
MISSING_MEMBER_POLICIES: Final = ("require_all", "reweight_available")
#: Unweighted combinations of the members under the declared eligibility rule.
ENSEMBLE_COMBINATIONS: Final = ("mean", "median")
#: Fitted weights: nonnegative, summing to one, least squares on training rows only.
ENSEMBLE_METHODS: Final = ("simplex_least_squares",)
#: ``shrink_to_equal``: w = (1 - alpha) * w_fit + alpha / K; alpha = 1 is the simple mean.
ENSEMBLE_REGULARISATIONS: Final = ("shrink_to_equal",)
#: Inner grouped-validation criterion for alpha.
ENSEMBLE_SELECTION_METRICS: Final = ("rmse",)
#: No feature transformation: weights act on the extracted ET values in mm d-1.
ENSEMBLE_PREPROCESSING: Final = ("none",)
#: Too little independent data: ``decline`` (no weights) or ``exploratory`` (in-sample
#: weights, labelled exploratory, never held-out predictions).
INSUFFICIENT_DATA_POLICIES: Final = ("decline", "exploratory")
#: Largest member count (the exact simplex solver enumerates member subsets).
MAX_ENSEMBLE_MEMBERS: Final = 12

#: Top-level configuration sections (``x-`` keys hold YAML anchors and are ignored).
CONFIG_SECTIONS: Final = (
    "stations", "products", "model_aliases", "site_aliases", "station_tables",
    "extracted_tables", "openet_rasters", "footprint_sets", "raster_extractions",
    "footprint_aggregations", "comparisons", "period_aggregations", "crop_height_tables",
    "metric_sets", "bootstrap", "rankings", "diagnostics", "representativeness",
    "satellite_support", "event_tables", "event_analyses", "anomalies", "water_balances",
    "scenarios", "ensembles", "footprint_stacks", "footprint_comparisons", "run", "report",
    "cache", "upstream", "decisions", "output_dir",
)
#: Field holding each entry's ID, per list section.
_SECTION_ID_FIELDS: Final[Mapping[str, str]] = MappingProxyType({
    "station_tables": "site_id", "extracted_tables": "name", "footprint_sets": "set_id",
    "raster_extractions": "name", "footprint_aggregations": "method_id",
    "comparisons": "comparison_id", "period_aggregations": "aggregation_id",
    "crop_height_tables": "name", "metric_sets": "metric_set_id",
    "bootstrap": "bootstrap_id", "rankings": "ranking_id", "diagnostics": "diagnostics_id",
    "representativeness": "representativeness_id",
    "satellite_support": "satellite_support_id", "event_tables": "name",
    "event_analyses": "event_analysis_id", "anomalies": "anomaly_id",
    "water_balances": "water_balance_id", "scenarios": "scenario_id",
    "ensembles": "ensemble_id", "footprint_stacks": "site_id",
    "footprint_comparisons": "comparison_id", "upstream": "name", "decisions": "ref",
})
#: Workflow stages of the command-line interface (guide step 17; ``etcompare.workflow``).
WORKFLOW_STAGES: Final = ("validate", "index", "extract", "analyze", "report")
#: Analyses that ``run.skip`` may switch off. A declared analysis runs unless listed; an
#: undeclared one is recorded as ``skipped`` (``not_declared``), never silently absent.
WORKFLOW_ANALYSES: Final = (
    "footprint_aggregation", "raster_extraction", "footprint_comparison", "cohorts",
    "periods", "metrics", "bootstrap", "rankings", "ranking_sensitivity", "diagnostics",
    "satellite_support", "events", "anomalies", "water_balance", "ensembles",
    "representativeness",
)
REPORT_FORMATS: Final = ("markdown", "html")
FIGURE_FORMATS: Final = ("png", "svg")
#: Identity of raster inputs in the cache key and run manifest: ``stat`` (path, size and
#: modification time; cheap) or ``sha256`` (content; slow on network drives).
RASTER_IDENTITIES: Final = ("stat", "sha256")
#: ``open``: a user decision is pending; ``provisional``: a value is in use until the
#: named evidence or decision arrives.
DECISION_STATUSES: Final = ("open", "provisional")
UPSTREAM_EVIDENCE: Final = ("verified", "user_provided", "code_inferred", "declared",
                            "unknown")
#: What a missing crop-height file does: stop the run, or leave out only the
#: growing-season periods (recorded as skipped).
CROP_HEIGHT_ON_MISSING: Final = ("error", "skip_growing_season")

#: Water-balance terms (guide step 15; ``etcompare.water_balance``). Every term must be
#: declared; a term without an input is ``unknown``, never zero.
WATER_BALANCE_TERMS: Final = ("precipitation", "irrigation", "capillary_rise", "et", "runoff",
                              "deep_percolation", "storage_change")
#: Sign of each term under the ``inflows_positive`` convention of the ``soil_zone`` equation.
WATER_BALANCE_SIGNS: Final[Mapping[str, int]] = MappingProxyType({
    "precipitation": 1, "irrigation": 1, "capillary_rise": 1, "et": -1, "runoff": -1,
    "deep_percolation": -1, "storage_change": -1,
})
#: Supported equations and their text (recorded in every output).
WATER_BALANCE_EQUATIONS: Final[Mapping[str, str]] = MappingProxyType({
    "soil_zone": "residual = precipitation + irrigation + capillary_rise - et - runoff "
                 "- deep_percolation - storage_change",
})
#: Inflows (precipitation, irrigation, capillary rise) add to the boundary's water; outflows
#: and a storage increase are subtracted.
SIGN_CONVENTIONS: Final = ("inflows_positive",)
#: ``measured``: an observation; ``modeled``: a gridded or modeled product (e.g. gridMET);
#: ``scenario``: an explicit assumption with a justification; ``unknown``: no input.
TERM_STATUSES: Final = ("measured", "modeled", "scenario", "unknown")
#: Where a term's values come from: a ``station_daily`` column, the station ET of a declared
#: reference variant (D-004), a separate table, a declared scenario value, or nothing.
TERM_SOURCES: Final = ("station_column", "station_et", "table", "scenario", "none")
#: ``flux``: a daily depth (mm d-1) summed over the period; ``storage_state``: a storage depth
#: (mm) at the end of each station day, differenced over the period.
TERM_QUANTITIES: Final = ("flux", "storage_state")
#: The area a term's values represent. ``boundary`` means the term was measured or derived
#: for exactly the declared boundary. Anything else differs from a polygon boundary (a point
#: boundary also matches ``station_point``) and is flagged as a support mismatch.
SPATIAL_SUPPORTS: Final = ("boundary", "station_point", "tower_footprint", "gridded_cell",
                           "model_pixel", "regional")
#: ``polygon``: a declared polygon (e.g. the tower field), with or without a known area;
#: ``point``: a point/depth-only column at the station, which has no area. A tower footprint
#: is never a boundary: it changes every day.
BOUNDARY_KINDS: Final = ("polygon", "point")
#: The four uncertainty kinds of guide step 15, each kept and labelled on its own.
#: ``sampling`` is the step-10 bootstrap (``etcompare.bootstrap``) and is never produced or
#: merged by scenarios.
UNCERTAINTY_CATEGORIES: Final = ("sampling", "measurement", "footprint_parameter",
                                 "method_choice")
#: Categories a declared scenario can have (``etcompare.scenarios``).
SCENARIO_CATEGORIES: Final = ("measurement", "footprint_parameter", "method_choice")
#: Distribution families of declared measurement scenarios and their parameters.
#: ``lognormal`` draws ``exp(N(mu, sigma))``.
DISTRIBUTION_FAMILIES: Final[Mapping[str, tuple[str, ...]]] = MappingProxyType({
    "normal": ("mean", "sd"), "lognormal": ("mu", "sigma"), "uniform": ("low", "high"),
    "triangular": ("low", "mode", "high"),
})
#: ``multiplicative``: the term is multiplied by the draw; ``additive``: the draw is added
#: (mm d-1 per day for flux terms, mm for a storage change).
PERTURBATION_MODES: Final = ("multiplicative", "additive")
#: ``systematic``: one draw per term and realization, applied to every day (a bias);
#: ``independent_daily``: an independent draw for each station day.
TEMPORAL_DEPENDENCES: Final = ("systematic", "independent_daily")
#: Dependence between the perturbed terms: none, or a declared Gaussian copula.
TERM_DEPENDENCES: Final = ("independent", "gaussian_copula")
#: When a storage state is recorded.
STATE_TIMES: Final = ("end_of_station_day",)

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
    """A directory of OpenET GeoTIFFs to index (discovery recurses by default).

    - ``processing_tags``: declared tags allowed after the API suffix of processed copies
      (e.g. ``buffer400m_clip``); any other trailing text is a parse error (D-069).
    - ``on_conflict``: ``error`` (default), ``report`` (held out until selected) or
      ``collapse_identical`` (byte-identical copies collapse to one file; differing
      copies are held out, D-069).
    """

    root: Path
    recursive: bool = True
    processing_tags: tuple[str, ...] = ()
    on_conflict: str = "error"

    def __post_init__(self) -> None:
        object.__setattr__(self, "processing_tags", tuple(self.processing_tags or ()))
        for t in self.processing_tags:
            if not re.fullmatch(r"[A-Za-z0-9]+(?:_[A-Za-z0-9]+)*", str(t)):
                raise ContractError(f"openet_rasters: processing tag {t!r} must be "
                                    "underscore-separated letters and digits",
                                    config_key="openet_rasters[].processing_tags")
        _choice(self.on_conflict, ON_CONFLICT_POLICIES, input="openet_rasters",
                field="on_conflict", config_key="openet_rasters[].on_conflict")


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
    #: Strict mode: an unreadable raster stops the extraction instead of being excluded.
    strict: bool = False

    def __post_init__(self) -> None:
        key = f"raster_extractions.{self.name}"
        if not isinstance(self.strict, bool):
            raise ContractError(f"{key}: strict must be true or false",
                                config_key=f"{key}.strict")
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
    #: ``error`` (default) or ``skip_growing_season`` when the file is absent.
    on_missing: str = "error"

    def __post_init__(self) -> None:
        key = f"crop_height_tables.{self.name}"
        _choice(self.on_missing, CROP_HEIGHT_ON_MISSING, input=key, field="on_missing",
                config_key=f"{key}.on_missing")
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


def _margins(value: object, metrics: Mapping[str, str], *, key: str,
             field: str) -> Mapping[str, float]:
    if value is None:
        return MappingProxyType({})
    if not isinstance(value, Mapping):
        raise ContractError(f"{key}: {field} must be a mapping metric -> margin",
                            config_key=f"{key}.{field}")
    out = {}
    for m, v in value.items():
        if m not in metrics:
            raise ContractError(f"{key}: {field} names {m!r}, which this ranking does not rank",
                                config_key=f"{key}.{field}.{m}")
        if (isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v)
                or not v > 0):
            raise ContractError(f"{key}: {field}.{m} must be a positive finite number, got "
                                f"{v!r}", config_key=f"{key}.{field}.{m}")
        out[str(m)] = float(v)
    return MappingProxyType(out)


@dataclass(frozen=True)
class RankingSpec:
    """Station-level model rankings, paired model contrasts and network summaries (step 11).

    ``etcompare.ranking`` ranks the models of one ranking cohort (``ranking_scope``
    ``all_models`` or ``pairwise``) inside each frozen context (site, temporal
    scale and period product, reference variant, extraction side), using the
    point estimates of ``metric_set`` and the shared-index replicates of
    ``bootstrap``. Nothing is redrawn and no metric is re-implemented.

    - ``metrics``: mapping metric -> direction. Every entry must be a score
      metric of the metric set and match :data:`RANKING_DIRECTIONS`.
    - ``equivalence_margins`` (daily pairs, in the metric's units) and
      ``period_equivalence_margins`` (period products): predeclared practical
      equivalence margins. A metric without a margin gets ``not_assessed``.
    - ``network_summaries``: :data:`NETWORK_SUMMARY_TYPES`; must include the
      primary ``equal_site_mean``.
    - ``formal_tests`` requires an explicit ``multiplicity`` policy; without
      formal tests every contrast is exploratory.
    - ``year_subsets``: runners also rank each calendar year of a listed cohort
      that spans several years, as separately built frozen cohorts.
    """

    ranking_id: str
    metric_set: str
    bootstrap: str
    metrics: Mapping[str, str]
    equivalence_margins: Mapping[str, float] = field(default_factory=dict)
    period_equivalence_margins: Mapping[str, float] = field(default_factory=dict)
    network_summaries: tuple[str, ...] = ("equal_site_mean",)
    formal_tests: bool = False
    multiplicity: str | None = None
    year_subsets: bool = False
    comparisons: tuple[str, ...] | None = None
    description: str | None = None

    def __post_init__(self) -> None:
        rid = self.ranking_id
        _require(rid, input="ranking", field="ranking_id", config_key="rankings[].ranking_id")
        key = f"rankings.{rid}"
        if not _METHOD_ID.match(str(rid)):
            raise ContractError(f"{key}: ranking_id must be snake_case", config_key=key)
        for f in ("metric_set", "bootstrap"):
            _require(getattr(self, f), input=key, field=f, config_key=f"{key}.{f}")
        if not isinstance(self.metrics, Mapping) or not self.metrics:
            raise ContractError(f"{key}: metrics must map each ranked metric to its direction "
                                f"({list(DIRECTIONS)})", config_key=f"{key}.metrics")
        for m, d in self.metrics.items():
            if m not in RANKING_DIRECTIONS:
                raise ContractError(
                    f"{key}: metric {m!r} cannot be ranked; only score metrics with a declared "
                    f"direction are rankable ({sorted(RANKING_DIRECTIONS)}). Diagnostic, "
                    "descriptive and component metrics (bias_corrected_rmse, mape, ols_*, "
                    "kge_2009_r/_alpha/_beta) are never a ranking basis",
                    config_key=f"{key}.metrics.{m}")
            _choice(d, DIRECTIONS, input=key, field=f"metrics.{m}",
                    config_key=f"{key}.metrics.{m}")
            if d != RANKING_DIRECTIONS[m]:
                raise ContractError(f"{key}: metric {m} is ranked {RANKING_DIRECTIONS[m]!r}, "
                                    f"not {d!r} (RANKING_DIRECTIONS)",
                                    config_key=f"{key}.metrics.{m}")
        object.__setattr__(self, "metrics", MappingProxyType(dict(self.metrics)))
        for f in ("equivalence_margins", "period_equivalence_margins"):
            object.__setattr__(self, f, _margins(getattr(self, f), self.metrics, key=key,
                                                 field=f))
        object.__setattr__(self, "network_summaries", tuple(self.network_summaries or ()))
        if self.comparisons is not None:
            object.__setattr__(self, "comparisons", tuple(self.comparisons))
        ns = self.network_summaries
        if len(set(ns)) != len(ns):
            raise ContractError(f"{key}: duplicate network_summaries",
                                config_key=f"{key}.network_summaries")
        for s in ns:
            _choice(s, NETWORK_SUMMARY_TYPES, input=key, field="network_summaries",
                    config_key=f"{key}.network_summaries")
        if "equal_site_mean" not in ns:
            raise ContractError(f"{key}: network_summaries must include equal_site_mean, the "
                                "primary network result", config_key=f"{key}.network_summaries")
        if "pooled_rmse" in ns and "rmse" not in self.metrics:
            raise ContractError(f"{key}: pooled_rmse needs rmse among the ranked metrics",
                                config_key=f"{key}.network_summaries")
        for f in ("formal_tests", "year_subsets"):
            if not isinstance(getattr(self, f), bool):
                raise ContractError(f"{key}: {f} must be true or false",
                                    config_key=f"{key}.{f}")
        if self.formal_tests:
            if self.multiplicity is None:
                raise ContractError(f"{key}: formal_tests needs an explicit multiplicity policy "
                                    f"({list(MULTIPLICITY_POLICIES)})",
                                    config_key=f"{key}.multiplicity")
            _choice(self.multiplicity, MULTIPLICITY_POLICIES, input=key, field="multiplicity",
                    config_key=f"{key}.multiplicity")
        elif self.multiplicity is not None:
            raise ContractError(f"{key}: multiplicity applies only with formal_tests: true; "
                                "without formal tests every contrast is exploratory",
                                config_key=f"{key}.multiplicity")

    def margin(self, metric: str, temporal_scale: str) -> float | None:
        """Declared equivalence margin of ``metric`` at ``temporal_scale`` (None: not declared)."""
        if temporal_scale == "daily":
            return self.equivalence_margins.get(metric)
        return self.period_equivalence_margins.get(metric)


def _edges(value: object, *, key: str, field: str, closed_range: tuple[float, float] | None = None
           ) -> tuple[float, ...]:
    """Strictly increasing bin edges (at least two); +-inf allowed at the ends only."""
    if isinstance(value, str | bytes) or not isinstance(value, list | tuple):
        raise ContractError(f"{key}: {field} must be a list of numbers",
                            config_key=f"{key}.{field}")
    out = []
    for v in value:
        if isinstance(v, bool) or not isinstance(v, int | float) or math.isnan(v):
            raise ContractError(f"{key}: {field} holds {v!r}, not a number",
                                config_key=f"{key}.{field}")
        out.append(float(v))
    if len(out) < 2:
        raise ContractError(f"{key}: {field} needs at least two values",
                            config_key=f"{key}.{field}")
    if any(b <= a for a, b in zip(out, out[1:], strict=False)):  # so +-inf only at the ends
        raise ContractError(f"{key}: {field} must be strictly increasing",
                            config_key=f"{key}.{field}")
    if closed_range is not None and (out[0] < closed_range[0] or out[-1] > closed_range[1]):
        raise ContractError(f"{key}: {field} must lie in [{closed_range[0]}, {closed_range[1]}]",
                            config_key=f"{key}.{field}")
    return tuple(out)


@dataclass(frozen=True)
class DiagnosticVariableSpec:
    """A conditioning variable of the step-12 diagnostics, declared before any error is seen.

    - ``source``/``column``: a ``station_daily`` column (station meteorology passes
      through as ``src_<name>``, D-020) or an ``extractions`` column of the
      cohort's side (footprint support diagnostics).
    - ``units`` and ``meaning`` are mandatory; nothing is inferred from magnitude.
    - ``status``: ``verified``, ``provisional`` (used, rows flagged) or
      ``unverified`` (declared only so its diagnostic reports ``not_available``
      with ``note`` as the reason).
    - Bins, optional (a variable without bins is only referenced, e.g. as closure
      metadata or a regression predictor): fixed ``edges`` (the default), or
      ``quantiles`` of the variable's own values on a declared training period
      ``training_start``..``training_end`` (never of errors). ``direction``
      variables use ``n_sectors`` equal sectors, the first centred on north.
    - ``measured_days_only``: bin only ``corrected_from_raw`` days (e.g. a closure
      ratio, which has no closure meaning on gap-filled days, D-004).
    """

    name: str
    source: str
    column: str
    units: str
    meaning: str
    status: str
    kind: str = "continuous"
    edges: tuple[float, ...] | None = None
    quantiles: tuple[float, ...] | None = None
    training_start: str | None = None
    training_end: str | None = None
    n_sectors: int | None = None
    measured_days_only: bool = False
    note: str | None = None

    @property
    def binned(self) -> bool:
        return self.edges is not None or self.quantiles is not None or self.n_sectors is not None

    def __post_init__(self) -> None:
        key = f"diagnostics.variables.{self.name}"
        _require(self.name, input="diagnostic variable", field="name", config_key=key)
        for f in ("source", "column", "units", "meaning", "status"):
            _require(getattr(self, f), input=key, field=f, config_key=f"{key}.{f}")
        _choice(self.source, DIAGNOSTIC_SOURCES, input=key, field="source",
                config_key=f"{key}.source")
        _choice(self.status, VARIABLE_STATUSES, input=key, field="status",
                config_key=f"{key}.status")
        _choice(self.kind, VARIABLE_KINDS, input=key, field="kind", config_key=f"{key}.kind")
        if self.status == "unverified" and not self.note:
            raise ContractError(f"{key}: an unverified variable needs a note giving the reason "
                                "its diagnostic is not available", config_key=f"{key}.note")
        if not isinstance(self.measured_days_only, bool):
            raise ContractError(f"{key}: measured_days_only must be true or false",
                                config_key=f"{key}.measured_days_only")
        if self.kind == "direction":
            if self.edges is not None or self.quantiles is not None:
                raise ContractError(f"{key}: a direction variable is binned by n_sectors, not by "
                                    "edges or quantiles", config_key=f"{key}.n_sectors")
            if self.n_sectors is not None:
                _positive_int(self.n_sectors, key=key, field="n_sectors", minimum=2)
                if 360 % self.n_sectors:
                    raise ContractError(f"{key}: n_sectors must divide 360",
                                        config_key=f"{key}.n_sectors")
            if self.units != "degree":
                raise ContractError(f"{key}: a direction variable must be in units 'degree' "
                                    "(clockwise from north)", config_key=f"{key}.units")
            return
        if self.n_sectors is not None:
            raise ContractError(f"{key}: n_sectors applies only to kind direction",
                                config_key=f"{key}.n_sectors")
        if self.edges is not None and self.quantiles is not None:
            raise ContractError(f"{key}: declare either fixed edges or training quantiles, not "
                                "both", config_key=f"{key}.edges")
        if self.edges is not None:
            object.__setattr__(self, "edges", _edges(self.edges, key=key, field="edges"))
            if self.training_start or self.training_end:
                raise ContractError(f"{key}: training_start/end apply only to quantiles",
                                    config_key=f"{key}.training_start")
        if self.quantiles is not None:
            object.__setattr__(self, "quantiles", _edges(self.quantiles, key=key,
                                                         field="quantiles",
                                                         closed_range=(0.0, 1.0)))
            for f in ("training_start", "training_end"):
                if not getattr(self, f):
                    raise ContractError(
                        f"{key}: quantile bins need a declared training period ({f}); bins are "
                        "never taken from the data being scored", config_key=f"{key}.{f}")
                object.__setattr__(self, f, _iso_date(getattr(self, f), key=key, field=f))
            if self.training_start > self.training_end:  # type: ignore[operator]
                raise ContractError(f"{key}: training_start is after training_end",
                                    config_key=f"{key}.training_start")


@dataclass(frozen=True)
class DiagnosticsSpec:
    """Closure, coverage and environmental diagnostics of declared comparisons (guide step 12).

    - ``metric_set``: tolerances and the metric list; ``metrics`` (a subset of it,
      from :data:`DIAGNOSTIC_METRICS`) are summarised per bin with
      ``etcompare.metrics.compute_metrics``.
    - ``min_bin_days``: a bin with fewer days has status ``sparse_bin`` and no value.
    - ``bootstrap``: optional; paired intervals per bin with the step-10 scheme
      (every block length side by side).
    - ``variables``: declared conditioning variables (:class:`DiagnosticVariableSpec`).
    - ``calendar``: calendar conditions (``season``, ``month``).
    - ``gap_filled_fraction_edges``: bins of the per-month gap-filled share of a
      cohort's days (a conditioning variable; None: not analysed).
    - ``provenance_subsets``: observation-dominated (``corrected_from_raw``) and
      gap-filled days compared with all accepted days of the same cohort.
    - ``closure`` / ``closure_metadata``: correction magnitude on
      ``corrected_from_raw`` days, plus the named declared station variables.
    - ``sensible_heat_variable``, ``net_radiation_variable``,
      ``wind_direction_variable``: variables of the process diagnostics; when
      None the diagnostic is reported ``not_available``.
    """

    diagnostics_id: str
    metric_set: str
    min_bin_days: int
    variables: Mapping[str, DiagnosticVariableSpec] = field(default_factory=dict)
    metrics: tuple[str, ...] = ("mbe", "mae", "rmse")
    bootstrap: str | None = None
    calendar: tuple[str, ...] = CALENDAR_CONDITIONS
    gap_filled_fraction_edges: tuple[float, ...] | None = None
    provenance_subsets: bool = True
    closure: bool = True
    closure_metadata: tuple[str, ...] = ()
    sensible_heat_variable: str | None = None
    net_radiation_variable: str | None = None
    wind_direction_variable: str | None = None
    comparisons: tuple[str, ...] | None = None
    description: str | None = None

    def __post_init__(self) -> None:
        did = self.diagnostics_id
        _require(did, input="diagnostics", field="diagnostics_id",
                 config_key="diagnostics[].diagnostics_id")
        key = f"diagnostics.{did}"
        if not _METHOD_ID.match(str(did)):
            raise ContractError(f"{key}: diagnostics_id must be snake_case", config_key=key)
        _require(self.metric_set, input=key, field="metric_set", config_key=f"{key}.metric_set")
        if self.min_bin_days is None:
            raise ContractError(f"{key}: min_bin_days not declared (bins with fewer days get "
                                "status sparse_bin, not a value)", config_key=f"{key}.min_bin_days")
        _positive_int(self.min_bin_days, key=key, field="min_bin_days")
        for f in ("metrics", "calendar", "closure_metadata"):
            object.__setattr__(self, f, tuple(getattr(self, f) or ()))
        if self.comparisons is not None:
            object.__setattr__(self, "comparisons", tuple(self.comparisons))
        if not self.metrics:
            raise ContractError(f"{key}: no metrics", config_key=f"{key}.metrics")
        for f, allowed in (("metrics", DIAGNOSTIC_METRICS), ("calendar", CALENDAR_CONDITIONS)):
            values = getattr(self, f)
            if len(set(values)) != len(values):
                raise ContractError(f"{key}: duplicate {f}", config_key=f"{key}.{f}")
            for v in values:
                _choice(v, allowed, input=key, field=f, config_key=f"{key}.{f}")
        variables = {}
        for name, v in (self.variables or {}).items():
            if not isinstance(v, DiagnosticVariableSpec):
                _check_keys(v, set(DiagnosticVariableSpec.__dataclass_fields__) - {"name"},
                            f"{key}.variables.{name}")
                kw: dict[str, Any] = {f: v.get(f) for f in ("source", "column", "units",
                                                            "meaning", "status")}
                kw.update({k: x for k, x in v.items() if k not in kw})
                v = DiagnosticVariableSpec(name=name, **kw)
            elif v.name != name:
                raise ContractError(f"{key}: variable {name!r} is declared as {v.name!r}",
                                    config_key=f"{key}.variables.{name}")
            if name in CALENDAR_CONDITIONS or name in ("reference_provenance",
                                                       "gap_filled_fraction"):
                raise ContractError(f"{key}: variable name {name!r} is reserved",
                                    config_key=f"{key}.variables.{name}")
            variables[name] = v
        object.__setattr__(self, "variables", MappingProxyType(variables))
        if self.gap_filled_fraction_edges is not None:
            object.__setattr__(self, "gap_filled_fraction_edges", _edges(
                self.gap_filled_fraction_edges, key=key, field="gap_filled_fraction_edges",
                closed_range=(0.0, 1.0)))
        for f in ("provenance_subsets", "closure"):
            if not isinstance(getattr(self, f), bool):
                raise ContractError(f"{key}: {f} must be true or false", config_key=f"{key}.{f}")
        for name in self.closure_metadata:
            v = variables.get(name)
            if v is None or v.source != "station":
                raise ContractError(f"{key}: closure_metadata {name!r} is not a declared station "
                                    "variable", config_key=f"{key}.closure_metadata")
        if self.closure_metadata and not self.closure:
            raise ContractError(f"{key}: closure_metadata given with closure: false",
                                config_key=f"{key}.closure_metadata")
        for f, kind in (("sensible_heat_variable", "continuous"),
                        ("net_radiation_variable", "continuous"),
                        ("wind_direction_variable", "direction")):
            name = getattr(self, f)
            if name is None:
                continue
            v = variables.get(name)
            if v is None:
                raise ContractError(f"{key}: {f} {name!r} is not a declared variable",
                                    config_key=f"{key}.{f}")
            if v.kind != kind:
                raise ContractError(f"{key}: {f} {name!r} must be of kind {kind}",
                                    config_key=f"{key}.{f}")
        sh = self.sensible_heat_variable
        if sh is not None:
            edges = variables[sh].edges
            if variables[sh].quantiles is not None or (edges is not None and 0.0 not in edges):
                raise ContractError(f"{key}: sensible_heat_variable {sh!r} needs fixed edges that "
                                    "include 0, so the negative daily-mean bin is explicit",
                                    config_key=f"{key}.variables.{sh}.edges")


def _iso_day(value: object, *, key: str, field: str) -> str:
    out = _iso_date(value, key=key, field=field)
    if out is None:
        raise ContractError(f"{key}: {field} not declared", config_key=f"{key}.{field}")
    return out


@dataclass(frozen=True)
class LayerAssetSpec:
    """One file of a landscape layer and the period it describes.

    A categorical map declares its ``vintage`` (year); its reference period is that
    calendar year unless ``reference_start``/``reference_end`` say otherwise. A
    continuous index declares the ``acquisition_date`` of its scene (reference period
    = that day). A filename date is never read as either.
    """

    path: Path
    vintage: int | None = None
    acquisition_date: str | None = None
    reference_start: str | None = None
    reference_end: str | None = None

    @property
    def period(self) -> tuple[str, str]:
        assert self.reference_start is not None and self.reference_end is not None
        return self.reference_start, self.reference_end


@dataclass(frozen=True)
class LandscapeLayerSpec:
    """A land-cover map or vegetation index used to describe footprints (guide step 13).

    Every semantic is declared; nothing is read from magnitude or filenames:

    - ``crs``: checked against every file header (a mismatch is an error).
    - ``nodata``: mandatory (``.nan`` when only NaN marks missing values). Contribution
      on nodata cells is reported, never redistributed.
    - categorical: a class ``legend`` (code -> name) or ``legend_csv`` (columns
      ``code,name``), and a ``vintage`` per asset. A class found in the data but not
      in the legend is an error. Classes are never resampled.
    - continuous: ``units``, ``scale_factor``, ``add_offset``, an ``acquisition_date``
      per asset and ``acquisition_date_source`` (where those dates come from).
    """

    layer_id: str
    kind: str
    source: str
    crs: str
    nodata: float
    assets: tuple[LayerAssetSpec, ...]
    legend: Mapping[int, str] | None = None
    legend_csv: Path | None = None
    units: str | None = None
    scale_factor: float | None = None
    add_offset: float | None = None
    acquisition_date_source: str | None = None
    note: str | None = None

    def __post_init__(self) -> None:
        lid = self.layer_id
        _require(lid, input="landscape layer", field="layer_id", config_key="layers.<id>")
        key = f"representativeness.layers.{lid}"
        if not _METHOD_ID.match(str(lid)):
            raise ContractError(f"{key}: layer_id must be snake_case", config_key=key)
        _choice(self.kind, LAYER_KINDS, input=key, field="kind", config_key=f"{key}.kind")
        for f in ("source", "crs"):
            _require(getattr(self, f), input=key, field=f, config_key=f"{key}.{f}")
        if self.nodata is None:
            raise ContractError(f"{key}: nodata not declared (use .nan when only NaN marks "
                                "missing values)", config_key=f"{key}.nodata")
        if isinstance(self.nodata, bool) or not isinstance(self.nodata, int | float):
            raise ContractError(f"{key}: nodata must be a number", config_key=f"{key}.nodata")
        object.__setattr__(self, "nodata", float(self.nodata))
        assets = []
        for i, a in enumerate(self.assets or ()):
            akey = f"{key}.assets[{i}]"
            if not isinstance(a, LayerAssetSpec):
                _check_keys(a, set(LayerAssetSpec.__dataclass_fields__), akey)
                _require(a.get("path"), input=akey, field="path", config_key=f"{akey}.path")
                a = LayerAssetSpec(**{**a, "path": Path(str(a["path"]))})
            assets.append(self._asset(a, akey))
        if not assets:
            raise ContractError(f"{key}: no assets declared", config_key=f"{key}.assets")
        spans = sorted(a.period for a in assets)
        for (s0, e0), (s1, _) in zip(spans, spans[1:], strict=False):
            if s1 <= e0:
                raise ContractError(f"{key}: asset reference periods overlap ({s0}..{e0} and "
                                    f"{s1}..); matching would be ambiguous",
                                    config_key=f"{key}.assets")
        object.__setattr__(self, "assets", tuple(assets))
        if self.kind == "categorical":
            if self.legend is None and self.legend_csv is None:
                raise ContractError(f"{key}: a categorical layer needs a class legend (legend or "
                                    "legend_csv)", config_key=f"{key}.legend")
            if self.legend is not None and self.legend_csv is not None:
                raise ContractError(f"{key}: declare legend or legend_csv, not both",
                                    config_key=f"{key}.legend")
            if self.legend is not None:
                legend = {}
                for code, name in dict(self.legend).items():
                    if isinstance(code, bool) or not isinstance(code, int) or not name:
                        raise ContractError(f"{key}: legend entries must be integer code -> name",
                                            config_key=f"{key}.legend")
                    legend[code] = str(name)
                if not legend:
                    raise ContractError(f"{key}: empty legend", config_key=f"{key}.legend")
                if not math.isnan(self.nodata) and int(self.nodata) in legend:
                    raise ContractError(f"{key}: nodata {self.nodata:g} is also a legend class",
                                        config_key=f"{key}.legend")
                object.__setattr__(self, "legend", MappingProxyType(legend))
            for f in ("units", "scale_factor", "add_offset", "acquisition_date_source"):
                if getattr(self, f) is not None:
                    raise ContractError(f"{key}: {f} applies to continuous layers only",
                                        config_key=f"{key}.{f}")
        else:
            if self.legend is not None or self.legend_csv is not None:
                raise ContractError(f"{key}: a continuous layer has no legend",
                                    config_key=f"{key}.legend")
            for f in ("units", "scale_factor", "add_offset", "acquisition_date_source"):
                v = getattr(self, f)
                if v is None or (isinstance(v, str) and not v.strip()):
                    raise ContractError(f"{key}: {f} not declared", config_key=f"{key}.{f}")
            for f in ("scale_factor", "add_offset"):
                v = getattr(self, f)
                if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v):
                    raise ContractError(f"{key}: {f} must be a finite number",
                                        config_key=f"{key}.{f}")

    def _asset(self, a: LayerAssetSpec, akey: str) -> LayerAssetSpec:
        if self.kind == "categorical":
            if a.acquisition_date is not None:
                raise ContractError(f"{akey}: a categorical map declares a vintage, not an "
                                    "acquisition_date", config_key=f"{akey}.vintage")
            if a.vintage is None:
                raise ContractError(f"{akey}: vintage not declared; a land-cover map without a "
                                    "vintage cannot be matched to footprint days",
                                    config_key=f"{akey}.vintage")
            _positive_int(a.vintage, key=akey, field="vintage", minimum=1900)
            start = _iso_date(a.reference_start, key=akey, field="reference_start") or \
                f"{a.vintage:04d}-01-01"
            end = _iso_date(a.reference_end, key=akey, field="reference_end") or \
                f"{a.vintage:04d}-12-31"
        else:
            if a.vintage is not None or a.reference_start or a.reference_end:
                raise ContractError(f"{akey}: a continuous index declares its acquisition_date "
                                    "only", config_key=f"{akey}.acquisition_date")
            if a.acquisition_date is None:
                raise ContractError(f"{akey}: acquisition_date not declared; a vegetation index "
                                    "without an acquisition date cannot be matched",
                                    config_key=f"{akey}.acquisition_date")
            start = end = _iso_day(a.acquisition_date, key=akey, field="acquisition_date")
        if start > end:
            raise ContractError(f"{akey}: reference_start is after reference_end",
                                config_key=f"{akey}.reference_start")
        return LayerAssetSpec(a.path, a.vintage, a.acquisition_date and start, start, end)


@dataclass(frozen=True)
class MatchingPolicySpec:
    """How a layer asset is matched to a footprint day (guide step 13).

    ``days_from_layer`` = day - reference_end for a past asset (> 0), 0 when the day lies
    in the asset's reference period, and day - reference_start for a future asset (< 0).

    - ``previous_only``: assets whose reference period ends on or before the day.
    - ``nearest``: the smallest ``|days_from_layer|``; ties go to the past asset.
    - ``within_n_days``: ``nearest`` among assets with ``|days_from_layer| <= n_days``.

    A row whose asset's reference period ends after the day uses later information and
    is labelled retrospective. A day without an admissible asset is ``not_available``.
    """

    policy: str
    n_days: int | None = None

    @property
    def policy_id(self) -> str:
        return f"within_{self.n_days}_days" if self.policy == "within_n_days" else self.policy

    def __post_init__(self) -> None:
        key = "representativeness.matching_policies"
        _choice(self.policy, MATCHING_POLICIES, input=key, field="policy",
                config_key=f"{key}[].policy")
        if self.policy == "within_n_days":
            if self.n_days is None:
                raise ContractError(f"{key}: within_n_days needs n_days",
                                    config_key=f"{key}[].n_days")
            _positive_int(self.n_days, key=key, field="n_days", minimum=0)
        elif self.n_days is not None:
            raise ContractError(f"{key}: n_days applies only to within_n_days",
                                config_key=f"{key}[].n_days")


@dataclass(frozen=True)
class PolygonLayerSpec:
    """A declared polygon (e.g. the tower field) whose landscape is described.

    ``crs`` is mandatory because a file may carry none; ``vintage`` is mandatory but
    may be ``unknown``. ``feature_id`` selects one feature by ``id_column``.
    """

    polygon_id: str
    path: Path
    crs: str
    id_column: str
    feature_id: str
    source: str
    vintage: str
    layer: str | None = None

    @property
    def method_id(self) -> str:
        return f"polygon_{self.polygon_id}"

    def __post_init__(self) -> None:
        key = f"representativeness.polygons.{self.polygon_id}"
        _require(self.polygon_id, input="polygon", field="polygon_id", config_key=key)
        if not _METHOD_ID.match(str(self.polygon_id)):
            raise ContractError(f"{key}: polygon_id must be snake_case", config_key=key)
        for f in ("path", "crs", "id_column", "feature_id", "source", "vintage"):
            _require(getattr(self, f), input=key, field=f, config_key=f"{key}.{f}")
        object.__setattr__(self, "feature_id", str(self.feature_id))
        object.__setattr__(self, "vintage", str(self.vintage))


@dataclass(frozen=True)
class RepresentativenessSpec:
    """Footprint-weighted landscape composition of one station (guide step 13).

    - ``footprint_sets``: declared footprint sets whose daily footprints are described.
    - ``layers``: land-cover maps and vegetation indices (:class:`LandscapeLayerSpec`).
    - ``matching_policies``: every declared policy is run and keyed separately; none
      is adopted.
    - ``contour_levels``: cumulative-contribution contours in (0, 1); contour rows are
      a separate product from full-domain rows and never replace them.
    - ``methods``: ``footprint`` plus the compared fixed supports (``pixel_window`` of
      ``window_sizes``, ``nearest_tower_pixel``, ``polygon``). Pixel supports live on
      the model grid of ``reference_grid_raster`` (checked against
      ``reference_grid_id`` when given).
    - ``fluxfootprints_labels``: optional ``{layers, radii, alpha}`` for the upstream
      HIGH/MEDIUM/LOW labels, stored as labels only.
    """

    representativeness_id: str
    site_id: str
    footprint_sets: tuple[str, ...]
    layers: Mapping[str, LandscapeLayerSpec]
    matching_policies: tuple[MatchingPolicySpec, ...]
    contour_levels: tuple[float, ...] = ()
    methods: tuple[str, ...] = ("footprint",)
    window_sizes: tuple[int, ...] = (3,)
    reference_grid_raster: Path | None = None
    reference_grid_id: str | None = None
    polygons: tuple[PolygonLayerSpec, ...] = ()
    fluxfootprints_labels: Mapping[str, Any] | None = None
    negative_weight_tolerance: float = 1e-9
    start: str | None = None
    end: str | None = None
    description: str | None = None

    def method_ids(self) -> list[str]:
        """Stable IDs of the compared fixed supports (D-029 naming)."""
        out: list[str] = []
        if "pixel_window" in self.methods:
            out += [f"pixel_window_{n}x{n}" for n in self.window_sizes]
        if "nearest_tower_pixel" in self.methods:
            out.append("nearest_tower_pixel")
        if "polygon" in self.methods:
            out += [p.method_id for p in self.polygons]
        return out

    def __post_init__(self) -> None:
        rid = self.representativeness_id
        _require(rid, input="representativeness", field="representativeness_id",
                 config_key="representativeness[].representativeness_id")
        key = f"representativeness.{rid}"
        if not _METHOD_ID.match(str(rid)):
            raise ContractError(f"{key}: representativeness_id must be snake_case",
                                config_key=key)
        _require(self.site_id, input=key, field="site_id", config_key=f"{key}.site_id")
        for f in ("footprint_sets", "methods", "window_sizes", "contour_levels"):
            object.__setattr__(self, f, tuple(getattr(self, f) or ()))
        if not self.footprint_sets:
            raise ContractError(f"{key}: no footprint_sets declared",
                                config_key=f"{key}.footprint_sets")
        layers = {}
        for name, v in (self.layers or {}).items():
            if not isinstance(v, LandscapeLayerSpec):
                _check_keys(v, set(LandscapeLayerSpec.__dataclass_fields__) - {"layer_id"},
                            f"{key}.layers.{name}")
                kw: dict[str, Any] = {f: v.get(f) for f in ("kind", "source", "crs", "nodata",
                                                            "assets")}
                kw.update({k: x for k, x in v.items() if k not in kw})
                kw["assets"] = tuple(kw["assets"] or ())
                if kw.get("legend_csv") is not None:
                    kw["legend_csv"] = Path(str(kw["legend_csv"]))
                v = LandscapeLayerSpec(layer_id=name, **kw)
            elif v.layer_id != name:
                raise ContractError(f"{key}: layer {name!r} is declared as {v.layer_id!r}",
                                    config_key=f"{key}.layers.{name}")
            layers[name] = v
        if not layers:
            raise ContractError(f"{key}: no layers declared", config_key=f"{key}.layers")
        object.__setattr__(self, "layers", MappingProxyType(layers))
        policies = []
        for p in self.matching_policies or ():
            if not isinstance(p, MatchingPolicySpec):
                if isinstance(p, str):
                    p = {"policy": p}
                _check_keys(p, set(MatchingPolicySpec.__dataclass_fields__),
                            f"{key}.matching_policies")
                p = MatchingPolicySpec(policy=p.get("policy"), n_days=p.get("n_days"))
            policies.append(p)
        if not policies:
            raise ContractError(f"{key}: no matching policy declared; how layer dates are "
                                "matched to footprint days must be explicit",
                                config_key=f"{key}.matching_policies")
        ids = [p.policy_id for p in policies]
        if len(set(ids)) != len(ids):
            raise ContractError(f"{key}: duplicate matching policies {ids}",
                                config_key=f"{key}.matching_policies")
        object.__setattr__(self, "matching_policies", tuple(policies))
        for lv in self.contour_levels:
            if isinstance(lv, bool) or not isinstance(lv, int | float) or not 0 < lv < 1:
                raise ContractError(f"{key}: contour level {lv!r} must lie in (0, 1)",
                                    config_key=f"{key}.contour_levels")
        if len(set(self.contour_levels)) != len(self.contour_levels):
            raise ContractError(f"{key}: duplicate contour levels",
                                config_key=f"{key}.contour_levels")
        object.__setattr__(self, "contour_levels",
                           tuple(sorted(float(x) for x in self.contour_levels)))
        bad = set(self.methods) - set(LANDSCAPE_METHODS)
        if bad or "footprint" not in self.methods:
            raise ContractError(f"{key}: methods must include footprint and come from "
                                f"{list(LANDSCAPE_METHODS)}; got {list(self.methods)}",
                                config_key=f"{key}.methods")
        if any(isinstance(n, bool) or not isinstance(n, int) or n < 1 or n % 2 == 0
               for n in self.window_sizes):
            raise ContractError(f"{key}: window sizes must be odd and positive",
                                config_key=f"{key}.window_sizes")
        if {"pixel_window", "nearest_tower_pixel"} & set(self.methods) and \
                self.reference_grid_raster is None:
            raise ContractError(f"{key}: pixel supports need reference_grid_raster (a model "
                                "raster whose grid defines the pixels)",
                                config_key=f"{key}.reference_grid_raster")
        polys = []
        for pg in self.polygons or ():
            if not isinstance(pg, PolygonLayerSpec):
                _check_keys(pg, set(PolygonLayerSpec.__dataclass_fields__), f"{key}.polygons")
                kw = {f: pg.get(f) for f in ("polygon_id", "path", "crs", "id_column",
                                             "feature_id", "source", "vintage")}
                kw.update({k: x for k, x in pg.items() if k not in kw})
                if kw["path"] is not None:
                    kw["path"] = Path(str(kw["path"]))
                pg = PolygonLayerSpec(**kw)
            polys.append(pg)
        object.__setattr__(self, "polygons", tuple(polys))
        if ("polygon" in self.methods) != bool(polys):
            raise ContractError(f"{key}: the polygon method and polygons must be declared "
                                "together", config_key=f"{key}.polygons")
        flux = self.fluxfootprints_labels
        if flux is not None:
            _check_keys(flux, {"layers", "radii", "alpha"}, f"{key}.fluxfootprints_labels")
            fl = tuple(flux.get("layers") or ())
            for name in fl:
                if name not in layers or layers[name].kind != "categorical":
                    raise ContractError(f"{key}: fluxfootprints_labels layer {name!r} is not a "
                                        "declared categorical layer",
                                        config_key=f"{key}.fluxfootprints_labels.layers")
            radii = tuple(float(r) for r in (flux.get("radii") or ()))
            alpha = flux.get("alpha")
            if not fl or not radii or any(r <= 0 for r in radii) or alpha is None \
                    or not 0 < float(alpha) < 1:
                raise ContractError(f"{key}: fluxfootprints_labels needs layers, positive radii "
                                    "and alpha in (0, 1)",
                                    config_key=f"{key}.fluxfootprints_labels")
            object.__setattr__(self, "fluxfootprints_labels", MappingProxyType(
                {"layers": fl, "radii": radii, "alpha": float(alpha)}))
        if not 0 <= self.negative_weight_tolerance < 1:
            raise ContractError(f"{key}: negative_weight_tolerance must be in [0, 1)",
                                config_key=f"{key}.negative_weight_tolerance")
        object.__setattr__(self, "start", _iso_date(self.start, key=key, field="start"))
        object.__setattr__(self, "end", _iso_date(self.end, key=key, field="end"))
        if self.start and self.end and self.start > self.end:
            raise ContractError(f"{key}: start {self.start} is after end {self.end}",
                                config_key=f"{key}.start")


@dataclass(frozen=True)
class SupportSourceSpec:
    """Authoritative clear-observation metadata for one or more models (guide step 13).

    A table (CSV or Parquet) with ``site_column``, ``date_column`` and ``value_column``
    (and ``model_column`` unless the flag applies to every listed model). ``kind``:

    - ``per_pixel_flag`` / ``per_site_flag``: boolean; True = clear observation that
      day, False = no clear observation (the value is interpolated).
    - ``per_site_count``: count of clear observations; ``min_clear_count`` (declared)
      makes a day clear.

    ``authority`` (the product metadata the flags come from) and ``meaning`` are
    mandatory. A day absent from the table has unknown support: missing rows are not
    cloud evidence, filename and scene dates are labels, and a non-null interpolated ET
    value is not a clear observation. Scene-level cloud cover is not a flag (D-011).
    """

    source_id: str
    kind: str
    path: Path
    models: tuple[str, ...]
    authority: str
    meaning: str
    site_column: str
    date_column: str
    value_column: str
    model_column: str | None = None
    min_clear_count: int | None = None

    def __post_init__(self) -> None:
        key = f"satellite_support.sources.{self.source_id}"
        _require(self.source_id, input="support source", field="source_id", config_key=key)
        if self.kind in ("scene_cloud_cover", "scene_metadata"):
            raise ContractError(f"{key}: scene-level cloud cover is a labelled scene diagnostic, "
                                "never a clear-observation flag (D-011)",
                                config_key=f"{key}.kind")
        _choice(self.kind, SUPPORT_KINDS, input=key, field="kind", config_key=f"{key}.kind")
        for f in ("path", "authority", "meaning", "site_column", "date_column", "value_column"):
            _require(getattr(self, f), input=key, field=f, config_key=f"{key}.{f}")
        object.__setattr__(self, "models", tuple(self.models or ()))
        if not self.models:
            raise ContractError(f"{key}: no models declared", config_key=f"{key}.models")
        if self.kind == "per_site_count":
            if self.min_clear_count is None:
                raise ContractError(f"{key}: per_site_count needs min_clear_count",
                                    config_key=f"{key}.min_clear_count")
            _positive_int(self.min_clear_count, key=key, field="min_clear_count")
        elif self.min_clear_count is not None:
            raise ContractError(f"{key}: min_clear_count applies to per_site_count only",
                                config_key=f"{key}.min_clear_count")


@dataclass(frozen=True)
class SatelliteSupportSpec:
    """Errors on clear-observation versus interpolated days of frozen cohorts (guide step 13).

    - ``metric_set`` / ``metrics`` (from :data:`DIAGNOSTIC_METRICS`): metrics come from
      ``etcompare.metrics.compute_metrics``.
    - ``min_subset_days``: a subset or bin with fewer days has a status, not a value.
    - ``sources``: :class:`SupportSourceSpec`; each model needs at most one source.
    - ``gap_variables``: optional ``{name: edges}`` for :data:`ACQUISITION_GAP_VARIABLES`
      (bins from the configuration, never from errors). ``days_to_nearest_clear`` and
      ``enclosing_gap_days`` use later acquisitions and are retrospective.
    """

    satellite_support_id: str
    metric_set: str
    min_subset_days: int
    sources: tuple[SupportSourceSpec, ...] = ()
    metrics: tuple[str, ...] = ("mbe", "mae", "rmse")
    gap_variables: Mapping[str, tuple[float, ...]] = field(default_factory=dict)
    comparisons: tuple[str, ...] | None = None
    description: str | None = None

    def __post_init__(self) -> None:
        sid = self.satellite_support_id
        _require(sid, input="satellite support", field="satellite_support_id",
                 config_key="satellite_support[].satellite_support_id")
        key = f"satellite_support.{sid}"
        if not _METHOD_ID.match(str(sid)):
            raise ContractError(f"{key}: satellite_support_id must be snake_case",
                                config_key=key)
        _require(self.metric_set, input=key, field="metric_set", config_key=f"{key}.metric_set")
        if self.min_subset_days is None:
            raise ContractError(f"{key}: min_subset_days not declared",
                                config_key=f"{key}.min_subset_days")
        _positive_int(self.min_subset_days, key=key, field="min_subset_days")
        object.__setattr__(self, "metrics", tuple(self.metrics or ()))
        if not self.metrics or len(set(self.metrics)) != len(self.metrics):
            raise ContractError(f"{key}: metrics must be a non-empty list without duplicates",
                                config_key=f"{key}.metrics")
        for m in self.metrics:
            _choice(m, DIAGNOSTIC_METRICS, input=key, field="metrics", config_key=f"{key}.metrics")
        if self.comparisons is not None:
            object.__setattr__(self, "comparisons", tuple(self.comparisons))
        sources = []
        for s in self.sources or ():
            if not isinstance(s, SupportSourceSpec):
                _check_keys(s, set(SupportSourceSpec.__dataclass_fields__), f"{key}.sources")
                kw = {f: s.get(f) for f in ("source_id", "kind", "path", "models", "authority",
                                            "meaning", "site_column", "date_column",
                                            "value_column")}
                kw.update({k: x for k, x in s.items() if k not in kw})
                if kw["path"] is not None:
                    kw["path"] = Path(str(kw["path"]))
                s = SupportSourceSpec(**kw)
            sources.append(s)
        seen: dict[str, str] = {}
        for s in sources:
            for m in s.models:
                if m in seen:
                    raise ContractError(f"{key}: model {m} has two support sources "
                                        f"({seen[m]}, {s.source_id})",
                                        config_key=f"{key}.sources")
                seen[m] = s.source_id
        object.__setattr__(self, "sources", tuple(sources))
        gaps = {}
        for name, edges in (self.gap_variables or {}).items():
            _choice(name, ACQUISITION_GAP_VARIABLES, input=key, field="gap_variables",
                    config_key=f"{key}.gap_variables")
            gaps[name] = _edges(edges, key=key, field=f"gap_variables.{name}")
            if gaps[name][0] < 0:
                raise ContractError(f"{key}: gap_variables.{name} edges must be >= 0 days",
                                    config_key=f"{key}.gap_variables.{name}")
        object.__setattr__(self, "gap_variables", MappingProxyType(gaps))


@dataclass(frozen=True)
class EventTypeSpec:
    """How one source ``event_type`` value is treated (guide step 14).

    ``category`` is one of :data:`EVENT_CATEGORIES`. Management types also declare their
    ``management_kind`` (:data:`MANAGEMENT_KINDS`); other categories must not.
    """

    category: str
    management_kind: str | None = None

    def check(self, key: str) -> None:
        _choice(self.category, EVENT_CATEGORIES, input=key, field="category",
                config_key=f"{key}.category")
        if self.category == "management":
            _choice(self.management_kind, MANAGEMENT_KINDS, input=key, field="management_kind",
                    config_key=f"{key}.management_kind")
        elif self.management_kind is not None:
            raise ContractError(f"{key}: management_kind applies to management events only; "
                                f"this type is {self.category!r}",
                                config_key=f"{key}.management_kind")


@dataclass(frozen=True)
class ManagementRecordSpec:
    """Events that a management record (e.g. farm records) confirms (guide step 14).

    ``record`` names the record, ``confirmed_by`` who stated the confirmation and
    ``confirmed_on`` when (ISO date). A confirmation adds the ``management_record`` label to
    the listed events; their logged ``event_source`` and evidence class stay as logged.
    """

    record: str
    confirmed_by: str
    confirmed_on: str
    event_ids: tuple[str, ...]
    note: str | None = None

    def check(self, key: str) -> None:
        for f in ("record", "confirmed_by"):
            _require(getattr(self, f), input=key, field=f, config_key=f"{key}.{f}")
        day = _iso_date(self.confirmed_on, key=key, field="confirmed_on")
        if day is None:
            raise ContractError(f"{key}: confirmed_on not declared",
                                config_key=f"{key}.confirmed_on")
        object.__setattr__(self, "confirmed_on", day)
        ids = tuple(str(e) for e in (self.event_ids or ()))
        if not ids or len(set(ids)) != len(ids):
            raise ContractError(f"{key}: event_ids must be a non-empty list without duplicates",
                                config_key=f"{key}.event_ids")
        object.__setattr__(self, "event_ids", ids)

    @property
    def label(self) -> str:
        return f"{self.record} (confirmed by {self.confirmed_by}, {self.confirmed_on})"


@dataclass(frozen=True)
class EventTableSpec:
    """A station event log (guide step 14; ``etcompare.adapters.event_log``).

    Events are defined only from this table, never from model residuals or agreement.

    - ``columns``: ``{canonical: source column}`` for every name in :data:`EVENT_COLUMNS`
      (``end_timestamp`` optional).
    - ``timestamp_format``, ``timestamp_timezone`` and ``day_rule``
      (:data:`EVENT_DAY_RULES`): how a timestamp becomes a station-day label. The declared
      timezone must equal each mapped station's timezone (checked by :class:`Config` and by
      the adapter), because no conversion is implemented and dates are never shifted.
    - ``site_mapping``: ``{source station ID: site_id}``. Rows of other stations are
      excluded with a reason, not read.
    - ``site_windows``: ``{site_id: {start, end}}``, the declared station record of every
      mapped site. Events dated outside it are rejected with a reason (exclusion log).
    - ``event_types``: ``{source event_type: {category, management_kind}}``. Every type
      found for a mapped station must be declared.
    - ``evidence_classes``: ``{source event_source: class}`` (:data:`EVIDENCE_CLASSES`).
      Every non-null source found for a mapped station must be declared; an unlabelled
      (null) source is always ``unknown``.
    - ``approximate_date_patterns``: case-insensitive regular expressions searched in the
      notes. An event is ``approximate`` only when one matches. Mandatory; ``[]`` declares
      that no note marks a date as approximate.
    - ``management_records``: optional :class:`ManagementRecordSpec` entries. Listed events
      get the ``management_record`` label; nothing else does.
    """

    name: str
    path: Path | None
    columns: Mapping[str, str]
    timestamp_format: str
    timestamp_timezone: str
    day_rule: str
    site_mapping: Mapping[str, str]
    site_windows: Mapping[str, Mapping[str, str]]
    event_types: Mapping[str, EventTypeSpec]
    evidence_classes: Mapping[str, str]
    approximate_date_patterns: tuple[str, ...]
    management_records: tuple[ManagementRecordSpec, ...] = ()
    description: str | None = None

    def __post_init__(self) -> None:
        self._check_management_records()
        _require(self.name, input="event table", field="name",
                 config_key="event_tables[].name")
        key = f"event_tables.{self.name}"
        if not _METHOD_ID.match(str(self.name)):
            raise ContractError(f"{key}: name must be snake_case", config_key=key)
        _require(self.path, input=key, field="path", config_key=f"{key}.path")
        cols = dict(self.columns or {})
        for c in EVENT_COLUMNS:
            _require(cols.get(c), input=key, field=f"columns.{c}", config_key=f"{key}.columns.{c}")
        extra = set(cols) - set(EVENT_COLUMNS) - set(EVENT_OPTIONAL_COLUMNS)
        if extra:
            raise ContractError(f"{key}: unknown column mapping(s) {sorted(extra)}; allowed: "
                                f"{list(EVENT_COLUMNS + EVENT_OPTIONAL_COLUMNS)}",
                                config_key=f"{key}.columns")
        object.__setattr__(self, "columns", MappingProxyType(cols))
        _require(self.timestamp_format, input=key, field="timestamp_format",
                 config_key=f"{key}.timestamp_format")
        object.__setattr__(self, "timestamp_timezone", validate_timezone(
            self.timestamp_timezone, input=key, config_key=f"{key}.timestamp_timezone"))
        _choice(self.day_rule, EVENT_DAY_RULES, input=key, field="day_rule",
                config_key=f"{key}.day_rule")

        mapping = {str(k): str(v) for k, v in (self.site_mapping or {}).items()}
        if not mapping:
            raise ContractError(f"{key}: site_mapping declares no station",
                                config_key=f"{key}.site_mapping")
        object.__setattr__(self, "site_mapping", MappingProxyType(mapping))
        windows = {}
        for site in sorted(set(mapping.values())):
            w = (self.site_windows or {}).get(site)
            wkey = f"{key}.site_windows.{site}"
            if not isinstance(w, Mapping):
                raise ContractError(f"{key}: no station record window declared for {site}; "
                                    "events outside it cannot be rejected", config_key=wkey)
            _check_keys(w, {"start", "end"}, wkey)
            start, end = (_iso_date(w.get(f), key=wkey, field=f) for f in ("start", "end"))
            if start is None or end is None:
                raise ContractError(f"{wkey}: start and end are required", config_key=wkey)
            if start > end:
                raise ContractError(f"{wkey}: start {start} is after end {end}", config_key=wkey)
            windows[site] = MappingProxyType({"start": start, "end": end})
        extra = set(self.site_windows or {}) - set(windows)
        if extra:
            raise ContractError(f"{key}: site_windows for unmapped site(s) {sorted(extra)}",
                                config_key=f"{key}.site_windows")
        object.__setattr__(self, "site_windows", MappingProxyType(windows))

        types = {}
        for name, body in (self.event_types or {}).items():
            tkey = f"{key}.event_types.{name}"
            if not isinstance(body, EventTypeSpec):
                _check_keys(body, set(EventTypeSpec.__dataclass_fields__), tkey)
                body = EventTypeSpec(category=body.get("category"),
                                     management_kind=body.get("management_kind"))
            body.check(tkey)
            types[str(name)] = body
        if not types:
            raise ContractError(f"{key}: no event_types declared", config_key=f"{key}.event_types")
        object.__setattr__(self, "event_types", MappingProxyType(types))

        evidence = {}
        for src, cls in (self.evidence_classes or {}).items():
            _choice(cls, EVIDENCE_CLASSES, input=key, field=f"evidence_classes.{src}",
                    config_key=f"{key}.evidence_classes")
            evidence[str(src)] = cls
        object.__setattr__(self, "evidence_classes", MappingProxyType(evidence))

        if self.approximate_date_patterns is None:
            raise ContractError(f"{key}: approximate_date_patterns not declared; declare the "
                                "text rule that marks an event date as approximate ([] for none)",
                                config_key=f"{key}.approximate_date_patterns")
        if isinstance(self.approximate_date_patterns, str):
            raise ContractError(f"{key}: approximate_date_patterns must be a list",
                                config_key=f"{key}.approximate_date_patterns")
        patterns = tuple(str(p) for p in self.approximate_date_patterns)
        for p in patterns:
            try:
                re.compile(p, re.IGNORECASE)
            except re.error as exc:
                raise ContractError(f"{key}: approximate_date_patterns {p!r} is not a valid "
                                    f"regular expression: {exc}",
                                    config_key=f"{key}.approximate_date_patterns") from exc
        object.__setattr__(self, "approximate_date_patterns", patterns)

    def _check_management_records(self) -> None:
        key = f"event_tables.{self.name}.management_records"
        records, seen = [], {}
        for i, r in enumerate(self.management_records or ()):
            if not isinstance(r, ManagementRecordSpec):
                _check_keys(r, set(ManagementRecordSpec.__dataclass_fields__), f"{key}[{i}]")
                r = ManagementRecordSpec(**{f: r.get(f) for f in
                                            ManagementRecordSpec.__dataclass_fields__})
            r.check(f"{key}[{i}]")
            for e in r.event_ids:
                if e in seen:
                    raise ContractError(f"{key}: event {e} is confirmed by two entries",
                                        config_key=key)
                seen[e] = i
            records.append(r)
        object.__setattr__(self, "management_records", tuple(records))

    def confirmed_events(self) -> Mapping[str, ManagementRecordSpec]:
        """``{event_id: confirming record}`` for every declared management-record event."""
        return MappingProxyType({e: r for r in self.management_records for e in r.event_ids})

    def check_stations(self, stations: Mapping[str, StationSpec]) -> None:
        """Every mapped site is declared, with the log's timezone (dates are never shifted)."""
        key = f"event_tables.{self.name}"
        for site in sorted(set(self.site_mapping.values())):
            if site not in stations:
                raise ContractError(f"{key}: station {site} not declared",
                                    config_key=f"stations.{site}")
            tz = stations[site].timezone
            if tz != self.timestamp_timezone:
                raise ContractError(
                    f"{key}: timestamps are declared in {self.timestamp_timezone} but station "
                    f"{site} uses {tz}; day_rule {self.day_rule} takes the calendar date in the "
                    "station's own timezone, and no conversion is implemented (dates are never "
                    "shifted silently)", config_key=f"{key}.timestamp_timezone")


def _fraction(value: object, *, key: str, field: str, open_zero: bool = True) -> float:
    if value is None or isinstance(value, bool):
        raise ContractError(f"{key}: {field} not declared", config_key=f"{key}.{field}")
    try:
        x = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ContractError(f"{key}: {field}={value!r} is not a number",
                            config_key=f"{key}.{field}") from exc
    if not (0 < x <= 1 if open_zero else 0 <= x <= 1):
        raise ContractError(f"{key}: {field}={x} must lie in {'(0, 1]' if open_zero else '[0, 1]'}",
                            config_key=f"{key}.{field}")
    return x


@dataclass(frozen=True)
class WindowVariableSpec:
    """A station variable summarised over event windows, to keep weather visible.

    ``column`` is a ``station_daily`` column (e.g. ``src_gridMET_ETr``, D-020), ``units`` its
    declared units and ``aggregation`` ``mean`` or ``sum`` over the window's days.
    """

    column: str
    units: str
    aggregation: str
    meaning: str | None = None

    def check(self, key: str) -> None:
        for f in ("column", "units"):
            _require(getattr(self, f), input=key, field=f, config_key=f"{key}.{f}")
        _choice(self.aggregation, WINDOW_AGGREGATIONS, input=key, field="aggregation",
                config_key=f"{key}.aggregation")


@dataclass(frozen=True)
class EventAnalysisSpec:
    """Before/after responses of a cohort to management events (guide step 14).

    Day 0 is the event's station day. The windows are, relative to the event's start
    (pre) and end (post) days:

    - pre (baseline): days ``-pre_days .. -1``;
    - post: days ``post_offset_days .. post_offset_days + post_days - 1``;
    - recovery search: from the post window's start to day ``recovery_max_days``.

    A window mean needs at least ``min_window_completeness`` of its calendar days in the
    cohort; a cumulative response needs every post day. Relative change and recovery need
    a baseline above ``baseline_min_mm`` (mm d-1). Recovery is the first day whose trailing
    ``recovery_smoothing_days`` mean (all days present, all after the event) reaches
    ``recovery_fraction`` of the baseline. ``weather_variables`` are summarised over the same
    windows; ``normalize_by`` (a ``mean`` weather variable) adds the change in ET divided by
    that variable. ``approximate_policy`` ``flag`` keeps events with approximate dates
    (flagged), ``exclude`` leaves them out with a reason.
    """

    event_analysis_id: str
    event_table: str
    management_kinds: tuple[str, ...]
    pre_days: int
    post_offset_days: int
    post_days: int
    min_window_completeness: float
    baseline_min_mm: float
    recovery_fraction: float
    recovery_max_days: int
    recovery_smoothing_days: int
    approximate_policy: str
    weather_variables: Mapping[str, WindowVariableSpec] = field(default_factory=dict)
    normalize_by: str | None = None
    comparisons: tuple[str, ...] | None = None
    description: str | None = None

    def __post_init__(self) -> None:
        aid = self.event_analysis_id
        _require(aid, input="event analysis", field="event_analysis_id",
                 config_key="event_analyses[].event_analysis_id")
        key = f"event_analyses.{aid}"
        if not _METHOD_ID.match(str(aid)):
            raise ContractError(f"{key}: event_analysis_id must be snake_case", config_key=key)
        _require(self.event_table, input=key, field="event_table",
                 config_key=f"{key}.event_table")
        kinds = tuple(self.management_kinds or ())
        if not kinds or len(set(kinds)) != len(kinds):
            raise ContractError(f"{key}: management_kinds must be a non-empty list without "
                                "duplicates", config_key=f"{key}.management_kinds")
        for k in kinds:
            _choice(k, MANAGEMENT_KINDS, input=key, field="management_kinds",
                    config_key=f"{key}.management_kinds")
        object.__setattr__(self, "management_kinds", kinds)
        for f, lo in (("pre_days", 1), ("post_offset_days", 0), ("post_days", 1),
                      ("recovery_smoothing_days", 1), ("recovery_max_days", 1)):
            v = getattr(self, f)
            if v is None:
                raise ContractError(f"{key}: {f} not declared", config_key=f"{key}.{f}")
            object.__setattr__(self, f, _positive_int(v, key=key, field=f, minimum=lo))
        if self.recovery_max_days < self.post_offset_days + self.recovery_smoothing_days - 1:
            raise ContractError(f"{key}: recovery_max_days {self.recovery_max_days} leaves no "
                                "complete smoothing window after the event",
                                config_key=f"{key}.recovery_max_days")
        object.__setattr__(self, "min_window_completeness", _fraction(
            self.min_window_completeness, key=key, field="min_window_completeness"))
        object.__setattr__(self, "recovery_fraction", _fraction(
            self.recovery_fraction, key=key, field="recovery_fraction"))
        if self.baseline_min_mm is None or isinstance(self.baseline_min_mm, bool):
            raise ContractError(f"{key}: baseline_min_mm not declared (mm d-1)",
                                config_key=f"{key}.baseline_min_mm")
        b = float(self.baseline_min_mm)
        if not math.isfinite(b) or b < 0:
            raise ContractError(f"{key}: baseline_min_mm must be finite and >= 0",
                                config_key=f"{key}.baseline_min_mm")
        object.__setattr__(self, "baseline_min_mm", b)
        _choice(self.approximate_policy, APPROXIMATE_POLICIES, input=key,
                field="approximate_policy", config_key=f"{key}.approximate_policy")
        variables = {}
        for name, body in (self.weather_variables or {}).items():
            vkey = f"{key}.weather_variables.{name}"
            if not isinstance(body, WindowVariableSpec):
                _check_keys(body, set(WindowVariableSpec.__dataclass_fields__), vkey)
                body = WindowVariableSpec(**{f: body.get(f) for f in
                                             WindowVariableSpec.__dataclass_fields__})
            body.check(vkey)
            variables[str(name)] = body
        object.__setattr__(self, "weather_variables", MappingProxyType(variables))
        if self.normalize_by is not None:
            v = variables.get(self.normalize_by)
            if v is None or v.aggregation != "mean":
                raise ContractError(f"{key}: normalize_by must name a weather variable with "
                                    "aggregation 'mean'", config_key=f"{key}.normalize_by")
            if normalize_et_units(v.units, input=key, field=v.column,
                                  config_key=f"{key}.weather_variables.{self.normalize_by}"
                                  ) != ET_UNIT:
                raise ContractError(f"{key}: normalize_by needs mm/day units")
        if self.comparisons is not None:
            object.__setattr__(self, "comparisons", tuple(self.comparisons))

    @property
    def windows(self) -> dict[str, tuple[int, int]]:
        """Day offsets (inclusive) of each window; pre from the start, the rest from the end."""
        post0 = self.post_offset_days
        return {"pre": (-self.pre_days, -1), "post": (post0, post0 + self.post_days - 1),
                "recovery": (post0, self.recovery_max_days)}


@dataclass(frozen=True)
class AnomalySpec:
    """Anomaly skill of a cohort (guide step 14; ``etcompare.anomalies``).

    The same declared method is fitted separately to the observed and the modeled series,
    and raw-value metrics are reported on the same days next to the anomaly metrics
    (demeaning removes part of the bias).

    - ``held_out_climatology``: for each held-out ``fold_unit`` (``year`` or ``water_year``),
      a day-of-year climatology is fitted on the other folds only: the mean of the training
      values within ``climatology_half_window_days`` (circular, 365-day calendar,
      ``leap_day: as_feb28``). A day's climatology needs ``min_training_values`` values from
      ``min_training_years`` distinct training folds (the sparse-season rule); otherwise it
      is unavailable, never filled.
    - ``exploratory_moving_mean``: a centred ``window_days`` (odd) moving mean of the same
      series over calendar days, needing ``min_window_completeness`` of the window. It is
      fitted in sample: exploratory, with no claim of seasonal generalisation.

    A metric sample with fewer than ``min_days`` days has a status and no value.
    """

    anomaly_id: str
    metric_set: str
    method: str
    min_days: int
    metrics: tuple[str, ...] = ("mbe", "mae", "rmse", "pearson_r")
    fold_unit: str | None = None
    water_year_start_month: int = 10
    climatology_half_window_days: int | None = None
    min_training_values: int | None = None
    min_training_years: int | None = None
    leap_day: str | None = None
    window_days: int | None = None
    min_window_completeness: float | None = None
    comparisons: tuple[str, ...] | None = None
    description: str | None = None

    def __post_init__(self) -> None:
        aid = self.anomaly_id
        _require(aid, input="anomaly analysis", field="anomaly_id",
                 config_key="anomalies[].anomaly_id")
        key = f"anomalies.{aid}"
        if not _METHOD_ID.match(str(aid)):
            raise ContractError(f"{key}: anomaly_id must be snake_case", config_key=key)
        _require(self.metric_set, input=key, field="metric_set", config_key=f"{key}.metric_set")
        _choice(self.method, ANOMALY_METHODS, input=key, field="method",
                config_key=f"{key}.method")
        if self.min_days is None:
            raise ContractError(f"{key}: min_days not declared", config_key=f"{key}.min_days")
        object.__setattr__(self, "min_days", _positive_int(self.min_days, key=key,
                                                           field="min_days"))
        object.__setattr__(self, "metrics", tuple(self.metrics or ()))
        if not self.metrics or len(set(self.metrics)) != len(self.metrics):
            raise ContractError(f"{key}: metrics must be a non-empty list without duplicates",
                                config_key=f"{key}.metrics")
        for m in self.metrics:
            _choice(m, DIAGNOSTIC_METRICS, input=key, field="metrics", config_key=f"{key}.metrics")
        clim = ("fold_unit", "climatology_half_window_days", "min_training_values",
                "min_training_years", "leap_day")
        moving = ("window_days", "min_window_completeness")
        own, other = (clim, moving) if self.method == "held_out_climatology" else (moving, clim)
        for f in own:
            if getattr(self, f) is None:
                raise ContractError(f"{key}: {f} not declared for method {self.method}",
                                    config_key=f"{key}.{f}")
        for f in other:
            if getattr(self, f) is not None:
                raise ContractError(f"{key}: {f} does not apply to method {self.method}",
                                    config_key=f"{key}.{f}")
        if self.method == "held_out_climatology":
            _choice(self.fold_unit, FOLD_UNITS, input=key, field="fold_unit",
                    config_key=f"{key}.fold_unit")
            _choice(self.leap_day, LEAP_DAY_RULES, input=key, field="leap_day",
                    config_key=f"{key}.leap_day")
            object.__setattr__(self, "climatology_half_window_days", _positive_int(
                self.climatology_half_window_days, key=key, field="climatology_half_window_days",
                minimum=0))
            for f in ("min_training_values", "min_training_years"):
                object.__setattr__(self, f, _positive_int(getattr(self, f), key=key, field=f))
            if not 1 <= int(self.water_year_start_month) <= 12:
                raise ContractError(f"{key}: water_year_start_month must be 1-12",
                                    config_key=f"{key}.water_year_start_month")
        else:
            w = _positive_int(self.window_days, key=key, field="window_days", minimum=3)
            if w % 2 == 0:
                raise ContractError(f"{key}: window_days must be odd (a centred window)",
                                    config_key=f"{key}.window_days")
            object.__setattr__(self, "window_days", w)
            object.__setattr__(self, "min_window_completeness", _fraction(
                self.min_window_completeness, key=key, field="min_window_completeness"))
        if self.comparisons is not None:
            object.__setattr__(self, "comparisons", tuple(self.comparisons))


def _depth_units(value: object, quantity: str, *, key: str) -> str:
    """Canonical units of a water-balance term: ``mm d-1`` (flux) or ``mm`` (storage state)."""
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ContractError(f"{key}: units not declared; magnitude is not evidence",
                            config_key=f"{key}.units")
    text = " ".join(str(value).strip().lower().split())
    if quantity == "storage_state":
        if text != "mm":
            raise ContractError(f"{key}: a storage state needs units 'mm' (storage depth), got "
                                f"{value!r}; convert water content to a storage depth upstream "
                                "with a declared profile depth", config_key=f"{key}.units")
        return ET_TOTAL_UNIT
    if text in _ET_UNIT_SYNONYMS:
        return ET_UNIT
    raise ContractError(f"{key}: units {value!r} are not a daily depth; declare mm/day (a bare "
                        "'mm' does not state the period, and no unit is converted)",
                        config_key=f"{key}.units")


def _finite(value: object, *, key: str, field: str) -> float:
    if value is None or isinstance(value, bool):
        raise ContractError(f"{key}: {field} not declared", config_key=f"{key}.{field}")
    try:
        x = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ContractError(f"{key}: {field}={value!r} is not a number",
                            config_key=f"{key}.{field}") from exc
    if not math.isfinite(x):
        raise ContractError(f"{key}: {field} must be finite", config_key=f"{key}.{field}")
    return x


@dataclass(frozen=True)
class WaterBalanceTermSpec:
    """One term of a water balance (guide step 15).

    ``status`` is ``measured``, ``modeled``, ``scenario`` or ``unknown``. Each status admits
    only its own source:

    - ``unknown``: ``source: none`` and a ``reason``. The term has no value, so every
      residual that needs it is unknown; it is never set to zero.
    - ``scenario``: ``source: scenario``, a constant ``daily_mm``, a ``justification`` and
      the ``spatial_support`` it is assumed for. It gives a *scenario* residual, never a
      measured/modeled one.
    - ``measured`` / ``modeled``: ``station_column`` (``column``, a ``station_daily``
      column such as ``src_gridMET_prcp``), ``station_et`` (the station ET of
      ``reference_variant``; ET only) or ``table`` (``path``, ``date_column``,
      ``value_column``). Each needs ``spatial_support``; columns and tables also need
      ``units`` and ``day_definition`` (may be ``unknown``).

    ``quantity`` is ``flux`` (mm d-1, summed) or, for ``storage_change`` only,
    ``storage_state`` (mm at the end of each station day, ``state_time``).
    """

    status: str
    source: str
    spatial_support: str | None = None
    units: str | None = None
    quantity: str = "flux"
    column: str | None = None
    reference_variant: str | None = None
    path: Path | None = None
    date_column: str | None = None
    value_column: str | None = None
    date_format: str = "%Y-%m-%d"
    day_definition: str | None = None
    state_time: str | None = None
    daily_mm: float | None = None
    justification: str | None = None
    reason: str | None = None
    support_note: str | None = None
    description: str | None = None

    def check(self, term: str, key: str) -> None:
        """Validate the declaration of ``term`` (raises :class:`ContractError`)."""
        _choice(term, WATER_BALANCE_TERMS, input=key, field="term", config_key=key)
        _choice(self.status, TERM_STATUSES, input=key, field="status", config_key=f"{key}.status")
        _choice(self.source, TERM_SOURCES, input=key, field="source", config_key=f"{key}.source")
        _choice(self.quantity, TERM_QUANTITIES, input=key, field="quantity",
                config_key=f"{key}.quantity")
        allowed = {"unknown": ("none",), "scenario": ("scenario",),
                   "measured": ("station_column", "station_et", "table"),
                   "modeled": ("station_column", "station_et", "table")}[self.status]
        if self.source not in allowed:
            raise ContractError(f"{key}: status {self.status} needs source {list(allowed)}, not "
                                f"{self.source!r}", config_key=f"{key}.source")
        if self.quantity == "storage_state":
            if term != "storage_change":
                raise ContractError(f"{key}: only storage_change can be a storage_state",
                                    config_key=f"{key}.quantity")
            if self.status in ("unknown", "scenario"):
                raise ContractError(f"{key}: a storage_state needs a measured or modeled source",
                                    config_key=f"{key}.quantity")
            _choice(self.state_time, STATE_TIMES, input=key, field="state_time",
                    config_key=f"{key}.state_time")
        elif self.state_time is not None:
            raise ContractError(f"{key}: state_time applies only to a storage_state",
                                config_key=f"{key}.state_time")
        fields_by_source = {
            "none": {"reason"}, "scenario": {"daily_mm", "justification", "spatial_support"},
            "station_column": {"column", "units", "day_definition", "spatial_support"},
            "station_et": {"reference_variant", "spatial_support"},
            "table": {"path", "date_column", "value_column", "units", "day_definition",
                      "spatial_support"},
        }
        optional = {"quantity", "state_time", "support_note", "description", "date_format",
                    "status", "source"}
        own = fields_by_source[self.source]
        for f in sorted(own):
            _require(getattr(self, f), input=key, field=f, config_key=f"{key}.{f}")
        if self.source == "station_et":
            # Station ET is canonical mm d-1 (the station table declares its units).
            if self.units not in (None, ET_UNIT):
                raise ContractError(f"{key}: station_et units come from the station table "
                                    "declaration; do not redeclare them", config_key=f"{key}.units")
            optional = optional | {"units"}
        defaults = {"quantity": "flux", "date_format": "%Y-%m-%d"}
        for f in self.__dataclass_fields__:
            if f in own or f in optional:
                continue
            if getattr(self, f) is not None and getattr(self, f) != defaults.get(f):
                raise ContractError(f"{key}: {f} does not apply to source {self.source}",
                                    config_key=f"{key}.{f}")
        if self.source == "station_et":
            if term != "et":
                raise ContractError(f"{key}: station_et is the ET term's source only",
                                    config_key=f"{key}.source")
            _choice(self.reference_variant, REFERENCE_VARIANTS, input=key,
                    field="reference_variant", config_key=f"{key}.reference_variant")
            object.__setattr__(self, "units", ET_UNIT)
        if self.source in ("station_column", "table"):
            object.__setattr__(self, "units", _depth_units(self.units, self.quantity, key=key))
            _choice(self.day_definition, PRODUCT_DAY_DEFINITIONS, input=key,
                    field="day_definition", config_key=f"{key}.day_definition")
        if self.spatial_support is not None:
            _choice(self.spatial_support, SPATIAL_SUPPORTS, input=key, field="spatial_support",
                    config_key=f"{key}.spatial_support")
        if term == "irrigation" and self.spatial_support == "regional":
            raise ContractError(f"{key}: a regional diversion is not field irrigation; declare "
                                "irrigation records of the boundary itself, or a scenario",
                                config_key=f"{key}.spatial_support")
        if self.source == "scenario":
            object.__setattr__(self, "daily_mm",
                               _finite(self.daily_mm, key=key, field="daily_mm"))


@dataclass(frozen=True)
class WaterBalanceBoundarySpec:
    """The boundary every term of a water balance must cover.

    - ``polygon``: a declared polygon (``path``, ``crs``, ``id_column``, ``feature_id``,
      ``source`` and ``vintage``, which may be ``unknown``). ``area_m2`` with its
      ``area_source`` is optional; without it volumes are refused.
    - ``point``: a point/depth-only column at the station (optional ``depth_mm``). It has
      no area, so volumes are never defined.

    A tower footprint is never a boundary: it changes every day and is not a field area.
    """

    boundary_id: str
    kind: str
    area_m2: float | None = None
    area_source: str | None = None
    path: Path | None = None
    layer: str | None = None
    crs: str | None = None
    id_column: str | None = None
    feature_id: str | None = None
    source: str | None = None
    vintage: str | None = None
    depth_mm: float | None = None
    description: str | None = None

    @property
    def area_known(self) -> bool:
        return self.area_m2 is not None

    def check(self, key: str) -> None:
        _require(self.boundary_id, input=key, field="boundary_id",
                 config_key=f"{key}.boundary_id")
        if not _METHOD_ID.match(str(self.boundary_id)):
            raise ContractError(f"{key}: boundary_id must be snake_case",
                                config_key=f"{key}.boundary_id")
        if self.kind in ("footprint", "tower_footprint"):
            raise ContractError(f"{key}: a tower footprint changes every day and is never a "
                                "water-balance boundary or a field area; declare a polygon "
                                "or a point boundary", config_key=f"{key}.kind")
        _choice(self.kind, BOUNDARY_KINDS, input=key, field="kind", config_key=f"{key}.kind")
        polygon = ("path", "crs", "id_column", "feature_id", "source", "vintage")
        if self.kind == "polygon":
            for f in polygon:
                _require(getattr(self, f), input=key, field=f, config_key=f"{key}.{f}")
            object.__setattr__(self, "feature_id", str(self.feature_id))
            object.__setattr__(self, "vintage", str(self.vintage))
            if self.depth_mm is not None:
                raise ContractError(f"{key}: depth_mm applies to a point boundary",
                                    config_key=f"{key}.depth_mm")
        else:
            for f in (*polygon, "layer"):
                if getattr(self, f) is not None:
                    raise ContractError(f"{key}: {f} applies to a polygon boundary",
                                        config_key=f"{key}.{f}")
            if self.area_m2 is not None or self.area_source is not None:
                raise ContractError(f"{key}: a point/depth boundary has no area, so volumes "
                                    "are not defined", config_key=f"{key}.area_m2")
            if self.depth_mm is not None:
                d = _finite(self.depth_mm, key=key, field="depth_mm")
                if d <= 0:
                    raise ContractError(f"{key}: depth_mm must be > 0",
                                        config_key=f"{key}.depth_mm")
                object.__setattr__(self, "depth_mm", d)
        if (self.area_m2 is None) != (self.area_source is None):
            raise ContractError(f"{key}: declare area_m2 together with its area_source",
                                config_key=f"{key}.area_source")
        if self.area_m2 is not None:
            a = _finite(self.area_m2, key=key, field="area_m2")
            if a <= 0:
                raise ContractError(f"{key}: area_m2 must be > 0", config_key=f"{key}.area_m2")
            object.__setattr__(self, "area_m2", a)

    def matches(self, support: str | None) -> bool:
        """True when a term of ``support`` covers exactly this boundary."""
        return support == "boundary" or (self.kind == "point" and support == "station_point")


def _term_specs(value: object, *, key: str, require_all: bool
                ) -> Mapping[str, WaterBalanceTermSpec]:
    if not isinstance(value, Mapping):
        raise ContractError(f"{key}: terms must be a mapping term -> declaration",
                            config_key=key)
    terms: dict[str, WaterBalanceTermSpec] = {}
    for name, body in value.items():
        tkey = f"{key}.{name}"
        if not isinstance(body, WaterBalanceTermSpec):
            _check_keys(body, set(WaterBalanceTermSpec.__dataclass_fields__), tkey)
            body = WaterBalanceTermSpec(**{f: body.get(f) for f in ("status", "source")},
                                        **{k: v for k, v in body.items()
                                           if k not in ("status", "source")})
        body.check(str(name), tkey)
        terms[str(name)] = body
    if require_all:
        missing = [t for t in WATER_BALANCE_TERMS if t not in terms]
        if missing:
            raise ContractError(f"{key}: term(s) {missing} not declared; a term without an "
                                "input must be declared with status unknown (it is never zero)",
                                config_key=key)
    return MappingProxyType({t: terms[t] for t in WATER_BALANCE_TERMS if t in terms})


@dataclass(frozen=True)
class WaterBalanceSpec:
    """A water-balance plausibility check of one station's boundary (guide step 15).

    ``residual = P + I + CR - ET - RO - DP - dS`` (``equation: soil_zone``,
    ``sign_convention: inflows_positive``). Every term of :data:`WATER_BALANCE_TERMS` is
    declared (:class:`WaterBalanceTermSpec`). Periods are those of the step-8
    ``period_aggregation`` (optionally a subset, ``periods``), with the same calendar
    denominators. ``volumes`` converts depths to volumes and needs a boundary of known area.

    Residuals are plausibility diagnostics only: they hold every unmeasured exchange and the
    errors of every term, so they are never ET error and never a basis for choosing a model,
    reference, filter or footprint.
    """

    water_balance_id: str
    site_id: str
    period_aggregation: str
    boundary: WaterBalanceBoundarySpec
    equation: str
    sign_convention: str
    terms: Mapping[str, WaterBalanceTermSpec]
    periods: tuple[str, ...] | None = None
    volumes: bool = False
    description: str | None = None

    @property
    def equation_text(self) -> str:
        return WATER_BALANCE_EQUATIONS[self.equation]

    def __post_init__(self) -> None:
        wid = self.water_balance_id
        _require(wid, input="water balance", field="water_balance_id",
                 config_key="water_balances[].water_balance_id")
        key = f"water_balances.{wid}"
        if not _METHOD_ID.match(str(wid)):
            raise ContractError(f"{key}: water_balance_id must be snake_case", config_key=key)
        for f in ("site_id", "period_aggregation"):
            _require(getattr(self, f), input=key, field=f, config_key=f"{key}.{f}")
        _choice(self.equation, tuple(WATER_BALANCE_EQUATIONS), input=key, field="equation",
                config_key=f"{key}.equation")
        _choice(self.sign_convention, SIGN_CONVENTIONS, input=key, field="sign_convention",
                config_key=f"{key}.sign_convention")
        b = self.boundary
        if b is None:
            raise ContractError(f"{key}: boundary not declared", config_key=f"{key}.boundary")
        if isinstance(b, Mapping):
            _check_keys(b, set(WaterBalanceBoundarySpec.__dataclass_fields__), f"{key}.boundary")
            bkw: dict[str, Any] = {f: b.get(f) for f in ("boundary_id", "kind")}
            bkw.update({k: v for k, v in b.items() if k not in bkw})
            b = WaterBalanceBoundarySpec(**bkw)
            object.__setattr__(self, "boundary", b)
        b.check(f"{key}.boundary")
        object.__setattr__(self, "terms", _term_specs(self.terms or {}, key=f"{key}.terms",
                                                      require_all=True))
        if self.periods is not None:
            p = tuple(self.periods)
            if not p or len(set(p)) != len(p):
                raise ContractError(f"{key}: periods must be a non-empty list without duplicates",
                                    config_key=f"{key}.periods")
            for x in p:
                _choice(x, PERIOD_TYPES, input=key, field="periods", config_key=f"{key}.periods")
            object.__setattr__(self, "periods", p)
        if not isinstance(self.volumes, bool):
            raise ContractError(f"{key}: volumes must be true or false",
                                config_key=f"{key}.volumes")
        if self.volumes and not b.area_known:
            raise ContractError(f"{key}: volumes need a boundary with a declared, known area "
                                f"(boundary {b.boundary_id} has none); depths are not converted",
                                config_key=f"{key}.volumes")


@dataclass(frozen=True)
class PerturbationSpec:
    """A declared distribution for one term of a measurement scenario.

    ``distribution`` is ``{family: <DISTRIBUTION_FAMILIES>, <its parameters>}``; ``mode``
    is ``multiplicative`` or ``additive``; ``temporal_dependence`` is ``systematic`` (one
    draw per realization for every day) or ``independent_daily``. A ``justification`` is
    mandatory: no distribution is a default.
    """

    term: str
    distribution: Mapping[str, Any]
    mode: str
    temporal_dependence: str
    justification: str

    def check(self, key: str) -> None:
        _choice(self.term, WATER_BALANCE_TERMS, input=key, field="term", config_key=f"{key}.term")
        _choice(self.mode, PERTURBATION_MODES, input=key, field="mode", config_key=f"{key}.mode")
        _choice(self.temporal_dependence, TEMPORAL_DEPENDENCES, input=key,
                field="temporal_dependence", config_key=f"{key}.temporal_dependence")
        _require(self.justification, input=key, field="justification",
                 config_key=f"{key}.justification")
        d = self.distribution
        if not isinstance(d, Mapping):
            raise ContractError(f"{key}: distribution must be a mapping with a family",
                                config_key=f"{key}.distribution")
        family = d.get("family")
        _choice(family, tuple(DISTRIBUTION_FAMILIES), input=key, field="distribution.family",
                config_key=f"{key}.distribution.family")
        names = DISTRIBUTION_FAMILIES[str(family)]
        _check_keys(d, {"family", *names}, f"{key}.distribution")
        p = {n: _finite(d.get(n), key=f"{key}.distribution", field=n) for n in names}
        bad = ((family == "normal" and p["sd"] <= 0) or
               (family == "lognormal" and p["sigma"] <= 0) or
               (family == "uniform" and not p["low"] < p["high"]) or
               (family == "triangular" and not (p["low"] <= p["mode"] <= p["high"]
                                                and p["low"] < p["high"])))
        if bad:
            raise ContractError(f"{key}: invalid {family} parameters {p}",
                                config_key=f"{key}.distribution")
        object.__setattr__(self, "distribution", MappingProxyType({"family": family, **p}))


@dataclass(frozen=True)
class ScenarioAlternativeSpec:
    """A named alternative declaration of one or more terms (method choice or a supplied
    footprint realization), with its justification."""

    name: str
    justification: str
    terms: Mapping[str, WaterBalanceTermSpec]

    def check(self, key: str) -> None:
        _require(self.name, input=key, field="name", config_key=f"{key}.name")
        if not _METHOD_ID.match(str(self.name)) or self.name == "declared":
            raise ContractError(f"{key}: alternative name must be snake_case and not "
                                "'declared' (the water balance's own declaration)",
                                config_key=f"{key}.name")
        _require(self.justification, input=key, field="justification",
                 config_key=f"{key}.justification")
        terms = _term_specs(self.terms or {}, key=f"{key}.terms", require_all=False)
        if not terms:
            raise ContractError(f"{key}: an alternative must redeclare at least one term",
                                config_key=f"{key}.terms")
        object.__setattr__(self, "terms", terms)


def _cholesky_ok(matrix: list[list[float]]) -> bool:
    n = len(matrix)
    low = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1):
            s = matrix[i][j] - sum(low[i][k] * low[j][k] for k in range(j))
            if i == j:
                if s <= 1e-12:
                    return False
                low[i][i] = math.sqrt(s)
            else:
                low[i][j] = s / low[j][j]
    return True


@dataclass(frozen=True)
class ScenarioSpec:
    """Declared scenario uncertainty of one water balance (guide step 15).

    One ``category`` per scenario, so the kinds are never merged into one interval:

    - ``measurement``: ``perturbations`` (declared distributions with justifications), a
      ``dependence`` declaration (``across_terms``: ``independent`` or ``gaussian_copula``
      with ``correlations``, and a ``justification``), ``seed``, ``n_draws`` and
      ``quantiles``.
    - ``method_choice``: named ``alternatives`` (e.g. another ET reference variant). They
      are deterministic sensitivity cases, never a probability distribution.
    - ``footprint_parameter``: at least two ``alternatives`` that are supplied realizations
      (``table`` sources from regenerated footprints), with ``realization_source``. Noise is
      never added to normalized footprint pixels.

    ``sampling`` uncertainty is the step-10 bootstrap and cannot be declared here.
    """

    scenario_id: str
    water_balance: str
    category: str
    justification: str
    seed: int | None = None
    n_draws: int | None = None
    quantiles: tuple[float, ...] = ()
    perturbations: tuple[PerturbationSpec, ...] = ()
    dependence: Mapping[str, Any] | None = None
    alternatives: tuple[ScenarioAlternativeSpec, ...] = ()
    realization_source: str | None = None
    description: str | None = None

    def __post_init__(self) -> None:
        sid = self.scenario_id
        _require(sid, input="scenario", field="scenario_id", config_key="scenarios[].scenario_id")
        key = f"scenarios.{sid}"
        if not _METHOD_ID.match(str(sid)):
            raise ContractError(f"{key}: scenario_id must be snake_case", config_key=key)
        _require(self.water_balance, input=key, field="water_balance",
                 config_key=f"{key}.water_balance")
        _require(self.justification, input=key, field="justification",
                 config_key=f"{key}.justification")
        if self.category == "sampling":
            raise ContractError(f"{key}: sampling uncertainty is the step-10 block bootstrap "
                                "(etcompare.bootstrap); it is reported in the intervals tables "
                                "and never declared or merged here", config_key=f"{key}.category")
        _choice(self.category, SCENARIO_CATEGORIES, input=key, field="category",
                config_key=f"{key}.category")
        perts = []
        for i, p in enumerate(self.perturbations or ()):
            pkey = f"{key}.perturbations[{i}]"
            if isinstance(p, Mapping):
                _check_keys(p, set(PerturbationSpec.__dataclass_fields__), pkey)
                pkw: dict[str, Any] = {f: p.get(f) for f in PerturbationSpec.__dataclass_fields__}
                p = PerturbationSpec(**pkw)
            p.check(pkey)
            perts.append(p)
        object.__setattr__(self, "perturbations", tuple(perts))
        alts = []
        for i, a in enumerate(self.alternatives or ()):
            akey = f"{key}.alternatives[{i}]"
            if isinstance(a, Mapping):
                _check_keys(a, set(ScenarioAlternativeSpec.__dataclass_fields__), akey)
                akw: dict[str, Any] = {f: a.get(f) for f in
                                       ScenarioAlternativeSpec.__dataclass_fields__}
                a = ScenarioAlternativeSpec(**akw)
            a.check(akey)
            alts.append(a)
        object.__setattr__(self, "alternatives", tuple(alts))
        names = [a.name for a in alts]
        if len(set(names)) != len(names):
            raise ContractError(f"{key}: duplicate alternative names",
                                config_key=f"{key}.alternatives")
        object.__setattr__(self, "quantiles", tuple(self.quantiles or ()))
        if self.category == "measurement":
            self._check_measurement(key)
        else:
            for f in ("seed", "n_draws", "dependence"):
                if getattr(self, f) is not None:
                    raise ContractError(f"{key}: {f} applies to measurement scenarios only "
                                        f"({self.category} alternatives are deterministic)",
                                        config_key=f"{key}.{f}")
            if self.perturbations or self.quantiles:
                raise ContractError(f"{key}: {self.category} scenarios take alternatives, not "
                                    "distributions or quantiles", config_key=f"{key}.perturbations")
            minimum = 2 if self.category == "footprint_parameter" else 1
            if len(alts) < minimum:
                raise ContractError(f"{key}: {self.category} needs at least {minimum} "
                                    "alternative(s)", config_key=f"{key}.alternatives")
            if self.category == "footprint_parameter":
                _require(self.realization_source, input=key, field="realization_source",
                         config_key=f"{key}.realization_source")
                for a in alts:
                    bad = [t for t, s in a.terms.items() if s.source != "table"]
                    if bad:
                        raise ContractError(
                            f"{key}: footprint realization {a.name} redeclares {bad} without a "
                            "table source; footprint uncertainty needs regenerated footprints or "
                            "supplied realizations, never noise on normalized pixels",
                            config_key=f"{key}.alternatives")
            elif self.realization_source is not None:
                raise ContractError(f"{key}: realization_source applies to footprint_parameter",
                                    config_key=f"{key}.realization_source")

    def _check_measurement(self, key: str) -> None:
        if self.alternatives or self.realization_source is not None:
            raise ContractError(f"{key}: measurement scenarios take perturbations, not "
                                "alternatives", config_key=f"{key}.alternatives")
        if self.seed is None:
            raise ContractError(f"{key}: seed not declared; scenarios must be reproducible",
                                config_key=f"{key}.seed")
        _positive_int(self.seed, key=key, field="seed", minimum=0)
        if self.n_draws is None:
            raise ContractError(f"{key}: n_draws not declared", config_key=f"{key}.n_draws")
        _positive_int(self.n_draws, key=key, field="n_draws", minimum=2)
        if not self.perturbations:
            raise ContractError(f"{key}: no perturbations declared; distributions are never "
                                "a default", config_key=f"{key}.perturbations")
        terms = [p.term for p in self.perturbations]
        if len(set(terms)) != len(terms):
            raise ContractError(f"{key}: a term is perturbed twice",
                                config_key=f"{key}.perturbations")
        q = self.quantiles
        if not q or len(set(q)) != len(q) or any(
                isinstance(x, bool) or not isinstance(x, int | float) or not 0 < x < 1 for x in q):
            raise ContractError(f"{key}: quantiles must be distinct levels in (0, 1)",
                                config_key=f"{key}.quantiles")
        object.__setattr__(self, "quantiles", tuple(sorted(float(x) for x in q)))
        d = self.dependence
        if not isinstance(d, Mapping):
            raise ContractError(f"{key}: dependence not declared (across_terms: independent or "
                                "gaussian_copula, with a justification)",
                                config_key=f"{key}.dependence")
        _check_keys(d, {"across_terms", "correlations", "justification"}, f"{key}.dependence")
        _choice(d.get("across_terms"), TERM_DEPENDENCES, input=key,
                field="dependence.across_terms", config_key=f"{key}.dependence.across_terms")
        _require(d.get("justification"), input=key, field="dependence.justification",
                 config_key=f"{key}.dependence.justification")
        pairs = list(d.get("correlations") or ())
        by_term = {p.term: p for p in self.perturbations}
        rho: dict[tuple[str, ...], float] = {}
        if d["across_terms"] == "independent" and pairs:
            raise ContractError(f"{key}: correlations given for independent terms",
                                config_key=f"{key}.dependence.correlations")
        if d["across_terms"] == "gaussian_copula" and not pairs:
            raise ContractError(f"{key}: gaussian_copula needs declared correlations",
                                config_key=f"{key}.dependence.correlations")
        for i, c in enumerate(pairs):
            ckey = f"{key}.dependence.correlations[{i}]"
            _check_keys(c, {"terms", "rho"}, ckey)
            t = tuple(c.get("terms") or ())
            if len(t) != 2 or t[0] == t[1] or any(x not in by_term for x in t):
                raise ContractError(f"{ckey}: terms must be two different perturbed terms",
                                    config_key=ckey)
            if by_term[t[0]].temporal_dependence != by_term[t[1]].temporal_dependence:
                raise ContractError(f"{ckey}: correlated terms need the same temporal_dependence",
                                    config_key=ckey)
            r = _finite(c.get("rho"), key=ckey, field="rho")
            if not -1 < r < 1:
                raise ContractError(f"{ckey}: rho must lie in (-1, 1)", config_key=ckey)
            pair = tuple(sorted(t))
            if pair in rho:
                raise ContractError(f"{ckey}: pair {pair} declared twice", config_key=ckey)
            rho[pair] = r
        names = sorted(by_term)
        matrix = [[1.0 if a == b else rho.get(tuple(sorted((a, b))), 0.0)
                   for b in names] for a in names]
        if not _cholesky_ok(matrix):
            raise ContractError(f"{key}: the declared correlations are not positive definite",
                                config_key=f"{key}.dependence.correlations")
        object.__setattr__(self, "dependence", MappingProxyType({
            "across_terms": d["across_terms"], "justification": d["justification"],
            "correlations": tuple(MappingProxyType({"terms": list(k), "rho": v})
                                  for k, v in sorted(rho.items()))}))

    def correlation(self, a: str, b: str) -> float:
        """Declared correlation of two perturbed terms (1 on the diagonal, 0 if undeclared)."""
        if a == b:
            return 1.0
        for c in (self.dependence or {}).get("correlations", ()):
            if sorted(c["terms"]) == sorted((a, b)):
                return float(c["rho"])
        return 0.0


@dataclass(frozen=True)
class EnsembleSpec:
    """Held-out ensemble experiment (guide step 16; ``etcompare.ensemble``).

    Three products are compared on the same frozen cohort days: the downloaded ``official``
    ensemble (a comparison product, never a member), the unweighted ``combinations`` of the
    declared ``members`` and, when ``designs`` are declared, a fitted weighted ensemble whose
    held-out predictions come only from weights fitted on other folds.

    - **Members and eligibility.** ``members`` are the declared component products (2 to
      :data:`MAX_ENSEMBLE_MEMBERS`). ``missing_member_policy`` is fixed in advance:
      ``require_all``, or ``reweight_available`` with ``min_members``.
    - **Inputs.** One ``reference_variant`` and one extraction ``side``, from ``sources``
      (``raster_extractions`` or ``extracted_tables``), optionally limited to ``sites`` and
      ``start``/``end``. Ensembles are post-extraction, never pixel-level.
    - **Fitting** (only with ``designs``): ``method: simplex_least_squares`` (weights >= 0,
      summing to 1), ``regularisation: shrink_to_equal`` over ``shrinkage_grid`` (alpha in
      [0, 1]; must include 1, the simple mean), ``selection_metric: rmse`` from inner grouped
      leave-one-fold-out validation on training folds only, ``preprocessing: none``.
      ``fold_unit`` (year or water_year, for ``leave_one_year_out``), ``buffer_days``
      (training days removed on each side of every held-out fold, inner folds too),
      ``min_training_folds`` (>= 2, so inner validation is grouped),
      ``min_training_days_per_fold`` and ``min_test_days_per_fold`` (a held-out fold with
      fewer eligible days is declined, so a handful of days never counts as held out).
      ``insufficient_data`` (``decline`` or ``exploratory``) says what happens when no fold
      can be fitted.
    - **Gap-filled reference days** are training rows only with ``gap_filled_training: true``
      (``et_corrected_inclusive`` only); they share gridMET drivers with OpenET (D-005).
    - ``metric_set``, ``bootstrap`` (optional) and ``contrast_metrics`` (score metrics with a
      ranking direction) evaluate the products through steps 9-11.
    - ``site_footprint_sets``: with several stations whose footprints are different sets,
      ``{site: footprint_set_id}`` names each station's set, and ``side.footprint_set_id`` is
      then a label for the combination (not a declared set). Every mapped set must be
      target-independent, and every station in ``sites`` must be mapped.
    - ``subsets``: extra evaluation subsets, each ``{subset_id, months}``. Every compared
      scope is also evaluated on its days in those calendar months (e.g. ``apr_oct``,
      months 4-10), as a separate frozen cohort. Training and folds are unchanged.
    """

    ensemble_id: str
    members: tuple[str, ...]
    reference_variant: str
    side: ExtractionSide
    metric_set: str
    missing_member_policy: str
    official: str | None = None
    sources: tuple[str, ...] = ()
    sites: tuple[str, ...] | None = None
    start: str | None = None
    end: str | None = None
    min_members: int | None = None
    combinations: tuple[str, ...] = ENSEMBLE_COMBINATIONS
    bootstrap: str | None = None
    contrast_metrics: tuple[str, ...] = ("mae", "rmse", "mbe")
    designs: tuple[str, ...] = ()
    fold_unit: str | None = None
    water_year_start_month: int = 10
    buffer_days: int | None = None
    min_training_folds: int | None = None
    min_training_days_per_fold: int | None = None
    min_test_days_per_fold: int | None = None
    method: str | None = None
    regularisation: str | None = None
    shrinkage_grid: tuple[float, ...] = ()
    selection_metric: str | None = None
    preprocessing: str | None = None
    insufficient_data: str | None = None
    gap_filled_training: bool = False
    subsets: tuple[Mapping[str, Any], ...] = ()
    site_footprint_sets: Mapping[str, str] | None = None
    description: str | None = None

    _FIT_FIELDS: ClassVar[tuple[str, ...]] = (
        "buffer_days", "min_training_folds", "min_training_days_per_fold",
        "min_test_days_per_fold", "method",
        "regularisation", "selection_metric", "preprocessing", "insufficient_data")

    def __post_init__(self) -> None:
        eid = self.ensemble_id
        _require(eid, input="ensemble", field="ensemble_id", config_key="ensembles[].ensemble_id")
        key = f"ensembles.{eid}"
        if not _METHOD_ID.match(str(eid)):
            raise ContractError(f"{key}: ensemble_id must be snake_case", config_key=key)
        for f in ("members", "sources", "combinations", "contrast_metrics", "designs"):
            object.__setattr__(self, f, tuple(getattr(self, f) or ()))
        if self.sites is not None:
            object.__setattr__(self, "sites", tuple(self.sites))
        k = len(self.members)
        if len(set(self.members)) != k or not 2 <= k <= MAX_ENSEMBLE_MEMBERS:
            raise ContractError(f"{key}: members must be 2-{MAX_ENSEMBLE_MEMBERS} distinct "
                                "products", config_key=f"{key}.members")
        if self.official is not None and self.official in self.members:
            raise ContractError(f"{key}: the official ensemble {self.official!r} is a comparison "
                                "product, never a member of the ensemble it is compared with",
                                config_key=f"{key}.members")
        _choice(self.reference_variant, REFERENCE_VARIANTS, input=key, field="reference_variant",
                config_key=f"{key}.reference_variant")
        side = self.side
        if isinstance(side, Mapping):
            _check_keys(side, {"extraction_method", "footprint_set_id"}, f"{key}.side")
            parts: dict[str, Any] = {f: side.get(f) for f in ("extraction_method",
                                                              "footprint_set_id")}
            side = ExtractionSide(**parts)
            object.__setattr__(self, "side", side)
        if not isinstance(side, ExtractionSide):
            raise ContractError(f"{key}: side must name extraction_method and footprint_set_id",
                                config_key=f"{key}.side")
        for f in ("extraction_method", "footprint_set_id"):
            _require(getattr(side, f), input=key, field=f, config_key=f"{key}.side.{f}")
        _require(self.metric_set, input=key, field="metric_set", config_key=f"{key}.metric_set")
        _choice(self.missing_member_policy, MISSING_MEMBER_POLICIES, input=key,
                field="missing_member_policy", config_key=f"{key}.missing_member_policy")
        if self.missing_member_policy == "reweight_available":
            if self.min_members is None:
                raise ContractError(f"{key}: reweight_available needs min_members",
                                    config_key=f"{key}.min_members")
            _positive_int(self.min_members, key=key, field="min_members", minimum=2)
            if self.min_members > k:
                raise ContractError(f"{key}: min_members exceeds the {k} members",
                                    config_key=f"{key}.min_members")
        elif self.min_members is not None:
            raise ContractError(f"{key}: min_members applies only to reweight_available",
                                config_key=f"{key}.min_members")
        for f, values, allowed in (("combinations", self.combinations, ENSEMBLE_COMBINATIONS),
                                   ("designs", self.designs, ENSEMBLE_DESIGNS),
                                   ("contrast_metrics", self.contrast_metrics,
                                    tuple(RANKING_DIRECTIONS))):
            if len(set(values)) != len(values):
                raise ContractError(f"{key}: duplicate {f}", config_key=f"{key}.{f}")
            for v in values:
                _choice(v, allowed, input=key, field=f, config_key=f"{key}.{f}")
        if not self.combinations:
            raise ContractError(f"{key}: no combinations declared ({list(ENSEMBLE_COMBINATIONS)})",
                                config_key=f"{key}.combinations")
        if not self.contrast_metrics:
            raise ContractError(f"{key}: no contrast_metrics declared",
                                config_key=f"{key}.contrast_metrics")
        object.__setattr__(self, "start", _iso_date(self.start, key=key, field="start"))
        object.__setattr__(self, "end", _iso_date(self.end, key=key, field="end"))
        if self.start and self.end and self.start > self.end:
            raise ContractError(f"{key}: start {self.start} is after end {self.end}",
                                config_key=f"{key}.start")
        if not isinstance(self.gap_filled_training, bool):
            raise ContractError(f"{key}: gap_filled_training must be true or false",
                                config_key=f"{key}.gap_filled_training")
        if self.gap_filled_training and self.reference_variant != "et_corrected_inclusive":
            raise ContractError(f"{key}: gap_filled_training applies only to "
                                "et_corrected_inclusive", config_key=f"{key}.gap_filled_training")
        self._check_subsets(key)
        if self.site_footprint_sets is not None:
            m = self.site_footprint_sets
            if not isinstance(m, Mapping) or not m:
                raise ContractError(f"{key}: site_footprint_sets maps each site to its "
                                    "footprint set", config_key=f"{key}.site_footprint_sets")
            if self.sites is None or set(m) != set(self.sites):
                raise ContractError(f"{key}: site_footprint_sets must map exactly the declared "
                                    "sites", config_key=f"{key}.site_footprint_sets")
            object.__setattr__(self, "site_footprint_sets",
                               MappingProxyType({str(k): str(v) for k, v in m.items()}))
        self._check_fit(key)

    def _check_subsets(self, key: str) -> None:
        out = []
        for i, sub in enumerate(self.subsets or ()):
            skey = f"{key}.subsets[{i}]"
            if not isinstance(sub, Mapping):
                raise ContractError(f"{skey}: a subset is {{subset_id, months}}", config_key=skey)
            _check_keys(sub, {"subset_id", "months"}, skey)
            sid = sub.get("subset_id")
            if not isinstance(sid, str) or not _METHOD_ID.match(sid) or sid == "all":
                raise ContractError(f"{skey}: subset_id must be snake_case and not 'all'",
                                    config_key=f"{skey}.subset_id")
            months = sub.get("months")
            if (not isinstance(months, list | tuple) or not months
                    or len(set(months)) != len(months)
                    or any(isinstance(m, bool) or not isinstance(m, int) or not 1 <= m <= 12
                           for m in months)):
                raise ContractError(f"{skey}: months must be distinct calendar months 1-12",
                                    config_key=f"{skey}.months")
            out.append(MappingProxyType({"subset_id": sid, "months": tuple(sorted(months))}))
        ids = [o["subset_id"] for o in out]
        if len(set(ids)) != len(ids):
            raise ContractError(f"{key}: duplicate subset_id", config_key=f"{key}.subsets")
        object.__setattr__(self, "subsets", tuple(out))

    def _check_fit(self, key: str) -> None:
        fit = bool(self.designs)
        grid = tuple(self.shrinkage_grid or ())
        if not fit:
            given = [f for f in (*self._FIT_FIELDS, "fold_unit") if getattr(self, f) is not None]
            if grid:
                given.append("shrinkage_grid")
            if self.gap_filled_training:
                given.append("gap_filled_training")
            if given:
                raise ContractError(f"{key}: {given} apply only when designs are declared",
                                    config_key=f"{key}.designs")
            return
        for f in self._FIT_FIELDS:
            if getattr(self, f) is None:
                raise ContractError(f"{key}: {f} not declared; fitting choices are fixed in "
                                    "advance, never tuned on agreement", config_key=f"{key}.{f}")
        if "leave_one_year_out" in self.designs:
            _choice(self.fold_unit, FOLD_UNITS, input=key, field="fold_unit",
                    config_key=f"{key}.fold_unit")
        elif self.fold_unit is not None:
            raise ContractError(f"{key}: fold_unit applies only to leave_one_year_out",
                                config_key=f"{key}.fold_unit")
        wy = self.water_year_start_month
        if isinstance(wy, bool) or not isinstance(wy, int) or not 1 <= wy <= 12:
            raise ContractError(f"{key}: water_year_start_month must be 1..12",
                                config_key=f"{key}.water_year_start_month")
        _positive_int(self.buffer_days, key=key, field="buffer_days", minimum=0)
        _positive_int(self.min_training_folds, key=key, field="min_training_folds", minimum=2)
        _positive_int(self.min_training_days_per_fold, key=key, field="min_training_days_per_fold")
        _positive_int(self.min_test_days_per_fold, key=key, field="min_test_days_per_fold")
        for f, allowed in (("method", ENSEMBLE_METHODS),
                           ("regularisation", ENSEMBLE_REGULARISATIONS),
                           ("selection_metric", ENSEMBLE_SELECTION_METRICS),
                           ("preprocessing", ENSEMBLE_PREPROCESSING),
                           ("insufficient_data", INSUFFICIENT_DATA_POLICIES)):
            _choice(getattr(self, f), allowed, input=key, field=f, config_key=f"{key}.{f}")
        values = []
        for a in grid:
            if isinstance(a, bool) or not isinstance(a, int | float) or not 0 <= a <= 1:
                raise ContractError(f"{key}: shrinkage_grid values must lie in [0, 1], got {a!r}",
                                    config_key=f"{key}.shrinkage_grid")
            values.append(float(a))
        if not values or len(set(values)) != len(values):
            raise ContractError(f"{key}: shrinkage_grid must be a non-empty list without "
                                "duplicates", config_key=f"{key}.shrinkage_grid")
        if 1.0 not in values:
            raise ContractError(f"{key}: shrinkage_grid must include 1 (equal weights, the "
                                "simple mean), so shrinkage can always return to it",
                                config_key=f"{key}.shrinkage_grid")
        object.__setattr__(self, "shrinkage_grid", tuple(sorted(values)))


def _str_tuple(value: object, *, key: str, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str) or not isinstance(value, list | tuple):
        raise ContractError(f"{key}: {field} must be a list", config_key=f"{key}.{field}")
    return tuple(str(v) for v in value)


@dataclass(frozen=True)
class FootprintStackSpec:
    """Per-timestep footprint stacks of one station (guide step 6 input).

    ``stack_dir`` holds ``<site>_*_timesteps.npz`` files written upstream
    (``tools/upstream/run_timestep_footprints.py``). Every ``footprint_aggregations``
    method whose target set (``weighting_method: etcompare_aggregation:<method_id>``,
    ``set_id`` starting with ``<site>_``) is declared is aggregated from them.
    ``compare_sets`` names existing sets compared day by day with each aggregate
    (total-variation distance).
    """

    site_id: str
    stack_dir: Path
    compare_sets: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        key = f"footprint_stacks.{self.site_id}"
        _require(self.site_id, input="footprint stack", field="site_id",
                 config_key="footprint_stacks[].site_id")
        _require(self.stack_dir, input=key, field="stack_dir", config_key=f"{key}.stack_dir")
        object.__setattr__(self, "compare_sets",
                           _str_tuple(self.compare_sets, key=key, field="compare_sets"))


@dataclass(frozen=True)
class FootprintComparisonSpec:
    """Step-6 comparison of raster extractions that differ only in footprint set.

    The first extraction is the baseline. ``min_retained_frac`` is declared
    before any result is seen and applied identically to every set.
    """

    comparison_id: str
    extractions: tuple[str, ...]
    methods: tuple[str, ...] = ("footprint_conservative",
                                "footprint_conservative_common_support")
    min_retained_frac: float | None = None
    common_models: bool = False

    def __post_init__(self) -> None:
        key = f"footprint_comparisons.{self.comparison_id}"
        _require(self.comparison_id, input="footprint comparison", field="comparison_id",
                 config_key="footprint_comparisons[].comparison_id")
        object.__setattr__(self, "extractions",
                           _str_tuple(self.extractions, key=key, field="extractions"))
        object.__setattr__(self, "methods",
                           _str_tuple(self.methods, key=key, field="methods"))
        if len(self.extractions) < 2:
            raise ContractError(f"{key}: at least two extractions are compared",
                                config_key=f"{key}.extractions")
        if not self.methods:
            raise ContractError(f"{key}: no extraction methods", config_key=f"{key}.methods")
        m = self.min_retained_frac
        if m is not None and not 0 < float(m) <= 1:
            raise ContractError(f"{key}: min_retained_frac must be in (0, 1]",
                                config_key=f"{key}.min_retained_frac")
        if not isinstance(self.common_models, bool):
            raise ContractError(f"{key}: common_models must be true or false",
                                config_key=f"{key}.common_models")


@dataclass(frozen=True)
class RunSpec:
    """Workflow switches (``run:``). ``skip`` lists declared analyses not to run."""

    skip: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "skip", _str_tuple(
            self.skip, key="run", field="skip"))
        bad = set(self.skip) - set(WORKFLOW_ANALYSES)
        if bad:
            raise ContractError(f"run.skip: unknown analyses {sorted(bad)}; expected "
                                f"{list(WORKFLOW_ANALYSES)}", config_key="run.skip")


@dataclass(frozen=True)
class ReportSpec:
    """Report settings (``report:``).

    ``synthetic: true`` labels every page and caption as synthetic and requires
    every station ID to start with ``synthetic_site_prefix`` (default ``XX-``, an ISO 3166
    code assigned to no country), so a synthetic
    report cannot carry a real station's name.
    """

    title: str = "etcompare report"
    formats: tuple[str, ...] = REPORT_FORMATS
    figure_formats: tuple[str, ...] = FIGURE_FORMATS
    synthetic: bool = False
    synthetic_site_prefix: str = "XX-"
    #: Comparisons that get figures (default: all declared).
    comparisons: tuple[str, ...] | None = None
    #: Pixel density of PNG figures.
    dpi: int = 120

    def __post_init__(self) -> None:
        for f, allowed in (("formats", REPORT_FORMATS), ("figure_formats", FIGURE_FORMATS)):
            v = _str_tuple(getattr(self, f), key="report", field=f)
            if not v:
                raise ContractError(f"report.{f}: at least one of {list(allowed)}",
                                    config_key=f"report.{f}")
            bad = set(v) - set(allowed)
            if bad:
                raise ContractError(f"report.{f}: unknown {sorted(bad)}; expected "
                                    f"{list(allowed)}", config_key=f"report.{f}")
            object.__setattr__(self, f, v)
        if self.comparisons is not None:
            object.__setattr__(self, "comparisons", _str_tuple(
                self.comparisons, key="report", field="comparisons"))
        if not isinstance(self.synthetic, bool):
            raise ContractError("report.synthetic must be true or false",
                                config_key="report.synthetic")
        if not isinstance(self.dpi, int) or self.dpi < 50:
            raise ContractError("report.dpi must be an integer >= 50", config_key="report.dpi")


@dataclass(frozen=True)
class CacheSpec:
    """Output cache (``cache:``). Raster identity is ``stat`` unless ``sha256`` is declared."""

    enabled: bool = True
    raster_identity: str = "stat"

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise ContractError("cache.enabled must be true or false",
                                config_key="cache.enabled")
        _choice(self.raster_identity, RASTER_IDENTITIES, input="cache",
                field="raster_identity", config_key="cache.raster_identity")


@dataclass(frozen=True)
class UpstreamSpec:
    """A declared upstream software version (``upstream:``) and its evidence.

    Installed versions are detected and recorded separately in the run manifest;
    a declaration records what produced the inputs, which may differ.
    """

    name: str
    evidence: str
    version: str | None = None
    commit: str | None = None
    source: str | None = None
    note: str | None = None

    def __post_init__(self) -> None:
        _require(self.name, input="upstream", field="name", config_key="upstream[].name")
        _choice(self.evidence, UPSTREAM_EVIDENCE, input=f"upstream {self.name}",
                field="evidence", config_key=f"upstream.{self.name}.evidence")
        if self.version is None and self.commit is None:
            raise ContractError(f"upstream {self.name}: declare a version or a commit "
                                "(use evidence: unknown with version: unknown otherwise)",
                                config_key=f"upstream.{self.name}.version")


@dataclass(frozen=True)
class DecisionSpec:
    """An open or provisional decision that outputs depend on (``decisions:``).

    ``applies_to`` names config sections (``bootstrap``) or entries
    (``bootstrap.utj_block_bootstrap``); the report lists the decision with every
    analysis it applies to. ``ref`` points to the decision log (e.g. ``D-048``).
    """

    ref: str
    status: str
    summary: str
    applies_to: tuple[str, ...]

    def __post_init__(self) -> None:
        key = f"decisions.{self.ref}"
        _require(self.ref, input="decision", field="ref", config_key="decisions[].ref")
        _choice(self.status, DECISION_STATUSES, input=key, field="status",
                config_key=f"{key}.status")
        _require(self.summary, input=key, field="summary", config_key=f"{key}.summary")
        object.__setattr__(self, "applies_to", _str_tuple(
            self.applies_to, key=key,
            field="applies_to"))
        if not self.applies_to:
            raise ContractError(f"{key}: applies_to is empty", config_key=f"{key}.applies_to")


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
    rankings: tuple[RankingSpec, ...] = ()
    diagnostics: tuple[DiagnosticsSpec, ...] = ()
    representativeness: tuple[RepresentativenessSpec, ...] = ()
    satellite_support: tuple[SatelliteSupportSpec, ...] = ()
    event_tables: tuple[EventTableSpec, ...] = ()
    event_analyses: tuple[EventAnalysisSpec, ...] = ()
    anomalies: tuple[AnomalySpec, ...] = ()
    water_balances: tuple[WaterBalanceSpec, ...] = ()
    scenarios: tuple[ScenarioSpec, ...] = ()
    ensembles: tuple[EnsembleSpec, ...] = ()
    footprint_stacks: tuple[FootprintStackSpec, ...] = ()
    footprint_comparisons: tuple[FootprintComparisonSpec, ...] = ()
    run: RunSpec = field(default_factory=RunSpec)
    report: ReportSpec = field(default_factory=ReportSpec)
    cache: CacheSpec = field(default_factory=CacheSpec)
    upstream: tuple[UpstreamSpec, ...] = ()
    decisions: tuple[DecisionSpec, ...] = ()
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
        self._check_rankings()
        self._check_diagnostics()
        self._check_representativeness(ids)
        names = [e.name for e in self.event_tables]
        if len(set(names)) != len(names):
            raise ContractError(f"duplicate event table name in {names}",
                                config_key="event_tables")
        for e in self.event_tables:
            e.check_stations(self.stations)
        cids = {c.comparison_id for c in self.comparisons}
        msets = {m.metric_set_id for m in self.metric_sets}
        for section, specs, id_field in (("event_analyses", self.event_analyses,
                                          "event_analysis_id"),
                                         ("anomalies", self.anomalies, "anomaly_id")):
            ids = [getattr(s, id_field) for s in specs]
            if len(set(ids)) != len(ids):
                raise ContractError(f"duplicate {id_field} in {ids}", config_key=section)
            for s in specs:
                key = f"{section}.{getattr(s, id_field)}"
                unknown = set(s.comparisons or ()) - cids
                if unknown:
                    raise ContractError(f"{key}: comparisons {sorted(unknown)} not declared",
                                        config_key=f"{key}.comparisons")
        for a in self.event_analyses:
            if a.event_table not in names:
                raise ContractError(f"event_analyses.{a.event_analysis_id}: event table "
                                    f"{a.event_table!r} not declared",
                                    config_key=f"event_analyses.{a.event_analysis_id}.event_table")
        for an in self.anomalies:
            if an.metric_set not in msets:
                raise ContractError(f"anomalies.{an.anomaly_id}: metric set {an.metric_set!r} "
                                    "not declared",
                                    config_key=f"anomalies.{an.anomaly_id}.metric_set")
        self._check_water_balances()
        self._check_ensembles()
        self._check_workflow()

    def _check_workflow(self) -> None:
        """Step-17 sections: stacks, footprint comparisons, report, upstream, decisions."""
        set_ids = {f.set_id for f in self.footprint_sets}
        sites = [s.site_id for s in self.footprint_stacks]
        if len(set(sites)) != len(sites):
            raise ContractError(f"duplicate footprint_stacks site_id in {sites}",
                                config_key="footprint_stacks")
        for st in self.footprint_stacks:
            key = f"footprint_stacks.{st.site_id}"
            if st.site_id not in self.stations:
                raise ContractError(f"{key}: station not declared",
                                    config_key=f"stations.{st.site_id}")
            unknown = set(st.compare_sets) - set_ids
            if unknown:
                raise ContractError(f"{key}: compare_sets {sorted(unknown)} not declared",
                                    config_key=f"{key}.compare_sets")
        extractions = {x.name: x for x in self.raster_extractions}
        ids = [c.comparison_id for c in self.footprint_comparisons]
        if len(set(ids)) != len(ids) or set(ids) & {c.comparison_id for c in self.comparisons}:
            raise ContractError(f"footprint_comparisons: duplicate or reused comparison_id in "
                                f"{ids}", config_key="footprint_comparisons")
        for fc in self.footprint_comparisons:
            key = f"footprint_comparisons.{fc.comparison_id}"
            missing = [n for n in fc.extractions if n not in extractions]
            if missing:
                raise ContractError(f"{key}: raster extractions {missing} not declared",
                                    config_key=f"{key}.extractions")
            specs = [extractions[n] for n in fc.extractions]
            if len({x.site_id for x in specs}) != 1:
                raise ContractError(f"{key}: extractions are for different sites",
                                    config_key=f"{key}.extractions")
            if any(not x.footprint_set_id for x in specs):
                raise ContractError(f"{key}: every extraction needs a footprint set",
                                    config_key=f"{key}.extractions")
        names = [u.name for u in self.upstream]
        if len(set(names)) != len(names):
            raise ContractError(f"duplicate upstream name in {names}", config_key="upstream")
        refs = [d.ref for d in self.decisions]
        if len(set(refs)) != len(refs):
            raise ContractError(f"duplicate decision ref in {refs}", config_key="decisions")
        for d in self.decisions:
            for target in d.applies_to:
                section, _, entry = target.partition(".")
                if section not in CONFIG_SECTIONS:
                    raise ContractError(f"decisions.{d.ref}: applies_to {target!r} names no "
                                        "configuration section",
                                        config_key=f"decisions.{d.ref}.applies_to")
                if entry and entry not in self.section_ids(section):
                    raise ContractError(f"decisions.{d.ref}: applies_to {target!r}: no entry "
                                        f"{entry!r} in {section}",
                                        config_key=f"decisions.{d.ref}.applies_to")
        r = self.report
        if r.comparisons is not None:
            unknown = set(r.comparisons) - {c.comparison_id for c in self.comparisons}
            if unknown:
                raise ContractError(f"report.comparisons: {sorted(unknown)} not declared",
                                    config_key="report.comparisons")
        if r.synthetic:
            real = [s for s in self.stations if not s.startswith(r.synthetic_site_prefix)]
            if real:
                raise ContractError(
                    f"report.synthetic: station(s) {real} do not start with "
                    f"{r.synthetic_site_prefix!r}; a synthetic report never names a real "
                    "station", config_key="report.synthetic")

    def section_ids(self, section: str) -> tuple[str, ...]:
        """Entry IDs of a configuration section (empty for sections without entries)."""
        if section in ("stations", "products"):
            return tuple(getattr(self, section))
        f = _SECTION_ID_FIELDS.get(section)
        if f is None:
            return ()
        return tuple(str(getattr(x, f)) for x in getattr(self, section))

    def _check_ensembles(self) -> None:
        eids = [e.ensemble_id for e in self.ensembles]
        if len(set(eids)) != len(eids):
            raise ContractError(f"duplicate ensemble_id in {eids}", config_key="ensembles")
        msets = {m.metric_set_id: m for m in self.metric_sets}
        boots = {b.bootstrap_id: b for b in self.bootstrap}
        sets = {f.set_id: f for f in self.footprint_sets}
        sources = ({x.name for x in self.raster_extractions}
                   | {t.name for t in self.extracted_tables})
        for e in self.ensembles:
            key = f"ensembles.{e.ensemble_id}"
            for m in e.members:
                p = self.products.get(m)
                if p is None:
                    raise ContractError(f"{key}: member {m!r} is not a declared product",
                                        config_key="products")
                if p.is_ensemble:
                    raise ContractError(f"{key}: {m!r} is an ensemble product (is_ensemble); "
                                        "the official ensemble is a comparison product, never "
                                        "an independent member", config_key=f"{key}.members")
            if e.official is not None:
                p = self.products.get(e.official)
                if p is None or not p.is_ensemble:
                    raise ContractError(f"{key}: official {e.official!r} must be a declared "
                                        "product with is_ensemble: true",
                                        config_key=f"{key}.official")
            ms = msets.get(e.metric_set)
            if ms is None:
                raise ContractError(f"{key}: metric set {e.metric_set!r} not declared",
                                    config_key=f"{key}.metric_set")
            missing = set(e.contrast_metrics) - set(ms.metrics)
            if missing:
                raise ContractError(f"{key}: contrast_metrics {sorted(missing)} are not computed "
                                    f"by metric set {e.metric_set}",
                                    config_key=f"{key}.contrast_metrics")
            if e.bootstrap is not None:
                b = boots.get(e.bootstrap)
                if b is None:
                    raise ContractError(f"{key}: bootstrap {e.bootstrap!r} not declared",
                                        config_key=f"{key}.bootstrap")
                if b.metric_set != e.metric_set:
                    raise ContractError(f"{key}: bootstrap {e.bootstrap} resamples metric set "
                                        f"{b.metric_set!r}, not {e.metric_set!r}",
                                        config_key=f"{key}.bootstrap")
            unknown = set(e.sources) - sources
            if unknown:
                raise ContractError(f"{key}: sources {sorted(unknown)} not declared",
                                    config_key=f"{key}.sources")
            for s in e.sites or ():
                if s not in self.stations:
                    raise ContractError(f"{key}: station {s} not declared",
                                        config_key=f"stations.{s}")
            fp = e.side.footprint_set_id
            if e.site_footprint_sets is not None:
                if fp in sets:
                    raise ContractError(f"{key}: with site_footprint_sets, side.footprint_set_id "
                                        f"is a label and must not be the declared set {fp!r}",
                                        config_key=f"{key}.side")
                mapped = list(e.site_footprint_sets.values())
            else:
                mapped = [fp] if fp != "none" else []
            for set_id in mapped:
                f = sets.get(set_id)
                if f is None:
                    raise ContractError(f"{key}: footprint set {set_id!r} not declared; its "
                                        "dependence on the target measurements is unknown",
                                        config_key="footprint_sets")
                if f.target_dependence != "no":
                    raise ContractError(f"{key}: footprint set {set_id} is weighted by the "
                                        "target measurements (D-037); it never enters an "
                                        "ensemble experiment", config_key=f"{key}.side")

    def ensemble(self, ensemble_id: str) -> EnsembleSpec:
        for e in self.ensembles:
            if e.ensemble_id == ensemble_id:
                return e
        raise KeyError(ensemble_id)

    def _check_water_balances(self) -> None:
        wids = [w.water_balance_id for w in self.water_balances]
        if len(set(wids)) != len(wids):
            raise ContractError(f"duplicate water_balance_id in {wids}",
                                config_key="water_balances")
        aggs = {a.aggregation_id: a for a in self.period_aggregations}
        for w in self.water_balances:
            key = f"water_balances.{w.water_balance_id}"
            if w.site_id not in self.stations:
                raise ContractError(f"{key}: station {w.site_id} not declared",
                                    config_key=f"stations.{w.site_id}")
            if not any(t.site_id == w.site_id for t in self.station_tables):
                raise ContractError(f"{key}: station {w.site_id} has no station table",
                                    config_key="station_tables")
            agg = aggs.get(w.period_aggregation)
            if agg is None:
                raise ContractError(f"{key}: period_aggregation {w.period_aggregation!r} not "
                                    "declared; water balances reuse the step-8 periods",
                                    config_key=f"{key}.period_aggregation")
            outside = set(w.periods or ()) - set(agg.periods)
            if outside:
                raise ContractError(f"{key}: periods {sorted(outside)} are not periods of "
                                    f"{agg.aggregation_id}", config_key=f"{key}.periods")
        sids = [s.scenario_id for s in self.scenarios]
        if len(set(sids)) != len(sids):
            raise ContractError(f"duplicate scenario_id in {sids}", config_key="scenarios")
        balances = {w.water_balance_id: w for w in self.water_balances}
        for s in self.scenarios:
            key = f"scenarios.{s.scenario_id}"
            wb = balances.get(s.water_balance)
            if wb is None:
                raise ContractError(f"{key}: water balance {s.water_balance!r} not declared",
                                    config_key=f"{key}.water_balance")
            for p in s.perturbations:
                t = wb.terms[p.term]
                if t.status == "unknown":
                    raise ContractError(
                        f"{key}: term {p.term} is unknown in {wb.water_balance_id}; a distribution "
                        "cannot give an unknown term a value (declare it as a scenario term with "
                        "daily_mm and a justification first)", config_key=f"{key}.perturbations")
                if t.quantity == "storage_state" and p.temporal_dependence != "systematic":
                    raise ContractError(f"{key}: a storage_state change can only be perturbed "
                                        "systematically", config_key=f"{key}.perturbations")

    def water_balance(self, water_balance_id: str) -> WaterBalanceSpec:
        for w in self.water_balances:
            if w.water_balance_id == water_balance_id:
                return w
        raise KeyError(water_balance_id)

    def scenario(self, scenario_id: str) -> ScenarioSpec:
        for s in self.scenarios:
            if s.scenario_id == scenario_id:
                return s
        raise KeyError(scenario_id)

    def event_analysis(self, event_analysis_id: str) -> EventAnalysisSpec:
        for a in self.event_analyses:
            if a.event_analysis_id == event_analysis_id:
                return a
        raise KeyError(event_analysis_id)

    def anomaly_spec(self, anomaly_id: str) -> AnomalySpec:
        for a in self.anomalies:
            if a.anomaly_id == anomaly_id:
                return a
        raise KeyError(anomaly_id)

    def event_table(self, name: str) -> EventTableSpec:
        for e in self.event_tables:
            if e.name == name:
                return e
        raise KeyError(name)

    def _check_representativeness(self, footprint_set_ids: list[str]) -> None:
        rids = [r.representativeness_id for r in self.representativeness]
        if len(set(rids)) != len(rids):
            raise ContractError(f"duplicate representativeness_id in {rids}",
                                config_key="representativeness")
        for r in self.representativeness:
            key = f"representativeness.{r.representativeness_id}"
            if r.site_id not in self.stations:
                raise ContractError(f"{key}: station {r.site_id} not declared",
                                    config_key=f"stations.{r.site_id}")
            unknown = set(r.footprint_sets) - set(footprint_set_ids)
            if unknown:
                raise ContractError(f"{key}: footprint sets {sorted(unknown)} not declared",
                                    config_key=f"{key}.footprint_sets")
        sids = [s.satellite_support_id for s in self.satellite_support]
        if len(set(sids)) != len(sids):
            raise ContractError(f"duplicate satellite_support_id in {sids}",
                                config_key="satellite_support")
        sets = {m.metric_set_id: m for m in self.metric_sets}
        cids = {c.comparison_id: c for c in self.comparisons}
        for s in self.satellite_support:
            key = f"satellite_support.{s.satellite_support_id}"
            ms = sets.get(s.metric_set)
            if ms is None:
                raise ContractError(f"{key}: metric_set {s.metric_set!r} not declared",
                                    config_key=f"{key}.metric_set")
            missing = set(s.metrics) - set(ms.metrics)
            if missing:
                raise ContractError(f"{key}: metrics {sorted(missing)} are not computed by metric "
                                    f"set {ms.metric_set_id}", config_key=f"{key}.metrics")
            for cid in s.comparisons or ():
                c = cids.get(cid)
                if c is None:
                    raise ContractError(f"{key}: comparison {cid!r} not declared",
                                        config_key=f"{key}.comparisons")
                if not c.freeze_models:
                    raise ContractError(f"{key}: comparison {cid} does not freeze days across "
                                        "models; clear/interpolated subsets need one frozen cohort",
                                        config_key=f"{key}.comparisons")
            unknown = {m for src in s.sources for m in src.models} - set(self.products)
            if unknown:
                raise ContractError(f"{key}: support for undeclared models {sorted(unknown)}",
                                    config_key=f"{key}.sources")

    def representativeness_spec(self, representativeness_id: str) -> RepresentativenessSpec:
        for r in self.representativeness:
            if r.representativeness_id == representativeness_id:
                return r
        raise KeyError(representativeness_id)

    def satellite_support_spec(self, satellite_support_id: str) -> SatelliteSupportSpec:
        for s in self.satellite_support:
            if s.satellite_support_id == satellite_support_id:
                return s
        raise KeyError(satellite_support_id)

    def _check_diagnostics(self) -> None:
        dids = [d.diagnostics_id for d in self.diagnostics]
        if len(set(dids)) != len(dids):
            raise ContractError(f"duplicate diagnostics_id in {dids}", config_key="diagnostics")
        sets = {m.metric_set_id: m for m in self.metric_sets}
        boots = {b.bootstrap_id: b for b in self.bootstrap}
        cids = {c.comparison_id for c in self.comparisons}
        for d in self.diagnostics:
            key = f"diagnostics.{d.diagnostics_id}"
            ms = sets.get(d.metric_set)
            if ms is None:
                raise ContractError(f"{key}: metric_set {d.metric_set!r} not declared in "
                                    "metric_sets", config_key=f"{key}.metric_set")
            missing = set(d.metrics) - set(ms.metrics)
            if missing:
                raise ContractError(f"{key}: metrics {sorted(missing)} are not computed by metric "
                                    f"set {ms.metric_set_id}", config_key=f"{key}.metrics")
            if d.bootstrap is not None:
                b = boots.get(d.bootstrap)
                if b is None:
                    raise ContractError(f"{key}: bootstrap {d.bootstrap!r} not declared",
                                        config_key=f"{key}.bootstrap")
                if b.metric_set != d.metric_set:
                    raise ContractError(f"{key}: bootstrap {b.bootstrap_id} resamples metric set "
                                        f"{b.metric_set}, not {d.metric_set}",
                                        config_key=f"{key}.bootstrap")
            unknown = set(d.comparisons or ()) - cids
            if unknown:
                raise ContractError(f"{key}: comparisons {sorted(unknown)} not declared",
                                    config_key=f"{key}.comparisons")

    def diagnostics_spec(self, diagnostics_id: str) -> DiagnosticsSpec:
        for d in self.diagnostics:
            if d.diagnostics_id == diagnostics_id:
                return d
        raise KeyError(diagnostics_id)

    def _check_rankings(self) -> None:
        rids = [r.ranking_id for r in self.rankings]
        if len(set(rids)) != len(rids):
            raise ContractError(f"duplicate ranking_id in {rids}", config_key="rankings")
        sets = {m.metric_set_id: m for m in self.metric_sets}
        boots = {b.bootstrap_id: b for b in self.bootstrap}
        cmps = {c.comparison_id: c for c in self.comparisons}
        for r in self.rankings:
            key = f"rankings.{r.ranking_id}"
            ms = sets.get(r.metric_set)
            if ms is None:
                raise ContractError(f"{key}: metric_set {r.metric_set!r} not declared in "
                                    "metric_sets", config_key=f"{key}.metric_set")
            b = boots.get(r.bootstrap)
            if b is None:
                raise ContractError(f"{key}: bootstrap {r.bootstrap!r} not declared",
                                    config_key=f"{key}.bootstrap")
            if b.metric_set != r.metric_set:
                raise ContractError(f"{key}: bootstrap {b.bootstrap_id} resamples metric set "
                                    f"{b.metric_set}, not {r.metric_set}",
                                    config_key=f"{key}.bootstrap")
            missing = set(r.metrics) - set(ms.metrics)
            if missing:
                raise ContractError(f"{key}: metrics {sorted(missing)} are not computed by "
                                    f"metric set {ms.metric_set_id}", config_key=f"{key}.metrics")
            if r.period_equivalence_margins and not ms.period_products:
                raise ContractError(f"{key}: period_equivalence_margins given but metric set "
                                    f"{ms.metric_set_id} scores no period products",
                                    config_key=f"{key}.period_equivalence_margins")
            for cid in r.comparisons or ():
                c = cmps.get(cid)
                if c is None:
                    raise ContractError(f"{key}: comparison {cid!r} not declared",
                                        config_key=f"{key}.comparisons")
                if c.ranking_scope == "none":
                    raise ContractError(
                        f"{key}: comparison {cid} (kind {c.kind}) has ranking scope 'none'; "
                        "only common_date and pairwise_model cohorts can be ranked",
                        config_key=f"{key}.comparisons")
                for what, ids in (("metric set", ms.comparisons), ("bootstrap", b.comparisons)):
                    if ids is not None and cid not in ids:
                        raise ContractError(f"{key}: comparison {cid} is not covered by the "
                                            f"{what}", config_key=f"{key}.comparisons")

    def ranking_spec(self, ranking_id: str) -> RankingSpec:
        for r in self.rankings:
            if r.ranking_id == ranking_id:
                return r
        raise KeyError(ranking_id)

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
    top = set(CONFIG_SECTIONS)
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
        rasters.append(OpenETRasterSource(
            root=_path(body["root"], base_dir),  # type: ignore[arg-type]
            recursive=bool(body.get("recursive", True)),
            processing_tags=tuple(body.get("processing_tags") or ()),
            on_conflict=body.get("on_conflict", "error")))

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

    rankings = []
    rk_fields = set(RankingSpec.__dataclass_fields__)
    for i, body in enumerate(data.get("rankings") or []):
        _check_keys(body, rk_fields, f"rankings[{i}]")
        kw = {f: body.get(f) for f in ("ranking_id", "metric_set", "bootstrap", "metrics")}
        kw.update({k: v for k, v in body.items() if k not in kw})
        rankings.append(RankingSpec(**kw))

    diagnostics = []
    dg_fields = set(DiagnosticsSpec.__dataclass_fields__)
    for i, body in enumerate(data.get("diagnostics") or []):
        _check_keys(body, dg_fields, f"diagnostics[{i}]")
        kw = {f: body.get(f) for f in ("diagnostics_id", "metric_set", "min_bin_days")}
        kw.update({k: v for k, v in body.items() if k not in kw})
        diagnostics.append(DiagnosticsSpec(**kw))

    represent = []
    rp_fields = set(RepresentativenessSpec.__dataclass_fields__)
    for i, body in enumerate(data.get("representativeness") or []):
        _check_keys(body, rp_fields, f"representativeness[{i}]")
        kw = {f: body.get(f) for f in ("representativeness_id", "site_id", "footprint_sets",
                                       "layers", "matching_policies")}
        kw.update({k: v for k, v in body.items() if k not in kw})
        kw["reference_grid_raster"] = _path(kw.get("reference_grid_raster"), base_dir)
        layers = {}
        for name, lay in (kw.get("layers") or {}).items():
            if isinstance(lay, Mapping):
                lay = dict(lay)
                lay["assets"] = [{**a, "path": _path(a.get("path"), base_dir)}
                                 if isinstance(a, Mapping) else a
                                 for a in (lay.get("assets") or [])]
                if lay.get("legend_csv") is not None:
                    lay["legend_csv"] = _path(lay["legend_csv"], base_dir)
            layers[name] = lay
        kw["layers"] = layers
        kw["polygons"] = [{**p, "path": _path(p.get("path"), base_dir)} if isinstance(p, Mapping)
                          else p for p in (kw.get("polygons") or [])]
        represent.append(RepresentativenessSpec(**kw))

    satellites = []
    ss_fields = set(SatelliteSupportSpec.__dataclass_fields__)
    for i, body in enumerate(data.get("satellite_support") or []):
        _check_keys(body, ss_fields, f"satellite_support[{i}]")
        kw = {f: body.get(f) for f in ("satellite_support_id", "metric_set", "min_subset_days")}
        kw.update({k: v for k, v in body.items() if k not in kw})
        kw["sources"] = [{**s, "path": _path(s.get("path"), base_dir)} if isinstance(s, Mapping)
                         else s for s in (kw.get("sources") or [])]
        satellites.append(SatelliteSupportSpec(**kw))

    events = []
    ev_fields = set(EventTableSpec.__dataclass_fields__)
    for i, body in enumerate(data.get("event_tables") or []):
        _check_keys(body, ev_fields, f"event_tables[{i}]")
        kw = {f: body.get(f) for f in ev_fields}
        kw["path"] = _path(kw.get("path"), base_dir)
        events.append(EventTableSpec(**kw))

    event_analyses = []
    ea_fields = set(EventAnalysisSpec.__dataclass_fields__)
    for i, body in enumerate(data.get("event_analyses") or []):
        _check_keys(body, ea_fields, f"event_analyses[{i}]")
        kw = {f: body.get(f) for f in ("event_analysis_id", "event_table", "management_kinds",
                                       "pre_days", "post_offset_days", "post_days",
                                       "min_window_completeness", "baseline_min_mm",
                                       "recovery_fraction", "recovery_max_days",
                                       "recovery_smoothing_days", "approximate_policy")}
        kw.update({k: v for k, v in body.items() if k not in kw})
        event_analyses.append(EventAnalysisSpec(**kw))

    anomalies = []
    an_fields = set(AnomalySpec.__dataclass_fields__)
    for i, body in enumerate(data.get("anomalies") or []):
        _check_keys(body, an_fields, f"anomalies[{i}]")
        kw = {f: body.get(f) for f in ("anomaly_id", "metric_set", "method", "min_days")}
        kw.update({k: v for k, v in body.items() if k not in kw})
        anomalies.append(AnomalySpec(**kw))

    def term_paths(terms: Any) -> Any:
        if not isinstance(terms, Mapping):
            return terms
        return {t: ({**b, "path": _path(b.get("path"), base_dir)}
                    if isinstance(b, Mapping) and b.get("path") is not None else b)
                for t, b in terms.items()}

    balances = []
    wb_fields = set(WaterBalanceSpec.__dataclass_fields__)
    for i, body in enumerate(data.get("water_balances") or []):
        _check_keys(body, wb_fields, f"water_balances[{i}]")
        kw = {f: body.get(f) for f in ("water_balance_id", "site_id", "period_aggregation",
                                       "boundary", "equation", "sign_convention", "terms")}
        kw.update({k: v for k, v in body.items() if k not in kw})
        if isinstance(kw.get("boundary"), Mapping):
            kw["boundary"] = {**kw["boundary"],
                              "path": _path(kw["boundary"].get("path"), base_dir)}
        kw["terms"] = term_paths(kw.get("terms"))
        balances.append(WaterBalanceSpec(**kw))

    scenarios = []
    sc_fields = set(ScenarioSpec.__dataclass_fields__)
    for i, body in enumerate(data.get("scenarios") or []):
        _check_keys(body, sc_fields, f"scenarios[{i}]")
        kw = {f: body.get(f) for f in ("scenario_id", "water_balance", "category",
                                       "justification")}
        kw.update({k: v for k, v in body.items() if k not in kw})
        kw["alternatives"] = [{**a, "terms": term_paths(a.get("terms"))}
                              if isinstance(a, Mapping) else a
                              for a in (kw.get("alternatives") or [])]
        scenarios.append(ScenarioSpec(**kw))

    ensembles = []
    en_fields = set(EnsembleSpec.__dataclass_fields__) - {"_FIT_FIELDS"}
    for i, body in enumerate(data.get("ensembles") or []):
        _check_keys(body, en_fields, f"ensembles[{i}]")
        kw = {f: body.get(f) for f in ("ensemble_id", "members", "reference_variant", "side",
                                       "metric_set", "missing_member_policy")}
        kw.update({k: v for k, v in body.items() if k not in kw})
        ensembles.append(EnsembleSpec(**kw))

    def entries(section: str, cls: Any, required: tuple[str, ...]) -> list[dict[str, Any]]:
        out = []
        allowed = set(cls.__dataclass_fields__)
        for i, body in enumerate(data.get(section) or []):
            _check_keys(body, allowed, f"{section}[{i}]")
            kw = {f: body.get(f) for f in required}
            kw.update({k: v for k, v in body.items() if k not in kw})
            out.append(kw)
        return out

    stacks = []
    for kw in entries("footprint_stacks", FootprintStackSpec, ("site_id", "stack_dir")):
        kw["stack_dir"] = _path(kw.get("stack_dir"), base_dir)
        stacks.append(FootprintStackSpec(**kw))
    footprint_comparisons = [FootprintComparisonSpec(**kw) for kw in entries(
        "footprint_comparisons", FootprintComparisonSpec, ("comparison_id", "extractions"))]
    upstream = [UpstreamSpec(**kw) for kw in entries("upstream", UpstreamSpec,
                                                       ("name", "evidence"))]
    decisions = [DecisionSpec(**kw) for kw in entries(
        "decisions", DecisionSpec, ("ref", "status", "summary", "applies_to"))]
    singles: dict[str, Any] = {}
    for section, cls in (("run", RunSpec), ("report", ReportSpec), ("cache", CacheSpec)):
        body = data.get(section)
        if body is None:
            singles[section] = cls()
            continue
        _check_keys(body, set(cls.__dataclass_fields__), section)
        singles[section] = cls(**body)

    return Config(
        footprint_stacks=tuple(stacks),
        footprint_comparisons=tuple(footprint_comparisons),
        upstream=tuple(upstream),
        decisions=tuple(decisions),
        **singles,
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
        rankings=tuple(rankings),
        diagnostics=tuple(diagnostics),
        representativeness=tuple(represent),
        satellite_support=tuple(satellites),
        event_tables=tuple(events),
        event_analyses=tuple(event_analyses),
        anomalies=tuple(anomalies),
        water_balances=tuple(balances),
        scenarios=tuple(scenarios),
        ensembles=tuple(ensembles),
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
