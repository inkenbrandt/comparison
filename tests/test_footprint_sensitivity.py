"""Footprint weighting sensitivity on frozen dates (guide step 6)."""

import numpy as np
import pandas as pd
import pytest

from etcompare.config import FootprintSetSpec
from etcompare.errors import ContractError
from etcompare.footprint_sensitivity import compare_footprint_sets
from etcompare.references import reference_values
from etcompare.schemas import PAIRED, check_primary_key

DATES = pd.date_range("2025-06-01", periods=5)
SETS = [FootprintSetSpec("ETW", "x", weighting_method="et_weighted_unverified",
                         value_semantics="normalized_cell_weight"),
        FootprintSetSpec("MEAN", "y", weighting_method="time_mean_unverified",
                         value_semantics="normalized_cell_weight", temporal_weighting="time_mean")]


def ext(set_id, model, values, retained=None, method="footprint_conservative"):
    n = len(values)
    return pd.DataFrame({
        "site_id": "US-XXX", "date": DATES[:n], "period": "day", "model": model,
        "extraction_method": method, "footprint_set_id": set_id, "footprint_method": "m",
        "et_mm": values, "fp_weight_sum": 1.0,
        "fp_weight_retained_frac": retained if retained is not None else [1.0] * n,
        "fp_weight_retained_denominator": "normalized_truncated_footprint",
        "support_id": None, "day_alignment": "unverified", "model_asset_id": None,
        "footprint_asset_id": None, "source_path": None,
    })


def station():
    return pd.DataFrame({
        "site_id": "US-XXX", "date": DATES,
        "et_raw_mm": [3.0, np.nan, 2.0, 4.0, np.nan],
        "et_corrected_mm": [3.3, 2.5, 2.2, 4.4, np.nan],
        "gap_filled": [False, True, False, False, False],
        "raw_available": [True, False, True, True, False],
        "reference_provenance": ["corrected_from_raw", "corrected_filled",
                                 "corrected_from_raw", "corrected_from_raw",
                                 "reference_missing"],
    })


def frames():
    return [
        ext("ETW", "ssebop", [1.0, 2.0, 3.0, 4.0, 5.0]),
        ext("MEAN", "ssebop", [1.5, 2.0, np.nan, 4.5, 5.0]),  # day 3 missing in MEAN only
        ext("ETW", "sims", [2.0, 2.0, 2.0, 2.0, 2.0], retained=[1, 1, 1, 0.5, 1]),
        ext("MEAN", "sims", [2.5, 2.5, 2.5, 2.5, 2.5]),
    ]


def test_removing_a_date_from_one_set_removes_it_from_both():
    r = compare_footprint_sets(frames(), SETS, comparison_id="fp", station_daily=station())
    p = r.paired.frame
    ss = p[(p.model == "ssebop") & (p.reference_variant == "et_corrected_inclusive")]
    for set_id in ("ETW", "MEAN"):
        dates = set(ss.loc[ss.footprint_set_id == set_id, "date"])
        assert DATES[2] not in dates and dates == {DATES[0], DATES[1], DATES[3]}
    ex = r.exclusions.to_frame()
    assert "missing_in_footprint_set" in set(ex.reason_code)
    check_primary_key(p, PAIRED.primary_key, table="paired")


def test_reference_variant_cohorts_keep_observations_paired():
    r = compare_footprint_sets(frames(), SETS, comparison_id="fp", station_daily=station())
    p = r.paired.frame
    days = {v: set(g.date) for v, g in p[p.model == "sims"].groupby("reference_variant")}
    assert days["et_raw"] == {DATES[0], DATES[2], DATES[3]}
    assert days["et_corrected_measured"] == days["et_raw"]
    assert days["et_corrected_inclusive"] == {DATES[0], DATES[1], DATES[2], DATES[3]}
    filled = p[(p.date == DATES[1]) & (p.reference_variant == "et_corrected_inclusive")]
    assert set(filled.reference_provenance) == {"corrected_filled"}
    assert (filled.observed_mm == 2.5).all()
    # Each set's row carries the same observation as its counterpart.
    w = p.pivot_table(index=["reference_variant", "model", "date"], columns="footprint_set_id",
                      values="observed_mm")
    assert (w["ETW"] == w["MEAN"]).all()


def test_differences_are_set_minus_baseline_on_frozen_days():
    r = compare_footprint_sets(frames(), SETS, comparison_id="fp")
    d = r.differences.set_index(["reference_variant", "model"])
    row = d.loc[("none", "ssebop")]
    # frozen days 0, 1, 3, 4: diffs 0.5, 0, 0.5, 0
    assert row.n_days == 4 and row.mean_diff_mm == pytest.approx(0.25)
    assert row.max_abs_diff_mm == pytest.approx(0.5) and row.baseline_set_id == "ETW"
    assert row.baseline_mean_mm == pytest.approx(3.0)
    assert len(r.paired) == 0  # no station table: model-only cohort only


def test_min_retained_frac_applies_to_every_set():
    r = compare_footprint_sets(frames(), SETS, comparison_id="fp", min_retained_frac=0.9)
    d = r.differences.set_index(["reference_variant", "model"])
    assert d.loc[("none", "sims")].n_days == 4  # day 3 dropped on both sides
    assert "below_min_retained_frac" in set(r.exclusions.to_frame().reason_code)


def test_common_models():
    r = compare_footprint_sets(frames(), SETS, comparison_id="fp", common_models=True)
    d = r.differences.set_index(["reference_variant", "model"])
    assert d.loc[("none", "sims")].n_days == 4  # ssebop lacks day 2 in MEAN
    assert "not_all_models" in set(r.exclusions.to_frame().reason_code)


def test_target_dependence_is_flagged():
    measured = FootprintSetSpec("ETW", "x", weighting_method="LE weighted",
                                value_semantics="normalized_cell_weight",
                                temporal_weighting="measured_et_weighted")
    r = compare_footprint_sets(frames(), [measured, SETS[1]], comparison_id="fp",
                               station_daily=station())
    assert "footprint_target_dependence" in r.report.codes("warning")
    p = r.paired.frame
    assert set(p.loc[p.footprint_set_id == "ETW", "flags"]) == {
        "footprint_target_dependence=yes"}
    assert p.loc[p.footprint_set_id == "MEAN", "flags"].isna().all()
    unk = compare_footprint_sets(frames(), SETS, comparison_id="fp", station_daily=station())
    assert "footprint_target_dependence=unknown" in set(unk.paired.frame["flags"].dropna())


def test_missing_set_is_skipped_not_invented():
    only_etw = [f for f in frames() if (f.footprint_set_id == "ETW").all()]
    r = compare_footprint_sets(only_etw, SETS, comparison_id="fp", station_daily=station())
    assert r.status == "not_available" and "MEAN" in r.reason
    assert r.paired is None and r.differences.empty
    with pytest.raises(ContractError):
        compare_footprint_sets(frames(), SETS[:1], comparison_id="fp")


def test_reference_values_rules():
    st = station()
    assert len(reference_values(st, "et_raw")) == 3
    assert len(reference_values(st, "et_corrected_measured")) == 3
    inc = reference_values(st, "et_corrected_inclusive")
    assert list(inc.reference_provenance) == ["corrected_from_raw", "corrected_filled",
                                              "corrected_from_raw", "corrected_from_raw"]
    with pytest.raises(ValueError):
        reference_values(st, "et_fill")
