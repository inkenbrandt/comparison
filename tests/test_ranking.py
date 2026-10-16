"""Rankings and paired model contrasts (guide step 11).

Expected values are computed independently in the tests: ranks with
scipy.stats.rankdata on the step-10 replicate arrays, contrasts and intervals
with numpy on those arrays, metrics of concatenated or duplicated records with
numpy, and Holm/Bonferroni adjustments by hand.
"""

import numpy as np
import pandas as pd
import pytest
from scipy.stats import rankdata

from etcompare.aggregation import aggregate_cohort
from etcompare.bootstrap import bootstrap_comparison
from etcompare.cohorts import build_cohort
from etcompare.config import (
    RANKING_DIRECTIONS,
    BootstrapSpec,
    ComparisonSpec,
    ExtractionSide,
    FootprintSetSpec,
    MetricSpec,
    NearZeroTolerance,
    PeriodAggregationSpec,
    ProductSpec,
    RankingSpec,
    config_from_dict,
)
from etcompare.errors import ContractError
from etcompare.io.tables import read_table, write_table
from etcompare.metrics import METRIC_DEFINITIONS, compute_metrics, score_comparison
from etcompare.ranking import (
    adjust_p_values,
    check_rankable,
    network_summaries,
    rank_comparison,
    rank_positions,
    ranking_sensitivity,
    year_subsets,
)
from etcompare.schemas import CanonicalTable

TOL = NearZeroTolerance(mean=1e-9, std=1e-12)
FP = ExtractionSide("footprint_conservative", "SET")
PIX = ExtractionSide("pixel_window_3x3", "none")
SETS = [FootprintSetSpec("SET", "x", weighting_method="nldas",
                         value_semantics="normalized_cell_weight",
                         temporal_weighting="reference_et_weighted")]
MODELS = ("aaa", "bbb", "ccc")
PRODUCTS = {m: ProductSpec(m, "mm/day", "unknown", "interpolated_daily") for m in MODELS}
METRICS = ("mbe", "mae", "rmse", "bias_corrected_rmse", "pearson_r", "nse")
RANKED = {"mae": "lower", "rmse": "lower", "mbe": "closest_to_zero", "pearson_r": "higher",
          "nse": "higher"}


def ext(model, dates, vals, side=FP, site="US-AAA"):
    n = len(dates)
    vals = np.broadcast_to(np.asarray(vals, dtype=float), (n,))
    return pd.DataFrame({
        "site_id": site, "date": dates, "period": "day", "model": model,
        "extraction_method": side.extraction_method, "footprint_set_id": side.footprint_set_id,
        "footprint_method": "m", "et_mm": vals, "fp_weight_sum": 1.0,
        "fp_weight_retained_frac": 1.0 if side.footprint_set_id != "none" else np.nan,
        "fp_weight_retained_denominator": None, "support_id": None, "day_alignment": "declared",
        "model_asset_id": None, "footprint_asset_id": None, "source_path": None,
    })


def station(dates, vals, site="US-AAA", corrected=None):
    vals = np.asarray(vals, dtype=float)
    return pd.DataFrame({
        "site_id": site, "date": dates, "et_raw_mm": vals,
        "et_corrected_mm": vals if corrected is None else np.asarray(corrected, dtype=float),
        "gap_filled": False, "raw_available": True, "reference_provenance": "corrected_from_raw",
    })


def cspec(kind="common_date", models=MODELS, variants=("et_corrected_inclusive",), sides=(FP,),
          cid="cmp", **kw):
    return ComparisonSpec(cid, kind, models, variants, sides, **kw)


def mspec(metrics=METRICS, **kw):
    kw.setdefault("daily_tolerance", TOL)
    return MetricSpec("mset", metrics=metrics, **kw)


def bspec(**kw):
    base = dict(bootstrap_id="bs", metric_set="mset", block_lengths=(1, 7), seed=7,
                n_replicates=300, confidence_level=0.9, min_blocks=5, segment_boundary="none")
    base.update(kw)
    return BootstrapSpec(**base)


def rspec(**kw):
    base = dict(ranking_id="rk", metric_set="mset", bootstrap="bs", metrics=RANKED,
                network_summaries=("equal_site_mean", "observation_weighted_mean",
                                   "sqrt_observation_weighted_mean", "pooled_rmse"))
    base.update(kw)
    return RankingSpec(**base)


def run(obs, frames, spec=None, m=None, b=None, r=None, period_totals=(), subset="all"):
    c = build_cohort(obs, frames, spec or cspec(), footprint_sets=SETS)
    m = m or mspec()
    res = score_comparison(c, m, products=PRODUCTS, period_totals=period_totals)
    boot = None if b is False else bootstrap_comparison(c, m, b or bspec(), products=PRODUCTS,
                                                        period_totals=period_totals,
                                                        metric_results=res)
    return c, res, boot, rank_comparison(c, res, boot, r or rspec(), subset=subset)


def sel(table, **match):
    f = table.frame
    for k, v in match.items():
        f = f[f[k] == v]
    return f.reset_index(drop=True)


def gamma(n, seed=0):
    return np.random.default_rng(seed).gamma(3.0, 1.0, n)


DAYS = pd.date_range("2025-01-01", periods=150)


def advantage_case(k=(1.0, 1.02, 3.0)):
    """aaa has a known small error advantage: errors k * e with a common e."""
    o = gamma(len(DAYS))
    e = np.random.default_rng(3).normal(0.4, 1.0, len(DAYS))
    return o, e, [ext(m, DAYS, o + f * e) for m, f in zip(MODELS, k, strict=True)]


# --------------------------------------------------------------------------- directions

def test_directions_are_declared_once_for_exactly_the_score_metrics():
    score = {m for m, d in METRIC_DEFINITIONS.items() if d.role == "score"}
    assert set(RANKING_DIRECTIONS) == score
    assert {m for m, d in RANKING_DIRECTIONS.items() if d == "lower"} == {
        "mae", "rmse", "nrmse_mean", "nrmse_mean_percent"}
    assert {m for m, d in RANKING_DIRECTIONS.items() if d == "higher"} == {
        "pearson_r", "pearson_r_squared", "nse", "kge_2009"}
    assert {m for m, d in RANKING_DIRECTIONS.items() if d == "closest_to_zero"} == {
        "mbe", "percent_bias"}


def test_rank_positions_ties_share_positions():
    mid, share = rank_positions(np.array([[1.0], [1.0], [3.0], [0.5]]))
    assert mid[:, 0].tolist() == [2.5, 2.5, 4.0, 1.0]
    assert mid[:, 0].tolist() == rankdata([1.0, 1.0, 3.0, 0.5]).tolist()
    assert share[0, :, 0].tolist() == [0, 0.5, 0.5, 0] and share[3, :, 0].tolist() == [1, 0, 0, 0]
    assert np.allclose(share.sum(axis=0), 1) and np.allclose(share.sum(axis=1), 1)


# --------------------------------------------------------------------------- gates

def test_known_small_error_advantage_has_documented_sign_and_rank():
    o, e, frames = advantage_case()
    _, res, boot, rk = run(station(DAYS, o), frames)
    mae_a = np.mean(np.abs(e))
    con = sel(rk.contrasts, metric="mae", model_a="bbb", model_b="aaa", block_length=7).iloc[0]
    # contrast = MAE_bbb - MAE_aaa = 0.02 * mean|e| > 0: positive means model_a (bbb) is worse
    assert con["estimate"] == pytest.approx(0.02 * mae_a)
    assert con["status"] == "ok" and con["lower"] > 0 and con["interval_position"] == "above_zero"
    assert con["favoured_model"] == "aaa" and "negative means model_a" in con["sign_meaning"]
    nse = sel(rk.contrasts, metric="nse", model_a="bbb", model_b="aaa", block_length=7).iloc[0]
    assert nse["estimate"] < 0 and nse["upper"] < 0 and nse["favoured_model"] == "aaa"
    mbe = sel(rk.contrasts, metric="mbe", model_a="bbb", model_b="aaa", block_length=7)
    assert set(mbe["contrast_basis"]) == {"value", "absolute_value"}
    absolute = mbe[mbe["contrast_basis"] == "absolute_value"].iloc[0]
    signed = mbe[mbe["contrast_basis"] == "value"].iloc[0]
    assert absolute["estimate"] == pytest.approx(0.02 * abs(e.mean()))
    assert absolute["favoured_model"] == "aaa" and absolute["ranking_basis"]
    assert signed["favoured_model"] is pd.NA and not signed["ranking_basis"]
    for metric in ("mae", "rmse", "nse", "mbe"):
        r = sel(rk.rankings, metric=metric, block_length=7).set_index("model")
        assert r["rank"].to_dict() == {"aaa": 1.0, "bbb": 2.0, "ccc": 3.0}, metric
        assert r.loc["aaa", "top_rank_frequency"] == 1.0  # every replicate: errors scale by k
        assert set(r["leader_status"]) == {"unique_leader"} and r.loc["aaa", "leading_models"] \
            == "aaa"


def test_rank_statistics_match_replicates():
    o = gamma(len(DAYS))
    rng = np.random.default_rng(11)
    frames = [ext(m, DAYS, o + rng.normal(0, s, len(DAYS))) for m, s in
              zip(MODELS, (1.0, 1.05, 1.1), strict=True)]
    _, res, boot, rk = run(station(DAYS, o), frames)
    idx, vals = boot.replicates()
    for metric, sign in (("rmse", 1), ("pearson_r", -1)):
        g = idx[(idx["metric"] == metric) & (idx["block_length"] == 7)]
        reps = np.vstack([vals[g.index[g["model"] == m][0]] for m in MODELS])
        ranks = np.column_stack([rankdata(sign * reps[:, r]) for r in range(reps.shape[1])])
        r = sel(rk.rankings, metric=metric, block_length=7).set_index("model").loc[list(MODELS)]
        assert np.allclose(r["mean_rank"], ranks.mean(axis=1))
        assert np.allclose(r["top_rank_frequency"], (ranks == 1).mean(axis=1))
        assert r["top_rank_frequency"].sum() == pytest.approx(1.0)
        assert np.allclose(r["rank_lower"], np.quantile(ranks, 0.05, axis=1, method="lower"))
        assert np.allclose(r["rank_upper"], np.quantile(ranks, 0.95, axis=1, method="higher"))
        d = sel(rk.rank_distribution, metric=metric, block_length=7)
        for m in MODELS:
            got = d[d["model"] == m].set_index("position")["frequency"]
            want = [(ranks[MODELS.index(m)] == p).mean() for p in (1, 2, 3)]
            assert np.allclose(got.loc[[1, 2, 3]], want)
        assert np.allclose(d.groupby("position")["frequency"].sum(), 1)
        # contrast intervals from the same replicates, ccc - aaa
        c = sel(rk.contrasts, metric=metric, model_a="ccc", model_b="aaa", block_length=7).iloc[0]
        diff = reps[2] - reps[0]
        assert (c["lower"], c["upper"]) == pytest.approx(tuple(np.quantile(diff, [0.05, 0.95])))


def test_identical_models_give_zero_contrasts_and_documented_ties():
    o, e, _ = advantage_case()
    p = o + e
    frames = [ext("aaa", DAYS, p), ext("bbb", DAYS, p), ext("ccc", DAYS, o + 2 * e)]
    _, res, boot, rk = run(station(DAYS, o), frames, r=rspec(equivalence_margins={"mae": 0.05}))
    idx, vals = boot.replicates()
    for key, g in idx.groupby(["block_length", "metric"]):
        a = vals[g.index[g["model"] == "aaa"][0]]
        assert (vals[g.index[g["model"] == "bbb"][0]] - a == 0).all(), key  # every replicate
    con = sel(rk.contrasts, model_a="bbb", model_b="aaa")
    assert (con["status"] == "ok").all()
    assert (con[["estimate", "lower", "upper"]] == 0).all().all()
    mae = con[con["metric"] == "mae"]
    assert set(mae["equivalence"]) == {"equivalent"}
    for metric in ("mae", "rmse", "nse"):
        r = sel(rk.rankings, metric=metric, block_length=7).set_index("model")
        # tied models share positions 1 and 2: mid-rank 1.5, half of position 1 each
        assert r.loc[["aaa", "bbb"], "rank"].tolist() == [1.5, 1.5]
        assert r.loc[["aaa", "bbb"], "n_tied"].tolist() == [2, 2] and r.loc["ccc", "rank"] == 3
        assert r.loc[["aaa", "bbb"], "top_rank_frequency"].tolist() == [0.5, 0.5]
        assert set(r["leader_status"]) == {"no_unique_leader"}
        assert r.loc["aaa", "leading_models"] == "aaa;bbb"
        d = sel(rk.rank_distribution, metric=metric, block_length=7, model="aaa")
        assert d.set_index("position")["frequency"].to_dict() == {1: 0.5, 2: 0.5, 3: 0.0}


def test_overlapping_intervals_give_no_unique_winner():
    o = gamma(len(DAYS))
    rng = np.random.default_rng(21)
    frames = [ext(m, DAYS, o + rng.normal(0, 1.0, len(DAYS))) for m in MODELS]
    _, res, boot, rk = run(station(DAYS, o), frames)
    for b in (1, 7):
        r = sel(rk.rankings, metric="mae", block_length=b).set_index("model")
        best = r["rank"].idxmin()
        con = sel(rk.contrasts, metric="mae", block_length=b)
        vs_best = con[(con["model_a"] == best) | (con["model_b"] == best)]
        assert (vs_best["interval_position"] == "includes_zero").any()  # precondition
        assert set(r["leader_status"]) == {"no_unique_leader"}
        assert len(r["leading_models"].iloc[0].split(";")) >= 2
        assert r["top_rank_frequency"].sum() == pytest.approx(1.0)
        assert (r["top_rank_frequency"] < 1).all()
        # an interval crossing zero is not evidence of equivalence
        assert set(con["equivalence"]) == {"not_assessed"}


def test_equivalence_needs_a_declared_margin():
    o, e, frames = advantage_case()
    _, _, _, rk = run(station(DAYS, o), frames)
    c = rk.contrasts.frame
    assert set(c.loc[c["ranking_basis"].astype(bool), "equivalence"]) == {"not_assessed"}
    assert set(c.loc[~c["ranking_basis"].astype(bool), "equivalence"]) == {"not_applicable"}
    assert c["equivalence_margin"].isna().all()
    row = sel(rk.contrasts, metric="mae", model_a="bbb", model_b="aaa", block_length=7).iloc[0]
    lo, hi = row["lower"], row["upper"]
    for margin, want in ((lo / 2, "practically_different"), (2 * hi, "equivalent"),
                         ((lo + hi) / 2, "inconclusive")):
        _, _, _, rk2 = run(station(DAYS, o), frames,
                           r=rspec(equivalence_margins={"mae": float(margin)}))
        got = sel(rk2.contrasts, metric="mae", model_a="bbb", model_b="aaa", block_length=7)
        assert got["equivalence"].iloc[0] == want and got["equivalence_margin"].iloc[0] == margin


def test_no_ranking_pools_unequal_cohorts():
    o, e, frames = advantage_case()
    obs = station(DAYS, o, corrected=o * 1.1)
    c1, r1, _, _ = run(obs, frames, b=False)
    c2, r2, _, _ = run(obs, frames[:2] + [ext("ccc", DAYS[1:], (o + 3 * e)[1:])], b=False)
    assert c1.fingerprint != c2.fingerprint

    def rows(res, c):
        f = res.table.frame.assign(cohort_membership_sha256=c.fingerprint)
        return f[(f["metric"] == "mae") & (f["product"] == "daily_pairs")]

    a, b = rows(r1, c1), rows(r2, c2)
    mixed = pd.concat([a[a["model"] != "ccc"], b[b["model"] == "ccc"]])
    with pytest.raises(ContractError, match="differ in cohort_membership_sha256"):
        check_rankable(mixed)
    # reference variants, extraction methods and period products
    for col, other in (("reference_variant", "et_corrected_measured"),
                       ("extraction_method", "pixel_window_3x3"),
                       ("product", "partial_sum")):
        bad = a.copy()
        bad.loc[bad["model"] == "ccc", col] = other
        with pytest.raises(ContractError, match=f"differ in {col}"):
            check_rankable(bad)
    check_rankable(a)  # one frozen context is fine
    # metric results of another cohort
    with pytest.raises(ContractError, match="fingerprint mismatch"):
        rank_comparison(c1, r2, None, rspec())
    # coverage and paired-method cohorts are never ranked
    for kind, kw in (("model_available", {"models": ("aaa",)}),
                     ("paired_method", {"sides": (FP, PIX)})):
        fr = frames + [ext(m, DAYS, o, side=PIX) for m in MODELS]
        c = build_cohort(obs, fr, cspec(kind, **kw), footprint_sets=SETS)
        res = score_comparison(c, mspec(), products=PRODUCTS)
        with pytest.raises(ContractError, match="ranking scope 'none'"):
            rank_comparison(c, res, None, rspec())


def test_diagnostic_metrics_cannot_be_ranked():
    for m in ("bias_corrected_rmse", "mape", "ols_slope", "kge_2009_alpha"):
        with pytest.raises(ContractError, match="cannot be ranked"):
            rspec(metrics={m: "lower"})
    o, e, frames = advantage_case()
    _, res, _, _ = run(station(DAYS, o), frames, b=False)
    f = res.table.frame.assign(cohort_membership_sha256="x")
    with pytest.raises(ContractError, match="role diagnostic"):
        check_rankable(f[(f["metric"] == "bias_corrected_rmse")
                         & (f["product"] == "daily_pairs")])


def test_pairwise_contrast_equals_step10_interval():
    o, e, frames = advantage_case()
    _, _, boot, rk = run(station(DAYS, o), frames[:2], spec=cspec("pairwise_model",
                                                                    models=("aaa", "bbb")))
    iv = boot.intervals.frame
    iv = iv[iv["contrast_dimension"] == "model"].set_index(["metric", "block_length"])
    c = sel(rk.contrasts, contrast_basis="value").set_index(["metric", "block_length"])
    assert set(c["model_a"]) == {"bbb"} and set(c["model_b"]) == {"aaa"}
    for k, row in c.iterrows():
        assert row[["estimate", "lower", "upper"]].tolist() == pytest.approx(
            iv.loc[k, ["estimate", "lower", "upper"]].tolist())
    assert set(rk.rankings.frame["ranking_scope"]) == {"pairwise"}


def test_statuses_carry_through():
    o, e, frames = advantage_case()
    frames[2] = ext("ccc", DAYS, 2.0)  # constant model: pearson_r undefined
    _, _, _, rk = run(station(DAYS, o), frames, b=bspec(block_lengths=(7, 50), min_blocks=5))
    r = sel(rk.rankings, metric="pearson_r", block_length=7)
    assert set(r["status"]) == {"estimate_undefined"} and r["rank"].isna().all()
    assert "ccc: constant_modeled" in r["status_detail"].iloc[0]
    con = sel(rk.contrasts, metric="pearson_r", block_length=7)
    assert set(con.loc[con["model_a"] == "ccc", "status"]) == {"estimate_undefined"}
    assert set(con.loc[con["model_a"] == "bbb", "status"]) == {"ok"}  # defined pair still ok
    # 150 days hold 3 disjoint 50-day blocks < min_blocks 5
    r50 = sel(rk.rankings, metric="mae", block_length=50)
    assert set(r50["status"]) == {"insufficient_support"}
    assert r50["rank"].notna().all()  # the point rank is the step-9 estimate
    assert r50[["mean_rank", "rank_lower", "rank_upper", "top_rank_frequency"]].isna().all().all()
    assert set(r50["leader_status"]) == {"not_assessed"}
    assert sel(rk.contrasts, block_length=50)["lower"].isna().all()
    assert not len(sel(rk.rank_distribution, block_length=50))
    # without a bootstrap: point ranks only
    _, _, _, rk0 = run(station(DAYS, o), frames, b=False)
    assert set(rk0.rankings.frame["status"]) <= {"not_resampled", "estimate_undefined"}
    assert set(rk0.rankings.frame["block_length"]) == {0}


def test_formal_tests_need_a_policy_and_are_adjusted():
    assert adjust_p_values([0.01, 0.04, 0.03, 0.005], "holm").tolist() == pytest.approx(
        [0.03, 0.06, 0.06, 0.02])
    assert adjust_p_values([0.01, 0.04, 0.03, 0.005], "bonferroni").tolist() == pytest.approx(
        [0.04, 0.16, 0.12, 0.02])
    o, e, frames = advantage_case()
    _, _, boot, rk = run(station(DAYS, o), frames,
                         r=rspec(formal_tests=True, multiplicity="holm"))
    c = rk.contrasts.frame
    formal = c[c["inference"] == "formal"]
    assert len(formal) and formal["ranking_basis"].astype(bool).all()
    assert set(c.loc[~c["ranking_basis"].astype(bool), "inference"]) == {"exploratory"}
    one = formal[(formal["block_length"] == 7)]
    assert set(one["family_size"]) == {one.drop_duplicates(
        [k for k in c.columns if k not in ("interval_method",)]).shape[0]}
    row = sel(rk.contrasts, metric="mae", model_a="bbb", model_b="aaa", block_length=7).iloc[0]
    assert row["p_value"] == 0.0 and row["test_decision"] == "reject_zero"
    _, _, _, rk_x = run(station(DAYS, o), frames)
    assert set(rk_x.contrasts.frame["inference"]) == {"exploratory"}
    assert rk_x.contrasts.frame["p_value"].isna().all()


# --------------------------------------------------------------------------- network

def two_sites(n_a=120, n_b=80):
    da, db = pd.date_range("2025-01-01", periods=n_a), pd.date_range("2025-03-01", periods=n_b)
    oa, ob = gamma(n_a, 1), gamma(n_b, 2)
    ea = np.random.default_rng(4).normal(0.2, 0.8, n_a)
    eb = np.random.default_rng(5).normal(-0.1, 1.5, n_b)
    obs = [station(da, oa, site="US-AAA"), station(db, ob, site="US-BBB")]
    frames = [ext(m, da, oa + k * ea, site="US-AAA") for m, k in zip(MODELS, (1, 2, 3),
                                                                     strict=True)]
    frames += [ext(m, db, ob + k * eb, site="US-BBB") for m, k in zip(MODELS, (1, 2, 3),
                                                                      strict=True)]
    return (oa, ea), (ob, eb), obs, frames


def test_duplicating_a_station_record_leaves_equal_site_summary_unchanged():
    (oa, ea), (ob, eb), obs, frames = two_sites()
    _, res, _, _ = run(obs, frames, b=False)
    t = res.table
    # duplicating site A's record (every day twice) leaves its station metric unchanged...
    dup = compute_metrics(np.tile(oa, 2), np.tile(oa + ea, 2), tolerance=TOL, metrics=METRICS)
    orig = compute_metrics(oa, oa + ea, tolerance=TOL, metrics=METRICS)
    for m in RANKED:
        assert dup[m].value == pytest.approx(orig[m].value, rel=1e-12)
    # ...and doubles its observation count
    f2 = t.frame.copy()
    f2.loc[f2["scope"] == "US-AAA", "n_days"] *= 2
    t2 = CanonicalTable(t.schema, f2, dict(t.attributes))
    s1 = network_summaries(t, rspec()).frame
    s2 = network_summaries(t2, rspec()).frame

    def get(s, st, metric="mae", model="aaa"):
        return s[(s["summary_type"] == st) & (s["metric"] == metric) & (s["model"] == model)
                 & (s["product"] == "daily_pairs")].iloc[0]

    mae_a, mae_b = np.mean(np.abs(ea)), np.mean(np.abs(eb))
    assert get(s1, "equal_site_mean")["value"] == pytest.approx((mae_a + mae_b) / 2)
    assert get(s2, "equal_site_mean")["value"] == get(s1, "equal_site_mean")["value"]
    assert get(s1, "observation_weighted_mean")["value"] == pytest.approx(
        (120 * mae_a + 80 * mae_b) / 200)
    assert get(s2, "observation_weighted_mean")["value"] == pytest.approx(
        (240 * mae_a + 80 * mae_b) / 320)
    assert get(s2, "observation_weighted_mean")["value"] != pytest.approx(
        get(s1, "observation_weighted_mean")["value"])
    w = np.sqrt([120, 80])
    assert get(s1, "sqrt_observation_weighted_mean")["value"] == pytest.approx(
        (w[0] * mae_a + w[1] * mae_b) / w.sum())
    assert get(s1, "equal_site_mean")["primary"] and get(s1, "equal_site_mean")["n_sites"] == 2
    # pooled RMSE is the RMSE over all site-days, not the mean station RMSE
    pooled = get(s1, "pooled_rmse", "rmse")
    all_e = np.concatenate([ea, eb])
    assert pooled["value"] == pytest.approx(np.sqrt(np.mean(all_e ** 2)))
    mean_rmse = get(s1, "equal_site_mean", "rmse")["value"]
    assert mean_rmse == pytest.approx((np.sqrt(np.mean(ea ** 2)) + np.sqrt(np.mean(eb ** 2))) / 2)
    assert pooled["value"] != pytest.approx(mean_rmse)
    assert "not the mean station RMSE" in pooled["summary_label"]
    assert not (s1["metric"] != "rmse").to_numpy()[s1["summary_type"] == "pooled_rmse"].any()
    # a copy counted as a separate site is a new site: the equal-site mean then changes
    f3 = pd.concat([t.frame, t.frame[t.frame["scope"] == "US-AAA"].assign(scope="US-AA2")])
    s3 = network_summaries(CanonicalTable(t.schema, f3, dict(t.attributes)), rspec()).frame
    assert get(s3, "equal_site_mean")["n_sites"] == 3
    assert get(s3, "equal_site_mean")["value"] == pytest.approx((2 * mae_a + mae_b) / 3)


def test_single_site_network_summary_is_not_network_evidence():
    o, e, frames = advantage_case()
    _, res, _, rk = run(station(DAYS, o), frames)
    n = rk.network.frame
    assert (n["n_sites"] == 1).all() and not n["network_evidence"].astype(bool).any()
    assert set(n["status"]) == {"single_site"}
    assert n["flags"].str.contains("not_network_evidence").all()
    assert "single_site_network" in rk.report.codes()
    s = n[(n["summary_type"] == "equal_site_mean") & (n["metric"] == "mae")
          & (n["model"] == "aaa")].iloc[0]
    assert s["value"] == pytest.approx(np.mean(np.abs(e)))
    with pytest.raises(ContractError, match="differ in comparison_id"):
        f = res.table.frame
        network_summaries(CanonicalTable(res.table.schema, pd.concat(
            [f, f.assign(comparison_id="other")])), rspec())


def test_undefined_site_metric_leaves_network_summary_undefined():
    (oa, ea), (ob, eb), obs, frames = two_sites()
    frames = [f if not (f["model"].iloc[0] == "ccc" and f["site_id"].iloc[0] == "US-BBB")
              else f.assign(et_mm=2.0) for f in frames]
    _, res, _, _ = run(obs, frames, b=False)
    s = network_summaries(res, rspec()).frame
    r = s[(s["metric"] == "pearson_r") & (s["model"] == "ccc")]
    assert set(r["status"]) == {"estimate_undefined"} and r["value"].isna().all()
    assert "US-BBB: constant_modeled" in r["status_detail"].iloc[0]


# --------------------------------------------------------------------------- sensitivity

def test_ranking_sensitivity_across_frozen_contexts():
    d = pd.date_range("2024-07-01", "2025-06-30")
    o = gamma(len(d), 9)
    e = np.random.default_rng(6).normal(0.3, 1.0, len(d))
    frames = [ext(m, d, o + k * e) for m, k in zip(MODELS, (1.0, 1.5, 0.7), strict=True)]
    obs = station(d, o, corrected=o * 1.25)
    spec = cspec(variants=("et_raw", "et_corrected_measured"))
    agg = PeriodAggregationSpec("agg", ("month",))
    c = build_cohort(obs, frames, spec, footprint_sets=SETS)
    t = aggregate_cohort(c, agg, products=PRODUCTS)
    m = mspec(period_aggregations=("agg",), period_products=("complete_total",),
              period_tolerance=TOL)
    b = bspec(period_block_lengths=(1,))
    _, _, _, rk = run(obs, frames, spec=spec, m=m, b=b, period_totals=[t])
    results = [rk]
    subsets = year_subsets(c)
    assert [s[0] for s in subsets] == ["year=2024", "year=2025"]
    assert subsets[0][1:] == ("2024-01-01", "2024-12-31")
    from dataclasses import replace
    for label, start, end in subsets:
        _, _, _, r = run(obs, frames, spec=replace(spec, start=start, end=end), subset=label)
        results.append(r)
    sens = ranking_sensitivity(results)
    f = sens.frame
    assert {"reference_variant", "temporal_scale", "year"} <= set(f["dimension"])
    assert set(sens.attributes["dimensions_absent"]) == {"station", "extraction_method"}
    rv = f[(f["dimension"] == "reference_variant") & (f["metric"] == "mae")]
    assert rv["same_cohort"].astype(bool).all()
    assert rv["context_a"].str.contains(r"\|all\|.*\|daily:").any()  # daily and monthly pairs
    assert rv["context_a"].str.contains("complete_total").any()
    yr = f[(f["dimension"] == "year") & (f["metric"] == "mae")]
    assert not yr["same_cohort"].astype(bool).any()
    ts = f[f["dimension"] == "temporal_scale"]
    assert ts["context_a"].str.contains(r"\|daily:").all()
    # every row's ranks are the point ranks of the two contexts
    r0 = rk.rankings.frame.drop_duplicates(["reference_variant", "temporal_scale", "metric",
                                            "model"])
    one = rv.iloc[0]
    want_b = r0[(r0["reference_variant"] == "et_raw") & (r0["temporal_scale"] == "daily")
                & (r0["metric"] == "mae") & (r0["model"] == one["model"])]["rank"].iloc[0]
    assert {one["rank_a"], one["rank_b"]} >= {want_b}
    assert (f["rank_change"] == f["rank_b"] - f["rank_a"]).all()


# --------------------------------------------------------------------------- tables

def test_tables_round_trip_through_parquet(tmp_path):
    o, e, frames = advantage_case()
    _, _, _, rk = run(station(DAYS, o), frames, r=rspec(formal_tests=True, multiplicity="holm",
                                                        equivalence_margins={"mae": 0.1}))
    sens = ranking_sensitivity([rk])
    for name, t in (("rankings", rk.rankings), ("dist", rk.rank_distribution),
                    ("contrasts", rk.contrasts), ("network", rk.network), ("sens", sens)):
        assert len(t) or name == "sens"
        back = read_table(write_table(t, tmp_path / f"{name}.parquet"))
        assert back.schema is t.schema
        pd.testing.assert_frame_equal(back.frame, t.frame)
        assert back.attributes["workflow"] == t.attributes["workflow"]
    keys = rk.contrasts.schema.primary_key
    assert {"cohort_id", "block_length", "product", "subset", "interval_method"} <= set(keys)


def test_result_is_immutable():
    o, e, frames = advantage_case()
    _, _, _, rk = run(station(DAYS, o), frames)
    t = rk.rankings
    t.frame.loc[0, "rank"] = 99
    assert t.frame.loc[0, "rank"] == 99 and rk.rankings.frame.loc[0, "rank"] != 99
    with pytest.raises(TypeError):
        rk.attributes["x"] = 1  # type: ignore[index]


# --------------------------------------------------------------------------- config

BASE = {
    "stations": {"US-AAA": {"timezone": "UTC-07:00", "day_definition": "local_standard_time",
                            "interval_label": "end"}},
    "products": {m: {"units": "mm/day", "day_definition": "unknown",
                     "temporal_support": "interpolated_daily"} for m in MODELS},
    "comparisons": [
        {"comparison_id": "rank", "kind": "common_date", "models": list(MODELS),
         "reference_variants": ["et_corrected_inclusive"],
         "sides": [{"extraction_method": "footprint_conservative", "footprint_set_id": "none"}]},
        {"comparison_id": "cov", "kind": "model_available", "models": list(MODELS),
         "reference_variants": ["et_corrected_inclusive"],
         "sides": [{"extraction_method": "footprint_conservative", "footprint_set_id": "none"}]},
    ],
    "metric_sets": [{"metric_set_id": "m", "metrics": ["mbe", "mae", "rmse", "pearson_r",
                                                       "bias_corrected_rmse"],
                     "daily_tolerance": {"mean": 0.01, "std": 0.001}},
                    {"metric_set_id": "m2", "daily_tolerance": {"mean": 0.01, "std": 0.001}}],
    "bootstrap": [{"bootstrap_id": "b", "metric_set": "m", "block_lengths": [7], "seed": 1,
                   "n_replicates": 10, "confidence_level": 0.95, "min_blocks": 2,
                   "segment_boundary": "none"},
                  {"bootstrap_id": "b2", "metric_set": "m2", "block_lengths": [7], "seed": 1,
                   "n_replicates": 10, "confidence_level": 0.95, "min_blocks": 2,
                   "segment_boundary": "none"}],
}
GOOD = {"ranking_id": "r", "metric_set": "m", "bootstrap": "b",
        "metrics": {"mae": "lower", "rmse": "lower", "mbe": "closest_to_zero"},
        "comparisons": ["rank"]}


def test_ranking_config():
    cfg = config_from_dict({**BASE, "rankings": [{**GOOD, "equivalence_margins": {"mae": 0.2},
                                                  "network_summaries": ["equal_site_mean",
                                                                        "pooled_rmse"]}]})
    r = cfg.ranking_spec("r")
    assert dict(r.metrics) == GOOD["metrics"] and r.margin("mae", "daily") == 0.2
    assert r.margin("rmse", "daily") is None and r.margin("mae", "period") is None
    assert not r.formal_tests and r.multiplicity is None


@pytest.mark.parametrize("body, match", [
    ({"metrics": {}}, "metrics must map"),
    ({"metrics": {"mae": "higher"}}, "ranked 'lower', not 'higher'"),
    ({"metrics": {"mae": "best"}}, "is not one of"),
    ({"metrics": {"bias_corrected_rmse": "lower"}}, "cannot be ranked"),
    ({"metrics": {"ols_intercept": "closest_to_zero"}}, "cannot be ranked"),
    ({"metrics": {"nse": "higher"}}, "not computed by metric set"),
    ({"equivalence_margins": {"mae": -0.1}}, "positive finite"),
    ({"equivalence_margins": {"mae": True}}, "positive finite"),
    ({"equivalence_margins": {"pearson_r": 0.1}}, "does not rank"),
    ({"period_equivalence_margins": {"mae": 1.0}}, "scores no period products"),
    ({"network_summaries": ["observation_weighted_mean"]}, "must include equal_site_mean"),
    ({"network_summaries": ["equal_site_mean", "median"]}, "is not one of"),
    ({"metrics": {"mae": "lower"}, "network_summaries": ["equal_site_mean", "pooled_rmse"]},
     "pooled_rmse needs rmse"),
    ({"formal_tests": True}, "explicit multiplicity policy"),
    ({"formal_tests": True, "multiplicity": "fdr"}, "is not one of"),
    ({"multiplicity": "holm"}, "only with formal_tests"),
    ({"formal_tests": "yes"}, "must be true or false"),
    ({"bootstrap": None}, "bootstrap not declared"),
    ({"bootstrap": "nope"}, "bootstrap 'nope' not declared"),
    ({"bootstrap": "b2"}, "resamples metric set m2"),
    ({"metric_set": "nope"}, "metric_set 'nope' not declared"),
    ({"comparisons": ["cov"]}, "ranking scope 'none'"),
    ({"comparisons": ["nope"]}, "not declared"),
    ({"ranking_id": "Bad-ID"}, "snake_case"),
    ({"metric": "mae"}, "unknown configuration key"),
])
def test_ranking_config_errors(body, match):
    with pytest.raises(ContractError, match=match):
        config_from_dict({**BASE, "rankings": [{**GOOD, **body}]})


def test_duplicate_ranking_ids():
    with pytest.raises(ContractError, match="duplicate ranking_id"):
        config_from_dict({**BASE, "rankings": [GOOD, GOOD]})
