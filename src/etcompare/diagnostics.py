"""Closure, coverage and environmental diagnostics (guide step 12).

:func:`diagnose_comparison` describes one frozen :class:`~etcompare.cohorts.Cohort`
under a :class:`~etcompare.config.DiagnosticsSpec`. It never recomputes the
energy-balance correction, never chooses a bin from the errors and never pools
unequal cohorts. It returns six tables:

- ``closure_magnitude``: size of the existing correction;
- ``reference_provenance_periods``: observation-dominated and gap-filled days;
- ``conditional_errors``: metrics of ``e = P - O`` inside declared bins;
- ``error_regressions``: error against net radiation, corrected and raw;
- ``diagnostic_availability``: what ran, what was skipped and why.

:func:`reference_rank_changes` labels the step-11 rank changes between
reference variants (``reference_rank_changes`` table).

Closure and reference sensitivity
---------------------------------
The correction magnitude uses the station's own columns only, on
``corrected_from_raw`` days: ``correction_mm = ET_corr - ET``,
``correction_ratio = ET_corr / ET`` (days with ``ET = 0`` are counted as
missing), the closure factor (``ebc_cf``) and declared closure metadata (e.g.
``ebr``, ``ebr_corr``, ``ebr_5day_clim``). Gap-filled days are excluded and
counted, because their ``ET_corr`` is ``gridMET_ETr * ETrF_filtered`` and not a
closure adjustment (D-004, D-005). Statistics are given for all days, per
calendar month (``YYYY-MM``) and per meteorological season (``YYYY-DJF`` with
December counted in the following year), for the station record and for the
cohort's days. The difference between corrected and raw ET is **reference
sensitivity**: it is not a calibrated uncertainty envelope that contains the
true ET. Rank changes between reference variants come from
:func:`etcompare.ranking.ranking_sensitivity`; nothing is re-ranked.

Gap-filled versus observation-dominated days
--------------------------------------------
Inside one frozen cohort, metrics on all accepted days are compared with the
``corrected_from_raw`` (observation-dominated; subdaily gaps are unknown, so
not "fully observed") and the ``corrected_filled`` subsets
(``variable = reference_provenance``). Provenance is a property of the station
day, so every model of a frozen cohort has identical subset days. Gap-filled
reference ET and interpolated OpenET share gridMET drivers: agreement on those
days is never independent validation (flags ``shared_gridmet_drivers`` and
``not_independent_validation``). The gap-filled fraction per period is reported
in ``reference_provenance_periods`` and used as a conditioning variable
(``gap_filled_fraction``: the share of the cohort's days in the day's calendar
month that are gap-filled).

Conditional error summaries
---------------------------
Every conditioning variable is declared in the configuration with its source
column, units, meaning and status. Station meteorology passes through as
``src_<name>`` columns (D-020); footprint support comes from the cohort side's
extraction rows. A variable that is undeclared, ``unverified``, missing from
its table, non-numeric or entirely null on the cohort's days gets a
``not_available`` row with a reason; only that diagnostic is skipped.

Bins are fixed before any error is computed (:func:`resolve_bins` sees no
errors): declared edges, quantiles of the variable's own values over a declared
training period (station variables only), month of year, meteorological season,
wind sectors or reference provenance. Interval bins are ``[lower, upper)``; the
last is ``[lower, upper]``. Days outside the edges, days without a value and
(for ``measured_days_only`` variables) gap-filled days are reported as
remainder rows with negative ``bin_index``, so every day is accounted for.

For each model, reference variant and side, a bin's paired sample goes through
:func:`etcompare.metrics.compute_metrics` unchanged. A bin with fewer than
``min_bin_days`` days has status ``sparse_bin`` (``empty_bin`` without days) and
no value. ``n_days`` (this model), ``n_days_bin_cohort`` (any model) and
``bin_days_shared`` are explicit: a variable taken from extraction rows (e.g.
the retained footprint fraction) can put different days of different models in
one bin, and such bins do not compare the models on identical days.

Optional intervals reuse the step-10 scheme (:mod:`etcompare.bootstrap`): the
bin's days are split into runs of consecutive calendar days (and at the
season-year boundary), moving blocks are drawn inside the runs with shared
indices for every series that has the same bin days, and metrics are
recomputed with ``compute_metrics``. Every declared block length is reported
side by side; bins with fewer than ``min_blocks`` disjoint blocks get
``insufficient_support``. No block length is preferred (D-048).

Conditional summaries describe associations between error and a condition in
one station's record. They are not causal explanations: conditions co-vary
with season, crop stage and each other.

Process diagnostics
-------------------
- **Sensible heat.** Only daily-mean H exists. Its negative bin is a daily-mean
  indicator; it is neither a daytime negative-H frequency nor proof of
  advection (``daytime_negative_h_frequency`` is ``not_available``).
- **Wind direction.** :func:`wind_sector` assigns degrees clockwise from north
  to ``n`` equal sectors, sector 0 centred on north. Sector ``k`` covers
  ``[k*w - w/2, k*w + w/2)`` with ``w = 360/n``, wrapping at 0/360, so 359 and 1
  degree share the north sector and 360 equals 0. Values outside [0, 360] raise.
  Without a declared direction variable the analysis is ``not_available``.
- **Net radiation.** ``error_regressions`` gives the OLS slope of ``e`` on net
  radiation. The corrected reference scales measured ET by the energy-balance
  closure, which itself depends on net radiation, so with a corrected reference
  error and predictor are mathematically coupled. The uncorrected-reference
  version is given alongside where the cohort has ``et_raw``.
- **Satellite support.** No acquisition flags exist (D-011); clear-versus-
  interpolated analyses are ``not_available``. Cloud cover is never inferred
  from missing rows or scene dates.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

import numpy as np
import pandas as pd

from etcompare.bootstrap import (
    PairedSample,
    _replicates,
    block_support,
    sample_rng,
    segment_bounds,
)
from etcompare.cohorts import Cohort
from etcompare.config import (
    ET_UNIT,
    BootstrapSpec,
    DiagnosticsSpec,
    DiagnosticVariableSpec,
    MetricSpec,
    to_plain,
)
from etcompare.errors import ContractError
from etcompare.metrics import METRIC_DEFINITIONS, compute_metrics
from etcompare.schemas import (
    CLOSURE_MAGNITUDE,
    CONDITIONAL_ERRORS,
    DIAGNOSTIC_AVAILABILITY,
    ERROR_REGRESSIONS,
    EXTRACTIONS,
    RANKING_SENSITIVITY,
    REFERENCE_PROVENANCE_PERIODS,
    REFERENCE_RANK_CHANGES,
    STATION_DAILY,
    CanonicalTable,
    TableSchema,
    check_primary_key,
    conform,
)
from etcompare.validation import Issue, ValidationReport

_SIDE = ["extraction_method", "footprint_set_id"]
_SERIES = ["model", "reference_variant", *_SIDE]
_GROUP = ["cohort_id", "site_id", *_SERIES]
SEASONS = ("DJF", "MAM", "JJA", "SON")
_SEASON_OF_MONTH = {12: 0, 1: 0, 2: 0, 3: 1, 4: 1, 5: 1, 6: 2, 7: 2, 8: 2, 9: 3, 10: 3, 11: 3}
_REMAINDER = {-1: ("outside_edges", "outside_edges"), -2: ("variable_missing", "variable_missing"),
              -3: ("excluded_provenance", "excluded_provenance")}
_PROVENANCE_BINS = ("all_accepted", "corrected_from_raw", "corrected_filled")
INTERPRETATION = ("conditional association within one station's record; not a causal "
                  "explanation")
REFERENCE_SENSITIVITY = ("corrected/raw differences are reference sensitivity, not a calibrated "
                         "uncertainty envelope containing the truth")


# --------------------------------------------------------------------------- building blocks

def season_index(dates: pd.DatetimeIndex | pd.Series) -> np.ndarray:
    """Meteorological season of each date: 0 DJF, 1 MAM, 2 JJA, 3 SON."""
    months = pd.DatetimeIndex(dates).month
    return np.array([_SEASON_OF_MONTH[int(m)] for m in months], dtype=np.int64)


def season_labels(dates: pd.DatetimeIndex | pd.Series) -> np.ndarray:
    """``YYYY-SSS`` season instance; December belongs to the following year's DJF."""
    d = pd.DatetimeIndex(dates)
    years = np.asarray(d.year) + (np.asarray(d.month) == 12)
    return np.array([f"{y}-{SEASONS[s]}" for y, s in zip(years, season_index(d), strict=True)],
                    dtype=object)


def month_labels(dates: pd.DatetimeIndex | pd.Series) -> np.ndarray:
    return np.asarray(pd.DatetimeIndex(dates).strftime("%Y-%m"), dtype=object)


def wind_sector(directions: Sequence[float] | np.ndarray, n_sectors: int) -> np.ndarray:
    """Sector index of each direction (degrees clockwise from north); -1 for NaN.

    Sector ``k`` covers ``[k*w - w/2, k*w + w/2)``, ``w = 360 / n_sectors``; sector
    0 is centred on north and wraps at 0/360 (360 is north). Values outside
    [0, 360] raise :class:`ValueError` rather than being wrapped silently.
    """
    if n_sectors < 2 or 360 % n_sectors:
        raise ValueError("n_sectors must be >= 2 and divide 360")
    d = np.asarray(directions, dtype=float)
    finite = np.isfinite(d)
    if ((d[finite] < 0) | (d[finite] > 360)).any():
        raise ValueError("wind directions must lie in [0, 360] degrees")
    w = 360.0 / n_sectors
    out = np.full(d.shape, -1, dtype=np.int64)
    out[finite] = (np.floor(((d[finite] + w / 2.0) % 360.0) / w).astype(np.int64)) % n_sectors
    return out


def sector_bounds(n_sectors: int) -> list[tuple[float, float]]:
    """``(lower, upper)`` of each sector; sector 0's lower bound is ``360 - w/2`` (it wraps)."""
    w = 360.0 / n_sectors
    return [((k * w - w / 2.0) % 360.0, k * w + w / 2.0) for k in range(n_sectors)]


def assign_bins(values: Sequence[float] | np.ndarray, edges: Sequence[float]) -> np.ndarray:
    """Bin index of each value: ``[e_i, e_(i+1))``, the last bin closed; -1 outside; -2 NaN."""
    v = np.asarray(values, dtype=float)
    e = np.asarray(edges, dtype=float)
    k = len(e) - 1
    idx = np.searchsorted(e, v, side="right") - 1
    idx = np.where(v == e[-1], k - 1, idx)
    idx = np.where((v < e[0]) | (v > e[-1]), -1, idx)
    return np.where(np.isnan(v), -2, idx).astype(np.int64)


def _fmt(x: float) -> str:
    return "-inf" if x == -math.inf else "inf" if x == math.inf else f"{x:g}"


@dataclass(frozen=True)
class BinDefinition:
    """Bins of one conditioning variable, fixed before any error is computed."""

    variable: str
    source: str  # fixed_edges, training_quantiles, calendar, sectors, provenance
    kind: str  # interval, category, sector
    labels: tuple[str, ...]
    lower: tuple[float | None, ...]
    upper: tuple[float | None, ...]
    closed: tuple[str | None, ...]
    edges: tuple[float, ...] | None = None
    n_sectors: int | None = None

    @classmethod
    def from_edges(cls, variable: str, edges: Sequence[float], source: str) -> BinDefinition:
        e = tuple(float(x) for x in edges)
        k = len(e) - 1
        labels = tuple(f"[{_fmt(e[i])}, {_fmt(e[i + 1])}{']' if i == k - 1 else ')'}"
                       for i in range(k))
        return cls(variable, source, "interval", labels, e[:-1], e[1:],
                   tuple("both" if i == k - 1 else "left" for i in range(k)), edges=e)

    @classmethod
    def categories(cls, variable: str, labels: Sequence[str], source: str) -> BinDefinition:
        n = len(labels)
        return cls(variable, source, "category", tuple(labels), (None,) * n, (None,) * n,
                   (None,) * n)

    @classmethod
    def sectors(cls, variable: str, n_sectors: int) -> BinDefinition:
        b = sector_bounds(n_sectors)
        labels = tuple(f"sector {k} [{_fmt(lo)}, {_fmt(hi)})" for k, (lo, hi) in enumerate(b))
        return cls(variable, "sectors", "sector", labels, tuple(lo for lo, _ in b),
                   tuple(hi for _, hi in b), ("left",) * n_sectors, n_sectors=n_sectors)

    def assign(self, values: np.ndarray) -> np.ndarray:
        if self.kind == "sector":
            assert self.n_sectors is not None
            s = wind_sector(values, self.n_sectors)
            return np.where(s < 0, -2, s)
        assert self.edges is not None
        return assign_bins(values, self.edges)


def training_quantile_edges(var: DiagnosticVariableSpec,
                            station_daily: CanonicalTable | pd.DataFrame) -> tuple[float, ...]:
    """Edges at ``var.quantiles`` of the variable's own values over its training period.

    Only the variable column and dates are read; errors play no part.
    """
    frame = station_daily.frame if isinstance(station_daily, CanonicalTable) else station_daily
    if var.column not in frame.columns:
        raise ContractError(f"diagnostics variable {var.name}: column {var.column!r} not found")
    d = frame["date"]
    inside = (d >= pd.Timestamp(var.training_start)) & (d <= pd.Timestamp(var.training_end))
    values = pd.to_numeric(frame.loc[inside, var.column]).dropna().to_numpy(float)
    if not len(values):
        raise ContractError(f"diagnostics variable {var.name}: no values in the training period "
                            f"{var.training_start}..{var.training_end}")
    assert var.quantiles is not None
    edges = np.quantile(values, list(var.quantiles))
    if (np.diff(edges) <= 0).any():
        raise ContractError(f"diagnostics variable {var.name}: training quantiles {edges.tolist()} "
                            "are not strictly increasing; declare fixed edges")
    return tuple(float(x) for x in edges)


def resolve_bins(spec: DiagnosticsSpec, station_daily: CanonicalTable | pd.DataFrame
                 ) -> dict[str, BinDefinition]:
    """Bins of every usable binned variable, from the configuration and variable values only.

    No errors, modeled values or observations of ET enter this function, so bins
    cannot depend on agreement. Unverified variables and those whose column is
    absent are left out (their diagnostics are reported ``not_available``).
    """
    frame = station_daily.frame if isinstance(station_daily, CanonicalTable) else station_daily
    out: dict[str, BinDefinition] = {}
    if "season" in spec.calendar:
        out["season"] = BinDefinition.categories("season", SEASONS, "calendar")
    if "month" in spec.calendar:
        out["month"] = BinDefinition.categories("month", [f"{m:02d}" for m in range(1, 13)],
                                                "calendar")
    if spec.gap_filled_fraction_edges is not None:
        out["gap_filled_fraction"] = BinDefinition.from_edges(
            "gap_filled_fraction", spec.gap_filled_fraction_edges, "fixed_edges")
    if spec.provenance_subsets:
        out["reference_provenance"] = BinDefinition.categories(
            "reference_provenance", _PROVENANCE_BINS, "provenance")
    for name, v in spec.variables.items():
        if not v.binned or v.status == "unverified":
            continue
        if v.kind == "direction":
            assert v.n_sectors is not None
            out[name] = BinDefinition.sectors(name, v.n_sectors)
        elif v.edges is not None:
            out[name] = BinDefinition.from_edges(name, v.edges, "fixed_edges")
        elif v.source == "station" and v.column in frame.columns:
            out[name] = BinDefinition.from_edges(name, training_quantile_edges(v, frame),
                                                 "training_quantiles")
    return out


def _stats(x: np.ndarray) -> dict[str, float | None]:
    if not len(x):
        return {k: None for k in ("mean", "std", "min", "q05", "q25", "median", "q75", "q95",
                                  "max")}
    q = np.quantile(x, [0.05, 0.25, 0.5, 0.75, 0.95])
    return {"mean": float(x.mean()), "std": float(x.std()), "min": float(x.min()),
            "q05": float(q[0]), "q25": float(q[1]), "median": float(q[2]), "q75": float(q[3]),
            "q95": float(q[4]), "max": float(x.max())}


def _union_flags(values: Iterable[object]) -> str | None:
    tokens = sorted({t for v in values if isinstance(v, str) for t in v.split(";") if t})
    return ";".join(tokens) or None


def _frame(rows: list[dict[str, Any]], schema: TableSchema) -> pd.DataFrame:
    frame = pd.DataFrame(rows, columns=schema.names) if rows else schema.empty()
    for c in schema.columns:
        if c.kind in ("float", "int"):
            frame[c.name] = pd.to_numeric(frame[c.name])
    return frame.sort_values(list(schema.primary_key), ignore_index=True)


def _periods(dates: pd.Series) -> list[tuple[str, np.ndarray]]:
    """(period_type, label per day) for all / month / season."""
    return [("all", np.full(len(dates), "all", dtype=object)), ("month", month_labels(dates)),
            ("season", season_labels(dates))]


# --------------------------------------------------------------------------- results

@dataclass(frozen=True)
class DiagnosticsResult:
    """The step-12 tables of one cohort. Immutable; accessors return copies."""

    spec: DiagnosticsSpec
    comparison_id: str
    _tables: Mapping[str, CanonicalTable] = field(repr=False)
    _bins: Mapping[str, BinDefinition] = field(repr=False)
    _issues: tuple[Issue, ...] = field(repr=False)
    attributes: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "attributes", MappingProxyType(dict(self.attributes)))
        object.__setattr__(self, "_tables", MappingProxyType(dict(self._tables)))
        object.__setattr__(self, "_bins", MappingProxyType(dict(self._bins)))

    def _get(self, name: str) -> CanonicalTable:
        t = self._tables[name]
        return CanonicalTable(t.schema, t.frame.copy(), dict(t.attributes))

    @property
    def closure(self) -> CanonicalTable:
        return self._get("closure_magnitude")

    @property
    def provenance(self) -> CanonicalTable:
        return self._get("reference_provenance_periods")

    @property
    def conditional(self) -> CanonicalTable:
        return self._get("conditional_errors")

    @property
    def regressions(self) -> CanonicalTable:
        return self._get("error_regressions")

    @property
    def availability(self) -> CanonicalTable:
        return self._get("diagnostic_availability")

    @property
    def bins(self) -> Mapping[str, BinDefinition]:
        return self._bins

    @property
    def report(self) -> ValidationReport:
        return ValidationReport(f"diagnostics {self.comparison_id}/{self.spec.diagnostics_id}",
                                list(self._issues))

    def tables(self) -> dict[str, CanonicalTable]:
        return {k: self._get(k) for k in self._tables}

    def summary(self) -> pd.DataFrame:
        """Conditional rows per variable and status (point rows only)."""
        f = self._tables["conditional_errors"].frame
        f = f[f["block_length"].isin([0, f["block_length"].min()])]
        f = f.drop_duplicates([c for c in CONDITIONAL_ERRORS.primary_key
                               if c not in ("interval_method", "block_length")])
        return f.groupby(["variable", "status"]).size().rename("n_rows").reset_index()


# --------------------------------------------------------------------------- main

def _concat(x: CanonicalTable | pd.DataFrame | Sequence[CanonicalTable | pd.DataFrame],
            schema: TableSchema) -> pd.DataFrame:
    parts = x if isinstance(x, Sequence) else [x]
    frames = [p.frame if isinstance(p, CanonicalTable) else conform(p, schema) for p in parts]
    return pd.concat(frames, ignore_index=True) if frames else schema.empty()


def diagnose_comparison(
    cohort: Cohort,
    spec: DiagnosticsSpec,
    metric_spec: MetricSpec,
    *,
    station_daily: CanonicalTable | pd.DataFrame | Sequence[CanonicalTable | pd.DataFrame],
    extractions: CanonicalTable | pd.DataFrame | Sequence[CanonicalTable | pd.DataFrame] = (),
    bootstrap: BootstrapSpec | None = None,
) -> DiagnosticsResult:
    """Closure, provenance, conditional-error and process diagnostics of ``cohort``.

    ``station_daily`` supplies the reference columns, closure factor and
    ``src_*`` meteorology; ``extractions`` the footprint support of the cohort's
    sides. ``bootstrap`` must be the one named by ``spec.bootstrap`` (or None
    when it names none). See the module docstring for every rule.
    """
    cid = cohort.comparison_id
    did = spec.diagnostics_id
    report = ValidationReport(f"diagnostics {cid}/{did}")
    if metric_spec.metric_set_id != spec.metric_set:
        raise ContractError(f"diagnostics {did} uses metric set {spec.metric_set!r}, not "
                            f"{metric_spec.metric_set_id!r}")
    missing = set(spec.metrics) - set(metric_spec.metrics)
    if missing:
        raise ContractError(f"diagnostics {did}: metrics {sorted(missing)} not in metric set "
                            f"{metric_spec.metric_set_id}")
    if (spec.bootstrap is None) != (bootstrap is None) or (
            bootstrap is not None and bootstrap.bootstrap_id != spec.bootstrap):
        raise ContractError(f"diagnostics {did} declares bootstrap {spec.bootstrap!r}; supply "
                            "exactly that bootstrap spec (or none)")
    if bootstrap is not None and bootstrap.metric_set != spec.metric_set:
        raise ContractError(f"bootstrap {bootstrap.bootstrap_id} resamples metric set "
                            f"{bootstrap.metric_set}, not {spec.metric_set}")
    if spec.comparisons is not None and cid not in spec.comparisons:
        raise ContractError(f"diagnostics {did} does not list comparison {cid}")

    paired = cohort.paired.frame
    sites = sorted(paired["site_id"].unique()) if len(paired) else list(cohort.spec.sites or ())
    obs = _concat(station_daily, STATION_DAILY)
    obs = obs[obs["site_id"].isin(sites or obs["site_id"].unique())]
    if cohort.spec.start:
        obs = obs[obs["date"] >= pd.Timestamp(cohort.spec.start)]
    if cohort.spec.end:
        obs = obs[obs["date"] <= pd.Timestamp(cohort.spec.end)]
    obs = obs.reset_index(drop=True)
    check_primary_key(obs, STATION_DAILY.primary_key, table=f"station_daily ({cid})")

    # Bins first: fixed from the configuration and variable values, before any error exists.
    bins = resolve_bins(spec, obs)

    paired = paired.sort_values([*_GROUP, "date"], ignore_index=True)
    paired["error_mm"] = paired["modeled_mm"] - paired["observed_mm"]
    avail: list[dict[str, Any]] = []
    base = {"diagnostics_id": did, "comparison_id": cid}

    def unavailable(analysis: str, variable: str, code: str, reason: str, *,
                    var: DiagnosticVariableSpec | None = None, cohort_id: str = "all",
                    variant: str = "all", side: tuple[str, str] = ("all", "all"),
                    n_cohort: int | None = None, n_value: int | None = None) -> None:
        avail.append({**base, "cohort_id": cohort_id, "analysis": analysis, "variable": variable,
                      "reference_variant": variant, "extraction_method": side[0],
                      "footprint_set_id": side[1], "status": "not_available",
                      "reason_code": code, "reason": reason,
                      **_var_fields(var), "n_days_cohort": n_cohort,
                      "n_days_with_value": n_value, "flags": None})

    def available(analysis: str, variable: str, reason: str | None = None, *,
                  var: DiagnosticVariableSpec | None = None, cohort_id: str = "all",
                  variant: str = "all", side: tuple[str, str] = ("all", "all"),
                  n_cohort: int | None = None, n_value: int | None = None,
                  flags: str | None = None) -> None:
        avail.append({**base, "cohort_id": cohort_id, "analysis": analysis, "variable": variable,
                      "reference_variant": variant, "extraction_method": side[0],
                      "footprint_set_id": side[1], "status": "available", "reason_code": None,
                      "reason": reason, **_var_fields(var), "n_days_cohort": n_cohort,
                      "n_days_with_value": n_value, "flags": flags})

    # ---- variable values per paired row (station or extraction); None when unusable
    ext = _concat(extractions, EXTRACTIONS) if (
        isinstance(extractions, CanonicalTable | pd.DataFrame) or len(extractions)) else None
    values: dict[str, np.ndarray] = {}
    n_cohort_days = paired[["cohort_id", "site_id", "date"]].drop_duplicates().shape[0]
    for name, v in spec.variables.items():
        got = _variable_values(v, obs, ext, paired, cohort, report)
        if isinstance(got, tuple):
            code, reason = got
            for analysis in _analyses_of(spec, name):
                unavailable(analysis, name, code, reason, var=v, n_cohort=n_cohort_days)
            continue
        values[name] = got

    closure_rows = _closure(spec, cohort, obs, paired, values, base, report, unavailable,
                            available) if spec.closure else []
    prov_rows, month_fraction = _provenance(paired, base)
    if spec.closure is False:
        unavailable("closure_magnitude", "none", "not_requested", "closure: false in the "
                    "diagnostics declaration")

    # ---- conditional errors
    cond_rows: list[dict[str, Any]] = []
    values["season"] = season_index(paired["date"]) if len(paired) else np.empty(0)
    values["month"] = (pd.DatetimeIndex(paired["date"]).month.to_numpy() - 1).astype(float) if len(
        paired) else np.empty(0)
    if "gap_filled_fraction" in bins:
        values["gap_filled_fraction"] = month_fraction
    for name, b in bins.items():
        var = spec.variables.get(name)
        if name not in values and name != "reference_provenance":
            continue  # unusable; reported above
        cond_rows += _conditional(name, b, var, values.get(name), paired, cohort, spec,
                                  metric_spec, bootstrap, base, report, available, unavailable)

    # ---- process diagnostics
    reg_rows = _process(spec, values, paired, cohort, base, available, unavailable)
    unavailable("satellite_support", "acquisition_flags", "no_acquisition_metadata",
                "no per-pixel clear-observation or acquisition flags exist (D-011); "
                "clear-versus-interpolated analyses are not available, and cloud cover is never "
                "inferred from missing rows or scene dates")
    if cohort.status == "empty":
        report.warning("empty_cohort", "the cohort is empty; every conditional bin is empty")

    attrs: dict[str, Any] = {
        "workflow": "etcompare.diagnostics.diagnose_comparison", "diagnostics": to_plain(spec),
        "comparison_id": cid, "cohort_membership_sha256": cohort.fingerprint,
        "cohort_kind": cohort.spec.kind, "metric_set_id": metric_spec.metric_set_id,
        "bootstrap": to_plain(bootstrap) if bootstrap is not None else None,
        "error_convention": "e = modeled - observed", "interpretation": INTERPRETATION,
        "reference_sensitivity": REFERENCE_SENSITIVITY,
        "bins": {k: to_plain(b) for k, b in bins.items()},
    }
    tables = {
        "closure_magnitude": CanonicalTable.build(_frame(closure_rows, CLOSURE_MAGNITUDE),
                                                  CLOSURE_MAGNITUDE, attrs, source=cid),
        "reference_provenance_periods": CanonicalTable.build(
            _frame(prov_rows, REFERENCE_PROVENANCE_PERIODS), REFERENCE_PROVENANCE_PERIODS, attrs,
            source=cid),
        "conditional_errors": CanonicalTable.build(_frame(cond_rows, CONDITIONAL_ERRORS),
                                                   CONDITIONAL_ERRORS, attrs, source=cid),
        "error_regressions": CanonicalTable.build(_frame(reg_rows, ERROR_REGRESSIONS),
                                                  ERROR_REGRESSIONS, attrs, source=cid),
        "diagnostic_availability": CanonicalTable.build(
            _frame(avail, DIAGNOSTIC_AVAILABILITY), DIAGNOSTIC_AVAILABILITY, attrs, source=cid),
    }
    a = tables["diagnostic_availability"].frame
    for r in a[a["status"] == "not_available"].itertuples():
        report.info("not_available", f"{r.analysis}/{r.variable}: {r.reason_code}",
                    field=str(r.variable))
    c = tables["conditional_errors"].frame
    for st, n in c.groupby("status").size().items():
        report.info("bin_status", f"{n} conditional rows {st}", field=str(st), n_rows=int(n))
    return DiagnosticsResult(spec, cid, tables, bins, tuple(report.issues), attrs)


def _var_fields(var: DiagnosticVariableSpec | None) -> dict[str, Any]:
    if var is None:
        return {"variable_column": None, "variable_units": None, "variable_status": None}
    return {"variable_column": var.column, "variable_units": var.units,
            "variable_status": var.status}


def _analyses_of(spec: DiagnosticsSpec, name: str) -> list[str]:
    out = []
    v = spec.variables[name]
    if v.binned:
        out.append("conditional_error")
    if name in spec.closure_metadata:
        out.append("closure_metadata")
    if name == spec.net_radiation_variable:
        out.append("net_radiation_coupling")
    return out or ["declared_only"]


def _variable_values(v: DiagnosticVariableSpec, obs: pd.DataFrame, ext: pd.DataFrame | None,
                     paired: pd.DataFrame, cohort: Cohort, report: ValidationReport
                     ) -> np.ndarray | tuple[str, str]:
    """Values aligned with ``paired`` rows, or (reason_code, reason) when unusable."""
    if v.status == "unverified":
        return "variable_unverified", f"declared unverified: {v.note}"
    if v.source == "station":
        if v.column not in obs.columns:
            return "column_missing", f"station column {v.column!r} not found"
        col = obs[["site_id", "date", v.column]]
        frame = paired[["site_id", "date"]].merge(col, on=["site_id", "date"], how="left",
                                                  validate="many_to_one")
    else:
        if ext is None:
            return "extractions_not_supplied", "no extraction rows were supplied"
        if v.column not in ext.columns:
            return "column_missing", f"extraction column {v.column!r} not found"
        keys = ["site_id", "date", "model", *_SIDE]
        e = ext[(ext["period"] == "day") & ext["model"].isin(list(cohort.spec.models))]
        sides = {(s.extraction_method, s.footprint_set_id) for s in cohort.spec.sides}
        e = e[[(m, f) in sides for m, f in zip(e["extraction_method"], e["footprint_set_id"],
                                                strict=True)]]
        check_primary_key(e, keys, table=f"extractions ({cohort.comparison_id})")
        frame = paired[keys].merge(e[[*keys, v.column]], on=keys, how="left",
                                   validate="many_to_one")
    try:
        x = pd.to_numeric(frame[v.column]).to_numpy(dtype=float)
    except (ValueError, TypeError):
        return "non_numeric", f"column {v.column!r} holds non-numeric values"
    if len(x) and not np.isfinite(x).any():
        return "no_values", f"column {v.column!r} has no values on the cohort's days"
    if v.kind == "direction":
        f = x[np.isfinite(x)]
        if ((f < 0) | (f > 360)).any():
            return "out_of_range", (f"column {v.column!r} holds directions outside [0, 360] "
                                    "degrees; they are not wrapped silently")
    if v.status == "provisional":
        report.info("variable_provisional", f"{v.name}: units {v.units!r} and meaning are "
                    "provisional; its rows are flagged variable_provisional", field=v.name)
    return x


def _closure(spec: DiagnosticsSpec, cohort: Cohort, obs: pd.DataFrame, paired: pd.DataFrame,
             values: Mapping[str, np.ndarray], base: Mapping[str, Any], report: ValidationReport,
             unavailable: Any, available: Any) -> list[dict[str, Any]]:
    """Correction magnitude on corrected_from_raw days (never recomputed)."""
    rows: list[dict[str, Any]] = []
    raw, corr = obs["et_raw_mm"].to_numpy(float), obs["et_corrected_mm"].to_numpy(float)
    measured = (obs["reference_provenance"] == "corrected_from_raw").to_numpy()
    filled = (obs["reference_provenance"] == "corrected_filled").to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(raw != 0, corr / raw, np.nan)
    cf = obs["closure_factor"].to_numpy(float) if "closure_factor" in obs else np.full(len(obs),
                                                                                         np.nan)
    quantities: dict[str, tuple[np.ndarray, str, str]] = {
        "correction_mm": (corr - raw, ET_UNIT, "ET_corr - ET (existing columns; not recomputed)"),
        "correction_ratio": (ratio, "1", "ET_corr / ET; days with ET = 0 counted as missing"),
    }
    if np.isfinite(cf[measured]).any():
        quantities["closure_factor"] = (cf, "1", "declared closure factor column (ebc_cf)")
        both = measured & np.isfinite(cf) & np.isfinite(ratio)
        if both.any():
            dev = float(np.max(np.abs(ratio[both] - cf[both])))
            report.info("closure_relation", f"max |ET_corr/ET - closure_factor| = {dev:.3g} on "
                        f"{int(both.sum())} corrected_from_raw days (diagnostic of upstream "
                        "semantics; nothing is recomputed)")
    else:
        unavailable("closure_magnitude", "closure_factor", "no_values",
                    "no closure factor is declared or it has no values on corrected_from_raw days")
    for name in spec.closure_metadata:
        v = spec.variables[name]
        if v.status == "unverified" or v.column not in obs.columns:
            continue  # reported as not_available with the variable
        try:
            quantities[name] = (pd.to_numeric(obs[v.column]).to_numpy(float), v.units, v.meaning)
        except (ValueError, TypeError):
            continue
    if not measured.any():
        unavailable("closure_magnitude", "none", "no_measured_days",
                    "no corrected_from_raw station days in the comparison period")
    else:
        available("closure_magnitude", "none", "corrected_from_raw days only; " +
                  REFERENCE_SENSITIVITY, n_cohort=int(measured.sum()))

    day_sets: list[tuple[str, str, np.ndarray]] = [("none", "station_record",
                                                    np.ones(len(obs), dtype=bool))]
    p = paired[["cohort_id", "site_id", "date"]].drop_duplicates()
    key = obs["site_id"].astype(str) + "|" + obs["date"].dt.strftime("%Y-%m-%d")
    for coh, g in p.groupby("cohort_id"):
        k = set(g["site_id"].astype(str) + "|" + g["date"].dt.strftime("%Y-%m-%d"))
        day_sets.append((str(coh), "cohort", key.isin(k).to_numpy()))
    for coh, day_set, in_set in day_sets:
        for site in sorted(obs["site_id"].unique()):
            at = in_set & (obs["site_id"] == site).to_numpy()
            for ptype, labels in _periods(obs["date"]):
                for label in sorted(set(labels[at])):
                    sel = at & (labels == label)
                    for q, (x, units, definition) in quantities.items():
                        m = sel & measured
                        good = m & np.isfinite(x)
                        st = _stats(x[good])
                        rows.append({**base, "cohort_id": coh, "site_id": site,
                                     "day_set": day_set, "period_type": ptype,
                                     "period_label": label, "quantity": q,
                                     "quantity_units": units, "quantity_definition": definition,
                                     "n_days": int(good.sum()),
                                     "n_days_filled_excluded": int((sel & filled).sum()),
                                     "n_days_value_missing": int((m & ~np.isfinite(x)).sum()),
                                     **st, "status": "ok" if good.any() else "no_days",
                                     "flags": _union_flags([
                                         "reference_sensitivity_not_uncertainty",
                                         "variable_provisional" if q in spec.variables and
                                         spec.variables[q].status == "provisional" else None])})
    return rows


def _provenance(paired: pd.DataFrame, base: Mapping[str, Any]
                ) -> tuple[list[dict[str, Any]], np.ndarray]:
    """Provenance counts per period, and each paired row's month gap-filled fraction."""
    rows: list[dict[str, Any]] = []
    keys = ["cohort_id", "site_id", "reference_variant"]
    days = paired[[*keys, "date", "reference_provenance"]].drop_duplicates([*keys, "date"])
    frac_of: dict[tuple[str, str, str, str], float] = {}
    for k, g in days.groupby(keys, sort=True):
        for ptype, labels in _periods(g["date"]):
            prov = g["reference_provenance"].to_numpy()
            for label in sorted(set(labels)):
                sel = labels == label
                n = int(sel.sum())
                n_raw = int((sel & (prov == "corrected_from_raw")).sum())
                n_fill = int((sel & (prov == "corrected_filled")).sum())
                frac = n_fill / n if n else None
                if ptype == "month":
                    frac_of[(*k, label)] = float(frac) if frac is not None else math.nan
                rows.append({**base, **dict(zip(keys, k, strict=True)), "period_type": ptype,
                             "period_label": label, "n_days": n,
                             "n_days_corrected_from_raw": n_raw,
                             "n_days_corrected_filled": n_fill, "gap_filled_fraction": frac,
                             "flags": "shared_gridmet_drivers" if n_fill else None})
    labels = month_labels(paired["date"]) if len(paired) else np.empty(0, dtype=object)
    fraction = np.array([frac_of[(c, s, v, lab)] for c, s, v, lab in zip(
        paired["cohort_id"], paired["site_id"], paired["reference_variant"], labels,
        strict=True)], dtype=float)
    return rows, fraction


def _interval(reps: np.ndarray, estimate: float, method: str, alpha: float
              ) -> tuple[float, float]:
    q_lo, q_hi = np.quantile(reps, [alpha / 2, 1 - alpha / 2])
    if method == "percentile":
        return float(q_lo), float(q_hi)
    return 2 * estimate - float(q_hi), 2 * estimate - float(q_lo)


def _conditional(name: str, b: BinDefinition, var: DiagnosticVariableSpec | None,
                 x: np.ndarray | None, paired: pd.DataFrame, cohort: Cohort,
                 spec: DiagnosticsSpec, metric_spec: MetricSpec, bootstrap: BootstrapSpec | None,
                 base: Mapping[str, Any], report: ValidationReport, available: Any,
                 unavailable: Any) -> list[dict[str, Any]]:
    """Rows of one conditioning variable for every series of the cohort."""
    prov = paired["reference_provenance"].to_numpy()
    if name == "reference_provenance":
        idx = None  # overlapping categories: handled per bin below
    else:
        assert x is not None
        idx = b.assign(x) if b.kind != "category" else np.where(np.isnan(x), -2,
                                                                 x).astype(np.int64)
        if var is not None and var.measured_days_only:
            idx = np.where(prov == "corrected_from_raw", idx, -3)

    def in_bin(k: int) -> np.ndarray:
        if idx is not None:
            return idx == k
        if k == 0:
            return np.ones(len(paired), dtype=bool)
        return prov == _PROVENANCE_BINS[k]

    status_tag = ("derived" if var is None else var.status)
    var_flags = ["variable_provisional" if var is not None and var.status == "provisional"
                 else None]
    if name == spec.sensible_heat_variable:
        var_flags.append("daily_mean_h_indicator")
    groups = {k: g.index.to_numpy() for k, g in paired.groupby(_GROUP, sort=True)}
    unit_groups = {k: g.index.to_numpy() for k, g in
                   paired.groupby(["cohort_id", "site_id", "reference_variant", *_SIDE],
                                  sort=True)}
    k_bins = list(range(len(b.labels))) + [r for r in (-1, -2, -3) if idx is not None
                                           and (idx == r).any()]
    dates = paired["date"].to_numpy()
    o_all, p_all = paired["observed_mm"].to_numpy(float), paired["modeled_mm"].to_numpy(float)
    flags_all = paired["flags"].to_numpy(dtype=object)

    # Per bin and cohort/site/variant/side: union and sharing of bin days across models.
    shared: dict[tuple[Any, ...], tuple[int, bool]] = {}
    for ukey, rows_idx in unit_groups.items():
        for k in k_bins:
            mask = in_bin(k)[rows_idx]
            sets: dict[str, frozenset[Any]] = {}
            for m in cohort.spec.models:
                mm = mask & (paired["model"].to_numpy()[rows_idx] == m)
                sets[m] = frozenset(dates[rows_idx][mm])
            union = frozenset().union(*sets.values())
            shared[(*ukey, k)] = (len(union), len(set(sets.values())) == 1)

    # Availability of the variable per side (extraction variables can be null on one side).
    if idx is not None and var is not None:
        for ukey, rows_idx in unit_groups.items():
            n_val = int((idx[rows_idx] != -2).sum())
            side = (ukey[3], ukey[4])
            if n_val == 0:
                unavailable("conditional_error", name, "no_values",
                            f"column {var.column!r} has no values on this side's cohort days",
                            var=var, cohort_id=ukey[0], variant=ukey[2], side=side,
                            n_cohort=len(rows_idx), n_value=0)
    if var is None or idx is None or (idx != -2).any():
        reason = INTERPRETATION
        if name == spec.sensible_heat_variable:
            reason = ("daily-mean H only: the negative bin is a daily-mean indicator, not a "
                      "daytime negative-H frequency and not proof of advection; " + reason)
        if name == "reference_provenance":
            reason = ("observation-dominated (corrected_from_raw) and gap-filled days of one "
                      "frozen cohort; filled days share gridMET drivers with OpenET (D-005) and "
                      "are not independent validation")
        available("conditional_error", name, reason, var=var,
                  n_cohort=paired[["cohort_id", "site_id", "date"]].drop_duplicates().shape[0],
                  n_value=None if idx is None else int(
                      pd.DataFrame({"c": paired["cohort_id"], "s": paired["site_id"],
                                    "d": paired["date"]})[idx != -2].drop_duplicates().shape[0]),
                  flags=_union_flags(var_flags))

    lengths = list(bootstrap.block_lengths) if bootstrap is not None else []
    methods = list(bootstrap.interval_methods) if bootstrap is not None else ["none"]
    tol = metric_spec.daily_tolerance
    ids = list(spec.metrics)
    rows: list[dict[str, Any]] = []
    # estimates and per-bin draws
    for k in k_bins:
        mask_k = in_bin(k)
        estimates: dict[tuple[Any, ...], tuple[np.ndarray, dict[str, Any], str, str | None]] = {}
        for gkey, rows_idx in groups.items():
            sel = rows_idx[mask_k[rows_idx]]
            n = len(sel)
            if k < 0:
                st, detail = _REMAINDER[k][1], None
            elif n == 0:
                st, detail = "empty_bin", "no days in the bin"
            elif n < spec.min_bin_days:
                st, detail = "sparse_bin", f"{n} days < min_bin_days {spec.min_bin_days}"
            else:
                st, detail = "ok", None
            res = (compute_metrics(o_all[sel], p_all[sel], tolerance=tol, metrics=ids)
                   if st == "ok" else {})
            estimates[gkey] = (sel, res, st, detail)

        # bootstrap draws: series of one cohort/site sharing identical bin days share indices
        reps: dict[tuple[tuple[Any, ...], int], np.ndarray] = {}
        supports: dict[tuple[tuple[Any, ...], int], Any] = {}
        if bootstrap is not None:
            by_days: dict[tuple[Any, ...], list[tuple[Any, ...]]] = {}
            for gkey, (sel, _res, st, _d) in estimates.items():
                if st == "ok":
                    by_days.setdefault((gkey[0], gkey[1], tuple(dates[sel])), []).append(gkey)
            for (coh, site, day_key), members in by_days.items():
                days = pd.DatetimeIndex(day_key)
                bounds = segment_bounds(days, boundary=bootstrap.segment_boundary,
                                        water_year_start_month=bootstrap.water_year_start_month)
                series = {g: (o_all[estimates[g][0]], p_all[estimates[g][0]]) for g in members}
                sample = PairedSample((coh, site, "daily", "none", "none", "daily_pairs"),
                                      "site_day", days, days, MappingProxyType(series))
                digest = hashlib.sha256("|".join(str(d)[:10] for d in day_key).encode()
                                        ).hexdigest()[:16]
                for bl in lengths:
                    sup = block_support(bounds, bl)
                    for g in members:
                        supports[(g, bl)] = sup
                    if sup.n_blocks < bootstrap.min_blocks:
                        continue
                    rng = sample_rng(bootstrap.seed, "site_day", bl,
                                     (coh, site, "diagnostics", spec.diagnostics_id, name,
                                      str(k), digest))
                    vals = _replicates(sample, bounds, bl, bootstrap.n_replicates, ids, tol, rng)
                    for i, g in enumerate(members):
                        reps[(g, bl)] = vals[i]

        for gkey, (sel, res, st, detail) in estimates.items():
            ukey = (gkey[0], gkey[1], gkey[3], gkey[4], gkey[5])
            n_union, is_shared = shared[(*ukey, k)]
            n_filled = int((prov[sel] == "corrected_filled").sum())
            flags = [*var_flags, _union_flags(flags_all[sel])]
            if name == "reference_provenance" and n_filled:
                flags += ["shared_gridmet_drivers", "not_independent_validation"
                          if k == 2 else "includes_gap_filled_days"]
            up = b.upper[k] if k >= 0 else None
            if name == spec.sensible_heat_variable and up is not None and up <= 0:
                flags.append("negative_daily_mean_h")
            if not is_shared and k >= 0:
                flags.append("bin_days_differ_across_models")
            if k >= 0:
                label, lower, upper, closed = b.labels[k], b.lower[k], b.upper[k], b.closed[k]
                kind = b.kind
            else:
                label, kind = _REMAINDER[k][0], _REMAINDER[k][0]
                lower = upper = closed = None
            row_base = {**base, **dict(zip(_GROUP, gkey, strict=True)), "variable": name,
                        "bin_index": k, "bin_label": label, "bin_lower": lower,
                        "bin_upper": upper, "bin_closed": closed, "bin_kind": kind,
                        "bin_source": b.source,
                        "variable_column": var.column if var is not None else None,
                        "variable_units": var.units if var is not None else None,
                        "variable_status": status_tag, "n_days": len(sel),
                        "n_days_bin_cohort": n_union, "n_days_reference_filled": n_filled,
                        "n_days_series": len(groups[gkey]), "min_bin_days": spec.min_bin_days,
                        "bin_days_shared": is_shared}
            for j, m in enumerate(ids):
                d = METRIC_DEFINITIONS[m]
                r = res.get(m)
                value = r.value if r is not None else None
                m_status, m_detail = st, detail
                if st == "ok" and value is None:
                    m_status, m_detail = "estimate_undefined", r.reason if r else None
                units = ET_UNIT if d.units == "value" else d.units
                common = {**row_base, "metric": m, "value": value, "units": units,
                          "status": m_status, "status_detail": m_detail,
                          "flags": _union_flags(flags)}
                if bootstrap is None:
                    rows.append({**common, "interval_method": "none", "block_length": 0,
                                 "interval_status": "not_resampled" if m_status == "ok"
                                 else "no_estimate",
                                 "interval_detail": "no bootstrap declared"
                                 if m_status == "ok" else m_status})
                    continue
                alpha = 1.0 - bootstrap.confidence_level
                for bl in lengths:
                    bsup = supports.get((gkey, bl))
                    rv = reps.get((gkey, bl))
                    i_status, i_detail, lo_hi = "ok", None, None
                    n_valid = None
                    if m_status != "ok":
                        i_status, i_detail = "no_estimate", m_status
                    elif bsup is None or bsup.n_blocks < bootstrap.min_blocks:
                        i_status = "insufficient_support"
                        i_detail = (f"{bsup.n_blocks if bsup else 0} disjoint {bl}-day blocks in "
                                    f"{bsup.n_segments if bsup else 0} run(s) of bin days < "
                                    f"min_blocks {bootstrap.min_blocks}")
                    else:
                        assert rv is not None
                        reps_m = rv[j]
                        good = reps_m[np.isfinite(reps_m)]
                        n_valid = len(good)
                        if n_valid < bootstrap.min_valid_fraction * bootstrap.n_replicates:
                            i_status = "insufficient_valid_replicates"
                            i_detail = f"{n_valid} of {bootstrap.n_replicates} replicates valid"
                    for method in methods:
                        if i_status == "ok":
                            assert value is not None
                            lo_hi = _interval(good, float(value), method, alpha)
                        rows.append({**common, "interval_method": method, "block_length": bl,
                                     "lower": lo_hi[0] if lo_hi else None,
                                     "upper": lo_hi[1] if lo_hi else None,
                                     "confidence_level": bootstrap.confidence_level,
                                     "n_segments": bsup.n_segments if bsup else None,
                                     "n_blocks": bsup.n_blocks if bsup else None,
                                     "n_replicates_valid": n_valid,
                                     "interval_status": i_status, "interval_detail": i_detail})
    return rows


def _process(spec: DiagnosticsSpec, values: Mapping[str, np.ndarray], paired: pd.DataFrame,
             cohort: Cohort, base: Mapping[str, Any], available: Any, unavailable: Any
             ) -> list[dict[str, Any]]:
    """Sensible-heat labels, wind-sector and satellite availability, net-radiation coupling."""
    unavailable("daytime_negative_h_frequency", spec.sensible_heat_variable or "none",
                "daily_only_input", "only daily-mean sensible heat exists; a daytime negative-H "
                "frequency needs subdaily H (a negative daily mean is a daily-mean indicator, "
                "not proof of advection)")
    if spec.sensible_heat_variable is None:
        unavailable("negative_daily_mean_h", "none", "variable_not_declared",
                    "no sensible_heat_variable declared")
    if spec.wind_direction_variable is None:
        unavailable("wind_direction_sector", "none", "variable_not_declared",
                    "no wind direction variable is declared (the US-UTJ daily table has no wind "
                    "direction)")
    rows: list[dict[str, Any]] = []
    name = spec.net_radiation_variable
    if name is None:
        unavailable("net_radiation_coupling", "none", "variable_not_declared",
                    "no net_radiation_variable declared")
        return rows
    var = spec.variables[name]
    x = values.get(name)
    if x is None:
        return rows  # reported with the variable
    variants = set(cohort.spec.reference_variants)
    if "et_raw" not in variants:
        unavailable("net_radiation_coupling", name, "cohort_lacks_variant",
                    "the uncorrected-reference version needs a cohort with the et_raw variant "
                    "(e.g. a paired_reference cohort); only the coupled corrected version is "
                    "given here", var=var, variant="et_raw")
    available("net_radiation_coupling", name,
              "e = P - O regressed on net radiation; with an energy-balance-corrected reference "
              "the error and net radiation are mathematically coupled through the correction",
              var=var, flags="variable_provisional" if var.status == "provisional" else None)
    e_all = (paired["modeled_mm"] - paired["observed_mm"]).to_numpy(float)
    prov = paired["reference_provenance"].to_numpy()
    for gkey, g in paired.groupby(_GROUP, sort=True):
        ix = g.index.to_numpy()
        xs, es = x[ix], e_all[ix]
        ok = np.isfinite(xs)
        xs, es = xs[ok], es[ok]
        n = len(xs)
        n_filled = int((prov[ix][ok] == "corrected_filled").sum())
        slope = intercept = r = None
        status, detail = "ok", None
        if n < 3:
            status, detail = "estimate_undefined", f"{n} days with a predictor value (< 3)"
        else:
            dx, de = xs - xs.mean(), es - es.mean()
            sxx, see, sxe = float(dx @ dx), float(de @ de), float(dx @ de)
            if sxx <= 0:
                status, detail = "estimate_undefined", "constant predictor"
            else:
                slope = sxe / sxx
                intercept = float(es.mean() - slope * xs.mean())
                r = float(np.clip(sxe / math.sqrt(sxx * see), -1, 1)) if see > 0 else None
        variant = gkey[3]
        coupled = variant != "et_raw"
        rows.append({**base, **dict(zip(_GROUP, gkey, strict=True)), "predictor": name,
                     "predictor_column": var.column, "predictor_units": var.units,
                     "predictor_status": var.status, "slope": slope,
                     "slope_units": f"{ET_UNIT} per {var.units}", "intercept": intercept,
                     "pearson_r": r, "n_days": n, "n_days_predictor_missing": int((~ok).sum()),
                     "n_days_reference_filled": n_filled, "status": status,
                     "status_detail": detail,
                     "coupling": ("energy_balance_corrected_reference" if coupled
                                  else "uncorrected_reference"),
                     "flags": _union_flags([
                         "mathematical_coupling" if coupled else None,
                         "includes_gap_filled_days" if n_filled else None,
                         "variable_provisional" if var.status == "provisional" else None,
                         _union_flags(g["flags"])])})
    return rows


# --------------------------------------------------------------------------- rank changes

_PAIR_EFFECT = {
    frozenset({"et_raw", "et_corrected_measured"}): "closure",
    frozenset({"et_corrected_measured", "et_corrected_inclusive"}): "gap_filling_and_cohort_days",
    frozenset({"et_raw", "et_corrected_inclusive"}): "closure_and_gap_filling",
}


def reference_rank_changes(sensitivity: CanonicalTable, diagnostics_id: str) -> CanonicalTable:
    """Rank changes between reference variants, from a ``ranking_sensitivity`` table.

    Only rows with dimension ``reference_variant`` are kept; nothing is
    re-ranked. ``effect`` says what a pair isolates: ``closure`` (raw vs
    corrected on the same measured days), ``closure_and_cohort_days`` (the same
    variants on different cohorts), ``gap_filling_and_cohort_days`` (measured vs
    inclusive corrected: gap-filled days and different days, D-004) or
    ``closure_and_gap_filling``.
    """
    if sensitivity.schema is not RANKING_SENSITIVITY:
        raise ContractError("reference_rank_changes needs a ranking_sensitivity table")
    f = sensitivity.frame
    f = f[f["dimension"] == "reference_variant"]
    rows = []
    for r in f.itertuples(index=False):
        va = str(r.context_a).split("|")[4]
        vb = str(r.context_b).split("|")[4]
        effect = _PAIR_EFFECT.get(frozenset({va, vb}), "other")
        if effect == "closure" and not bool(r.same_cohort):
            effect = "closure_and_cohort_days"
        rows.append({"diagnostics_id": diagnostics_id, "ranking_id": r.ranking_id,
                     "context_a": r.context_a, "context_b": r.context_b, "metric": r.metric,
                     "model": r.model, "variant_a": va, "variant_b": vb, "effect": effect,
                     "rank_a": r.rank_a, "rank_b": r.rank_b, "rank_change": r.rank_change,
                     "n_models": r.n_models, "same_cohort": bool(r.same_cohort),
                     "comparison_a": r.comparison_a, "comparison_b": r.comparison_b,
                     "n_days_a": r.n_days_a, "n_days_b": r.n_days_b})
    attrs = {"workflow": "etcompare.diagnostics.reference_rank_changes",
             "source": dict(sensitivity.attributes), "interpretation": REFERENCE_SENSITIVITY,
             "rule": "rows of ranking_sensitivity with dimension reference_variant; not re-ranked"}
    return CanonicalTable.build(_frame(rows, REFERENCE_RANK_CHANGES), REFERENCE_RANK_CHANGES,
                                attrs, source="reference rank changes")
