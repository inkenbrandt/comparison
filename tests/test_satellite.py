"""Satellite support: clear-observation versus interpolated days (guide step 13).

Expected values are independent: subset masks are built from the synthetic flags
with numpy, metrics are recomputed with explicit numpy formulas, and acquisition
gaps are counted by hand on a short calendar.
"""

import numpy as np
import pandas as pd
import pytest

from etcompare.cohorts import build_cohort
from etcompare.config import (
    ComparisonSpec,
    ExtractionSide,
    FootprintSetSpec,
    MetricSpec,
    NearZeroTolerance,
    SatelliteSupportSpec,
    SupportSourceSpec,
    config_from_dict,
)
from etcompare.errors import ContractError, DataValidationError
from etcompare.io.tables import read_table, write_table
from etcompare.metrics import compute_metrics
from etcompare.satellite import acquisition_gaps, assess_satellite_support, support_status

TOL = NearZeroTolerance(mean=1e-9, std=1e-12)
FP = ExtractionSide("footprint_conservative", "SET")
SETS = [FootprintSetSpec("SET", "x", weighting_method="m",
                         value_semantics="normalized_cell_weight",
                         temporal_weighting="reference_et_weighted")]
MODELS = ("aaa", "bbb", "ccc")
DATES = pd.date_range("2025-03-01", periods=120, freq="D")
SITE = "US-AAA"


def station(dates=DATES):
    rng = np.random.default_rng(1)
    raw = rng.uniform(1, 6, len(dates))
    return pd.DataFrame({
        "site_id": SITE, "date": dates, "et_raw_mm": raw, "et_corrected_mm": raw * 1.2,
        "gap_filled": False, "closure_factor": 1.2, "raw_available": True,
        "reference_provenance": "corrected_from_raw"})


def ext(model, dates, vals):
    return pd.DataFrame({
        "site_id": SITE, "date": dates, "period": "day", "model": model,
        "extraction_method": FP.extraction_method, "footprint_set_id": FP.footprint_set_id,
        "footprint_method": "m", "et_mm": vals, "fp_weight_sum": 1.0,
        "fp_weight_retained_frac": 1.0, "fp_weight_retained_denominator": None,
        "support_id": None, "day_alignment": "declared", "model_asset_id": None,
        "footprint_asset_id": None, "source_path": None})


def cohort(kind="common_date", models=MODELS):
    obs = station()
    rng = np.random.default_rng(4)
    truth = obs["et_corrected_mm"].to_numpy()
    frames = [ext(m, DATES, truth * s + rng.normal(0, 0.5, len(DATES)))  # a value every day
              for m, s in zip(models, (1.0, 1.1, 0.8), strict=False)]
    spec = ComparisonSpec("cmp", kind, models, ("et_corrected_measured",), (FP,))
    return build_cohort(obs, frames, spec, footprint_sets=SETS)


def mspec():
    return MetricSpec("mset", metrics=("mbe", "mae", "rmse"), daily_tolerance=TOL)


def sspec(sources=(), **kw):
    base = dict(satellite_support_id="sat", metric_set="mset", min_subset_days=10,
                sources=tuple(sources), metrics=("mbe", "mae", "rmse"))
    base.update(kw)
    return SatelliteSupportSpec(**base)


def src(models=MODELS, kind="per_site_flag", model_column=None, **kw):
    base = dict(source_id="flags", kind=kind, path="flags.csv", models=models,
                authority="synthetic product QA band", meaning="clear observation at the tower",
                site_column="site", date_column="day", value_column="clear",
                model_column=model_column)
    base.update(kw)
    return SupportSourceSpec(**base)


# ----------------------------------------------------------------------- no metadata


def test_daily_values_without_acquisition_metadata_invent_no_flags():
    c = cohort()  # every model has a value on every day: interpolated daily product
    res = assess_satellite_support(c, sspec(), mspec(), support=None)
    assert len(res.errors.frame) == 0
    av = res.availability.frame
    assert list(av["status"]) == ["not_available"]
    assert av["reason_code"].iloc[0] == "no_acquisition_metadata"
    assert "never inferred from missing rows" in av["reason"].iloc[0]


def test_missing_rows_are_unknown_not_interpolated():
    c = cohort()
    # Flags only on the first 60 days (alternating clear / not clear).
    t = pd.DataFrame({"site": SITE, "day": DATES[:60], "clear": np.arange(60) % 2 == 0})
    sources = [src()]
    sup = support_status(sources, {"flags": t})
    assert set(sup["support_status"]) == {"clear_observation", "interpolated"}
    assert len(sup) == 60 * 3  # one row per model and listed day, nothing invented
    res = assess_satellite_support(c, sspec(sources), mspec(), support=sup)
    e = res.errors.frame
    row = e[(e["subset"] == "interpolated") & (e["metric"] == "mae")].iloc[0]
    assert row["n_days"] == 30 and row["n_days_unknown_support"] == 60
    assert row["n_days_model_unknown"] == 60
    assert e[e["subset"] == "all_cohort_days"]["n_days"].iloc[0] == 120


# ----------------------------------------------------------------------- subsets


def per_model_flags():
    rng = np.random.default_rng(7)
    base = rng.uniform(size=len(DATES)) < 0.4
    rows = []
    for m in MODELS:
        f = base.copy()
        if m == "ccc":
            f[[3, 10, 50]] = ~f[[3, 10, 50]]  # three days where ccc differs
        rows.append(pd.DataFrame({"site": SITE, "day": DATES, "model": m, "clear": f}))
    return pd.concat(rows, ignore_index=True), base


def test_clear_and_interpolated_subsets_identical_days_and_metrics_match_numpy():
    c = cohort()
    t, base = per_model_flags()
    sources = [src(model_column="model")]
    sup = support_status(sources, {"flags": t})
    res = assess_satellite_support(c, sspec(sources), mspec(), support=sup)
    e = res.errors.frame
    mixed = np.zeros(len(DATES), bool)
    mixed[[3, 10, 50]] = True
    clear = base & ~mixed
    interp = ~base & ~mixed
    paired = c.paired.frame
    for subset, mask in (("clear_observation", clear), ("interpolated", interp)):
        days = set(DATES[mask])
        rows = e[e["subset"] == subset]
        assert (rows["n_days"] == mask.sum()).all()  # identical across models
        assert (rows["n_days_mixed_support"] == 3).all()
        for m in MODELS:
            p = paired[(paired["model"] == m) & paired["date"].isin(days)]
            err = p["modeled_mm"].to_numpy() - p["observed_mm"].to_numpy()
            got = rows[rows["model"] == m].set_index("metric")["value"]
            assert got["mbe"] == pytest.approx(err.mean(), abs=1e-12)
            assert got["mae"] == pytest.approx(np.abs(err).mean(), abs=1e-12)
            assert got["rmse"] == pytest.approx(np.sqrt((err ** 2).mean()), abs=1e-12)
            cm = compute_metrics(p["observed_mm"], p["modeled_mm"], tolerance=TOL,
                                 metrics=("mbe", "mae", "rmse"))
            assert got["rmse"] == cm["rmse"].value
    # Each model's own support is recorded separately.
    a = e[(e["subset"] == "clear_observation") & (e["metric"] == "mbe")].set_index("model")
    assert a.loc["aaa", "n_days_model_clear"] == base.sum()
    assert a.loc["ccc", "n_days_model_clear"] != a.loc["aaa", "n_days_model_clear"]
    assert e["flags"].str.contains("days_identical_across_models").all()


def test_sparse_subset_has_status_not_value():
    c = cohort()
    t = pd.DataFrame({"site": SITE, "day": DATES, "clear": np.arange(120) < 5})
    sources = [src()]
    res = assess_satellite_support(c, sspec(sources), mspec(),
                                   support=support_status(sources, {"flags": t}))
    e = res.errors.frame
    clear = e[e["subset"] == "clear_observation"]
    assert set(clear["status"]) == {"sparse_subset"} and clear["value"].isna().all()


def test_count_source_and_gap_variables_by_hand():
    days = pd.date_range("2025-01-01", periods=10, freq="D")
    clear = pd.DatetimeIndex(["2025-01-02", "2025-01-06"])
    g = acquisition_gaps(days, clear)
    # day:      1   2  3  4  5  6  7  8  9  10
    assert np.allclose(g["days_since_previous_clear"],
                       [np.nan, 0, 1, 2, 3, 0, 1, 2, 3, 4], equal_nan=True)
    assert np.allclose(g["days_to_nearest_clear"], [1, 0, 1, 2, 1, 0, 1, 2, 3, 4], equal_nan=True)
    assert np.allclose(g["enclosing_gap_days"],
                       [np.nan, 0, 4, 4, 4, 0, np.nan, np.nan, np.nan, np.nan], equal_nan=True)
    c = cohort()
    counts = pd.DataFrame({"site": SITE, "day": DATES, "n": (np.arange(120) % 8 == 0) * 2})
    sources = [src(kind="per_site_count", value_column="n", min_clear_count=1)]
    sup = support_status(sources, {"flags": counts})
    assert (sup["support_status"] == "clear_observation").sum() == 15 * 3
    spec = sspec(sources, gap_variables={"days_since_previous_clear": [0, 1, 4, 8],
                                         "enclosing_gap_days": [0, 1, 9]})
    e = assess_satellite_support(c, spec, mspec(), support=sup).errors.frame
    since = e[(e["variable"] == "days_since_previous_clear") & (e["model"] == "aaa")
              & (e["metric"] == "mae")].set_index("subset")
    # Every 8th day is clear: since-previous is 0 on 15 days, 1..3 on 45, 4..7 on 60.
    assert since.loc["[0, 1)", "n_days"] == 15 and since.loc["[1, 4)", "n_days"] == 45
    assert since.loc["[4, 8]", "n_days"] == 60
    assert not since["retrospective"].any()
    enc = e[e["variable"] == "enclosing_gap_days"]
    assert enc["retrospective"].all()
    assert enc["flags"].str.contains("retrospective_uses_later_acquisitions").all()
    # The last 7 days follow the final clear day (day 113): no enclosing gap.
    miss = enc[(enc["subset"] == "variable_missing") & (enc["model"] == "aaa")]
    assert (miss["n_days"] == 7).all() and miss["value"].isna().all()


def test_unfrozen_cohort_refused_and_missing_model_support():
    with pytest.raises(ContractError, match="does not freeze"):
        assess_satellite_support(cohort(kind="model_available", models=("aaa",)), sspec(),
                                 mspec())
    c = cohort()
    t = pd.DataFrame({"site": SITE, "day": DATES, "clear": True})
    sources = [src(models=("aaa", "bbb"))]
    res = assess_satellite_support(c, sspec(sources), mspec(),
                                   support=support_status(sources, {"flags": t}))
    assert res.availability.frame["reason_code"].iloc[0] == "support_missing_for_models"
    assert len(res.errors.frame) == 0


def test_flag_values_validated():
    with pytest.raises(DataValidationError, match="boolean or 0/1"):
        support_status([src()], {"flags": pd.DataFrame({"site": SITE, "day": DATES[:3],
                                                        "clear": ["yes", "no", "yes"]})})
    with pytest.raises(DataValidationError, match="null values"):
        support_status([src()], {"flags": pd.DataFrame({"site": SITE, "day": DATES[:2],
                                                        "clear": [True, None]})})


def test_errors_round_trip_parquet(tmp_path):
    c = cohort()
    t, _ = per_model_flags()
    sources = [src(model_column="model")]
    res = assess_satellite_support(c, sspec(sources, gap_variables={
        "days_to_nearest_clear": [0, 2, 10]}), mspec(), support=support_status(
            sources, {"flags": t}))
    for t_ in (res.errors, res.availability):
        back = read_table(write_table(t_, tmp_path / f"{t_.schema.name}.parquet"))
        pd.testing.assert_frame_equal(back.frame, t_.frame)


# ----------------------------------------------------------------------- config


BASE = {
    "stations": {SITE: {"timezone": "UTC-07:00", "day_definition": "local_standard_time",
                        "interval_label": "start"}},
    "products": {m: {"units": "mm/day", "day_definition": "unknown",
                     "temporal_support": "interpolated_daily"} for m in MODELS},
    "metric_sets": [{"metric_set_id": "mset", "metrics": ["mbe", "mae", "rmse"],
                     "daily_tolerance": {"mean": 0.01, "std": 0.001}}],
    "comparisons": [
        {"comparison_id": "frozen", "kind": "common_date", "models": list(MODELS),
         "reference_variants": ["et_corrected_measured"],
         "sides": [{"extraction_method": "x", "footprint_set_id": "none"}]},
        {"comparison_id": "coverage", "kind": "model_available", "models": list(MODELS),
         "reference_variants": ["et_corrected_measured"],
         "sides": [{"extraction_method": "x", "footprint_set_id": "none"}]},
    ],
}


def source_body(**kw):
    b = {"source_id": "f", "kind": "per_pixel_flag", "path": "f.csv", "models": ["aaa"],
         "authority": "product QA", "meaning": "clear", "site_column": "s",
         "date_column": "d", "value_column": "v"}
    b.update(kw)
    return b


def sat_body(**kw):
    b = {"satellite_support_id": "sat", "metric_set": "mset", "min_subset_days": 20,
         "sources": [source_body()], "comparisons": ["frozen"]}
    b.update(kw)
    return b


def test_config_accepts_satellite_body():
    cfg = config_from_dict({**BASE, "satellite_support": [sat_body()]})
    assert cfg.satellite_support_spec("sat").sources[0].authority == "product QA"
    empty = config_from_dict({**BASE, "satellite_support": [sat_body(sources=[])]})
    assert empty.satellite_support[0].sources == ()


@pytest.mark.parametrize("body, match", [
    (sat_body(sources=[source_body(authority=None)]), "authority not declared"),
    (sat_body(sources=[source_body(meaning=None)]), "meaning not declared"),
    (sat_body(sources=[source_body(kind="scene_cloud_cover")]), "never a clear-observation"),
    (sat_body(sources=[source_body(kind="per_site_count")]), "min_clear_count"),
    (sat_body(sources=[source_body(kind="guess")]), "not one of"),
    (sat_body(min_subset_days=None), "min_subset_days not declared"),
    (sat_body(comparisons=["coverage"]), "does not freeze"),
    (sat_body(sources=[source_body(), source_body(source_id="g")]), "two support sources"),
    (sat_body(sources=[source_body(models=["zzz"])]), "undeclared models"),
    (sat_body(gap_variables={"cloudiness": [0, 1]}), "not one of"),
    (sat_body(metrics=["nse"]), "not one of"),
])
def test_config_rejections(body, match):
    with pytest.raises(ContractError, match=match):
        config_from_dict({**BASE, "satellite_support": [body]})
