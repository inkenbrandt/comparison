"""Transparent performance metrics (guide step 9).

With ``P`` the modeled ET, ``O`` the station ET and ``e = P - O`` over one
sample of ``n`` paired values, :func:`compute_metrics` returns:

==========================  ==============================================  =========  ===========
Metric ID                   Definition                                      Units      Role
==========================  ==============================================  =========  ===========
``mbe``                     ``mean(e)``                                     value      score
``mae``                     ``mean(|e|)``                                   value      score
``rmse``                    ``sqrt(mean(e**2))``                            value      score
``bias_corrected_rmse``     ``sqrt(mean((e - mean(e))**2))``                value      diagnostic
``percent_bias``            ``100 * sum(e) / sum(O)``                       ``%``      score
``nrmse_mean``              ``rmse / mean(O)``                              ``1``      score
``nrmse_mean_percent``      ``100 * rmse / mean(O)`` (optional)             ``%``      score
``pearson_r``               ``Sxy / sqrt(Sxx * Syy)`` (Pearson correlation)  ``1``      score
``pearson_r_squared``       ``pearson_r**2``                                ``1``      score
``ols_slope``               ``Sxy / Sxx``, ordinary least squares P on O    ``1``      descriptive
``ols_intercept``           ``mean(P) - ols_slope * mean(O)``               value      descriptive
``nse`` (optional)          ``1 - sum(e**2) / sum((O - mean(O))**2)``       ``1``      score
``kge_2009`` (optional)     ``1 - sqrt((r-1)**2 + (alpha-1)**2 + (beta-1)**2)``  ``1`` score
``kge_2009_r``              ``pearson_r``                                   ``1``      component
``kge_2009_alpha``          ``std(P) / std(O)``                             ``1``      component
``kge_2009_beta``           ``mean(P) / mean(O)``                           ``1``      component
``mape`` (optional)         ``100 * mean(|e| / |O|)``                       ``%``      diagnostic
==========================  ==============================================  =========  ===========

``Sxx = sum((O - mean(O))**2)``, ``Syy = sum((P - mean(P))**2)``,
``Sxy = sum((O - mean(O)) * (P - mean(P)))``; ``std`` is the population standard
deviation (``ddof=0``; it cancels in every ratio above). "value" units are those
of the sample: ``mm d-1`` for daily pairs, ``mm`` for period totals.

Interpretation
--------------
- ``bias_corrected_rmse`` removes the in-sample mean error. It is a diagnostic
  of error variability, not evidence that a bias-corrected product would
  perform that well on new data, and it is never a ranking basis.
- ``pearson_r_squared`` is the square of the Pearson correlation. It is **not**
  the predictive coefficient of determination ``1 - SSE/SST`` (which, with P as
  the prediction of O, is ``nse``); a biased model can have
  ``pearson_r_squared = 1``.
- ``ols_slope`` / ``ols_intercept`` describe the regression of P on O; they are
  descriptive and carry no uncertainty (intervals are step 10).
- ``kge_2009`` is the original Kling-Gupta efficiency (Gupta et al. 2009,
  J. Hydrol. 377:80-91) with the standard-deviation ratio ``alpha``. The
  modified KGE (Kling et al. 2012, coefficient-of-variation ratio) and later
  variants are not implemented and are never mixed with it.
- ``mape`` is computed only when listed and is never a default or ranking
  metric: daily observed ET near zero makes it unstable.

Undefined values
----------------
A metric that cannot be computed is null with a stable ``validity_reason``;
nothing is dropped or clipped to make it defined:

- ``empty_cohort`` (no pairs) or ``no_available_periods`` (no period of the
  product is available);
- ``singleton_sample``: ``n = 1`` for metrics that need a spread
  (``bias_corrected_rmse``, correlation, regression, NSE, KGE);
- ``constant_observed`` / ``constant_modeled``: population standard deviation
  ``<= tolerance.std``;
- ``near_zero_observed_mean``: ``|mean(O)| <= tolerance.mean`` (equivalently a
  near-zero observed total ``sum(O)``), for percent bias, NRMSE and KGE beta;
- ``near_zero_observation``: some ``|O_i| <= tolerance.mean`` (MAPE only).

``ols_slope`` stays defined for a constant P (it is 0); ``pearson_r`` does not.

Samples
-------
:func:`score_comparison` scores a :class:`~etcompare.cohorts.Cohort` on its
common mask only: the cohort's paired rows (``daily_pairs``) and, separately,
each requested period product of its
:class:`~etcompare.aggregation.PeriodTotals` (matched-day ``partial_sum``,
``complete_total``, ``estimated_total``). A period product's sample is the
periods where that product is available; the availability is decided by the
aggregation rules, and it is checked to be identical across every model, side
and reference variant of a cohort. Values are never dropped per metric or per
model. Models of one cohort must share their declared temporal support, and
daily (``mm d-1``) and period (``mm``) values are never pooled.
:func:`cumulative_error` gives the running sum of daily errors over cohort days
with every missing period flagged.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Final

import numpy as np
import pandas as pd

from etcompare.aggregation import PeriodTotals
from etcompare.cohorts import Cohort
from etcompare.config import (
    DEFAULT_METRICS,
    ET_TOTAL_UNIT,
    ET_UNIT,
    MetricSpec,
    NearZeroTolerance,
    ProductSpec,
    to_plain,
)
from etcompare.errors import ContractError
from etcompare.schemas import CUMULATIVE_ERROR, METRICS, CanonicalTable
from etcompare.validation import Issue, ValidationReport

_SIDE = ["extraction_method", "footprint_set_id"]
_GROUP = ["cohort_id", "site_id", "model", "reference_variant", *_SIDE]


@dataclass(frozen=True)
class MetricDefinition:
    """A metric's stable ID, exact definition, units and role."""

    metric_id: str
    formula: str
    units: str  # "value" (unit of the sample), "%" or "1"
    role: str  # score / diagnostic / descriptive / component
    note: str = ""


_DEFS = (
    MetricDefinition("mbe", "mean(e), e = P - O", "value", "score",
                     "mean bias error; positive means the model overestimates"),
    MetricDefinition("mae", "mean(|e|)", "value", "score"),
    MetricDefinition("rmse", "sqrt(mean(e**2))", "value", "score"),
    MetricDefinition("bias_corrected_rmse", "sqrt(mean((e - mean(e))**2))", "value",
                     "diagnostic", "removes the in-sample mean error; diagnostic only, not "
                     "evidence for a validated bias-corrected product"),
    MetricDefinition("percent_bias", "100 * sum(e) / sum(O)", "%", "score"),
    MetricDefinition("nrmse_mean", "rmse / mean(O)", "1", "score", "fraction, not percent"),
    MetricDefinition("nrmse_mean_percent", "100 * rmse / mean(O)", "%", "score",
                     "nrmse_mean multiplied by 100"),
    MetricDefinition("pearson_r", "sum(dO*dP) / sqrt(sum(dO**2) * sum(dP**2)), dX = X - mean(X)",
                     "1", "score", "Pearson correlation"),
    MetricDefinition("pearson_r_squared", "pearson_r**2", "1", "score",
                     "squared Pearson correlation; not the predictive coefficient of "
                     "determination 1 - SSE/SST (see nse)"),
    MetricDefinition("ols_slope", "sum(dO*dP) / sum(dO**2)", "1", "descriptive",
                     "ordinary least squares of P on O; descriptive, no uncertainty"),
    MetricDefinition("ols_intercept", "mean(P) - ols_slope * mean(O)", "value", "descriptive",
                     "ordinary least squares of P on O; descriptive, no uncertainty"),
    MetricDefinition("nse", "1 - sum(e**2) / sum((O - mean(O))**2)", "1", "score",
                     "Nash-Sutcliffe efficiency (predictive coefficient of determination)"),
    MetricDefinition("kge_2009", "1 - sqrt((r - 1)**2 + (alpha - 1)**2 + (beta - 1)**2)", "1",
                     "score", "original Kling-Gupta efficiency (Gupta et al. 2009)"),
    MetricDefinition("kge_2009_r", "pearson_r", "1", "component", "KGE 2009 correlation"),
    MetricDefinition("kge_2009_alpha", "std(P) / std(O), population std", "1", "component",
                     "KGE 2009 standard-deviation ratio"),
    MetricDefinition("kge_2009_beta", "mean(P) / mean(O)", "1", "component",
                     "KGE 2009 mean ratio"),
    MetricDefinition("mape", "100 * mean(|e| / |O|)", "%", "diagnostic",
                     "never a default or ranking metric"),
)
#: Every output metric and its definition.
METRIC_DEFINITIONS: Final[Mapping[str, MetricDefinition]] = MappingProxyType(
    {d.metric_id: d for d in _DEFS})
_EXPANDS: Final = {"kge_2009": ("kge_2009", "kge_2009_r", "kge_2009_alpha", "kge_2009_beta")}


def definitions_frame() -> pd.DataFrame:
    """The metric definitions as a table (for export next to metric tables)."""
    return pd.DataFrame([vars(d) for d in _DEFS])


@dataclass(frozen=True)
class MetricResult:
    """One metric value, or ``None`` with the reason it is undefined."""

    value: float | None
    reason: str | None = None


def output_metrics(metrics: Iterable[str]) -> tuple[str, ...]:
    """Output IDs of the requested metrics (``kge_2009`` adds its three components)."""
    out: list[str] = []
    for m in metrics:
        if m not in METRIC_DEFINITIONS or m.startswith("kge_2009_"):
            raise ValueError(f"unknown metric {m!r}")
        out.extend(_EXPANDS.get(m, (m,)))
    return tuple(out)


def compute_metrics(observed: Sequence[float] | np.ndarray, modeled: Sequence[float] | np.ndarray,
                    *, tolerance: NearZeroTolerance,
                    metrics: Iterable[str] = DEFAULT_METRICS) -> dict[str, MetricResult]:
    """Metrics of one paired sample (definitions in the module docstring).

    The sample must already be the common mask: every value is used. Non-finite
    values raise :class:`ValueError` instead of being dropped.
    """
    o = np.asarray(observed, dtype=float)
    p = np.asarray(modeled, dtype=float)
    if o.ndim != 1 or o.shape != p.shape:
        raise ValueError(f"observed and modeled must be 1-D of equal length, got {o.shape} "
                         f"and {p.shape}")
    if not (np.isfinite(o).all() and np.isfinite(p).all()):
        raise ValueError("non-finite values in the sample; metrics never drop values, so build "
                         "the sample from a cohort's common mask")
    ids = output_metrics(metrics)
    n = len(o)
    if n == 0:
        return {m: MetricResult(None, "empty_cohort") for m in ids}

    e = p - o
    mo, mp, me = float(o.mean()), float(p.mean()), float(e.mean())
    do, dp = o - mo, p - mp
    sxx, syy, sxy = float(do @ do), float(dp @ dp), float(do @ dp)
    so, sp = math.sqrt(sxx / n), math.sqrt(syy / n)
    single = "singleton_sample" if n == 1 else None
    spread_o = single or ("constant_observed" if so <= tolerance.std else None)
    spread_p = single or ("constant_modeled" if sp <= tolerance.std else None)
    near_zero_mean = "near_zero_observed_mean" if abs(mo) <= tolerance.mean else None
    rmse = math.sqrt(float(e @ e) / n)

    def r() -> float:
        return float(np.clip(sxy / math.sqrt(sxx * syy), -1.0, 1.0))

    def kge() -> float:
        return 1.0 - math.sqrt((r() - 1) ** 2 + (sp / so - 1) ** 2 + (mp / mo - 1) ** 2)

    # metric -> (reason it is undefined or None, value function)
    table: dict[str, tuple[str | None, Any]] = {
        "mbe": (None, lambda: me),
        "mae": (None, lambda: float(np.abs(e).mean())),
        "rmse": (None, lambda: rmse),
        "bias_corrected_rmse": (single, lambda: math.sqrt(float(((e - me) ** 2).mean()))),
        "percent_bias": (near_zero_mean, lambda: 100.0 * float(e.sum()) / float(o.sum())),
        "nrmse_mean": (near_zero_mean, lambda: rmse / mo),
        "nrmse_mean_percent": (near_zero_mean, lambda: 100.0 * rmse / mo),
        "pearson_r": (spread_o or spread_p, r),
        "pearson_r_squared": (spread_o or spread_p, lambda: r() ** 2),
        "ols_slope": (spread_o, lambda: sxy / sxx),
        "ols_intercept": (spread_o, lambda: mp - (sxy / sxx) * mo),
        "nse": (spread_o, lambda: 1.0 - float(e @ e) / sxx),
        "kge_2009": (spread_o or spread_p or near_zero_mean, kge),
        "kge_2009_r": (spread_o or spread_p, r),
        "kge_2009_alpha": (spread_o, lambda: sp / so),
        "kge_2009_beta": (near_zero_mean, lambda: mp / mo),
        "mape": ("near_zero_observation" if bool((np.abs(o) <= tolerance.mean).any()) else None,
                 lambda: 100.0 * float((np.abs(e) / np.abs(o)).mean())),
    }
    out = {}
    for m in ids:
        reason, fn = table[m]
        out[m] = MetricResult(None, reason) if reason else MetricResult(float(fn()))
    return out


# --------------------------------------------------------------------------- tables

@dataclass(frozen=True)
class MetricResults:
    """Metric table (``metrics`` schema) and cumulative errors of one cohort. Immutable."""

    spec: MetricSpec
    comparison_id: str
    _table: CanonicalTable = field(repr=False)
    _cumulative: CanonicalTable | None = field(repr=False)
    _issues: tuple[Issue, ...] = field(repr=False)
    attributes: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "attributes", MappingProxyType(dict(self.attributes)))

    @property
    def table(self) -> CanonicalTable:
        t = self._table
        return CanonicalTable(t.schema, t.frame.copy(), dict(t.attributes))

    @property
    def cumulative(self) -> CanonicalTable | None:
        t = self._cumulative
        return None if t is None else CanonicalTable(t.schema, t.frame.copy(),
                                                     dict(t.attributes))

    @property
    def report(self) -> ValidationReport:
        return ValidationReport(f"metrics {self.comparison_id}/{self.spec.metric_set_id}",
                                list(self._issues))

    def wide(self) -> pd.DataFrame:
        """One row per scored sample, one column per metric (for reading, not export)."""
        f = self._table.frame
        keys = ["cohort_id", "scope", "temporal_scale", "period_type", "aggregation_id",
                "product", "model", "reference_variant", *_SIDE]
        counts = f.drop_duplicates(keys)[[*keys, "n_days", "n_periods"]]
        values = f.pivot(index=keys, columns="metric", values="value").reset_index()
        order = [m for m in METRIC_DEFINITIONS if m in values.columns]
        return counts.merge(values, on=keys, validate="one_to_one")[[*keys, "n_days",
                                                                     "n_periods", *order]]


def _union_flags(values: Iterable[object]) -> str | None:
    tokens = sorted({t for v in values if isinstance(v, str) for t in v.split(";") if t})
    return ";".join(tokens) or None


def _support(cohort: Cohort, products: Mapping[str, ProductSpec],
             report: ValidationReport) -> dict[str, str]:
    cs = cohort.spec
    missing = [m for m in cs.models if m not in products]
    if missing:
        raise ContractError(f"metrics {cs.comparison_id}: temporal support of model(s) "
                            f"{missing} not declared", config_key="products")
    support = {m: products[m].temporal_support for m in cs.models}
    if cs.freeze_models and len(set(support.values())) > 1:
        raise ContractError(
            f"metrics {cs.comparison_id}: models of one cohort differ in temporal support "
            f"{support}; only products with the same temporal support are compared",
            config_key="products")
    for m, s in support.items():
        if s != "interpolated_daily":
            report.warning("temporal_support", f"{m}: temporal support {s!r}; its metrics are "
                           "labelled and comparable only with the same support", field=m)
    return support


def _rows(results: Mapping[str, MetricResult], base: Mapping[str, Any],
          value_unit: str) -> list[dict[str, Any]]:
    rows = []
    for m, r in results.items():
        d = METRIC_DEFINITIONS[m]
        units = value_unit if d.units == "value" else d.units
        rows.append({**base, "metric": m, "value": r.value, "units": units,
                     "valid": r.value is not None, "validity_reason": r.reason,
                     "metric_role": d.role})
    return rows


def _check_mask(frame: pd.DataFrame, keys: list[str], unit_col: str, what: str) -> None:
    """Every (model, side, variant) inside ``keys`` must cover the same ``unit_col`` values."""
    varying = ["model", "reference_variant", *_SIDE]
    for k, g in frame.groupby(keys):
        n_groups = g[varying].drop_duplicates().shape[0]
        counts = g.groupby(unit_col).size()
        if (counts != n_groups).any():
            raise ContractError(f"{what} {k}: models, sides or reference variants do not share "
                                f"one {unit_col} mask; metrics never drop values per model")


def _groups(cohort: Cohort) -> pd.DataFrame:
    cs = cohort.spec
    m = cohort.membership.frame[["cohort_id", "site_id", "model"]].drop_duplicates()
    sides = pd.DataFrame([(s.extraction_method, s.footprint_set_id) for s in cs.sides],
                         columns=_SIDE)
    variants = pd.DataFrame({"reference_variant": list(cs.reference_variants)})
    return m.merge(sides, how="cross").merge(variants, how="cross")[_GROUP]


def _daily_rows(cohort: Cohort, spec: MetricSpec, support: Mapping[str, str],
                base: Mapping[str, Any]) -> list[dict[str, Any]]:
    paired = cohort.paired.frame
    if cohort.paired.schema.units.get("observed_mm") != ET_UNIT:
        raise ContractError("paired rows are not in mm d-1")
    _check_mask(paired, ["cohort_id", "site_id"], "date", "cohort")
    by_group = {k: g.sort_values("date") for k, g in paired.groupby(_GROUP)}
    rows: list[dict[str, Any]] = []
    for key in _groups(cohort).itertuples(index=False, name=None):
        g = by_group.get(key)
        g = g if g is not None else paired.iloc[0:0]
        res = compute_metrics(g["observed_mm"].to_numpy(float), g["modeled_mm"].to_numpy(float),
                              tolerance=spec.daily_tolerance, metrics=spec.metrics)
        k = dict(zip(_GROUP, key, strict=True))
        rows += _rows(res, {
            **base, **k, "scope": k["site_id"], "temporal_scale": "daily",
            "period_type": "none", "aggregation_id": "none", "product": "daily_pairs",
            "n_sites": int(len(g) > 0), "n_days": len(g),
            "n_days_reference_filled": int((g["reference_provenance"] == "corrected_filled").sum()),
            "n_days_imputed": None, "n_periods": None, "period_completeness": None,
            "temporal_support": support[k["model"]], "flags": _union_flags(g["flags"]),
        }, ET_UNIT)
    return rows


def _period_rows(cohort: Cohort, totals: PeriodTotals, spec: MetricSpec,
                 base: Mapping[str, Any]) -> list[dict[str, Any]]:
    aid = totals.spec.aggregation_id
    if totals.attributes.get("cohort_membership_sha256") != cohort.fingerprint:
        raise ContractError(f"period totals {aid} were not built from cohort "
                            f"{cohort.comparison_id} (fingerprint mismatch)")
    t = totals.table
    if t.schema.units.get("observed_total_mm") != ET_TOTAL_UNIT:
        raise ContractError(f"period totals {aid} are not in mm")
    f = t.frame
    f = f[f["product"].isin(spec.period_products)]
    if spec.period_types is not None:
        f = f[f["period_type"].isin(spec.period_types)]
    avail = f["observed_total_mm"].notna()
    if (avail != f["modeled_total_mm"].notna()).any():
        raise ContractError(f"period totals {aid}: observed and modeled availability differ")
    for cid, g in f.groupby("cohort_id"):
        if g["temporal_support"].nunique() > 1:
            raise ContractError(f"period totals {aid}/{cid}: models differ in temporal support; "
                                "only products with the same temporal support are compared")
    a = f[avail]
    _check_mask(a, ["cohort_id", "site_id", "period_type", "product"], "period_label",
                f"period totals {aid}")
    tol = spec.period_tolerance
    assert tol is not None  # MetricSpec requires it with period products
    empty = cohort.status == "empty"
    rows: list[dict[str, Any]] = []
    for key, g in f.groupby([*_GROUP, "period_type", "product"]):
        g = g[g["observed_total_mm"].notna()].sort_values("period_start")
        res = compute_metrics(g["observed_total_mm"].to_numpy(float),
                              g["modeled_total_mm"].to_numpy(float), tolerance=tol,
                              metrics=spec.metrics)
        if not len(g) and not empty:
            res = {m: MetricResult(None, "no_available_periods") for m in res}
        k = dict(zip([*_GROUP, "period_type", "product"], key, strict=True))
        n_exp = int(g["n_days_expected"].sum())
        imputed = g["n_days_imputed"].dropna()
        rows += _rows(res, {
            **base, **k, "scope": k["site_id"], "temporal_scale": "period",
            "aggregation_id": aid, "n_sites": int(len(g) > 0),
            "n_days": int(g["n_days_matched"].sum()),
            "n_days_reference_filled": int(g["n_days_matched_reference_filled"].sum()),
            "n_days_imputed": (int(imputed.sum()) if k["product"] == "estimated_total" and len(g)
                               else None),
            "n_periods": len(g),
            "period_completeness": int(g["n_days_matched"].sum()) / n_exp if n_exp else None,
            "temporal_support": (f.loc[f["model"] == k["model"], "temporal_support"].iloc[0]),
            "flags": _union_flags(g["flags"]),
        }, ET_TOTAL_UNIT)
    return rows


def score_comparison(cohort: Cohort, spec: MetricSpec, *, products: Mapping[str, ProductSpec],
                     period_totals: Sequence[PeriodTotals] = ()) -> MetricResults:
    """Score ``cohort`` (daily pairs) and each of its ``period_totals`` under ``spec``.

    ``products`` supplies each model's declared temporal support; a model without
    a declaration raises, as does a ranking cohort whose models differ in
    support. Only aggregations listed in ``spec.period_aggregations`` are
    scored; each must come from this cohort (fingerprint check).
    """
    cid = cohort.comparison_id
    report = ValidationReport(f"metrics {cid}/{spec.metric_set_id}")
    support = _support(cohort, products, report)
    base = {"comparison_id": cid, "metric_set_id": spec.metric_set_id, "scope_type": "site",
            "ranking_scope": cohort.spec.ranking_scope}
    rows: list[dict[str, Any]] = []
    if spec.daily:
        rows += _daily_rows(cohort, spec, support, base)
    scored = []
    for t in period_totals:
        if t.spec.aggregation_id in spec.period_aggregations:
            rows += _period_rows(cohort, t, spec, base)
            scored.append(t.spec.aggregation_id)
    for aid in set(spec.period_aggregations) - set(scored):
        report.info("aggregation_not_supplied", f"period aggregation {aid} was not supplied; "
                    "its totals are not scored", field=aid)
    frame = pd.DataFrame(rows, columns=METRICS.names) if rows else METRICS.empty()
    for c in METRICS.columns:
        if c.kind in ("float", "int"):  # None marks an undefined value or a count n/a
            frame[c.name] = pd.to_numeric(frame[c.name])
    attributes: dict[str, Any] = {
        "workflow": "etcompare.metrics.score_comparison", "metric_set": to_plain(spec),
        "comparison_id": cid, "cohort_membership_sha256": cohort.fingerprint,
        "cohort_kind": cohort.spec.kind, "ranking_scope": cohort.spec.ranking_scope,
        "temporal_support": support, "aggregations_scored": scored,
        "definitions": {m: d.formula for m, d in METRIC_DEFINITIONS.items()},
        "error_convention": "e = modeled - observed",
    }
    table = CanonicalTable.build(frame, METRICS, attributes, source=f"{cid}/{spec.metric_set_id}")
    cumulative = cumulative_error(cohort) if spec.cumulative_error else None

    f = table.frame
    reasons = f.loc[~f["valid"].astype(bool), "validity_reason"].value_counts()
    report.info("metric_rows", f"{len(f)} metric rows, {int(f['valid'].sum())} defined",
                n_rows=len(f))
    for reason, n in reasons.items():
        report.info("metric_undefined", f"{n} metric rows undefined: {reason}", field=str(reason),
                    n_rows=int(n))
    if cohort.status == "empty":
        report.warning("empty_cohort", "the cohort is empty; every metric is null")
    return MetricResults(spec, cid, table, cumulative, tuple(report.issues), attributes)


def cumulative_error(cohort: Cohort) -> CanonicalTable:
    """Running sums of the cohort's daily pairs, per site/model/side/variant.

    Sums run over cohort days only; days outside the cohort are never filled or
    interpolated. The first day after a run of missing calendar days carries
    ``missing_days_before > 0``, the flag ``missing_period_before`` and a new
    ``segment`` number, so plots can break the curve there.
    """
    p = cohort.paired.frame.sort_values([*_GROUP, "date"], ignore_index=True)
    out = p[["comparison_id", *_GROUP, "date", "observed_mm", "modeled_mm"]].copy()
    out["error_mm"] = p["modeled_mm"] - p["observed_mm"]
    g = out.groupby(_GROUP, sort=False)
    for col in ("observed_mm", "modeled_mm", "error_mm"):
        out[f"cumulative_{col}"] = g[col].cumsum()
    out["n_days_cumulated"] = g.cumcount() + 1
    gap = (g["date"].diff().dt.days - 1)
    out["missing_days_before"] = gap
    out["segment"] = (gap.fillna(0) > 0).groupby([out[c] for c in _GROUP], sort=False
                                                  ).cumsum().astype(int) + 1
    out["flags"] = [_union_flags([f, "missing_period_before" if m > 0 else None])
                    for f, m in zip(p["flags"], gap.fillna(0), strict=True)]
    attributes = {"workflow": "etcompare.metrics.cumulative_error",
                  "comparison_id": cohort.comparison_id,
                  "cohort_membership_sha256": cohort.fingerprint,
                  "error_convention": "e = modeled - observed"}
    return CanonicalTable.build(out[CUMULATIVE_ERROR.names], CUMULATIVE_ERROR, attributes,
                                source=f"{cohort.comparison_id} cumulative error")
