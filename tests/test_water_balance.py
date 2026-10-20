"""Guide step 15: water-balance plausibility checks (synthetic, analytic expectations)."""

import inspect
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import etcompare
from etcompare.config import (
    PeriodAggregationSpec,
    StationSpec,
    WaterBalanceBoundarySpec,
    WaterBalanceSpec,
    WaterBalanceTermSpec,
    config_from_dict,
)
from etcompare.errors import ContractError
from etcompare.schemas import STATION_DAILY, CanonicalTable
from etcompare.water_balance import compute_water_balance, depth_to_volume_m3

SITE = "US-SYN"
DAYS = pd.date_range("2024-01-01", "2024-03-31")          # 91 days, leap February
STATION = StationSpec(SITE, "UTC-07:00", "local_standard_time", "start")
AGG = PeriodAggregationSpec("syn_periods", periods=("month", "year", "water_year"))
# Binary-exact daily depths: 3 + 1 + 0.5 - 2 - 0.75 - 0.25 = 1.5 = dS per day.
P, IRR, CR, ET, RO, DP, DS = 3.0, 1.0, 0.5, 2.0, 0.75, 0.25, 1.5
AREA = 10_000.0


def station_table(days=DAYS, filled=()):
    filled = pd.DatetimeIndex(filled)
    is_filled = days.isin(filled)
    frame = pd.DataFrame({
        "site_id": SITE, "date": days,
        "et_raw_mm": np.where(is_filled, np.nan, ET / 1.25),
        "et_corrected_mm": ET, "gap_filled": is_filled, "raw_available": ~is_filled,
        "reference_provenance": np.where(is_filled, "corrected_filled", "corrected_from_raw"),
        "src_prcp": P, "src_irrigation": IRR,
    })
    return CanonicalTable.build(frame, STATION_DAILY, {"source_sha256": "synthetic",
                                                       "station": {"site_id": SITE}})


def write(tmp_path: Path, name: str, value, days=DAYS) -> Path:
    path = tmp_path / f"{name}.csv"
    pd.DataFrame({"date": days.strftime("%Y-%m-%d"), "value": value}).to_csv(path, index=False)
    return path


def table_term(path, units="mm/day", support="boundary", **kw):
    return WaterBalanceTermSpec(status="measured", source="table", path=path, date_column="date",
                                value_column="value", units=units, spatial_support=support,
                                day_definition="local_standard_time", **kw)


def column_term(column, support="boundary", status="measured", day="local_standard_time"):
    return WaterBalanceTermSpec(status=status, source="station_column", column=column,
                                units="mm/day", spatial_support=support, day_definition=day)


def unknown(reason="no input"):
    return WaterBalanceTermSpec(status="unknown", source="none", reason=reason)


def make_closed_terms(tmp_path):
    """Terms of a closed balance: P + I + CR - ET - RO - DP - dS = 0 every day."""
    state_days = pd.date_range("2023-12-31", "2024-03-31")
    storage = 100.0 + DS * np.arange(len(state_days))
    return {
        "precipitation": column_term("src_prcp"),
        "irrigation": column_term("src_irrigation"),
        "capillary_rise": table_term(write(tmp_path, "cr", CR)),
        "et": WaterBalanceTermSpec(status="measured", source="station_et",
                                   reference_variant="et_corrected_inclusive",
                                   spatial_support="boundary"),
        "runoff": table_term(write(tmp_path, "ro", RO)),
        "deep_percolation": table_term(write(tmp_path, "dp", DP)),
        "storage_change": table_term(write(tmp_path, "s", storage, state_days), units="mm",
                                     quantity="storage_state", state_time="end_of_station_day"),
    }


@pytest.fixture
def closed_terms(tmp_path):
    return make_closed_terms(tmp_path)


def boundary(area=AREA, kind="polygon"):
    if kind == "point":
        return WaterBalanceBoundarySpec("tower_point", "point", depth_mm=1000.0)
    return WaterBalanceBoundarySpec(
        "field", "polygon", area_m2=area, area_source=None if area is None else "synthetic",
        path=Path("fields.gpkg"), crs="EPSG:26912", id_column="fieldid", feature_id="1",
        source="synthetic", vintage="unknown")


def spec(terms, *, b=None, volumes=True, periods=None):
    return WaterBalanceSpec("syn_balance", SITE, "syn_periods", boundary() if b is None else b,
                            "soil_zone", "inflows_positive", terms, periods=periods,
                            volumes=volumes)


def run(s, station=None):
    return compute_water_balance(s, station or station_table(), period_aggregation=AGG,
                                 station=STATION)


def residual(result, label, kind="measured_modeled"):
    f = result.residuals.frame
    return f[(f["period_label"] == label) & (f["residual_kind"] == kind)].iloc[0]


def term(result, label, name):
    f = result.terms.frame
    return f[(f["period_label"] == label) & (f["term"] == name)].iloc[0]


# --------------------------------------------------------------------------- gates

def test_closed_synthetic_balance_has_zero_residual(closed_terms):
    r = run(spec(closed_terms))
    months = {"2024-01": 31, "2024-02": 29, "2024-03": 31}
    for label, n in months.items():
        row = residual(r, label)
        assert row["status"] == "computed" and row["residual_mm"] == 0.0
        assert row["residual_volume_m3"] == 0.0 and row["volume_status"] == "computed"
        assert row["n_days_expected"] == n and pd.isna(row["unknown_terms"])
        for name, daily in (("precipitation", P), ("irrigation", IRR), ("capillary_rise", CR),
                            ("et", ET), ("runoff", RO), ("deep_percolation", DP),
                            ("storage_change", DS)):
            t = term(r, label, name)
            assert t["total_status"] == "complete" and t["total_mm"] == daily * n
            assert t["volume_m3"] == pytest.approx(daily * n / 1000 * AREA)
        assert term(r, label, "et")["signed_total_mm"] == -ET * n
        assert "plausibility_diagnostic_only" in row["flags"] and "not_et_error" in row["flags"]
        assert "boundary_vintage_unknown" in row["flags"]
        assert residual(r, label, "scenario")["status"] == "not_applicable"
    assert "never ET error" in r.residuals.frame["interpretation"].iloc[0]


def test_unknown_inflow_keeps_the_residual_unknown(closed_terms):
    r = run(spec({**closed_terms, "irrigation": unknown("no irrigation records")}))
    for label in ("2024-01", "2024-02", "2024-03"):
        row = residual(r, label)
        assert row["status"] == "unknown" and pd.isna(row["residual_mm"])  # never a silent zero
        assert row["unknown_terms"] == "irrigation" and "irrigation" in row["reason"]
        assert pd.isna(row["residual_volume_m3"]) and row["volume_status"] == "no_value"
        t = term(r, label, "irrigation")
        assert t["total_status"] == "unknown_term" and pd.isna(t["total_mm"])
        assert pd.isna(t["partial_sum_mm"]) and pd.isna(t["support_matches_boundary"])
    # Every unknown term is listed.
    r2 = run(spec({**closed_terms, "irrigation": unknown(), "runoff": unknown(),
                   "capillary_rise": unknown()}))
    row = residual(r2, "2024-02")
    assert row["unknown_terms"] == "irrigation;capillary_rise;runoff"
    for t in ("irrigation", "capillary_rise", "runoff"):
        assert t in row["reason"]


def test_support_mismatch_is_flagged_never_treated_as_equal(closed_terms):
    terms = {**closed_terms, "precipitation": column_term("src_prcp", support="gridded_cell"),
             "et": replace(closed_terms["et"], spatial_support="tower_footprint")}
    r = run(spec(terms))
    row = residual(r, "2024-01")
    assert row["support_mismatch_terms"] == "precipitation;et"
    assert "support_mismatch" in row["flags"]
    # The residual depth is computed but flagged; no volume of the boundary is implied.
    assert row["residual_mm"] == 0.0 and pd.isna(row["residual_volume_m3"])
    assert row["volume_status"] == "support_mismatch"
    p = term(r, "2024-01", "precipitation")
    assert not p["support_matches_boundary"] and "support_mismatch" in p["flags"]
    assert pd.isna(p["volume_m3"]) and p["volume_status"] == "support_mismatch"
    assert term(r, "2024-01", "irrigation")["support_matches_boundary"]
    assert "support_mismatch" in r.report.codes("warning")


def test_point_boundary_matches_station_point_and_has_no_area(closed_terms):
    terms = {t: (replace(s, spatial_support="station_point") if s.status != "unknown" else s)
             for t, s in closed_terms.items()}
    r = run(spec(terms, b=boundary(kind="point"), volumes=False))
    row = residual(r, "2024-01")
    assert row["residual_mm"] == 0.0 and pd.isna(row["support_mismatch_terms"])
    assert row["volume_status"] == "not_requested"
    with pytest.raises(ContractError, match="no area"):
        WaterBalanceBoundarySpec("p", "point", area_m2=5.0, area_source="x").check("b")


def test_volumes_are_refused_without_a_known_area(closed_terms):
    with pytest.raises(ContractError, match="known area"):
        spec(closed_terms, b=boundary(area=None), volumes=True)
    with pytest.raises(ContractError, match="known area"):
        spec(closed_terms, b=boundary(kind="point"), volumes=True)
    for b in (boundary(area=None), boundary(kind="point")):
        b.check("b")
        with pytest.raises(ContractError, match="not converted to a volume"):
            depth_to_volume_m3(10.0, b)
    assert depth_to_volume_m3(10.0, boundary()) == 100.0      # 10 mm over 1 ha = 100 m3
    r = run(spec(closed_terms, b=boundary(area=None), volumes=False))
    assert set(r.residuals.frame["volume_status"]) == {"not_requested"}
    assert r.residuals.frame["residual_volume_m3"].isna().all()


def test_tower_footprint_is_never_a_boundary():
    with pytest.raises(ContractError, match="never a water-balance boundary"):
        WaterBalanceBoundarySpec("fp", "tower_footprint").check("b")


# --------------------------------------------------------------------------- completeness

def test_incomplete_period_gives_partial_sum_only(closed_terms, tmp_path):
    days = DAYS[DAYS != pd.Timestamp("2024-01-15")]
    terms = {**closed_terms, "runoff": table_term(write(tmp_path, "ro2", RO, days))}
    r = run(spec(terms))
    t = term(r, "2024-01", "runoff")
    assert t["total_status"] == "incomplete_period" and pd.isna(t["total_mm"])
    assert t["partial_sum_mm"] == RO * 30 and t["n_values_present"] == 30
    assert t["n_values_required"] == 31 and t["period_completeness"] == pytest.approx(30 / 31)
    row = residual(r, "2024-01")
    assert row["status"] == "unknown" and row["incomplete_terms"] == "runoff"
    assert "30 of 31 days" in row["reason"]
    assert residual(r, "2024-02")["residual_mm"] == 0.0


def test_calendar_denominators_follow_step_8(closed_terms):
    r = run(spec(closed_terms))
    f = r.residuals.frame
    row = f[(f["period_label"] == "2024") & (f["residual_kind"] == "measured_modeled")].iloc[0]
    assert row["n_days_expected"] == 366 and row["status"] == "unknown"
    assert "period_outside_record" in row["flags"]
    wy = f[(f["period_label"] == "WY2024") & (f["residual_kind"] == "measured_modeled")].iloc[0]
    assert wy["n_days_expected"] == 366 and wy["period_start"] == pd.Timestamp("2023-10-01")
    p = term(r, "2024", "precipitation")
    assert p["partial_sum_mm"] == P * 91 and pd.isna(p["total_mm"])
    # A subset of the aggregation's periods.
    r2 = run(spec(closed_terms, periods=("month",)))
    assert set(r2.residuals.frame["period_type"]) == {"month"}


def test_storage_change_needs_both_states(closed_terms, tmp_path):
    state_days = pd.date_range("2023-12-31", "2024-03-31")
    storage = pd.Series(100.0 + DS * np.arange(len(state_days)), index=state_days)
    storage = storage.drop(pd.Timestamp("2024-01-31"))
    path = write(tmp_path, "s2", storage.to_numpy(), storage.index)
    terms = {**closed_terms, "storage_change": replace(closed_terms["storage_change"], path=path)}
    r = run(spec(terms))
    for label in ("2024-01", "2024-02"):       # 01-31 ends January and precedes February
        t = term(r, label, "storage_change")
        assert t["total_status"] == "incomplete_period" and t["n_values_present"] == 1
        assert pd.isna(t["partial_sum_mm"])
    assert term(r, "2024-03", "storage_change")["total_mm"] == DS * 31


def test_gap_filled_et_days_are_counted_and_flagged(closed_terms):
    filled = pd.date_range("2024-02-10", "2024-02-14")
    station = station_table(filled=filled)
    r = run(spec(closed_terms), station)
    t = term(r, "2024-02", "et")
    assert t["n_days_et_gap_filled"] == 5 and t["reference_variant"] == "et_corrected_inclusive"
    assert "shared_gridmet_drivers" in t["flags"]
    row = residual(r, "2024-02")
    assert row["n_days_et_gap_filled"] == 5 and row["residual_mm"] == 0.0
    assert "includes_gap_filled_et_days" in row["flags"]
    assert residual(r, "2024-01")["n_days_et_gap_filled"] == 0
    # The measured-only variant has no value on those days: February is incomplete.
    measured = {**closed_terms, "et": replace(closed_terms["et"],
                                              reference_variant="et_corrected_measured")}
    r2 = run(spec(measured), station)
    t2 = term(r2, "2024-02", "et")
    assert t2["total_status"] == "incomplete_period" and t2["n_values_present"] == 24
    assert residual(r2, "2024-02")["incomplete_terms"] == "et"


def test_unknown_day_definition_is_flagged(closed_terms):
    terms = {**closed_terms, "precipitation": column_term("src_prcp", day="unknown")}
    r = run(spec(terms))
    assert "day_alignment=unverified" in term(r, "2024-01", "precipitation")["flags"]
    assert "day_alignment=unverified" in residual(r, "2024-01")["flags"]
    et_flags = term(r, "2024-01", "et")["flags"]
    assert pd.isna(et_flags) or "day_alignment=unverified" not in et_flags


def test_scenario_term_gives_a_separately_labelled_scenario_residual(closed_terms):
    s = WaterBalanceTermSpec(status="scenario", source="scenario", daily_mm=IRR,
                             justification="synthetic assumption", spatial_support="boundary")
    r = run(spec({**closed_terms, "irrigation": s}))
    mm = residual(r, "2024-01")
    assert mm["status"] == "unknown" and pd.isna(mm["residual_mm"])
    assert mm["scenario_terms"] == "irrigation" and "scenario residual" in mm["reason"]
    sc = residual(r, "2024-01", "scenario")
    assert sc["status"] == "computed" and sc["residual_mm"] == 0.0
    assert "scenario_assumption" in sc["flags"]
    t = term(r, "2024-01", "irrigation")
    assert t["status"] == "scenario" and t["total_mm"] == IRR * 31


def test_negative_values_are_kept(closed_terms):
    station = station_table()
    f = station.frame.copy()
    f.loc[0, "src_irrigation"] = -1.0
    r = run(spec(closed_terms), CanonicalTable.build(f, STATION_DAILY))
    assert term(r, "2024-01", "irrigation")["total_mm"] == IRR * 30 - 1.0
    assert "negative_values" in r.report.codes("info")


def test_growing_season_needs_attached_windows(closed_terms):
    agg = PeriodAggregationSpec("syn_periods", periods=("growing_season",),
                                growing_season_source="crop")
    s = spec(closed_terms)
    with pytest.raises(ContractError, match="with_growing_seasons"):
        compute_water_balance(s, station_table(), period_aggregation=agg, station=STATION)


def test_parquet_round_trip(closed_terms, tmp_path):
    from etcompare.io.tables import read_table, write_table

    r = run(spec(closed_terms))
    for name, t in r.tables().items():
        back = read_table(write_table(t, tmp_path / f"{name}.parquet"))
        pd.testing.assert_frame_equal(back.frame, t.frame)
        assert back.attributes["equation"].startswith("residual = precipitation")


# --------------------------------------------------------------------------- isolation

def test_no_cohort_or_model_input():
    params = set(inspect.signature(compute_water_balance).parameters)
    assert params == {"spec", "station_daily", "period_aggregation", "station"}


def test_residuals_feed_no_selection():
    # Only the scenarios module uses the water balance as an input; rankings, cohorts,
    # metrics, filters and footprints never see a residual. The step-17 orchestration
    # (workflow runs the unit), the report (prints its table) and the demo (declares one)
    # name it without passing a residual anywhere.
    src = Path(etcompare.__file__).parent
    users = sorted(p.name for p in src.rglob("*.py")
                   if "water_balance" in p.read_text(encoding="utf-8")
                   and p.name not in ("water_balance.py", "config.py", "schemas.py"))
    assert users == ["demo.py", "reporting.py", "scenarios.py", "workflow.py"]
    selecting = ("cohorts.py", "metrics.py", "bootstrap.py", "ranking.py", "diagnostics.py",
                 "ensemble.py", "extraction.py", "footprints.py", "aggregation.py",
                 "events.py", "anomalies.py", "satellite.py", "representativeness.py")
    for name in selecting:
        assert "water_balance" not in (src / name).read_text(encoding="utf-8"), name


# --------------------------------------------------------------------------- declarations

BASE = {
    "stations": {SITE: {"timezone": "UTC-07:00", "day_definition": "local_standard_time",
                        "interval_label": "start"}},
    "station_tables": [{"site_id": SITE, "date_column": "date",
                        "reference_columns": {"et_raw": "ET", "et_corrected": "ET_corr"},
                        "gap_flag_column": "ET_gap", "units": {"ET": "mm/day",
                                                               "ET_corr": "mm/day"}}],
    "period_aggregations": [{"aggregation_id": "syn_periods", "periods": ["month", "year"]}],
}
UNKNOWN = {"status": "unknown", "source": "none", "reason": "no input"}


def balance(**over):
    terms = {t: dict(UNKNOWN) for t in ("precipitation", "irrigation", "capillary_rise", "et",
                                        "runoff", "deep_percolation", "storage_change")}
    terms["precipitation"] = {"status": "modeled", "source": "station_column",
                              "column": "src_gridMET_prcp", "units": "mm/day",
                              "spatial_support": "gridded_cell", "day_definition": "unknown"}
    terms.update(over.pop("terms", {}))
    body = {"water_balance_id": "wb", "site_id": SITE, "period_aggregation": "syn_periods",
            "boundary": {"boundary_id": "field", "kind": "polygon", "path": "f.gpkg",
                         "crs": "EPSG:26912", "id_column": "id", "feature_id": 7,
                         "source": "s", "vintage": "unknown"},
            "equation": "soil_zone", "sign_convention": "inflows_positive", "terms": terms}
    body.update(over)
    return body


def load(**over):
    return config_from_dict({**BASE, "water_balances": [balance(**over)]}, base_dir=Path("."))


def test_config_loads_a_declared_balance():
    cfg = load()
    w = cfg.water_balance("wb")
    assert w.terms["precipitation"].units == "mm d-1"
    assert w.boundary.feature_id == "7" and not w.boundary.area_known
    assert list(w.terms) == ["precipitation", "irrigation", "capillary_rise", "et", "runoff",
                             "deep_percolation", "storage_change"]
    assert w.equation_text.startswith("residual = precipitation + irrigation")


@pytest.mark.parametrize("over, match", [
    ({"terms": {"runoff": None}}, "mapping"),
    ({"equation": "basin"}, "equation"),
    ({"sign_convention": "outflows_positive"}, "sign_convention"),
    ({"period_aggregation": "nope"}, "not declared"),
    ({"periods": ["growing_season"]}, "not periods of"),
    ({"site_id": "US-XXX"}, "not declared"),
    ({"boundary": {"boundary_id": "fp", "kind": "footprint"}}, "never a water-balance boundary"),
    ({"boundary": {"boundary_id": "f", "kind": "polygon", "path": "f.gpkg"}}, "crs not declared"),
    ({"boundary": {"boundary_id": "f", "kind": "polygon", "path": "f", "crs": "c",
                   "id_column": "i", "feature_id": 1, "source": "s", "vintage": "unknown",
                   "area_m2": 5.0}}, "area_source"),
    ({"boundary": {"boundary_id": "f", "kind": "polygon", "path": "f", "crs": "c",
                   "id_column": "i", "feature_id": 1, "source": "s", "vintage": "unknown",
                   "area_m2": -5.0, "area_source": "x"}}, "> 0"),
    ({"volumes": True}, "known area"),
    ({"terms": {"irrigation": {"status": "unknown", "source": "none"}}}, "reason not declared"),
    ({"terms": {"irrigation": {"status": "measured", "source": "none"}}}, "needs source"),
    ({"terms": {"irrigation": {"status": "unknown", "source": "none", "reason": "r",
                               "daily_mm": 0.0}}}, "does not apply"),
    ({"terms": {"runoff": {"status": "scenario", "source": "scenario", "daily_mm": 0.0,
                           "spatial_support": "boundary"}}},
     "justification not declared"),
    ({"terms": {"runoff": {"status": "scenario", "source": "scenario", "daily_mm": ".nan",
                           "spatial_support": "boundary",
                           "justification": "j"}}}, "not a number|finite"),
    ({"terms": {"irrigation": {"status": "measured", "source": "table", "path": "i.csv",
                               "date_column": "d", "value_column": "v", "units": "mm/day",
                               "day_definition": "unknown", "spatial_support": "regional"}}},
     "regional diversion is not field irrigation"),
    ({"terms": {"irrigation": {"status": "measured", "source": "station_column", "column": "c",
                               "units": "mm", "day_definition": "unknown",
                               "spatial_support": "boundary"}}}, "not a daily depth"),
    ({"terms": {"irrigation": {"status": "measured", "source": "station_column", "column": "c",
                               "units": "mm/day", "spatial_support": "boundary"}}},
     "day_definition not declared"),
    ({"terms": {"irrigation": {"status": "measured", "source": "station_column", "column": "c",
                               "units": "mm/day", "day_definition": "unknown"}}},
     "spatial_support not declared"),
    ({"terms": {"irrigation": {"status": "measured", "source": "station_et",
                               "reference_variant": "et_raw", "spatial_support": "boundary"}}},
     "ET term's source only"),
    ({"terms": {"et": {"status": "measured", "source": "station_et",
                       "reference_variant": "et_fill", "spatial_support": "tower_footprint"}}},
     "reference_variant"),
    ({"terms": {"storage_change": {"status": "measured", "source": "table", "path": "s.csv",
                                   "date_column": "d", "value_column": "v", "units": "m3/m3",
                                   "day_definition": "unknown", "spatial_support": "boundary",
                                   "quantity": "storage_state",
                                   "state_time": "end_of_station_day"}}}, "storage depth"),
    ({"terms": {"storage_change": {"status": "measured", "source": "table", "path": "s.csv",
                                   "date_column": "d", "value_column": "v", "units": "mm",
                                   "day_definition": "unknown", "spatial_support": "boundary",
                                   "quantity": "storage_state"}}}, "state_time"),
    ({"terms": {"runoff": {"status": "measured", "source": "table", "path": "s.csv",
                           "date_column": "d", "value_column": "v", "units": "mm",
                           "day_definition": "unknown", "spatial_support": "boundary",
                           "quantity": "storage_state", "state_time": "end_of_station_day"}}},
     "only storage_change"),
    ({"terms": {"runoff": {"status": "measured", "source": "station_column", "column": "c",
                           "units": "mm/day", "day_definition": "unknown",
                           "spatial_support": "field"}}}, "spatial_support"),
])
def test_config_refusals(over, match):
    with pytest.raises(ContractError, match=match):
        load(**over)


def test_every_term_must_be_declared():
    body = balance()
    del body["terms"]["deep_percolation"]
    with pytest.raises(ContractError, match=r"deep_percolation.*never zero"):
        config_from_dict({**BASE, "water_balances": [body]}, base_dir=Path("."))


def test_duplicate_water_balance_ids():
    with pytest.raises(ContractError, match="duplicate water_balance_id"):
        config_from_dict({**BASE, "water_balances": [balance(), balance()]}, base_dir=Path("."))


def test_missing_station_column_is_refused(closed_terms):
    terms = {**closed_terms, "precipitation": column_term("prcp_missing")}
    with pytest.raises(ContractError, match="prefix src_"):
        run(spec(terms))
