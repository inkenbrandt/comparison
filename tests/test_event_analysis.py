"""Management-event responses (guide step 14; etcompare.events)."""

import numpy as np
import pandas as pd
import pytest

from etcompare.adapters.event_log import adapt_event_table
from etcompare.config import EventAnalysisSpec, StationSpec
from etcompare.errors import ContractError
from etcompare.events import analyse_events, recovery
from etcompare.schemas import CanonicalTable
from test_aggregation import SITE, build_cohort, cspec, ext, station
from test_events import spec as event_spec

STATIONS = {SITE: StationSpec(SITE, "UTC-07:00", "local_standard_time", "start")}
DAYS = pd.date_range("2024-06-01", "2024-08-31")
EVENT = pd.Timestamp("2024-07-01")
KINDS = {"cutting": {"category": "management", "management_kind": "cutting"},
         "irrigation cutoff": {"category": "management", "management_kind": "irrigation_cutoff"},
         "station visit": {"category": "instrument"},
         "clouds": {"category": "confounder"}}


def aspec(**over):
    kw = dict(event_analysis_id="ev", event_table="events",
              management_kinds=("cutting", "irrigation_cutoff"), pre_days=7, post_offset_days=1,
              post_days=7, min_window_completeness=0.7, baseline_min_mm=0.5,
              recovery_fraction=0.9, recovery_max_days=28, recovery_smoothing_days=3,
              approximate_policy="flag")
    kw.update(over)
    return EventAnalysisSpec(**kw)


def events(rows, **over):
    df = pd.DataFrame(rows, columns=["event_id", "stationid", "event_date", "observer",
                                     "event_type", "event_source", "event_notes"])
    kw = {"event_types": KINDS, "site_windows": {SITE: {"start": "2024-06-01",
                                                        "end": "2024-08-31"}}, **over}
    return adapt_event_table(df, event_spec(**kw), stations=STATIONS).table


def cutoff(eid="1", day="2024-07-01", kind="irrigation cutoff", source="camera", note=None):
    return (eid, SITE, f"{day} 00:00:00.000", "DM", kind, source, note)


def cohort(obs, mod, days=DAYS, filled=(), kind="model_available", models=("aaa",)):
    st = station(days, obs, filled=filled)
    frames = [ext(m, days, v) for m, v in zip(models, mod, strict=True)]
    return build_cohort(st, frames, cspec(kind=kind, models=models)), st


def one(result, series="observed", event="1", model="aaa"):
    f = result.responses.frame
    r = f[(f.series == series) & (f.event_id == event) & (f.model == model)]
    assert len(r) == 1
    return r.iloc[0]


def step(before, after, days=DAYS, at=EVENT):
    return np.where(days < at, before, after).astype(float)


# --------------------------------------------------------------------------- gate

def test_known_irrigation_cutoff_decrease():
    """Guide gate: a synthetic cutoff with a known ET decrease yields the expected change."""
    obs = step(5.0, 2.0)            # observed drops 3 mm/day on the cutoff day
    mod = step(4.0, 3.0)            # model drops 1 mm/day
    c, st = cohort(obs, [mod])
    r = analyse_events(c, events([cutoff()]), aspec(), station_daily=st)
    o, m, d = one(r), one(r, "modeled"), one(r, "modeled_minus_observed")
    assert o.status == "ok" and o.n_pre_days == 7 and o.n_post_days == 7
    assert o.baseline_mean_mm == pytest.approx(5.0) and o.post_mean_mm == pytest.approx(2.0)
    assert o.change_mm == pytest.approx(-3.0)
    assert o.relative_change_percent == pytest.approx(-60.0)
    assert o.cumulative_response_mm == pytest.approx(-21.0)          # 7 days x -3 mm
    assert m.change_mm == pytest.approx(-1.0)
    assert d.change_mm == pytest.approx(2.0)                         # modeled - observed
    assert o.recovery_status == "not_recovered_within_horizon"
    flags = set(o["flags"].split(";"))
    assert {"before_after_uncontrolled", "not_causal_evidence", "evidence_photo_observation",
            "no_management_record"} <= flags
    assert o.design == "before_after_uncontrolled"


def test_incomplete_baseline_is_flagged():
    gap = DAYS[(DAYS >= "2024-06-25") & (DAYS <= "2024-06-28")]   # 4 of 7 baseline days
    days = DAYS.difference(gap)
    c, st = cohort(step(5.0, 2.0, days), [step(5.0, 2.0, days)], days=days)
    o = one(analyse_events(c, events([cutoff()]), aspec()))
    assert (o.status, o.n_pre_days, o.n_pre_days_expected) == ("incomplete_baseline", 3, 7)
    assert pd.isna(o.baseline_mean_mm) and pd.isna(o.change_mm)
    assert o.recovery_status == "baseline_unavailable"


def test_incomplete_post_window_and_cumulative_need_all_days():
    days = DAYS.difference([pd.Timestamp("2024-07-03")])     # 6 of 7 post days
    c, _ = cohort(step(5.0, 2.0, days), [step(5.0, 2.0, days)], days=days)
    o = one(analyse_events(c, events([cutoff()]), aspec()))
    assert o.status == "ok" and o.change_mm == pytest.approx(-3.0)
    assert pd.isna(o.cumulative_response_mm) and "missing day" in o.status_detail
    days = DAYS.difference(pd.date_range("2024-07-02", "2024-07-05"))
    c, _ = cohort(step(5.0, 2.0, days), [step(5.0, 2.0, days)], days=days)
    assert one(analyse_events(c, events([cutoff()]), aspec())).status == "incomplete_post_window"


def test_overlapping_events_are_flagged_and_kept_visible():
    ev = events([cutoff(),
                 cutoff("2", "2024-07-10", kind="cutting"),
                 ("3", SITE, "2024-06-28 13:00:00.000", "PI", "station visit", None, "moved"),
                 ("4", SITE, "2024-07-03 00:00:00.000", "PCI", "clouds", "Sentinel NDVI", None)])
    c, _ = cohort(step(5.0, 2.0), [step(5.0, 2.0)])
    r = analyse_events(c, ev, aspec())
    o = one(r)
    assert set(o.overlapping_event_ids.split(";")) == {"2", "3", "4"}
    assert {"overlapping_management_event", "instrument_event_in_window",
            "confounder_event_in_window"} <= set(o["flags"].split(";"))
    ov = r.overlaps.frame
    ov1 = ov[ov.event_id == "1"].set_index("other_event_id")
    assert ov1.loc["3", "window"] == "pre" and ov1.loc["3", "other_analysis_role"] == "overlap_only"
    assert ov1.loc["4", "window"] == "post" and ov1.loc["2", "window"] == "recovery"
    # instrument and confounder events are never analysed as treatments
    assert set(r.responses.frame.event_id) == {"1", "2"}


def test_recovery_after_a_cutting():
    # ET 5 before, 1 on the post days, then back to 5 from day 10 on.
    obs = np.where(DAYS < EVENT, 5.0, np.where(DAYS < EVENT + pd.Timedelta(days=10), 1.0, 5.0))
    c, _ = cohort(obs, [obs])
    o = one(analyse_events(c, events([cutoff(kind="cutting")]), aspec()))
    # trailing 3-day mean first reaches 4.5 when all three days are 5: days 10, 11, 12
    assert (o.recovery_status, o.recovery_days) == ("recovered", 12)
    d = one(analyse_events(c, events([cutoff(kind="cutting")]), aspec()),
            "modeled_minus_observed")
    assert (d.recovery_status, d.recovery_days) == ("recovered", 0)


def test_recovery_statuses():
    sp = aspec()
    idx = pd.date_range("2024-07-02", "2024-07-20")
    v = pd.Series(1.0, index=idx)
    assert recovery(v, EVENT, 5.0, sp, None) == (None, "not_identifiable_gap")   # ends 07-20
    assert recovery(v, EVENT, 5.0, sp, pd.Timestamp("2024-07-08")) == (
        None, "interrupted_by_management_event")
    assert recovery(v, EVENT, None, sp, None) == (None, "baseline_unavailable")
    assert recovery(v, EVENT, 0.2, sp, None) == (None, "baseline_below_min")
    full = pd.Series(1.0, index=pd.date_range("2024-07-02", "2024-08-15"))
    assert recovery(full, EVENT, 5.0, sp, None) == (None, "not_recovered_within_horizon")


def test_gap_filled_days_and_weather_are_visible():
    filled = pd.date_range("2024-07-02", "2024-07-04")
    c, st = cohort(step(5.0, 2.0), [step(5.0, 2.0)], filled=filled)
    st = st.assign(src_ETr=step(10.0, 8.0), src_prcp=np.where(DAYS == "2024-07-02", 12.0, 0.0))
    sp = aspec(weather_variables={
        "etr": {"column": "src_ETr", "units": "mm/day", "aggregation": "mean"},
        "precip": {"column": "src_prcp", "units": "mm", "aggregation": "sum"},
        "missing": {"column": "src_nope", "units": "mm", "aggregation": "sum"}},
        normalize_by="etr")
    r = analyse_events(c, events([cutoff()]), sp, station_daily=st)
    o = one(r)
    assert o.n_post_days_reference_filled == 3
    assert {"includes_gap_filled_reference_days", "shared_gridmet_drivers"} <= set(
        o["flags"].split(";"))
    assert o.baseline_fraction == pytest.approx(0.5) and o.post_fraction == pytest.approx(0.25)
    assert o.fraction_change == pytest.approx(-0.25)
    cond = r.conditions.frame.set_index(["variable", "window"])["value"]
    assert cond[("etr", "pre")] == pytest.approx(10.0) and cond[("precip", "post")] == 12.0
    assert "weather_variable_unavailable" in r.report.codes()          # only that one skipped
    assert ("missing", "pre") not in cond.index


def test_approximate_policy():
    ev = events([cutoff(note="date approximate")])
    c, _ = cohort(step(5.0, 2.0), [step(5.0, 2.0)])
    assert "approximate_date" in one(analyse_events(c, ev, aspec()))["flags"]
    r = analyse_events(c, ev, aspec(approximate_policy="exclude"))
    assert not len(r.responses.frame) and "approximate_events_excluded" in r.report.codes()


def test_models_of_a_frozen_cohort_share_days():
    days = DAYS.difference([pd.Timestamp("2024-06-29")])
    c, _ = cohort(step(5.0, 2.0, days), [step(5.0, 2.0, days), step(4.0, 1.0, days)],
                  days=days, kind="common_date", models=("aaa", "bbb"))
    f = analyse_events(c, events([cutoff()]), aspec()).responses.frame
    assert set(f.n_pre_days) == {6}
    assert not f["flags"].str.contains("days_differ_across_models").any()


def test_events_must_come_from_the_event_log():
    c, _ = cohort(step(5.0, 2.0), [step(5.0, 2.0)])
    ev = events([cutoff()])
    forged = CanonicalTable(ev.schema, ev.frame, {})          # e.g. built from residuals
    with pytest.raises(ContractError, match="only from the station event log"):
        analyse_events(c, forged, aspec())
    with pytest.raises(ContractError, match="reads event table"):
        analyse_events(c, ev, aspec(event_table="other"))


@pytest.mark.parametrize("over, match", [
    ({"management_kinds": ()}, "management_kinds"),
    ({"management_kinds": ("mowing",)}, "management_kinds"),
    ({"pre_days": 0}, "pre_days"),
    ({"pre_days": None}, "pre_days not declared"),
    ({"min_window_completeness": 0}, r"\(0, 1\]"),
    ({"recovery_fraction": 1.5}, "recovery_fraction"),
    ({"baseline_min_mm": None}, "baseline_min_mm not declared"),
    ({"approximate_policy": "shift"}, "approximate_policy"),
    ({"recovery_max_days": 2}, "no complete smoothing window"),
    ({"normalize_by": "etr"}, "normalize_by"),
    ({"weather_variables": {"p": {"column": "c", "units": "mm", "aggregation": "max"}}},
     "aggregation"),
])
def test_declaration_refuses(over, match):
    with pytest.raises(ContractError, match=match):
        aspec(**over)
