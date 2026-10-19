"""The seven-station Utah ensemble on the user's local files (never distributed; D-014).

Skips unless the station exports and the extractions of tools/run_raster_extraction.py
(examples/utah_stations.yaml) are present (D-069, D-070).
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from etcompare.adapters.station import adapt_station_daily
from etcompare.config import load_config
from etcompare.ensemble import FITTED, MEAN, run_ensemble
from etcompare.io.tables import read_table

ROOT = Path(__file__).parents[2]
CFG = load_config(ROOT / "examples" / "utah_stations.yaml")
SPEC = CFG.ensemble("utah_ensemble")
OUT = CFG.output_dir
EXT = {x.site_id: OUT / f"{x.name}_extractions.parquet" for x in CFG.raster_extractions}  # type: ignore[operator]
TABLES = {t.site_id: t for t in CFG.station_tables}
MEMBERS = list(SPEC.members)
OLD_UTJ = ROOT / "outputs" / "us_utj_nldas_eto_extractions.parquet"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not all(p.exists() for p in EXT.values())
                       or not all(t.path is not None and t.path.exists()
                                  for t in TABLES.values()),
                       reason="Utah station exports or extractions not present"),
]


@pytest.fixture(scope="module")
def result():
    obs = [adapt_station_daily(t.path, t, CFG.stations[s]).table for s, t in TABLES.items()]
    return run_ensemble(SPEC, obs, [read_table(p) for p in EXT.values()],
                        products=CFG.products, metric_spec=CFG.metric_set(SPEC.metric_set),
                        bootstrap_spec=CFG.bootstrap_spec(SPEC.bootstrap),  # type: ignore[arg-type]
                        footprint_sets=CFG.footprint_sets)


@pytest.fixture(scope="module")
def independent():
    """Six members + official + et_corrected_measured per station, straight from the files."""
    out = {}
    for site, path in EXT.items():
        e = pd.read_parquet(path)
        e = e[(e["extraction_method"] == "footprint_conservative")
              & (e["footprint_set_id"] == SPEC.site_footprint_sets[site])]  # type: ignore[index]
        w = e.pivot(index="date", columns="model", values="et_mm")
        w.index = pd.to_datetime(w.index)
        c = pd.read_csv(TABLES[site].path, parse_dates=["date"]).set_index("date")
        meas = c["ET"].notna() & c["ET_corr"].notna() & ~c["ET_gap"].astype(bool)
        ref = c["ET_corr"].where(meas).reindex(w.index)
        ok = w.reindex(columns=MEMBERS).notna().all(axis=1) & w["ensemble"].notna() & ref.notna()
        out[site] = (w[ok], ref[ok])
    return out


def test_eligible_days_match_an_independent_count(result, independent):
    d = result.days.frame
    got = d[d["eligible"]].groupby("site_id").size().to_dict()
    assert got == {s: len(w) for s, (w, _) in independent.items()}
    assert sum(got.values()) == 1798
    p = result.predictions.frame
    for site, (w, _) in independent.items():
        mean = p[(p["product"] == MEAN) & (p["site_id"] == site)].set_index("date")["value_mm"]
        np.testing.assert_allclose(mean.sort_index(), w[MEMBERS].mean(axis=1), rtol=1e-12)


def test_leave_one_site_out_is_held_out_and_small_stations_never_train(result):
    s = result.summary.frame.set_index(["scope", "subset"])
    assert s.loc[("leave_one_site_out", "all"), "status"] == "held_out"
    assert s.loc[("leave_one_site_out", "all"), "n_folds_fitted"] == 7
    m = result.training_membership.frame
    m = m[m["design"] == "leave_one_site_out"]
    trained = set(m[m["in_training"]]["site_id"])
    assert "US-UTE" not in trained and "US-UTG" not in trained   # < 180 eligible days
    for site in ("US-UTD", "US-UTJ", "US-UTM", "US-UTV", "US-UTW"):
        own = m[(m["design"] == "leave_one_site_out") & (m["fold"] == site)]
        assert not own[own["site_id"] == site]["in_training"].any()
    w = result.weights.frame
    w = w[w["design"] == "leave_one_site_out"]
    assert np.allclose(w.groupby("fold")["weight"].sum(), 1.0)
    assert (w["weight"] >= 0).all()


def test_leave_one_year_out_has_no_supported_claim(result):
    f = result.folds.frame
    f = f[f["design"] == "leave_one_year_out"].set_index("fold")
    assert f.loc["2023", "status"] == "declined" and f.loc["2025", "status"] == "declined"
    assert f.loc["2024", "n_test_days"] == 5
    imp = result.improvement.frame
    imp = imp[imp["scope"] == "leave_one_year_out"]
    assert set(imp["held_out_improvement"]) == {"not_assessed"}


def test_fitted_predictions_use_only_other_stations_weights(result, independent):
    p = result.predictions.frame
    w = result.weights.frame
    w = w[w["design"] == "leave_one_site_out"]
    for site, (vals, _) in independent.items():
        weights = w[w["fold"] == site].set_index("member")["weight"].reindex(MEMBERS)
        fit = p[(p["product"] == FITTED) & (p["scope"] == "leave_one_site_out")
                & (p["site_id"] == site)].set_index("date")["value_mm"].sort_index()
        np.testing.assert_allclose(fit, vals[MEMBERS].to_numpy() @ weights.to_numpy(),
                                   rtol=1e-12)


def test_april_october_subset_has_only_growing_season_days(result):
    c = result.comparisons["leave_one_site_out__apr_oct"].cohort.paired.frame
    assert set(c["date"].dt.month) <= set(range(4, 11))
    s = result.summary.frame.set_index(["scope", "subset"])
    assert s.loc[("baseline", "apr_oct"), "n_days_compared"] == 1225


@pytest.mark.skipif(not OLD_UTJ.exists(), reason="earlier unclipped US-UTJ extraction absent")
def test_clipped_us_utj_rasters_reproduce_the_unclipped_extraction():
    a = read_table(OLD_UTJ).frame
    b = read_table(EXT["US-UTJ"]).frame
    k = ["date", "model"]
    a = a[a["extraction_method"] == "footprint_conservative"].set_index(k)["et_mm"]
    b = b[b["extraction_method"] == "footprint_conservative"].set_index(k)["et_mm"]
    assert a.index.sort_values().equals(b.index.sort_values())
    assert float((a - b.reindex(a.index)).abs().max()) < 1e-9
