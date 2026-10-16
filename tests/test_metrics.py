"""Performance metrics (guide step 9).

Expected values come from the guide's fixture and from independent numpy/scipy
calculations; ``etcompare.metrics`` itself never imports scipy.
"""

import math

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from etcompare.aggregation import aggregate_cohort
from etcompare.cohorts import build_cohort as _build_cohort
from etcompare.config import (
    DEFAULT_METRICS,
    METRIC_IDS,
    ComparisonSpec,
    ExtractionSide,
    FootprintSetSpec,
    MetricSpec,
    NearZeroTolerance,
    PeriodAggregationSpec,
    ProductSpec,
    config_from_dict,
)
from etcompare.errors import ContractError
from etcompare.io.tables import read_table, write_table
from etcompare.metrics import (
    METRIC_DEFINITIONS,
    _check_mask,
    compute_metrics,
    cumulative_error,
    definitions_frame,
    output_metrics,
    score_comparison,
)
from etcompare.schemas import CUMULATIVE_ERROR, METRICS

TOL = NearZeroTolerance(mean=1e-9, std=1e-12)
SITE = "US-XXX"
FP = ExtractionSide("footprint_conservative", "SET")
PIX = ExtractionSide("pixel_window_3x3", "none")
SETS = [FootprintSetSpec("SET", "x", weighting_method="nldas",
                         value_semantics="normalized_cell_weight",
                         temporal_weighting="reference_et_weighted")]


def product(model, support="interpolated_daily"):
    return ProductSpec(model, "mm/day", "unknown", support)


PRODUCTS = {m: product(m) for m in ("aaa", "bbb")}


def values(o, p, metrics=METRIC_IDS, tol=TOL):
    return {k: r.value for k, r in compute_metrics(o, p, tolerance=tol, metrics=metrics).items()}


def reasons(o, p, metrics=METRIC_IDS, tol=TOL):
    return {k: r.reason for k, r in compute_metrics(o, p, tolerance=tol, metrics=metrics).items()}


# --------------------------------------------------------------------------- core values

def test_guide_acceptance_fixture():
    o, p = [1.0, 2.0, 3.0], [2.0, 3.0, 4.0]
    v = values(o, p)
    assert v["mbe"] == pytest.approx(1) and v["mae"] == pytest.approx(1)
    assert v["rmse"] == pytest.approx(1)
    assert v["bias_corrected_rmse"] == pytest.approx(0, abs=1e-15)
    assert v["ols_slope"] == pytest.approx(1) and v["ols_intercept"] == pytest.approx(1)
    assert v["pearson_r_squared"] == pytest.approx(1)
    assert v["percent_bias"] == pytest.approx(50)
    # independently
    lr = stats.linregress(o, p)
    assert (lr.slope, lr.intercept, lr.rvalue ** 2) == pytest.approx((1, 1, 1))
    e = np.subtract(p, o)
    assert v["rmse"] == pytest.approx(np.sqrt(np.mean(e ** 2)))
    assert v["percent_bias"] == pytest.approx(100 * e.sum() / np.sum(o))


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_random_sample_matches_numpy_and_scipy(seed):
    rng = np.random.default_rng(seed)
    o = rng.gamma(2.0, 1.5, 200)
    p = 0.8 * o + 0.4 + rng.normal(0, 0.6, 200)
    v = values(o, p)
    e = p - o
    lr = stats.linregress(o, p)
    r = stats.pearsonr(o, p)[0]
    expect = {
        "mbe": e.mean(), "mae": np.abs(e).mean(), "rmse": np.sqrt((e ** 2).mean()),
        "bias_corrected_rmse": np.std(e), "percent_bias": 100 * e.sum() / o.sum(),
        "nrmse_mean": np.sqrt((e ** 2).mean()) / o.mean(),
        "nrmse_mean_percent": 100 * np.sqrt((e ** 2).mean()) / o.mean(),
        "pearson_r": r, "pearson_r_squared": r ** 2,
        "ols_slope": lr.slope, "ols_intercept": lr.intercept,
        "nse": 1 - np.sum(e ** 2) / np.sum((o - o.mean()) ** 2),
        "kge_2009_r": np.corrcoef(o, p)[0, 1], "kge_2009_alpha": np.std(p) / np.std(o),
        "kge_2009_beta": p.mean() / o.mean(),
        "mape": 100 * np.mean(np.abs(e) / np.abs(o)),
    }
    polyfit = np.polyfit(o, p, 1)
    assert v["ols_slope"] == pytest.approx(polyfit[0], rel=1e-10)
    expect["kge_2009"] = 1 - math.sqrt((expect["kge_2009_r"] - 1) ** 2
                                       + (expect["kge_2009_alpha"] - 1) ** 2
                                       + (expect["kge_2009_beta"] - 1) ** 2)
    for k, x in expect.items():
        assert v[k] == pytest.approx(x, rel=1e-10, abs=1e-12), k
    # bias-corrected RMSE identity: RMSE^2 = MBE^2 + bcRMSE^2
    assert v["rmse"] ** 2 == pytest.approx(v["mbe"] ** 2 + v["bias_corrected_rmse"] ** 2)


def test_pearson_r_squared_is_not_predictive_r2():
    o = np.array([1.0, 2.0, 3.0, 4.0])
    v = values(o, 2 * o + 5)  # perfectly correlated, badly biased
    assert v["pearson_r_squared"] == pytest.approx(1)
    assert v["nse"] < 0  # the predictive coefficient of determination is poor
    assert "r2" not in METRIC_DEFINITIONS and "r_squared" not in METRIC_DEFINITIONS
    assert "not the predictive" in METRIC_DEFINITIONS["pearson_r_squared"].note


def test_roles_and_defaults():
    assert METRIC_DEFINITIONS["bias_corrected_rmse"].role == "diagnostic"
    assert "diagnostic only" in METRIC_DEFINITIONS["bias_corrected_rmse"].note
    assert METRIC_DEFINITIONS["mape"].role == "diagnostic"
    assert "mape" not in DEFAULT_METRICS and "nse" not in DEFAULT_METRICS
    assert "kge_2009" not in DEFAULT_METRICS
    assert METRIC_DEFINITIONS["ols_slope"].role == "descriptive"
    assert METRIC_DEFINITIONS["nrmse_mean"].units == "1"
    assert METRIC_DEFINITIONS["nrmse_mean_percent"].units == "%"
    assert set(definitions_frame()["metric_id"]) == set(METRIC_DEFINITIONS)
    assert output_metrics(["kge_2009"]) == ("kge_2009", "kge_2009_r", "kge_2009_alpha",
                                            "kge_2009_beta")
    with pytest.raises(ValueError, match="unknown metric"):
        output_metrics(["kge_2009_alpha"])  # components come only with kge_2009
    with pytest.raises(ValueError, match="unknown metric"):
        output_metrics(["kge_2012"])
    assert set(compute_metrics([1, 2], [1, 2], tolerance=TOL)) == set(DEFAULT_METRICS)


# --------------------------------------------------------------------------- undefined

def test_empty_sample():
    r = reasons([], [])
    assert set(r.values()) == {"empty_cohort"}
    assert all(v is None for v in values([], []).values())


def test_singleton_sample():
    v, r = values([2.0], [3.0]), reasons([2.0], [3.0])
    assert (v["mbe"], v["mae"], v["rmse"]) == (1.0, 1.0, 1.0)
    assert v["percent_bias"] == pytest.approx(50)
    for m in ("bias_corrected_rmse", "pearson_r", "pearson_r_squared", "ols_slope",
              "ols_intercept", "nse", "kge_2009", "kge_2009_r", "kge_2009_alpha"):
        assert v[m] is None and r[m] == "singleton_sample", m


def test_constant_observed_series():
    o, p = [2.0, 2.0, 2.0, 2.0], [1.0, 2.0, 3.0, 2.5]
    v, r = values(o, p), reasons(o, p)
    for m in ("pearson_r", "pearson_r_squared", "ols_slope", "ols_intercept", "nse",
              "kge_2009", "kge_2009_alpha"):
        assert v[m] is None and r[m] == "constant_observed", m
    e = np.subtract(p, o)
    assert v["bias_corrected_rmse"] == pytest.approx(np.std(e))
    assert v["percent_bias"] == pytest.approx(100 * e.sum() / 8)
    assert v["kge_2009_beta"] == pytest.approx(np.mean(p) / 2)


def test_constant_modeled_series():
    o, p = [1.0, 2.0, 4.0], [3.0, 3.0, 3.0]
    v, r = values(o, p), reasons(o, p)
    assert v["pearson_r"] is None and r["pearson_r"] == "constant_modeled"
    assert r["kge_2009"] == "constant_modeled"
    assert v["ols_slope"] == pytest.approx(0) and v["ols_intercept"] == pytest.approx(3)
    assert v["kge_2009_alpha"] == pytest.approx(0)
    lr = stats.linregress(o, p)
    assert (v["ols_slope"], v["ols_intercept"]) == pytest.approx((lr.slope, lr.intercept))


def test_std_tolerance_is_configurable():
    o, p = [1.0, 1.0 + 1e-7, 1.0 - 1e-7], [1.0, 2.0, 3.0]
    assert values(o, p)["pearson_r"] is not None
    tol = NearZeroTolerance(mean=1e-9, std=1e-6)
    assert reasons(o, p, tol=tol)["pearson_r"] == "constant_observed"


def test_near_zero_observed_total():
    o, p = [1.0, -1.0, 0.0], [1.5, -0.5, 0.2]
    v, r = values(o, p), reasons(o, p)
    for m in ("percent_bias", "nrmse_mean", "nrmse_mean_percent", "kge_2009_beta", "kge_2009"):
        assert v[m] is None and r[m] == "near_zero_observed_mean", m
    assert v["mbe"] == pytest.approx(0.4)  # absolute metrics stay defined
    assert r["mape"] == "near_zero_observation"


def test_mean_tolerance_is_configurable():
    o, p = [0.004, 0.006, 0.005], [0.01, 0.02, 0.0]
    assert reasons(o, p, tol=NearZeroTolerance(mean=0.01, std=0))["percent_bias"] == \
        "near_zero_observed_mean"
    v = values(o, p, tol=NearZeroTolerance(mean=0.001, std=0))
    assert v["percent_bias"] == pytest.approx(100 * (0.03 - 0.015) / 0.015)


def test_non_finite_values_are_never_dropped():
    with pytest.raises(ValueError, match="never drop"):
        compute_metrics([1.0, np.nan], [1.0, 2.0], tolerance=TOL)
    with pytest.raises(ValueError, match="equal length"):
        compute_metrics([1.0, 2.0], [1.0], tolerance=TOL)


# --------------------------------------------------------------------------- cohorts

def ext(model, dates, vals, side=FP, alignment="declared"):
    n = len(dates)
    vals = np.broadcast_to(np.asarray(vals, dtype=float), (n,))
    return pd.DataFrame({
        "site_id": SITE, "date": dates, "period": "day", "model": model,
        "extraction_method": side.extraction_method, "footprint_set_id": side.footprint_set_id,
        "footprint_method": "m", "et_mm": vals, "fp_weight_sum": 1.0,
        "fp_weight_retained_frac": 1.0 if side.footprint_set_id != "none" else np.nan,
        "fp_weight_retained_denominator": None, "support_id": None, "day_alignment": alignment,
        "model_asset_id": None, "footprint_asset_id": None, "source_path": None,
    })


def station(dates, vals=2.0, filled=()):
    n = len(dates)
    vals = np.broadcast_to(np.asarray(vals, dtype=float), (n,)).copy()
    is_filled = pd.Series(dates).isin(pd.DatetimeIndex(list(filled))).to_numpy()
    return pd.DataFrame({
        "site_id": SITE, "date": dates, "et_raw_mm": np.where(is_filled, np.nan, vals),
        "et_corrected_mm": vals, "gap_filled": is_filled, "raw_available": ~is_filled,
        "reference_provenance": np.where(is_filled, "corrected_filled", "corrected_from_raw"),
    })


def cspec(kind="common_date", models=("aaa", "bbb"), variants=("et_corrected_inclusive",),
          sides=(FP,), **kw):
    return ComparisonSpec("cmp", kind, models, variants, sides, **kw)


def mspec(**kw):
    kw.setdefault("daily_tolerance", TOL)
    return MetricSpec("mset", **kw)


def build(obs, frames, spec):
    return _build_cohort(obs, frames, spec, footprint_sets=SETS)


def pick(results, **match):
    f = results.table.frame
    for k, v in match.items():
        f = f[f[k] == v]
    return f.reset_index(drop=True)


def metric(results, name, **match):
    f = pick(results, metric=name, **match)
    assert len(f) == 1, f
    return f.iloc[0]


@pytest.fixture
def two_models():
    rng = np.random.default_rng(7)
    d = pd.date_range("2024-01-01", "2024-02-29")
    o = rng.gamma(2, 1, len(d))
    a = o + rng.normal(0.2, 0.3, len(d))
    b = o + rng.normal(-0.1, 0.5, len(d))
    missing = d[[10, 11, 40]]  # bbb misses three days
    keep = ~d.isin(missing)
    obs = station(d, o, filled=d[[5]])
    c = build(obs, [ext("aaa", d, a), ext("bbb", d[keep], b[keep])], cspec())
    return c, d, o, a, b, keep


def test_daily_metrics_use_the_common_mask(two_models):
    c, d, o, a, b, keep = two_models
    res = score_comparison(c, mspec(metrics=METRIC_IDS), products=PRODUCTS)
    f = res.table.frame
    assert set(f["product"]) == {"daily_pairs"} and set(f["temporal_scale"]) == {"daily"}
    for model, x in (("aaa", a), ("bbb", b)):
        row = metric(res, "rmse", model=model)
        assert row["n_days"] == keep.sum() == 57  # both on the same frozen 57 days
        e = x[keep] - o[keep]
        assert row["value"] == pytest.approx(np.sqrt(np.mean(e ** 2)))
        assert row["units"] == "mm d-1" and row["n_sites"] == 1
        assert row["n_days_reference_filled"] == 1
        assert row["ranking_scope"] == "all_models" and row["temporal_support"] == \
            "interpolated_daily"
        assert metric(res, "pearson_r", model=model)["value"] == pytest.approx(
            stats.pearsonr(o[keep], x[keep])[0])
    assert metric(res, "percent_bias", model="aaa")["units"] == "%"
    assert metric(res, "bias_corrected_rmse", model="aaa")["metric_role"] == "diagnostic"
    assert metric(res, "kge_2009_alpha", model="aaa")["metric_role"] == "component"
    assert f["valid"].all() and f["validity_reason"].isna().all()
    assert res.attributes["cohort_membership_sha256"] == c.fingerprint
    w = res.wide()
    assert len(w) == 2 and "pearson_r_squared" in w.columns


def test_metric_table_round_trip(tmp_path, two_models):
    c = two_models[0]
    res = score_comparison(c, mspec(), products=PRODUCTS)
    back = read_table(write_table(res.table, tmp_path / "m.parquet"))
    assert back.schema is METRICS and METRICS.version == "2"
    pd.testing.assert_frame_equal(back.frame, res.table.frame)
    cum = read_table(write_table(res.cumulative, tmp_path / "c.parquet"))
    assert cum.schema is CUMULATIVE_ERROR
    pd.testing.assert_frame_equal(cum.frame, res.cumulative.frame)


def test_empty_cohort_gives_null_rows():
    d = pd.date_range("2024-01-01", periods=5)
    c = build(station(d), [ext("aaa", d[:2], 1.0), ext("bbb", d[3:], 1.0)], cspec())
    assert c.status == "empty"
    res = score_comparison(c, mspec(), products=PRODUCTS)
    f = res.table.frame
    assert len(f) == 2 * len(DEFAULT_METRICS)
    assert (~f["valid"].astype(bool)).all() and set(f["validity_reason"]) == {"empty_cohort"}
    assert (f["n_days"] == 0).all() and (f["n_sites"] == 0).all()
    assert "empty_cohort" in res.report.codes()
    assert len(res.cumulative) == 0


def test_paired_methods_are_separate_rows():
    d = pd.date_range("2024-01-01", periods=10)
    o = np.arange(1.0, 11.0)
    c = build(station(d, o), [ext("aaa", d, o + 1), ext("aaa", d[1:], o[1:] + 2, side=PIX)],
              cspec("paired_method", models=("aaa",), sides=(FP, PIX)))
    res = score_comparison(c, mspec(), products=PRODUCTS)
    fp = metric(res, "mbe", footprint_set_id="SET")
    px = metric(res, "mbe", footprint_set_id="none")
    assert (fp["value"], px["value"]) == pytest.approx((1, 2))
    assert fp["n_days"] == px["n_days"] == 9  # day 1 dropped from both sides
    assert fp["ranking_scope"] == "none"


def test_temporal_support_must_match_in_a_ranking_cohort(two_models):
    c = two_models[0]
    mixed = {"aaa": product("aaa"), "bbb": product("bbb", "overpass_only")}
    with pytest.raises(ContractError, match="temporal support"):
        score_comparison(c, mspec(), products=mixed)
    with pytest.raises(ContractError, match="not declared"):
        score_comparison(c, mspec(), products={"aaa": product("aaa")})


def test_mask_check_rejects_unequal_samples():
    f = pd.DataFrame({"cohort_id": "c", "site_id": SITE, "model": ["aaa", "aaa", "bbb"],
                      "reference_variant": "et_raw", "extraction_method": "m",
                      "footprint_set_id": "none",
                      "date": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-01"])})
    with pytest.raises(ContractError, match="never drop values"):
        _check_mask(f, ["cohort_id", "site_id"], "date", "cohort")


def test_flags_are_carried():
    d = pd.date_range("2024-01-01", periods=4)
    c = build(station(d, [1.0, 2, 3, 4]),
              [ext("aaa", d, [1.0, 2, 3, 5], alignment="unverified")],
              cspec("model_available", models=("aaa",)))
    res = score_comparison(c, mspec(), products=PRODUCTS)
    assert metric(res, "mbe")["flags"] == "day_alignment=unverified"
    assert metric(res, "mbe")["cohort_id"] == "cmp:aaa"


# --------------------------------------------------------------------------- periods

def test_period_products_are_scored_separately():
    d = pd.date_range("2024-01-01", "2024-04-30")
    rng = np.random.default_rng(3)
    o = rng.gamma(3, 1, len(d))
    m = o * 1.1 + 0.1
    drop = d.isin(pd.to_datetime(["2024-02-10", "2024-03-05", "2024-03-06"]))
    c = build(station(d, o), [ext("aaa", d[~drop], m[~drop])],
              cspec("model_available", models=("aaa",)))
    agg = PeriodAggregationSpec("agg", ("month",), estimation_method="linear_interpolation",
                                estimation_min_completeness=0.5, estimation_max_gap_days=3)
    t = aggregate_cohort(c, agg, products=PRODUCTS)
    spec = mspec(period_aggregations=("agg",), period_tolerance=TOL,
                 period_products=("partial_sum", "complete_total", "estimated_total"))
    res = score_comparison(c, spec, products=PRODUCTS, period_totals=[t])
    totals = t.table.frame

    for prod, n_periods in (("partial_sum", 4), ("complete_total", 2), ("estimated_total", 4)):
        rows = totals[(totals["product"] == prod) & totals["observed_total_mm"].notna()]
        assert len(rows) == n_periods
        e = rows["modeled_total_mm"] - rows["observed_total_mm"]
        r = metric(res, "rmse", product=prod)
        assert r["temporal_scale"] == "period" and r["period_type"] == "month"
        assert r["aggregation_id"] == "agg" and r["units"] == "mm"
        assert r["n_periods"] == n_periods
        assert r["value"] == pytest.approx(np.sqrt(np.mean(e ** 2)))
        assert metric(res, "percent_bias", product=prod)["value"] == pytest.approx(
            100 * e.sum() / rows["observed_total_mm"].sum())
        assert r["n_days"] == rows["n_days_matched"].sum()
    est = metric(res, "rmse", product="estimated_total")
    assert est["n_days_imputed"] == 3 and est["n_days"] == len(d) - 3
    assert est["period_completeness"] == pytest.approx((len(d) - 3) / len(d))
    assert pd.isna(metric(res, "rmse", product="partial_sum")["n_days_imputed"])
    daily = metric(res, "rmse", product="daily_pairs")
    assert daily["units"] == "mm d-1" and daily["n_days"] == len(d) - 3


def test_period_product_without_available_periods():
    d = pd.date_range("2024-01-01", "2024-02-29")
    keep = d != pd.Timestamp("2024-01-15")
    keep &= d != pd.Timestamp("2024-02-15")
    c = build(station(d), [ext("aaa", d[keep], 2.5)], cspec("model_available", models=("aaa",)))
    t = aggregate_cohort(c, PeriodAggregationSpec("agg", ("month",)), products=PRODUCTS)
    spec = mspec(period_aggregations=("agg",), period_tolerance=TOL,
                 period_products=("complete_total", "partial_sum"))
    res = score_comparison(c, spec, products=PRODUCTS, period_totals=[t])
    r = metric(res, "mbe", product="complete_total")
    assert pd.isna(r["value"]) and r["validity_reason"] == "no_available_periods"
    assert r["n_periods"] == 0
    p = metric(res, "mbe", product="partial_sum")
    # Jan 30 and Feb 28 matched days; daily error 0.5 mm, so partial-sum errors 15 and 14 mm
    assert p["value"] == pytest.approx(14.5) and p["n_periods"] == 2 and p["n_days"] == 58
    assert p["units"] == "mm"


def test_period_totals_from_another_cohort_are_refused(two_models):
    c = two_models[0]
    d = pd.date_range("2024-01-01", periods=40)
    other = build(station(d), [ext("aaa", d, 1.0)], cspec("model_available", models=("aaa",)))
    t = aggregate_cohort(other, PeriodAggregationSpec("agg", ("month",)), products=PRODUCTS)
    spec = mspec(period_aggregations=("agg",), period_tolerance=TOL,
                 period_products=("partial_sum",))
    with pytest.raises(ContractError, match="fingerprint"):
        score_comparison(c, spec, products=PRODUCTS, period_totals=[t])


def test_period_types_filter_and_unsupplied_aggregation(two_models):
    c = two_models[0]
    t = aggregate_cohort(c, PeriodAggregationSpec("agg", ("month", "year")), products=PRODUCTS)
    spec = mspec(period_aggregations=("agg", "other"), period_tolerance=TOL,
                 period_products=("partial_sum",), period_types=("year",))
    res = score_comparison(c, spec, products=PRODUCTS, period_totals=[t])
    f = res.table.frame
    assert set(f.loc[f["temporal_scale"] == "period", "period_type"]) == {"year"}
    assert "aggregation_not_supplied" in res.report.codes()


# --------------------------------------------------------------------------- cumulative

def test_cumulative_error_flags_missing_periods():
    d = pd.date_range("2024-01-01", periods=10)
    keep = ~d.isin(d[[3, 4, 7]])
    o = np.arange(1.0, 11.0)
    c = build(station(d, o), [ext("aaa", d[keep], o[keep] + 0.5)],
              cspec("model_available", models=("aaa",)))
    t = cumulative_error(c).frame
    assert len(t) == 7  # no filled days
    assert t["cumulative_error_mm"].to_numpy() == pytest.approx(0.5 * np.arange(1, 8))
    assert t["cumulative_observed_mm"].to_numpy() == pytest.approx(np.cumsum(o[keep]))
    assert t["missing_days_before"].tolist()[1:] == [0, 0, 2, 0, 1, 0]
    assert pd.isna(t["missing_days_before"].iloc[0])
    assert t["segment"].tolist() == [1, 1, 1, 2, 2, 3, 3]
    flagged = t["flags"].fillna("").str.contains("missing_period_before")
    assert t.loc[flagged, "date"].tolist() == [d[5], d[8]]
    assert t["n_days_cumulated"].tolist() == list(range(1, 8))
    assert (t["error_mm"] == t["modeled_mm"] - t["observed_mm"]).all()


# --------------------------------------------------------------------------- config

BASE = {
    "stations": {SITE: {"timezone": "UTC-07:00", "day_definition": "local_standard_time",
                        "interval_label": "end"}},
    "products": {"aaa": {"units": "mm/day", "day_definition": "unknown",
                         "temporal_support": "interpolated_daily"}},
}


def test_metric_set_config():
    cfg = config_from_dict({**BASE, "metric_sets": [{
        "metric_set_id": "m", "metrics": ["mbe", "kge_2009"],
        "daily_tolerance": {"mean": 0.01, "std": 0.001}}]})
    m = cfg.metric_set("m")
    assert m.daily_tolerance == NearZeroTolerance(0.01, 0.001)
    assert m.metrics == ("mbe", "kge_2009") and m.cumulative_error
    default = config_from_dict({**BASE, "metric_sets": [{
        "metric_set_id": "m", "daily_tolerance": {"mean": 0, "std": 0}}]}).metric_set("m")
    assert default.metrics == DEFAULT_METRICS


@pytest.mark.parametrize("body, match", [
    ({"metric_set_id": "m"}, "daily_tolerance"),
    ({"daily_tolerance": {"mean": -1, "std": 0}}, "non-negative"),
    ({"daily_tolerance": {"mean": 0.1}}, "non-negative"),
    ({"daily_tolerance": {"mean": 0.1, "std": 0, "rel": 1}}, "unknown configuration key"),
    ({"metrics": ["mape", "r2"]}, "metrics='r2'"),
    ({"metrics": ["mbe", "mbe"]}, "duplicate"),
    ({"period_aggregations": ["agg"]}, "together"),
    ({"period_aggregations": ["agg"], "period_products": ["partial_sum"]}, "period_tolerance"),
    ({"period_types": ["month"]}, "period_types without"),
    ({"period_aggregations": ["nope"], "period_products": ["partial_sum"],
      "period_tolerance": {"mean": 0, "std": 0}}, "not declared"),
    ({"period_aggregations": ["agg"], "period_products": ["estimated_total"],
      "period_tolerance": {"mean": 0, "std": 0}}, "estimation_method"),
    ({"comparisons": ["nope"]}, "not declared"),
    ({"daily": False, "cumulative_error": False}, "nothing to compute"),
])
def test_metric_set_config_errors(body, match):
    m = {"metric_set_id": "m", "daily_tolerance": {"mean": 0.01, "std": 0.001}}
    m.update(body)
    if "metric_set_id" in body and len(body) == 1:
        m.pop("daily_tolerance")
    data = {**BASE, "period_aggregations": [{"aggregation_id": "agg", "periods": ["month"]}],
            "metric_sets": [m]}
    with pytest.raises(ContractError, match=match):
        config_from_dict(data)
