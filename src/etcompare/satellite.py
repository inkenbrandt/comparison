"""Satellite support: errors on clear-observation versus interpolated days (guide step 13).

:func:`support_status` turns declared clear-observation metadata
(:class:`~etcompare.config.SupportSourceSpec`) into a per site/day/model status, and
:func:`assess_satellite_support` compares errors of one **frozen** cohort on the days
whose support is known.

What counts as support
----------------------
Only authoritative product metadata, declared with its authority and meaning: a
per-pixel or per-site clear-observation flag, or a per-site count of clear observations
with a declared ``min_clear_count``. Statuses are ``clear_observation``,
``interpolated`` (the metadata says there was no clear observation that day) and
``unknown``. In particular:

- a day absent from the metadata is ``unknown``; missing rows are not cloud evidence;
- a non-null interpolated ET value is not a clear observation, and a raster with a value
  every day carries no acquisition information (D-011);
- a filename or scene date is a label, not support;
- scene-level cloud cover is a scene diagnostic, never a pixel-clear flag (D-011); the
  configuration refuses it as a source.

Without any source every analysis is ``not_available`` with the reason.

Subsets of one frozen cohort
----------------------------
Each model can have its own support. A cohort day is a ``clear_observation`` day only
when every model of the cohort is clear that day, and an ``interpolated`` day only when
every model is interpolated, so both subsets hold identical days for every model. Days
with mixed or unknown support are counted (``n_days_mixed_support``,
``n_days_unknown_support``) and belong to neither subset; each model's own counts are
recorded (``n_days_model_*``). Metrics come from
:func:`etcompare.metrics.compute_metrics` with the metric set's tolerance; a subset with
fewer than ``min_subset_days`` days has a status and no value.

Acquisition timing
------------------
Optional binned variables use the days on which every cohort model is clear, over the
whole support record (not only cohort days):

- ``days_since_previous_clear``: day minus the latest clear day on or before it (uses no
  later acquisition);
- ``days_to_nearest_clear``: distance to the nearest clear day in either direction;
- ``enclosing_gap_days``: next clear day minus previous clear day (0 on a clear day).

The last two use later acquisitions and are labelled ``retrospective``. Bins are the
declared edges (``[lower, upper)``, the last closed); they never come from errors.
Days outside the edges or without a value are reported as remainder rows.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

import numpy as np
import pandas as pd

from etcompare.cohorts import Cohort
from etcompare.config import MetricSpec, SatelliteSupportSpec, SupportSourceSpec, to_plain
from etcompare.diagnostics import assign_bins
from etcompare.errors import ContractError, DataValidationError
from etcompare.metrics import METRIC_DEFINITIONS, compute_metrics
from etcompare.schemas import (
    REPRESENTATIVENESS_AVAILABILITY,
    SATELLITE_SUPPORT_ERRORS,
    CanonicalTable,
    TableSchema,
    check_primary_key,
)
from etcompare.validation import Issue, ValidationReport

_SERIES = ["model", "reference_variant", "extraction_method", "footprint_set_id"]
RETROSPECTIVE = {"days_since_previous_clear": False, "days_to_nearest_clear": True,
                 "enclosing_gap_days": True}
NO_METADATA = ("no authoritative per-pixel or per-site clear-observation metadata is declared "
               "(D-011); clear-versus-interpolated analyses are not available, and cloud cover "
               "is never inferred from missing rows, filenames, scene dates or daily values")


def support_status(sources: tuple[SupportSourceSpec, ...] | list[SupportSourceSpec],
                   tables: Mapping[str, pd.DataFrame]) -> pd.DataFrame:
    """Per site/date/model support from declared sources.

    Columns ``site_id``, ``date``, ``model``, ``support_status`` (``clear_observation`` or
    ``interpolated``), ``clear_count`` (count sources) and ``source_id``. Only rows present
    in the metadata appear; every other day is unknown.
    """
    parts = []
    for s in sources:
        if s.source_id not in tables:
            raise ContractError(f"support source {s.source_id}: table not supplied")
        t = tables[s.source_id]
        cols = [s.site_column, s.date_column, s.value_column] + (
            [s.model_column] if s.model_column else [])
        missing = [c for c in cols if c not in t.columns]
        if missing:
            raise ContractError(f"support source {s.source_id}: columns {missing} not found")
        v = t[s.value_column]
        if v.isna().any():
            raise DataValidationError(f"support source {s.source_id}: null values in "
                                      f"{s.value_column}; absent days must be absent rows")
        if s.kind == "per_site_count":
            num = pd.to_numeric(v, errors="raise")
            if (num < 0).any() or (num != np.round(num)).any():
                raise DataValidationError(f"support source {s.source_id}: counts must be "
                                          "non-negative integers")
            clear = num >= int(s.min_clear_count)  # type: ignore[arg-type]
            count: pd.Series = num.astype("Int64")
        else:
            if v.dtype == bool or str(v.dtype) == "boolean":
                clear = v.astype(bool)
            elif set(pd.unique(v)) <= {0, 1}:
                clear = v.astype(int) == 1
            else:
                raise DataValidationError(f"support source {s.source_id}: a flag must be "
                                          "boolean or 0/1")
            count = pd.Series(pd.NA, index=t.index, dtype="Int64")
        frame = pd.DataFrame({"site_id": t[s.site_column].astype(str),
                              "date": pd.to_datetime(t[s.date_column]).dt.normalize(),
                              "support_status": np.where(clear, "clear_observation",
                                                         "interpolated"),
                              "clear_count": count, "source_id": s.source_id})
        if s.model_column:
            frame["model"] = t[s.model_column].astype(str).to_numpy()
            frame = frame[frame["model"].isin(s.models)]
        else:
            frame = pd.concat([frame.assign(model=m) for m in s.models], ignore_index=True)
        parts.append(frame)
    if not parts:
        return pd.DataFrame(columns=["site_id", "date", "model", "support_status", "clear_count",
                                     "source_id"])
    out = pd.concat(parts, ignore_index=True)
    check_primary_key(out, ("site_id", "date", "model"), table="satellite support")
    return out.sort_values(["site_id", "model", "date"], ignore_index=True)


def acquisition_gaps(days: pd.DatetimeIndex, clear_days: pd.DatetimeIndex
                     ) -> dict[str, np.ndarray]:
    """Timing variables of ``days`` relative to ``clear_days`` (NaN where undefined)."""
    d = days.to_numpy("datetime64[D]").astype(np.int64)
    a = np.unique(clear_days.to_numpy("datetime64[D]").astype(np.int64))
    n = len(d)
    out = {k: np.full(n, np.nan) for k in RETROSPECTIVE}
    if not len(a):
        return out
    i = np.searchsorted(a, d, side="right")  # a[i-1] <= d < a[i]
    has_prev, has_next = i > 0, i < len(a)
    prev = np.where(has_prev, a[np.clip(i - 1, 0, len(a) - 1)], 0)
    nxt = np.where(has_next, a[np.clip(i, 0, len(a) - 1)], 0)
    since = np.where(has_prev, d - prev, np.nan)
    until = np.where(has_next, nxt - d, np.nan)
    out["days_since_previous_clear"] = since
    out["days_to_nearest_clear"] = np.fmin(since, until)
    on_clear = has_prev & (since == 0)
    out["enclosing_gap_days"] = np.where(on_clear, 0.0,
                                         np.where(has_prev & has_next, nxt - prev, np.nan))
    return out


@dataclass(frozen=True)
class SatelliteSupportResult:
    """Errors by satellite support of one cohort, and availability. Immutable."""

    spec: SatelliteSupportSpec
    comparison_id: str
    _tables: Mapping[str, CanonicalTable] = field(repr=False)
    _issues: tuple[Issue, ...] = field(repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_tables", MappingProxyType(dict(self._tables)))

    def _get(self, name: str) -> CanonicalTable:
        t = self._tables[name]
        return CanonicalTable(t.schema, t.frame.copy(), dict(t.attributes))

    @property
    def errors(self) -> CanonicalTable:
        return self._get("satellite_support_errors")

    @property
    def availability(self) -> CanonicalTable:
        return self._get("representativeness_availability")

    @property
    def report(self) -> ValidationReport:
        return ValidationReport(f"satellite support {self.comparison_id}", list(self._issues))


def _frame(rows: list[dict[str, Any]], schema: TableSchema) -> pd.DataFrame:
    frame = pd.DataFrame(rows, columns=schema.names) if rows else schema.empty()
    for c in schema.columns:
        if c.kind in ("float", "int"):
            frame[c.name] = pd.to_numeric(frame[c.name])
    return frame.sort_values(list(schema.primary_key), ignore_index=True)


def assess_satellite_support(cohort: Cohort, spec: SatelliteSupportSpec,
                             metric_spec: MetricSpec, *,
                             support: pd.DataFrame | None = None) -> SatelliteSupportResult:
    """Errors on clear-observation and interpolated days of a frozen ``cohort``.

    ``support`` is the output of :func:`support_status` (None when no source exists).
    """
    cid = cohort.comparison_id
    sid = spec.satellite_support_id
    report = ValidationReport(f"satellite support {cid}/{sid}")
    if metric_spec.metric_set_id != spec.metric_set:
        raise ContractError(f"satellite support {sid} uses metric set {spec.metric_set!r}, not "
                            f"{metric_spec.metric_set_id!r}")
    if not cohort.spec.freeze_models:
        raise ContractError(f"comparison {cid} (kind {cohort.spec.kind}) does not freeze days "
                            "across models; clear/interpolated subsets need one frozen cohort")
    paired = cohort.paired.frame.sort_values(["cohort_id", "site_id", *_SERIES, "date"],
                                             ignore_index=True)
    sites = sorted(paired["site_id"].unique()) if len(paired) else ["all"]
    avail: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []

    def unavailable(code: str, reason: str, site: str = "all", n_days: int | None = None) -> None:
        avail.append({"analysis_id": sid, "site_id": site, "analysis": "satellite_support",
                      "layer_id": "none", "extraction_method": "all", "day_set": cid,
                      "matching_policy": "all", "domain": "all", "date_label": "all",
                      "status": "not_available", "reason_code": code, "reason": reason,
                      "n_days": n_days, "flags": None})

    models = list(cohort.spec.models)
    covered = {m for s in spec.sources for m in s.models}
    if not spec.sources or support is None or not len(support):
        for site in sites:
            unavailable("no_acquisition_metadata", NO_METADATA, site)
    elif not set(models) <= covered:
        for site in sites:
            unavailable("support_missing_for_models",
                        f"no support source for {sorted(set(models) - covered)}; subsets with "
                        "identical days across models cannot be formed", site)
    else:
        rows = _subset_rows(spec, metric_spec, cohort, paired, support, models, report)
        for site in sites:
            avail.append({"analysis_id": sid, "site_id": site, "analysis": "satellite_support",
                          "layer_id": "none", "extraction_method": "all", "day_set": cid,
                          "matching_policy": "all", "domain": "all", "date_label": "all",
                          "status": "available", "reason_code": None,
                          "reason": "support from " + "; ".join(
                              f"{s.source_id} ({s.authority})" for s in spec.sources),
                          "n_days": None, "flags": None})
    attrs = {"workflow": "etcompare.satellite.assess_satellite_support",
             "satellite_support": to_plain(spec), "comparison_id": cid,
             "cohort_membership_sha256": cohort.fingerprint,
             "error_convention": "e = modeled - observed"}
    tables = {"satellite_support_errors": CanonicalTable.build(
        _frame(rows, SATELLITE_SUPPORT_ERRORS), SATELLITE_SUPPORT_ERRORS, attrs, source=cid),
        "representativeness_availability": CanonicalTable.build(
            _frame(avail, REPRESENTATIVENESS_AVAILABILITY), REPRESENTATIVENESS_AVAILABILITY,
            attrs, source=cid)}
    return SatelliteSupportResult(spec, cid, tables, tuple(report.issues))


def _subset_rows(spec: SatelliteSupportSpec, metric_spec: MetricSpec, cohort: Cohort,
                 paired: pd.DataFrame, support: pd.DataFrame, models: list[str],
                 report: ValidationReport) -> list[dict[str, Any]]:
    tol = metric_spec.daily_tolerance
    sources = "; ".join(f"{s.source_id}: {s.authority} ({s.kind})" for s in spec.sources)
    out: list[dict[str, Any]] = []
    for (coh, site), g in paired.groupby(["cohort_id", "site_id"], sort=True):
        days = pd.DatetimeIndex(sorted(g["date"].unique()))
        sup = support[(support["site_id"] == site) & support["model"].isin(models)]
        wide = sup.pivot(index="date", columns="model", values="support_status")
        wide = wide.reindex(columns=models)
        on_days = wide.reindex(days)
        known = on_days.notna().all(axis=1)
        clear_all = known & (on_days == "clear_observation").all(axis=1)
        interp_all = known & (on_days == "interpolated").all(axis=1)
        mixed = known & ~clear_all & ~interp_all
        per_model = {m: on_days[m].value_counts().to_dict() for m in models}
        # acquisition timing from days on which every model is clear (whole record)
        rec = wide.dropna()
        joint_clear = pd.DatetimeIndex(rec.index[(rec == "clear_observation").all(axis=1)])
        gaps = acquisition_gaps(days, joint_clear)
        subsets: list[tuple[str, str, np.ndarray, dict[str, Any]]] = [
            ("support_status", "all_cohort_days", np.ones(len(days), bool), {}),
            ("support_status", "clear_observation", clear_all.to_numpy(), {}),
            ("support_status", "interpolated", interp_all.to_numpy(), {}),
        ]
        for var, edges in spec.gap_variables.items():
            b = assign_bins(gaps[var], edges)
            for i in range(len(edges) - 1):
                last = i == len(edges) - 2
                label = f"[{edges[i]:g}, {edges[i + 1]:g}{']' if last else ')'}"
                subsets.append((var, label, b == i, {"bin_index": i, "bin_lower": edges[i],
                                                     "bin_upper": edges[i + 1]}))
            subsets.append((var, "outside_edges", b == -1, {"bin_index": -1}))
            subsets.append((var, "variable_missing", b == -2, {"bin_index": -2}))
        day_pos = {d: k for k, d in enumerate(days)}
        for keys, s in g.groupby(_SERIES, sort=True):
            series = dict(zip(_SERIES, keys, strict=True))
            pos = s["date"].map(day_pos).to_numpy()
            if len(pos) != len(days):
                raise ContractError(f"cohort {coh}: series {series} does not cover the frozen "
                                    "days; the cohort is not frozen")
            o, p = s["observed_mm"].to_numpy(float), s["modeled_mm"].to_numpy(float)
            counts = per_model.get(series["model"], {})
            row_flags = {t for v in s["flags"].dropna() for t in str(v).split(";") if t}
            for var, label, mask, extra in subsets:
                sel = mask[pos]
                n = int(sel.sum())
                remainder = label in ("outside_edges", "variable_missing")
                res = None if remainder or n < spec.min_subset_days else compute_metrics(
                    o[sel], p[sel], tolerance=tol, metrics=spec.metrics)
                for metric in spec.metrics:
                    value: float | None
                    if remainder:
                        status, detail, value = (label, "days accounted for but not binned",
                                                 None)
                    elif n == 0:
                        status, detail, value = "empty_subset", None, None
                    elif res is None:
                        status, detail, value = ("sparse_subset",
                                                 f"{n} days < min_subset_days "
                                                 f"{spec.min_subset_days}", None)
                    else:
                        r = res[metric]
                        value = r.value
                        status = "ok" if r.value is not None else "estimate_undefined"
                        detail = r.reason
                    retro = RETROSPECTIVE.get(var, False)
                    out.append({
                        "satellite_support_id": spec.satellite_support_id,
                        "comparison_id": cohort.comparison_id, "cohort_id": coh,
                        "site_id": site, **series, "variable": var, "subset": label,
                        "metric": metric, "bin_index": extra.get("bin_index"),
                        "bin_lower": extra.get("bin_lower"), "bin_upper": extra.get("bin_upper"),
                        "value": value, "units": _units(metric), "status": status,
                        "status_detail": detail, "n_days": n, "n_days_cohort": len(days),
                        "n_days_mixed_support": int(mixed.sum()),
                        "n_days_unknown_support": int((~known).sum()),
                        "n_days_model_clear": int(counts.get("clear_observation", 0)),
                        "n_days_model_interpolated": int(counts.get("interpolated", 0)),
                        "n_days_model_unknown": int(len(days) - sum(counts.values())),
                        "min_subset_days": spec.min_subset_days, "support_sources": sources,
                        "retrospective": retro,
                        "flags": ";".join(sorted({
                            "days_identical_across_models", *row_flags,
                            *(["retrospective_uses_later_acquisitions"] if retro else [])}))})
        if int((~known).sum()):
            report.info("unknown_support", f"{coh}/{site}: {int((~known).sum())} cohort days "
                        "have no support record for some model; they are in neither subset")
    return out


def _units(metric: str) -> str:
    u = METRIC_DEFINITIONS[metric].units
    return "mm d-1" if u == "value" else u
