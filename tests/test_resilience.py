"""Step 18: atomic outputs, retries, keep-awake, stall detection, checkpoints and
systematic-failure rules (extraction parts need the geo extra)."""

import json
import logging
import sys
import time

import numpy as np
import pandas as pd
import pytest

from etcompare.config import ProcessingSpec
from etcompare.errors import ContractError, InputMissingError, SystematicFailureError
from etcompare.io.atomic import atomic_path, write_csv_atomic, write_text_atomic
from etcompare.resilience import (
    Checkpoint,
    RetryPolicy,
    StallTimeoutError,
    StallWatchdog,
    call_with_retries,
    keep_awake,
)


class Events(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(getattr(record, "etcompare_fields", {}) or {})

    def named(self, event):
        return [r for r in self.records if r.get("event") == event]


@pytest.fixture
def events():
    h = Events()
    lg = logging.getLogger("etcompare")
    old = lg.level
    lg.addHandler(h)
    lg.setLevel(logging.DEBUG)
    yield h
    lg.removeHandler(h)
    lg.setLevel(old)


# --------------------------------------------------------------------------- atomic writes

def test_atomic_write_keeps_previous_file_on_failure(tmp_path):
    p = tmp_path / "t.csv"
    write_csv_atomic(pd.DataFrame({"a": [1, 2]}), p)
    before = p.read_bytes()

    class Boom(Exception):
        pass

    with pytest.raises(Boom), atomic_path(p) as tmp:
        tmp.write_text("a\n9\n")  # half a new file ...
        raise Boom  # ... then the disk fills up
    assert p.read_bytes() == before
    assert sorted(x.name for x in tmp_path.iterdir()) == ["t.csv"]  # no temp file left


def test_atomic_writers_match_plain_writers(tmp_path):
    f = pd.DataFrame({"x": [0.1, np.nan], "s": ["a", None]})
    f.to_csv(tmp_path / "plain.csv", index=False)
    write_csv_atomic(f, tmp_path / "atomic.csv")
    assert (tmp_path / "plain.csv").read_bytes() == (tmp_path / "atomic.csv").read_bytes()
    (tmp_path / "plain.txt").write_text("a\nb\n", encoding="utf-8")
    write_text_atomic("a\nb\n", tmp_path / "atomic.txt")
    assert (tmp_path / "plain.txt").read_bytes() == (tmp_path / "atomic.txt").read_bytes()


# --------------------------------------------------------------------------- retries

def test_transient_error_is_retried_and_logged(tmp_path, events):
    p = tmp_path / "r.tif"
    p.write_bytes(b"x")
    calls, waits = [], []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise OSError("network share timed out")
        return "ok"

    assert call_with_retries(flaky, p, policy=RetryPolicy(3, (1.0, 4.0)), what="t",
                             sleep=waits.append) == "ok"
    assert len(calls) == 3 and waits == [1.0, 4.0]
    retries = events.named("io_retry")
    assert [r["attempt"] for r in retries] == [1, 2] and retries[0]["path"] == str(p)


def test_retries_are_finite(tmp_path, events):
    p = tmp_path / "r.tif"
    p.write_bytes(b"x")

    def broken():
        raise OSError("corrupt")

    with pytest.raises(OSError, match="corrupt"):
        call_with_retries(broken, p, policy=RetryPolicy(3, (0.5,)), what="t",
                          sleep=lambda s: None)
    assert len(events.named("io_retry")) == 2 and len(events.named("io_failed")) == 1


def test_configuration_errors_and_missing_files_are_not_retried(tmp_path):
    calls = []

    def contract():
        calls.append(1)
        raise ContractError("scaling not declared")

    p = tmp_path / "r.tif"
    p.write_bytes(b"x")
    with pytest.raises(ContractError):
        call_with_retries(contract, p, policy=RetryPolicy(5, (1,)), what="t",
                          sleep=lambda s: None)
    assert len(calls) == 1

    def gone():
        calls.append(1)
        raise OSError("no such file")

    calls.clear()
    with pytest.raises(InputMissingError):
        call_with_retries(gone, tmp_path / "absent.tif", policy=RetryPolicy(5, (1,)),
                          what="t", sleep=lambda s: None)
    assert len(calls) == 1


def test_processing_section_is_validated():
    assert ProcessingSpec().io_attempts == 3
    for bad in ({"io_attempts": 0}, {"io_backoff_s": [-1]}, {"checkpoint": "day"},
                {"max_unreadable_fraction": 1.5}, {"stall_warning_s": 0},
                {"stall_timeout_s": 10, "stall_warning_s": 20}, {"keep_awake": "yes"}):
        with pytest.raises(ContractError):
            ProcessingSpec(**bad)


# --------------------------------------------------------------------------- awake / stall

def test_keep_awake(events):
    with keep_awake(False) as held:
        assert held is False
    with keep_awake(True) as held:
        assert held is (sys.platform == "win32")
    assert [r["requested"] for r in events.named("keep_awake")] == [False, True]


def test_stall_warning_and_suspend_detection(events):
    now = [1000.0]
    w = StallWatchdog(warning_s=10, interval_s=0.01, clock=lambda: now[0])
    with w:
        now[0] += 11  # not armed yet (no raster read): an analysis is never a stall
        time.sleep(0.1)
        assert w.events == []
        w.beat("first.tif")
        now[0] += 11  # 11 s without a beat, below the suspend threshold
        time.sleep(0.1)
        assert "io_stall" in w.events
        w.beat("next.tif")
        now[0] += 3600  # the clock jumps an hour between two ticks: the machine slept
        time.sleep(0.1)
    assert "system_suspended" in w.events
    assert w.events.count("io_stall") == 1  # the sleep is not counted as a stall
    assert events.named("system_suspended")[0]["gap_s"] > 3000


def test_stall_timeout_fails_the_run(events):
    called = []
    w = StallWatchdog(warning_s=0.05, timeout_s=0.1, interval_s=0.01,
                      on_timeout=called.append, grace_s=5)
    with pytest.raises(StallTimeoutError), w:
        w.beat("stuck.tif")
        for _ in range(500):  # a stalled loop that still runs Python code
            time.sleep(0.01)
    assert called and "stall_timeout_s" in called[0]
    assert "io_stall_timeout" in w.events


def test_stall_watch_is_disarmed_after_extraction(events):
    from etcompare.resilience import beat, rest

    w = StallWatchdog(warning_s=0.05, interval_s=0.01)
    with w:
        beat("a.tif")
        rest()  # the extraction finished; the analyses that follow read no raster
        time.sleep(0.2)
    assert "io_stall" not in w.events


# --------------------------------------------------------------------------- checkpoints

def test_checkpoint_needs_the_same_key_and_an_intact_file(tmp_path):
    a = Checkpoint(tmp_path, "raster_extraction:x", "k1")
    a.save({"n_done": 3}, through="2024-01-31", n_rows=7)
    assert Checkpoint(tmp_path, "raster_extraction:x", "k1").load() == {"n_done": 3}
    assert Checkpoint(tmp_path, "raster_extraction:x", "k2").load() is None
    a.state_path.write_bytes(a.state_path.read_bytes()[:-3])  # truncated by a crash
    assert a.load() is None
    rec = json.loads(a.record_path.read_text())
    assert rec["cache_key"] == "k1" and rec["through"] == "2024-01-31"
    Checkpoint(tmp_path, "raster_extraction:x", "k2").prune()
    assert not a.dir.exists()


# --------------------------------------------------------------------------- extraction

rasterio = pytest.importorskip("rasterio")
pyproj = pytest.importorskip("pyproj")
from affine import Affine  # noqa: E402

from etcompare.config import (  # noqa: E402
    FootprintSetSpec,
    ProductSpec,
    RasterExtractionSpec,
    StationSpec,
)
from etcompare.extraction import extract_rasters  # noqa: E402
from etcompare.io.manifest import discover_footprints, discover_openet_rasters  # noqa: E402
from etcompare.registry import ModelRegistry  # noqa: E402

CRS = "EPSG:32612"
X0, Y0 = 629000.0, 4127300.0
MODELS = ModelRegistry.default()
DAYS = pd.to_datetime(["2024-05-30", "2024-05-31", "2024-06-01", "2024-06-15", "2024-07-01",
                       "2024-07-02", "2024-08-20"])


def _write(path, arr, transform, nodata):
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", driver="GTiff", height=arr.shape[0], width=arr.shape[1],
                       count=1, dtype="float32", crs=CRS, transform=transform,
                       nodata=nodata) as ds:
        ds.write(arr.astype("float32"), 1)


@pytest.fixture
def scene(tmp_path):
    rng = np.random.default_rng(18)
    et_root, fp_root = tmp_path / "openet", tmp_path / "fp"
    for d in DAYS:
        for model in ("ssebop", "sims", "eemetric"):
            _write(et_root / model / f"US-XXX_{model}_et_{d:%Y_%m_%d}_abc12.tif",
                   1 + rng.random((10, 10)), Affine(30, 0, X0, 0, -30, Y0), -9999)
        w = rng.random((9, 9))
        _write(fp_root / f"ffp_US-XXX_daily_etw_{d:%Y%m%d}.tif", w / w.sum(),
               Affine(10, 0, X0 + 60, 0, -10, Y0 - 60), 0.0)
    lon, lat = pyproj.Transformer.from_crs(CRS, "EPSG:4326", always_xy=True).transform(
        X0 + 105, Y0 - 105)
    station = StationSpec("US-XXX", timezone="UTC-07:00", day_definition="local_standard_time",
                          interval_label="start", latitude=lat, longitude=lon)
    products = {m: ProductSpec(m, units="mm/day", day_definition="unknown",
                               temporal_support="interpolated_daily", raster_scaling="declared",
                               scale_factor=1.0, add_offset=0.0, nodata=-9999.0)
                for m in MODELS.models}
    fset = FootprintSetSpec("SYN", fp_root, weighting_method="synthetic",
                            value_semantics="normalized_cell_weight")
    spec = RasterExtractionSpec("syn", "US-XXX", methods=(
        "footprint", "footprint_common_support", "pixel_window", "nearest_tower_pixel"),
        footprint_set_id="SYN", window_sizes=(3,))

    def manifest():
        return discover_openet_rasters(et_root, models=MODELS, products=products).manifest

    def run(model_manifest=None, **kw):
        return extract_rasters(
            spec, station=station, products=products, footprint_set=fset,
            model_manifest=manifest() if model_manifest is None else model_manifest,
            footprint_manifest=discover_footprints(fset).manifest, **kw)

    return {"run": run, "manifest": manifest, "et_root": et_root, "fp_root": fp_root}


def _same(a, b):
    pd.testing.assert_frame_equal(a.table.frame, b.table.frame)
    assert a.table.attributes == b.table.attributes
    assert json.dumps(a.table.attributes, default=str) == json.dumps(b.table.attributes,
                                                                     default=str)


def test_corrupt_raster_is_skipped_counted_and_kept_null(scene):
    clean = scene["run"]()
    bad = scene["et_root"] / "sims" / f"US-XXX_sims_et_{DAYS[2]:%Y_%m_%d}_abc12.tif"
    bad.write_bytes(b"not a tiff")
    r = scene["run"](retry=RetryPolicy(2, (0.0,)), max_unreadable_fraction=0.5)
    ex = r.exclusions.to_frame()
    assert list(ex.loc[ex.reason_code == "unreadable_raster", "input_key"]) == [str(bad)]
    f = r.table.frame
    day = f[(f.model == "sims") & (f.date == DAYS[2])]
    assert len(day) == 4 and day.et_mm.isna().all()  # every method, null: visible in coverage
    assert r.table.attributes["counts"]["unreadable_raster"] == 1
    # Every other model-day is unchanged except common support on that day (one fewer model).
    key = ["model", "date", "extraction_method"]
    m = clean.table.frame.merge(f, on=key, suffixes=("", "_new"))
    other = m[~((m.date == DAYS[2]) & ((m.model == "sims")
                                      | (m.extraction_method.str.contains("common"))))]
    assert np.array_equal(other.et_mm.to_numpy(), other.et_mm_new.to_numpy(), equal_nan=True)
    with pytest.raises(rasterio.errors.RasterioIOError):
        scene["run"](strict=True)


def test_missing_raster_has_its_own_code(scene):
    indexed = scene["manifest"]()  # indexed while the file still existed
    (scene["et_root"] / "ssebop" / f"US-XXX_ssebop_et_{DAYS[0]:%Y_%m_%d}_abc12.tif").unlink()
    r = scene["run"](indexed, retry=RetryPolicy(3, (0.0,)))
    codes = set(r.exclusions.to_frame().reason_code)
    assert "raster_missing" in codes and "unreadable_raster" not in codes
    with pytest.raises(InputMissingError):  # a FileNotFoundError: exit code 2
        scene["run"](indexed, strict=True)


def test_every_raster_of_one_model_unreadable_is_systematic(scene):
    for p in (scene["et_root"] / "eemetric").glob("*.tif"):
        p.write_bytes(b"garbage")
    with pytest.raises(SystematicFailureError, match="every eemetric raster") as e:
        scene["run"](retry=RetryPolicy(1, ()))
    assert e.value.details["files_failed"] == {"eemetric": len(DAYS)}
    assert len(e.value.exclusions.to_frame()) >= len(DAYS)


def test_unreadable_fraction_above_threshold_is_systematic(scene):
    bad = scene["et_root"] / "ssebop" / f"US-XXX_ssebop_et_{DAYS[0]:%Y_%m_%d}_abc12.tif"
    bad.write_bytes(b"garbage")
    scene["run"](max_unreadable_fraction=0.1)  # 1 of 21 files: skipped and counted
    with pytest.raises(SystematicFailureError, match="max_unreadable_fraction"):
        scene["run"](max_unreadable_fraction=0.01)


def test_every_footprint_unreadable_is_systematic(scene):
    for p in scene["fp_root"].glob("*.tif"):
        p.write_bytes(b"garbage")
    with pytest.raises(SystematicFailureError, match="footprints"):
        scene["run"]()


def test_interrupted_extraction_resumes_to_identical_output(scene, tmp_path, monkeypatch):
    clean = scene["run"]()
    ck = Checkpoint(tmp_path / "ck", "raster_extraction:syn", "key-1")

    from etcompare import extraction

    real = extraction.read_model_window
    stop_at = DAYS[4]  # first July day: May and June are checkpointed

    def dies(path, *a, **kw):
        if f"{stop_at:%Y_%m_%d}" in str(path):
            raise KeyboardInterrupt  # the process is killed mid-extraction
        return real(path, *a, **kw)

    monkeypatch.setattr(extraction, "read_model_window", dies)
    with pytest.raises(KeyboardInterrupt):
        scene["run"](checkpoint=ck)
    rec = json.loads(ck.record_path.read_text())
    assert rec["through"] == "2024-06-15"
    monkeypatch.setattr(extraction, "read_model_window", real)

    opened = []
    monkeypatch.setattr(extraction, "read_model_window",
                        lambda p, *a, **kw: opened.append(p) or real(p, *a, **kw))
    resumed = scene["run"](checkpoint=ck)
    _same(resumed, clean)
    assert str(resumed.report.summary()) == str(clean.report.summary())
    assert not any(f"{d:%Y_%m_%d}" in p for p in opened for d in DAYS[:4])  # not reread

    # A checkpoint written under another key is never used.
    other = Checkpoint(tmp_path / "ck", "raster_extraction:syn", "key-2")
    assert other.load() is None
