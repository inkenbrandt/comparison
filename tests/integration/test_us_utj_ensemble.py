"""The US-UTJ ensemble experiment on the user's local files (never distributed; D-014).

The fitted ensemble is declined by design: with calendar-year folds the six members coexist
only in 2025, and leave-one-site-out needs other stations (D-066).
"""

from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from etcompare.adapters.station import adapt_station_daily
from etcompare.config import ExtractionSide, load_config
from etcompare.ensemble import MEAN, MEDIAN, run_ensemble
from etcompare.errors import ContractError
from etcompare.io.tables import read_table

ROOT = Path(__file__).parents[2]
CFG = load_config(ROOT / "examples" / "us_utj.yaml")
SPEC = CFG.ensemble("utj_ensemble")
TABLE = next(t for t in CFG.station_tables if t.site_id == "US-UTJ")
EXT = CFG.output_dir / f"{SPEC.sources[0]}_extractions.parquet"  # type: ignore[operator]
MEMBERS = ["disalexi", "eemetric", "geesebal", "ptjpl", "sims", "ssebop"]

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(TABLE.path is None or not TABLE.path.exists() or not EXT.exists(),
                       reason="US-UTJ station file or primary extractions not present"),
]


def run(spec=SPEC):
    station = adapt_station_daily(TABLE.path, TABLE, CFG.stations["US-UTJ"]).table
    boot = CFG.bootstrap_spec(spec.bootstrap) if spec.bootstrap else None
    return run_ensemble(spec, [station], [read_table(EXT)], products=CFG.products,
                        metric_spec=CFG.metric_set(spec.metric_set), bootstrap_spec=boot,
                        footprint_sets=CFG.footprint_sets)


@pytest.fixture(scope="module")
def result():
    return run()


@pytest.fixture(scope="module")
def independent():
    """Six members, the official ensemble and et_corrected_measured, straight from the files."""
    e = pd.read_parquet(EXT)
    e = e[(e["extraction_method"] == SPEC.side.extraction_method)
          & (e["footprint_set_id"] == SPEC.side.footprint_set_id) & (e["period"] == "day")]
    w = e.pivot(index="date", columns="model", values="et_mm")
    w.index = pd.to_datetime(w.index)
    csv = pd.read_csv(TABLE.path, parse_dates=["date"]).set_index("date")
    measured = csv["ET"].notna() & csv["ET_corr"].notna() & ~csv["ET_gap"].astype(bool)
    ref = csv["ET_corr"].where(measured).reindex(w.index)
    ok = w[MEMBERS].notna().all(axis=1) & w["ensemble"].notna() & ref.notna()
    return w[ok], ref[ok]


def test_fitted_ensemble_is_declined_and_site_folds_are_not_available(result):
    s = result.summary.frame.set_index("scope")
    assert s.loc["leave_one_year_out", "status"] == "declined"
    assert "1 year fold(s) with eligible days (2025)" in s.loc["leave_one_year_out",
                                                               "status_detail"]
    assert "min_training_folds is 2" in s.loc["leave_one_year_out", "status_detail"]
    assert s.loc["leave_one_site_out", "status"] == "not_available"
    assert s.loc["baseline", "status"] == "evaluated"
    assert result.weights.frame.empty and result.inner_scores.frame.empty
    assert set(result.predictions.frame["product"]) == {"ensemble", MEAN, MEDIAN}
    assert set(result.comparisons) == {"baseline"} and result.improvement.frame.empty
    assert set(s["flags"].str.contains("single_site_not_network_evidence")) == {True}
    assert set(s["ensemble_level"]) == {"post_extraction"}


def test_baseline_days_and_values_match_an_independent_count(result, independent):
    w, ref = independent
    assert len(w) == 188 and w.index.min() == pd.Timestamp("2025-01-01")
    assert w.index.max() == pd.Timestamp("2025-10-08")     # 10-09 has no measured ET
    p = result.predictions.frame
    mean = p[p["product"] == MEAN].set_index("date")["value_mm"].sort_index()
    assert mean.index.equals(w.index)
    np.testing.assert_allclose(mean, w[MEMBERS].mean(axis=1), rtol=1e-12)
    med = p[p["product"] == MEDIAN].set_index("date")["value_mm"].sort_index()
    np.testing.assert_allclose(med, w[MEMBERS].median(axis=1), rtol=1e-12)
    m = result.comparisons["baseline"].metrics.table.frame
    m = m[m["temporal_scale"] == "daily"].set_index(["model", "metric"])["value"]
    for model, values in (("ensemble", w["ensemble"]), (MEAN, w[MEMBERS].mean(axis=1))):
        err = values - ref
        assert m[(model, "mae")] == pytest.approx(err.abs().mean(), rel=1e-12)
        assert m[(model, "rmse")] == pytest.approx(np.sqrt((err ** 2).mean()), rel=1e-12)


def test_ineligible_days_are_reported(result):
    d = result.days.frame
    d = d[d["reference_provenance"].notna()]
    assert d["eligibility_reason"].fillna("eligible").value_counts().to_dict() == {
        "eligible": 188, "member_missing": 115}
    assert (d.loc[~d["eligible"], "date"].dt.year == 2024).all()
    assert d["reference_provenance"].eq("corrected_from_raw").all()   # no gap-filled rows


def test_water_year_folds_and_a_two_member_subset_are_declined_too():
    wy = run(replace(SPEC, fold_unit="water_year", designs=("leave_one_year_out",)))
    f = wy.folds.frame.set_index("fold")
    assert list(f.index) == ["WY2025", "WY2026"] and set(f["status"]) == {"declined"}
    assert f.loc["WY2026", "n_test_days"] == 8
    pair = run(replace(SPEC, members=("eemetric", "ssebop"), designs=("leave_one_year_out",)))
    assert pair.summary.frame.set_index("scope").loc["leave_one_year_out", "status"] == \
        "declined"


def test_official_ensemble_and_measured_le_footprints_are_refused():
    with pytest.raises(ContractError, match="never a member"):
        replace(SPEC, members=(*SPEC.members, "ensemble"))
    with pytest.raises(ContractError, match="ensemble product"):
        run(replace(SPEC, members=("eemetric", "ensemble"), official=None))
    le = ExtractionSide("footprint_conservative", "US-UTJ_measured_le_20261014")
    with pytest.raises(ContractError, match="target"):
        run(replace(SPEC, side=le))
