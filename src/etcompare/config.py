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

#: Canonical unit of every daily ET column: depth per declared station day.
ET_UNIT: Final = "mm d-1"
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
        for col in self.reference_columns.values():
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

    def footprint_set(self, set_id: str) -> FootprintSetSpec:
        for f in self.footprint_sets:
            if f.set_id == set_id:
                return f
        raise KeyError(set_id)

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
           "footprint_aggregations", "output_dir"}
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
