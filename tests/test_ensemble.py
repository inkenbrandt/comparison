"""Held-out ensemble experiments (guide step 16)."""

import inspect

import numpy as np
import pandas as pd
import pytest

import etcompare.ensemble as ens_module
from etcompare.config import (
    BootstrapSpec,
    EnsembleSpec,
    ExtractionSide,
    FootprintSetSpec,
    MetricSpec,
    NearZeroTolerance,
    ProductSpec,
    config_from_dict,
)
from etcompare.ensemble import (
    FITTED,
    MEAN,
    MEDIAN,
    combine,
    run_ensemble,
    shrink,
    simplex_least_squares,
)
from etcompare.errors import ContractError
from etcompare.io.tables import read_table, write_table
from etcompare.metrics import compute_metrics
from test_aggregation import FP, SETS, ext, product, station

MEMBERS = ("aaa", "bbb", "ccc")
DAYS = pd.date_range("2021-01-01", "2023-12-31")
METRICS = MetricSpec("m", NearZeroTolerance(0.01, 0.001), metrics=("mbe", "mae", "rmse"))
BOOT = BootstrapSpec("b", "m", (7,), 11, 200, 0.95, 10, "year")
PRODUCTS = {**{m: product(m) for m in MEMBERS},
            "ens": ProductSpec("ens", "mm/day", "unknown", "interpolated_daily",
                               is_ensemble=True)}
FIT = dict(designs=("leave_one_year_out",), fold_unit="year", buffer_days=28,
           min_training_folds=2, min_training_days_per_fold=180, min_test_days_per_fold=180,
           method="simplex_least_squares", regularisation="shrink_to_equal",
           shrinkage_grid=(0, 0.25, 0.5, 0.75, 1), selection_metric="rmse",
           preprocessing="none", insufficient_data="decline")


def espec(fit=True, **over):
    kw = dict(ensemble_id="ens_x", members=MEMBERS, reference_variant="et_corrected_measured",
              side=FP, metric_set="m", missing_member_policy="require_all", official="ens",
              bootstrap="b")
    if fit:
        kw.update(FIT)
    kw.update(over)
    return EnsembleSpec(**kw)


def observed(days=DAYS, seed=1):
    rng = np.random.default_rng(seed)
    doy = np.asarray(days.dayofyear, dtype=float)
    return 4 + 2 * np.sin(2 * np.pi * doy / 365) + rng.normal(0, 0.3, len(days))


def members(o, days=DAYS, seed=2, aaa_offset=None):
    """aaa is close to the station; bbb and ccc are biased and noisier."""
    rng = np.random.default_rng(seed)
    aaa = o + rng.normal(0, 0.3, len(days))
    if aaa_offset is not None:
        aaa = aaa + aaa_offset
    return {"aaa": aaa, "bbb": o + 0.8 + rng.normal(0, 0.6, len(days)),
            "ccc": o - 0.5 + rng.normal(0, 0.6, len(days)), "ens": o + 0.1}


def frames(values, days=DAYS, site=None):
    out = [ext(m, days, v) for m, v in values.items()]
    if site is not None:
        out = [f.assign(site_id=site) for f in out]
    return out


def run(o, values, spec=None, days=DAYS, boot=BOOT, obs=None, exts=None, **kw):
    obs = station(days, o) if obs is None else obs
    exts = frames(values, days) if exts is None else exts
    return run_ensemble(spec or espec(), obs, exts, products=PRODUCTS, metric_spec=METRICS,
                        bootstrap_spec=boot, footprint_sets=SETS, **kw)


def status(result, scope):
    return result.summary.frame.set_index("scope").loc[scope, "status"]


def weights(result, fold, design="leave_one_year_out"):
    w = result.weights.frame
    w = w[(w["design"] == design) & (w["fold"] == fold)]
    return w.set_index("member")["weight"].reindex(list(MEMBERS)).to_numpy(float)


def rmse(o, p):
    return compute_metrics(o, p, tolerance=METRICS.daily_tolerance, metrics=("rmse",))[
        "rmse"].value


# --------------------------------------------------------------------------- solver

def test_simplex_least_squares_recovers_an_exact_mixture():
    rng = np.random.default_rng(0)
    x = rng.normal(5, 1, (200, 3))
    w = simplex_least_squares(x, x @ np.array([0.3, 0.7, 0.0]))
    np.testing.assert_allclose(w, [0.3, 0.7, 0.0], atol=1e-10)


def test_simplex_least_squares_is_optimal_on_the_simplex():
    rng = np.random.default_rng(3)
    x = rng.normal(4, 1, (120, 4))
    y = 2.5 * x[:, 0] - 0.4 * x[:, 2] + rng.normal(0, 0.2, 120)      # outside the hull
    w = simplex_least_squares(x, y)
    assert (w >= 0).all() and w.sum() == pytest.approx(1.0, abs=1e-12)
    best = float(np.sum((y - x @ w) ** 2))
    for cand in rng.dirichlet(np.ones(4), 5000):                      # no feasible point beats it
        assert np.sum((y - x @ cand) ** 2) >= best - 1e-9
    for j in range(4):                                                # nor any vertex
        assert np.sum((y - x[:, j]) ** 2) >= best - 1e-9


def test_shrink_and_combine():
    w = np.array([0.6, 0.4, 0.0])
    np.testing.assert_array_equal(shrink(w, 1.0), np.full(3, 1 / 3))
    np.testing.assert_allclose(shrink(w, 0.5), 0.5 * w + 0.5 / 3)
    np.testing.assert_array_equal(shrink(w, 0.0), w)
    vals = np.array([[1.0, 2.0, 3.0], [1.0, np.nan, 3.0], [np.nan, np.nan, 3.0]])
    avail = ~np.isnan(vals)
    pred, eff = combine(vals, avail, w)
    assert pred[0] == pytest.approx(0.6 * 1 + 0.4 * 2)
    assert pred[1] == pytest.approx(1.0) and eff[1].tolist()[:1] == [1.0]   # 0.6 / 0.6
    assert np.isnan(pred[2])                                          # only a zero-weight member


# --------------------------------------------------------------------------- gates

def test_model_good_only_in_training_years_gets_no_false_held_out_improvement():
    """Guide gate: aaa is excellent in 2021-2022 and badly biased in 2023."""
    o = observed()
    offset = np.where(DAYS.year == 2023, 4.0, 0.0)
    values = members(o, aaa_offset=offset)
    r = run(o, values)
    assert status(r, "leave_one_year_out") == "held_out"
    # the trap: trained on 2021-2022, the weights favour aaa and look good in training ...
    w23 = weights(r, "2023")
    assert w23[0] > 0.5
    x = np.column_stack([values[m] for m in MEMBERS])
    train = DAYS.year < 2023
    mean = x.mean(axis=1)
    assert rmse(o[train], x[train] @ w23) < rmse(o[train], mean[train])
    # ... but on the held-out year the fitted ensemble is worse than the simple mean
    p = r.predictions.frame
    fit = p[(p["product"] == FITTED) & (p["fold"] == "2023")].sort_values("date")
    test = DAYS.year == 2023
    assert rmse(o[test], fit["value_mm"].to_numpy()) > rmse(o[test], mean[test])
    # and no held-out improvement over the mean or median is reported
    imp = r.improvement.frame
    vs = imp[imp["baseline_product"].isin([MEAN, MEDIAN]) & imp["metric"].isin(["mae", "rmse"])]
    assert len(vs) == 4 and (vs["held_out_improvement"] != "supported").all()
    assert (vs["estimate"] > 0).all()


def test_consistently_better_member_gives_a_supported_improvement():
    """Positive control: the gate above is not passed trivially."""
    o = observed()
    r = run(o, members(o))
    imp = r.improvement.frame.set_index(["metric", "baseline_product"])
    assert imp.loc[("rmse", MEAN), "held_out_improvement"] == "supported"
    assert imp.loc[("rmse", MEAN), "estimate"] < 0
    assert set(imp["status"]) == {"ok"}


def test_held_out_rows_never_influence_their_own_fold():
    """Guide gate: perturbing a held-out fold leaves its weights, alpha and inner scores."""
    o = observed()
    values = members(o)
    r1 = run(o, values)
    test = DAYS.year == 2022
    rng = np.random.default_rng(9)
    o2 = o.copy()
    o2[test] = o2[test] * 3 + rng.normal(0, 2, test.sum())
    v2 = {m: np.where(test, v * 0.2 + rng.normal(0, 3, len(v)), v) for m, v in values.items()}
    r2 = run(o2, v2)
    np.testing.assert_array_equal(weights(r1, "2022"), weights(r2, "2022"))
    for t in ("ensemble_inner_scores", "ensemble_weights", "ensemble_training_membership"):
        a, b = (x.tables()[t].frame for x in (r1, r2))
        pd.testing.assert_frame_equal(a[a["fold"] == "2022"].reset_index(drop=True),
                                      b[b["fold"] == "2022"].reset_index(drop=True))
    f1, f2 = (x.folds.frame.set_index("fold") for x in (r1, r2))
    assert f1.loc["2022", "alpha_selected"] == f2.loc["2022", "alpha_selected"]
    assert f1.loc["2022", "n_training_days"] == f2.loc["2022", "n_training_days"]
    # the perturbed year is training data for the other folds, whose weights do change
    assert not np.array_equal(weights(r1, "2021"), weights(r2, "2021"))
    m = r1.training_membership.frame
    assert not m[(m["fold"] == "2022") & m["in_training"]]["date"].dt.year.eq(2022).any()


def test_official_ensemble_is_refused_as_a_member():
    with pytest.raises(ContractError, match="never a member"):
        espec(members=("aaa", "bbb", "ens"))
    spec = espec(members=("aaa", "bbb", "ens"), official=None)
    with pytest.raises(ContractError, match="ensemble product"):
        run(observed(), members(observed()), spec)
    with pytest.raises(ContractError, match="is_ensemble"):
        run(observed(), members(observed()), espec(members=("aaa", "bbb"), official="ccc"))


def test_equal_weight_limit_reproduces_the_simple_mean():
    o = observed()
    values = members(o)
    r = run(o, values, espec(shrinkage_grid=(1,)))
    assert (r.folds.frame["alpha_selected"] == 1.0).all()
    w = r.weights.frame
    assert np.allclose(w["weight"], 1 / 3, rtol=0, atol=0)
    p = r.predictions.frame
    fit = p[p["product"] == FITTED].set_index("date")["value_mm"]
    mean = p[p["product"] == MEAN].set_index("date")["value_mm"]
    np.testing.assert_allclose(fit.to_numpy(), mean.reindex(fit.index).to_numpy(), rtol=0,
                               atol=1e-12)
    imp = r.improvement.frame
    vs = imp[imp["baseline_product"] == MEAN]
    assert np.allclose(vs["estimate"], 0, atol=1e-12)
    assert (vs["held_out_improvement"] != "supported").all()


def test_missing_member_policy_require_all_drops_and_reports():
    o = observed()
    values = members(o)
    gap = pd.DatetimeIndex(["2022-03-01", "2022-07-15"])
    exts = [f if m != "ccc" else f[~f["date"].isin(gap)] for m, f in
            zip(values, frames(values), strict=True)]
    r = run(o, values, exts=exts)
    d = r.days.frame.set_index("date")
    assert not d.loc[gap, "eligible"].any()
    assert set(d.loc[gap, "eligibility_reason"]) == {"member_missing"}
    assert set(d.loc[gap, "members_missing"]) == {"ccc"}
    p = r.predictions.frame
    assert not p["date"].isin(gap).any()
    assert not p["reweighted"].any()
    assert r.summary.frame.set_index("scope").loc["baseline", "n_days_compared"] == len(DAYS) - 2
    assert any(i.code == "member_missing" for i in r.report.issues)


def test_missing_member_policy_reweight_is_applied_and_reported():
    o = observed()
    values = members(o)
    gap = pd.DatetimeIndex(["2022-03-01", "2022-07-15"])
    exts = [f if m != "ccc" else f[~f["date"].isin(gap)] for m, f in
            zip(values, frames(values), strict=True)]
    r = run(o, values, espec(missing_member_policy="reweight_available", min_members=2),
            exts=exts)
    d = r.days.frame.set_index("date")
    assert d.loc[gap, "eligible"].all() and d.loc[gap, "reweighted"].all()
    p = r.predictions.frame.set_index(["product", "date"])
    w = weights(r, "2022")
    for day in gap:
        a, b = values["aaa"][DAYS == day][0], values["bbb"][DAYS == day][0]
        assert p.loc[(MEAN, day), "value_mm"] == pytest.approx((a + b) / 2)
        assert p.loc[(FITTED, day), "value_mm"] == pytest.approx(
            (w[0] * a + w[1] * b) / (w[0] + w[1]))
        assert p.loc[(FITTED, day), "reweighted"]
        assert "reweighted_members" in p.loc[(FITTED, day), "flags"]
        assert p.loc[(FITTED, day), "weights_used"].count("=") == 2
    m = r.training_membership.frame
    assert set(m[m["date"].isin(gap) & (m["fold"] != "2022")]["role"]) == {"members_incomplete"}
    folds = r.folds.frame.set_index("fold")
    assert folds.loc["2022", "n_test_days_reweighted"] == 2
    assert any(i.code == "reweighted_members" for i in r.report.issues)


def test_too_few_folds_decline_without_weights():
    days = pd.date_range("2022-01-01", "2023-12-31")
    o = observed(days)
    r = run(o, members(o, days), days=days)
    assert status(r, "leave_one_year_out") == "declined"
    detail = r.summary.frame.set_index("scope").loc["leave_one_year_out", "status_detail"]
    assert "min_training_folds is 2" in detail
    assert set(r.folds.frame["status"]) == {"declined"}
    assert r.weights.frame.empty and r.inner_scores.frame.empty
    assert not (r.predictions.frame["product"] == FITTED).any()
    assert set(r.comparisons) == {"baseline"} and r.improvement.frame.empty
    assert not r.training_membership.frame["in_training"].any()


def test_too_few_folds_exploratory_gives_in_sample_weights_only():
    days = pd.date_range("2023-01-01", "2023-12-31")
    o = observed(days)
    r = run(o, members(o, days), espec(insufficient_data="exploratory"), days=days)
    assert status(r, "leave_one_year_out") == "exploratory"
    w = r.weights.frame
    assert set(w["fold"]) == {"in_sample"} and set(w["inference"]) == {"exploratory_in_sample"}
    assert w["weight"].isna().all() and w["alpha"].isna().all()
    assert w["flags"].str.contains("no_held_out_claim").all()
    assert not (r.predictions.frame["product"] == FITTED).any()
    assert set(r.comparisons) == {"baseline"} and r.improvement.frame.empty


def test_training_folds_below_the_minimum_size_are_left_out():
    """The 28-day buffer leaves 337 days in a training year next to the held-out year."""
    o = observed()
    r = run(o, members(o), espec(min_training_days_per_fold=340))
    f = r.folds.frame.set_index("fold")
    assert set(f["status"]) == {"declined"} and status(r, "leave_one_year_out") == "declined"
    assert f["n_small_fold_days_excluded"].tolist() == [337, 674, 337]
    assert f.loc["2021", "training_folds"] == "2023"
    m = r.training_membership.frame
    sel = m[(m["fold"] == "2021") & (m["date"].dt.year == 2022)]
    assert sel["role"].value_counts().to_dict() == {"training_fold_too_small": 337,
                                                    "buffer_excluded": 28}
    r2 = run(o, members(o), espec(min_training_days_per_fold=300))
    assert set(r2.folds.frame["status"]) == {"fitted"}


def test_same_seed_reproduces_intervals_and_another_differs():
    o = observed()
    values = members(o)
    a = run(o, values).comparisons["leave_one_year_out"].bootstrap.intervals.frame
    b = run(o, values).comparisons["leave_one_year_out"].bootstrap.intervals.frame
    pd.testing.assert_frame_equal(a, b)
    other = BootstrapSpec("b", "m", (7,), 12, 200, 0.95, 10, "year")
    c = run(o, values, boot=other).comparisons["leave_one_year_out"].bootstrap.intervals.frame
    assert not np.allclose(a["lower"], c["lower"])


# --------------------------------------------------------------------------- folds and rows

def test_buffer_removes_training_days_next_to_the_held_out_year():
    o = observed()
    r = run(o, members(o))
    m = r.training_membership.frame
    m = m[m["fold"] == "2022"].set_index("date")
    buf = m[m["role"] == "buffer_excluded"].index
    expected = pd.date_range("2021-12-04", "2021-12-31").append(
        pd.date_range("2023-01-01", "2023-01-28"))
    assert buf.equals(expected)
    assert not m.loc[buf, "in_training"].any()
    f = r.folds.frame.set_index("fold")
    assert f.loc["2022", "n_buffer_days_removed"] == 56
    assert f.loc["2022", "n_training_days"] == 2 * 365 - 56
    # inner folds are buffered too: fewer than all 2021 + 2023 days are inner-scored
    s = r.inner_scores.frame
    assert (s[s["fold"] == "2022"]["n_days"] == 2 * 365 - 56).all()


def test_gap_filled_reference_days_are_never_training_rows_by_default():
    o = observed()
    values = members(o)
    filled = pd.date_range("2021-05-01", "2021-05-31")
    obs = station(DAYS, o, filled=filled)
    spec = espec(reference_variant="et_corrected_inclusive")
    r = run(o, values, spec, obs=obs)
    m = r.training_membership.frame
    sel = m[m["date"].isin(filled) & (m["fold"] != "2021")]
    assert set(sel["role"]) == {"gap_filled_reference"} and not sel["in_training"].any()
    p = r.predictions.frame
    flagged = p[p["date"].isin(filled)]
    assert flagged["flags"].str.contains("shared_gridmet_drivers").all()
    r2 = run(o, values, espec(reference_variant="et_corrected_inclusive",
                              gap_filled_training=True), obs=obs)
    m2 = r2.training_membership.frame
    assert m2[m2["date"].isin(filled) & (m2["fold"] == "2022")]["in_training"].all()
    assert r2.folds.frame.set_index("fold").loc["2022", "n_training_days_gap_filled"] == 31


def test_measured_reference_excludes_gap_filled_days():
    o = observed()
    filled = pd.date_range("2021-05-01", "2021-05-31")
    r = run(o, members(o), obs=station(DAYS, o, filled=filled))
    d = r.days.frame.set_index("date")
    assert set(d.loc[filled, "eligibility_reason"]) == {"reference_unavailable"}


def test_leave_one_site_out_needs_other_sites():
    o = observed()
    r = run(o, members(o), espec(designs=("leave_one_site_out",), fold_unit=None))
    assert status(r, "leave_one_site_out") == "not_available"
    assert "n_sites = 1" in r.summary.frame.set_index("scope").loc["leave_one_site_out",
                                                                   "status_detail"]
    assert r.summary.frame["flags"].str.contains("single_site_not_network_evidence").all()


def test_leave_one_site_out_on_three_synthetic_sites():
    days = pd.date_range("2022-01-01", "2022-12-31")
    obs, exts = [], []
    for i, site in enumerate(("S-A", "S-B", "S-C")):
        o = observed(days, seed=10 + i)
        obs.append(station(days, o).assign(site_id=site))
        exts += frames(members(o, days, seed=20 + i), days, site=site)
    r = run(None, None, espec(designs=("leave_one_site_out",), fold_unit=None), days=days,
            obs=obs, exts=exts)
    assert status(r, "leave_one_site_out") == "held_out"
    f = r.folds.frame.set_index("fold")
    assert list(f.index) == ["S-A", "S-B", "S-C"] and set(f["status"]) == {"fitted"}
    assert set(f["n_buffer_days_removed"]) == {0}
    m = r.training_membership.frame
    a = m[m["fold"] == "S-A"]
    assert not a[a["site_id"] == "S-A"]["in_training"].any()
    assert a[a["site_id"] != "S-A"]["in_training"].all()
    assert not r.summary.frame["flags"].str.contains("single_site").any()


def test_comparison_freezes_days_across_products_and_reuses_steps_9_to_11():
    o = observed()
    values = members(o)
    r = run(o, values)
    comp = r.comparisons["leave_one_year_out"]
    assert comp.cohort.spec.kind == "common_date"
    assert set(comp.cohort.spec.models) == {"ens", MEAN, MEDIAN, FITTED}
    paired = comp.cohort.paired.frame
    assert paired.groupby("model")["date"].apply(lambda s: tuple(sorted(s))).nunique() == 1
    # metrics are those of compute_metrics on the paired rows
    mt = comp.metrics.table.frame.set_index(["model", "metric"])
    for model in ("ens", MEAN, FITTED):
        g = paired[paired["model"] == model]
        assert mt.loc[(model, "rmse"), "value"] == pytest.approx(
            rmse(g["observed_mm"].to_numpy(), g["modeled_mm"].to_numpy()), rel=1e-12)
    x = np.column_stack([values[m] for m in MEMBERS])
    mean = paired[paired["model"] == MEAN].sort_values("date")["modeled_mm"].to_numpy()
    np.testing.assert_allclose(mean, x.mean(axis=1), rtol=1e-12)
    med = paired[paired["model"] == MEDIAN].sort_values("date")["modeled_mm"].to_numpy()
    np.testing.assert_allclose(med, np.median(x, axis=1), rtol=1e-12)
    assert comp.ranking.contrasts.frame["inference"].eq("exploratory").all()


def test_disagreement_is_labelled_and_matches_the_members():
    o = observed()
    values = members(o)
    r = run(o, values)
    d = r.days.frame.sort_values("date")
    x = np.column_stack([values[m] for m in MEMBERS])
    np.testing.assert_allclose(d["member_range_mm"], x.max(axis=1) - x.min(axis=1))
    np.testing.assert_allclose(d["member_sd_mm"], x.std(axis=1))
    assert d["flags"].str.contains("disagreement_not_confidence_interval").all()
    assert "not a confidence interval" in r.days.attributes["spread_label"]


def test_every_row_is_labelled_post_extraction():
    o = observed()
    r = run(o, members(o))
    s = r.summary.frame
    assert set(s["ensemble_level"]) == {"post_extraction"}
    for t in ("ensemble_predictions", "ensemble_summary", "ensemble_weights"):
        assert r.tables()[t].frame["flags"].str.contains("not_pixel_level_ensemble").all()


def test_target_dependent_footprints_are_refused():
    le = [FootprintSetSpec("SET", "x", weighting_method="measured_le",
                           value_semantics="normalized_cell_weight",
                           temporal_weighting="measured_et_weighted")]
    o = observed()
    with pytest.raises(ContractError, match="target"):
        run_ensemble(espec(), station(DAYS, o), frames(members(o)), products=PRODUCTS,
                     metric_spec=METRICS, bootstrap_spec=BOOT, footprint_sets=le)
    with pytest.raises(ContractError, match="not supplied"):
        run_ensemble(espec(), station(DAYS, o), frames(members(o)), products=PRODUCTS,
                     metric_spec=METRICS, bootstrap_spec=BOOT)


def test_no_event_residual_or_anomaly_input():
    params = set(inspect.signature(run_ensemble).parameters)
    assert params == {"spec", "observations", "extractions", "products", "metric_spec",
                      "bootstrap_spec", "footprint_sets"}
    src = inspect.getsource(ens_module)
    for mod in ("events", "water_balance", "scenarios", "anomalies", "diagnostics"):
        assert f"etcompare.{mod}" not in src.split('"""', 2)[2]


def test_result_tables_round_trip(tmp_path):
    o = observed()
    r = run(o, members(o))
    for name, table in r.tables().items():
        back = read_table(write_table(table, tmp_path / f"{name}.parquet"))
        pd.testing.assert_frame_equal(back.frame, table.frame)


def test_without_bootstrap_contrasts_are_not_assessed():
    o = observed()
    r = run(o, members(o), espec(bootstrap=None), boot=None)
    imp = r.improvement.frame
    assert len(imp) and set(imp["held_out_improvement"]) == {"not_assessed"}


# --------------------------------------------------------------------------- config

BASE = {
    "stations": {"US-XXX": {"timezone": "UTC-07:00", "day_definition": "local_standard_time",
                            "interval_label": "start"}},
    "products": {**{m: {"units": "mm/day", "day_definition": "unknown",
                        "temporal_support": "interpolated_daily"} for m in MEMBERS},
                 "ens": {"units": "mm/day", "day_definition": "unknown",
                         "temporal_support": "interpolated_daily", "is_ensemble": True}},
    "footprint_sets": [
        {"set_id": "SET", "root": ".", "weighting_method": "nldas",
         "temporal_weighting": "reference_et_weighted", "value_semantics":
         "normalized_cell_weight"},
        {"set_id": "LE", "root": ".", "weighting_method": "le",
         "temporal_weighting": "measured_et_weighted", "value_semantics":
         "normalized_cell_weight"}],
    "metric_sets": [{"metric_set_id": "m", "metrics": ["mbe", "mae", "rmse"],
                     "daily_tolerance": {"mean": 0.01, "std": 0.001}}],
    "bootstrap": [{"bootstrap_id": "b", "metric_set": "m", "block_lengths": [7], "seed": 1,
                   "n_replicates": 10, "confidence_level": 0.95, "min_blocks": 10,
                   "segment_boundary": "year"}],
}
ENS = {"ensemble_id": "e", "members": list(MEMBERS), "official": "ens",
       "reference_variant": "et_corrected_measured",
       "side": {"extraction_method": "footprint_conservative", "footprint_set_id": "SET"},
       "metric_set": "m", "bootstrap": "b", "missing_member_policy": "require_all",
       **{k: list(v) if isinstance(v, tuple) else v for k, v in FIT.items()}}


def cfg(**over):
    body = {**ENS, **over}
    return config_from_dict({**BASE, "ensembles": [{k: v for k, v in body.items()
                                                     if v is not None}]})


def test_config_section_loads():
    e = cfg().ensemble("e")
    assert e.members == MEMBERS and e.side == FP and e.shrinkage_grid == (0, .25, .5, .75, 1)
    assert e.designs == ("leave_one_year_out",) and e.buffer_days == 28


@pytest.mark.parametrize("over, match", [
    ({"members": ["aaa", "bbb", "ens"]}, "never a member"),
    ({"members": ["aaa", "ens"], "official": None}, "is_ensemble"),
    ({"members": ["aaa", "bbb"], "official": "ccc"}, "is_ensemble"),
    ({"members": ["aaa"]}, "2-12"),
    ({"members": ["aaa", "aaa"]}, "2-12"),
    ({"members": ["aaa", "zzz"]}, "not a declared product"),
    ({"side": {"extraction_method": "footprint_conservative", "footprint_set_id": "LE"}},
     "target"),
    ({"side": {"extraction_method": "footprint_conservative", "footprint_set_id": "NOPE"}},
     "not declared"),
    ({"metric_set": "zz"}, "metric set"),
    ({"bootstrap": "zz"}, "bootstrap"),
    ({"contrast_metrics": ["nse"]}, "not computed"),
    ({"contrast_metrics": ["bias_corrected_rmse"]}, "contrast_metrics"),
    ({"missing_member_policy": "fill"}, "missing_member_policy"),
    ({"missing_member_policy": "reweight_available"}, "min_members"),
    ({"min_members": 2}, "only to reweight"),
    ({"missing_member_policy": "reweight_available", "min_members": 4}, "exceeds"),
    ({"designs": ["random_days"]}, "designs"),
    ({"shrinkage_grid": [0, 0.5]}, "must include 1"),
    ({"shrinkage_grid": [0, 1.5, 1]}, r"\[0, 1\]"),
    ({"shrinkage_grid": []}, "non-empty"),
    ({"min_training_folds": 1}, "min_training_folds"),
    ({"buffer_days": None}, "buffer_days not declared"),
    ({"insufficient_data": None}, "insufficient_data not declared"),
    ({"preprocessing": "zscore"}, "preprocessing"),
    ({"regularisation": "ridge"}, "regularisation"),
    ({"fold_unit": None}, "fold_unit"),
    ({"fold_unit": "month"}, "fold_unit"),
    ({"gap_filled_training": True}, "et_corrected_inclusive"),
    ({"sources": ["nope"]}, "sources"),
    ({"sites": ["US-ZZZ"]}, "not declared"),
    ({"reference_variant": "et_fill"}, "reference_variant"),
    ({"bogus": 1}, "bogus"),
])
def test_config_refusals(over, match):
    with pytest.raises(ContractError, match=match):
        cfg(**over)


def test_fit_fields_need_designs():
    with pytest.raises(ContractError, match="apply only when designs"):
        cfg(designs=[])
    no_fit = {k: None for k in FIT}
    e = cfg(**no_fit).ensemble("e")
    assert e.designs == () and e.shrinkage_grid == ()


def test_duplicate_ensemble_ids():
    with pytest.raises(ContractError, match="duplicate ensemble_id"):
        config_from_dict({**BASE, "ensembles": [ENS, ENS]})


# --------------------------------------------------------------------------- subsets, sites

APR_OCT = ({"subset_id": "apr_oct", "months": [4, 5, 6, 7, 8, 9, 10]},)


def test_month_subset_is_evaluated_apart_and_training_is_unchanged():
    o = observed()
    values = members(o)
    r_all = run(o, values)
    r = run(o, values, espec(subsets=APR_OCT))
    pd.testing.assert_frame_equal(r.weights.frame, r_all.weights.frame)
    pd.testing.assert_frame_equal(r.inner_scores.frame, r_all.inner_scores.frame)
    sub = r.comparisons["leave_one_year_out__apr_oct"]
    assert sub.subset == "apr_oct"
    paired = sub.cohort.paired.frame
    assert set(paired["date"].dt.month) == set(range(4, 11))
    gs = DAYS.month.isin(range(4, 11))
    assert paired["date"].nunique() == int(gs.sum())
    mt = sub.metrics.table.frame.set_index(["model", "metric"])["value"]
    g = paired[paired["model"] == MEAN].sort_values("date")
    assert mt[(MEAN, "rmse")] == pytest.approx(
        rmse(g["observed_mm"].to_numpy(), g["modeled_mm"].to_numpy()), rel=1e-12)
    s = r.summary.frame.set_index(["scope", "subset"])
    assert s.loc[("leave_one_year_out", "apr_oct"), "n_days_compared"] == int(gs.sum())
    assert s.loc[("leave_one_year_out", "all"), "n_days_compared"] == len(DAYS)
    assert "apr_oct months only" in s.loc[("baseline", "apr_oct"), "interpretation"]
    imp = r.improvement.frame
    assert set(imp["subset"]) == {"all", "apr_oct"}


@pytest.mark.parametrize("subsets, match", [
    ([{"subset_id": "all", "months": [4]}], "not 'all'"),
    ([{"subset_id": "gs", "months": [0, 4]}], "months"),
    ([{"subset_id": "gs", "months": []}], "months"),
    ([{"subset_id": "gs", "months": [4]}, {"subset_id": "gs", "months": [5]}], "duplicate"),
    ([{"subset_id": "gs", "months": [4], "extra": 1}], "extra"),
])
def test_subset_refusals(subsets, match):
    with pytest.raises(ContractError, match=match):
        espec(subsets=subsets)


SET2 = FootprintSetSpec("SET2", "x", weighting_method="nldas_other",
                        value_semantics="normalized_cell_weight",
                        temporal_weighting="reference_et_weighted")
LE = FootprintSetSpec("LE", "x", weighting_method="measured_le",
                      value_semantics="normalized_cell_weight",
                      temporal_weighting="measured_et_weighted")


def two_site_inputs(set_b="SET2"):
    days = pd.date_range("2022-01-01", "2022-12-31")
    obs, exts = [], []
    for i, (site, set_id) in enumerate((("S-A", "SET"), ("S-B", set_b))):
        o = observed(days, seed=30 + i)
        obs.append(station(days, o).assign(site_id=site))
        exts += [f.assign(site_id=site, footprint_set_id=set_id)
                 for f in frames(members(o, days, seed=40 + i), days)]
    return days, obs, exts


def test_per_site_footprint_sets_are_relabelled_with_provenance():
    days, obs, exts = two_site_inputs()
    spec = espec(fit=False, side=ExtractionSide("footprint_conservative", "per_site"),
                 sites=("S-A", "S-B"), site_footprint_sets={"S-A": "SET", "S-B": "SET2"})
    r = run_ensemble(spec, obs, exts, products=PRODUCTS, metric_spec=METRICS,
                     bootstrap_spec=BOOT, footprint_sets=[*SETS, SET2])
    paired = r.comparisons["baseline"].cohort.paired.frame
    assert set(paired["footprint_set_id"]) == {"per_site"}
    assert set(paired["site_id"]) == {"S-A", "S-B"}
    assert paired.groupby("site_id")["date"].nunique().tolist() == [len(days)] * 2
    assert r.days.attributes["site_footprint_sets"] == {"S-A": "SET", "S-B": "SET2"}
    # a station's rows of another set never enter
    exts_wrong = [f.assign(footprint_set_id="SET2") if (f["site_id"] == "S-A").all() else f
                  for f in exts]
    r2 = run_ensemble(spec, obs, exts_wrong, products=PRODUCTS, metric_spec=METRICS,
                      bootstrap_spec=BOOT, footprint_sets=[*SETS, SET2])
    assert set(r2.comparisons["baseline"].cohort.paired.frame["site_id"]) == {"S-B"}


def test_per_site_target_dependent_set_is_refused():
    days, obs, exts = two_site_inputs(set_b="LE")
    spec = espec(fit=False, side=ExtractionSide("footprint_conservative", "per_site"),
                 sites=("S-A", "S-B"), site_footprint_sets={"S-A": "SET", "S-B": "LE"})
    with pytest.raises(ContractError, match="target"):
        run_ensemble(spec, obs, exts, products=PRODUCTS, metric_spec=METRICS,
                     bootstrap_spec=BOOT, footprint_sets=[*SETS, LE])


def test_per_site_mapping_must_cover_the_sites():
    with pytest.raises(ContractError, match="exactly the declared sites"):
        espec(fit=False, sites=("S-A", "S-B"), site_footprint_sets={"S-A": "SET"})
    with pytest.raises(ContractError, match="exactly the declared sites"):
        espec(fit=False, site_footprint_sets={"S-A": "SET"})


def test_config_per_site_side_is_a_label():
    with pytest.raises(ContractError, match="must not be the declared set"):
        cfg(sites=["US-XXX"], site_footprint_sets={"US-XXX": "SET"})
    with pytest.raises(ContractError, match="target"):
        cfg(side={"extraction_method": "footprint_conservative", "footprint_set_id": "lbl"},
            sites=["US-XXX"], site_footprint_sets={"US-XXX": "LE"})
    e = cfg(side={"extraction_method": "footprint_conservative", "footprint_set_id": "lbl"},
            sites=["US-XXX"], site_footprint_sets={"US-XXX": "SET"}).ensemble("e")
    assert dict(e.site_footprint_sets) == {"US-XXX": "SET"}


def test_too_few_held_out_days_decline_the_fold():
    """A fold that predicts too few days is declined, however good its training (D-072)."""
    days = pd.date_range("2021-01-01", "2023-12-31")
    o = observed(days)
    values = members(o, days)
    keep = ~((days.year == 2022) & (days.month > 1))           # 2022 keeps 31 January days
    obs = station(days[keep], o[keep])
    exts = [f[f["date"].isin(days[keep])] for f in frames(values, days)]
    r = run(o, values, days=days, obs=obs, exts=exts)
    f = r.folds.frame.set_index("fold")
    assert f.loc["2022", "status"] == "declined" and f.loc["2022", "n_test_days"] == 31
    assert "31 eligible held-out day(s) < min_test_days_per_fold 180" in f.loc["2022",
                                                                              "status_detail"]
    p = r.predictions.frame
    assert not ((p["product"] == FITTED) & (p["date"].dt.year == 2022)).any()
    r2 = run(o, values, espec(min_test_days_per_fold=30), days=days, obs=obs, exts=exts)
    assert r2.folds.frame.set_index("fold").loc["2022", "status"] == "fitted"


def test_min_test_days_is_mandatory_with_designs():
    with pytest.raises(ContractError, match="min_test_days_per_fold not declared"):
        cfg(min_test_days_per_fold=None)
