"""The user's station event log for US-UTJ (local only, never distributed; D-059)."""

from pathlib import Path

import pandas as pd
import pytest

from etcompare.adapters.crop_height import adapt_crop_height
from etcompare.adapters.event_log import adapt_event_table
from etcompare.config import load_config
from etcompare.provenance import file_sha256

ROOT = Path(__file__).parents[2]

CFG = load_config(ROOT / "examples" / "us_utj.yaml")
SPEC = CFG.event_table("utj_station_events")
CROP = CFG.crop_height_table("utj_zone_e_crop_height")
SHA = "ea3ca8916cdb2809f174774f4286e26eb9a856be9b6af8354d3b32ec12f34698"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(SPEC.path is None or not SPEC.path.exists(),
                       reason="station event log not present"),
]


@pytest.fixture(scope="module")
def events():
    return adapt_event_table(SPEC.path, SPEC, stations=CFG.stations)


def test_us_utj_event_log(events):
    assert file_sha256(SPEC.path) == SHA
    f = events.frame
    assert events.report.ok and len(f) == 17
    assert f["category"].value_counts().to_dict() == {"instrument": 11, "management": 6}
    cuts = f[f["category"] == "management"].set_index("start_date").sort_index()
    assert [d.date().isoformat() for d in cuts.index] == [
        "2024-06-11", "2024-07-19", "2024-09-08", "2025-05-28", "2025-07-05", "2025-09-07"]
    assert set(cuts["management_kind"]) == {"cutting"}           # never irrigation
    assert cuts["evidence_class"].to_dict() == {
        pd.Timestamp(d): c for d, c in [
            ("2024-06-11", "remote_sensing_inference"), ("2024-07-19", "remote_sensing_inference"),
            ("2024-09-08", "photo_observation"), ("2025-05-28", "photo_observation"),
            ("2025-07-05", "remote_sensing_inference"), ("2025-09-07", "photo_observation")]}
    assert cuts.index[cuts["approximate"]].tolist() == [pd.Timestamp("2024-09-08")]
    assert cuts["start_time"].isna().all()                        # date-only entries
    # The farm records confirm all six (user, 2026-10-17); sources stay as logged.
    assert set(cuts["event_source"]) == {"Planet imagery", "camera"}
    assert cuts["management_record"].all()
    assert not f.loc[f["category"] != "management", "management_record"].any()
    assert "management_record" not in set(f["evidence_class"])


def test_us_utj_instrument_events_are_overlaps_only(events):
    f = events.frame
    inst = f[f["category"] == "instrument"]
    assert set(inst["analysis_role"]) == {"overlap_only"}
    move = inst[inst["start_date"] == pd.Timestamp("2025-07-15")]
    assert len(move) == 1 and "9.9 ft" in move["event_notes"].iloc[0]
    assert move["start_time"].iloc[0] == "13:36:00"


def test_us_utj_exclusions(events):
    x = events.exclusions.to_frame()
    out = x[x["reason_code"] == "outside_station_record"]
    assert len(out) == 17                       # 4 cuttings of 2023 and 13 entries after the record
    assert out["detail"].str.startswith("cutting on 2023-").sum() == 4
    assert (x["reason_code"] == "station_not_declared").sum() == 16


@pytest.mark.skipif(CROP.path is None or not CROP.path.exists(),
                    reason="US-UTJ crop-height file not present")
def test_us_utj_cuttings_match_crop_height_resets(events):
    # A cutting resets height to the 5 cm base in one day (drops of 36-45 cm); the model's
    # end-of-season decline is ~0.4 cm/day and never ends a day at the base from far above.
    h = adapt_crop_height(CROP.path, CROP).frame.set_index("date")["height_cm"]
    resets = h.index[(h == 5.0) & (h.shift() - h > 1.0)]
    f = events.frame
    cuts = f.loc[f["category"] == "management", "start_date"].sort_values()
    assert list(resets) == list(cuts)
