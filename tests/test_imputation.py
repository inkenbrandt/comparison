"""Imputed estimated totals, reference ET and crop-height growing seasons (guide step 8)."""

import dataclasses

import numpy as np
import pandas as pd
import pytest

from etcompare.adapters.crop_height import adapt_crop_height, growing_season_windows
from etcompare.adapters.station import adapt_station_daily
from etcompare.aggregation import aggregate_cohort
from etcompare.config import (
    CropHeightTableSpec,
    PeriodAggregationSpec,
    StationSpec,
    StationTableSpec,
    config_from_dict,
)
from etcompare.errors import ContractError, DataValidationError
from etcompare.io.tables import read_table, write_table
from test_aggregation import JAN, PRODUCTS, SITE, build_cohort, cspec, ext, one, station


def imp(method="linear_interpolation", max_gap=3, min_completeness=0.5, periods=("month",)):
    return PeriodAggregationSpec("agg", periods, estimation_method=method,
                                 estimation_min_completeness=min_completeness,
                                 estimation_max_gap_days=max_gap)


def daily(t, **match):
    f = t.daily_estimates.frame
    for k, v in match.items():
        f = f[f[k] == v]
    return f.set_index("date")


# --------------------------------------------------------------------------- imputation

def test_linear_imputation_goes_to_a_new_column():
    o = np.arange(31, dtype=float)             # 0..30 mm/day: linear, so imputation is exact
    m = 2 * o
    keep = JAN.delete([10, 11])
    c = build_cohort(station(JAN, o), ext("aaa", keep, m[np.isin(JAN, keep)]), cspec())
    t = aggregate_cohort(c, imp(), products=PRODUCTS)
    d = daily(t)
    day = pd.Timestamp("2025-01-11")
    assert np.isnan(d.loc[day, "observed_mm"]) and np.isnan(d.loc[day, "modeled_mm"])
    assert d.loc[day, "observed_estimated_mm"] == pytest.approx(10.0)
    assert d.loc[day, "modeled_estimated_mm"] == pytest.approx(20.0)
    assert d.loc[day, "value_source"] == "imputed" and d.loc[day, "gap_length_days"] == 2
    assert d.loc[pd.Timestamp("2025-01-01"), "value_source"] == "cohort"
    est = one(t, "estimated_total")
    assert est.observed_total_mm == pytest.approx(o.sum())
    assert est.modeled_total_mm == pytest.approx(m.sum())
    assert (est.n_days_imputed, est.estimation_method) == (2, "linear_interpolation")
    # the partial and complete products are untouched by imputation
    assert one(t).observed_total_mm == pytest.approx(o.sum() - 10 - 11)
    assert one(t, "complete_total").validity_reason == "incomplete_period"
    assert pd.isna(one(t).n_days_imputed)


def test_imputation_is_paired_and_ignores_values_outside_the_cohort():
    """A day with a real station value but no model value is imputed on both sides."""
    keep = JAN.delete(15)
    c = build_cohort(station(JAN, 2.0), ext("aaa", keep, 3.0), cspec())
    d = daily(aggregate_cohort(c, imp(), products=PRODUCTS))
    day = JAN[15]
    assert d.loc[day, "value_source"] == "imputed" and np.isnan(d.loc[day, "observed_mm"])
    assert (d.loc[day, "observed_estimated_mm"], d.loc[day, "modeled_estimated_mm"]) == (2.0, 3.0)


def test_frozen_cohort_models_share_imputed_days():
    frames = [ext("aaa", JAN, 2.0), ext("bbb", JAN.delete(4), 2.0)]
    c = build_cohort(station(JAN), frames, cspec("common_date", models=("aaa", "bbb")))
    t = aggregate_cohort(c, imp(), products=PRODUCTS)
    sources = {m: daily(t, model=m)["value_source"] for m in ("aaa", "bbb")}
    pd.testing.assert_series_equal(sources["aaa"], sources["bbb"])
    assert sources["aaa"].loc[JAN[4]] == "imputed"


def test_gap_limit_and_no_extrapolation():
    keep = JAN.delete([10, 11, 12, 13])        # a 4-day gap
    c = build_cohort(station(JAN), ext("aaa", keep, 2.0), cspec())
    short = aggregate_cohort(c, imp(max_gap=3), products=PRODUCTS)
    assert (daily(short)["value_source"] == "not_imputed").sum() == 4
    est = one(short, "estimated_total")
    assert np.isnan(est.observed_total_mm) and est.validity_reason == "imputation_incomplete"
    assert one(aggregate_cohort(c, imp(max_gap=4), products=PRODUCTS), "estimated_total"
               ).observed_total_mm == pytest.approx(62.0)

    edge = JAN[3:]                              # first three days have no anchor before them
    c = build_cohort(station(edge), ext("aaa", edge, 2.0), cspec())
    t = aggregate_cohort(c, imp(max_gap=10), products=PRODUCTS)
    assert one(t, "estimated_total").validity_reason == "imputation_incomplete"


def test_anchors_outside_the_period_bracket_a_gap_at_its_edge():
    days = pd.date_range("2025-01-01", "2025-02-28")
    keep = days.delete([30, 31])               # Jan 31 and Feb 1 missing
    c = build_cohort(station(days), ext("aaa", keep, 2.0), cspec())
    t = aggregate_cohort(c, imp(), products=PRODUCTS)
    for label in ("2025-01", "2025-02"):
        est = one(t, "estimated_total", period_label=label)
        assert est.n_days_imputed == 1 and est.observed_total_mm == pytest.approx(
            2.0 * est.n_days_expected)


def test_min_completeness_applies_before_imputation():
    keep = JAN[::2]                             # 16 of 31 days, gaps of 1
    c = build_cohort(station(JAN), ext("aaa", keep, 2.0), cspec())
    r = one(aggregate_cohort(c, imp(min_completeness=0.6), products=PRODUCTS),
            "estimated_total")
    assert np.isnan(r.observed_total_mm) and r.validity_reason == "below_min_completeness"


def test_reference_et_fraction():
    etr = 4.0 + np.sin(np.arange(31))           # varying reference ET
    obs = station(JAN, 0.5 * etr).assign(reference_et_mm=etr)
    keep = JAN.delete([20, 21])
    c = build_cohort(obs, ext("aaa", keep, 0.8 * etr[np.isin(JAN, keep)]), cspec())
    spec = imp("reference_et_fraction")
    with pytest.raises(ContractError, match="reference ET"):
        aggregate_cohort(c, spec, products=PRODUCTS)
    t = aggregate_cohort(c, spec, products=PRODUCTS, reference_et=obs)
    d = daily(t)
    for i in (20, 21):
        assert d.loc[JAN[i], "observed_estimated_mm"] == pytest.approx(0.5 * etr[i])
        assert d.loc[JAN[i], "modeled_estimated_mm"] == pytest.approx(0.8 * etr[i])
        assert d.loc[JAN[i], "reference_et_mm"] == pytest.approx(etr[i])
    est = one(t, "estimated_total")
    assert est.observed_total_mm == pytest.approx(0.5 * etr.sum())
    assert est.modeled_total_mm == pytest.approx(0.8 * etr.sum())


def test_reference_et_fraction_skips_unusable_reference():
    etr = np.full(31, 4.0)
    etr[19] = 0.0                               # anchor without positive reference ET
    etr[20] = np.nan                            # missing day without reference ET
    obs = station(JAN, 2.0).assign(reference_et_mm=etr)
    c = build_cohort(obs, ext("aaa", JAN.delete(20), 2.0), cspec())
    d = daily(aggregate_cohort(c, imp("reference_et_fraction"), products=PRODUCTS,
                               reference_et=obs))
    assert d.loc[JAN[19], "value_source"] == "cohort"            # real value kept
    assert d.loc[JAN[20], "value_source"] == "not_imputed"


def test_daily_estimates_round_trip(tmp_path):
    c = build_cohort(station(JAN), ext("aaa", JAN.delete(3), 2.0), cspec())
    t = aggregate_cohort(c, imp(), products=PRODUCTS)
    back = read_table(write_table(t.daily_estimates, tmp_path / "daily.parquet"))
    pd.testing.assert_frame_equal(back.frame, t.daily_estimates.frame)
    assert back.attributes["cohort_membership_sha256"] == c.fingerprint
    assert aggregate_cohort(c, dataclasses.replace(
        imp(), estimation_method=None, estimation_min_completeness=None,
        estimation_max_gap_days=None), products=PRODUCTS).daily_estimates is None


def test_imputation_spec_validation():
    with pytest.raises(ContractError, match="estimation_max_gap_days"):
        PeriodAggregationSpec("a", ("month",), estimation_method="linear_interpolation",
                              estimation_min_completeness=0.5)
    with pytest.raises(ContractError, match="applies only"):
        PeriodAggregationSpec("a", ("month",), estimation_method="mean_daily_scaling",
                              estimation_min_completeness=0.5, estimation_max_gap_days=3)


# --------------------------------------------------------------------------- station ET ref

def test_station_reference_et_column_needs_units():
    df = pd.DataFrame({"date": ["2025-01-01", "2025-01-02"], "ET": [1.0, np.nan],
                       "ET_corr": [1.1, 2.0], "ET_gap": [False, True], "ETr": [5.0, 6.0]})
    st = StationSpec(SITE, timezone="UTC-07:00", day_definition="local_standard_time",
                     interval_label="start")
    kw = dict(site_id=SITE, date_column="date", gap_flag_column="ET_gap",
              reference_columns={"et_raw": "ET", "et_corrected": "ET_corr"},
              reference_et_column="ETr")
    with pytest.raises(ContractError, match="ETr"):
        StationTableSpec(units={"ET": "mm/day", "ET_corr": "mm/day"}, **kw)
    spec = StationTableSpec(units={"ET": "mm/day", "ET_corr": "mm/day", "ETr": "mm/day"}, **kw)
    f = adapt_station_daily(df, spec, st).table.frame
    assert list(f["reference_et_mm"]) == [5.0, 6.0] and "src_ETr" in f


# --------------------------------------------------------------------------- crop height

CROP = CropHeightTableSpec("crop", None, "date", "height_cm", "cm", "season_start",
                           "season_end", site_column="stationid", active_column="active_k")


def crop_frame():
    d = pd.date_range("2024-01-01", "2025-12-31")
    start = np.where(d.year == 2024, "2024-03-15", "2025-03-20")
    end = np.where(d.year == 2024, "2024-12-06", "2025-11-30")
    inside = (d >= pd.to_datetime(start)) & (d <= pd.to_datetime(end))
    return pd.DataFrame({"date": d.strftime("%Y-%m-%d"), "height_cm": np.where(inside, 20.0, 5.0),
                         "active_k": np.where(inside, 0.002, np.nan), "stationid": SITE,
                         "zone": "E", "season_start": start, "season_end": end})


def test_crop_height_growing_seasons():
    r = adapt_crop_height(crop_frame(), CROP)
    assert r.report.ok and "src_zone" in r.table.frame
    w = growing_season_windows(r)
    assert [(x.label, x.start, x.end) for x in w] == [
        ("GS2024", "2024-03-15", "2024-12-06"), ("GS2025", "2025-03-20", "2025-11-30")]
    spec = PeriodAggregationSpec("g", ("growing_season",), growing_season_source="crop")
    days = pd.date_range("2025-03-01", "2025-12-31")
    c = build_cohort(station(days, 1.0), ext("aaa", days, 1.0), cspec())
    with pytest.raises(ContractError, match="with_growing_seasons"):
        aggregate_cohort(c, spec, products=PRODUCTS)
    t = aggregate_cohort(c, spec.with_growing_seasons(w), products=PRODUCTS)
    comp = one(t, "complete_total")
    assert (comp.period_label, comp.n_days_expected) == ("GS2025", 256)
    assert comp.observed_total_mm == pytest.approx(256.0)


@pytest.mark.parametrize("mutate, match", [
    (lambda f: f.assign(active_k=0.002), "active_k"),
    (lambda f: f.assign(season_start=np.where(f.date < "2024-06-01", "2024-03-01",
                                              f.season_start)), "change within a year"),
    (lambda f: f.assign(season_end="2023-01-01"), "start after end"),
])
def test_crop_height_rejects_inconsistent_seasons(mutate, match):
    with pytest.raises(DataValidationError, match=match):
        adapt_crop_height(mutate(crop_frame()), CROP)


def test_crop_height_config():
    base = {
        "stations": {SITE: {"timezone": "UTC-07:00", "day_definition": "local_standard_time",
                            "interval_label": "start"}},
        "crop_height_tables": [{"name": "crop", "path": "x.csv", "date_column": "date",
                                "height_column": "h", "height_units": "cm",
                                "season_start_column": "a", "season_end_column": "b",
                                "site_id": SITE}],
    }
    cfg = config_from_dict({**base, "period_aggregations": [
        {"aggregation_id": "p", "periods": ["growing_season"],
         "growing_season_source": "crop"}]})
    assert cfg.crop_height_table("crop").height_units == "cm"
    with pytest.raises(ContractError, match="crop_height_tables name"):
        config_from_dict({**base, "period_aggregations": [
            {"aggregation_id": "p", "periods": ["growing_season"],
             "growing_season_source": "nope"}]})
    with pytest.raises(ContractError, match="height_units"):
        CropHeightTableSpec("c", None, "d", "h", "inch", "a", "b", site_id=SITE)
