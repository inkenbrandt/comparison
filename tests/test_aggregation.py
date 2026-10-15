"""Temporal aggregation and completeness (guide step 8)."""

import numpy as np
import pandas as pd
import pytest

from etcompare.aggregation import aggregate_cohort, period_calendar
from etcompare.cohorts import build_cohort as _build_cohort
from etcompare.config import (
    ComparisonSpec,
    ExtractionSide,
    FootprintSetSpec,
    PeriodAggregationSpec,
    ProductSpec,
    config_from_dict,
)
from etcompare.errors import ContractError
from etcompare.io.tables import read_table, write_table

SITE = "US-XXX"
FP = ExtractionSide("footprint_conservative", "SET")
PIX = ExtractionSide("pixel_window_3x3", "none")


def product(model, support="interpolated_daily"):
    return ProductSpec(model, "mm/day", "unknown", support)


PRODUCTS = {m: product(m) for m in ("aaa", "bbb")}
SETS = [FootprintSetSpec("SET", "x", weighting_method="nldas",
                         value_semantics="normalized_cell_weight",
                         temporal_weighting="reference_et_weighted")]


def build_cohort(obs, frames, spec):
    return _build_cohort(obs, frames, spec, footprint_sets=SETS)


def ext(model, dates, values, side=FP, alignment="declared"):
    n = len(dates)
    values = np.broadcast_to(np.asarray(values, dtype=float), (n,))
    return pd.DataFrame({
        "site_id": SITE, "date": dates, "period": "day", "model": model,
        "extraction_method": side.extraction_method, "footprint_set_id": side.footprint_set_id,
        "footprint_method": "m", "et_mm": values, "fp_weight_sum": 1.0,
        "fp_weight_retained_frac": 1.0 if side.footprint_set_id != "none" else np.nan,
        "fp_weight_retained_denominator": None, "support_id": None, "day_alignment": alignment,
        "model_asset_id": None, "footprint_asset_id": None, "source_path": None,
    })


def station(dates, values=2.0, filled=()):
    """Measured on every date except ``filled`` (gap-filled corrected value, no raw)."""
    n = len(dates)
    values = np.broadcast_to(np.asarray(values, dtype=float), (n,)).copy()
    is_filled = pd.Series(dates).isin(pd.DatetimeIndex(list(filled))).to_numpy()
    return pd.DataFrame({
        "site_id": SITE, "date": dates, "et_raw_mm": np.where(is_filled, np.nan, values),
        "et_corrected_mm": values, "gap_filled": is_filled, "raw_available": ~is_filled,
        "reference_provenance": np.where(is_filled, "corrected_filled", "corrected_from_raw"),
    })


def cspec(kind="model_available", models=("aaa",), variants=("et_corrected_inclusive",),
          sides=(FP,), **kw):
    return ComparisonSpec("cmp", kind, models, variants, sides, **kw)


def agg(periods=("month",), **kw):
    return PeriodAggregationSpec("agg", periods, **kw)


def rows(totals, product="partial_sum", **match):
    f = totals.table.frame
    f = f[f["product"] == product]
    for k, v in match.items():
        f = f[f[k] == v]
    return f.reset_index(drop=True)


def one(totals, product="partial_sum", **match):
    r = rows(totals, product, **match)
    assert len(r) == 1, r
    return r.iloc[0]


JAN = pd.date_range("2025-01-01", "2025-01-31")


# --------------------------------------------------------------------------- gate

def test_gate_31_day_month_with_30_valid_days():
    """Guide step 8 gate: partial 60 mm, complete total unavailable; full month 62 mm."""
    obs = station(JAN)
    gap = ext("aaa", JAN.delete(14), 2.0)               # model missing 2025-01-15
    t = aggregate_cohort(build_cohort(obs, gap, cspec()), agg(), products=PRODUCTS)
    part = one(t, model="aaa")
    assert part.observed_total_mm == pytest.approx(60.0)
    assert part.modeled_total_mm == pytest.approx(60.0)
    assert (part.n_days_expected, part.n_days_matched) == (31, 30)
    assert part.period_completeness == pytest.approx(30 / 31)
    comp = one(t, "complete_total", model="aaa")
    assert np.isnan(comp.observed_total_mm) and np.isnan(comp.modeled_total_mm)
    assert comp.validity_reason == "incomplete_period"

    full = aggregate_cohort(build_cohort(obs, ext("aaa", JAN, 2.0), cspec()), agg(),
                            products=PRODUCTS)
    comp = one(full, "complete_total", model="aaa")
    assert comp.observed_total_mm == pytest.approx(62.0)
    assert comp.modeled_total_mm == pytest.approx(62.0)
    assert pd.isna(comp.validity_reason)
    # the partial sum of a complete month equals the complete total
    assert one(full, model="aaa").observed_total_mm == pytest.approx(62.0)


def test_gate_day_missing_from_both_products_is_still_expected():
    """A day absent from station and model alike does not make a complete month."""
    days = JAN.delete(14)
    t = aggregate_cohort(build_cohort(station(days), ext("aaa", days, 2.0), cspec()), agg(),
                         products=PRODUCTS)
    part = one(t, model="aaa")
    assert (part.n_days_expected, part.n_days_matched) == (31, 30)
    assert part.observed_total_mm == pytest.approx(60.0)
    assert one(t, "complete_total", model="aaa").validity_reason == "incomplete_period"


def test_totals_are_sums_not_means():
    rng = np.random.default_rng(1)
    o, m = rng.uniform(0, 6, 31), rng.uniform(0, 6, 31)
    t = aggregate_cohort(build_cohort(station(JAN, o), ext("aaa", JAN, m), cspec()), agg(),
                         products=PRODUCTS)
    comp = one(t, "complete_total", model="aaa")
    assert comp.observed_total_mm == pytest.approx(o.sum(), abs=1e-12)
    assert comp.modeled_total_mm == pytest.approx(m.sum(), abs=1e-12)
    assert t.table.units["observed_total_mm"] == "mm"


def test_partial_sums_are_never_scaled_by_default():
    days = JAN[:10]
    t = aggregate_cohort(build_cohort(station(days), ext("aaa", days, 2.0), cspec()), agg(),
                         products=PRODUCTS)
    assert one(t, model="aaa").observed_total_mm == pytest.approx(20.0)
    assert set(t.table.frame["product"]) == {"partial_sum", "complete_total"}


# --------------------------------------------------------------------------- calendar

def test_leap_days_and_calendar_lengths():
    s = agg(("month", "year", "water_year"))
    rng = {SITE: (pd.Timestamp("2023-10-01"), pd.Timestamp("2024-12-31"))}
    m = period_calendar("month", rng, s).groupby("period_label").size()
    assert m["2024-02"] == 29 and m["2023-10"] == 31
    y = period_calendar("year", rng, s).groupby("period_label").size()
    assert y["2024"] == 366 and y["2023"] == 365     # 2023 listed whole, from 2023-01-01
    wy = period_calendar("water_year", rng, s).groupby("period_label").size()
    assert wy["WY2024"] == 366 and wy["WY2025"] == 365


def test_water_year_labels_by_ending_year_and_start_month():
    rng = {SITE: (pd.Timestamp("2024-09-30"), pd.Timestamp("2024-10-01"))}
    cal = period_calendar("water_year", rng, agg(("water_year",))).set_index("date")
    assert cal.loc["2024-09-30", "period_label"] == "WY2024"
    assert cal.loc["2024-10-01", "period_label"] == "WY2025"
    assert cal.loc["2024-10-01", "period_start"] == pd.Timestamp("2024-10-01")
    assert cal.loc["2024-10-01", "period_end"] == pd.Timestamp("2025-09-30")
    jan = period_calendar("water_year", rng, agg(("water_year",), water_year_start_month=1))
    assert set(jan["period_label"]) == {"WY2024"} and len(jan) == 366


def test_cross_year_growing_season_rule():
    s = agg(("growing_season",), growing_season_rule={"start": "11-01", "end": "03-31"})
    rng = {SITE: (pd.Timestamp("2024-01-15"), pd.Timestamp("2025-01-15"))}
    n = period_calendar("growing_season", rng, s).groupby("period_label").size()
    assert dict(n) == {"GS2023-2024": 152, "GS2024-2025": 151}   # Feb 2024 has 29 days


def test_growing_season_windows_exclude_days_outside_the_season():
    days = pd.date_range("2025-03-01", "2025-11-30")
    s = agg(("growing_season",), growing_season_windows=[
        {"site_id": SITE, "label": "GS2025", "start": "2025-04-15", "end": "2025-10-15"}])
    t = aggregate_cohort(build_cohort(station(days, 1.0), ext("aaa", days, 1.0), cspec()), s,
                         products=PRODUCTS)
    comp = one(t, "complete_total", model="aaa")
    assert comp.period_label == "GS2025" and comp.n_days_expected == 184
    assert comp.observed_total_mm == pytest.approx(184.0)
    other = PeriodAggregationSpec("g", ("growing_season",), growing_season_windows=[
        {"site_id": "US-OTHER", "label": "GS2025", "start": "2025-04-15", "end": "2025-10-15"}])
    assert aggregate_cohort(build_cohort(station(days), ext("aaa", days, 1.0), cspec()), other,
                            products=PRODUCTS).table.frame.empty


def test_period_outside_inputs_is_flagged():
    days = pd.date_range("2025-01-05", "2025-02-28")
    t = aggregate_cohort(build_cohort(station(days, 1.0), ext("aaa", days, 1.0), cspec()), agg(),
                         products=PRODUCTS)
    jan, feb = one(t, period_label="2025-01"), one(t, period_label="2025-02")
    assert jan["flags"] == "period_outside_inputs"
    assert (jan.n_days_expected, jan.n_days_matched) == (31, 27)
    assert pd.isna(feb["flags"])
    assert one(t, "complete_total", period_label="2025-02").observed_total_mm == pytest.approx(28)


# --------------------------------------------------------------------------- references

def test_accepted_gap_filled_days_complete_inclusive_but_not_measured():
    filled = JAN[10:13]
    obs = station(JAN, 2.0, filled=filled)
    c = build_cohort(obs, ext("aaa", JAN, 2.5), cspec(
        "paired_reference", variants=("et_corrected_measured", "et_corrected_inclusive")))
    t = aggregate_cohort(c, agg(), products=PRODUCTS)
    # the paired-reference cohort freezes the measured days on both variants
    for v in ("et_corrected_measured", "et_corrected_inclusive"):
        assert one(t, "complete_total", reference_variant=v).validity_reason == "incomplete_period"
        assert one(t, reference_variant=v).n_days_matched == 28

    inclusive = build_cohort(obs, ext("aaa", JAN, 2.5), cspec())
    t = aggregate_cohort(inclusive, agg(), products=PRODUCTS)
    comp = one(t, "complete_total")
    assert comp.observed_total_mm == pytest.approx(62.0)
    assert comp.modeled_total_mm == pytest.approx(77.5)
    assert (comp.n_days_matched_reference_filled, comp.n_days_reference_filled,
            comp.n_days_reference_measured) == (3, 3, 28)


def test_frozen_common_cohort_shares_completeness_across_models():
    frames = [ext("aaa", JAN, 2.0), ext("bbb", JAN.delete(3), 2.0)]
    c = build_cohort(station(JAN), frames, cspec("common_date", models=("aaa", "bbb")))
    t = aggregate_cohort(c, agg(), products=PRODUCTS)
    for m in ("aaa", "bbb"):
        r = one(t, model=m)
        assert (r.n_days_matched, r.observed_total_mm) == (30, pytest.approx(60.0))
        assert one(t, "complete_total", model=m).validity_reason == "incomplete_period"
    # availability is reported before freezing: aaa had every day
    assert one(t, model="aaa").n_days_model_available == 31
    assert one(t, model="bbb").n_days_model_available == 30


def test_paired_methods_aggregate_on_the_same_days():
    frames = [ext("aaa", JAN, 2.0), ext("aaa", JAN.delete(0), 3.0, side=PIX)]
    c = build_cohort(station(JAN), frames, cspec("paired_method", sides=(FP, PIX)))
    t = aggregate_cohort(c, agg(), products=PRODUCTS)
    fp, pix = (one(t, extraction_method=s.extraction_method) for s in (FP, PIX))
    assert fp.n_days_matched == pix.n_days_matched == 30
    assert fp.observed_total_mm == pix.observed_total_mm == pytest.approx(60.0)
    assert (fp.modeled_total_mm, pix.modeled_total_mm) == (pytest.approx(60.0),
                                                           pytest.approx(90.0))
    assert fp.n_days_model_available == 31 and pix.n_days_model_available == 30


def test_period_without_matched_days_keeps_its_denominator():
    days = pd.date_range("2025-01-01", "2025-02-28")
    model_days = days[days.month == 2]
    t = aggregate_cohort(build_cohort(station(days), ext("aaa", model_days, 1.0), cspec()),
                         agg(), products=PRODUCTS)
    jan = one(t, period_label="2025-01")
    assert (jan.n_days_expected, jan.n_days_matched, jan.n_days_model_available) == (31, 0, 0)
    assert np.isnan(jan.observed_total_mm) and jan.validity_reason == "no_matched_days"
    assert jan.n_days_reference_measured == 31


# --------------------------------------------------------------------------- support

@pytest.mark.parametrize("support", ["overpass_only", "unknown"])
def test_full_period_totals_need_interpolated_daily_support(support):
    products = {"aaa": product("aaa", support)}
    s = agg(estimation_method="mean_daily_scaling", estimation_min_completeness=0.5)
    c = build_cohort(station(JAN), ext("aaa", JAN, 2.0), cspec())
    t = aggregate_cohort(c, s, products=products)
    assert one(t).observed_total_mm == pytest.approx(62.0)
    for prod in ("complete_total", "estimated_total"):
        r = one(t, prod)
        assert np.isnan(r.observed_total_mm)
        assert r.validity_reason == f"temporal_support_{support}"
    assert one(t).temporal_support == support
    assert "no_full_period_support" in {i.code for i in t.report.issues}


def test_undeclared_product_raises():
    c = build_cohort(station(JAN), ext("aaa", JAN, 2.0), cspec())
    with pytest.raises(ContractError, match="temporal support"):
        aggregate_cohort(c, agg(), products={})


def test_mixed_support_warns():
    products = {"aaa": product("aaa"), "bbb": product("bbb", "overpass_only")}
    c = build_cohort(station(JAN), [ext("aaa", JAN, 2.0), ext("bbb", JAN, 2.0)],
                     cspec("common_date", models=("aaa", "bbb")))
    t = aggregate_cohort(c, agg(), products=products)
    assert "mixed_temporal_support" in {i.code for i in t.report.issues}
    assert one(t, "complete_total", model="aaa").observed_total_mm == pytest.approx(62.0)


# --------------------------------------------------------------------------- estimation

def test_estimated_totals_are_opt_in_and_labelled():
    days = JAN.delete([0, 1, 2])                      # 28 of 31 days
    c = build_cohort(station(days, 2.0), ext("aaa", days, 3.0), cspec())
    s = agg(estimation_method="mean_daily_scaling", estimation_min_completeness=0.9)
    est = one(aggregate_cohort(c, s, products=PRODUCTS), "estimated_total")
    assert est.observed_total_mm == pytest.approx(56.0 * 31 / 28)
    assert est.modeled_total_mm == pytest.approx(84.0 * 31 / 28)
    assert est.estimation_method == "mean_daily_scaling"
    strict = agg(estimation_method="mean_daily_scaling", estimation_min_completeness=0.95)
    r = one(aggregate_cohort(c, strict, products=PRODUCTS), "estimated_total")
    assert np.isnan(r.observed_total_mm) and r.validity_reason == "below_min_completeness"
    # complete period: the estimate equals the complete total
    full = build_cohort(station(JAN), ext("aaa", JAN, 2.0), cspec())
    assert one(aggregate_cohort(full, s, products=PRODUCTS), "estimated_total"
               ).observed_total_mm == pytest.approx(62.0)


# --------------------------------------------------------------------------- export

def test_flags_round_trip_and_provenance(tmp_path):
    days = pd.date_range("2025-01-01", "2025-03-31")
    c = build_cohort(station(days), ext("aaa", days, 2.0, alignment="unverified"), cspec())
    t = aggregate_cohort(c, agg(("month", "year", "water_year")), products=PRODUCTS)
    f = t.table.frame
    assert (f["flags"].str.contains("day_alignment=unverified")).all()
    assert one(t, period_type="year")["flags"] == (
        "day_alignment=unverified;period_outside_inputs")
    assert t.attributes["cohort_membership_sha256"] == c.fingerprint
    back = read_table(write_table(t.table, tmp_path / "totals.parquet"))
    pd.testing.assert_frame_equal(back.frame, f)
    assert back.attributes["cohort_membership_sha256"] == c.fingerprint
    s = t.summary()
    row = s[s.period_type == "month"].iloc[0]
    assert (row.n_periods, row.n_periods_complete, row.n_days_expected, row.n_days_matched) == (
        3, 3, 90, 90)
    # immutability: the accessor returns a copy
    copy = t.table.frame
    copy.loc[0, "observed_total_mm"] = -1.0
    assert t.table.frame.loc[0, "observed_total_mm"] != -1.0


def test_daily_period_is_identity_on_paired_rows():
    days = JAN[:5]
    c = build_cohort(station(days, [1, 2, 3, 4, 5]), ext("aaa", days, 2.0), cspec())
    t = aggregate_cohort(c, agg(("day",)), products=PRODUCTS)
    comp = rows(t, "complete_total")
    assert list(comp.observed_total_mm) == [1, 2, 3, 4, 5]
    assert (comp.n_days_expected == 1).all()


# --------------------------------------------------------------------------- config

def test_spec_validation():
    with pytest.raises(ContractError, match="no default season"):
        PeriodAggregationSpec("a", ("growing_season",))
    with pytest.raises(ContractError, match="02-29"):
        PeriodAggregationSpec("a", ("growing_season",),
                              growing_season_rule={"start": "02-29", "end": "10-31"})
    with pytest.raises(ContractError, match="declare one of"):
        PeriodAggregationSpec("a", ("growing_season",),
                              growing_season_rule={"start": "04-01", "end": "10-31"},
                              growing_season_windows=[{"site_id": SITE, "label": "g",
                                                       "start": "2025-04-01",
                                                       "end": "2025-10-01"}])
    with pytest.raises(ContractError, match="overlap"):
        PeriodAggregationSpec("a", ("growing_season",), growing_season_windows=[
            {"site_id": SITE, "label": "a", "start": "2025-04-01", "end": "2025-06-01"},
            {"site_id": SITE, "label": "b", "start": "2025-06-01", "end": "2025-09-01"}])
    with pytest.raises(ContractError, match="estimation_min_completeness"):
        PeriodAggregationSpec("a", ("month",), estimation_method="mean_daily_scaling")
    with pytest.raises(ContractError, match="not one of"):
        PeriodAggregationSpec("a", ("season",))
    with pytest.raises(ContractError, match="water_year_start_month"):
        PeriodAggregationSpec("a", ("water_year",), water_year_start_month=13)


def test_config_section():
    base = {
        "stations": {SITE: {"timezone": "UTC-07:00", "day_definition": "local_standard_time",
                            "interval_label": "start"}},
        "products": {"aaa": {"units": "mm/day", "day_definition": "unknown",
                             "temporal_support": "interpolated_daily"}},
        "comparisons": [{"comparison_id": "c1", "kind": "model_available", "models": ["aaa"],
                         "reference_variants": ["et_corrected_inclusive"],
                         "sides": [{"extraction_method": "x", "footprint_set_id": "none"}]}],
    }
    cfg = config_from_dict({**base, "period_aggregations": [
        {"aggregation_id": "p", "periods": ["month", "water_year"], "comparisons": ["c1"]}]})
    assert cfg.period_aggregation("p").periods == ("month", "water_year")
    with pytest.raises(ContractError, match="not declared"):
        config_from_dict({**base, "period_aggregations": [
            {"aggregation_id": "p", "periods": ["month"], "comparisons": ["nope"]}]})
    with pytest.raises(ContractError, match="unknown configuration key"):
        config_from_dict({**base, "period_aggregations": [
            {"aggregation_id": "p", "periods": ["month"], "scale": True}]})
