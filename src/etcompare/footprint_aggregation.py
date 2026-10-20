"""Temporal aggregation of per-timestep footprints (guide step 6).

For one period (a station day or a calendar month) and its eligible
timesteps *E*:

    W_i = sum_{t in E} a_t * f_it / sum_{t in E} a_t

- ``f_it`` is the cell contribution of timestep *t*: density times cell
  area for ``density_per_m2`` input, the value itself for
  ``cell_contribution``. With basis ``renormalized_per_timestep`` each
  timestep is first divided by its own grid total.
- ``a_t = s_t * d_t`` when ``include_duration`` is declared, else ``s_t``.
  ``d_t`` is the interval length in seconds and ``s_t`` the temporal weight
  (1 for ``time_mean``). Multiplying every ``s_t`` by one common factor leaves
  ``W`` unchanged.

Timesteps are assigned to station days by their interval: a start-labelled
stamp belongs to its own calendar day, an end-labelled stamp to the day of
``stamp - d_t``. Stamps are naive station-time labels; nothing is shifted.

Per-timestep footprints that were already truncated and renormalized
(``normalized_cell_weight``) carry no original capture, so they can only be
aggregated with basis ``renormalized_per_timestep``.

An aggregate can only be built from per-timestep inputs. When they, or the
temporal weights a method needs, are missing, the result is
``not_available`` with a reason: a single daily raster cannot be re-weighted
(guide step 6).
"""

from __future__ import annotations

import json
import math
import os
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from etcompare.config import (
    FOOTPRINT_VALUE_SEMANTICS,
    INTERVAL_LABELS,
    FootprintSetSpec,
    TemporalAggregationSpec,
    to_plain,
)
from etcompare.errors import ContractError, GridError, SchemaError
from etcompare.io.atomic import write_csv_atomic, write_text_atomic
from etcompare.results import ExclusionLog, format_key
from etcompare.spatial import Grid, is_metric_projected
from etcompare.validation import ValidationReport

STAGE = "footprint_aggregation"
PERIOD_DENOMINATOR = "period_duration"


@dataclass(frozen=True)
class TimestepFootprints:
    """A stack of per-timestep footprints on one grid.

    ``values`` has shape ``(T, height, width)``. ``times`` are naive
    station-time stamps labelling each interval's ``interval_label`` end;
    ``durations_s`` are the interval lengths in seconds. ``daytime`` is an
    optional boolean flag per timestep. NaN and ``nodata`` cells contribute
    nothing.
    """

    site_id: str
    values: np.ndarray
    grid: Grid
    times: pd.DatetimeIndex
    durations_s: np.ndarray
    value_semantics: str
    interval_label: str
    daytime: np.ndarray | None = None
    nodata: float | None = None

    def __post_init__(self) -> None:
        v = np.asarray(self.values, dtype="float64")
        if v.ndim != 3 or v.shape[1:] != self.grid.shape:
            raise GridError(f"timestep footprints {v.shape} do not match grid "
                            f"{self.grid.shape}")
        t = pd.DatetimeIndex(self.times)
        if t.tz is not None:
            raise SchemaError("timestep stamps are timezone-aware; convert them to naive "
                              "station-time labels with the declared day definition first")
        if len(t) != v.shape[0]:
            raise SchemaError(f"{len(t)} stamps for {v.shape[0]} timesteps")
        if t.hasnans or not t.is_unique or not t.is_monotonic_increasing:
            raise SchemaError("timestep stamps must be non-null, unique and increasing")
        d = np.asarray(self.durations_s, dtype="float64")
        if d.shape != (len(t),) or not np.all(np.isfinite(d)) or np.any(d <= 0):
            raise SchemaError("durations_s must be one positive finite length per timestep")
        if self.value_semantics not in FOOTPRINT_VALUE_SEMANTICS:
            raise ContractError(f"timestep footprint value semantics {self.value_semantics!r} "
                                f"not in {list(FOOTPRINT_VALUE_SEMANTICS)}")
        if self.interval_label not in INTERVAL_LABELS:
            raise ContractError(f"interval_label must be one of {list(INTERVAL_LABELS)}")
        if self.daytime is not None:
            day = np.asarray(self.daytime)
            if day.shape != (len(t),) or day.dtype != bool:
                raise SchemaError("daytime must be one boolean per timestep")
            object.__setattr__(self, "daytime", day)
        object.__setattr__(self, "values", v)
        object.__setattr__(self, "times", t)
        object.__setattr__(self, "durations_s", d)

    def period_labels(self, period: str) -> pd.DatetimeIndex:
        """The station-day (or month-start) label of each timestep's interval."""
        start = self.times
        if self.interval_label == "end":
            start = start - pd.to_timedelta(self.durations_s, unit="s")
        day = start.normalize()
        if period == "day":
            return day
        if period == "month":
            return day.to_period("M").to_timestamp()
        raise ValueError(f"period must be 'day' or 'month', got {period!r}")


@dataclass(frozen=True)
class AggregatedFootprint:
    """One period's aggregate, or the reason it could not be built."""

    site_id: str
    date: pd.Timestamp
    period: str
    method_id: str
    status: str
    contribution: np.ndarray | None
    grid: Grid | None
    value_semantics: str | None
    n_timesteps: int
    n_eligible: int
    excluded: Mapping[str, int]
    temporal_weight_total: float
    temporal_coverage_frac: float | None
    domain_capture_frac: float | None
    n_negative_cells: int = 0
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "ok"


@dataclass(frozen=True)
class FootprintAggregationResult:
    """All periods of one aggregation, with a summary table and exclusions."""

    spec: TemporalAggregationSpec
    status: str  # ok | not_available
    reason: str
    periods: tuple[AggregatedFootprint, ...] = ()
    report: ValidationReport = field(default_factory=lambda: ValidationReport("aggregation"))
    exclusions: ExclusionLog = field(default_factory=ExclusionLog)

    @property
    def available(self) -> bool:
        return self.status == "ok"

    def summary(self) -> pd.DataFrame:
        cols = ["site_id", "date", "period", "method_id", "status", "n_timesteps",
                "n_eligible", "temporal_weight_total", "temporal_coverage_frac",
                "domain_capture_frac", "n_negative_cells", "excluded", "detail"]
        rows = [{**{c: getattr(p, c) for c in cols if c != "excluded"},
                 "excluded": ";".join(f"{k}={v}" for k, v in sorted(p.excluded.items()))}
                for p in self.periods]
        return pd.DataFrame(rows, columns=cols)


def combine_results(results: Sequence[FootprintAggregationResult]
                    ) -> FootprintAggregationResult:
    """Join results of one spec computed on separate chunks (e.g. monthly stacks).

    Chunks must not share a period label; a period split across chunks would be
    aggregated twice instead of once.
    """
    if not results:
        raise ValueError("no results to combine")
    spec = results[0].spec
    if any(r.spec != spec for r in results):
        raise ContractError("cannot combine results of different aggregation specs")
    usable = [r for r in results if r.available]
    if not usable:
        return results[0]
    periods = [p for r in usable for p in r.periods]
    keys = [(p.site_id, p.period, p.date) for p in periods]
    if len(set(keys)) != len(keys):
        raise ContractError("chunks share a period; aggregate each period from one chunk")
    report = ValidationReport(f"footprint aggregation {spec.method_id}")
    exclusions = ExclusionLog()
    for r in usable:
        report.extend(r.report)
        exclusions.extend(r.exclusions)
    return FootprintAggregationResult(spec, "ok", "", tuple(sorted(
        periods, key=lambda p: (p.site_id, p.date))), report, exclusions)


def not_available(spec: TemporalAggregationSpec, reason: str) -> FootprintAggregationResult:
    """A skipped aggregation: nothing is invented in place of missing inputs."""
    report = ValidationReport(f"footprint aggregation {spec.method_id}")
    report.info("not_available", reason)
    return FootprintAggregationResult(spec, "not_available", reason, report=report)


def _contributions(stack: TimestepFootprints) -> np.ndarray:
    v = stack.values
    missing = ~np.isfinite(v)
    if stack.nodata is not None and not math.isnan(stack.nodata):
        missing |= v == stack.nodata
    w = np.where(missing, 0.0, v)
    if stack.value_semantics == "density_per_m2":
        if not is_metric_projected(stack.grid.crs):
            raise GridError("density_per_m2 footprints need a projected CRS in metres")
        w = w * stack.grid.cell_area
    return w


def _weights(spec: TemporalAggregationSpec, stack: TimestepFootprints,
             weights: pd.Series | Sequence[float] | np.ndarray | None) -> np.ndarray:
    if spec.temporal_weighting == "time_mean":
        if weights is not None:
            raise ContractError(f"aggregation {spec.method_id} is time_mean; it takes no "
                                "temporal weights")
        return np.ones(len(stack.times))
    if isinstance(weights, pd.Series):
        idx = pd.DatetimeIndex(weights.index)
        if idx.tz is not None:
            raise SchemaError("temporal weight stamps are timezone-aware")
        if not idx.is_unique:
            raise SchemaError("temporal weight stamps are not unique")
        # Exact stamp match only: a weight at another stamp is not this timestep's.
        return pd.to_numeric(weights, errors="raise").reindex(stack.times).to_numpy(
            dtype="float64", na_value=np.nan)
    s = np.asarray(weights, dtype="float64")
    if s.shape != (len(stack.times),):
        raise SchemaError(f"{s.shape[0] if s.ndim else 0} temporal weights for "
                          f"{len(stack.times)} timesteps")
    return s


def aggregate_footprints(
    stack: TimestepFootprints | None,
    spec: TemporalAggregationSpec,
    *,
    weights: pd.Series | Sequence[float] | np.ndarray | None = None,
    period: str = "day",
) -> FootprintAggregationResult:
    """Aggregate per-timestep footprints into one footprint per period.

    ``weights`` gives ``s_t`` for weighted methods, as a Series indexed by the
    timestep stamps (matched exactly) or an array aligned with them.
    Returns ``not_available`` when the stack or the weights are missing.
    """
    if stack is None:
        return not_available(spec, "per-timestep footprints not supplied; a daily footprint "
                             "raster cannot be re-weighted")
    if spec.temporal_weighting != "time_mean" and weights is None:
        return not_available(spec, f"{spec.temporal_weighting} aggregation needs a temporal "
                             "weight series, none supplied")
    if spec.basis == "original_contribution" and \
            stack.value_semantics == "normalized_cell_weight":
        raise ContractError(f"aggregation {spec.method_id}: per-timestep footprints are "
                            "normalized_cell_weight (already truncated and renormalized), so "
                            "they carry no original contribution; declare basis "
                            "renormalized_per_timestep")
    if spec.night_policy == "exclude" and stack.daytime is None:
        raise ContractError(f"aggregation {spec.method_id}: night_policy=exclude needs a "
                            "daytime flag per timestep")

    report = ValidationReport(f"footprint aggregation {spec.method_id} ({stack.site_id})")
    exclusions = ExclusionLog()
    f = _contributions(stack)
    s = _weights(spec, stack, weights)
    d = stack.durations_s
    totals = f.sum(axis=(1, 2))
    neg_cells = (f < 0).any(axis=(1, 2))

    # Reason each timestep is ineligible ("" = eligible), in order of precedence.
    reason = np.full(len(s), "", dtype=object)
    reason[neg_cells] = "negative_footprint_cells"
    reason[(reason == "") & ~(totals > 0)] = "no_footprint_mass"
    reason[(reason == "") & ~np.isfinite(s)] = "weight_missing"
    if spec.night_policy == "exclude":
        reason[(reason == "") & ~stack.daytime] = "night"  # type: ignore[operator]
    negative_s = (reason == "") & (s < 0)
    if spec.negative_weight_policy != "keep":
        reason[negative_s] = "negative_temporal_weight"
    gap_reasons = {"negative_footprint_cells", "no_footprint_mass", "weight_missing"}

    if spec.basis == "renormalized_per_timestep":
        with np.errstate(divide="ignore", invalid="ignore"):
            f = f / np.where(totals > 0, totals, np.nan)[:, None, None]
    a = s * d if spec.include_duration else s.copy()

    labels = stack.period_labels(period)
    out: list[AggregatedFootprint] = []
    for label in labels.unique():
        idx = np.flatnonzero(labels == label)
        r = reason[idx]
        counts = Counter(x for x in r if x)
        excluded = dict(counts)
        period_s = 86400.0 if period == "day" else \
            86400.0 * pd.Timestamp(label).days_in_month
        base: dict[str, Any] = dict(site_id=stack.site_id, date=pd.Timestamp(label),
                                    period=period, method_id=spec.method_id,
                                    n_timesteps=len(idx), excluded=excluded)
        status, detail = "ok", ""
        if spec.gap_policy == "reject_period" and gap_reasons & set(counts):
            status = "rejected_gaps"
            detail = ", ".join(f"{k}={v}" for k, v in sorted(counts.items())
                               if k in gap_reasons)
        elif spec.negative_weight_policy == "reject_period" and counts["negative_temporal_weight"]:
            status = "rejected_negative_temporal_weight"
            detail = f"{counts['negative_temporal_weight']} negative weight(s)"
        elig = idx[r == ""]
        coverage = float(d[elig].sum() / period_s)
        a_e = a[elig]
        a_total = float(a_e.sum()) if len(elig) else 0.0
        if status == "ok" and not len(elig):
            status, detail = "no_eligible_timesteps", "every timestep was excluded"
        elif status == "ok" and not a_total > 0:
            status, detail = "no_positive_temporal_weight", f"sum of a_t = {a_total:.6g}"
        elif status == "ok" and spec.min_temporal_coverage is not None and \
                coverage < spec.min_temporal_coverage:
            status = "insufficient_temporal_coverage"
            detail = f"coverage {coverage:.3f} < {spec.min_temporal_coverage}"
        if status != "ok":
            out.append(AggregatedFootprint(
                **base, status=status, contribution=None, grid=None, value_semantics=None,
                n_eligible=len(elig), temporal_weight_total=a_total,
                temporal_coverage_frac=coverage, domain_capture_frac=None, detail=detail))
            exclusions.add(STAGE, format_key(site_id=stack.site_id, date=label,
                                             method=spec.method_id), status, detail=detail)
            continue
        w = np.tensordot(a_e, f[elig], axes=(0, 0)) / a_total
        if spec.basis == "original_contribution":
            semantics, capture = "cell_contribution", float(w.sum())
        else:
            semantics, capture = "normalized_cell_weight", None
        n_neg = int((w < 0).sum())
        n_neg_s = int(negative_s[idx].sum())
        if n_neg_s:
            action = "kept" if spec.negative_weight_policy == "keep" else "excluded"
            exclusions.add(STAGE, format_key(site_id=stack.site_id, date=label,
                                             method=spec.method_id),
                           f"negative_temporal_weight_{action}", severity="info",
                           detail=f"{n_neg_s} timestep(s); {n_neg} negative aggregate cell(s)")
        out.append(AggregatedFootprint(
            **base, status="ok", contribution=w, grid=stack.grid, value_semantics=semantics,
            n_eligible=len(elig), temporal_weight_total=a_total,
            temporal_coverage_frac=coverage, domain_capture_frac=capture,
            n_negative_cells=n_neg))

    n_ok = sum(p.ok for p in out)
    report.info("aggregation_counts", f"{n_ok} of {len(out)} {period} period(s) aggregated; "
                f"timestep exclusions {dict(Counter(x for x in reason if x))}", n_rows=n_ok)
    report.info("temporal_coverage_denominator",
                f"temporal_coverage_frac = eligible interval duration / {PERIOD_DENOMINATOR}")
    return FootprintAggregationResult(spec, "ok", "", tuple(out), report, exclusions)


def write_footprint_set(
    result: FootprintAggregationResult,
    out_dir: str | os.PathLike[str],
    *,
    set_id: str,
    layer: str | None = None,
) -> FootprintSetSpec:
    """Write each aggregated period as a footprint GeoTIFF and return its set declaration.

    Files are named ``ffp_<site>_<daily|monthly>_<layer>_<stamp>.tif``, so the
    directory is discovered like any other footprint set. Original domain
    capture (basis ``original_contribution``) goes into
    ``<set_id>_domain_capture.csv`` and is attached through the returned spec.
    Needs the ``geo`` extra.
    """
    from etcompare.io.rasters import write_footprint

    if not result.available:
        raise ContractError(f"aggregation {result.spec.method_id} is not available: "
                            f"{result.reason}")
    layer = layer or result.spec.method_id.replace("_", "")
    if not layer.isalnum():
        raise ContractError(f"footprint layer token {layer!r} must be alphanumeric")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    capture_rows: list[dict[str, Any]] = []
    semantics: set[str] = set()
    for p in result.periods:
        if not p.ok:
            continue
        agg, stamp = ("daily", p.date.strftime("%Y%m%d")) if p.period == "day" else \
            ("monthly", p.date.strftime("%Y%m"))
        write_footprint(out / f"ffp_{p.site_id}_{agg}_{layer}_{stamp}.tif", p.contribution,  # type: ignore[arg-type]
                        p.grid)  # type: ignore[arg-type]
        semantics.add(str(p.value_semantics))
        if p.domain_capture_frac is not None and p.period == "day":
            capture_rows.append({"time": p.date.date().isoformat(),
                                 "domain_capture_frac": p.domain_capture_frac,
                                 "temporal_coverage_frac": p.temporal_coverage_frac})
    if len(semantics) != 1:
        raise ContractError(f"aggregation produced no usable period or mixed semantics "
                            f"{sorted(semantics)}")
    csv = None
    if capture_rows:
        csv = out / f"{set_id}_domain_capture.csv"
        write_csv_atomic(pd.DataFrame(capture_rows), csv)
    spec = result.spec
    fs = FootprintSetSpec(
        set_id=set_id, root=out, layers=(layer,), value_semantics=semantics.pop(),
        weighting_method=f"etcompare_aggregation:{spec.method_id}",
        temporal_weighting=spec.temporal_weighting,
        domain_capture_csv=csv, domain_capture_column="domain_capture_frac" if csv else None,
        domain_capture_units="fraction")
    write_text_atomic(
        json.dumps({"aggregation": to_plain(spec), "footprint_set": to_plain(fs)}, indent=2),
        out / f"{set_id}_aggregation.json")
    return fs


def common_window(a: Grid, b: Grid) -> tuple[slice, slice, slice, slice] | None:
    """Index windows of the overlap of two north-up grids with equal cells and CRS.

    Returns ``(rows_a, cols_a, rows_b, cols_b)``, or ``None`` when the grids are not
    aligned or do not overlap.
    """
    if a.crs != b.crs or a.transform[0] != b.transform[0] or a.transform[4] != b.transform[4]:
        return None
    dc = (b.transform[2] - a.transform[2]) / a.transform[0]
    dr = (b.transform[5] - a.transform[5]) / a.transform[4]
    if abs(dc - round(dc)) > 1e-6 or abs(dr - round(dr)) > 1e-6:
        return None
    dc, dr = round(dc), round(dr)  # b's origin in a's cells
    r0, c0 = max(0, dr), max(0, dc)
    r1, c1 = min(a.height, dr + b.height), min(a.width, dc + b.width)
    if r1 <= r0 or c1 <= c0:
        return None
    return (slice(r0, r1), slice(c0, c1), slice(r0 - dr, r1 - dr), slice(c0 - dc, c1 - dc))


def tv_distance(a: np.ndarray, b: np.ndarray) -> float:
    """Total-variation distance ``0.5 * sum |a - b|`` of two footprints, each renormalized.

    Non-finite and non-positive cells count as zero. 0 = identical shape, 1 = disjoint;
    NaN when either has no positive mass.
    """
    a = np.where(np.isfinite(a) & (a > 0), a, 0.0)
    b = np.where(np.isfinite(b) & (b > 0), b, 0.0)
    if a.sum() <= 0 or b.sum() <= 0:
        return math.nan
    return float(0.5 * np.abs(a / a.sum() - b / b.sum()).sum())
