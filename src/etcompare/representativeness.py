"""Landscape representativeness of footprints and fixed supports (guide step 13).

:func:`assess_landscape` describes what land each support sees: the daily footprint
and the fixed supports of D-029 (``pixel_window_<n>x<n>`` and ``nearest_tower_pixel``
on the model grid) plus declared polygons. It writes five tables:

- ``landscape_class_fractions``: weight on each class of a categorical map;
- ``landscape_index_summaries``: weighted mean and variability of a continuous index;
- ``landscape_method_differences``: footprint minus fixed support, on frozen days;
- ``representativeness_labels``: the station's metadata land cover and optional
  fluxfootprints HIGH/MEDIUM/LOW labels, kept apart from every number;
- ``representativeness_availability``: what ran, and why the rest did not.

Conservative transfer, never resampling
---------------------------------------
Every support is a set of cell-integrated weights on its own grid. They are moved onto
the layer's **native** grid by exact area overlap (:func:`etcompare.spatial.transfer_matrix`,
D-028). Class codes and index values never move: no categorical map is interpolated, and
a 30 m class is never presented as 10 m information. A footprint cell lying across two
map cells gives each its area share.

Denominators (D-029 style)
--------------------------
For a support with grid total ``T`` (the footprint-grid total, the normalized truncated
footprint for normalized weights, D-008; 1 for fixed supports) and domain weight ``D``
(``T`` for the full domain, the contour mass for a contour):

- ``valid_frac_of_grid_total = W_valid / T``: weight on valid (non-nodata) layer cells;
- ``nodata_frac_of_grid_total = W_nodata / T`` and
  ``outside_layer_frac_of_grid_total = (D - W_in_extent) / T`` are reported explicitly;
- ``class_frac_of_valid = W_class / W_valid``: conditional on valid cells, summing to 1
  over the class rows.

These are never multiplied together or with domain capture (``fp_domain_capture_frac``),
because their conditioning differs.

Continuous index
----------------
Over valid cells ``j`` with weight ``c_j > 0`` and finite value ``v_j``:
``m = sum c_j v_j / sum c_j``; ``weighted_std = sqrt(sum c_j (v_j - m)^2 / sum c_j)``
(population form, no small-sample correction: the weights are source fractions, not
replicate counts); ``n_eff = (sum c_j)^2 / sum c_j^2`` (Kish effective number of cells).

Contours
--------
:func:`contour_mask` takes the footprint alone: the highest-weight cells, in descending
order, until their cumulative weight reaches ``level`` times the footprint-grid total;
cells tied with the last one are included, so the contour mass is at least ``level``.
Contour rows (``domain = contour_<level>``) are a separate product. They never replace
full-domain rows, and the level is a fraction of the truncated grid total, not of the
original footprint.

Temporal matching
-----------------
Each layer asset has a reference period: a categorical map's vintage year, or a scene's
acquisition day. A declared :class:`~etcompare.config.MatchingPolicySpec` picks one
asset per footprint day (:func:`match_asset`). Every row records ``days_from_layer``,
``temporal_relation``, the footprint and layer years, and ``retrospective`` (the asset's
period ends after the day, so the row uses later information). A day without an
admissible asset is ``not_available`` for that day; nothing is filled.

Fixed supports and ET_mean
--------------------------
Fixed supports are evaluated on the footprint's frozen days with the same asset, so
footprint-minus-support differences compare like with like. ``ET_mean``
(``source_region_mean``) is the footprint-positive part of a daily footprint grid, a
varying source region (D-007); it is never treated as a field or a fixed window.

Labels are not accuracy
-----------------------
Landscape fractions and HIGH/MEDIUM/LOW classes describe surroundings; neither is a
quantitative ET accuracy guarantee. Labels never enter a metric or threshold. The IGBP
class of the station metadata is a site label, not a footprint-weighted fraction.
"""

from __future__ import annotations

import json
import math
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

import numpy as np
import pandas as pd

from etcompare._optional import require
from etcompare.config import (
    LandscapeLayerSpec,
    MatchingPolicySpec,
    RepresentativenessSpec,
    to_plain,
)
from etcompare.errors import ContractError, GridError
from etcompare.footprints import PreparedFootprint
from etcompare.schemas import (
    LANDSCAPE_CLASS_FRACTIONS,
    LANDSCAPE_INDEX_SUMMARIES,
    LANDSCAPE_METHOD_DIFFERENCES,
    REPRESENTATIVENESS_AVAILABILITY,
    REPRESENTATIVENESS_LABELS,
    CanonicalTable,
    TableSchema,
)
from etcompare.spatial import Grid, Transfer, same_crs, transfer_matrix, transform_coords
from etcompare.validation import Issue, ValidationReport

FOOTPRINT_METHOD = "footprint_conservative"
FULL_DOMAIN = "full_domain"
FULL_SUPPORT = "full_support"
STD_ESTIMATOR = ("sqrt(sum_j c_j (v_j - m)^2 / sum_j c_j) over valid cells j, m = sum_j c_j v_j "
                 "/ sum_j c_j; population form, no small-sample correction")
NOT_ACCURACY = ("landscape description of the support; not a quantitative ET accuracy "
                "guarantee")
IGBP_MEANING = ("station metadata land cover (IGBP); a site label, not a footprint-weighted "
                "fraction, and not an ET accuracy guarantee")


def contour_domain(level: float) -> str:
    return f"contour_{level:g}"


# --------------------------------------------------------------------------- building blocks

def contour_mask(weights: np.ndarray, level: float) -> np.ndarray:
    """Cells of the ``level`` cumulative-contribution contour, from the footprint alone.

    Cells are taken in descending weight until their sum reaches ``level`` times the
    grid total; every cell tied with the last one is included. Only positive cells can
    belong to the contour.
    """
    if not 0 < level < 1:
        raise ValueError(f"contour level {level} must lie in (0, 1)")
    w = np.asarray(weights, dtype="float64")
    if not np.isfinite(w).all() or (w < 0).any():
        raise ValueError("contour weights must be finite and non-negative")
    total = float(w.sum())
    if total <= 0:
        raise ValueError("contour of a footprint without positive weight")
    order = np.sort(w.ravel())[::-1]
    k = int(np.searchsorted(np.cumsum(order), level * total, side="left"))
    threshold = order[min(k, order.size - 1)]
    return (w >= threshold) & (w > 0)


@dataclass(frozen=True)
class LayerWeights:
    """Weight accounting of one support (and domain) on one layer asset."""

    weight_denominator: str
    weight_grid_total: float
    weight_domain: float
    weight_in_extent: float
    weight_valid: float
    weight_nodata: float
    n_cells_valid: int

    def fractions(self) -> dict[str, float]:
        t = self.weight_grid_total
        return {
            "valid_frac_of_grid_total": self.weight_valid / t,
            "nodata_frac_of_grid_total": self.weight_nodata / t,
            "outside_layer_frac_of_grid_total": max(self.weight_domain - self.weight_in_extent,
                                                    0.0) / t,
            "domain_frac_of_grid_total": self.weight_domain / t,
        }

    def columns(self) -> dict[str, Any]:
        return {"weight_denominator": self.weight_denominator,
                "weight_grid_total": self.weight_grid_total, "weight_domain": self.weight_domain,
                "weight_in_layer_extent": self.weight_in_extent,
                "weight_valid": self.weight_valid, "weight_nodata": self.weight_nodata,
                "n_cells_valid": self.n_cells_valid, **self.fractions()}


@dataclass(frozen=True)
class ClassComposition:
    """Weights of a support on the classes of a categorical map."""

    weights: LayerWeights
    class_weights: Mapping[int, float]

    def frac_of_valid(self) -> dict[int, float]:
        wv = self.weights.weight_valid
        return {k: v / wv for k, v in self.class_weights.items()} if wv > 0 else {}


def _accounting(c: np.ndarray, valid: np.ndarray, nodata: np.ndarray, *, denominator: str,
                weight_grid_total: float, weight_domain: float | None) -> LayerWeights:
    if (c < 0).any():
        raise GridError("negative support weight reached the layer; reject it upstream")
    if not weight_grid_total > 0:
        raise ValueError("weight_grid_total must be positive")
    in_extent = float(c.sum())
    return LayerWeights(denominator, float(weight_grid_total),
                        float(weight_domain if weight_domain is not None else weight_grid_total),
                        in_extent, float(c[valid].sum()), float(c[nodata].sum()),
                        int(((c > 0) & valid).sum()))


def class_composition(contribution: np.ndarray, classes: np.ndarray, *, nodata: float,
                      legend: Mapping[int, str], weight_grid_total: float,
                      weight_domain: float | None = None,
                      denominator: str = "footprint_grid_total") -> ClassComposition:
    """Weight of ``contribution`` (on the map's grid) on each class of ``classes``.

    Cells equal to ``nodata`` or non-finite are nodata. A class with weight that is not
    in ``legend``, or a non-integer code, raises :class:`ContractError`.
    """
    c = np.asarray(contribution, dtype="float64")
    k = np.asarray(classes, dtype="float64")
    if c.shape != k.shape:
        raise GridError(f"contribution shape {c.shape} != class map shape {k.shape}")
    nod = ~np.isfinite(k)
    if not math.isnan(nodata):
        nod |= k == nodata
    valid = ~nod
    acc = _accounting(c, valid, nod, denominator=denominator,
                      weight_grid_total=weight_grid_total, weight_domain=weight_domain)
    used = valid & (c > 0)
    codes = k[used]
    if (codes != np.round(codes)).any():
        raise ContractError("categorical layer holds non-integer codes; it is not a class map")
    out: dict[int, float] = {}
    if used.any():
        uniq, inv = np.unique(codes.astype(np.int64), return_inverse=True)
        sums = np.bincount(inv, weights=c[used])
        out = {int(u): float(s) for u, s in zip(uniq, sums, strict=True)}
    missing = sorted(set(out) - set(legend))
    if missing:
        raise ContractError(f"class code(s) {missing} carry weight but are not in the declared "
                            "legend; declare them before describing the landscape")
    return ClassComposition(acc, MappingProxyType(out))


@dataclass(frozen=True)
class IndexSummary:
    """Weighted mean, standard deviation and effective cell count of an index."""

    weights: LayerWeights
    weighted_mean: float
    weighted_std: float
    n_eff: float


def index_summary(contribution: np.ndarray, values: np.ndarray, *, weight_grid_total: float,
                  weight_domain: float | None = None,
                  denominator: str = "footprint_grid_total") -> IndexSummary | None:
    """Weighted summary of ``values`` (already scaled, NaN = nodata); None without valid cells."""
    c = np.asarray(contribution, dtype="float64")
    v = np.asarray(values, dtype="float64")
    if c.shape != v.shape:
        raise GridError(f"contribution shape {c.shape} != index shape {v.shape}")
    valid = np.isfinite(v)
    acc = _accounting(c, valid, ~valid, denominator=denominator,
                      weight_grid_total=weight_grid_total, weight_domain=weight_domain)
    sel = valid & (c > 0)
    if not sel.any():
        return None
    w, x = c[sel], v[sel]
    sw = float(w.sum())
    m = float(np.sum(w * x) / sw)
    sd = math.sqrt(max(float(np.sum(w * (x - m) ** 2) / sw), 0.0))
    return IndexSummary(acc, m, sd, sw * sw / float(np.sum(w * w)))


# --------------------------------------------------------------------------- matching

@dataclass(frozen=True)
class LayerMatch:
    """The asset chosen for one day and its temporal relation to the day."""

    asset_index: int
    days_from_layer: int
    temporal_relation: str
    retrospective: bool


def _relation(day: pd.Timestamp, start: pd.Timestamp, end: pd.Timestamp) -> tuple[int, str]:
    if end < day:
        return int((day - end).days), "past"
    if start > day:
        return int((day - start).days), "future"
    return 0, "contains_day"


def match_asset(day: pd.Timestamp | str, periods: Sequence[tuple[Any, Any]],
                policy: MatchingPolicySpec) -> LayerMatch | None:
    """Asset for ``day`` under ``policy``; None when no asset is admissible.

    ``nearest`` and ``within_n_days`` minimise ``|days_from_layer|``; a tie goes to the
    asset that uses no later information, then to the earlier asset.
    """
    d = pd.Timestamp(day)
    cands = []
    for i, (s, e) in enumerate(periods):
        start, end = pd.Timestamp(s), pd.Timestamp(e)
        days, rel = _relation(d, start, end)
        retro = bool(end > d)
        if policy.policy == "previous_only" and end > d:
            continue
        if policy.policy == "within_n_days" and abs(days) > int(policy.n_days):  # type: ignore[arg-type]
            continue
        cands.append((abs(days), retro, start, i, days, rel))
    if not cands:
        return None
    _, retro, _, i, days, rel = min(cands)
    return LayerMatch(i, days, rel, retro)


# --------------------------------------------------------------------------- supports

@dataclass(frozen=True)
class FixedSupport:
    """A support that does not change from day to day.

    Pixel supports carry weights on a sub-grid of the model grid; a polygon carries
    its geometry and CRS and is rasterised exactly onto each layer grid.
    """

    method_id: str
    weight_denominator: str
    grid: Grid | None = None
    weights: np.ndarray | None = field(default=None, repr=False)
    geometry: Any = field(default=None, repr=False)
    crs: str | None = None
    note: str | None = None


def pixel_window_support(grid: Grid, x: float, y: float, size: int) -> FixedSupport:
    """Equal weights on the whole pixels of the ``size x size`` block around ``(x, y)``.

    ``(x, y)`` are in ``grid``'s CRS. Pixels outside the grid are left out and counted
    in ``note``; the weights sum to 1 over the pixels inside.
    """
    if size < 1 or size % 2 == 0:
        raise ValueError("window size must be odd and positive")
    row, col = (int(math.floor(float(t))) for t in grid.rowcol(x, y))
    if not (0 <= row < grid.height and 0 <= col < grid.width):
        raise GridError("the tower lies outside the reference grid")
    h = size // 2
    r0, r1 = max(row - h, 0), min(row + h + 1, grid.height)
    c0, c1 = max(col - h, 0), min(col + h + 1, grid.width)
    n = (r1 - r0) * (c1 - c0)
    sub = grid.window(r0, c0, r1 - r0, c1 - c0)
    method = "nearest_tower_pixel" if size == 1 else f"pixel_window_{size}x{size}"
    note = None if n == size * size else f"{size * size - n} window pixel(s) outside the grid"
    return FixedSupport(method, "window_pixels", sub, np.full(sub.shape, 1.0 / n), note=note)


def nearest_pixel_support(grid: Grid, x: float, y: float) -> FixedSupport:
    """The model pixel containing ``(x, y)`` (diagnostic support, D-029)."""
    return pixel_window_support(grid, x, y, 1)


def polygon_support(polygon_id: str, geometry: Any, crs: str) -> FixedSupport:
    """A declared polygon; its area is distributed exactly over each layer grid."""
    if crs is None:
        raise GridError(f"polygon {polygon_id} has no declared CRS")
    return FixedSupport(f"polygon_{polygon_id}", "polygon_area", geometry=geometry, crs=crs)


def polygon_cell_fractions(geometry: Any, crs: str, grid: Grid, *,
                           segment_length: float | None = None) -> np.ndarray:
    """Share of the polygon's area in each cell of ``grid`` (sums to the share inside it).

    Edges are densified before reprojection (``segment_length`` in the polygon's CRS
    units; default 1/10 of the grid cell size when the CRSs differ) and areas are taken
    in the grid CRS. Needs the ``geo`` extra.
    """
    shapely = require("shapely", "Polygon support")
    geom = geometry
    if not same_crs(crs, grid.crs):
        seg = segment_length
        if seg is None:
            xs, ys = transform_coords(*grid.centers(), grid.crs, crs)  # type: ignore[arg-type]
            seg = max(float(np.hypot(np.diff(xs[0, :2]), np.diff(ys[0, :2]))[0]) / 10, 1e-9)
        geom = shapely.segmentize(geom, seg)
        coords = shapely.get_coordinates(geom)
        x, y = transform_coords(coords[:, 0], coords[:, 1], crs, grid.crs)  # type: ignore[arg-type]
        geom = shapely.set_coordinates(geom, np.column_stack([x, y]))
    if not shapely.is_valid(geom):
        raise GridError("polygon is invalid in the layer CRS")
    area = float(shapely.area(geom))
    if area <= 0:
        raise GridError("polygon has no area")
    X, Y = grid.corners()
    ring = np.stack([np.stack([X[:-1, :-1], Y[:-1, :-1]], -1),
                     np.stack([X[:-1, 1:], Y[:-1, 1:]], -1),
                     np.stack([X[1:, 1:], Y[1:, 1:]], -1), np.stack([X[1:, :-1], Y[1:, :-1]], -1),
                     np.stack([X[:-1, :-1], Y[:-1, :-1]], -1)], axis=2)
    cells = shapely.polygons(ring.reshape(-1, 5, 2))
    tree = shapely.STRtree(cells)
    idx = tree.query(geom, predicate="intersects")
    out = np.zeros(grid.size)
    if len(idx):
        out[idx] = shapely.area(shapely.intersection(cells[idx], geom)) / area
    return out.reshape(grid.shape)


# --------------------------------------------------------------------------- inputs

@dataclass(frozen=True)
class LayerAsset:
    """One asset of a layer, read on a window of its native grid.

    ``values``: class codes (categorical, nodata as declared) or scaled index values
    with NaN for nodata (continuous).
    """

    layer_id: str
    asset_index: int
    label: str
    grid: Grid
    values: np.ndarray = field(repr=False)
    reference_start: pd.Timestamp = field(default=pd.NaT)
    reference_end: pd.Timestamp = field(default=pd.NaT)
    vintage: int | None = None


@dataclass(frozen=True)
class FootprintDay:
    """A usable daily footprint of one set (see :func:`etcompare.footprints.prepare_footprint`)."""

    date: pd.Timestamp
    footprint: PreparedFootprint
    asset_id: str | None = None


#: ``labeler(contribution, classes, asset, layer, tower_xy) -> list of label dicts``
#: with keys radius_m, label, dominant_class, p_footprint, p_target, chi2, p_value.
Labeler = Callable[[np.ndarray, np.ndarray, LayerAsset, LandscapeLayerSpec,
                    tuple[float, float]], list[dict[str, Any]]]


@dataclass(frozen=True)
class LabelSource:
    """An optional label provider (e.g. the fluxfootprints adapter) and its identity."""

    labeler: Labeler
    version: str
    parameters: Mapping[str, Any]
    tower_xy: Mapping[str, tuple[float, float]]  # per layer, in the layer CRS


# --------------------------------------------------------------------------- result

@dataclass(frozen=True)
class RepresentativenessResult:
    """The step-13 landscape tables. Immutable; accessors return copies."""

    spec: RepresentativenessSpec
    _tables: Mapping[str, CanonicalTable] = field(repr=False)
    _issues: tuple[Issue, ...] = field(repr=False)
    attributes: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "attributes", MappingProxyType(dict(self.attributes)))
        object.__setattr__(self, "_tables", MappingProxyType(dict(self._tables)))

    def _get(self, name: str) -> CanonicalTable:
        t = self._tables[name]
        return CanonicalTable(t.schema, t.frame.copy(), dict(t.attributes))

    @property
    def class_fractions(self) -> CanonicalTable:
        return self._get("landscape_class_fractions")

    @property
    def index_summaries(self) -> CanonicalTable:
        return self._get("landscape_index_summaries")

    @property
    def differences(self) -> CanonicalTable:
        return self._get("landscape_method_differences")

    @property
    def labels(self) -> CanonicalTable:
        return self._get("representativeness_labels")

    @property
    def availability(self) -> CanonicalTable:
        return self._get("representativeness_availability")

    @property
    def report(self) -> ValidationReport:
        return ValidationReport(f"representativeness {self.spec.representativeness_id}",
                                list(self._issues))

    def tables(self) -> dict[str, CanonicalTable]:
        return {k: self._get(k) for k in self._tables}


def _frame(rows: list[dict[str, Any]], schema: TableSchema) -> pd.DataFrame:
    frame = pd.DataFrame(rows, columns=schema.names) if rows else schema.empty()
    for c in schema.columns:
        if c.kind in ("float", "int"):
            frame[c.name] = pd.to_numeric(frame[c.name])
    return frame.sort_values(list(schema.primary_key), ignore_index=True)


def _flags(*tokens: str | None) -> str | None:
    out = sorted({t for t in tokens if t})
    return ";".join(out) or None


# --------------------------------------------------------------------------- main

def assess_landscape(
    spec: RepresentativenessSpec,
    *,
    footprint_days: Mapping[str, Sequence[FootprintDay]],
    layers: Mapping[str, Sequence[LayerAsset]],
    fixed_supports: Sequence[FixedSupport] = (),
    footprint_unusable: Mapping[str, Mapping[pd.Timestamp, str]] | None = None,
    station_land_cover: str | None = None,
    label_source: LabelSource | None = None,
    unavailable: Sequence[Mapping[str, Any]] = (),
) -> RepresentativenessResult:
    """Describe the landscape of every footprint day and fixed support (module docstring).

    - ``footprint_days``: usable footprints per declared set.
    - ``layers``: assets per declared layer, read on windows of their native grids;
      a declared layer missing here is reported ``not_available``.
    - ``footprint_unusable``: rejected or missing footprint days per set, with reasons.
    - ``unavailable``: availability rows decided by the caller (e.g. an unreadable layer
      or an absent label source); keys as in ``representativeness_availability``.
    """
    rid = spec.representativeness_id
    site = spec.site_id
    report = ValidationReport(f"representativeness {rid}")
    base = {"representativeness_id": rid, "site_id": site}
    cls_rows: list[dict[str, Any]] = []
    idx_rows: list[dict[str, Any]] = []
    diff_rows: list[dict[str, Any]] = []
    lab_rows: list[dict[str, Any]] = []
    avail: list[dict[str, Any]] = []

    def availability(analysis: str, status: str, *, layer: str = "none", method: str = "all",
                     day_set: str = "all", policy: str = "all", domain: str = "all",
                     date_label: str = "all", code: str | None = None,
                     reason: str | None = None, n_days: int | None = None,
                     flags: str | None = None) -> None:
        avail.append({"analysis_id": rid, "site_id": site, "analysis": analysis,
                      "layer_id": layer, "extraction_method": method, "day_set": day_set,
                      "matching_policy": policy, "domain": domain, "date_label": date_label,
                      "status": status, "reason_code": code, "reason": reason,
                      "n_days": n_days, "flags": flags})

    for u in unavailable:
        availability(**{"status": "not_available", **u})
    kinds = {lay.kind for lay in spec.layers.values()}
    for kind, what in (("categorical", "land-cover map"), ("continuous", "vegetation index")):
        if kind not in kinds:
            availability(f"landscape_{kind}", "not_available", code=f"no_{kind}_layer_declared",
                         reason=f"no {what} layer is declared; its summaries are not available "
                         "and nothing is substituted")
    if station_land_cover:
        lab_rows.append({**base, "date_label": "all", "day_set": "none",
                         "layer_id": "station_metadata", "matching_policy": "none",
                         "domain": "none", "label_source": "station_metadata", "radius_m": 0.0,
                         "label": station_land_cover, "label_meaning": IGBP_MEANING,
                         "source_version": "station declaration (stations.<id>.land_cover)",
                         "flags": "label_not_accuracy_guarantee;site_label_not_fraction"})

    transfers: dict[tuple[str, str], Transfer] = {}

    def transfer(src: Grid, dst: Grid) -> Transfer:
        k = (src.grid_id, dst.grid_id)
        if k not in transfers:
            transfers[k] = transfer_matrix(src, dst)
        return transfers[k]

    fixed_cache: dict[tuple[str, str, int], np.ndarray] = {}

    def fixed_on(s: FixedSupport, asset: LayerAsset) -> np.ndarray:
        k = (s.method_id, asset.layer_id, asset.asset_index)
        if k not in fixed_cache:
            if s.geometry is not None:
                fixed_cache[k] = polygon_cell_fractions(s.geometry, s.crs, asset.grid)  # type: ignore[arg-type]
            else:
                fixed_cache[k] = transfer(s.grid, asset.grid).apply(s.weights)  # type: ignore[arg-type]
        return fixed_cache[k]

    for name, layer in spec.layers.items():
        assets = list(layers.get(name, ()))
        for a in assets:
            if a.layer_id != name:
                raise ContractError(f"layer asset {a.label} belongs to {a.layer_id}, not {name}")
        if not assets:
            if not any(u.get("layer") == name for u in unavailable):
                availability("landscape_layer", "not_available", layer=name,
                             code="layer_not_supplied", reason="no asset of this layer was read")
            continue
        periods = [(a.reference_start, a.reference_end) for a in assets]
        legend = _legend_of(layer)
        for set_id in spec.footprint_sets:
            days = sorted(footprint_days.get(set_id, ()), key=lambda f: f.date)
            if spec.start:
                days = [f for f in days if f.date >= pd.Timestamp(spec.start)]
            if spec.end:
                days = [f for f in days if f.date <= pd.Timestamp(spec.end)]
            for policy in spec.matching_policies:
                pid = policy.policy_id
                n_ok = 0
                for fday in days:
                    m = match_asset(fday.date, periods, policy)
                    dlabel = fday.date.date().isoformat()
                    if m is None:
                        availability("landscape_" + layer.kind, "not_available", layer=name,
                                     day_set=set_id, policy=pid, date_label=dlabel,
                                     code="no_admissible_asset",
                                     reason=f"no asset of {name} is admissible under {pid}; "
                                     "the day is not filled")
                        continue
                    asset = assets[m.asset_index]
                    match = _match_columns(fday.date, asset, m)
                    retro = "retrospective_layer_match" if m.retrospective else None
                    fp = fday.footprint
                    assert fp.contribution is not None
                    domains: list[tuple[str, np.ndarray, float | None]] = [
                        (FULL_DOMAIN, fp.contribution, None)]
                    for lv in spec.contour_levels:
                        mask = contour_mask(fp.contribution, lv)
                        w = np.where(mask, fp.contribution, 0.0)
                        domains.append((contour_domain(lv), w, lv))
                    t = transfer(fp.grid, asset.grid)
                    fp_values: dict[str, dict[str, float]] = {}
                    for domain, w, level in domains:
                        contrib = t.apply(w)
                        wd = float(w.sum())
                        got = _describe(layer, legend, contrib, asset, fp.weight_total, wd,
                                        fp.denominator)
                        key = {**base, "date": fday.date, "day_set": set_id,
                               "extraction_method": FOOTPRINT_METHOD, "footprint_set_id": set_id,
                               "layer_id": name, "matching_policy": pid, "domain": domain}
                        extra = {**match, "contour_level": level,
                                 "flags": _flags(retro, "contour_subset" if level else None)}
                        if got is None:
                            availability("landscape_" + layer.kind, "not_available", layer=name,
                                         method=FOOTPRINT_METHOD, day_set=set_id, policy=pid,
                                         domain=domain, date_label=dlabel,
                                         code="no_valid_layer_cells",
                                         reason="the support has no weight on valid layer cells")
                            continue
                        rows, values = got
                        (cls_rows if layer.kind == "categorical" else idx_rows).extend(
                            {**key, **r, **extra} for r in rows)
                        fp_values[domain] = values
                        if label_source is not None and layer.kind == "categorical" and \
                                name in label_source.parameters.get("layers", ()):
                            lab_rows.extend(_labels(label_source, contrib, asset, layer,
                                                    {**base, "date_label": dlabel,
                                                     "day_set": set_id, "layer_id": name,
                                                     "matching_policy": pid, "domain": domain},
                                                    availability))
                    if FULL_DOMAIN not in fp_values:
                        continue
                    n_ok += 1
                    for s in fixed_supports:
                        got = _describe(layer, legend, fixed_on(s, asset), asset, 1.0, None,
                                        s.weight_denominator)
                        if got is None:
                            availability("method_difference", "not_available", layer=name,
                                         method=s.method_id, day_set=set_id, policy=pid,
                                         date_label=dlabel, code="no_valid_layer_cells",
                                         reason="the fixed support has no weight on valid cells")
                            continue
                        rows, values = got
                        key = {**base, "date": fday.date, "day_set": set_id,
                               "extraction_method": s.method_id, "footprint_set_id": "none",
                               "layer_id": name, "matching_policy": pid, "domain": FULL_SUPPORT}
                        extra = {**match, "contour_level": None,
                                 "flags": _flags(retro, "fixed_support",
                                                 "support_partly_outside_grid" if s.note else None)}
                        (cls_rows if layer.kind == "categorical" else idx_rows).extend(
                            {**key, **r, **extra} for r in rows)
                        for domain, fv in fp_values.items():
                            diff_rows.extend(_differences(
                                fv, values, layer, {**base, "date": fday.date,
                                                    "footprint_set_id": set_id, "layer_id": name,
                                                    "matching_policy": pid, "domain": domain,
                                                    "compared_method": s.method_id,
                                                    "layer_asset": asset.label,
                                                    "retrospective": m.retrospective,
                                                    "flags": _flags(retro)}))
                availability("landscape_" + layer.kind, "available", layer=name,
                             method=FOOTPRINT_METHOD, day_set=set_id, policy=pid, n_days=n_ok,
                             reason=f"{n_ok} of {len(days)} usable footprint days described; "
                             + NOT_ACCURACY,
                             flags=None if policy.policy == "previous_only"
                             else "policy_may_use_later_information")
    for set_id in spec.footprint_sets:
        for d, why in sorted((footprint_unusable or {}).get(set_id, {}).items()):
            availability("footprint", "not_available", day_set=set_id,
                         date_label=pd.Timestamp(d).date().isoformat(), code=why,
                         reason="footprint not usable that day; no landscape row is produced")

    attrs = {"workflow": "etcompare.representativeness.assess_landscape",
             "representativeness": to_plain(spec), "std_estimator": STD_ESTIMATOR,
             "interpretation": NOT_ACCURACY,
             "transfers": {f"{a}->{b}": t.method for (a, b), t in transfers.items()}}
    tables = {s.name: CanonicalTable.build(_frame(rows, s), s, attrs, source=rid)
              for s, rows in ((LANDSCAPE_CLASS_FRACTIONS, cls_rows),
                              (LANDSCAPE_INDEX_SUMMARIES, idx_rows),
                              (LANDSCAPE_METHOD_DIFFERENCES, diff_rows),
                              (REPRESENTATIVENESS_LABELS, lab_rows),
                              (REPRESENTATIVENESS_AVAILABILITY, avail))}
    a = tables["representativeness_availability"].frame
    for (analysis, code), n in a[a["status"] == "not_available"].groupby(
            ["analysis", "reason_code"]).size().items():
        report.info("not_available", f"{analysis}: {code} ({n} row(s))", n_rows=int(n))
    return RepresentativenessResult(spec, tables, tuple(report.issues), attrs)


def _legend_of(layer: LandscapeLayerSpec) -> Mapping[int, str]:
    if layer.kind != "categorical":
        return {}
    if layer.legend is not None:
        return layer.legend
    return read_legend_csv(layer.legend_csv)  # type: ignore[arg-type]


def read_legend_csv(path: str | os.PathLike[str]) -> dict[int, str]:
    """A legend file with columns ``code`` and ``name`` (one row per class)."""
    frame = pd.read_csv(path)
    if not {"code", "name"} <= set(frame.columns):
        raise ContractError(f"legend {path}: needs columns code and name")
    if frame["code"].duplicated().any() or frame["name"].isna().any():
        raise ContractError(f"legend {path}: duplicate codes or empty names")
    return {int(c): str(n) for c, n in zip(frame["code"], frame["name"], strict=True)}


def _match_columns(day: pd.Timestamp, asset: LayerAsset, m: LayerMatch) -> dict[str, Any]:
    return {"layer_asset": asset.label, "layer_vintage": asset.vintage,
            "layer_reference_start": asset.reference_start,
            "layer_reference_end": asset.reference_end, "days_from_layer": m.days_from_layer,
            "temporal_relation": m.temporal_relation, "footprint_year": int(day.year),
            "layer_year": int(asset.reference_end.year),
            "year_mismatch": int(day.year) - int(asset.reference_end.year),
            "retrospective": m.retrospective}


def _describe(layer: LandscapeLayerSpec, legend: Mapping[int, str], contrib: np.ndarray,
              asset: LayerAsset, total: float, domain: float | None, denominator: str
              ) -> tuple[list[dict[str, Any]], dict[str, float]] | None:
    """Rows (without key and match columns) and the quantities used for differences."""
    if layer.kind == "categorical":
        comp = class_composition(contrib, asset.values, nodata=layer.nodata, legend=legend,
                                 weight_grid_total=total, weight_domain=domain,
                                 denominator=denominator)
        if comp.weights.weight_valid <= 0:
            return None
        cols = comp.weights.columns()
        frac = comp.frac_of_valid()
        t = comp.weights.weight_grid_total
        rows = [{"class_code": str(k), "class_role": "class", "class_name": legend[k],
                 "class_weight": w, "class_frac_of_valid": frac[k],
                 "class_frac_of_grid_total": w / t, **cols}
                for k, w in sorted(comp.class_weights.items())]
        rows.append({"class_code": "nodata", "class_role": "nodata", "class_name": "nodata",
                     "class_weight": comp.weights.weight_nodata, "class_frac_of_valid": None,
                     "class_frac_of_grid_total": comp.weights.weight_nodata / t, **cols})
        values = {f"class_frac_of_valid:{k}": v for k, v in frac.items()}
        values["nodata_frac_of_grid_total"] = cols["nodata_frac_of_grid_total"]
        return rows, values
    s = index_summary(contrib, asset.values, weight_grid_total=total, weight_domain=domain,
                      denominator=denominator)
    if s is None:
        return None
    row = {"index_units": layer.units, "weighted_mean": s.weighted_mean,
           "weighted_std": s.weighted_std, "std_estimator": STD_ESTIMATOR, "n_eff": s.n_eff,
           **s.weights.columns()}
    return [row], {"index_mean": s.weighted_mean, "index_std": s.weighted_std,
                   "nodata_frac_of_grid_total": row["nodata_frac_of_grid_total"]}


def _differences(fp: Mapping[str, float], other: Mapping[str, float], layer: LandscapeLayerSpec,
                 key: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for q in sorted(set(fp) | set(other)):
        if q.startswith("class_frac_of_valid:"):
            kind, units = "class_fraction", "1"
            a, b = fp.get(q, 0.0), other.get(q, 0.0)  # a class absent on one side has share 0
        else:
            if q not in fp or q not in other:
                continue
            kind = {"index_mean": "index_mean", "index_std": "index_std"}.get(
                q, "nodata_fraction")
            units = layer.units if kind.startswith("index") else "1"  # type: ignore[assignment]
            a, b = fp[q], other[q]
        rows.append({**key, "quantity": q, "quantity_kind": kind, "footprint_value": a,
                     "method_value": b, "difference": a - b, "units": units})
    return rows


def _labels(src: LabelSource, contrib: np.ndarray, asset: LayerAsset, layer: LandscapeLayerSpec,
            key: Mapping[str, Any], availability: Callable[..., None]) -> list[dict[str, Any]]:
    try:
        got = src.labeler(contrib, asset.values, asset, layer, src.tower_xy[layer.layer_id])
    except (ValueError, GridError) as exc:
        availability("fluxfootprints_labels", "not_available", layer=layer.layer_id,
                     day_set=key["day_set"], policy=key["matching_policy"], domain=key["domain"],
                     date_label=key["date_label"], code="label_source_refused", reason=str(exc))
        return []
    params = json.dumps(to_plain(dict(src.parameters)), sort_keys=True)
    return [{**key, "label_source": "fluxfootprints", "radius_m": float(r["radius_m"]),
             "label": str(r["label"]),
             "label_meaning": "fluxfootprints Chu et al. (2021) categorical representativeness of "
             "the footprint relative to a target disc; a label, not an ET accuracy guarantee",
             "dominant_class": None if r.get("dominant_class") is None
             else str(r["dominant_class"]),
             "p_footprint": r.get("p_footprint"), "p_target": r.get("p_target"),
             "chi2": r.get("chi2"), "p_value": r.get("p_value"), "source_version": src.version,
             "source_parameters": params, "layer_asset": asset.label,
             "flags": "label_not_accuracy_guarantee"} for r in got]


# --------------------------------------------------------------------------- raster input

def read_layer_asset(layer: LandscapeLayerSpec, asset_index: int, *, x: float, y: float,
                     xy_crs: str, half_width_m: float) -> LayerAsset:
    """Read one asset on a window of its native grid around ``(x, y)`` (needs ``geo``).

    The header CRS must equal the declared CRS, and a header nodata must equal the
    declared nodata (D-026); otherwise :class:`ContractError`. ``half_width_m`` is the
    half-size of the window in metres of a projected layer CRS.
    """
    rasterio = require("rasterio", "Reading landscape layers")
    windows = require("rasterio.windows", "Reading landscape layers")
    a = layer.assets[asset_index]
    with rasterio.open(a.path) as ds:
        if ds.crs is None:
            raise GridError(f"layer raster {a.path} has no CRS")
        header = ds.crs.to_wkt()
        if not same_crs(layer.crs, header):
            raise ContractError(f"{a.path}: header CRS differs from the declared CRS {layer.crs}",
                                config_key=f"representativeness.layers.{layer.layer_id}.crs")
        hdr = ds.nodata
        if hdr is not None and not (math.isnan(hdr) and math.isnan(layer.nodata)) \
                and hdr != layer.nodata:
            raise ContractError(f"{a.path}: header nodata {hdr} differs from declared nodata "
                                f"{layer.nodata}",
                                config_key=f"representativeness.layers.{layer.layer_id}.nodata")
        grid = Grid.from_affine(ds.crs, ds.transform, ds.width, ds.height)
        tx, ty = transform_coords(np.array([x]), np.array([y]), xy_crs, grid.crs)  # type: ignore[arg-type]
        row, col = (float(v) for v in grid.rowcol(float(tx[0]), float(ty[0])))
        step = math.sqrt(grid.cell_area)
        h = int(math.ceil(half_width_m / step)) + 2
        r0, r1 = max(int(row) - h, 0), min(int(row) + h + 1, grid.height)
        c0, c1 = max(int(col) - h, 0), min(int(col) + h + 1, grid.width)
        if r1 <= r0 or c1 <= c0:
            raise GridError(f"{a.path}: the station lies outside the layer raster")
        raw = ds.read(1, window=windows.Window(c0, r0, c1 - c0, r1 - r0)).astype("float64")
    sub = grid.window(r0, c0, r1 - r0, c1 - c0)
    if layer.kind == "continuous":
        missing = ~np.isfinite(raw)
        if not math.isnan(layer.nodata):
            missing |= raw == layer.nodata
        raw = raw * float(layer.scale_factor) + float(layer.add_offset)  # type: ignore[arg-type]
        raw[missing] = np.nan
    start, end = a.period
    return LayerAsset(layer.layer_id, asset_index, os.path.basename(str(a.path)), sub, raw,
                      pd.Timestamp(start), pd.Timestamp(end), a.vintage)
