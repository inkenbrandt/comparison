"""Station-level rankings, paired model contrasts and network summaries (guide step 11).

:func:`rank_comparison` ranks the models of one ranking cohort under a
:class:`~etcompare.config.RankingSpec`. It uses the step-9 point estimates
(:class:`~etcompare.metrics.MetricResults`) and the step-10 replicate values
(:meth:`~etcompare.bootstrap.BootstrapResult.replicates`); it never redraws a
sample and never re-implements a metric. It returns ``model_rankings``,
``rank_distribution``, ``model_contrasts`` and ``network_summaries`` tables.
:func:`ranking_sensitivity` compares point ranks across contexts.

What may be ranked
------------------
- **Cohorts.** Only cohorts whose ``ranking_scope`` is ``all_models``
  (``common_date``) or ``pairwise`` (``pairwise_model``). ``model_available``
  (coverage) cohorts and paired method/reference cohorts are refused.
- **Contexts.** Models are ranked inside one frozen context: one cohort
  (fingerprint), site, temporal scale and period product, reference variant and
  extraction side. Rows that differ in any of :data:`CONTEXT_FIELDS` are never
  ranked together; :func:`check_rankable` raises instead.
- **Metrics.** Only role ``score`` metrics listed in
  :data:`~etcompare.config.RANKING_DIRECTIONS`, the one place where directions
  are declared: lower is better for ``mae``, ``rmse``, ``nrmse_mean``
  (and ``nrmse_mean_percent``); higher is better for ``pearson_r``,
  ``pearson_r_squared``, ``nse`` and ``kge_2009``; closest to zero is better for
  ``mbe`` and ``percent_bias`` (ranked by absolute value). Diagnostic,
  descriptive and component metrics are refused.

Ranks and ties
--------------
Each value is put on a lower-is-better scale (``v``, ``-v`` or ``|v|``).
Models with exactly equal scaled values are tied; no tolerance is applied, so
identical predictions (which give bit-identical metrics) tie and anything else
does not. Tied models share their positions equally: a model's **rank** is its
mid-rank, ``1 + (number strictly better) + (n_tied - 1) / 2``, and each tied
model receives ``1 / n_tied`` of every shared position in the rank
distribution. Hence each model's position frequencies sum to 1, each position's
frequencies sum to 1 over the models, and the top-rank frequencies of a
context sum to 1. A context with any undefined point estimate is not ranked
(``estimate_undefined``); dropping that model would rank an unequal set.

Rank stability (per declared block length, side by side)
--------------------------------------------------------
For one block length, the replicate matrix of a context (models x replicates)
was drawn on shared indices (step 10). A replicate is valid when every model's
metric is defined in it. Over the valid replicates:

- ``mean_rank``: the mean mid-rank;
- ``rank_lower`` / ``rank_upper``: the ``alpha/2`` (numpy ``method="lower"``) and
  ``1 - alpha/2`` (``method="higher"``) quantiles of the mid-ranks, with
  ``alpha = 1 - confidence_level`` of the bootstrap;
- ``top_rank_frequency``: the share of position 1 (ties split it). This is a
  **bootstrap stability measure**, not the probability that a model is best;
- the full ``rank_distribution``.

A rank statistic built on unsupported or undefined replicates has a status,
not a value: ``insufficient_support`` (fewer than ``min_blocks`` blocks, carried
from step 10), ``insufficient_valid_replicates`` (fewer than
``min_valid_fraction`` of the replicates valid), ``estimate_undefined`` or
``not_resampled`` (no block length applies). No block length is preferred.

No forced winner. ``leader_status`` is ``unique_leader`` only when the
point-best model's contrast interval (first declared interval method) lies
entirely in its favour against every other model. Otherwise it is
``no_unique_leader`` and ``leading_models`` lists the point-best model(s) and
every model not separated from them. Even a unique leader is a statement
about one station, cohort and block length, not a verdict on which model is
best.

Contrasts
---------
For models declared in the order ``m_1 .. m_k``, every pair ``i < j`` gives
``contrast = metric(model_a) - metric(model_b)`` with ``model_a = m_j`` and
``model_b = m_i`` (the earlier model is the baseline, matching the step-10
pairwise contrast). The sign meaning is stated per row: negative favours
``model_a`` for lower-is-better metrics, positive for higher-is-better ones.
Closest-to-zero metrics get two rows: ``value`` (the signed difference, which
orders nothing) and ``absolute_value`` (``|metric_a| - |metric_b|``, negative
favours ``model_a``). The contrast is the practical effect size, in the
metric's units (percentage points for percent metrics), with its interval from
the replicate-by-replicate differences. Extraction-method contrasts keep the
step-10 convention ``delta_mae = MAE_window - MAE_footprint`` (positive: the
footprint has the smaller error) and come from :mod:`etcompare.bootstrap`.

Equivalence and multiplicity
----------------------------
With a predeclared margin ``d`` (``equivalence_margins``), a ranking-basis
interval inside ``(-d, d)`` is ``equivalent``, one entirely at or beyond ``d``
on one side is ``practically_different``, anything else is ``inconclusive``.
Without a margin the result is ``not_assessed``; an interval crossing zero is
never taken as equivalence. Equivalence is judged on the reported interval
(at the bootstrap's level, i.e. a TOST at ``alpha/2`` per side).
With ``formal_tests`` each ranking-basis contrast with an interval gets the
percentile-inversion bootstrap p-value ``min(1, 2 min(P(d <= 0), P(d >= 0)))``
over its valid replicates, adjusted by the declared ``multiplicity`` policy
(Holm or Bonferroni) within the family of one comparison, subset and block
length (every metric, context and pair); decisions use
``alpha = 1 - confidence_level``. Every other row is ``exploratory``.

Network summaries
-----------------
:func:`network_summaries` combines station metrics of one cohort across sites.
The primary result is the **equal-site mean**. Optional, labelled
alternatives weight sites by observation count ``n`` (days for daily pairs,
periods for period products) or ``sqrt(n)``. ``pooled_rmse`` is
``sqrt(sum n RMSE^2 / sum n)``, the RMSE over all site observations; it is not
the mean station RMSE. A summary is undefined when any site's metric is.
``n_sites`` is explicit, and a one-site summary has status ``single_site``
and the flag ``not_network_evidence``.

Limitations
-----------
Station-level inference only. A fixed monitored network and a population of
sites are different targets; population inference needs hierarchical
resampling of sites together with common calendar blocks. That design is not
implemented, so network summaries carry no interval.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

import numpy as np
import pandas as pd

from etcompare.bootstrap import BootstrapResult
from etcompare.cohorts import Cohort
from etcompare.config import RANKING_DIRECTIONS, RankingSpec, to_plain
from etcompare.errors import ContractError
from etcompare.metrics import METRIC_DEFINITIONS, MetricResults
from etcompare.schemas import (
    MODEL_CONTRASTS,
    MODEL_RANKINGS,
    NETWORK_SUMMARIES,
    RANK_DISTRIBUTION,
    RANKING_SENSITIVITY,
    CanonicalTable,
    TableSchema,
)
from etcompare.validation import Issue, ValidationReport

_SIDE = ["extraction_method", "footprint_set_id"]
_SCALE = ["temporal_scale", "period_type", "aggregation_id", "product"]
#: Fields that must be identical across the rows (models) of one ranking.
CONTEXT_FIELDS = ("comparison_id", "cohort_id", "cohort_membership_sha256", "metric_set_id",
                  "scope", *_SCALE, "reference_variant", *_SIDE, "metric", "units",
                  "temporal_support", "ranking_scope")
RANKABLE_SCOPES = ("all_models", "pairwise")
_CTX = ["cohort_id", "scope", *_SCALE, "reference_variant", *_SIDE]
_UNIT = {"daily": "site_day", "period": "period"}
_REP_KEY = ["block_unit", "block_length", "cohort_id", "site_id", *_SCALE, "model",
            "reference_variant", *_SIDE, "metric"]


# --------------------------------------------------------------------------- core

def oriented(values: np.ndarray, direction: str) -> np.ndarray:
    """Values on a lower-is-better scale: ``v`` (lower), ``-v`` (higher), ``|v|`` (closest to 0)."""
    v = np.asarray(values, dtype=float)
    if direction == "lower":
        return v
    if direction == "higher":
        return -v
    if direction == "closest_to_zero":
        return np.abs(v)
    raise ValueError(f"unknown direction {direction!r}")


def rank_positions(scores: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Mid-ranks and position shares of lower-is-better ``scores`` (models x columns).

    Returns ``mid`` (models x columns) and ``share`` (models x positions x
    columns); tied models share their positions equally (module docstring).
    """
    s = np.asarray(scores, dtype=float)
    if s.ndim == 1:
        s = s[:, None]
    if not np.isfinite(s).all():
        raise ValueError("ranks need finite scores")
    less = (s[None, :, :] < s[:, None, :]).sum(axis=1)  # [i, r]: models strictly better than i
    eq = (s[None, :, :] == s[:, None, :]).sum(axis=1)  # [i, r]: models tied with i (incl. i)
    mid = less + (eq + 1) / 2.0
    pos = np.arange(1, s.shape[0] + 1)[None, :, None]
    share = ((pos > less[:, None, :]) & (pos <= (less + eq)[:, None, :])) / eq[:, None, :]
    return mid, share


def adjust_p_values(p: Sequence[float], policy: str) -> np.ndarray:
    """Holm (step-down) or Bonferroni adjusted p-values, capped at 1."""
    q = np.asarray(p, dtype=float)
    m = len(q)
    if policy == "bonferroni":
        return np.minimum(1.0, q * m)
    if policy != "holm":
        raise ValueError(f"unknown multiplicity policy {policy!r}")
    out = np.empty(m)
    running = 0.0
    for k, i in enumerate(np.argsort(q, kind="stable")):
        running = max(running, min(1.0, (m - k) * q[i]))
        out[i] = running
    return out


def bootstrap_p_value(differences: np.ndarray) -> float:
    """Percentile-inversion p-value of ``H0: contrast = 0`` from replicate differences."""
    d = np.asarray(differences, dtype=float)
    return float(min(1.0, 2.0 * min(float(np.mean(d <= 0)), float(np.mean(d >= 0)))))


def _bounds(reps: np.ndarray, estimate: float, method: str, alpha: float) -> tuple[float, float]:
    q_lo, q_hi = np.quantile(reps, [alpha / 2, 1 - alpha / 2])
    if method == "percentile":
        return float(q_lo), float(q_hi)
    return 2 * estimate - float(q_hi), 2 * estimate - float(q_lo)


def check_rankable(rows: pd.DataFrame) -> None:
    """Raise unless ``rows`` (one per model) form one frozen, rankable context.

    Every field of :data:`CONTEXT_FIELDS` must take one value, the cohort must
    have a ranking scope, the metric must be a declared score metric and each
    model must appear once.
    """
    for c in CONTEXT_FIELDS:
        if c not in rows.columns:
            raise ContractError(f"ranking rows lack column {c!r}")
        values = rows[c].drop_duplicates()
        if len(values) > 1:
            raise ContractError(
                f"a ranking would pool unequal cohorts or samples: rows differ in {c} "
                f"({sorted(map(str, values))}); rank each frozen context separately")
    if not len(rows):
        raise ContractError("no rows to rank")
    scope = rows["ranking_scope"].iloc[0]
    if scope not in RANKABLE_SCOPES:
        raise ContractError(f"ranking scope {scope!r}: only all_models and pairwise cohorts are "
                            "ranked; coverage (model_available) cohorts never are")
    metric = str(rows["metric"].iloc[0])
    d = METRIC_DEFINITIONS.get(metric)
    if metric not in RANKING_DIRECTIONS or d is None or d.role != "score":
        role = d.role if d is not None else "unknown"
        raise ContractError(f"metric {metric!r} (role {role}) is not a ranking basis; only score "
                            f"metrics in RANKING_DIRECTIONS are ranked")
    if rows["model"].duplicated().any():
        raise ContractError("a model appears twice in one ranking context")


def _union_flags(values: Iterable[object]) -> str | None:
    tokens = sorted({t for v in values if isinstance(v, str) for t in v.split(";") if t})
    return ";".join(tokens) or None


def _int(v: Any) -> int | None:
    return None if v is None or pd.isna(v) else int(v)


def _frame(rows: list[dict[str, Any]], schema: TableSchema) -> pd.DataFrame:
    frame = pd.DataFrame(rows, columns=schema.names) if rows else schema.empty()
    for c in schema.columns:
        if c.kind in ("float", "int"):
            frame[c.name] = pd.to_numeric(frame[c.name])
    return frame.sort_values(list(schema.primary_key), ignore_index=True)


def year_subsets(cohort: Cohort) -> list[tuple[str, str, str]]:
    """``(label, start, end)`` of each calendar year a cohort's days span, if more than one.

    Each subset is meant to be rebuilt with :func:`etcompare.cohorts.build_cohort`
    under the same rule with ``start``/``end`` restricted, so every year is an
    internally frozen cohort.
    """
    dates = pd.DatetimeIndex(cohort.paired.frame["date"].unique())
    years = sorted(set(dates.year))
    if len(years) < 2:
        return []
    s, e = cohort.spec.start, cohort.spec.end
    return [(f"year={y}", max(s or f"{y}-01-01", f"{y}-01-01"),
             min(e or f"{y}-12-31", f"{y}-12-31")) for y in years]


# --------------------------------------------------------------------------- results

@dataclass(frozen=True)
class RankingResult:
    """Rankings, rank distributions, model contrasts and network summaries. Immutable."""

    spec: RankingSpec
    comparison_id: str
    subset: str
    _rankings: CanonicalTable = field(repr=False)
    _distribution: CanonicalTable = field(repr=False)
    _contrasts: CanonicalTable = field(repr=False)
    _network: CanonicalTable = field(repr=False)
    _issues: tuple[Issue, ...] = field(repr=False)
    attributes: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "attributes", MappingProxyType(dict(self.attributes)))

    @staticmethod
    def _copy(t: CanonicalTable) -> CanonicalTable:
        return CanonicalTable(t.schema, t.frame.copy(), dict(t.attributes))

    @property
    def rankings(self) -> CanonicalTable:
        return self._copy(self._rankings)

    @property
    def rank_distribution(self) -> CanonicalTable:
        return self._copy(self._distribution)

    @property
    def contrasts(self) -> CanonicalTable:
        return self._copy(self._contrasts)

    @property
    def network(self) -> CanonicalTable:
        return self._copy(self._network)

    @property
    def report(self) -> ValidationReport:
        return ValidationReport(f"ranking {self.comparison_id}/{self.spec.ranking_id}"
                                f"/{self.subset}", list(self._issues))

    def summary(self) -> pd.DataFrame:
        """Ranking rows per temporal scale, block length, status and leader status."""
        f = self._rankings.frame.drop_duplicates([*_CTX, "metric", "block_length"])
        return (f.groupby(["temporal_scale", "block_length", "status", "leader_status"])
                .size().rename("n_rankings").reset_index())


# --------------------------------------------------------------------------- ranking

def rank_comparison(cohort: Cohort, metric_results: MetricResults,
                    bootstrap: BootstrapResult | None, spec: RankingSpec, *,
                    subset: str = "all") -> RankingResult:
    """Rank the models of ``cohort`` in every frozen context (module docstring).

    ``metric_results`` and ``bootstrap`` must come from this cohort (fingerprint
    check) and from the metric set and bootstrap named by ``spec``. Without a
    bootstrap only point ranks are given (``not_resampled``).
    """
    cid = cohort.comparison_id
    report = ValidationReport(f"ranking {cid}/{spec.ranking_id}/{subset}")
    cs = cohort.spec
    if cs.ranking_scope not in RANKABLE_SCOPES:
        raise ContractError(f"comparison {cid} (kind {cs.kind}) has ranking scope 'none'; only "
                            "common_date and pairwise_model cohorts are ranked")
    if spec.comparisons is not None and cid not in spec.comparisons:
        raise ContractError(f"ranking {spec.ranking_id} does not list comparison {cid}")
    if metric_results.spec.metric_set_id != spec.metric_set:
        raise ContractError(f"ranking {spec.ranking_id} uses metric set {spec.metric_set!r}, not "
                            f"{metric_results.spec.metric_set_id!r}")
    fp = cohort.fingerprint
    if metric_results.attributes.get("cohort_membership_sha256") != fp:
        raise ContractError(f"metric results are not those of cohort {cid} (fingerprint "
                            "mismatch); a ranking never mixes cohorts")
    if bootstrap is not None:
        if bootstrap.spec.bootstrap_id != spec.bootstrap:
            raise ContractError(f"ranking {spec.ranking_id} uses bootstrap {spec.bootstrap!r}, "
                                f"not {bootstrap.spec.bootstrap_id!r}")
        if (bootstrap.attributes.get("cohort_membership_sha256") != fp
                or bootstrap.attributes.get("metric_set_id") != spec.metric_set):
            raise ContractError(f"bootstrap replicates are not those of cohort {cid} and metric "
                                f"set {spec.metric_set}")

    point = metric_results.table.frame
    missing = set(spec.metrics) - set(point["metric"])
    if missing and len(point):
        raise ContractError(f"metrics {sorted(missing)} were not scored for {cid}")
    point = point[point["metric"].isin(list(spec.metrics))].assign(
        cohort_membership_sha256=fp)
    models = list(cs.models)

    bs = bootstrap.spec if bootstrap is not None else None
    if bootstrap is not None and bs is not None:
        idx, values = bootstrap.replicates()
        rep = {k: i for i, k in enumerate(idx[_REP_KEY].itertuples(index=False, name=None))}
        iv = bootstrap.intervals.frame
        iv = iv[(iv["contrast_dimension"] == "none")
                & (iv["interval_method"] == bs.interval_methods[0])]
        support = {k: v for k, v in zip(
            iv[[*_CTX[:1], "scope", *_SCALE, "model", "reference_variant", *_SIDE, "metric",
                "block_unit", "block_length"]].itertuples(index=False, name=None),
            iv[["status", "status_detail", "n_segments", "n_blocks"]].itertuples(
                index=False, name=None), strict=True)}
        lengths = {"daily": list(bs.block_lengths), "period": list(bs.period_block_lengths)}
        methods = list(bs.interval_methods)
        n_rep, level, seed, min_valid = (bs.n_replicates, bs.confidence_level, bs.seed,
                                         bs.min_valid_fraction)
    else:
        values = np.empty((0, 0))
        rep, support, lengths = {}, {}, {"daily": [], "period": []}
        methods, n_rep, level, seed, min_valid = ["percentile"], 0, None, None, 1.0
    alpha = 1.0 - level if level is not None else 0.05
    boot_id = bs.bootstrap_id if bs is not None else "none"
    base = {"ranking_id": spec.ranking_id, "comparison_id": cid, "subset": subset,
            "metric_set_id": spec.metric_set,
            "bootstrap_id": boot_id,
            "scope_type": "site", "ranking_scope": cs.ranking_scope,
            "cohort_membership_sha256": fp, "seed": seed, "n_replicates_requested": n_rep}

    rank_rows: list[dict[str, Any]] = []
    dist_rows: list[dict[str, Any]] = []
    con_rows: list[dict[str, Any]] = []
    tests: dict[tuple[str, int], list[tuple[int, float]]] = {}

    for key, rows in point.groupby([*_CTX, "metric"], sort=True):
        check_rankable(rows)
        ctx = dict(zip([*_CTX, "metric"], key, strict=True))
        metric, scale = ctx["metric"], ctx["temporal_scale"]
        if set(rows["model"]) != set(models):
            raise ContractError(f"{cid} {key}: models {sorted(rows['model'])} differ from the "
                                f"cohort's {sorted(models)}; a ranking never mixes model sets")
        rows = rows.set_index("model").loc[models]
        direction = spec.metrics[metric]
        v = rows["value"].to_numpy(dtype=float)
        undefined = [f"{m}: {r}" for m, r, x in zip(models, rows["validity_reason"], v,
                                                    strict=True) if not np.isfinite(x)]
        r0 = rows.iloc[0]
        unit, units = _UNIT[scale], str(r0["units"])
        counts = {"n_sites": int(r0["n_sites"]), "n_days": int(r0["n_days"]),
                  "n_periods": _int(r0["n_periods"])}
        ctx_out = {k: ctx[k] for k in _CTX if k != "scope"} | {"scope": ctx["scope"]}
        s_point = oriented(v, direction)
        if not undefined:
            mid_point = rank_positions(s_point)[0][:, 0]
            n_tied = (s_point[None, :] == s_point[:, None]).sum(axis=1)
        flags = {m: rows.loc[m, "flags"] for m in models}

        for b in (lengths[scale] or [0]):
            # ---- status and replicate matrix shared by the ranking and the contrasts
            reps: np.ndarray | None = None
            n_seg = n_blk = None
            if b == 0:
                status, detail = "not_resampled", ("no bootstrap supplied" if bs is None else
                                                   "the bootstrap declares no block length for "
                                                   f"{scale} samples")
            else:
                sk = (ctx["cohort_id"], ctx["scope"], *(ctx[c] for c in _SCALE))
                sup = support.get((*sk, models[0], ctx["reference_variant"],
                                   ctx["extraction_method"], ctx["footprint_set_id"], metric,
                                   unit, b))
                if sup is None:
                    raise ContractError(f"bootstrap has no interval for {cid} {key} at block "
                                        f"length {b}")
                st, det, n_seg, n_blk = sup
                n_seg, n_blk = _int(n_seg), _int(n_blk)
                status, detail = "ok", None
                rk = [(unit, b, ctx["cohort_id"], ctx["scope"], *(ctx[c] for c in _SCALE), m,
                       ctx["reference_variant"], ctx["extraction_method"],
                       ctx["footprint_set_id"], metric) for m in models]
                if st == "insufficient_support":
                    status, detail = st, det
                elif all(k in rep for k in rk):  # NaN: the metric is undefined in a replicate
                    reps = np.vstack([values[rep[k]] for k in rk])
                elif len(undefined) == len(models):  # no available unit: nothing was drawn
                    status, detail = st, det
                else:
                    raise ContractError(f"bootstrap has no replicates for {cid} {key} at block "
                                        f"length {b}")
            common = {**base, **ctx_out, "metric": metric, "block_unit": unit,
                      "block_length": b, "n_segments": n_seg, "n_blocks": n_blk, **counts}

            # ---- ranking rows
            valid = (np.isfinite(reps).all(axis=0) if reps is not None
                     else np.zeros(0, dtype=bool))
            n_valid = int(valid.sum())
            r_status, r_detail = status, detail
            if undefined:
                r_status, r_detail = "estimate_undefined", "; ".join(undefined)
            elif r_status == "ok" and n_valid < min_valid * n_rep:
                r_status = "insufficient_valid_replicates"
                r_detail = f"{n_valid} of {n_rep} replicates have every model's metric defined"
            stats: dict[str, Any] = {}
            leader, leading = "not_assessed", None
            if r_status == "ok":
                assert reps is not None
                s_rep = oriented(reps[:, valid], direction)
                mid_rep, share = rank_positions(s_rep)
                freq = share.mean(axis=2)
                stats = {"mean_rank": mid_rep.mean(axis=1),
                         "rank_lower": np.quantile(mid_rep, alpha / 2, axis=1, method="lower"),
                         "rank_upper": np.quantile(mid_rep, 1 - alpha / 2, axis=1,
                                                   method="higher"),
                         "top_rank_frequency": freq[:, 0]}
                best = np.flatnonzero(s_point == s_point.min())
                lead = set(best.tolist())
                for j in range(len(models)):
                    if j in lead:
                        continue
                    for bi in best.tolist():
                        d = s_rep[bi] - s_rep[j]
                        _, upper = _bounds(d, float(s_point[bi] - s_point[j]), methods[0],
                                           alpha)
                        if not upper < 0:
                            lead.add(j)
                            break
                leader = "unique_leader" if len(lead) == 1 else "no_unique_leader"
                leading = ";".join(models[i] for i in sorted(lead))
                for i, m in enumerate(models):
                    for p in range(len(models)):
                        dist_rows.append({**{k: common[k] for k in RANK_DISTRIBUTION.names
                                             if k in common}, "model": m, "position": p + 1,
                                          "frequency": float(freq[i, p]),
                                          "n_replicates_valid": n_valid})
            for i, m in enumerate(models):
                rank_rows.append({
                    **common, "model": m, "direction": direction,
                    "value": None if not np.isfinite(v[i]) else float(v[i]), "units": units,
                    "rank": None if undefined else float(mid_point[i]),
                    "n_models": len(models), "n_tied": None if undefined else int(n_tied[i]),
                    **{k: float(a[i]) for k, a in stats.items()},
                    "confidence_level": level, "leader_status": leader,
                    "leading_models": leading,
                    "leader_interval_method": methods[0] if bs is not None else None,
                    "n_replicates_valid": n_valid,
                    "n_days_reference_filled": int(rows.loc[m, "n_days_reference_filled"]),
                    "status": r_status, "status_detail": r_detail, "flags": flags[m]})

            # ---- contrasts: model_a = later declared model, model_b = earlier (baseline)
            bases = ["value", "absolute_value"] if direction == "closest_to_zero" else ["value"]
            for j in range(len(models)):
                for i in range(j):
                    a, bm = models[j], models[i]
                    for basis in bases:
                        rank_basis = direction != "closest_to_zero" or basis == "absolute_value"
                        why = [f"model_a {a}: {rows.loc[a, 'validity_reason']}"
                               if not np.isfinite(v[j]) else None,
                               f"model_b {bm}: {rows.loc[bm, 'validity_reason']}"
                               if not np.isfinite(v[i]) else None]
                        est = (None if any(why) else
                               float(_on_basis(v[j], basis) - _on_basis(v[i], basis)))
                        c_status, c_detail = status, detail
                        d_rep = np.zeros(0)
                        if est is None:
                            c_status = "estimate_undefined"
                            c_detail = "; ".join(w for w in why if w)
                        elif status == "ok":
                            assert reps is not None
                            dd = _on_basis(reps[j], basis) - _on_basis(reps[i], basis)
                            d_rep = dd[np.isfinite(dd)]
                            if len(d_rep) < min_valid * n_rep:
                                c_status = "insufficient_valid_replicates"
                                c_detail = (f"{len(d_rep)} of {n_rep} replicates have both "
                                            "metrics defined")
                        p_value = None
                        if spec.formal_tests and rank_basis and c_status == "ok":
                            p_value = bootstrap_p_value(d_rep)
                        cid_row = len(con_rows)
                        if p_value is not None:
                            tests.setdefault((unit, b), []).append((cid_row, p_value))
                        for method in methods:
                            lo = hi = None
                            position = favoured = None
                            if c_status == "ok":
                                assert est is not None
                                lo, hi = _bounds(d_rep, est, method, alpha)
                                position = ("above_zero" if lo > 0 else
                                            "below_zero" if hi < 0 else "includes_zero")
                                if rank_basis and position != "includes_zero":
                                    a_better = ((position == "above_zero")
                                                == (direction == "higher"))
                                    favoured = a if a_better else bm
                            margin = spec.margin(metric, scale)
                            if not rank_basis:
                                eq = "not_applicable"
                            elif margin is None:
                                eq = "not_assessed"
                            elif lo is None or hi is None:
                                eq = "no_interval"
                            elif -margin < lo and hi < margin:
                                eq = "equivalent"
                            elif lo >= margin or hi <= -margin:
                                eq = "practically_different"
                            else:
                                eq = "inconclusive"
                            con_rows.append({
                                **common, "model_a": a, "model_b": bm, "contrast_basis": basis,
                                "interval_method": method, "direction": direction,
                                "ranking_basis": rank_basis,
                                "sign_meaning": _sign_meaning(direction, basis),
                                "estimate": est, "lower": lo, "upper": hi,
                                "units": "percentage points" if units == "%" else units,
                                "interval_position": position, "favoured_model": favoured,
                                "equivalence_margin": margin if rank_basis else None,
                                "equivalence": eq, "confidence_level": level,
                                "n_replicates_valid": len(d_rep),
                                "status": c_status, "status_detail": c_detail,
                                "inference": "exploratory", "p_value": p_value,
                                "flags": _union_flags([flags[a], flags[bm]]),
                                "_test": cid_row})

    # ---- multiplicity: one family per block unit and length of this comparison/subset
    if spec.formal_tests:
        assert spec.multiplicity is not None
        for fam in tests.values():
            adj = adjust_p_values([p for _, p in fam], spec.multiplicity)
            by_test = {t: q for (t, _), q in zip(fam, adj, strict=True)}
            for r in con_rows:
                q = by_test.get(r["_test"])
                if q is not None:
                    r.update({"inference": "formal", "p_value_adjusted": float(q),
                              "multiplicity_policy": spec.multiplicity,
                              "family_size": len(fam),
                              "test_decision": "reject_zero" if q <= alpha else "not_rejected"})

    attrs: dict[str, Any] = {
        "workflow": "etcompare.ranking.rank_comparison", "ranking": to_plain(spec),
        "comparison_id": cid, "subset": subset, "cohort_membership_sha256": fp,
        "ranking_scope": cs.ranking_scope, "model_order": models,
        "bootstrap": to_plain(bs) if bs is not None else None,
        "rank_rule": "mid-ranks of exactly tied lower-is-better values; ties share positions",
        "contrast_convention": "metric(model_a) - metric(model_b); model_b declared earlier",
        "inference": ("formal tests under " + str(spec.multiplicity)) if spec.formal_tests
        else "exploratory: no formal tests requested",
        "interpretation": "station-level description; top_rank_frequency is a bootstrap "
                          "stability measure, not the probability that a model is best",
    }
    rankings = CanonicalTable.build(_frame(rank_rows, MODEL_RANKINGS), MODEL_RANKINGS, attrs,
                                    source=f"{cid}/{spec.ranking_id}")
    dist = CanonicalTable.build(_frame(dist_rows, RANK_DISTRIBUTION), RANK_DISTRIBUTION, attrs,
                                source=f"{cid}/{spec.ranking_id}")
    contrasts = CanonicalTable.build(_frame([{k: v for k, v in r.items() if k != "_test"}
                                             for r in con_rows], MODEL_CONTRASTS),
                                     MODEL_CONTRASTS, attrs, source=f"{cid}/{spec.ranking_id}")
    network = network_summaries(metric_results, spec, subset=subset,
                                bootstrap_id=boot_id)

    f = rankings.frame.drop_duplicates([*_CTX, "metric", "block_length"])
    for (b, st), n in f.groupby(["block_length", "status"]).size().items():
        report.info("ranking_status", f"block length {b}: {n} rankings {st}", field=str(st),
                    n_rows=int(n))
    if (f["status"] == "insufficient_support").any():
        report.warning("insufficient_support", "some rankings have no rank statistics: their "
                       "sample holds fewer than min_blocks blocks (step 10)")
    if not spec.formal_tests:
        report.info("exploratory", "no formal tests requested: every contrast is exploratory")
    nw = network.frame
    if len(nw) and (nw["n_sites"] < 2).all():
        report.warning("single_site_network", "network summaries cover one site: they restate "
                       "station results and are not network evidence")
    return RankingResult(spec, cid, subset, rankings, dist, contrasts, network,
                         tuple(report.issues), attrs)


def _on_basis(x: Any, basis: str) -> Any:
    return np.abs(x) if basis == "absolute_value" else x


def _sign_meaning(direction: str, basis: str) -> str:
    if basis == "absolute_value":
        return "|metric_a| - |metric_b|: negative means model_a is closer to zero (better)"
    if direction == "closest_to_zero":
        return "signed difference of a closest-to-zero metric: the sign does not order the models"
    if direction == "lower":
        return "negative means model_a has the smaller value (better)"
    return "positive means model_a has the larger value (better)"


# --------------------------------------------------------------------------- network

_LABELS = {
    "equal_site_mean": "equal-site mean of station {m} (primary network result)",
    "observation_weighted_mean": "mean of station {m} weighted by observation count",
    "sqrt_observation_weighted_mean": "mean of station {m} weighted by sqrt(observation count)",
    "pooled_rmse": "pooled RMSE over all site observations, sqrt(sum n*RMSE^2 / sum n); not "
                   "the mean station RMSE",
}
_WEIGHTING = {"equal_site_mean": "equal", "observation_weighted_mean": "n_obs",
              "sqrt_observation_weighted_mean": "sqrt_n_obs", "pooled_rmse": "pooled"}


def network_summaries(metrics: MetricResults | CanonicalTable, spec: RankingSpec, *,
                      subset: str = "all", bootstrap_id: str = "none") -> CanonicalTable:
    """Summaries of station metrics across the sites of one cohort (module docstring).

    ``metrics`` is one comparison's ``metrics`` table (or its
    :class:`~etcompare.metrics.MetricResults`); rows of several comparisons or
    metric sets are refused, so unequal cohorts are never pooled.
    """
    table = metrics.table if isinstance(metrics, MetricResults) else metrics
    fp = table.attributes.get("cohort_membership_sha256")
    f = table.frame
    f = f[f["metric"].isin(list(spec.metrics))]
    for c in ("comparison_id", "metric_set_id"):
        if f[c].nunique() > 1:
            raise ContractError(f"network summaries pool one comparison and metric set; rows "
                                f"differ in {c} ({sorted(f[c].unique())})")
    keys = ["comparison_id", "cohort_id", "metric_set_id", *_SCALE, "model",
            "reference_variant", *_SIDE, "metric"]
    rows: list[dict[str, Any]] = []
    for key, g in f.groupby(keys, sort=True):
        k = dict(zip(keys, key, strict=True))
        if g["scope"].duplicated().any():
            raise ContractError(f"network summary {key}: a site appears twice")
        if g["units"].nunique() > 1:
            raise ContractError(f"network summary {key}: station metrics differ in units")
        g = g.sort_values("scope")
        daily = k["temporal_scale"] == "daily"
        n = (g["n_days"] if daily else g["n_periods"]).to_numpy(dtype=float)
        v = g["value"].to_numpy(dtype=float)
        sites = g["scope"].tolist()
        n_sites = len(sites)
        undefined = [f"{s}: {r}" for s, r, x in zip(sites, g["validity_reason"], v, strict=True)
                     if not np.isfinite(x)]
        for st in spec.network_summaries:
            if st == "pooled_rmse" and k["metric"] != "rmse":
                continue
            value = None
            if not undefined:
                if st == "equal_site_mean":
                    value = float(v.mean())
                elif st == "observation_weighted_mean":
                    value = float((n * v).sum() / n.sum())
                elif st == "sqrt_observation_weighted_mean":
                    w = np.sqrt(n)
                    value = float((w * v).sum() / w.sum())
                else:
                    value = float(np.sqrt((n * v ** 2).sum() / n.sum()))
            if undefined:
                status, detail = "estimate_undefined", "; ".join(undefined)
            elif n_sites < 2:
                status = "single_site"
                detail = (f"1 site ({sites[0]}): a station result, not network evidence")
            else:
                status, detail = "ok", None
            rows.append({
                "ranking_id": spec.ranking_id, "subset": subset, "bootstrap_id": bootstrap_id,
                **k, "summary_type": st, "scope_type": "network",
                "summary_label": _LABELS[st].format(m=k["metric"]),
                "primary": st == "equal_site_mean", "weighting": _WEIGHTING[st],
                "observation_unit": "day" if daily else "period", "value": value,
                "units": str(g["units"].iloc[0]), "n_sites": n_sites, "sites": ";".join(sites),
                "n_days": int(g["n_days"].sum()),
                "n_days_reference_filled": int(g["n_days_reference_filled"].sum()),
                "n_periods": None if daily else int(g["n_periods"].fillna(0).sum()),
                "network_evidence": n_sites >= 2, "status": status, "status_detail": detail,
                "cohort_membership_sha256": fp,
                "flags": _union_flags([*g["flags"], "not_network_evidence"
                                       if n_sites < 2 else None]),
            })
    attrs = {"workflow": "etcompare.ranking.network_summaries", "ranking_id": spec.ranking_id,
             "subset": subset, "cohort_membership_sha256": fp,
             "primary": "equal_site_mean",
             "observation_count": "n_days for daily pairs, n_periods for period products",
             "limitation": "no interval: site-population inference needs hierarchical "
                           "site x calendar-block resampling, which is not implemented"}
    return CanonicalTable.build(_frame(rows, NETWORK_SUMMARIES), NETWORK_SUMMARIES, attrs,
                                source=f"network {spec.ranking_id}")


# --------------------------------------------------------------------------- sensitivity

_DIMENSIONS = {"station": ["scope"], "year": ["subset"], "temporal_scale": _SCALE,
               "reference_variant": ["reference_variant"], "extraction_method": _SIDE}
_POINT_CTX = ["ranking_id", "comparison_id", "cohort_id", "subset", "scope", *_SCALE,
              "reference_variant", *_SIDE]


def _label(c: Mapping[str, Any]) -> str:
    return (f"{c['comparison_id']}|{c['subset']}|{c['scope']}|{c['temporal_scale']}:"
            f"{c['period_type']}:{c['aggregation_id']}:{c['product']}|"
            f"{c['reference_variant']}|{c['extraction_method']}@{c['footprint_set_id']}")


def ranking_sensitivity(rankings: Iterable[RankingResult | CanonicalTable]) -> CanonicalTable:
    """Point-rank changes between ranking contexts that differ in exactly one dimension.

    Contexts come from ``model_rankings`` tables, each an internally frozen
    cohort. A pair is compared only when it ranks the same models, belongs to
    one ranking and metric, and differs in exactly one of station, year subset
    (both subsets must be years), temporal scale/product (one side daily), reference
    variant or extraction side; pairs that differ only in their cohort get the
    dimension ``cohort``. Anything else is confounded and skipped.
    """
    frames = [r.rankings.frame if isinstance(r, RankingResult) else r.frame for r in rankings]
    f = pd.concat(frames, ignore_index=True) if frames else MODEL_RANKINGS.empty()
    f = f[f["rank"].notna()].drop_duplicates([*_POINT_CTX, "metric", "model"])
    ctxs = []
    for key, g in f.groupby([*_POINT_CTX, "metric"], sort=True):
        c = dict(zip([*_POINT_CTX, "metric"], key, strict=True))
        c["ranks"] = dict(zip(g["model"], g["rank"].astype(float), strict=True))
        c["fp"] = g["cohort_membership_sha256"].iloc[0]
        c["n_days"] = int(g["n_days"].iloc[0])
        c["n_periods"] = _int(g["n_periods"].iloc[0])
        c["label"] = _label(c)
        ctxs.append(c)
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for c in ctxs:
        groups.setdefault((c["ranking_id"], c["metric"]), []).append(c)
    rows: list[dict[str, Any]] = []
    for (rid, metric), cs in groups.items():
        for x in range(len(cs)):
            for y in range(x + 1, len(cs)):
                a, b = cs[x], cs[y]
                if set(a["ranks"]) != set(b["ranks"]):
                    continue
                diff = [d for d, cols in _DIMENSIONS.items() if any(a[c] != b[c] for c in cols)]
                if not diff:
                    if a["fp"] == b["fp"]:
                        continue
                    dim = "cohort"
                elif len(diff) == 1:
                    dim = diff[0]
                else:
                    continue
                if dim == "year" and "all" in (a["subset"], b["subset"]):
                    continue
                if dim == "temporal_scale":
                    if "daily" not in (a["temporal_scale"], b["temporal_scale"]):
                        continue
                    if b["temporal_scale"] == "daily":
                        a, b = b, a
                for m in sorted(a["ranks"]):
                    rows.append({
                        "ranking_id": rid, "dimension": dim, "context_a": a["label"],
                        "context_b": b["label"], "metric": metric, "model": m,
                        "rank_a": a["ranks"][m], "rank_b": b["ranks"][m],
                        "rank_change": b["ranks"][m] - a["ranks"][m],
                        "n_models": len(a["ranks"]), "same_cohort": a["fp"] == b["fp"],
                        "comparison_a": a["comparison_id"], "comparison_b": b["comparison_id"],
                        "n_days_a": a["n_days"], "n_days_b": b["n_days"],
                        "n_periods_a": a["n_periods"], "n_periods_b": b["n_periods"]})
    present = {d for r in rows for d in [r["dimension"]]}
    attrs = {"workflow": "etcompare.ranking.ranking_sensitivity",
             "dimensions_present": sorted(present),
             "dimensions_absent": sorted(set(_DIMENSIONS) - present),
             "rule": "point ranks only; each context is an internally frozen cohort; contexts "
                     "differing in more than one dimension are skipped"}
    return CanonicalTable.build(_frame(rows, RANKING_SENSITIVITY), RANKING_SENSITIVITY, attrs,
                                source="ranking sensitivity")
