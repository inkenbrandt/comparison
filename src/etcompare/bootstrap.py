"""Paired moving-block bootstrap intervals (guide step 10).

:func:`bootstrap_comparison` resamples one :class:`~etcompare.cohorts.Cohort`
under a :class:`~etcompare.config.BootstrapSpec` and returns intervals
(``intervals`` schema v2) for every step-9 metric and paired contrast, plus a
residual autocorrelation diagnostic (``residual_autocorrelation`` schema).

Estimand
--------
For one station and one cohort, each interval describes the sampling
variability of a step-9 metric (or of a paired contrast of two metrics) across
station days. The cohort's daily paired values are treated as one realization
of a weakly dependent process inside uninterrupted runs of calendar days. The
interval is conditional on the station, the cohort rule, the reference variant,
the extraction method and the period of record. It is not an interval for other
stations, other years or a population of sites, and it contains neither
reference measurement error, footprint-model uncertainty nor method-choice
sensitivity (guide step 15 keeps those apart). An interval is not a ranking.

Resampling scheme (segment-truncated moving blocks)
---------------------------------------------------
1. **Unit: the cohort day.** Whole paired rows are resampled. Inside a cohort and
   site every model, side and reference variant covers the same days (checked),
   and one index draw per replicate is applied to all of them, so a paired
   contrast is always computed on identical days.
2. **Segments.** The cohort's days are sorted and split into segments at every
   missing calendar day (gaps are never compressed into consecutive days), at
   sites (each site is resampled on its own) and at the declared season-year
   boundary (``segment_boundary``: ``none``, ``year`` or ``water_year``).
3. **Blocks.** Any cohort day may start a block. A block of nominal length ``b``
   holds the start day and up to ``b - 1`` following days of the *same segment*:
   it stops at the segment end and never spans a gap or a boundary, and blocks
   never wrap around (no circular blocks). Starts are drawn uniformly with
   replacement, blocks are concatenated until the replicate has the original
   number of days ``n``, and the last block is cut so the replicate has exactly
   ``n`` days. ``b = 1`` is the iid-day bootstrap. The step-9 daily metrics do
   not depend on the order of days, so concatenation creates no artificial
   adjacency in any statistic.
4. **Edge handling.** Blocks run forward and stop at segment ends, so a day that
   is ``j`` days after its segment start (0-based) is covered by
   ``min(j + 1, b)`` starts: the first ``b - 1`` days of each segment are drawn
   less often than interior days (the moving-block edge effect, here one-sided).
   Segments shorter than ``b`` give only truncated blocks; such rows carry the
   flag ``short_segments=<k>``.
5. **Metrics are recomputed, not re-implemented.** Every replicate sample goes
   through :func:`etcompare.metrics.compute_metrics` with the metric set's
   metrics and tolerance. A metric undefined in a replicate (for example a
   constant series) makes that replicate invalid for that metric.
6. **Support.** ``n_blocks = sum_s floor(L_s / b)`` disjoint full-length blocks
   over the segment lengths ``L_s``. With ``n_blocks < min_blocks`` nothing is
   drawn and the row's status is ``insufficient_support``.
7. **Intervals.** ``percentile``: the ``(a/2, 1 - a/2)`` quantiles of the valid
   replicate values, ``a = 1 - confidence_level`` (numpy's default linear
   quantile). ``basic``: ``(2*estimate - q_hi, 2*estimate - q_lo)``. No bounds
   when the estimate is undefined (``estimate_undefined``) or fewer than
   ``min_valid_fraction`` of the replicates are valid
   (``insufficient_valid_replicates``). The estimate is always the step-9 value
   on the full cohort, never a replicate mean.

Period metrics
--------------
Metrics of period totals (``partial_sum``, ``complete_total``,
``estimated_total``) resample **whole periods**: the unit is one available
period of the product, blocks are runs of consecutive periods
(``period_block_lengths``), and a segment ends where a period is missing or a
season-year boundary falls between two periods. Period totals are never rebuilt
from resampled daily blocks, so the two interpretations are never mixed. Every
model, side and variant of a cohort has the same available periods (step-9
check), so period indices are paired as well. Without ``period_block_lengths``
period metrics get no intervals.

Contrasts
---------
``contrast = metric(target) - metric(baseline)``, computed in every replicate
from the two metrics on identical indices:

- ``paired_method``: each further side minus the first declared side
  (``extraction_side``). With footprint first and a fixed window second,
  ``mae`` gives ``MAE_window - MAE_footprint``: positive means the footprint
  side has the smaller error (guide step 11 convention).
- ``paired_reference``: each further reference variant minus the first.
- ``pairwise_model``: the second model minus the first.

For error metrics (MAE, RMSE) a negative contrast means the target has the
smaller error; for ``pearson_r``, NSE and KGE a positive one means it scores
higher. Contrasts of percent metrics are in percentage points. All-model
(``common_date``) model contrasts, rankings and network summaries are step 11;
:meth:`BootstrapResult.replicates` keeps the replicate values, drawn on shared
indices, for that step.

Scope and limitations
---------------------
Station level only (``scope_type = site``). A fixed monitored network and a
population of sites are different targets: inference for a population needs a
hierarchical design that resamples sites and, inside them, temporal blocks,
keeping common calendar blocks across sites where shared weather matters.
Neither is attempted here.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

import numpy as np
import pandas as pd

from etcompare.aggregation import PeriodTotals
from etcompare.cohorts import Cohort
from etcompare.config import (
    BootstrapSpec,
    MetricSpec,
    NearZeroTolerance,
    ProductSpec,
    to_plain,
)
from etcompare.errors import ContractError
from etcompare.metrics import MetricResults, compute_metrics, output_metrics, score_comparison
from etcompare.schemas import INTERVALS, RESIDUAL_AUTOCORRELATION, CanonicalTable
from etcompare.validation import Issue, ValidationReport

_SIDE = ["extraction_method", "footprint_set_id"]
_SERIES = ["model", "reference_variant", *_SIDE]
#: Columns identifying one resampled sample (shared indices for every series in it).
_SAMPLE = ["cohort_id", "site_id", "temporal_scale", "period_type", "aggregation_id", "product"]
_UNIT_CODE = {"site_day": 1, "period": 2}

SCHEME = "segment_truncated_moving_block"


# --------------------------------------------------------------------------- blocks

def season_year(dates: pd.DatetimeIndex, boundary: str,
                water_year_start_month: int = 10) -> np.ndarray:
    """Season-year label of each date under ``boundary`` (``none``, ``year``, ``water_year``)."""
    d = pd.DatetimeIndex(dates)
    if boundary == "none":
        return np.zeros(len(d), dtype=np.int64)
    years = np.asarray(d.year, dtype=np.int64)
    if boundary == "year":
        return years
    if boundary == "water_year":
        m = water_year_start_month
        return years + ((np.asarray(d.month) >= m) & (m > 1)).astype(np.int64)
    raise ValueError(f"unknown segment boundary {boundary!r}")


def segment_bounds(first: Sequence[Any] | pd.DatetimeIndex,
                   last: Sequence[Any] | pd.DatetimeIndex | None = None, *,
                   boundary: str, water_year_start_month: int = 10) -> np.ndarray:
    """``(start, stop)`` index pairs of the uninterrupted segments of sorted units.

    A unit covers the calendar days ``first[i]..last[i]`` (a day when ``last`` is
    omitted, a period otherwise). Unit ``i + 1`` continues the segment only when
    it starts the day after unit ``i`` ends and both share the season-year label
    of their first day; any missing day therefore starts a new segment.
    """
    f = pd.DatetimeIndex(first)
    la = f if last is None else pd.DatetimeIndex(last)
    if len(f) != len(la):
        raise ValueError("first and last differ in length")
    if len(f) == 0:
        return np.empty((0, 2), dtype=np.int64)
    if not f.is_monotonic_increasing or f.has_duplicates:
        raise ValueError("units must be sorted and unique")
    if (la < f).any() or (f[1:] <= la[:-1]).any():
        raise ValueError("units overlap or end before they start")
    step = (f[1:] - la[:-1]).days.to_numpy()
    sy = season_year(f, boundary, water_year_start_month)
    brk = (step != 1) | (np.diff(sy) != 0)
    starts = np.concatenate([[0], np.flatnonzero(brk) + 1])
    stops = np.concatenate([starts[1:], [len(f)]])
    return np.column_stack([starts, stops]).astype(np.int64)


@dataclass(frozen=True)
class BlockSupport:
    """How many independent blocks a sample's segments hold for one block length."""

    n_units: int
    n_segments: int
    n_blocks: int  # disjoint full-length blocks: sum over segments of floor(length / b)
    n_short_segments: int  # segments shorter than b (only truncated blocks)


def block_support(bounds: np.ndarray, block_length: int) -> BlockSupport:
    lengths = bounds[:, 1] - bounds[:, 0] if len(bounds) else np.empty(0, dtype=np.int64)
    return BlockSupport(int(lengths.sum()), len(lengths), int((lengths // block_length).sum()),
                        int((lengths < block_length).sum()))


def draw_blocks(bounds: np.ndarray, block_length: int,
                rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    """Starts and lengths of one replicate's blocks (see the module docstring).

    Lengths sum to the number of units; no block extends past its segment.
    """
    if len(bounds) == 0:
        raise ValueError("no units to resample")
    n = int(bounds[-1, 1])
    seg_stop = np.repeat(bounds[:, 1], bounds[:, 1] - bounds[:, 0])
    available = np.minimum(block_length, seg_stop - np.arange(n))
    starts = rng.integers(0, n, size=n)  # every block has >= 1 unit, so n starts suffice
    lengths = available[starts]
    cum = np.cumsum(lengths)
    k = int(np.searchsorted(cum, n)) + 1
    starts, lengths = starts[:k], lengths[:k].copy()
    lengths[-1] -= int(cum[k - 1]) - n
    return starts, lengths


def block_indices(starts: np.ndarray, lengths: np.ndarray) -> np.ndarray:
    """Unit indices of concatenated blocks."""
    offsets = np.arange(int(lengths.sum())) - np.repeat(np.cumsum(lengths) - lengths, lengths)
    return np.repeat(starts, lengths) + offsets


def _stable_int(text: str) -> int:
    return int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:8], 16)


def sample_rng(seed: int, block_unit: str, block_length: int,
               sample_key: Sequence[str]) -> np.random.Generator:
    """Random stream of one sample and block length.

    Derived from the declared seed and the sample's identity only, so results do
    not depend on the order in which samples, comparisons or block lengths run.
    """
    entropy = [int(seed), _UNIT_CODE[block_unit], int(block_length),
               *(_stable_int(str(k)) for k in sample_key)]
    return np.random.default_rng(entropy)


# --------------------------------------------------------------------------- samples

@dataclass(frozen=True)
class PairedSample:
    """Units shared by every series of one cohort/site (and period product).

    ``series`` maps ``(model, reference_variant, extraction_method,
    footprint_set_id)`` to the observed and modeled arrays aligned on the units.
    """

    key: tuple[str, ...]  # values of _SAMPLE
    block_unit: str
    first: pd.DatetimeIndex
    last: pd.DatetimeIndex
    series: Mapping[tuple[str, ...], tuple[np.ndarray, np.ndarray]]

    def bounds(self, spec: BootstrapSpec) -> np.ndarray:
        last = None if self.block_unit == "site_day" else self.last
        return segment_bounds(self.first, last, boundary=spec.segment_boundary,
                              water_year_start_month=spec.water_year_start_month)


def daily_samples(cohort: Cohort) -> list[PairedSample]:
    """The cohort's paired rows as one sample per cohort and site (shared day sets)."""
    p = cohort.paired.frame
    out = []
    for (cid, site), g in p.groupby(["cohort_id", "site_id"], sort=True):
        days = pd.DatetimeIndex(np.sort(g["date"].unique()))
        series = {}
        for k, s in g.groupby(_SERIES, sort=True):
            s = s.sort_values("date")
            if not pd.DatetimeIndex(s["date"]).equals(days):
                raise ContractError(f"cohort {cid} site {site}: series {k} does not cover the "
                                    "cohort's days; paired resampling needs one common mask")
            series[tuple(k)] = (s["observed_mm"].to_numpy(float), s["modeled_mm"].to_numpy(float))
        out.append(PairedSample((cid, site, "daily", "none", "none", "daily_pairs"), "site_day",
                                days, days, MappingProxyType(series)))
    return out


def period_samples(totals: PeriodTotals, metric_spec: MetricSpec) -> list[PairedSample]:
    """Available periods of each scored product, as step 9 selects them (shared periods)."""
    f = totals.table.frame
    f = f[f["product"].isin(metric_spec.period_products)]
    if metric_spec.period_types is not None:
        f = f[f["period_type"].isin(metric_spec.period_types)]
    f = f[f["observed_total_mm"].notna()]
    aid = totals.spec.aggregation_id
    out = []
    for (cid, site, ptype, prod), g in f.groupby(["cohort_id", "site_id", "period_type",
                                                  "product"], sort=True):
        periods = (g[["period_label", "period_start", "period_end"]].drop_duplicates()
                   .sort_values("period_start", ignore_index=True))
        series = {}
        for k, s in g.groupby(_SERIES, sort=True):
            s = s.sort_values("period_start")
            if s["period_label"].tolist() != periods["period_label"].tolist():
                raise ContractError(f"period totals {aid}/{cid}/{ptype}/{prod}: series {k} has "
                                    "other available periods; paired resampling needs one mask")
            series[tuple(k)] = (s["observed_total_mm"].to_numpy(float),
                                s["modeled_total_mm"].to_numpy(float))
        out.append(PairedSample((cid, site, "period", ptype, aid, prod), "period",
                                pd.DatetimeIndex(periods["period_start"]),
                                pd.DatetimeIndex(periods["period_end"]),
                                MappingProxyType(series)))
    return out


def _replicates(sample: PairedSample, bounds: np.ndarray, block_length: int, n_replicates: int,
                metrics: Sequence[str], tolerance: NearZeroTolerance,
                rng: np.random.Generator) -> np.ndarray:
    """Metric values of every replicate: array (series, metric, replicate); NaN = undefined."""
    ids = output_metrics(metrics)
    pairs = list(sample.series.values())
    out = np.full((len(pairs), len(ids), n_replicates), np.nan)
    for r in range(n_replicates):
        idx = block_indices(*draw_blocks(bounds, block_length, rng))
        for i, (o, p) in enumerate(pairs):
            res = compute_metrics(o[idx], p[idx], tolerance=tolerance, metrics=metrics)
            for j, m in enumerate(ids):
                v = res[m].value
                if v is not None:
                    out[i, j, r] = v
    return out


# --------------------------------------------------------------------------- results

@dataclass(frozen=True)
class BootstrapResult:
    """Intervals and the residual autocorrelation diagnostic of one cohort. Immutable."""

    spec: BootstrapSpec
    comparison_id: str
    _intervals: CanonicalTable = field(repr=False)
    _autocorrelation: CanonicalTable = field(repr=False)
    _replicate_index: pd.DataFrame = field(repr=False)
    _replicate_values: np.ndarray = field(repr=False)
    _issues: tuple[Issue, ...] = field(repr=False)
    attributes: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "attributes", MappingProxyType(dict(self.attributes)))

    @property
    def intervals(self) -> CanonicalTable:
        t = self._intervals
        return CanonicalTable(t.schema, t.frame.copy(), dict(t.attributes))

    @property
    def autocorrelation(self) -> CanonicalTable:
        t = self._autocorrelation
        return CanonicalTable(t.schema, t.frame.copy(), dict(t.attributes))

    @property
    def report(self) -> ValidationReport:
        return ValidationReport(f"bootstrap {self.comparison_id}/{self.spec.bootstrap_id}",
                                list(self._issues))

    def replicates(self) -> tuple[pd.DataFrame, np.ndarray]:
        """Replicate metric values (copies): an index frame and an array (series, replicate).

        Row ``i`` of the frame (block unit and length, sample, series, metric)
        describes row ``i`` of the array; NaN marks an undefined replicate. Series
        of one sample and block length were drawn with identical indices, so
        contrasts can be formed replicate by replicate (step 11).
        """
        return self._replicate_index.copy(), self._replicate_values.copy()

    def summary(self) -> pd.DataFrame:
        """Interval rows per block unit, block length and status."""
        f = self._intervals.frame
        return (f.groupby(["block_unit", "block_length", "status"]).size()
                .rename("n_rows").reset_index())


def _union_flags(values: Iterable[object]) -> str | None:
    tokens = sorted({t for v in values if isinstance(v, str) for t in v.split(";") if t})
    return ";".join(tokens) or None


def _contrasts(cohort: Cohort) -> list[tuple[str, tuple[str, ...], tuple[str, ...]]]:
    """(dimension, target series, baseline series) templates; '*' is filled per series."""
    cs = cohort.spec
    sides = [(s.extraction_method, s.footprint_set_id) for s in cs.sides]
    out: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = []
    if cs.kind == "paired_method":
        out += [("extraction_side", ("*", "*", *t), ("*", "*", *sides[0])) for t in sides[1:]]
    elif cs.kind == "paired_reference":
        v = cs.reference_variants
        out += [("reference_variant", ("*", t, "*", "*"), ("*", v[0], "*", "*")) for t in v[1:]]
    elif cs.kind == "pairwise_model":
        out.append(("model", (cs.models[1], "*", "*", "*"), (cs.models[0], "*", "*", "*")))
    return out


def _fill(template: tuple[str, ...], series: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(s if t == "*" else t for t, s in zip(template, series, strict=True))


def _baseline_label(dimension: str, baseline: tuple[str, ...]) -> str:
    if dimension == "extraction_side":
        return f"{baseline[2]}@{baseline[3]}"
    return baseline[{"reference_variant": 1, "model": 0}[dimension]]


def bootstrap_comparison(cohort: Cohort, metric_spec: MetricSpec, spec: BootstrapSpec, *,
                         products: Mapping[str, ProductSpec],
                         period_totals: Sequence[PeriodTotals] = (),
                         metric_results: MetricResults | None = None) -> BootstrapResult:
    """Paired block-bootstrap intervals for ``cohort`` under ``spec``.

    Point estimates come from :func:`etcompare.metrics.score_comparison` (or the
    supplied ``metric_results`` of the same cohort and metric set); replicate
    values from :func:`etcompare.metrics.compute_metrics`. Daily metrics use
    every ``spec.block_lengths``; period metrics every
    ``spec.period_block_lengths``.
    """
    cid = cohort.comparison_id
    report = ValidationReport(f"bootstrap {cid}/{spec.bootstrap_id}")
    if spec.metric_set != metric_spec.metric_set_id:
        raise ContractError(f"bootstrap {spec.bootstrap_id} resamples metric set "
                            f"{spec.metric_set!r}, not {metric_spec.metric_set_id!r}",
                            config_key=f"bootstrap.{spec.bootstrap_id}.metric_set")
    res = metric_results or score_comparison(cohort, metric_spec, products=products,
                                             period_totals=period_totals)
    if (res.attributes.get("cohort_membership_sha256") != cohort.fingerprint
            or res.spec.metric_set_id != metric_spec.metric_set_id):
        raise ContractError(f"metric results are not those of cohort {cid} and metric set "
                            f"{metric_spec.metric_set_id}")
    point = res.table.frame

    samples: list[PairedSample] = daily_samples(cohort) if metric_spec.daily else []
    scored = set(res.attributes.get("aggregations_scored", ()))
    if spec.period_block_lengths:
        for t in period_totals:
            if t.spec.aggregation_id in scored:
                samples += period_samples(t, metric_spec)
    elif (point["temporal_scale"] == "period").any():
        report.info("period_intervals_not_requested", "period-total metrics get no intervals: "
                    "the bootstrap declares no period_block_lengths")
    lengths = {"site_day": spec.block_lengths, "period": spec.period_block_lengths}
    tolerance = {"site_day": metric_spec.daily_tolerance, "period": metric_spec.period_tolerance}
    ids = output_metrics(metric_spec.metrics)
    R = spec.n_replicates

    # ---- replicate values, per sample and block length
    computed: dict[tuple[tuple[str, ...], int], tuple[BlockSupport, dict[Any, np.ndarray]]] = {}
    index_rows: list[dict[str, Any]] = []
    value_rows: list[np.ndarray] = []
    for sample in samples:
        bounds = sample.bounds(spec)
        tol = tolerance[sample.block_unit]
        assert tol is not None  # MetricSpec requires period_tolerance with period products
        for b in lengths[sample.block_unit]:
            support = block_support(bounds, b)
            per_series: dict[Any, np.ndarray] = {}
            if support.n_blocks >= spec.min_blocks:
                rng = sample_rng(spec.seed, sample.block_unit, b, sample.key)
                values = _replicates(sample, bounds, b, R, metric_spec.metrics, tol, rng)
                for i, skey in enumerate(sample.series):
                    for j, m in enumerate(ids):
                        per_series[(skey, m)] = values[i, j]
                        index_rows.append({"block_unit": sample.block_unit, "block_length": b,
                                           **dict(zip(_SAMPLE, sample.key, strict=True)),
                                           **dict(zip(_SERIES, skey, strict=True)),
                                           "metric": m,
                                           "n_valid": int(np.isfinite(values[i, j]).sum())})
                        value_rows.append(values[i, j])
            computed[(sample.key, b)] = (support, per_series)

    # ---- interval rows: every point-estimate row, then contrasts
    point = point.assign(site_id=point["scope"])
    units_of = {"site_day": "daily", "period": "period"}
    by_series = {(tuple(r[_SAMPLE]), tuple(r[_SERIES]), r["metric"]): r
                 for _, r in point.iterrows()}
    alpha = 1.0 - spec.confidence_level
    base = {"comparison_id": cid, "bootstrap_id": spec.bootstrap_id,
            "metric_set_id": metric_spec.metric_set_id, "scope_type": "site",
            "confidence_level": spec.confidence_level, "seed": spec.seed,
            "n_replicates_requested": R}
    rows: list[dict[str, Any]] = []

    def emit(r: pd.Series, estimate: float | None, undefined: str | None, reps: np.ndarray | None,
             support: BlockSupport, b: int, unit: str, contrast: tuple[str, str],
             units: str, flags: str | None) -> None:
        n_valid = int(np.isfinite(reps).sum()) if reps is not None else 0
        tokens = [flags]
        if support.n_short_segments and support.n_blocks >= spec.min_blocks:
            tokens.append(f"short_segments={support.n_short_segments}")
        if undefined is not None:
            status, detail = "estimate_undefined", undefined
        elif support.n_blocks < spec.min_blocks:
            status = "insufficient_support"
            detail = (f"{support.n_blocks} disjoint {b}-{unit} blocks in {support.n_segments} "
                      f"segment(s) < min_blocks {spec.min_blocks}")
        elif n_valid < spec.min_valid_fraction * R:
            status, detail = "insufficient_valid_replicates", f"{n_valid} of {R} replicates valid"
        else:
            status, detail = "ok", None
        for method in spec.interval_methods:
            lo = hi = None
            row_tokens = list(tokens)
            if status == "ok":
                assert reps is not None and estimate is not None
                q_lo, q_hi = np.quantile(reps[np.isfinite(reps)], [alpha / 2, 1 - alpha / 2])
                lo, hi = ((float(q_lo), float(q_hi)) if method == "percentile"
                          else (2 * estimate - float(q_hi), 2 * estimate - float(q_lo)))
                if n_valid < R:
                    row_tokens.append(f"undefined_replicates={R - n_valid}")
                if not lo <= estimate <= hi:
                    row_tokens.append("estimate_outside_interval")
            rows.append({
                **base, **{k: r[k] for k in ("cohort_id", "scope", "temporal_scale",
                                             "period_type", "aggregation_id", "product",
                                             *_SERIES, "metric", "metric_role",
                                             "ranking_scope", "n_days", "n_periods")},
                "contrast_dimension": contrast[0], "contrast_baseline": contrast[1],
                "interval_method": method, "block_unit": unit, "block_length": b,
                "estimate": estimate, "lower": lo, "upper": hi, "units": units,
                "n_replicates_valid": n_valid, "n_segments": support.n_segments,
                "n_blocks": support.n_blocks, "status": status, "status_detail": detail,
                "flags": _union_flags(row_tokens)})

    empty = BlockSupport(0, 0, 0, 0)
    for unit in ("site_day", "period"):
        scale = units_of[unit]
        for _, r in point[point["temporal_scale"] == scale].iterrows():
            for b in lengths[unit]:
                support, per_series = computed.get((tuple(r[_SAMPLE]), b), (empty, {}))
                reps = per_series.get((tuple(r[_SERIES]), r["metric"]))
                v = None if pd.isna(r["value"]) else float(r["value"])
                emit(r, v, None if v is not None else str(r["validity_reason"]), reps, support,
                     b, unit, ("none", "none"), str(r["units"]), r["flags"])
        if not spec.contrasts:
            continue
        for dim, t_tpl, b_tpl in _contrasts(cohort):
            for (skey, series, metric), r in by_series.items():
                if skey[2] != scale or _fill(t_tpl, series) != series:
                    continue
                baseline = _fill(b_tpl, series)
                rb = by_series.get((skey, baseline, metric))
                if rb is None or baseline == series:
                    continue
                vt, vb = r["value"], rb["value"]
                v = None if pd.isna(vt) or pd.isna(vb) else float(vt) - float(vb)
                why = None if v is not None else (
                    f"target {r['validity_reason']}" if pd.isna(vt)
                    else f"baseline {rb['validity_reason']}")
                units = "percentage points" if r["units"] == "%" else str(r["units"])
                for b in lengths[unit]:
                    support, per_series = computed.get((skey, b), (empty, {}))
                    at, ab = per_series.get((series, metric)), per_series.get((baseline, metric))
                    reps = at - ab if at is not None and ab is not None else None
                    emit(r, v, why, reps, support, b, unit,
                         (dim, _baseline_label(dim, baseline)), units,
                         _union_flags([r["flags"], rb["flags"]]))

    frame = pd.DataFrame(rows, columns=INTERVALS.names) if rows else INTERVALS.empty()
    for c in INTERVALS.columns:
        if c.kind in ("float", "int"):
            frame[c.name] = pd.to_numeric(frame[c.name])
    frame = frame.sort_values(list(INTERVALS.primary_key), ignore_index=True)
    attributes: dict[str, Any] = {
        "workflow": "etcompare.bootstrap.bootstrap_comparison", "bootstrap": to_plain(spec),
        "metric_set_id": metric_spec.metric_set_id, "comparison_id": cid,
        "cohort_membership_sha256": cohort.fingerprint, "cohort_kind": cohort.spec.kind,
        "ranking_scope": cohort.spec.ranking_scope, "scheme": SCHEME,
        "resampling_unit": {"daily": "site_day (whole paired rows)",
                            "period": "period (whole available periods)"},
        "quantile": "numpy.quantile default (linear interpolation)",
        "contrast_convention": "metric(target) - metric(baseline)",
        "scope": "station level only; not a network or site-population interval",
    }
    intervals = CanonicalTable.build(frame, INTERVALS, attributes,
                                     source=f"{cid}/{spec.bootstrap_id}")
    acf = residual_autocorrelation(cohort, spec.max_lag)

    idx = pd.DataFrame(index_rows, columns=["block_unit", "block_length", *_SAMPLE, *_SERIES,
                                            "metric", "n_valid"])
    vals = np.vstack(value_rows) if value_rows else np.empty((0, R))
    f = intervals.frame
    for (unit, b, status), n in f.groupby(["block_unit", "block_length", "status"]).size().items():
        report.info("interval_status", f"{unit} block length {b}: {n} rows {status}",
                    field=str(status), n_rows=int(n))
    if (f["status"] == "insufficient_support").any():
        report.warning("insufficient_support", "some samples hold fewer than min_blocks "
                       f"({spec.min_blocks}) disjoint blocks; no interval is given for them")
    return BootstrapResult(spec, cid, intervals, acf, idx, vals, tuple(report.issues),
                           attributes)


# --------------------------------------------------------------------------- diagnostics

def residual_autocorrelation(cohort: Cohort, max_lag: int) -> CanonicalTable:
    """Autocorrelation of daily errors ``e = P - O`` at calendar lags ``1..max_lag``.

    For each series, ``d = e - mean(e)`` over the cohort days and
    ``r_k = mean(d_t * d_(t+k)) / mean(d**2)``, the numerator averaged over the
    pairs of cohort days exactly ``k`` calendar days apart (pairs across missing
    days keep their true lag; nothing is compressed). Null when no pair exists or
    the errors are constant. ``white_noise_bound = 1.96 / sqrt(n_pairs)``.
    """
    rows = []
    for sample in daily_samples(cohort):
        cid, site = sample.key[0], sample.key[1]
        t = ((sample.first - sample.first[0]).days.to_numpy()).astype(np.int64)
        n = len(t)
        for skey, (o, p) in sample.series.items():
            d = (p - o) - float(np.mean(p - o))
            c0 = float(np.mean(d * d))
            for k in range(1, max_lag + 1):
                j = np.searchsorted(t, t + k)
                hit = j < n
                hit[hit] = t[j[hit]] == t[hit] + k
                n_pairs = int(hit.sum())
                acf = (float(np.mean(d[hit] * d[j[hit]])) / c0
                       if n_pairs and c0 > 0 else None)
                rows.append({"comparison_id": cohort.comparison_id, "cohort_id": cid,
                             "site_id": site, **dict(zip(_SERIES, skey, strict=True)),
                             "lag": k, "n_days": n, "n_pairs": n_pairs, "autocorrelation": acf,
                             "white_noise_bound": 1.96 / np.sqrt(n_pairs) if n_pairs else None})
    frame = (pd.DataFrame(rows, columns=RESIDUAL_AUTOCORRELATION.names) if rows
             else RESIDUAL_AUTOCORRELATION.empty())
    for c in ("autocorrelation", "white_noise_bound"):
        frame[c] = pd.to_numeric(frame[c])
    attributes = {"workflow": "etcompare.bootstrap.residual_autocorrelation",
                  "comparison_id": cohort.comparison_id,
                  "cohort_membership_sha256": cohort.fingerprint,
                  "error_convention": "e = modeled - observed", "max_lag": max_lag}
    return CanonicalTable.build(frame, RESIDUAL_AUTOCORRELATION, attributes,
                                source=f"{cohort.comparison_id} residual autocorrelation")
