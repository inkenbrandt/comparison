"""Paired block-bootstrap intervals (guide step 10).

Expected values are computed independently in the tests: block geometry from
the dates themselves, AR(1) sampling variability by Monte Carlo with
scipy.signal.lfilter, autocorrelation with explicit numpy loops, and interval
bounds with numpy.quantile on the returned replicates.
"""

import numpy as np
import pandas as pd
import pytest
from scipy import signal

import etcompare.bootstrap as bs
from etcompare.aggregation import aggregate_cohort
from etcompare.bootstrap import (
    block_indices,
    block_support,
    bootstrap_comparison,
    daily_samples,
    draw_blocks,
    residual_autocorrelation,
    segment_bounds,
)
from etcompare.cohorts import build_cohort
from etcompare.config import (
    BootstrapSpec,
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
from etcompare.metrics import score_comparison
from etcompare.schemas import INTERVALS, RESIDUAL_AUTOCORRELATION

TOL = NearZeroTolerance(mean=1e-9, std=1e-12)
SITE = "US-XXX"
FP = ExtractionSide("footprint_conservative", "SET")
PIX = ExtractionSide("pixel_window_3x3", "none")
SETS = [FootprintSetSpec("SET", "x", weighting_method="nldas",
                         value_semantics="normalized_cell_weight",
                         temporal_weighting="reference_et_weighted")]
PRODUCTS = {m: ProductSpec(m, "mm/day", "unknown", "interpolated_daily") for m in ("aaa", "bbb")}


def ext(model, dates, vals, side=FP):
    n = len(dates)
    vals = np.broadcast_to(np.asarray(vals, dtype=float), (n,))
    return pd.DataFrame({
        "site_id": SITE, "date": dates, "period": "day", "model": model,
        "extraction_method": side.extraction_method, "footprint_set_id": side.footprint_set_id,
        "footprint_method": "m", "et_mm": vals, "fp_weight_sum": 1.0,
        "fp_weight_retained_frac": 1.0 if side.footprint_set_id != "none" else np.nan,
        "fp_weight_retained_denominator": None, "support_id": None, "day_alignment": "declared",
        "model_asset_id": None, "footprint_asset_id": None, "source_path": None,
    })


def station(dates, vals=2.0):
    n = len(dates)
    vals = np.broadcast_to(np.asarray(vals, dtype=float), (n,)).copy()
    return pd.DataFrame({
        "site_id": SITE, "date": dates, "et_raw_mm": vals, "et_corrected_mm": vals,
        "gap_filled": False, "raw_available": True, "reference_provenance": "corrected_from_raw",
    })


def cspec(kind="model_available", models=("aaa",), variants=("et_corrected_inclusive",),
          sides=(FP,), **kw):
    return ComparisonSpec("cmp", kind, models, variants, sides, **kw)


def mspec(metrics=("mbe", "mae", "rmse", "pearson_r"), **kw):
    kw.setdefault("daily_tolerance", TOL)
    return MetricSpec("mset", metrics=metrics, **kw)


def bspec(**kw):
    base = dict(bootstrap_id="bs", metric_set="mset", block_lengths=(1, 7), seed=42,
                n_replicates=200, confidence_level=0.9, min_blocks=5, segment_boundary="none")
    base.update(kw)
    return BootstrapSpec(**base)


def build(obs, frames, spec):
    return build_cohort(obs, frames, spec, footprint_sets=SETS)


def run(cohort, b=None, m=None, **kw):
    return bootstrap_comparison(cohort, m or mspec(), b or bspec(), products=PRODUCTS, **kw)


def rows(result, **match):
    f = result.intervals.frame
    for k, v in match.items():
        f = f[f[k] == v]
    return f.reset_index(drop=True)


def gamma_series(n, seed=0):
    return np.random.default_rng(seed).gamma(3.0, 1.0, n)


# --------------------------------------------------------------------------- blocks

def test_segments_split_at_gaps_sites_and_season_years():
    d = pd.to_datetime(["2024-09-28", "2024-09-29", "2024-09-30", "2024-10-01", "2024-10-02",
                        "2024-10-04", "2024-12-31", "2025-01-01"])
    assert segment_bounds(d, boundary="none").tolist() == [[0, 5], [5, 6], [6, 8]]
    assert segment_bounds(d, boundary="year").tolist() == [[0, 5], [5, 6], [6, 7], [7, 8]]
    assert segment_bounds(d, boundary="water_year").tolist() == [[0, 3], [3, 5], [5, 6],
                                                                 [6, 8]]
    assert segment_bounds(d, boundary="water_year",
                          water_year_start_month=1).tolist() == [[0, 5], [5, 6], [6, 7], [7, 8]]
    # periods: consecutive months continue a segment, a missing month ends it
    starts = pd.to_datetime(["2025-01-01", "2025-02-01", "2025-04-01"])
    ends = pd.to_datetime(["2025-01-31", "2025-02-28", "2025-04-30"])
    assert segment_bounds(starts, ends, boundary="none").tolist() == [[0, 2], [2, 3]]
    with pytest.raises(ValueError, match="sorted"):
        segment_bounds(d[::-1], boundary="none")


def test_support_counts_disjoint_full_blocks():
    bounds = np.array([[0, 20], [20, 26], [26, 27]])
    s = block_support(bounds, 7)
    assert (s.n_units, s.n_segments, s.n_blocks, s.n_short_segments) == (27, 3, 2, 2)
    assert block_support(bounds, 1).n_blocks == 27


@pytest.mark.parametrize("b", [1, 3, 7, 28])
def test_blocks_never_bridge_gaps_or_boundaries(b):
    rng = np.random.default_rng(5)
    all_days = pd.date_range("2024-09-01", "2025-03-31")
    days = all_days[rng.random(len(all_days)) > 0.15]  # random gaps of 1+ days
    bounds = segment_bounds(days, boundary="water_year")
    wy = days.year + (days.month >= 10)
    for _ in range(200):
        starts, lengths = draw_blocks(bounds, b, rng)
        assert lengths.sum() == len(days) and (lengths >= 1).all() and (lengths <= b).all()
        for s, n in zip(starts, lengths, strict=True):
            block = days[s:s + n]
            # independently: consecutive calendar days, one water year
            assert (np.diff(block.values).astype("timedelta64[D]").astype(int) == 1).all()
            assert len(set(wy[s:s + n])) == 1
        idx = block_indices(starts, lengths)
        assert len(idx) == len(days) and idx.min() >= 0 and idx.max() < len(days)
    if b == 1:  # b = 1 is the iid-day bootstrap
        starts, lengths = draw_blocks(bounds, 1, np.random.default_rng(1))
        assert (lengths == 1).all() and len(starts) == len(days)


def test_moving_block_edge_effect_is_as_documented():
    # One segment of 30 days, b = 5: day j is covered by min(j + 1, 5) starts.
    bounds = np.array([[0, 30]])
    rng = np.random.default_rng(0)
    counts = np.zeros(30)
    for _ in range(20000):
        counts += np.bincount(block_indices(*draw_blocks(bounds, 5, rng)), minlength=30)
    rel = counts / counts[10:25].mean()  # interior days, away from the last-block cut
    assert rel[:5] == pytest.approx(np.array([1, 2, 3, 4, 5]) / 5, abs=0.03)


# --------------------------------------------------------------------------- gates

def test_identical_predictions_give_zero_paired_differences():
    d = pd.date_range("2024-01-01", periods=120)
    o = gamma_series(len(d))
    p = o + np.random.default_rng(1).normal(0.3, 0.5, len(d))
    c = build(station(d, o), [ext("aaa", d, p), ext("bbb", d, p)],
              cspec("pairwise_model", models=("aaa", "bbb")))
    res = run(c, m=mspec(("mbe", "mae", "rmse", "pearson_r", "nse", "kge_2009")))
    con = rows(res, contrast_dimension="model")
    assert len(con) and set(con["contrast_baseline"]) == {"aaa"} and set(con["model"]) == {"bbb"}
    assert (con["status"] == "ok").all()
    assert (con["estimate"] == 0).all() and (con["lower"] == 0).all() and (con["upper"] == 0).all()
    idx, vals = res.replicates()
    for (b, metric), g in idx.groupby(["block_length", "metric"]):
        a = vals[g.index[g["model"] == "aaa"][0]]
        bb = vals[g.index[g["model"] == "bbb"][0]]
        assert np.isfinite(a).all()
        assert (bb - a == 0).all(), (b, metric)  # zero in every replicate


def test_identical_sides_give_zero_method_contrast():
    d = pd.date_range("2024-01-01", periods=60)
    o = gamma_series(len(d))
    p = o * 1.1
    c = build(station(d, o), [ext("aaa", d, p), ext("aaa", d, p, side=PIX)],
              cspec("paired_method", sides=(FP, PIX)))
    con = rows(run(c), contrast_dimension="extraction_side")
    assert set(con["contrast_baseline"]) == {"footprint_conservative@SET"}
    assert set(con["extraction_method"]) == {"pixel_window_3x3"}
    assert (con[["estimate", "lower", "upper"]] == 0).all().all()


def test_same_seed_gives_identical_results():
    d = pd.date_range("2024-01-01", periods=90)
    o = gamma_series(len(d))
    c = build(station(d, o), [ext("aaa", d, o + np.random.default_rng(2).normal(0, 1, len(d)))],
              cspec())
    a, b = run(c), run(c)
    pd.testing.assert_frame_equal(a.intervals.frame, b.intervals.frame)
    assert np.array_equal(a.replicates()[1], b.replicates()[1], equal_nan=True)
    other = run(c, b=bspec(seed=43)).intervals.frame
    assert not np.allclose(other["lower"], a.intervals.frame["lower"], equal_nan=True)


def test_ar1_errors_iid_resampling_is_too_narrow():
    """Gate: with AR(1) errors, iid-day intervals are overconfident; blocks are not."""
    n, phi, sigma = 1000, 0.8, 0.5
    rng = np.random.default_rng(11)
    shocks = rng.normal(0, sigma, n + 500)
    e = signal.lfilter([1.0], [1.0, -phi], shocks)[500:]  # stationary after burn-in
    d = pd.date_range("2020-01-01", periods=n)
    o = gamma_series(n, seed=3)
    c = build(station(d, o), [ext("aaa", d, o + e)], cspec())
    res = run(c, b=bspec(block_lengths=(1, 28), n_replicates=1000, confidence_level=0.95,
                         min_blocks=10), m=mspec(("mbe",)))

    # independent expectation: Monte Carlo sampling SD of the mean error of AR(1) series
    sims = signal.lfilter([1.0], [1.0, -phi], rng.normal(0, sigma, (4000, n + 500)),
                          axis=1)[:, 500:]
    true_width = 2 * 1.959964 * sims.mean(axis=1).std()
    theory = 2 * 1.959964 * sigma / (1 - phi) / np.sqrt(n)  # large-n AR(1) result
    assert true_width == pytest.approx(theory, rel=0.1)

    r = rows(res, metric="mbe", interval_method="percentile").set_index("block_length")
    assert (r["status"] == "ok").all()
    w_iid = r.loc[1, "upper"] - r.loc[1, "lower"]
    w_block = r.loc[28, "upper"] - r.loc[28, "lower"]
    # iid width ~ sqrt((1 - phi) / (1 + phi)) = 1/3 of the true width
    assert w_iid < 0.5 * true_width
    assert 0.7 * true_width < w_block < 1.3 * true_width
    assert w_iid < 0.6 * w_block
    acf = residual_autocorrelation(c, 3).frame
    assert acf.loc[acf["lag"] == 1, "autocorrelation"].iloc[0] == pytest.approx(phi, abs=0.06)


def test_too_few_blocks_gives_insufficient_support():
    d = pd.date_range("2024-01-01", periods=30)
    o = gamma_series(30)
    c = build(station(d, o), [ext("aaa", d, o + 0.5)], cspec())
    res = run(c, b=bspec(block_lengths=(1, 7), min_blocks=10))
    r7 = rows(res, block_length=7)
    assert (r7["status"] == "insufficient_support").all()
    assert r7["lower"].isna().all() and r7["upper"].isna().all()
    assert (r7["n_blocks"] == 4).all() and (r7["n_replicates_valid"] == 0).all()
    assert r7["status_detail"].str.contains("4 disjoint 7-site_day blocks in 1 segment").all()
    assert r7["estimate"].notna().all()  # the point estimate is still reported
    assert (rows(res, block_length=1)["status"] == "ok").all()
    assert "insufficient_support" in res.report.codes()
    # gaps reduce support: five 6-day segments hold no 7-day block
    keep = np.arange(36) % 7 != 6
    d2 = pd.date_range("2024-01-01", periods=36)[keep]
    c2 = build(station(d2, 2.0), [ext("aaa", d2, 2.5)], cspec())
    r = rows(run(c2, b=bspec(block_lengths=(7,), min_blocks=1)), metric="mbe")
    assert (r["n_segments"] == 6).all() and (r["n_blocks"] == 0).all()
    assert (r["status"] == "insufficient_support").all()


def test_removing_a_day_from_one_side_removes_it_from_both():
    d = pd.date_range("2024-01-01", periods=60)
    o = gamma_series(len(d))
    fp = o + 0.2
    fp[17] = 500.0  # outlier on the footprint side only ...
    pix = o + 0.2
    keep = np.arange(len(d)) != 17  # ... and the window side has no value that day
    c = build(station(d, o), [ext("aaa", d, fp), ext("aaa", d[keep], pix[keep], side=PIX)],
              cspec("paired_method", sides=(FP, PIX)))
    (sample,) = daily_samples(c)
    assert d[17] not in sample.first and len(sample.first) == 59
    assert all(len(op[0]) == 59 for op in sample.series.values())
    res = run(c, m=mspec(("mbe", "mae", "rmse")))
    con = rows(res, contrast_dimension="extraction_side")
    assert (con[["estimate", "lower", "upper"]] == 0).all().all()
    idx, vals = res.replicates()
    for _, g in idx.groupby(["block_length", "metric"]):
        a = vals[g.index[g["extraction_method"] == "footprint_conservative"][0]]
        b = vals[g.index[g["extraction_method"] == "pixel_window_3x3"][0]]
        assert (a - b == 0).all() and (a < 100).all()  # the outlier day never enters


def test_tables_round_trip_through_parquet(tmp_path):
    d = pd.date_range("2024-01-01", periods=80)
    o = gamma_series(len(d))
    c = build(station(d, o), [ext("aaa", d, o * 0.9), ext("aaa", d[3:], o[3:], side=PIX)],
              cspec("paired_method", sides=(FP, PIX)))
    res = run(c, b=bspec(interval_methods=("percentile", "basic")))
    back = read_table(write_table(res.intervals, tmp_path / "i.parquet"))
    assert back.schema is INTERVALS and INTERVALS.version == "2"
    pd.testing.assert_frame_equal(back.frame, res.intervals.frame)
    assert back.attributes["scheme"] == "segment_truncated_moving_block"
    assert back.attributes["cohort_membership_sha256"] == c.fingerprint
    acf = read_table(write_table(res.autocorrelation, tmp_path / "a.parquet"))
    assert acf.schema is RESIDUAL_AUTOCORRELATION
    pd.testing.assert_frame_equal(acf.frame, res.autocorrelation.frame)


# --------------------------------------------------------------------------- behaviour

def test_metrics_are_recomputed_in_every_replicate(monkeypatch):
    d = pd.date_range("2024-01-01", periods=50)
    o = gamma_series(len(d))
    c = build(station(d, o), [ext("aaa", d, o + 1), ext("bbb", d, o - 1)],
              cspec("common_date", models=("aaa", "bbb")))
    calls = []
    real = bs.compute_metrics
    monkeypatch.setattr(bs, "compute_metrics",
                        lambda *a, **k: calls.append(1) or real(*a, **k))
    spec = bspec(block_lengths=(1, 5, 7), n_replicates=30)
    res = run(c, b=spec)
    assert len(calls) == 30 * 2 * 3  # replicates x series x block lengths
    point = score_comparison(c, mspec(), products=PRODUCTS).table.frame
    r = rows(res, contrast_dimension="none", block_length=7, interval_method="percentile")
    j = r.merge(point, on=["model", "metric"], suffixes=("", "_pt"), validate="one_to_one")
    assert (j["estimate"] == j["value"]).all()
    assert rows(res, contrast_dimension="model").empty  # common_date contrasts are step 11


def test_percentile_and_basic_bounds():
    d = pd.date_range("2024-01-01", periods=100)
    o = gamma_series(len(d))
    c = build(station(d, o), [ext("aaa", d, o + np.random.default_rng(4).gamma(1, 1, len(d)))],
              cspec())
    res = run(c, b=bspec(block_lengths=(7,), interval_methods=("percentile", "basic"),
                         confidence_level=0.8))
    idx, vals = res.replicates()
    for _, row in idx.iterrows():
        x = vals[_]
        q = np.quantile(x, [0.1, 0.9])
        ip = rows(res, metric=row["metric"], interval_method="percentile").iloc[0]
        ib = rows(res, metric=row["metric"], interval_method="basic").iloc[0]
        assert (ip["lower"], ip["upper"]) == pytest.approx(tuple(q))
        est = ip["estimate"]
        assert (ib["lower"], ib["upper"]) == pytest.approx((2 * est - q[1], 2 * est - q[0]))
        assert ip["n_replicates_valid"] == 200 and ip["confidence_level"] == 0.8
        assert ip["seed"] == 42 and ip["block_unit"] == "site_day"


def test_undefined_estimate_and_invalid_replicates():
    d = pd.date_range("2024-01-01", periods=60)
    o = np.full(len(d), 2.0)  # constant observations: r undefined everywhere
    c = build(station(d, o), [ext("aaa", d, gamma_series(len(d)))], cspec())
    res = run(c)
    r = rows(res, metric="pearson_r")
    assert (r["status"] == "estimate_undefined").all()
    assert set(r["status_detail"]) == {"constant_observed"}
    # constant except on two days: many replicates miss both days -> undefined
    o2 = o.copy()
    o2[[10, 40]] = [3.0, 1.0]
    c2 = build(station(d, o2), [ext("aaa", d, gamma_series(len(d)))], cspec())
    r2 = rows(run(c2, b=bspec(block_lengths=(7,), min_valid_fraction=0.9)), metric="pearson_r")
    assert (r2["status"] == "insufficient_valid_replicates").all()
    assert r2["lower"].isna().all()


def test_empty_cohort_rows():
    d = pd.date_range("2024-01-01", periods=10)
    c = build(station(d), [ext("aaa", d[:3], 1.0), ext("bbb", d[5:], 1.0)],
              cspec("common_date", models=("aaa", "bbb")))
    assert c.status == "empty"
    f = run(c).intervals.frame
    assert len(f) and (f["status"] == "estimate_undefined").all()
    assert set(f["status_detail"]) == {"empty_cohort"} and (f["n_segments"] == 0).all()


def test_contrast_sign_and_reference_contrasts():
    d = pd.date_range("2024-01-01", periods=90)
    o = gamma_series(len(d))
    noise = np.random.default_rng(8).normal(0, 1, len(d))
    c = build(station(d, o), [ext("aaa", d, o + 0.2 * noise),
                              ext("aaa", d, o + 1.5 * noise, side=PIX)],
              cspec("paired_method", sides=(FP, PIX)))
    mae = rows(run(c), metric="mae", contrast_dimension="extraction_side", block_length=7).iloc[0]
    # window - footprint: positive means the footprint side has the smaller error
    assert mae["estimate"] > 0 and mae["lower"] > 0 and mae["status"] == "ok"
    pb = rows(run(c, m=mspec(("percent_bias",))), contrast_dimension="extraction_side")
    assert set(pb["units"]) == {"percentage points"}
    # paired_reference: corrected minus raw, with identical day indices
    st = station(d, o).assign(et_corrected_mm=o * 1.2)
    c2 = build(st, [ext("aaa", d, o)],
               cspec("paired_reference", variants=("et_raw", "et_corrected_measured")))
    rr = rows(run(c2), metric="mbe", contrast_dimension="reference_variant")
    assert set(rr["contrast_baseline"]) == {"et_raw"}
    assert set(rr["reference_variant"]) == {"et_corrected_measured"}
    expect = np.mean(o - 1.2 * o) - np.mean(o - o)
    assert rr["estimate"].to_numpy() == pytest.approx(expect)


def test_period_metrics_resample_whole_periods():
    d = pd.date_range("2024-01-01", "2025-02-28")
    drop = (d.month == 6) & (d.year == 2024) & (d.day == 15)  # June 2024 incomplete
    o = gamma_series(len(d))
    c = build(station(d, o), [ext("aaa", d[~drop], (o * 1.1)[~drop])], cspec())
    agg = PeriodAggregationSpec("agg", ("month",))
    t = aggregate_cohort(c, agg, products=PRODUCTS)
    ms = mspec(("mbe", "rmse"), period_aggregations=("agg",), period_tolerance=TOL,
               period_products=("partial_sum", "complete_total"))
    res = run(c, m=ms, b=bspec(block_lengths=(7,), period_block_lengths=(1, 2), min_blocks=5),
              period_totals=[t])
    per = rows(res, temporal_scale="period")
    assert set(per["block_unit"]) == {"period"} and set(per["block_length"]) == {1, 2}
    comp = rows(res, product="complete_total", block_length=1, metric="mbe").iloc[0]
    # 13 complete months, split by the missing June into two segments (5 + 8)
    assert (comp["n_periods"], comp["n_segments"], comp["n_blocks"]) == (13, 2, 13)
    assert rows(res, product="complete_total", block_length=2).iloc[0]["n_blocks"] == 2 + 4
    part = rows(res, product="partial_sum", block_length=1, metric="mbe").iloc[0]
    assert (part["n_periods"], part["n_segments"]) == (14, 1)
    assert comp["status"] == "ok" and comp["units"] == "mm"
    # without period block lengths, period metrics get no intervals
    res2 = run(c, m=ms, b=bspec(block_lengths=(7,)), period_totals=[t])
    assert (rows(res2)["temporal_scale"] == "daily").all()
    assert "period_intervals_not_requested" in res2.report.codes()


def test_autocorrelation_uses_exact_calendar_lags():
    d = pd.date_range("2024-01-01", periods=40)
    keep = ~np.isin(np.arange(40), [10, 11, 25])
    days = d[keep]
    o = gamma_series(len(days))
    e = np.random.default_rng(9).normal(0, 1, len(days))
    c = build(station(days, o), [ext("aaa", days, o + e)], cspec())
    acf = residual_autocorrelation(c, 3).frame.set_index("lag")
    dd = e - e.mean()
    t = (days - days[0]).days.to_numpy()
    for k in (1, 2, 3):
        prod = [dd[i] * dd[j] for i in range(len(t)) for j in range(len(t)) if t[j] - t[i] == k]
        assert acf.loc[k, "n_pairs"] == len(prod)
        assert acf.loc[k, "autocorrelation"] == pytest.approx(np.mean(prod) / np.mean(dd ** 2))
        assert acf.loc[k, "white_noise_bound"] == pytest.approx(1.96 / np.sqrt(len(prod)))
    assert acf.loc[1, "n_pairs"] == 37 - 3  # 36 consecutive pairs minus the 3 broken by gaps
    assert (acf["n_days"] == 37).all()


# --------------------------------------------------------------------------- config

BASE = {
    "stations": {SITE: {"timezone": "UTC-07:00", "day_definition": "local_standard_time",
                        "interval_label": "end"}},
    "products": {"aaa": {"units": "mm/day", "day_definition": "unknown",
                         "temporal_support": "interpolated_daily"}},
    "metric_sets": [{"metric_set_id": "m", "daily_tolerance": {"mean": 0.01, "std": 0.001}}],
}
GOOD = {"bootstrap_id": "b", "metric_set": "m", "block_lengths": [7, 14, 28], "seed": 1,
        "n_replicates": 100, "confidence_level": 0.95, "min_blocks": 10,
        "segment_boundary": "water_year"}


def test_bootstrap_config():
    cfg = config_from_dict({**BASE, "bootstrap": [GOOD]})
    b = cfg.bootstrap_spec("b")
    assert b.block_lengths == (7, 14, 28) and b.interval_methods == ("percentile",)
    assert b.max_lag == 28 and b.period_block_lengths == () and b.min_valid_fraction == 0.9


@pytest.mark.parametrize("body, match", [
    ({"block_lengths": []}, "no block_lengths"),
    ({"block_lengths": [0, 7]}, "block_lengths must be an integer >= 1"),
    ({"block_lengths": [7.5]}, "block_lengths must be an integer"),
    ({"block_lengths": [7, 7]}, "duplicate block_lengths"),
    ({"seed": None}, "seed not declared"),
    ({"seed": -1}, "seed must be an integer >= 0"),
    ({"seed": True}, "seed must be an integer"),
    ({"n_replicates": 0}, "n_replicates must be an integer >= 1"),
    ({"n_replicates": None}, "n_replicates not declared"),
    ({"confidence_level": 1.0}, r"confidence_level must be in \(0, 1\)"),
    ({"confidence_level": 95}, r"confidence_level must be in \(0, 1\)"),
    ({"min_blocks": None}, "min_blocks not declared"),
    ({"min_blocks": 0}, "min_blocks must be an integer >= 1"),
    ({"interval_methods": ["bca"]}, "interval_methods='bca'"),
    ({"segment_boundary": None}, "segment_boundary not declared"),
    ({"segment_boundary": "season"}, "segment_boundary='season'"),
    ({"water_year_start_month": 13}, "water_year_start_month"),
    ({"min_valid_fraction": 0}, "min_valid_fraction"),
    ({"period_block_lengths": [1]}, "scores no period products"),
    ({"metric_set": "nope"}, "metric_set 'nope' not declared"),
    ({"comparisons": ["nope"]}, "not declared"),
    ({"bootstrap_id": "Bad-ID"}, "snake_case"),
    ({"block_length": 7}, "unknown configuration key"),
])
def test_bootstrap_config_errors(body, match):
    data = {**BASE, "bootstrap": [{**GOOD, **body}]}
    with pytest.raises(ContractError, match=match):
        config_from_dict(data)


def test_duplicate_bootstrap_ids_and_metric_set_mismatch():
    with pytest.raises(ContractError, match="duplicate bootstrap_id"):
        config_from_dict({**BASE, "bootstrap": [GOOD, GOOD]})
    d = pd.date_range("2024-01-01", periods=20)
    c = build(station(d), [ext("aaa", d, 2.5)], cspec())
    with pytest.raises(ContractError, match="resamples metric set"):
        bootstrap_comparison(c, mspec(), bspec(metric_set="other"), products=PRODUCTS)
