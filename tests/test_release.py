"""Step 18 final gate on the synthetic raster demonstration (needs geo and plot extras).

A fresh user, offline and without credentials, can generate and run the full demonstration,
inspect its paired data and exclusions, reproduce its metrics and get the report. Also:
a corrupt raster is skipped and counted, a systematic failure stops the run, and an
interrupted extraction resumes to identical outputs.
"""

import json
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("rasterio")
pytest.importorskip("matplotlib")

from etcompare.cli import main  # noqa: E402
from etcompare.config import load_config  # noqa: E402
from etcompare.demo import write_demo  # noqa: E402
from etcompare.io.tables import read_table  # noqa: E402
from etcompare.runrecord import RunManifest  # noqa: E402
from etcompare.schemas import data_dictionary  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    d = tmp_path_factory.mktemp("demo")
    assert main(["demo", str(d), "--rasters"]) == 0
    assert main(["run", str(d / "config.yaml"), "--log-level", "WARNING"]) == 0
    return d


def latest_status(out: Path) -> pd.DataFrame:
    run = sorted((out / "runs").iterdir())[-1]
    return pd.read_csv(run / "analysis_status.csv")


def test_full_demo_runs_offline_end_to_end(demo):
    out = demo / "outputs"
    st = latest_status(out)
    by = dict(zip(st.unit_id, st.status, strict=True))
    for unit in ("index:openet_0", "index:footprints_synthetic_fp", "raster_extraction:rx_sy1",
                 "raster_extraction:rx_sy2", "raster_extraction:rx_sy3", "report"):
        assert by[unit] == "completed", unit
    assert any(u.startswith("representativeness:") and s == "completed" for u, s in by.items())
    assert set(st.status) <= {"completed", "skipped"}
    assert set(st.loc[st.status == "skipped", "reason_code"]) == {"not_declared"}
    m = RunManifest.read(out / "run_manifest.json")
    assert m.status == "completed" and m.seeds and m.raster_grids and m.inputs
    html = (out / "report" / "report.html").read_text(encoding="utf-8")
    assert "SYNTHETIC" in html.upper()
    # Paired data and exclusions are inspectable; raster-based comparison has rows.
    raster_pairs = read_table(out / "cohorts" / "syn_raster_fp_vs_window_paired.parquet").frame
    assert set(raster_pairs.extraction_method) == {"footprint_conservative", "pixel_window_3x3"}
    ex = pd.read_csv(out / "rx_sy1_exclusions.csv")
    assert {"stage", "input_key", "reason_code"} <= set(ex.columns)
    # A second run reuses every unit.
    assert main(["run", str(demo / "config.yaml"), "--log-level", "WARNING"]) == 0
    again = latest_status(out)
    assert set(again.status) <= {"cached", "skipped"}


def test_metrics_reproduce_from_paired_rows(demo):
    cohorts = demo / "outputs" / "cohorts"
    n = 0
    for mp in cohorts.glob("*__syn_metrics_metrics.parquet"):
        cid = mp.name.split("__")[0]
        paired = read_table(cohorts / f"{cid}_paired.parquet").frame
        met = read_table(mp).frame
        met = met[(met["product"] == "daily_pairs") & met.metric.isin(["mae", "rmse", "mbe"])
                  & (met.scope_type == "site")]
        for r in met.itertuples():
            p = paired[(paired.model == r.model) & (paired.site_id == r.scope)
                       & (paired.reference_variant == r.reference_variant)
                       & (paired.extraction_method == r.extraction_method)
                       & (paired.cohort_id == r.cohort_id)]
            e = (p.modeled_mm - p.observed_mm).to_numpy()
            want = {"mae": np.abs(e).mean(), "rmse": np.sqrt((e ** 2).mean()),
                    "mbe": e.mean()}[r.metric]
            assert len(p) == r.n_days
            assert r.value == pytest.approx(want, rel=1e-12, abs=1e-12), (cid, r)
            n += 1
    assert n > 300


def _copy(demo, tmp_path):
    d = tmp_path / "demo"
    shutil.copytree(demo / "data", d / "data")
    text = (demo / "config.yaml").read_text(encoding="utf-8")
    assert text.count("io_backoff_s: [1, 4]") == 1
    # Retries still happen (and are logged); the test just does not wait for them.
    (d / "config.yaml").write_text(text.replace("io_backoff_s: [1, 4]", "io_backoff_s: [0]"),
                                   encoding="utf-8")
    return d


def test_corrupt_raster_is_skipped_counted_and_reported(demo, tmp_path):
    d = _copy(demo, tmp_path)
    bad = sorted((d / "data" / "openet" / "bravo").glob("XX-SY2_*2022_06_10*.tif"))[0]
    bad.write_bytes(b"corrupted on disk")
    assert main(["index", str(d / "config.yaml"), "--log-level", "ERROR"]) == 0
    assert main(["extract", str(d / "config.yaml"), "--log-level", "ERROR"]) == 0
    assert main(["report", str(d / "config.yaml"), "--log-level", "ERROR"]) == 0
    out = d / "outputs"
    ex = pd.read_csv(out / "rx_sy2_exclusions.csv")
    assert list(ex.loc[ex.reason_code == "unreadable_raster", "input_key"]) == [str(bad)]
    rows = read_table(out / "rx_sy2_extractions.parquet").frame
    day = rows[(rows.model == "bravo") & (rows.date == pd.Timestamp("2022-06-10"))]
    assert len(day) == 4 and day.et_mm.isna().all()  # visible in coverage as null
    m = RunManifest.read(out / "run_manifest.json")
    skipped = pd.read_csv(out / "report" / "skipped_analyses.csv")
    files = skipped[skipped.status == "files_skipped"]
    assert list(files.reason_code) == ["unreadable_raster"] and list(files.n_rows) == [1]
    run = sorted((out / "runs").iterdir())
    logs = [json.loads(x) for p in run for x in (p / "log.jsonl").read_text().splitlines()]
    assert any(x.get("event") == "io_retry" and x.get("path") == str(bad) for x in logs)
    assert m.status == "completed"
    extract_manifest = [RunManifest.read(p / "run_manifest.json") for p in run
                        if RunManifest.read(p / "run_manifest.json").command.split()[1]
                        == "extract"][0]
    counts = {(c["unit_id"], c["reason_code"]): c["n"]
              for c in extract_manifest.exclusion_counts}
    assert counts[("rx_sy2", "unreadable_raster")] == 1


def test_systematic_failure_stops_the_run(demo, tmp_path):
    d = _copy(demo, tmp_path)
    for p in (d / "data" / "openet" / "charlie").glob("XX-SY1_*.tif"):
        p.write_bytes(b"wrong format")
    assert main(["index", str(d / "config.yaml"), "--log-level", "ERROR"]) == 0
    assert main(["extract", str(d / "config.yaml"), "--log-level", "ERROR"]) == 2
    out = d / "outputs"
    m = RunManifest.read(out / "run_manifest.json")
    assert m.status == "failed" and "SystematicFailureError" in m.error
    assert "every charlie raster" in m.error
    assert not (out / "rx_sy1_extractions.parquet").exists()  # nothing half-done to reuse
    ex = pd.read_csv(out / "rx_sy1_exclusions.csv")
    assert (ex.reason_code == "unreadable_raster").sum() > 50
    failed = [a for a in m.analyses if a["status"] == "failed"]
    assert [a["unit_id"] for a in failed] == ["raster_extraction:rx_sy1"]
    assert any(c["reason_code"] == "unreadable_raster" for c in m.exclusion_counts)


def test_interrupted_extraction_resumes_to_identical_outputs(demo, tmp_path, monkeypatch):
    d = _copy(demo, tmp_path)
    assert main(["index", str(d / "config.yaml"), "--log-level", "ERROR"]) == 0
    from etcompare import extraction

    real = extraction.read_model_window

    def killed(path, *a, **kw):
        if "XX-SY1" in str(path) and "2022_07_0" in str(path):
            raise KeyboardInterrupt  # the process dies in July
        return real(path, *a, **kw)

    monkeypatch.setattr(extraction, "read_model_window", killed)
    with pytest.raises(KeyboardInterrupt):
        main(["extract", str(d / "config.yaml"), "--log-level", "ERROR"])
    out = d / "outputs"
    assert not (out / "rx_sy1_extractions.parquet").exists()
    assert RunManifest.read(out / "run_manifest.json").status == "failed"
    ck = list((out / ".etcompare" / "checkpoints").rglob("state.json"))
    assert len(ck) == 1 and json.loads(ck[0].read_text())["through"] == "2022-06-30"

    reread = []
    monkeypatch.setattr(extraction, "read_model_window",
                        lambda p, *a, **kw: reread.append(str(p)) or real(p, *a, **kw))
    assert main(["extract", str(d / "config.yaml"), "--log-level", "ERROR"]) == 0
    # May came from the checkpoint, not from the rasters.
    assert not any("XX-SY1" in p and "2022_05_" in p for p in reread)
    assert not (out / ".etcompare" / "checkpoints" / "raster_extraction_rx_sy1").exists()
    for name in ("rx_sy1", "rx_sy2", "rx_sy3"):
        a = read_table(out / f"{name}_extractions.parquet")
        b = read_table(demo / "outputs" / f"{name}_extractions.parquet")
        pd.testing.assert_frame_equal(a.frame, b.frame.assign(
            source_path=b.frame.source_path.str.replace(str(demo), str(d), regex=False)))
        att_a, att_b = dict(a.attributes), dict(b.attributes)
        for k in ("station", "footprint_set"):
            att_a.pop(k), att_b.pop(k)  # contain the absolute demo folder
        assert att_a == att_b, name


def test_tables_demo_is_unchanged_by_the_raster_option(tmp_path):
    a, b = write_demo(tmp_path / "a"), write_demo(tmp_path / "b", rasters=True)
    for f in ("XX-SY1_daily.csv", "synthetic_extractions.parquet", "synthetic_events.csv"):
        assert (a.parent / "data" / f).read_bytes() == (b.parent / "data" / f).read_bytes()
    assert load_config(a).raster_extractions == ()
    assert len(load_config(b).raster_extractions) == 3


def test_invalid_yaml_is_a_configuration_error(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("stations: [unclosed\n", encoding="utf-8")
    assert main(["validate", str(p)]) == 2


def test_data_dictionary_is_current():
    assert (ROOT / "docs" / "data_dictionary.md").read_text(encoding="utf-8") == data_dictionary()


def test_limitations_list_the_known_gaps():
    text = (ROOT / "docs" / "limitations.md").read_text(encoding="utf-8")
    for gap in ("Pixel-level ensembles", "Hierarchical intervals", "Polygon area-weighted",
                "Google Drive for desktop keeps modification times", "Staging",
                "Concurrency", "Modified KGE"):
        assert gap in text, gap
