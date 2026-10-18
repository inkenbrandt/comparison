"""Declared scenario uncertainty of water-balance residuals (guide step 15).

:func:`run_scenario` propagates one declared :class:`~etcompare.config.ScenarioSpec` through
its water balance and writes a ``scenario_results`` table.

Four kinds of uncertainty, never merged
---------------------------------------
Each kind keeps its own label, and each scenario has exactly one category, so no interval
ever combines two of them:

- **sampling**: the step-10 paired block bootstrap (:mod:`etcompare.bootstrap`, the
  ``intervals`` table). It is not produced or declared here.
- **measurement** (``measurement``): Monte Carlo draws from distributions the user declares,
  each with a justification, a mode (``multiplicative`` or ``additive``), a temporal
  dependence (``systematic``: one draw per realization for every day, i.e. a bias;
  ``independent_daily``: one draw per station day) and a dependence across terms
  (``independent`` or a declared ``gaussian_copula``). Results carry the seed, the number of
  draws and the declaration.
- **footprint parameters** (``footprint_parameter``): only supplied alternative realizations
  (term tables computed from regenerated footprints). Noise is never added to normalized
  footprint pixels, and no distribution is accepted for this category.
- **method choice** (``method_choice``): named alternative declarations (e.g. another ET
  reference variant or another precipitation source). These are deterministic sensitivity
  cases: each alternative is reported with the range, never as a probability distribution.

No distribution is a default. The corrected/raw ET difference and model spread are never
turned into distributions: they can enter only as declared ``method_choice`` alternatives.

Rules
-----
- A scenario cannot give an unknown term a value: the config refuses a perturbation of an
  unknown term. A period whose base residual is unknown stays unknown in every draw.
- Perturbing values never changes which days are present, so completeness is that of the
  declared water balance (D-041).
- Draws come from ``numpy.random.default_rng([seed, hash(scenario_id)])``; the same seed and
  declaration reproduce every draw. Systematic draws are made first, then daily ones.
- Results are residual statistics in mm. They are plausibility diagnostics like the residual
  itself (never ET error), and are labelled apart from bootstrap performance intervals
  (flag ``not_bootstrap_interval``).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

from etcompare.config import (
    WATER_BALANCE_SIGNS,
    PeriodAggregationSpec,
    PerturbationSpec,
    ScenarioSpec,
    StationSpec,
    WaterBalanceSpec,
    to_plain,
)
from etcompare.errors import ContractError
from etcompare.schemas import SCENARIO_RESULTS, CanonicalTable
from etcompare.validation import Issue, ValidationReport
from etcompare.water_balance import (
    PreparedWaterBalance,
    StationTables,
    compute_water_balance,
    evaluate_water_balance,
    prepare_water_balance,
    term_total,
)

UNCERTAINTY_LABELS: Mapping[str, str] = {
    "measurement": "measurement uncertainty scenario (declared distributions)",
    "footprint_parameter": "footprint-parameter uncertainty (supplied realizations)",
    "method_choice": "method-choice sensitivity (declared alternatives)",
}
_INTERPRETATION = {
    "measurement": "spread of the residual under the declared measurement distributions; a "
                   "scenario, not a bootstrap performance interval and not ET error",
    "footprint_parameter": "residual under each supplied footprint realization; a range, not "
                           "a probability distribution and not ET error",
    "method_choice": "residual under each declared alternative method; a sensitivity range, not "
                     "a probability distribution and not ET error",
}


def scenario_sha256(spec: ScenarioSpec) -> str:
    """SHA-256 of the scenario declaration (JSON, sorted keys)."""
    text = json.dumps(to_plain(spec), sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _stable_int(text: str) -> int:
    return int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big")


@dataclass(frozen=True)
class ScenarioResult:
    """Scenario statistics of one water balance. Immutable."""

    spec: ScenarioSpec
    _table: CanonicalTable = field(repr=False)
    _issues: tuple[Issue, ...] = field(repr=False)
    _draws: tuple[pd.DataFrame, np.ndarray] | None = field(default=None, repr=False)

    @property
    def results(self) -> CanonicalTable:
        t = self._table
        return CanonicalTable(t.schema, t.frame.copy(), dict(t.attributes))

    @property
    def report(self) -> ValidationReport:
        return ValidationReport(f"scenario {self.spec.scenario_id}", list(self._issues))

    def draws(self) -> tuple[pd.DataFrame, np.ndarray] | None:
        """Residual draws (periods x draws; NaN for unknown periods), measurement only."""
        if self._draws is None:
            return None
        index, values = self._draws
        return index.copy(), values.copy()


def marginal(p: PerturbationSpec, z: np.ndarray) -> np.ndarray:
    """Values of the declared distribution at standard-normal scores ``z`` (copula form)."""
    d = p.distribution
    family = d["family"]
    if family == "normal":
        return d["mean"] + d["sd"] * z
    if family == "lognormal":
        return np.exp(d["mu"] + d["sigma"] * z)
    u = stats.norm.cdf(z)
    if family == "uniform":
        return d["low"] + (d["high"] - d["low"]) * u
    width = d["high"] - d["low"]
    return stats.triang.ppf(u, (d["mode"] - d["low"]) / width, loc=d["low"], scale=width)


def draw_perturbations(spec: ScenarioSpec, n_days: int) -> dict[str, np.ndarray]:
    """Draws of every perturbed term: shape (n_draws,) for systematic terms and
    (n_draws, n_days) for independent daily terms, correlated by the declared copula."""
    rng = np.random.default_rng([int(spec.seed), _stable_int(spec.scenario_id)])  # type: ignore[arg-type]
    n = int(spec.n_draws)  # type: ignore[arg-type]
    out: dict[str, np.ndarray] = {}
    for dependence in ("systematic", "independent_daily"):
        group = sorted((p for p in spec.perturbations if p.temporal_dependence == dependence),
                       key=lambda p: p.term)
        if not group:
            continue
        corr = np.array([[spec.correlation(a.term, b.term) for b in group] for a in group])
        chol = np.linalg.cholesky(corr)
        shape = (n, len(group)) if dependence == "systematic" else (n, n_days, len(group))
        z = rng.standard_normal(shape) @ chol.T
        for k, p in enumerate(group):
            out[p.term] = marginal(p, z[..., k])
    return out


def _flags(*parts: Any) -> str:
    tokens = {t for p in parts for t in (p if isinstance(p, set) else str(p or "").split(";"))
              if t}
    return ";".join(sorted(tokens))


def run_scenario(scenario: ScenarioSpec, water_balance: WaterBalanceSpec,
                 station_daily: StationTables, *, period_aggregation: PeriodAggregationSpec,
                 station: StationSpec) -> ScenarioResult:
    """Scenario statistics of ``water_balance``'s residuals under ``scenario`` (docstring)."""
    if scenario.water_balance != water_balance.water_balance_id:
        raise ContractError(f"scenario {scenario.scenario_id} is declared for water balance "
                            f"{scenario.water_balance!r}, not {water_balance.water_balance_id!r}",
                            config_key=f"scenarios.{scenario.scenario_id}.water_balance")
    for p in scenario.perturbations:
        if water_balance.terms[p.term].status == "unknown":
            raise ContractError(f"scenario {scenario.scenario_id}: term {p.term} is unknown; a "
                                "distribution cannot give it a value")
        if water_balance.terms[p.term].quantity == "storage_state" and \
                p.temporal_dependence != "systematic":
            raise ContractError(f"scenario {scenario.scenario_id}: a storage_state change can "
                                "only be perturbed systematically")
    if scenario.category == "measurement":
        prepared = prepare_water_balance(water_balance, station_daily,
                                         period_aggregation=period_aggregation, station=station)
        return _measurement(scenario, prepared)
    return _alternatives(scenario, water_balance, station_daily, period_aggregation, station)


def _base_kind(spec: WaterBalanceSpec) -> str:
    return "scenario" if any(t.status == "scenario" for t in spec.terms.values()) \
        else "measured_modeled"


def _attrs(scenario: ScenarioSpec, sha: str, wb: WaterBalanceSpec) -> dict[str, Any]:
    return {"workflow": "etcompare.scenarios.run_scenario", "scenario": to_plain(scenario),
            "scenario_sha256": sha, "seed": scenario.seed, "n_draws": scenario.n_draws,
            "uncertainty_category": scenario.category,
            "uncertainty_label": UNCERTAINTY_LABELS[scenario.category],
            "water_balance": to_plain(wb), "equation": wb.equation_text,
            "not_bootstrap_interval": True,
            "sampling_uncertainty": "step-10 bootstrap intervals; never merged here"}


def _row(scenario: ScenarioSpec, sha: str, wb: WaterBalanceSpec, period: Any,
         statistic: str, value: float | None, status: str, detail: str | None,
         n_draws: int, n_valid: int | None, kind: str, flags: str) -> dict[str, Any]:
    return {"scenario_id": scenario.scenario_id, "site_id": wb.site_id,
            "period_type": period.period_type, "period_label": period.period_label,
            "statistic": statistic, "water_balance_id": wb.water_balance_id,
            "uncertainty_category": scenario.category,
            "uncertainty_label": UNCERTAINTY_LABELS[scenario.category],
            "period_start": period.period_start, "period_end": period.period_end,
            "residual_kind": kind, "value": value, "status": status, "status_detail": detail,
            "n_draws": n_draws, "n_valid_draws": n_valid, "seed": scenario.seed,
            "scenario_sha256": sha, "interpretation": _INTERPRETATION[scenario.category],
            "flags": flags}


def _table(rows: list[dict[str, Any]], attrs: dict[str, Any], source: str) -> CanonicalTable:
    frame = pd.DataFrame(rows, columns=SCENARIO_RESULTS.names) if rows else SCENARIO_RESULTS.empty()
    for c in SCENARIO_RESULTS.columns:
        if c.kind in ("float", "int"):
            frame[c.name] = pd.to_numeric(frame[c.name])
    frame = frame.sort_values(list(SCENARIO_RESULTS.primary_key), ignore_index=True)
    return CanonicalTable.build(frame, SCENARIO_RESULTS, attrs, source=source)


def _measurement(scenario: ScenarioSpec, prepared: PreparedWaterBalance) -> ScenarioResult:
    wb = prepared.spec
    sha = scenario_sha256(scenario)
    report = ValidationReport(f"scenario {scenario.scenario_id}")
    kind = _base_kind(wb)
    base = evaluate_water_balance(prepared).residuals.frame
    base = base[base["residual_kind"] == kind].set_index(["period_type", "period_label"])
    axis = (pd.DatetimeIndex(sorted({d for p in prepared.periods for d in p.days}))
            if prepared.periods else pd.DatetimeIndex([]))
    pos = pd.Series(np.arange(len(axis)), index=axis)
    draws = draw_perturbations(scenario, len(axis))
    perts = {p.term: p for p in scenario.perturbations}
    n = int(scenario.n_draws)  # type: ignore[arg-type]
    flags = {"scenario_result", "not_bootstrap_interval", "declared_distributions",
             "plausibility_diagnostic_only", "not_et_error"}
    rows: list[dict[str, Any]] = []
    index_rows: list[tuple[str, str]] = []
    values = np.full((len(prepared.periods), n), np.nan)
    stat_names = ["base", "mean", "sd", *(f"q{q:g}" for q in scenario.quantiles)]
    for i, p in enumerate(prepared.periods):
        b = base.loc[(p.period_type, p.label)]
        period = pd.Series({"period_type": p.period_type, "period_label": p.label,
                            "period_start": p.start, "period_end": p.end})
        index_rows.append((p.period_type, p.label))
        row_flags = _flags(flags, b["flags"])
        if b["status"] != "computed":
            for s in stat_names:
                rows.append(_row(scenario, sha, wb, period, s, None, "unknown",
                                 f"base residual {b['status']}: {b['reason']}", n, 0, kind,
                                 row_flags))
            continue
        r = np.zeros(n)
        idx = pos.reindex(p.days).to_numpy()
        for term, ts in prepared.series.items():
            tot = term_total(ts, p)
            sign = WATER_BALANCE_SIGNS[term]
            pert = perts.get(term)
            assert tot.total is not None
            if pert is None:
                r += sign * tot.total
                continue
            x = draws[term]
            if pert.temporal_dependence == "systematic":
                if pert.mode == "multiplicative":
                    contrib = tot.total * x
                elif ts.spec.quantity == "storage_state":
                    contrib = tot.total + x
                else:
                    contrib = tot.total + x * len(p.days)
            else:
                daily = (np.full(len(p.days), float(ts.spec.daily_mm))  # type: ignore[arg-type]
                         if ts.values is None else ts.values.reindex(p.days).to_numpy(float))
                xd = x[:, idx]
                contrib = ((xd * daily).sum(axis=1) if pert.mode == "multiplicative"
                           else daily.sum() + xd.sum(axis=1))
            r += sign * contrib
        values[i] = r
        valid = np.isfinite(r)
        stat = {"base": float(b["residual_mm"]), "mean": float(np.mean(r[valid])),
                "sd": float(np.std(r[valid], ddof=1)),
                **{f"q{q:g}": float(np.quantile(r[valid], q)) for q in scenario.quantiles}}
        for s in stat_names:
            rows.append(_row(scenario, sha, wb, period, s, stat[s], "computed",
                             "base = declared residual without perturbation" if s == "base"
                             else None, n, int(valid.sum()), kind, row_flags))
    n_known = sum(np.isfinite(v).any() for v in values)
    report.info("scenario", f"{scenario.category}: {n} draws (seed {scenario.seed}); "
                f"{n_known} of {len(prepared.periods)} periods have a computed base residual")
    index = pd.DataFrame(index_rows, columns=["period_type", "period_label"])
    return ScenarioResult(scenario, _table(rows, _attrs(scenario, sha, wb), scenario.scenario_id),
                          tuple(report.issues), (index, values))


def _alternatives(scenario: ScenarioSpec, wb: WaterBalanceSpec, station_daily: StationTables,
                  period_aggregation: PeriodAggregationSpec, station: StationSpec
                  ) -> ScenarioResult:
    sha = scenario_sha256(scenario)
    report = ValidationReport(f"scenario {scenario.scenario_id}")
    prefix = "realization" if scenario.category == "footprint_parameter" else "alternative"
    specs = {"declared": wb}
    for a in scenario.alternatives:
        specs[a.name] = replace(wb, terms={**wb.terms, **a.terms})
    frames = {}
    for name, s in specs.items():
        res = compute_water_balance(s, station_daily, period_aggregation=period_aggregation,
                                    station=station).residuals.frame
        frames[name] = res[res["residual_kind"] == _base_kind(s)].set_index(
            ["period_type", "period_label"])
    flags = {"scenario_result", "not_bootstrap_interval", "not_a_probability_distribution",
             "plausibility_diagnostic_only", "not_et_error",
             "supplied_realizations" if prefix == "realization" else "declared_alternatives"}
    base = frames["declared"]
    kinds = {name: _base_kind(s) for name, s in specs.items()}
    rows: list[dict[str, Any]] = []
    n = len(specs)
    for key, b in base.iterrows():
        period = pd.Series({"period_type": key[0], "period_label": key[1],
                            "period_start": b["period_start"], "period_end": b["period_end"]})
        vals: dict[str, float | None] = {}
        row_flags = set(flags)
        for name, f in frames.items():
            r = f.loc[key]
            row_flags |= {t for t in str(r["flags"] or "").split(";") if t}
            ok = r["status"] == "computed"
            vals[name] = float(r["residual_mm"]) if ok else None
            rows.append(_row(scenario, sha, wb, period, f"{prefix}={name}", vals[name],
                             "computed" if ok else "unknown",
                             None if ok else f"{r['status']}: {r['reason']}", n, None,
                             kinds[name], _flags(row_flags)))
        if len(set(kinds.values())) > 1:
            row_flags.add("mixed_residual_kinds")
        known = [v for v in vals.values() if v is not None]
        missing = [k for k, v in vals.items() if v is None]
        summary: dict[str, Callable[[list[float]], float]] = (
            {"min": min, "max": max} if prefix == "alternative" else
            {"min": min, "median": lambda x: float(np.median(x)), "max": max})
        for stat_name, fn in summary.items():
            ok = not missing
            rows.append(_row(scenario, sha, wb, period, stat_name,
                             float(fn(known)) if ok else None,
                             "computed" if ok else "unknown",
                             None if ok else f"no residual for {prefix}(s) {missing}", n,
                             len(known), kinds["declared"], _flags(row_flags)))
    report.info("scenario", f"{scenario.category}: {n} {prefix}s including the declared one")
    return ScenarioResult(scenario, _table(rows, _attrs(scenario, sha, wb), scenario.scenario_id),
                          tuple(report.issues))
