"""Explicit comparison cohorts (guide step 7)."""

import dataclasses

import numpy as np
import pandas as pd
import pytest

from etcompare.cohorts import build_cohort
from etcompare.config import ComparisonSpec, ExtractionSide, FootprintSetSpec, config_from_dict
from etcompare.errors import ContractError, DuplicateKeyError
from etcompare.footprint_sensitivity import compare_footprint_sets
from etcompare.io.tables import read_table, write_table
from etcompare.schemas import PAIRED, check_primary_key

SITE = "US-XXX"
DATES = pd.date_range("2025-06-01", periods=10)
FP = ExtractionSide("footprint_conservative", "SET")
PIX = ExtractionSide("pixel_window_3x3", "none")
SETS = {"SET": FootprintSetSpec("SET", "x", weighting_method="nldas",
                                value_semantics="normalized_cell_weight",
                                temporal_weighting="reference_et_weighted")}


def ext(model, values, side=FP, retained=None, alignment="unverified", dates=DATES):
    n = len(values)
    return pd.DataFrame({
        "site_id": SITE, "date": dates[:n], "period": "day", "model": model,
        "extraction_method": side.extraction_method, "footprint_set_id": side.footprint_set_id,
        "footprint_method": "m", "et_mm": values, "fp_weight_sum": 1.0,
        "fp_weight_retained_frac": retained if retained is not None else
        ([1.0] * n if side.footprint_set_id != "none" else [np.nan] * n),
        "fp_weight_retained_denominator": "normalized_truncated_footprint",
        "support_id": None, "day_alignment": alignment, "model_asset_id": None,
        "footprint_asset_id": None, "source_path": None,
    })


def station(raw=None, corr=None):
    """Days 0-6 measured, 7-8 gap-filled, 9 missing."""
    raw = raw if raw is not None else [2.0] * 7 + [np.nan] * 3
    corr = corr if corr is not None else [2.2] * 9 + [np.nan]
    prov = ["corrected_from_raw"] * 7 + ["corrected_filled"] * 2 + ["reference_missing"]
    return pd.DataFrame({
        "site_id": SITE, "date": DATES, "et_raw_mm": raw, "et_corrected_mm": corr,
        "gap_filled": [False] * 7 + [True] * 2 + [False],
        "raw_available": [not np.isnan(x) for x in raw], "reference_provenance": prov,
    })


def spec(kind="common_date", models=("aaa", "bbb"), variants=("et_corrected_inclusive",),
         sides=(FP,), **kw):
    return ComparisonSpec("cmp", kind, models, variants, sides, **kw)


def mae(cohort, model):
    p = cohort.paired.frame
    p = p[p.model == model]
    return float((p.modeled_mm - p.observed_mm).abs().mean())


# --------------------------------------------------------------------------- gates

def test_model_missing_difficult_days_gains_no_advantage():
    """Guide step 7 gate: common-date ranking scores both models on the same days."""
    obs = station(corr=[2.0] * 10)
    easy = [2.1] * 10
    hard = easy[:7] + [5.0, 5.0] + [2.1]           # large errors on days 7-8
    frames = [ext("aaa", hard), ext("bbb", easy[:7] + [np.nan, np.nan] + [2.1])]
    obs.loc[9, ["et_corrected_mm"]] = 2.0
    obs.loc[9, "reference_provenance"] = "corrected_filled"

    per_model = build_cohort(obs, frames, spec("model_available", variants=(
        "et_corrected_inclusive",)))
    assert mae(per_model, "aaa") > 0.5 and mae(per_model, "bbb") == pytest.approx(0.1)

    c = build_cohort(obs, frames, spec())
    u = c.units()
    assert set(u.cohort_id) == {"cmp:common"}
    days = {m: set(g.date) for m, g in u.groupby("model")}
    assert days["aaa"] == days["bbb"] and DATES[7] not in days["aaa"]
    assert len(days["aaa"]) == 8
    assert mae(c, "aaa") == pytest.approx(mae(c, "bbb")) == pytest.approx(0.1)
    mem = c.membership.frame.set_index(["model", "date"])
    assert mem.loc[("aaa", DATES[7]), "reason_code"] == "not_all_models"
    assert mem.loc[("aaa", DATES[7]), "detail"] == "ineligible: bbb"
    assert mem.loc[("bbb", DATES[7]), "reason_code"] == "modeled_value_null"
    assert c.attributes["ranking_scope"] == "all_models"


def test_removing_a_date_from_one_method_removes_it_from_both():
    frames = [ext("aaa", [2.0] * 10), ext("bbb", [2.0] * 10),
              ext("aaa", [2.5] * 3 + [np.nan] + [2.5] * 6, side=PIX),
              ext("bbb", [2.5] * 10, side=PIX)]
    c = build_cohort(station(), frames, spec("paired_method", sides=(FP, PIX)))
    p = c.paired.frame
    a = p[p.model == "aaa"]
    for method in ("footprint_conservative", "pixel_window_3x3"):
        assert DATES[3] not in set(a.loc[a.extraction_method == method, "date"])
    assert set(a[a.extraction_method == "footprint_conservative"].date) == set(
        a[a.extraction_method == "pixel_window_3x3"].date)
    # per-model cohorts: bbb keeps day 3
    assert DATES[3] in set(p[p.model == "bbb"].date)
    assert c.cohort_ids == ["cmp:aaa", "cmp:bbb"]
    mem = c.membership.frame.set_index(["model", "date"])
    assert mem.loc[("aaa", DATES[3]), "detail"] == \
        "pixel_window_3x3@none:modeled_value_null"
    # common_models: day 3 leaves every model
    cm = build_cohort(station(), frames, spec("paired_method", sides=(FP, PIX),
                                              common_models=True))
    assert DATES[3] not in set(cm.paired.frame.date)


def test_membership_and_reasons_are_exported(tmp_path):
    frames = [ext("aaa", [2.0] * 10), ext("bbb", [2.0] * 6)]  # bbb lacks days 6-9
    c = build_cohort(station(), frames, spec())
    m = c.membership.frame
    assert len(m) == 2 * 10  # every site/date/model candidate has a decision
    assert m.loc[m.included, "reason_code"].isna().all()
    assert m.loc[~m.included, "reason_code"].notna().all()
    assert len(c.exclusions) == int((~m.included).sum())
    back = read_table(write_table(c.membership, tmp_path / "membership.parquet"))
    pd.testing.assert_frame_equal(back.frame, m)
    bp = read_table(write_table(c.paired, tmp_path / "paired.parquet"))
    assert bp.attributes["membership_sha256"] == c.fingerprint
    codes = dict(zip(m[m.model == "bbb"].date, m[m.model == "bbb"].reason_code, strict=True))
    assert codes[DATES[6]] == "extraction_missing"
    assert codes[DATES[9]] == "extraction_missing"  # first failing cell is reported
    assert m[(m.model == "bbb") & (m.date == DATES[9])].detail.item() == (
        "footprint_conservative@SET:extraction_missing;et_corrected_inclusive:"
        "reference_unavailable")


# --------------------------------------------------------------------------- kinds

def test_paired_reference_uses_days_with_every_variant():
    frames = [ext("aaa", [2.0] * 10)]
    c = build_cohort(station(), frames, spec("paired_reference", models=("aaa",),
                                             variants=("et_raw", "et_corrected_measured")))
    p = c.paired.frame
    assert set(p.date) == set(DATES[:7])
    w = p.pivot(index="date", columns="reference_variant", values="observed_mm")
    assert (w.et_raw == 2.0).all() and (w.et_corrected_measured == 2.2).all()
    assert set(p.reference_provenance) == {"corrected_from_raw"}


def test_pairwise_cohort_may_use_more_days_than_all_model_cohort():
    frames = [ext("aaa", [2.0] * 10), ext("bbb", [2.0] * 10), ext("ccc", [2.0] * 4)]
    allm = build_cohort(station(), frames, spec(models=("aaa", "bbb", "ccc")))
    pair = build_cohort(station(), frames, spec("pairwise_model", models=("aaa", "bbb")))
    assert len(set(allm.paired.frame.date)) == 4
    assert len(set(pair.paired.frame.date)) == 9  # day 9 has no reference
    assert pair.attributes["ranking_scope"] == "pairwise"
    assert allm.fingerprint != pair.fingerprint


def test_model_available_cohorts_are_per_model():
    frames = [ext("aaa", [2.0] * 10), ext("bbb", [2.0] * 4)]
    c = build_cohort(station(), frames, spec("model_available"))
    s = c.summary().set_index("cohort_id")
    assert s.loc["cmp:aaa", "n_days"] == 9 and s.loc["cmp:bbb", "n_days"] == 4
    assert s.loc["cmp:aaa", "n_days_reference_filled"] == 2
    assert c.attributes["ranking_scope"] == "none"


# --------------------------------------------------------------------------- rules

def test_quality_threshold_applies_to_every_model_and_footprint_side_only():
    ret = [1.0] * 5 + [0.5] + [np.nan] + [1.0] * 3
    frames = [ext("aaa", [2.0] * 10, retained=ret), ext("bbb", [2.0] * 10),
              ext("aaa", [2.0] * 10, side=PIX), ext("bbb", [2.0] * 10, side=PIX)]
    c = build_cohort(station(), frames, spec("paired_method", sides=(FP, PIX),
                                             min_retained_frac=0.9))
    m = c.membership.frame.set_index(["model", "date"])
    assert m.loc[("aaa", DATES[5]), "reason_code"] == "below_min_retained_frac"
    assert m.loc[("aaa", DATES[6]), "reason_code"] == "retained_frac_unknown"
    assert m.loc[("bbb", DATES[5]), "included"]  # pixel side has no retained fraction
    assert c.attributes["comparison"]["min_retained_frac"] == 0.9


def test_duplicates_are_rejected_before_joins():
    frames = [ext("aaa", [2.0] * 10), ext("bbb", [2.0] * 10)]
    with pytest.raises(DuplicateKeyError):
        build_cohort(station(), frames + [ext("aaa", [3.0] * 10)], spec())
    with pytest.raises(DuplicateKeyError):
        build_cohort([station(), station()], frames, spec())
    # rows of other sides or models may repeat across tables; they are not selected
    build_cohort(station(), frames + [ext("aaa", [3.0] * 10, side=PIX)] * 2, spec())


def test_paired_rows_are_complete_and_flagged():
    frames = [ext("aaa", [2.0] * 10), ext("bbb", [2.0] * 10, alignment="declared")]
    c = build_cohort(station(), frames, spec(), footprint_sets=SETS)
    p = c.paired.frame
    check_primary_key(p, PAIRED.primary_key, table="paired")
    assert p[["observed_mm", "modeled_mm"]].notna().all().all()
    assert set(p.loc[p.model == "aaa", "flags"]) == {"day_alignment=unverified"}
    assert p.loc[p.model == "bbb", "flags"].isna().all()
    assert "footprint_target_dependence" not in c.report.codes()
    assert "reference_filled_days" in c.report.codes("info")

    unknown = build_cohort(station(), frames, spec())  # set weighting not supplied
    assert "footprint_target_dependence" in unknown.report.codes("warning")
    assert set(unknown.paired.frame.loc[lambda d: d.model == "aaa", "flags"]) == {
        "day_alignment=unverified;footprint_target_dependence=unknown"}
    measured = {"SET": dataclasses.replace(SETS["SET"], temporal_weighting="measured_et_weighted")}
    mc = build_cohort(station(), frames, spec(), footprint_sets=measured)
    assert set(mc.paired.frame.loc[lambda d: d.model == "bbb", "flags"]) == {
        "footprint_target_dependence=yes"}


def test_cohort_is_immutable_and_fingerprinted():
    frames = [ext("aaa", [2.0] * 10), ext("bbb", [2.0] * 10)]
    c = build_cohort(station(), frames, spec())
    p, m = c.paired.frame, c.membership.frame
    p.loc[:, "modeled_mm"] = 99.0
    m.loc[:, "included"] = False
    assert (c.paired.frame.modeled_mm == 2.0).all() and c.membership.frame.included.any()
    with pytest.raises(dataclasses.FrozenInstanceError):
        c.status = "x"  # type: ignore[misc]
    with pytest.raises(TypeError):
        c.attributes["kind"] = "x"  # type: ignore[index]
    assert build_cohort(station(), frames, spec()).fingerprint == c.fingerprint
    fewer = build_cohort(station(), [frames[0], ext("bbb", [2.0] * 8)], spec())
    assert fewer.fingerprint != c.fingerprint


def test_period_and_empty_cohort():
    frames = [ext("aaa", [2.0] * 10), ext("bbb", [2.0] * 10)]
    c = build_cohort(station(), frames, spec(start="2025-06-03", end="2025-06-05"))
    assert set(c.membership.frame.date) == set(DATES[2:5])
    e = build_cohort(station(), [ext("aaa", [2.0] * 10)], spec())
    assert e.status == "empty" and len(e.paired) == 0
    assert {"empty_cohort", "model_without_rows"} <= e.report.codes("warning")


def test_paired_method_matches_footprint_sensitivity_on_sets():
    """build_cohort generalizes the step-6 frozen-key rule."""
    other = ExtractionSide("footprint_conservative", "SET2")
    frames = [ext("aaa", [2.0, 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9]),
              ext("aaa", [3.0] * 4 + [np.nan] + [3.0] * 5, side=other)]
    sets = [SETS["SET"], dataclasses.replace(SETS["SET"], set_id="SET2")]
    c = build_cohort(station(), frames, spec("paired_method", models=("aaa",),
                                             sides=(FP, other)), footprint_sets=sets)
    fs = compare_footprint_sets(frames, sets, comparison_id="cmp", station_daily=station(),
                                reference_variants=("et_corrected_inclusive",))
    cols = ["site_id", "date", "model", "footprint_set_id", "observed_mm", "modeled_mm",
            "reference_provenance"]
    a = c.paired.frame[cols].sort_values(cols[:4], ignore_index=True)
    b = fs.paired.frame[cols].sort_values(cols[:4], ignore_index=True)
    pd.testing.assert_frame_equal(a, b)


# --------------------------------------------------------------------------- config

@pytest.mark.parametrize("kw, match", [
    (dict(kind="pairwise_model", models=("a", "b", "c")), "exactly two models"),
    (dict(kind="common_date", models=("a",)), "at least two models"),
    (dict(kind="paired_method"), "at least two sides"),
    (dict(kind="paired_reference"), "at least two reference variants"),
    (dict(kind="model_available", variants=("et_raw", "et_corrected_measured")),
     "exactly one side"),
    (dict(kind="common_date", common_models=True), "common_models applies only"),
    (dict(variants=("et_fill",)), "unknown reference variant"),
    (dict(min_retained_frac=1.5), "min_retained_frac"),
    (dict(start="2025-02-30"), "not an ISO date"),
    (dict(kind="ranking"), "kind"),
])
def test_spec_validation(kw, match):
    args = dict(kind="common_date", models=("a", "b"), variants=("et_raw",), sides=(FP,))
    args.update(kw)
    with pytest.raises(ContractError, match=match):
        spec(**args)


def test_comparisons_from_yaml_data():
    base = {
        "stations": {SITE: {"timezone": "UTC-07:00", "day_definition": "local_standard_time",
                            "interval_label": "start"}},
        "products": {m: {"units": "mm/day", "day_definition": "unknown",
                         "temporal_support": "interpolated_daily"} for m in ("aaa", "bbb")},
        "comparisons": [{
            "comparison_id": "rank", "kind": "common_date", "models": ["aaa", "bbb"],
            "reference_variants": ["et_raw"],
            "sides": [{"extraction_method": "pixel_window_3x3", "footprint_set_id": "none"}],
        }],
    }
    cfg = config_from_dict(base)
    assert cfg.comparison("rank").sides == (PIX,)
    bad = {**base, "comparisons": [{**base["comparisons"][0], "models": ["aaa", "zzz"]}]}
    with pytest.raises(ContractError, match="zzz"):
        config_from_dict(bad)
    bad = {**base, "comparisons": [{**base["comparisons"][0], "sides": [
        {"extraction_method": "footprint_conservative", "footprint_set_id": "NOPE"}]}]}
    with pytest.raises(ContractError, match="NOPE"):
        config_from_dict(bad)
    bad = {**base, "comparisons": [{**base["comparisons"][0], "threshold": 1}]}
    with pytest.raises(ContractError, match="unknown configuration key"):
        config_from_dict(bad)
