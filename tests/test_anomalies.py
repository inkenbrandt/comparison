"""Anomalies: held-out climatologies and exploratory detrending (guide step 14)."""

import numpy as np
import pandas as pd
import pytest

from etcompare.anomalies import (
    climatology,
    compute_anomalies,
    day_of_year_365,
    fold_labels,
    moving_mean,
)
from etcompare.config import AnomalySpec, MetricSpec, NearZeroTolerance
from etcompare.errors import ContractError
from etcompare.metrics import compute_metrics
from test_aggregation import build_cohort, cspec, ext, station

METRICS = MetricSpec("m", NearZeroTolerance(0.01, 0.001))
YEARS = pd.date_range("2021-01-01", "2023-12-31")


def clim_spec(**over):
    kw = dict(anomaly_id="clim", metric_set="m", method="held_out_climatology", min_days=10,
              fold_unit="year", climatology_half_window_days=7, min_training_values=10,
              min_training_years=2, leap_day="as_feb28")
    kw.update(over)
    return AnomalySpec(**kw)


def mov_spec(**over):
    kw = dict(anomaly_id="mov", metric_set="m", method="exploratory_moving_mean", min_days=10,
              window_days=31, min_window_completeness=0.7)
    kw.update(over)
    return AnomalySpec(**kw)


def seasonal(days, amp=3.0, mean=4.0):
    return mean + amp * np.sin(2 * np.pi * (day_of_year_365(days) / 365.0))


def cohort(obs, mod, days=YEARS, filled=()):
    return build_cohort(station(days, obs, filled=filled), [ext("aaa", days, mod)], cspec())


def rows(r, **match):
    f = r.metrics.frame
    for k, v in match.items():
        f = f[f[k] == v]
    return f.set_index("metric")


def test_leap_day_and_folds():
    d = pd.DatetimeIndex(["2024-02-28", "2024-02-29", "2024-03-01", "2023-03-01",
                          "2024-12-31"])
    assert day_of_year_365(d).tolist() == [58, 58, 59, 59, 364]
    assert fold_labels(pd.DatetimeIndex(["2024-09-30", "2024-10-01"]), "water_year").tolist() \
        == [2024, 2025]


def test_climatology_window_is_circular_and_sparse_rule_holds():
    doy = np.array([0, 1, 364, 100])
    vals = np.array([1.0, 2.0, 3.0, 9.0])
    folds = np.array([1, 2, 1, 1])
    c, n, y = climatology(doy, vals, folds, np.array([0, 100]), half_window=1, min_values=3,
                          min_years=2)
    assert c[0] == pytest.approx(2.0) and (n[0], y[0]) == (3, 2)          # 364 wraps to 0
    assert np.isnan(c[1]) and (n[1], y[1]) == (1, 1)                       # sparse: no value


def test_held_out_climatology_uses_no_test_observations():
    """Guide gate: held-out anomalies use no test observations to fit climatologies."""
    obs = seasonal(YEARS)
    r1 = compute_anomalies(cohort(obs, obs + 0.5), clim_spec(), METRICS)
    test = YEARS.year == 2022
    changed = obs.copy()
    changed[test] += 100.0                                          # perturb the held-out year
    r2 = compute_anomalies(cohort(changed, obs + 0.5), clim_spec(), METRICS)
    d1, d2 = (r.daily.frame.set_index("date") for r in (r1, r2))
    sel = d1.index.year == 2022
    pd.testing.assert_series_equal(d1.loc[sel, "observed_baseline_mm"],
                                   d2.loc[sel, "observed_baseline_mm"])
    # the other years' baselines are trained on 2022, so they do move
    assert not np.allclose(d1.loc[~sel, "observed_baseline_mm"].dropna(),
                           d2.loc[~sel, "observed_baseline_mm"].dropna())


def test_constant_bias_vanishes_from_anomalies_but_raw_bias_is_reported():
    obs = seasonal(YEARS)
    r = compute_anomalies(cohort(obs, obs + 0.5), clim_spec(), METRICS)
    raw = rows(r, fold="all_folds", series_kind="raw")
    anom = rows(r, fold="all_folds", series_kind="anomaly")
    assert raw.loc["mbe", "value"] == pytest.approx(0.5)
    assert anom.loc["mbe", "value"] == pytest.approx(0.0, abs=1e-12)
    assert raw.loc["mbe", "n_days"] == anom.loc["mbe", "n_days"] > 0       # same days
    assert set(rows(r).inference) == {"held_out"}
    folds = set(r.metrics.frame.fold)
    assert folds == {"2021", "2022", "2023", "all_folds"}
    assert rows(r, fold="2022").training_folds.iloc[0] == "2021;2023"


def test_metrics_match_compute_metrics():
    rng = np.random.default_rng(1)
    obs = seasonal(YEARS) + rng.normal(0, 0.3, len(YEARS))
    mod = seasonal(YEARS) * 1.1 + rng.normal(0, 0.3, len(YEARS))
    r = compute_anomalies(cohort(obs, mod), clim_spec(), METRICS)
    d = r.daily.frame
    d = d[(d.fold == "2023") & (d.status == "ok")]
    want = compute_metrics(d.observed_anomaly_mm, d.modeled_anomaly_mm,
                           tolerance=METRICS.daily_tolerance, metrics=("rmse", "pearson_r"))
    got = rows(r, fold="2023", series_kind="anomaly")
    assert got.loc["rmse", "value"] == pytest.approx(want["rmse"].value)
    assert got.loc["pearson_r", "value"] == pytest.approx(want["pearson_r"].value)


def test_too_few_years_is_insufficient_not_fitted():
    days = pd.date_range("2024-06-05", "2025-10-09")       # two calendar years, like US-UTJ
    obs = seasonal(days)
    r = compute_anomalies(cohort(obs, obs, days=days), clim_spec(), METRICS)
    assert set(r.metrics.frame.status) == {"insufficient_training_years"}
    assert r.metrics.frame.value.isna().all()
    assert set(r.daily.frame.status) == {"baseline_unavailable"}
    assert "insufficient_training_years" in r.report.codes()


def test_exploratory_moving_mean():
    days = pd.date_range("2024-06-01", "2024-09-30")
    obs = np.full(len(days), 4.0)
    obs[days == "2024-07-15"] = 7.0                          # one departure of +3
    mod = obs + 1.0
    r = compute_anomalies(cohort(obs, mod, days=days), mov_spec(), METRICS)
    d = r.daily.frame.set_index("date")
    assert d.loc["2024-07-15", "observed_anomaly_mm"] == pytest.approx(3.0 - 3.0 / 31)
    assert d.loc["2024-06-01", "status"] == "baseline_unavailable"   # 16 of 31 days < 0.7
    f = r.metrics.frame
    assert set(f.inference) == {"exploratory"} and set(f.fold) == {"in_sample"}
    assert {"exploratory_in_sample_detrending", "no_seasonal_generalization"} <= set(
        f["flags"].iloc[0].split(";"))
    assert rows(r, series_kind="raw").loc["mbe", "value"] == pytest.approx(1.0)
    assert rows(r, series_kind="anomaly").loc["mbe", "value"] == pytest.approx(0.0, abs=1e-12)


def test_moving_mean_keeps_gaps():
    days = pd.DatetimeIndex(["2024-01-01", "2024-01-02", "2024-01-10"])
    m, n = moving_mean(days, np.array([1.0, 3.0, 5.0]), window=3, completeness=0.6)
    assert m[0] == pytest.approx(2.0) and m[1] == pytest.approx(2.0)
    assert np.isnan(m[2]) and n.tolist() == [2, 2, 1]


def test_sparse_samples_and_gap_filled_flags():
    days = pd.date_range("2024-06-01", "2024-07-31")
    obs = seasonal(days)
    filled = days[25:30]                   # mid-record: these days have a baseline
    r = compute_anomalies(cohort(obs, obs, days=days, filled=filled), mov_spec(min_days=500),
                          METRICS)
    f = r.metrics.frame
    assert set(f.status) == {"sparse_sample"}
    r = compute_anomalies(cohort(obs, obs, days=days, filled=filled), mov_spec(), METRICS)
    assert "shared_gridmet_drivers" in r.metrics.frame["flags"].iloc[0]


def test_metric_set_must_match():
    with pytest.raises(ContractError, match="metric set"):
        compute_anomalies(cohort(seasonal(YEARS), seasonal(YEARS)), clim_spec(metric_set="x"),
                          METRICS)


@pytest.mark.parametrize("make, over, match", [
    (clim_spec, {"method": "zscore"}, "method"),
    (clim_spec, {"fold_unit": None}, "fold_unit not declared"),
    (clim_spec, {"leap_day": "drop"}, "leap_day"),
    (clim_spec, {"window_days": 31}, "does not apply"),
    (clim_spec, {"metrics": ("nse",)}, "metrics"),
    (mov_spec, {"window_days": 30}, "odd"),
    (mov_spec, {"min_window_completeness": None}, "not declared"),
    (mov_spec, {"min_training_years": 2}, "does not apply"),
])
def test_declaration_refuses(make, over, match):
    with pytest.raises(ContractError, match=match):
        make(**over)
