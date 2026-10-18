"""Station event logs: the event-table declaration and adapter (guide step 14)."""

import inspect

import pandas as pd
import pytest

from etcompare.adapters.event_log import adapt_event_table, approximate_flags
from etcompare.config import EventTableSpec, StationSpec, config_from_dict
from etcompare.errors import ContractError, DataValidationError, SchemaError
from etcompare.io.tables import read_table, write_table

SITE = "US-XXX"
STATIONS = {SITE: StationSpec(SITE, "UTC-07:00", "local_standard_time", "start")}
PATTERNS = (r"\bdate\s+(?:is\s+)?approximate\b", r"\bapproximate\s+date\b")


def spec_kw(**over):
    kw = dict(
        name="events", path="events.csv",
        columns={"event_id": "event_id", "site": "stationid", "timestamp": "event_date",
                 "event_type": "event_type", "event_source": "event_source",
                 "observer": "observer", "notes": "event_notes"},
        timestamp_format="%Y-%m-%d %H:%M:%S.%f", timestamp_timezone="UTC-07:00",
        day_rule="station_local_date", site_mapping={SITE: SITE},
        site_windows={SITE: {"start": "2024-06-01", "end": "2024-12-31"}},
        event_types={"cutting": {"category": "management", "management_kind": "cutting"},
                     "station visit": {"category": "instrument"},
                     "program update": {"category": "instrument"},
                     "clouds": {"category": "confounder"}},
        evidence_classes={"camera": "photo_observation",
                          "Planet imagery": "remote_sensing_inference",
                          "Sentinel NDVI": "remote_sensing_inference", "eddy": "station_log"},
        approximate_date_patterns=PATTERNS,
    )
    kw.update(over)
    return kw


def spec(**over):
    return EventTableSpec(**spec_kw(**over))


def log(rows=None):
    """A small log in the source layout: every value is text, as read from the CSV."""
    rows = rows or [
        ("1", SITE, "2024-06-11 00:00:00.000", "PCI", "cutting", "Planet imagery", None),
        ("2", SITE, "2024-07-15 13:36:00.000", "PI, KL", "station visit", None,
         "Irgason moved to 9.9 ft and azimuth is 293 degrees"),
        ("3", SITE, "2024-09-08 00:00:00.000", "DM", "cutting", "camera",
         "date approximate; hay bales on 2024-09-11"),
        ("4", SITE, "2024-10-09 00:00:00.000", "DRM", "program update", "eddy",
         "depth approximate"),
        ("5", SITE, "2024-08-01 00:00:00.000", "PCI", "clouds", "Sentinel NDVI", None),
        ("6", SITE, "2023-07-12 00:00:00.000", "PCI", "cutting", "Planet imagery", None),
        ("7", "US-YYY", "2024-07-01 08:00:00.000", "KB", "pivot irrigation", "soil moisture",
         None),
    ]
    df = pd.DataFrame(rows, columns=["event_id", "stationid", "event_date", "observer",
                                     "event_type", "event_source", "event_notes"])
    df["zero_span_results"] = None
    return df


def adapt(df=None, **over):
    return adapt_event_table(log() if df is None else df, spec(**over), stations=STATIONS)


# --------------------------------------------------------------------------- adapter

def test_events_keep_their_logged_context():
    r = adapt()
    f = r.frame.set_index("event_id")
    assert r.report.ok and list(f.index) == ["1", "2", "3", "4", "5"]
    cut = f.loc["1"]
    assert (cut.site_id, cut.start_date, cut.end_date) == (
        SITE, pd.Timestamp("2024-06-11"), pd.Timestamp("2024-06-11"))
    assert pd.isna(cut.start_time)                      # logged at 00:00: a date-only entry
    assert (cut.category, cut.management_kind, cut.analysis_role) == (
        "management", "cutting", "management_event")
    assert (cut.event_source, cut.evidence_class, cut.observer) == (
        "Planet imagery", "remote_sensing_inference", "PCI")
    visit = f.loc["2"]
    assert (visit.start_date, visit.start_time) == (pd.Timestamp("2024-07-15"), "13:36:00")
    assert (visit.category, visit.analysis_role) == ("instrument", "overlap_only")
    assert pd.isna(visit.management_kind)
    assert visit.event_notes.startswith("Irgason moved")
    assert pd.isna(visit.event_source) and visit.evidence_class == "unknown"
    assert f.loc["3"].evidence_class == "photo_observation"
    assert f.loc["4"].evidence_class == "station_log"
    assert (f.loc["5"].category, f.loc["5"].analysis_role) == ("confounder", "overlap_only")
    assert {"src_event_id", "src_zero_span_results", "src_event_notes"} <= set(r.frame.columns)
    assert r.attributes["events_defined_from"] == "event_log_only"


def test_only_management_events_can_be_treatments():
    f = adapt().frame
    assert set(f.loc[f.analysis_role == "management_event", "category"]) == {"management"}
    assert set(f.loc[f.category != "management", "analysis_role"]) == {"overlap_only"}
    assert f.loc[f.category != "management", "management_kind"].isna().all()


def test_cuttings_are_never_irrigation():
    f = adapt().frame
    assert set(f["management_kind"].dropna()) == {"cutting"}


def test_station_day_is_the_logged_calendar_date():
    df = log([("1", SITE, "2024-07-01 23:59:00.000", "PI", "station visit", None, None),
              ("2", SITE, "2024-07-02 00:01:00.000", "PI", "station visit", None, None)])
    f = adapt(df).frame.set_index("event_id")
    assert f.loc["1"].start_date == pd.Timestamp("2024-07-01")
    assert f.loc["2"].start_date == pd.Timestamp("2024-07-02")


def test_unmapped_stations_and_events_outside_the_record_are_excluded_with_reasons():
    r = adapt()
    x = r.exclusions.to_frame().set_index("reason_code")
    assert x.loc["station_not_declared", "input_key"].endswith("source_station=US-YYY")
    assert x.loc["outside_station_record", "input_key"].endswith("event_id=6")
    assert "2023-07-12" in x.loc["outside_station_record", "detail"]
    c = r.attributes["counts"]
    assert (c["n_events"], c["n_excluded_station_not_declared"],
            c["n_excluded_outside_station_record"]) == (5, 1, 1)


def test_rows_of_unmapped_stations_need_no_declaration():
    # US-YYY's "pivot irrigation" / "soil moisture" are undeclared but never read.
    assert adapt().report.ok


def test_end_timestamps_and_record_edges():
    df = log([("1", SITE, "2024-05-30 00:00:00.000", "PI", "station visit", None, None),
              ("2", SITE, "2024-08-01 00:00:00.000", "PI", "station visit", None, None)])
    df["end"] = ["2024-06-02 00:00:00.000", None]
    cols = {**spec_kw()["columns"], "end_timestamp": "end"}
    r = adapt(df, columns=cols)
    f = r.frame.set_index("event_id")
    assert f.loc["1"].end_date == pd.Timestamp("2024-06-02")      # crosses the record start
    assert f.loc["2"].end_date == f.loc["2"].start_date            # no end: one station day
    assert {"event_crosses_record_edge", "single_day_events"} <= r.report.codes()
    df["end"] = ["2024-05-29 00:00:00.000", None]
    with pytest.raises(DataValidationError, match="end before"):
        adapt(df, columns=cols)


# --------------------------------------------------------------------------- approximate

@pytest.mark.parametrize("note, expected", [
    ("date approximate; hay bales on 2024-09-11", PATTERNS[0]),
    ("Date is Approximate", PATTERNS[0]),
    ("approximate date, from photos", PATTERNS[1]),
    ("depth approximate", None),                       # another quantity, not the date
    ("heat flux plate depth of about 0.05", None),
    ("time estimated based on when sensor values improved", None),
    ("dated approximately", None),
    (None, None),
])
def test_approximate_rule(note, expected):
    got = approximate_flags(pd.Series([note], dtype="string"), PATTERNS).iloc[0]
    assert (None if pd.isna(got) else got) == expected


def test_approximate_flag_is_set_only_by_the_declared_rule():
    f = adapt().frame.set_index("event_id")
    assert f["approximate"].to_dict() == {"1": False, "2": False, "3": True, "4": False,
                                          "5": False}
    assert f.loc["3"].approximate_rule == PATTERNS[0]
    assert not adapt(approximate_date_patterns=[]).frame["approximate"].any()


# --------------------------------------------------------------------------- refusals

def _dup(df):
    df.loc[6, "event_id"] = "1"                        # duplicated across stations
    return df


@pytest.mark.parametrize("mutate, exc, match", [
    (_dup, DataValidationError, "occur more than once"),
    (lambda d: d.assign(event_id=d.event_id.where(d.index != 0, None)), DataValidationError,
     "without an event ID"),
    (lambda d: d.assign(event_type=d.event_type.replace("clouds", "snow")), DataValidationError,
     "'snow'.*not declared"),
    (lambda d: d.assign(event_source=d.event_source.replace("camera", "farm notebook")),
     DataValidationError, "'farm notebook'.*no evidence class"),
    (lambda d: d.assign(event_date=d.event_date.replace("2024-09-08 00:00:00.000",
                                                        "2024-09-08")),
     DataValidationError, "do not match"),
    (lambda d: d.assign(event_date=d.event_date.where(d.index != 0, "  ")), DataValidationError,
     "without a timestamp"),
    (lambda d: d.drop(columns="observer"), SchemaError, "observer"),
])
def test_adapter_refuses(mutate, exc, match):
    with pytest.raises(exc, match=match):
        adapt(mutate(log()))


def test_timezone_aware_timestamps_are_refused():
    df = log()
    df["event_date"] = pd.to_datetime(df.event_date).dt.tz_localize("UTC")
    with pytest.raises(DataValidationError, match="timezone-aware"):
        adapt(df)


def test_missing_source_file(tmp_path):
    with pytest.raises(ContractError, match="not found"):
        adapt_event_table(tmp_path / "nope.csv", spec(), stations=STATIONS)


def test_timezone_must_match_the_station():
    with pytest.raises(ContractError, match="never shifted"):
        adapt(timestamp_timezone="UTC-06:00")
    with pytest.raises(ContractError, match="not declared"):
        adapt_event_table(log(), spec(), stations={})


def test_events_are_defined_only_from_the_log():
    params = set(inspect.signature(adapt_event_table).parameters)
    assert params == {"source", "spec", "stations"}     # no model, residual or cohort input


def test_csv_and_parquet_round_trip(tmp_path):
    p = tmp_path / "events.csv"
    log().to_csv(p, index=False)
    r = adapt_event_table(p, spec(), stations=STATIONS)
    names = r.table.schema.names
    assert r.attributes["source_sha256"]
    pd.testing.assert_frame_equal(r.frame[names], adapt().frame[names])
    back = read_table(write_table(r.table, tmp_path / "events.parquet"))
    pd.testing.assert_frame_equal(back.frame[names], r.frame[names])


# --------------------------------------------------------------------------- declaration

@pytest.mark.parametrize("over, match", [
    ({"path": None}, "path not declared"),
    ({"columns": {"event_id": "id"}}, "columns.site not declared"),
    ({"columns": {**spec_kw()["columns"], "treatment": "t"}}, "unknown column mapping"),
    ({"day_rule": "utc_date"}, "day_rule"),
    ({"timestamp_timezone": None}, "timezone not declared"),
    ({"site_mapping": {}}, "declares no station"),
    ({"site_windows": {}}, "no station record window"),
    ({"site_windows": {SITE: {"start": "2024-12-31", "end": "2024-01-01"}}}, "after end"),
    ({"event_types": {}}, "no event_types"),
    ({"event_types": {"cutting": {"category": "management"}}}, "management_kind not declared"),
    ({"event_types": {"visit": {"category": "instrument", "management_kind": "cutting"}}},
     "management events only"),
    ({"event_types": {"cutting": {"category": "treatment"}}}, "category"),
    ({"event_types": {"cutting": {"category": "management",
                                  "management_kind": "mowing"}}}, "management_kind"),
    ({"evidence_classes": {"camera": "farm_record"}}, "evidence_classes.camera"),
    ({"approximate_date_patterns": None}, "approximate_date_patterns not declared"),
    ({"approximate_date_patterns": "approximate"}, "must be a list"),
    ({"approximate_date_patterns": ["(unclosed"]}, "not a valid regular expression"),
])
def test_declaration_refuses(over, match):
    with pytest.raises(ContractError, match=match):
        spec(**over)


def test_config_section():
    station = {"timezone": "UTC-07:00", "day_definition": "local_standard_time",
               "interval_label": "start"}
    body = {k: v for k, v in spec_kw().items()}
    body["approximate_date_patterns"] = list(PATTERNS)
    cfg = config_from_dict({"stations": {SITE: station}, "event_tables": [body]})
    e = cfg.event_table("events")
    assert e.event_types["cutting"].management_kind == "cutting"
    assert e.site_windows[SITE]["end"] == "2024-12-31"
    with pytest.raises(ContractError, match="never shifted"):
        config_from_dict({"stations": {SITE: {**station, "timezone": "UTC-06:00"}},
                          "event_tables": [body]})
    with pytest.raises(ContractError, match="not declared"):
        config_from_dict({"stations": {}, "event_tables": [body]})
    with pytest.raises(ContractError, match="duplicate event table"):
        config_from_dict({"stations": {SITE: station}, "event_tables": [body, body]})
    with pytest.raises(ContractError, match="unknown configuration key"):
        config_from_dict({"stations": {SITE: station},
                          "event_tables": [{**body, "irrigation_from_et": True}]})


# --------------------------------------------------------------------------- management records

FARM = {"record": "farm records", "confirmed_by": "user", "confirmed_on": "2026-10-17",
        "event_ids": ["1", "3"]}


def test_management_record_label_is_declared_not_inferred():
    f = adapt(management_records=[FARM]).frame.set_index("event_id")
    assert f["management_record"].to_dict() == {"1": True, "2": False, "3": True, "4": False,
                                                "5": False}
    assert f.loc["1"].management_record_source == "farm records (confirmed by user, 2026-10-17)"
    # the logged source and its evidence class are unchanged
    assert (f.loc["1"].event_source, f.loc["1"].evidence_class) == (
        "Planet imagery", "remote_sensing_inference")
    assert not adapt().frame["management_record"].any()


@pytest.mark.parametrize("ids, match", [
    (["99"], "not kept events"),
    (["6"], "not kept events"),                 # outside the record
    (["2"], "non-management"),
])
def test_management_record_must_name_kept_management_events(ids, match):
    with pytest.raises(DataValidationError, match=match):
        adapt(management_records=[{**FARM, "event_ids": ids}])


@pytest.mark.parametrize("record, match", [
    ({**FARM, "record": None}, "record not declared"),
    ({**FARM, "confirmed_on": "yesterday"}, "ISO date"),
    ({**FARM, "event_ids": []}, "non-empty"),
    ({**FARM, "source": "x"}, "unknown configuration key"),
])
def test_management_record_declaration(record, match):
    with pytest.raises(ContractError, match=match):
        spec(management_records=[record])
    with pytest.raises(ContractError, match="two entries"):
        spec(management_records=[FARM, {**FARM, "event_ids": ["3"]}])


def test_event_analysis_and_anomaly_sections():
    station = {"timezone": "UTC-07:00", "day_definition": "local_standard_time",
               "interval_label": "start"}
    body = {**spec_kw(), "approximate_date_patterns": list(PATTERNS)}
    ea = {"event_analysis_id": "ev", "event_table": "events", "management_kinds": ["cutting"],
          "pre_days": 7, "post_offset_days": 1, "post_days": 7, "min_window_completeness": 0.7,
          "baseline_min_mm": 0.5, "recovery_fraction": 0.9, "recovery_max_days": 28,
          "recovery_smoothing_days": 3, "approximate_policy": "flag"}
    an = {"anomaly_id": "a", "metric_set": "m", "method": "exploratory_moving_mean",
          "min_days": 20, "window_days": 31, "min_window_completeness": 0.7}
    base = {"stations": {SITE: station}, "event_tables": [body],
            "metric_sets": [{"metric_set_id": "m", "daily_tolerance": {"mean": 0.01,
                                                                       "std": 0.001}}]}
    cfg = config_from_dict({**base, "event_analyses": [ea], "anomalies": [an]})
    assert cfg.event_analysis("ev").windows == {"pre": (-7, -1), "post": (1, 7),
                                                "recovery": (1, 28)}
    assert cfg.anomaly_spec("a").window_days == 31
    with pytest.raises(ContractError, match="event table 'nope' not declared"):
        config_from_dict({**base, "event_analyses": [{**ea, "event_table": "nope"}]})
    with pytest.raises(ContractError, match="metric set 'x' not declared"):
        config_from_dict({**base, "anomalies": [{**an, "metric_set": "x"}]})
    with pytest.raises(ContractError, match="comparisons"):
        config_from_dict({**base, "anomalies": [{**an, "comparisons": ["c"]}]})
