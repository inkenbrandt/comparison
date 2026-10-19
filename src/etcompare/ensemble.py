"""Held-out ensemble experiments (guide step 16).

:func:`run_ensemble` compares, on the same frozen cohort days,

1. the downloaded **official** ensemble (a comparison product; it is never a member),
2. the unweighted **mean** and **median** of the declared members, and
3. optionally a **fitted weighted** ensemble, whose every prediction is held out: it comes
   from weights fitted on other folds only.

Every ensemble here is built **after spatial extraction**, from the members' extracted
values of one declared side. It is not a pixel-level ensemble, which would combine model
rasters before extraction (rows carry ``post_extraction_ensemble`` and
``not_pixel_level_ensemble``).

Members and eligibility
-----------------------
The members are declared in an :class:`~etcompare.config.EnsembleSpec`; a product with
``is_ensemble`` is refused as a member. Availability and reference values come from
:func:`etcompare.cohorts.build_cohort` (a ``model_available`` cohort of the members and the
official product), so joins, keys and exclusions follow step 7. The missing-member policy is
fixed in advance:

- ``require_all``: a day is eligible only when every member has a value; other days are
  counted and reported (``member_missing``).
- ``reweight_available``: a day is eligible with at least ``min_members`` members; the mean
  and median use the available members, and fitted weights are renormalised over them.
  Every such row is flagged ``reweighted`` (the fitted ensemble changes there). Training rows
  are always complete days.

Member spread (range and population SD of the available members) is **disagreement**
between models, never a confidence interval.

Fitting (designs)
-----------------
- **Folds.** ``leave_one_year_out`` groups days by ``fold_unit`` (calendar year or water
  year), across all stations at once, so a held-out year is never seen through another
  station's record. ``leave_one_site_out`` groups days by station. Random daily splits are
  never used.
- **Training rows** are complete-member days of the other folds with a reference. Gap-filled
  reference days are excluded unless ``gap_filled_training`` (D-005). For temporal folds,
  training days within ``buffer_days`` of the held-out fold's calendar period are removed.
  Training folds with fewer than ``min_training_days_per_fold`` days are left out. A fold is
  fitted only with at least ``min_training_folds`` training folds; otherwise it is
  ``declined`` with the reason.
- **Weights** are simplex least squares (``w >= 0``, ``sum(w) = 1``), solved exactly by
  enumerating member supports (:func:`simplex_least_squares`). Preprocessing is ``none``:
  nothing is scaled or centred, so nothing can be learned from held-out rows.
- **Regularisation** shrinks toward equal weights: ``w = (1 - alpha) w_fit + alpha / K``;
  ``alpha = 1`` is exactly the simple mean. ``alpha`` is chosen from the declared grid by
  inner grouped leave-one-fold-out validation over the training folds only (pooled RMSE via
  :func:`etcompare.metrics.compute_metrics`; ties go to the larger alpha). The buffer applies
  around inner held-out folds too.
- **Held-out rows never influence** their fold's weights, alpha or membership (tested by
  perturbing a held-out fold).
- **Too little independent data** gives ``declined`` (no weights), or with
  ``insufficient_data: exploratory`` an in-sample fit labelled ``exploratory_in_sample`` that
  never yields predictions or comparisons. A single station gives ``not_available`` for
  leave-one-site-out.

Comparison
----------
Each scope (``baseline``: official, mean, median; each fitted design: the same plus
``fitted_weighted`` on its held-out days), and each declared month subset of it (for
example April-October; folds and training are unchanged), becomes a frozen
``common_date`` cohort built by
``build_cohort`` from derived extraction rows. It is scored by
:func:`~etcompare.metrics.score_comparison`, resampled by
:func:`~etcompare.bootstrap.bootstrap_comparison` and contrasted by
:func:`~etcompare.ranking.rank_comparison` (no metric is re-implemented). A held-out
improvement is ``supported`` only when the contrast interval excludes zero in the fitted
ensemble's favour; anything else is ``not_supported`` or ``not_assessed``. Results are
station-level; with one station they are not network evidence. This module takes no event,
residual or anomaly input, so none can select members, weights or folds.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from types import MappingProxyType
from typing import Any

import numpy as np
import pandas as pd

from etcompare.bootstrap import BootstrapResult, bootstrap_comparison, season_year
from etcompare.cohorts import Cohort, Tables, build_cohort
from etcompare.config import (
    RANKING_DIRECTIONS,
    BootstrapSpec,
    ComparisonSpec,
    EnsembleSpec,
    FootprintSetSpec,
    MetricSpec,
    ProductSpec,
    RankingSpec,
    to_plain,
)
from etcompare.errors import ContractError
from etcompare.metrics import MetricResults, compute_metrics, score_comparison
from etcompare.ranking import RankingResult, rank_comparison
from etcompare.schemas import (
    ENSEMBLE_DAYS,
    ENSEMBLE_FOLDS,
    ENSEMBLE_IMPROVEMENT,
    ENSEMBLE_INNER_SCORES,
    ENSEMBLE_PREDICTIONS,
    ENSEMBLE_SUMMARY,
    ENSEMBLE_TRAINING_MEMBERSHIP,
    ENSEMBLE_WEIGHTS,
    EXTRACTIONS,
    CanonicalTable,
    TableSchema,
)
from etcompare.validation import Issue, ValidationReport

MEAN, MEDIAN, FITTED = "member_mean", "member_median", "fitted_weighted"
LEVEL = "post_extraction"
BASE_FLAGS = ("post_extraction_ensemble", "not_pixel_level_ensemble")
_COMBINATION_PRODUCT = {"mean": MEAN, "median": MEDIAN}
_TIE = 1e-12


# --------------------------------------------------------------------------- weights

def simplex_least_squares(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Weights ``w >= 0`` with ``sum(w) = 1`` minimising ``||y - x @ w||^2``, exactly.

    The problem is a convex quadratic programme on the simplex. Its minimiser restricted to
    its own support solves the equality-constrained problem on that support (KKT), so every
    support is solved and the feasible solution with the smallest error is kept. Ties go to
    the solution closest to equal weights, which makes the result deterministic.
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    n, k = x.shape
    if n == 0:
        raise ValueError("no training rows")
    equal = np.full(k, 1.0 / k)
    best: tuple[float, float, np.ndarray] | None = None
    for r in range(1, k + 1):
        for support in combinations(range(k), r):
            xs = x[:, support]
            a = np.zeros((r + 1, r + 1))
            a[:r, :r] = xs.T @ xs
            a[:r, r] = 1.0
            a[r, :r] = 1.0
            b = np.concatenate([xs.T @ y, [1.0]])
            w_s = np.linalg.lstsq(a, b, rcond=None)[0][:r]
            if (w_s < -1e-12).any() or not np.isfinite(w_s).all():
                continue
            w = np.zeros(k)
            w[list(support)] = np.clip(w_s, 0.0, None)
            if w.sum() <= 0:
                continue
            w /= w.sum()
            sse = float(np.sum((y - x @ w) ** 2))
            dist = float(np.sum((w - equal) ** 2))
            if best is None or sse < best[0] - _TIE * max(1.0, best[0]) or (
                    abs(sse - best[0]) <= _TIE * max(1.0, best[0]) and dist < best[1]):
                best = (sse, dist, w)
    assert best is not None  # every single-member support is feasible
    return best[2]


def shrink(weights: np.ndarray, alpha: float) -> np.ndarray:
    """``(1 - alpha) * weights + alpha / K``; ``alpha = 1`` gives exactly equal weights."""
    k = len(weights)
    if alpha == 1.0:
        return np.full(k, 1.0 / k)
    return (1.0 - alpha) * np.asarray(weights, float) + alpha / k


def combine(values: np.ndarray, available: np.ndarray, weights: np.ndarray
            ) -> tuple[np.ndarray, np.ndarray]:
    """Weighted combination per row, renormalising ``weights`` over the available members.

    Returns the predictions (NaN where the available members carry no weight) and the
    effective weights actually applied (rows sum to one). Equal weights take exactly the
    arithmetic of the unweighted mean, so the equal-weight limit is the simple mean bit for
    bit and never differs from it by round-off.
    """
    w = np.where(available, weights[None, :], 0.0)
    s = w.sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        eff = w / s[:, None]
    masked = np.where(available, values, np.nan)
    if np.all(weights == weights[0]):
        pred = np.full(len(values), np.nan)
        rows = available.any(axis=1)
        pred[rows] = np.nanmean(masked[rows], axis=1)
    else:
        pred = np.where(s > 0, np.nansum(np.where(available, values, 0.0) * eff, axis=1),
                        np.nan)
    return pred, np.where(s[:, None] > 0, eff, np.nan)


# --------------------------------------------------------------------------- results

@dataclass(frozen=True)
class EnsembleComparison:
    """One frozen product cohort and its step-9 to step-11 results.

    ``subset`` is ``all`` (every compared day of the scope) or a declared month subset.
    """

    scope: str
    cohort: Cohort
    metrics: MetricResults
    bootstrap: BootstrapResult | None
    ranking: RankingResult
    subset: str = "all"


def comparison_key(scope: str, subset: str) -> str:
    """Key of :attr:`EnsembleResult.comparisons`: ``<scope>`` or ``<scope>__<subset>``."""
    return scope if subset == "all" else f"{scope}__{subset}"


@dataclass(frozen=True)
class EnsembleResult:
    """Tables of one ensemble experiment. Immutable; accessors return copies."""

    spec: EnsembleSpec
    _tables: Mapping[str, CanonicalTable] = field(repr=False)
    comparisons: Mapping[str, EnsembleComparison] = field(repr=False)
    _issues: tuple[Issue, ...] = field(repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_tables", MappingProxyType(dict(self._tables)))
        object.__setattr__(self, "comparisons", MappingProxyType(dict(self.comparisons)))

    def _get(self, name: str) -> CanonicalTable:
        t = self._tables[name]
        return CanonicalTable(t.schema, t.frame.copy(), dict(t.attributes))

    def tables(self) -> dict[str, CanonicalTable]:
        return {name: self._get(name) for name in self._tables}

    @property
    def days(self) -> CanonicalTable:
        return self._get("ensemble_days")

    @property
    def predictions(self) -> CanonicalTable:
        return self._get("ensemble_predictions")

    @property
    def folds(self) -> CanonicalTable:
        return self._get("ensemble_folds")

    @property
    def weights(self) -> CanonicalTable:
        return self._get("ensemble_weights")

    @property
    def inner_scores(self) -> CanonicalTable:
        return self._get("ensemble_inner_scores")

    @property
    def training_membership(self) -> CanonicalTable:
        return self._get("ensemble_training_membership")

    @property
    def summary(self) -> CanonicalTable:
        return self._get("ensemble_summary")

    @property
    def improvement(self) -> CanonicalTable:
        return self._get("ensemble_improvement")

    @property
    def report(self) -> ValidationReport:
        return ValidationReport(f"ensemble {self.spec.ensemble_id}", list(self._issues))


# --------------------------------------------------------------------------- helpers

def _frame(rows: list[dict[str, Any]], schema: TableSchema) -> pd.DataFrame:
    frame = pd.DataFrame(rows, columns=schema.names) if rows else schema.empty()
    for c in schema.columns:
        if c.kind in ("float", "int"):
            frame[c.name] = pd.to_numeric(frame[c.name])
    return frame.sort_values(list(schema.primary_key), ignore_index=True)


def _join(values: Sequence[str]) -> str | None:
    return ";".join(values) or None


def _fmt(x: float) -> str:
    return f"{x:.6g}"


def fold_groups(frame: pd.DataFrame, design: str, spec: EnsembleSpec) -> np.ndarray:
    """Fold label of every row: ``2025`` / ``WY2025`` (temporal) or the station."""
    if design == "leave_one_site_out":
        return frame["site_id"].astype(str).to_numpy(dtype=object)
    unit = spec.fold_unit
    assert unit is not None
    years = season_year(pd.DatetimeIndex(frame["date"]), unit, spec.water_year_start_month)
    prefix = "WY" if unit == "water_year" else ""
    return np.array([f"{prefix}{y}" for y in years], dtype=object)


def fold_period(label: str, spec: EnsembleSpec) -> tuple[pd.Timestamp, pd.Timestamp]:
    """First and last calendar day of a temporal fold."""
    if label.startswith("WY"):
        y, m = int(label[2:]), spec.water_year_start_month
        start = pd.Timestamp(y - 1, m, 1) if m > 1 else pd.Timestamp(y, 1, 1)
    else:
        start = pd.Timestamp(int(label), 1, 1)
    return start, start + pd.DateOffset(years=1) - pd.Timedelta(days=1)


def _buffered(dates: pd.Series, label: str, spec: EnsembleSpec) -> np.ndarray:
    start, end = fold_period(label, spec)
    b = pd.Timedelta(days=int(spec.buffer_days or 0))
    return ((dates >= start - b) & (dates <= end + b)).to_numpy()


def _check_inputs(spec: EnsembleSpec, products: Mapping[str, ProductSpec],
                  metric_spec: MetricSpec, bootstrap_spec: BootstrapSpec | None,
                  footprint_sets: Mapping[str, FootprintSetSpec] | None) -> None:
    key = f"ensembles.{spec.ensemble_id}"
    for m in spec.members:
        p = products.get(m)
        if p is None:
            raise ContractError(f"{key}: member {m!r} has no product declaration",
                                config_key="products")
        if p.is_ensemble:
            raise ContractError(f"{key}: {m!r} is an ensemble product; the official ensemble "
                                "is a comparison product, never an independent member",
                                config_key=f"{key}.members")
    if spec.official is not None:
        p = products.get(spec.official)
        if p is None or not p.is_ensemble:
            raise ContractError(f"{key}: official {spec.official!r} must be a product with "
                                "is_ensemble", config_key=f"{key}.official")
    if metric_spec.metric_set_id != spec.metric_set:
        raise ContractError(f"{key} uses metric set {spec.metric_set!r}, not "
                            f"{metric_spec.metric_set_id!r}")
    missing = set(spec.contrast_metrics) - set(metric_spec.metrics)
    if missing:
        raise ContractError(f"{key}: contrast metrics {sorted(missing)} are not in metric set "
                            f"{metric_spec.metric_set_id}")
    if bootstrap_spec is not None and bootstrap_spec.bootstrap_id != spec.bootstrap:
        raise ContractError(f"{key} uses bootstrap {spec.bootstrap!r}, not "
                            f"{bootstrap_spec.bootstrap_id!r}")
    fp = spec.side.footprint_set_id
    mapped = (list(spec.site_footprint_sets.values()) if spec.site_footprint_sets is not None
              else [fp] if fp != "none" else [])
    for set_id in mapped:
        f = (footprint_sets or {}).get(set_id)
        if f is None or f.target_dependence != "no":
            raise ContractError(f"{key}: footprint set {set_id} is weighted by the target "
                                "measurements or its weighting is not supplied; it never "
                                "enters an ensemble experiment", config_key=f"{key}.side")


def _per_site_side(spec: EnsembleSpec, extractions: Tables,
                   footprint_sets: Mapping[str, FootprintSetSpec]
                   ) -> tuple[list[CanonicalTable], dict[str, FootprintSetSpec]]:
    """Relabel each station's declared footprint set as the side label (provenance kept).

    Only rows of ``side.extraction_method`` and the station's mapped set are kept; their
    ``footprint_set_id`` becomes the label, and the original set ID is appended to
    ``support_id``. The label gets a footprint-set declaration combining the mapped sets.
    """
    assert spec.site_footprint_sets is not None
    parts = extractions if isinstance(extractions, Sequence) else [extractions]
    frames = [t.frame if isinstance(t, CanonicalTable) else t for t in parts]
    ext = pd.concat(frames, ignore_index=True) if frames else EXTRACTIONS.empty()
    label = spec.side.footprint_set_id
    mapping = spec.site_footprint_sets
    keep = (ext["extraction_method"] == spec.side.extraction_method) & (
        ext["footprint_set_id"] == ext["site_id"].map(mapping))
    sel = ext[keep].copy()
    support = sel["support_id"].astype(object).where(sel["support_id"].notna(), None)
    sel["support_id"] = [f"{s or ''};footprint_set={f}".lstrip(";") for s, f in
                         zip(support, sel["footprint_set_id"], strict=True)]
    sel["footprint_set_id"] = label
    used = [footprint_sets[v] for v in dict.fromkeys(mapping.values())]
    weighting = {f.temporal_weighting for f in used}
    semantics = {f.value_semantics for f in used}
    combined = FootprintSetSpec(
        label, Path("."), weighting_method="per_site:" + ";".join(
            f"{site}={mapping[site]}" for site in sorted(mapping)),
        value_semantics=semantics.pop() if len(semantics) == 1 else "normalized_cell_weight",
        temporal_weighting=weighting.pop() if len(weighting) == 1 else "unknown")
    table = CanonicalTable.build(sel.reset_index(drop=True), EXTRACTIONS,
                                 {"site_footprint_sets": dict(mapping)},
                                 source=f"{spec.ensemble_id} per-site side")
    return [table], {**footprint_sets, label: combined}


def _derived_product(name: str, members: Sequence[ProductSpec]) -> ProductSpec:
    support = {p.temporal_support for p in members}
    day = {p.day_definition for p in members}
    return ProductSpec(name, "mm/day", day.pop() if len(day) == 1 else "unknown",
                       support.pop() if len(support) == 1 else "unknown",
                       version=f"etcompare_{LEVEL}", is_ensemble=True)


# --------------------------------------------------------------------------- folds

@dataclass
class _Fold:
    label: str
    status: str
    detail: str | None
    roles: np.ndarray
    training_groups: list[str]
    alpha: float | None = None
    w_fit: np.ndarray | None = None
    weights: np.ndarray | None = None
    inner: list[tuple[float, float | None, int]] = field(default_factory=list)
    n_inner: int = 0
    n_buffer: int = 0
    n_small: int = 0


def _roles(groups: np.ndarray, label: str | None, eligible: np.ndarray, complete: np.ndarray,
           filled_ok: np.ndarray, buffered: np.ndarray) -> np.ndarray:
    """Role of every day for held-out fold ``label`` (before the training-fold size rule)."""
    test = groups == label if label is not None else np.zeros(len(groups), bool)
    roles = np.full(len(groups), "training", dtype=object)
    roles[~complete] = "members_incomplete"
    roles[complete & ~filled_ok] = "gap_filled_reference"
    roles[complete & filled_ok & buffered & ~test] = "buffer_excluded"
    roles[~eligible] = "not_eligible"
    roles[test & eligible] = "held_out"
    return roles


def _qualifying(groups: np.ndarray, roles: np.ndarray, min_days: int) -> tuple[list[str], int]:
    """Training groups with at least ``min_days`` training rows; marks the others."""
    train = roles == "training"
    keep, small = [], 0
    for g in sorted(set(groups[train].tolist())):
        n = int((train & (groups == g)).sum())
        if n >= min_days:
            keep.append(g)
        else:
            roles[train & (groups == g)] = "training_fold_too_small"
            small += n
    return keep, small


def _fit_fold(label: str, design: str, spec: EnsembleSpec, metric_spec: MetricSpec,
              frame: pd.DataFrame, x: np.ndarray, y: np.ndarray, groups: np.ndarray,
              eligible: np.ndarray, complete: np.ndarray, filled_ok: np.ndarray) -> _Fold:
    temporal = design == "leave_one_year_out"
    dates = frame["date"]
    min_days = int(spec.min_training_days_per_fold or 1)
    min_folds = int(spec.min_training_folds or 2)
    buffered = _buffered(dates, label, spec) if temporal else np.zeros(len(frame), bool)
    roles = _roles(groups, label, eligible, complete, filled_ok, buffered)
    n_buffer = int((roles == "buffer_excluded").sum())
    keep, n_small = _qualifying(groups, roles, min_days)
    fold = _Fold(label, "declined", None, roles, keep, n_buffer=n_buffer, n_small=n_small)
    if len(keep) < min_folds:
        fold.detail = (f"{len(keep)} training fold(s) with >= {min_days} training days after "
                       f"the {spec.buffer_days}-day buffer ({_join(keep) or 'none'}); "
                       f"min_training_folds is {min_folds}")
        return fold
    train = roles == "training"
    # ---- inner grouped leave-one-fold-out validation on the training folds only
    grid = list(spec.shrinkage_grid)
    preds: dict[float, list[np.ndarray]] = {a: [] for a in grid}
    obs: list[np.ndarray] = []
    for h in keep:
        inner_test = train & (groups == h)
        inner_buf = _buffered(dates, h, spec) if temporal else np.zeros(len(frame), bool)
        inner_train = train & (groups != h) & ~inner_buf
        enough = [g for g in keep if g != h
                  and int((inner_train & (groups == g)).sum()) >= min_days]
        if len(enough) < min_folds - 1:
            fold.detail = (f"inner fold {h}: {len(enough)} inner training fold(s) with >= "
                           f"{min_days} days after the buffer; inner validation needs "
                           f"{min_folds - 1}")
            return fold
        w_inner = simplex_least_squares(x[inner_train], y[inner_train])
        obs.append(y[inner_test])
        for a in grid:
            preds[a].append(x[inner_test] @ shrink(w_inner, a))
    o = np.concatenate(obs)
    scores: list[tuple[float, float | None, int]] = []
    for a in grid:
        r = compute_metrics(o, np.concatenate(preds[a]), tolerance=metric_spec.daily_tolerance,
                            metrics=("rmse",))["rmse"]
        scores.append((a, r.value, len(o)))
    fold.inner, fold.n_inner = scores, len(keep)
    defined = [(a, v) for a, v, _ in scores if v is not None]
    if not defined:
        fold.detail = "inner validation RMSE undefined for every alpha"
        return fold
    best = min(v for _, v in defined)
    fold.alpha = max(a for a, v in defined if abs(v - best) <= _TIE * max(1.0, abs(best)))
    fold.w_fit = simplex_least_squares(x[train], y[train])
    fold.weights = shrink(fold.w_fit, fold.alpha)
    fold.status = "fitted"
    return fold


# --------------------------------------------------------------------------- main

def run_ensemble(spec: EnsembleSpec, observations: Tables, extractions: Tables, *,
                 products: Mapping[str, ProductSpec], metric_spec: MetricSpec,
                 bootstrap_spec: BootstrapSpec | None = None,
                 footprint_sets: Mapping[str, FootprintSetSpec] | Sequence[FootprintSetSpec]
                 | None = None) -> EnsembleResult:
    """Run the ensemble experiment declared by ``spec`` (module docstring).

    ``observations`` are ``station_daily`` tables and ``extractions`` ``extractions`` tables
    (the members' and the official product's rows of the declared side). ``products``
    supplies every member's declaration (``is_ensemble`` members are refused).
    """
    if footprint_sets is not None and not isinstance(footprint_sets, Mapping):
        footprint_sets = {f.set_id: f for f in footprint_sets}
    _check_inputs(spec, products, metric_spec, bootstrap_spec, footprint_sets)
    if spec.site_footprint_sets is not None:
        assert footprint_sets is not None
        extractions, footprint_sets = _per_site_side(spec, extractions, footprint_sets)
    eid = spec.ensemble_id
    report = ValidationReport(f"ensemble {eid}")
    members = list(spec.members)
    k = len(members)
    v = spec.reference_variant
    side = spec.side
    models = [*members, *([spec.official] if spec.official else [])]

    # ---- availability and reference values through a step-7 coverage cohort
    avail_spec = ComparisonSpec(f"{eid}__availability", "model_available", tuple(models),
                                (v,), (side,), sites=spec.sites, start=spec.start, end=spec.end)
    avail = build_cohort(observations, extractions, avail_spec, footprint_sets=footprint_sets)
    report.issues.extend(avail.report.issues)
    p = avail.paired.frame
    ma = avail.model_availability()
    ref = avail.reference_availability()
    days = (ma[["site_id", "date"]].drop_duplicates().merge(
        ref[["site_id", "date", "reference_provenance"]], on=["site_id", "date"], how="left",
        validate="one_to_one").sort_values(["site_id", "date"], ignore_index=True))
    val = p.pivot(index=["site_id", "date"], columns="model", values="modeled_mm")
    obs = p.drop_duplicates(["site_id", "date"]).set_index(["site_id", "date"])["observed_mm"]
    unverified = (p.assign(u=p["flags"].fillna("").str.contains("day_alignment=unverified"))
                  .groupby(["site_id", "date", "model"])["u"].any())
    idx = pd.MultiIndex.from_frame(days[["site_id", "date"]])
    has = (ma.assign(available=ma["available"].astype(bool))
           .pivot_table(index=["site_id", "date"], columns="model", values="available",
                        aggfunc="any")
           .reindex(index=idx, columns=models, fill_value=False).astype(bool))
    x_all = val.reindex(index=idx, columns=members).to_numpy(float)
    has_ref = days["reference_provenance"].notna().to_numpy()
    avail_m = has[members].to_numpy() & has_ref[:, None]   # values exist only on reference days
    n_avail = has[members].to_numpy().sum(axis=1)
    y_all = obs.reindex(idx).to_numpy(float)
    prov = np.array([str(s) if h else None for s, h in
                     zip(days["reference_provenance"], has_ref, strict=True)], dtype=object)
    if spec.missing_member_policy == "require_all":
        eligible = has_ref & (n_avail == k)
        why = np.where(~has_ref, "reference_unavailable",
                       np.where(n_avail < k, "member_missing", None))
    else:
        assert spec.min_members is not None
        eligible = has_ref & (n_avail >= spec.min_members)
        why = np.where(~has_ref, "reference_unavailable",
                       np.where(n_avail < spec.min_members, "below_min_members", None))
    complete = eligible & (n_avail == k)
    reweighted = eligible & ~complete
    filled_ok = (prov != "corrected_filled") | spec.gap_filled_training
    sites_eligible = sorted(set(days.loc[eligible, "site_id"]))
    single_site = len(sites_eligible) <= 1
    base_flags = [*BASE_FLAGS, *(["single_site_not_network_evidence"] if single_site else [])]

    # ---- per-day table with member disagreement
    xv = np.where(avail_m, x_all, np.nan)
    any_val = avail_m.any(axis=1)
    rng = np.full(len(days), np.nan)
    sd = np.full(len(days), np.nan)
    if any_val.any():
        rng[any_val] = np.nanmax(xv[any_val], axis=1) - np.nanmin(xv[any_val], axis=1)
        sd[any_val] = np.nanstd(xv[any_val], axis=1)
    day_rows = []
    for i, r in enumerate(days.itertuples(index=False)):
        avail_names = [m for j, m in enumerate(members) if has.iat[i, j]]
        flags = ["disagreement_not_confidence_interval"]
        if prov[i] == "corrected_filled":
            flags.append("shared_gridmet_drivers")
        day_rows.append({
            "ensemble_id": eid, "site_id": r.site_id, "date": r.date,
            "observed_mm": y_all[i] if has_ref[i] else None,
            "reference_provenance": prov[i] if has_ref[i] else None,
            "n_members_declared": k, "n_members_available": int(n_avail[i]),
            "members_available": _join(avail_names),
            "members_missing": _join([m for m in members if m not in avail_names]),
            "official_available": (bool(has.at[(r.site_id, r.date), spec.official])
                                   if spec.official else None),
            "eligible": bool(eligible[i]), "eligibility_reason": why[i],
            "reweighted": bool(reweighted[i]),
            "member_range_mm": float(rng[i]) if any_val[i] else None,
            "member_sd_mm": float(sd[i]) if any_val[i] else None,
            "flags": _join(flags)})
    n_missing = int((has_ref & ~eligible).sum())
    if n_missing:
        report.info("member_missing", f"{n_missing} reference day(s) not eligible under "
                    f"{spec.missing_member_policy}", n_rows=n_missing)
    if reweighted.any():
        report.warning("reweighted_members", f"{int(reweighted.sum())} day(s) combine only the "
                       "available members; the fitted ensemble changes on them",
                       n_rows=int(reweighted.sum()))

    # ---- unfitted combinations
    mean = np.full(len(days), np.nan)
    med = np.full(len(days), np.nan)
    if eligible.any():
        mean[eligible] = np.nanmean(xv[eligible], axis=1)
        med[eligible] = np.nanmedian(xv[eligible], axis=1)
    pred_rows: list[dict[str, Any]] = []
    derived: dict[str, dict[str, np.ndarray]] = {"baseline": {}}

    def emit(scope: str, product: str, kind: str, values: np.ndarray, mask: np.ndarray,
             fold: np.ndarray | None = None, weights: np.ndarray | None = None) -> None:
        for i in np.flatnonzero(mask & np.isfinite(values)):
            flags = list(base_flags)
            if prov[i] == "corrected_filled":
                flags.append("shared_gridmet_drivers")
            if kind != "official" and reweighted[i]:
                flags.append("reweighted_members")
            pred_rows.append({
                "ensemble_id": eid, "scope": scope, "site_id": days.at[i, "site_id"],
                "date": days.at[i, "date"], "product": product, "product_kind": kind,
                "value_mm": float(values[i]), "observed_mm": y_all[i],
                "reference_provenance": prov[i],
                "fold": None if fold is None else fold[i],
                "inference": "held_out" if kind == "fitted" else "no_fitted_parameters",
                "n_members_used": None if kind == "official" else int(n_avail[i]),
                "reweighted": bool(kind != "official" and reweighted[i]),
                "weights_used": (None if weights is None else ";".join(
                    f"{m}={_fmt(weights[i, j])}" for j, m in enumerate(members)
                    if avail_m[i, j])),
                "flags": _join(flags)})

    if spec.official:
        off = val.reindex(index=idx, columns=[spec.official]).to_numpy(float)[:, 0]
        derived["baseline"][spec.official] = np.where(eligible, off, np.nan)
        emit("baseline", spec.official, "official", off, eligible)
    for c in spec.combinations:
        values = mean if c == "mean" else med
        derived["baseline"][_COMBINATION_PRODUCT[c]] = values
        emit("baseline", _COMBINATION_PRODUCT[c], c, values, eligible)

    # ---- fold designs
    fold_rows: list[dict[str, Any]] = []
    weight_rows: list[dict[str, Any]] = []
    inner_rows: list[dict[str, Any]] = []
    member_rows: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []
    design_status: dict[str, tuple[str, str | None, int | None, int | None]] = {}
    grid_text = ";".join(_fmt(a) for a in spec.shrinkage_grid)
    for design in spec.designs:
        groups = fold_groups(days, design, spec)
        labels = sorted(set(groups[eligible].tolist()))
        if design == "leave_one_site_out" and single_site:
            design_status[design] = (
                "not_available", f"{len(sites_eligible)} station(s) with eligible days "
                "(n_sites = 1): leave-one-site-out needs other stations' station and OpenET "
                "inputs", len(labels), 0)
            continue
        folds = [_fit_fold(g, design, spec, metric_spec, days, x_all, y_all, groups, eligible,
                           complete, filled_ok) for g in labels]
        fitted_any = any(f.status == "fitted" for f in folds)
        fitted_pred = np.full(len(days), np.nan)
        eff_all = np.full((len(days), k), np.nan)
        fold_of = np.full(len(days), None, dtype=object)
        for f in folds:
            test = (groups == f.label) & eligible
            start, end = (fold_period(f.label, spec) if design == "leave_one_year_out"
                          else (None, None))
            flags = list(base_flags)
            if f.status == "fitted":
                assert f.weights is not None and f.w_fit is not None
                pr, eff = combine(x_all[test], avail_m[test], f.weights)
                fitted_pred[test], eff_all[test], fold_of[test] = pr, eff, f.label
                n_zero = int(np.isnan(pr).sum())
                if n_zero:
                    flags.append(f"zero_weight_on_available_members={n_zero}")
                for j, m in enumerate(members):
                    weight_rows.append({
                        "ensemble_id": eid, "design": design, "fold": f.label, "member": m,
                        "weight_unregularised": float(f.w_fit[j]), "alpha": f.alpha,
                        "weight": float(f.weights[j]), "inference": "held_out_fold_weights",
                        "n_training_days": int((f.roles == "training").sum()),
                        "flags": _join(base_flags)})
                for a, value, n in f.inner:
                    inner_rows.append({
                        "ensemble_id": eid, "design": design, "fold": f.label, "alpha": a,
                        "selection_metric": spec.selection_metric, "value": value,
                        "units": "mm d-1", "n_days": n, "n_inner_folds": f.n_inner,
                        "status": "ok" if value is not None else "estimate_undefined",
                        "selected": a == f.alpha})
            cand = groups == f.label
            fold_rows.append({
                "ensemble_id": eid, "design": design, "fold": f.label, "status": f.status,
                "status_detail": f.detail, "held_out_start": start, "held_out_end": end,
                "training_folds": _join(f.training_groups),
                "n_training_folds": len(f.training_groups) if f.status == "fitted" else 0,
                "n_training_days": (int((f.roles == "training").sum())
                                    if f.status == "fitted" else 0),
                "n_training_days_gap_filled": (int(((f.roles == "training")
                                                    & (prov == "corrected_filled")).sum())
                                               if f.status == "fitted" else 0),
                "n_buffer_days_removed": f.n_buffer, "n_small_fold_days_excluded": f.n_small,
                "n_test_days": int(test.sum()),
                "n_test_days_reweighted": int((test & reweighted).sum()),
                "n_inner_folds": f.n_inner, "alpha_selected": f.alpha, "alpha_grid": grid_text,
                "selection_metric": spec.selection_metric, "method": spec.method,
                "regularisation": spec.regularisation, "preprocessing": spec.preprocessing,
                "buffer_days": spec.buffer_days if design == "leave_one_year_out" else 0,
                "member_availability": ";".join(
                    f"{m}={int((avail_m[:, j] & cand).sum())}" for j, m in enumerate(members)),
                "flags": _join(flags)})
            for i in range(len(days)):
                member_rows.append({
                    "ensemble_id": eid, "design": design, "fold": f.label,
                    "site_id": days.at[i, "site_id"], "date": days.at[i, "date"],
                    "group": groups[i], "role": f.roles[i],
                    "in_training": bool(f.status == "fitted" and f.roles[i] == "training")})
        n_fitted = sum(f.status == "fitted" for f in folds)
        if fitted_any:
            derived[design] = {**derived["baseline"], FITTED: fitted_pred}
            emit(design, FITTED, "fitted", fitted_pred, eligible, fold_of, eff_all)
            design_status[design] = ("held_out", _join(
                [f"fold {f.label}: {f.detail}" for f in folds if f.status != "fitted"]),
                len(labels), n_fitted)
            continue
        detail = (f"{len(labels)} {spec.fold_unit if design == 'leave_one_year_out' else 'site'}"
                  f" fold(s) with eligible days ({_join(labels) or 'none'}); "
                  + "; ".join(f"fold {f.label}: {f.detail}" for f in folds))
        if spec.insufficient_data == "exploratory":
            train = complete & filled_ok
            if train.any():
                w = simplex_least_squares(x_all[train], y_all[train])
                for j, m in enumerate(members):
                    weight_rows.append({
                        "ensemble_id": eid, "design": design, "fold": "in_sample", "member": m,
                        "weight_unregularised": float(w[j]), "alpha": None, "weight": None,
                        "inference": "exploratory_in_sample",
                        "n_training_days": int(train.sum()),
                        "flags": _join([*base_flags, "exploratory_in_sample",
                                        "no_held_out_claim"])})
                fold_rows.append({
                    "ensemble_id": eid, "design": design, "fold": "in_sample",
                    "status": "exploratory_in_sample", "status_detail": detail,
                    "held_out_start": None, "held_out_end": None,
                    "training_folds": _join(labels), "n_training_folds": len(labels),
                    "n_training_days": int(train.sum()),
                    "n_training_days_gap_filled": int((train & (prov == "corrected_filled"))
                                                      .sum()),
                    "n_buffer_days_removed": 0, "n_small_fold_days_excluded": 0,
                    "n_test_days": 0, "n_test_days_reweighted": 0, "n_inner_folds": 0,
                    "alpha_selected": None, "alpha_grid": grid_text,
                    "selection_metric": None, "method": spec.method,
                    "regularisation": None, "preprocessing": spec.preprocessing,
                    "buffer_days": None,
                    "member_availability": ";".join(
                        f"{m}={int(avail_m[:, j].sum())}" for j, m in enumerate(members)),
                    "flags": _join([*base_flags, "exploratory_in_sample",
                                    "no_held_out_claim"])})
                design_status[design] = ("exploratory", detail, len(labels), 0)
                continue
        design_status[design] = ("declined", detail, len(labels), 0)
        report.info("ensemble_declined", f"{design}: fitted ensemble declined; {detail}")

    # ---- frozen product cohorts, scored, resampled and contrasted by steps 7-11
    member_specs = [products[m] for m in members]
    prod_map = {m: _derived_product(m, member_specs) for m in (MEAN, MEDIAN, FITTED)}
    if spec.official:
        prod_map[spec.official] = products[spec.official]
    comparisons: dict[str, EnsembleComparison] = {}
    improvement_rows: list[dict[str, Any]] = []
    unver = np.array([any(bool(unverified.get((r.site_id, r.date, m), False)) for m in models)
                      for r in days.itertuples(index=False)], dtype=bool)
    months = pd.DatetimeIndex(days["date"]).month.to_numpy()
    subsets: list[tuple[str, np.ndarray]] = [("all", np.ones(len(days), bool))]
    subsets += [(str(sub["subset_id"]), np.isin(months, list(sub["months"])))
                for sub in spec.subsets]
    for scope, prods in derived.items():
        names = list(prods)
        if len(names) < 2:
            report.warning("too_few_products", f"{scope}: fewer than two products to compare")
            continue
        for subset, in_subset in subsets:
            key = comparison_key(scope, subset)
            ext = []
            for name, values in prods.items():
                ok = np.isfinite(values) & in_subset
                ext.append(pd.DataFrame({
                    "site_id": days.loc[ok, "site_id"].to_numpy(),
                    "date": days.loc[ok, "date"].to_numpy(), "period": "day", "model": name,
                    "extraction_method": side.extraction_method,
                    "footprint_set_id": side.footprint_set_id,
                    "footprint_method": (f"{LEVEL}_ensemble" if name != spec.official
                                         else "official"),
                    "et_mm": values[ok], "fp_weight_sum": np.nan,
                    "fp_weight_retained_frac": np.nan, "fp_weight_retained_denominator": None,
                    "support_id": None,
                    "day_alignment": np.where(unver[ok], "unverified", "declared"),
                    "model_asset_id": None, "footprint_asset_id": None, "source_path": None}))
            cspec = ComparisonSpec(f"{eid}__{key}", "common_date", tuple(names), (v,), (side,),
                                   sites=spec.sites, start=spec.start, end=spec.end,
                                   description=f"ensemble {eid}: {scope} products, {subset} "
                                   f"days ({LEVEL})")
            frames = [CanonicalTable.build(e, EXTRACTIONS, {}, source=f"{eid}/{key}")
                      for e in ext]
            cohort = build_cohort(observations, frames, cspec, footprint_sets=footprint_sets)
            res = score_comparison(cohort, metric_spec, products=prod_map)
            boot = (bootstrap_comparison(cohort, metric_spec, bootstrap_spec, products=prod_map,
                                         metric_results=res)
                    if bootstrap_spec is not None and cohort.status == "ok" else None)
            rspec = RankingSpec(f"{eid}__{key}", metric_spec.metric_set_id,
                                bootstrap_spec.bootstrap_id if bootstrap_spec else "none",
                                {m: RANKING_DIRECTIONS[m] for m in spec.contrast_metrics})
            ranking = rank_comparison(cohort, res, boot, rspec)
            comparisons[key] = EnsembleComparison(scope, cohort, res, boot, ranking, subset)
            if scope == "baseline":
                continue
            c = ranking.contrasts.frame
            c = c[(c["model_a"] == FITTED) & c["ranking_basis"].astype(bool)]
            for r in c.itertuples(index=False):
                favoured = None if pd.isna(r.favoured_model) else str(r.favoured_model)
                if r.status != "ok":
                    verdict = "not_assessed"
                else:
                    verdict = "supported" if favoured == FITTED else "not_supported"
                improvement_rows.append({
                    "ensemble_id": eid, "scope": scope, "subset": subset, "site_id": r.scope,
                    "metric": r.metric, "baseline_product": r.model_b,
                    "block_unit": r.block_unit, "block_length": r.block_length,
                    "interval_method": r.interval_method, "contrast_basis": r.contrast_basis,
                    "estimate": r.estimate, "lower": r.lower, "upper": r.upper,
                    "units": r.units, "sign_meaning": r.sign_meaning, "status": r.status,
                    "favoured_model": favoured, "held_out_improvement": verdict,
                    "n_days": r.n_days,
                    "flags": _join([*base_flags, "held_out_days_only",
                                    *([] if pd.isna(r.flags) else [str(r.flags)])])})

    # ---- summary per scope and subset
    def summary(scope: str, status: str, detail: str | None, n_folds: int | None,
                n_fitted: int | None, interpretation: str) -> None:
        for subset, _ in subsets:
            comp = comparisons.get(comparison_key(scope, subset))
            n_days = None
            if comp is not None:
                n_days = int(comp.cohort.paired.frame[["site_id", "date"]].drop_duplicates()
                             .shape[0])
            note = interpretation if subset == "all" else (
                f"{interpretation}; evaluated on the {subset} months only (training unchanged)")
            summary_rows.append({
                "ensemble_id": eid, "scope": scope, "subset": subset, "status": status,
                "status_detail": detail, "ensemble_level": LEVEL,
                "products": _join(list(derived.get(scope, {}))),
                "members": ";".join(members), "missing_member_policy": spec.missing_member_policy,
                "reference_variant": v, "extraction_method": side.extraction_method,
                "footprint_set_id": side.footprint_set_id, "n_sites": len(sites_eligible),
                "n_folds": n_folds, "n_folds_fitted": n_fitted, "n_days_compared": n_days,
                "comparison_id": comp.cohort.comparison_id if comp else None,
                "cohort_membership_sha256": comp.cohort.fingerprint if comp else None,
                "interpretation": note, "flags": _join(base_flags)})

    summary("baseline", "evaluated", None, None, None,
            "official ensemble and unweighted member combinations on frozen days; nothing is "
            "fitted, so this is an evaluation, not a held-out result")
    meaning = {"held_out": "fitted weights evaluated only on held-out folds",
               "declined": "no fitted weight set: too little independent data",
               "exploratory": "in-sample weights only; no held-out claim",
               "not_available": "the design cannot be built from these inputs"}
    for design, (d_status, d_detail, d_folds, d_fitted) in design_status.items():
        summary(design, d_status, d_detail, d_folds, d_fitted, meaning[d_status])

    attrs = {"workflow": "etcompare.ensemble.run_ensemble", "ensemble": to_plain(spec),
             "ensemble_level": LEVEL, "availability_membership_sha256": avail.fingerprint,
             "spread_label": "member disagreement, not a confidence interval",
             "site_footprint_sets": (dict(spec.site_footprint_sets)
                                     if spec.site_footprint_sets is not None else None),
             "scope": "station level; n_sites = 1 is not network evidence"}
    tables = {
        "ensemble_days": (day_rows, ENSEMBLE_DAYS),
        "ensemble_predictions": (pred_rows, ENSEMBLE_PREDICTIONS),
        "ensemble_folds": (fold_rows, ENSEMBLE_FOLDS),
        "ensemble_weights": (weight_rows, ENSEMBLE_WEIGHTS),
        "ensemble_inner_scores": (inner_rows, ENSEMBLE_INNER_SCORES),
        "ensemble_training_membership": (member_rows, ENSEMBLE_TRAINING_MEMBERSHIP),
        "ensemble_summary": (summary_rows, ENSEMBLE_SUMMARY),
        "ensemble_improvement": (improvement_rows, ENSEMBLE_IMPROVEMENT),
    }
    built = {name: CanonicalTable.build(_frame(rows, schema), schema, attrs, source=eid)
             for name, (rows, schema) in tables.items()}
    return EnsembleResult(spec, built, comparisons, tuple(report.issues))
