"""Anomaly skill: held-out climatologies and exploratory detrending (guide step 14).

:func:`compute_anomalies` subtracts a baseline from the observed and the modeled series of
one :class:`~etcompare.cohorts.Cohort` under an :class:`~etcompare.config.AnomalySpec`, and
scores the anomalies with :func:`etcompare.metrics.compute_metrics`. It writes
``anomaly_daily`` and ``anomaly_metrics``.

The same declared method is fitted **separately** to the observed and the modeled series.
Raw-value metrics are reported on exactly the same days next to the anomaly metrics,
because demeaning removes part of the bias: a model can have good anomaly skill and a
large raw bias.

``held_out_climatology`` (multi-year records)
---------------------------------------------
- Days are grouped into folds by calendar ``year`` or ``water_year`` (starting in
  ``water_year_start_month``, labelled by the year it ends in).
- For each held-out fold, the climatology is fitted on the **other folds only**: for a
  target day, the mean of the training values whose day of year lies within
  ``climatology_half_window_days`` (circular distance on a 365-day calendar). No value of
  the held-out fold enters its own climatology (tested).
- **Leap day** (``leap_day: as_feb28``): 29 February takes 28 February's day of year.
- **Sparse seasons:** a day's climatology needs ``min_training_values`` values from at
  least ``min_training_years`` distinct training folds. Otherwise the day is
  ``baseline_unavailable`` and has no anomaly; nothing is filled or extrapolated.
- With fewer than ``min_training_years + 1`` folds no fold can be held out, and every row is
  ``insufficient_training_years``.
- Metrics are given per held-out fold and pooled over all held-out days (``all_folds``).

``exploratory_moving_mean`` (single-year records)
-------------------------------------------------
The baseline is a centred moving mean of ``window_days`` calendar days of the series
itself, needing ``min_window_completeness`` of the window to be cohort days. It is fitted
in sample, so its rows are ``inference = exploratory`` and flagged
``exploratory_in_sample_detrending`` and ``no_seasonal_generalization``: they describe how
well short-term departures agree, never how a model generalises to another season or year.
Any smoothing used for validation would have to be fitted inside training folds, which
this method does not do.

Other rules
-----------
- Only cohort days carry values; gaps stay gaps, and a frozen cohort gives every model the
  same days. Cohorts that do not freeze days are flagged ``days_differ_across_models``.
- Gap-filled reference days share gridMET drivers with OpenET (D-005); samples containing
  them are flagged ``includes_gap_filled_reference_days`` and ``shared_gridmet_drivers``.
- A sample with fewer than ``min_days`` days is ``sparse_sample`` (no value).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

import numpy as np
import pandas as pd

from etcompare.cohorts import Cohort
from etcompare.config import AnomalySpec, MetricSpec, to_plain
from etcompare.errors import ContractError
from etcompare.metrics import METRIC_DEFINITIONS, compute_metrics
from etcompare.schemas import ANOMALY_DAILY, ANOMALY_METRICS, CanonicalTable, TableSchema
from etcompare.validation import Issue, ValidationReport

_SERIES = ["model", "reference_variant", "extraction_method", "footprint_set_id"]
DAYS_PER_YEAR = 365


@dataclass(frozen=True)
class AnomalyResult:
    """Daily anomalies and their metrics for one cohort. Immutable."""

    spec: AnomalySpec
    comparison_id: str
    _tables: Mapping[str, CanonicalTable] = field(repr=False)
    _issues: tuple[Issue, ...] = field(repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_tables", MappingProxyType(dict(self._tables)))

    def _get(self, name: str) -> CanonicalTable:
        t = self._tables[name]
        return CanonicalTable(t.schema, t.frame.copy(), dict(t.attributes))

    @property
    def daily(self) -> CanonicalTable:
        return self._get("anomaly_daily")

    @property
    def metrics(self) -> CanonicalTable:
        return self._get("anomaly_metrics")

    @property
    def report(self) -> ValidationReport:
        return ValidationReport(f"anomalies {self.comparison_id}", list(self._issues))


def day_of_year_365(dates: pd.DatetimeIndex | pd.Series) -> np.ndarray:
    """0-based day of year on a 365-day calendar; 29 February takes 28 February's day."""
    d = pd.DatetimeIndex(dates)
    day = np.where((d.month == 2) & (d.day == 29), 28, d.day)
    ref = pd.to_datetime({"year": np.full(len(d), 2001), "month": d.month, "day": day})
    return np.asarray(pd.DatetimeIndex(ref).dayofyear, dtype=np.int64) - 1


def fold_labels(dates: pd.DatetimeIndex | pd.Series, unit: str,
                water_year_start_month: int = 10) -> np.ndarray:
    """Calendar year, or water year labelled by the year it ends in."""
    d = pd.DatetimeIndex(dates)
    years = np.asarray(d.year, dtype=np.int64)
    if unit == "year":
        return years
    if unit == "water_year":
        m = water_year_start_month
        return years + ((np.asarray(d.month) >= m) & (m > 1)).astype(np.int64)
    raise ValueError(f"unknown fold unit {unit!r}")


def climatology(train_doy: np.ndarray, train_values: np.ndarray, train_folds: np.ndarray,
                target_doy: np.ndarray, *, half_window: int, min_values: int, min_years: int
                ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Windowed day-of-year mean of the training values at each target day.

    Returns the climatology (NaN where the sparse-season rule fails), the number of
    training values and the number of distinct training folds behind each target.
    """
    dist = np.abs(target_doy[:, None] - train_doy[None, :])
    dist = np.minimum(dist, DAYS_PER_YEAR - dist)
    inside = dist <= half_window
    n = inside.sum(axis=1)
    years = np.array([np.unique(train_folds[row]).size for row in inside], dtype=np.int64)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = (inside * train_values[None, :]).sum(axis=1) / n
    ok = (n >= min_values) & (years >= min_years)
    return np.where(ok, mean, np.nan), n, years


def moving_mean(dates: pd.DatetimeIndex, values: np.ndarray, *, window: int,
                completeness: float) -> tuple[np.ndarray, np.ndarray]:
    """Centred calendar-day moving mean at ``dates`` and the number of values behind it."""
    if not len(dates):
        return np.array([], float), np.array([], np.int64)
    s = pd.Series(values, index=pd.DatetimeIndex(dates))
    full = s.reindex(pd.date_range(s.index.min(), s.index.max(), freq="D"))
    need = max(1, math.ceil(completeness * window - 1e-9))
    mean = full.rolling(window, center=True, min_periods=need).mean().reindex(s.index)
    count = full.rolling(window, center=True, min_periods=1).count().reindex(s.index)
    return mean.to_numpy(float), count.to_numpy().astype(np.int64)


def _frame(rows: list[dict[str, Any]], schema: TableSchema) -> pd.DataFrame:
    frame = pd.DataFrame(rows, columns=schema.names) if rows else schema.empty()
    for c in schema.columns:
        if c.kind in ("float", "int"):
            frame[c.name] = pd.to_numeric(frame[c.name])
    return frame.sort_values(list(schema.primary_key), ignore_index=True)


def _units(metric: str) -> str:
    u = METRIC_DEFINITIONS[metric].units
    return "mm d-1" if u == "value" else u


def compute_anomalies(cohort: Cohort, spec: AnomalySpec, metric_spec: MetricSpec
                      ) -> AnomalyResult:
    """Anomalies and anomaly/raw metrics of ``cohort`` (module docstring)."""
    cid = cohort.comparison_id
    report = ValidationReport(f"anomalies {cid}/{spec.anomaly_id}")
    if metric_spec.metric_set_id != spec.metric_set:
        raise ContractError(f"anomalies {spec.anomaly_id} use metric set {spec.metric_set!r}, "
                            f"not {metric_spec.metric_set_id!r}")
    held_out = spec.method == "held_out_climatology"
    paired = cohort.paired.frame.sort_values(["cohort_id", "site_id", *_SERIES, "date"],
                                             ignore_index=True)
    daily: list[dict[str, Any]] = []
    metrics: list[dict[str, Any]] = []
    base_flags = ({"held_out_climatology", "fitted_separately_observed_modeled",
                   "raw_bias_reported_alongside"} if held_out else
                  {"exploratory_in_sample_detrending", "no_seasonal_generalization",
                   "fitted_separately_observed_modeled", "raw_bias_reported_alongside"})
    if not cohort.spec.freeze_models:
        base_flags.add("days_differ_across_models")
    for keys, s in paired.groupby(["cohort_id", "site_id", *_SERIES], sort=True):
        coh, site, *series = keys
        ids = {"anomaly_id": spec.anomaly_id, "comparison_id": cid, "cohort_id": coh,
               "site_id": site, **dict(zip(_SERIES, series, strict=True))}
        dates = pd.DatetimeIndex(s["date"])
        o, p = s["observed_mm"].to_numpy(float), s["modeled_mm"].to_numpy(float)
        prov = s["reference_provenance"].to_numpy()
        row_flags = {t for v in s["flags"].dropna() for t in str(v).split(";") if t}
        n_years = np.full(len(s), np.nan)
        unique: list[int] = []
        if held_out:
            folds = fold_labels(dates, spec.fold_unit, spec.water_year_start_month)  # type: ignore[arg-type]
            labels = np.array([str(f) for f in folds], dtype=object)
            unique = sorted(set(folds.tolist()))
            ob, mb = np.full(len(s), np.nan), np.full(len(s), np.nan)
            n_vals = np.zeros(len(s), np.int64)
            enough = len(unique) >= spec.min_training_years + 1  # type: ignore[operator]
            if not enough:
                report.info("insufficient_training_years",
                            f"{coh}/{site}/{series[0]}: {len(unique)} {spec.fold_unit} fold(s) "
                            f"{unique}; holding one out needs {spec.min_training_years} other(s)")
            else:
                doy = day_of_year_365(dates)
                for f in unique:
                    test, train = folds == f, folds != f
                    kw = {"half_window": spec.climatology_half_window_days,
                          "min_values": spec.min_training_values,
                          "min_years": spec.min_training_years}
                    ob[test], n_vals[test], yrs = climatology(doy[train], o[train], folds[train],
                                                              doy[test], **kw)  # type: ignore[arg-type]
                    mb[test], _, _ = climatology(doy[train], p[train], folds[train], doy[test],
                                                 **kw)  # type: ignore[arg-type]
                    n_years[test] = yrs
            samples = [(str(f), folds == f, ";".join(str(x) for x in unique if x != f))
                       for f in unique] + [("all_folds", np.ones(len(s), bool), None)]
        else:
            labels = np.full(len(s), "in_sample", dtype=object)
            ob, n_vals = moving_mean(dates, o, window=spec.window_days,  # type: ignore[arg-type]
                                     completeness=spec.min_window_completeness)  # type: ignore[arg-type]
            mb, _ = moving_mean(dates, p, window=spec.window_days,  # type: ignore[arg-type]
                                completeness=spec.min_window_completeness)  # type: ignore[arg-type]
            enough = True
            samples = [("in_sample", np.ones(len(s), bool), None)]
        ok = np.isfinite(ob) & np.isfinite(mb)
        oa, pa = o - ob, p - mb
        for i, d in enumerate(dates):
            daily.append({**ids, "date": d, "method": spec.method, "fold": labels[i],
                          "observed_mm": o[i], "modeled_mm": p[i],
                          "observed_baseline_mm": ob[i] if ok[i] else None,
                          "modeled_baseline_mm": mb[i] if ok[i] else None,
                          "observed_anomaly_mm": oa[i] if ok[i] else None,
                          "modeled_anomaly_mm": pa[i] if ok[i] else None,
                          "status": "ok" if ok[i] else "baseline_unavailable",
                          "n_training_values": int(n_vals[i]),
                          "n_training_years": None if np.isnan(n_years[i]) else int(n_years[i]),
                          "reference_provenance": prov[i]})
        for fold, mask, training in samples:
            sel = mask & ok
            n = int(sel.sum())
            n_filled = int((prov[sel] == "corrected_filled").sum())
            flags = set(base_flags) | row_flags
            if n_filled:
                flags |= {"includes_gap_filled_reference_days", "shared_gridmet_drivers"}
            for kind, x, y in (("raw", o, p), ("anomaly", oa, pa)):
                res = (compute_metrics(x[sel], y[sel], tolerance=metric_spec.daily_tolerance,
                                       metrics=spec.metrics)
                       if enough and n >= spec.min_days else None)
                for metric in spec.metrics:
                    value: float | None = None
                    detail = None
                    if not enough:
                        status = "insufficient_training_years"
                        detail = (f"{spec.fold_unit} folds present: {len(unique)}; "
                                  f"min_training_years {spec.min_training_years}")
                    elif n == 0:
                        status = "empty_sample"
                    elif res is None:
                        status, detail = "sparse_sample", f"{n} days < min_days {spec.min_days}"
                    else:
                        value, detail = res[metric].value, res[metric].reason
                        status = "ok" if value is not None else "estimate_undefined"
                    metrics.append({**ids, "fold": fold, "series_kind": kind, "metric": metric,
                                    "method": spec.method,
                                    "inference": "held_out" if held_out else "exploratory",
                                    "value": value, "units": _units(metric), "status": status,
                                    "status_detail": detail, "n_days": n,
                                    "n_days_cohort": len(s), "n_days_reference_filled": n_filled,
                                    "training_folds": training,
                                    "flags": ";".join(sorted(flags))})
    attrs = {"workflow": "etcompare.anomalies.compute_anomalies", "anomaly": to_plain(spec),
             "comparison_id": cid, "cohort_membership_sha256": cohort.fingerprint,
             "error_convention": "anomaly = value - baseline fitted to the same series"}
    tables = {"anomaly_daily": CanonicalTable.build(_frame(daily, ANOMALY_DAILY), ANOMALY_DAILY,
                                                    attrs, source=cid),
              "anomaly_metrics": CanonicalTable.build(_frame(metrics, ANOMALY_METRICS),
                                                      ANOMALY_METRICS, attrs, source=cid)}
    return AnomalyResult(spec, cid, tables, tuple(report.issues))
