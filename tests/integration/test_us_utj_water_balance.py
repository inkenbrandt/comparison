"""The US-UTJ tower-field water balance on the user's local files (never distributed; D-014).

The residual is unknown by design: irrigation, capillary rise, runoff, deep percolation and
storage change have no input (D-063).
"""

from pathlib import Path

import pandas as pd
import pytest

from etcompare.adapters.crop_height import adapt_crop_height, growing_season_windows
from etcompare.adapters.station import adapt_station_daily
from etcompare.config import load_config
from etcompare.water_balance import compute_water_balance

ROOT = Path(__file__).parents[2]
CFG = load_config(ROOT / "examples" / "us_utj.yaml")
WB = CFG.water_balance("utj_tower_field_balance")
TABLE = next(t for t in CFG.station_tables if t.site_id == "US-UTJ")
AGG = CFG.period_aggregation(WB.period_aggregation)
CROP = CFG.crop_height_table(AGG.growing_season_source)
UNKNOWN = "irrigation;capillary_rise;runoff;deep_percolation;storage_change"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(TABLE.path is None or not TABLE.path.exists()
                       or CROP.path is None or not CROP.path.exists(),
                       reason="US-UTJ station or crop-height file not present"),
]


@pytest.fixture(scope="module")
def result():
    station = adapt_station_daily(TABLE.path, TABLE, CFG.stations["US-UTJ"]).table
    agg = AGG.with_growing_seasons(growing_season_windows(adapt_crop_height(CROP.path, CROP)))
    return compute_water_balance(WB, station, period_aggregation=agg,
                                 station=CFG.stations["US-UTJ"])


@pytest.fixture(scope="module")
def csv():
    return pd.read_csv(TABLE.path, parse_dates=["date"]).set_index("date")


def test_residual_is_unknown_with_every_unknown_term_listed(result):
    f = result.residuals.frame
    mm = f[f["residual_kind"] == "measured_modeled"]
    assert len(mm) == 24 and set(mm["status"]) == {"unknown"}
    assert mm["residual_mm"].isna().all()                 # never a silent zero
    assert set(mm["unknown_terms"]) == {UNKNOWN}
    for t in UNKNOWN.split(";"):
        assert mm["reason"].str.contains(t).all()
    sc = f[f["residual_kind"] == "scenario"]
    assert set(sc["status"]) == {"not_applicable"}        # no scenario term declared
    assert set(f["volume_status"]) == {"no_value"}
    assert f["flags"].str.contains("plausibility_diagnostic_only").all()
    assert f["flags"].str.contains("boundary_vintage_unknown").all()


def test_supports_are_flagged_and_no_field_volume_is_implied(result):
    t = result.terms.frame
    for term, support in (("precipitation", "gridded_cell"), ("et", "tower_footprint")):
        rows = t[t["term"] == term]
        assert set(rows["spatial_support"]) == {support}
        assert not rows["support_matches_boundary"].any()
        assert rows["volume_m3"].isna().all()
        assert set(rows["volume_status"]) <= {"support_mismatch", "no_value"}
    r = result.residuals.frame
    assert set(r["support_mismatch_terms"]) == {"precipitation;et"}


def test_station_gauge_is_not_used(result):
    t = result.terms.frame
    assert "src_p_1_1_1" not in set(t["source"].dropna())
    assert result.residuals.attributes["water_balance"]["terms"]["precipitation"]["column"] \
        == "src_gridMET_prcp"


def test_complete_totals_match_the_csv(result, csv):
    t = result.terms.frame.set_index(["period_label", "term"])
    month = csv.loc["2025-07"]
    assert t.loc[("2025-07", "precipitation"), "total_mm"] == pytest.approx(
        month["gridMET_prcp"].sum())
    assert t.loc[("2025-07", "et"), "total_mm"] == pytest.approx(month["ET_corr"].sum())
    assert t.loc[("2025-07", "et"), "n_days_et_gap_filled"] == int(month["ET_gap"].sum())
    wy = csv.loc["2024-10-01":"2025-09-30"]
    assert len(wy) == 365 and wy["ET_corr"].notna().all()
    assert t.loc[("WY2025", "et"), "total_mm"] == pytest.approx(wy["ET_corr"].sum())
    assert t.loc[("WY2025", "et"), "n_days_et_gap_filled"] == int(wy["ET_gap"].sum()) == 127
    assert t.loc[("WY2025", "precipitation"), "total_mm"] == pytest.approx(
        wy["gridMET_prcp"].sum())
    # May 2025 is entirely gap-filled (D-061): complete, but every day is shared-driver fill.
    may = t.loc[("2025-05", "et")]
    assert may["n_days_et_gap_filled"] == 31 and "shared_gridmet_drivers" in may["flags"]


def test_partial_periods_are_never_totals(result, csv):
    t = result.terms.frame.set_index(["period_label", "term"])
    jun = t.loc[("2024-06", "et")]                        # record starts 2024-06-05
    assert jun["total_status"] == "incomplete_period" and pd.isna(jun["total_mm"])
    assert jun["n_values_present"] == csv.loc["2024-06", "ET_corr"].notna().sum() == 7
    for label in ("2024", "2025", "GS2024", "GS2025", "WY2024", "WY2026"):
        assert pd.isna(t.loc[(label, "et"), "total_mm"])


@pytest.mark.skipif(WB.boundary.path is None or not WB.boundary.path.exists(),
                    reason="Bluff.gpkg not present")
def test_declared_area_matches_the_polygon():
    gpd = pytest.importorskip("geopandas")
    b = WB.boundary
    g = gpd.read_file(b.path, layer=b.layer)
    field = g[g[b.id_column].astype(str) == b.feature_id].set_crs(b.crs, allow_override=True)
    assert len(field) == 1
    assert float(field.geometry.area.iloc[0]) == pytest.approx(b.area_m2, abs=0.01)
    assert float(field["acres"].iloc[0]) * 4046.8564224 == pytest.approx(b.area_m2, rel=0.003)
