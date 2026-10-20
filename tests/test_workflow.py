"""Step 17: workflow units, statuses and cache invalidation (guide gate).

A change to one input, mask, grid or configuration entry must invalidate exactly the units
that read it, and nothing else.
"""

import os
import time
from pathlib import Path

import pandas as pd
import pytest
import yaml

from etcompare.config import load_config
from etcompare.demo import write_demo
from etcompare.errors import ContractError
from etcompare.runner import run_stages
from etcompare.workflow import Workflow

SMALL = """
x-station: &station {timezone: "UTC-07:00", day_definition: local_standard_time, interval_label: start}
stations:
  XX-SY1: *station
  XX-SY2: *station
x-product: &product {units: mm/day, day_definition: unknown, temporal_support: interpolated_daily}
products: {alpha: *product, bravo: *product, charlie: *product, delta: *product, echo: *product, ens: {<<: *product, is_ensemble: true}}
x-table: &table
  date_column: date
  reference_columns: {et_raw: ET, et_corrected: ET_corr}
  gap_flag_column: ET_gap
  closure_factor_column: ebc_cf
  units: {ET: mm/day, ET_corr: mm/day}
station_tables:
  - {<<: *table, site_id: XX-SY1, path: data/XX-SY1_daily.csv}
  - {<<: *table, site_id: XX-SY2, path: data/XX-SY2_daily.csv}
extracted_tables:
  - name: ext
    path: data/synthetic_extractions.parquet
    site_column: station_id
    model_column: model
    date_column: footprint_date
    value_columns: {ET_fp: upstream_footprint_weighted}
    units: {ET_fp: mm/day}
    footprint_set_id: fp
    footprint_method: synthetic
    retained_weight_column: valid_weight_fraction
    retained_weight_denominator: normalized_truncated_footprint
footprint_sets:
  - {set_id: fp, root: data/footprints, weighting_method: synthetic, temporal_weighting: reference_et_weighted, value_semantics: normalized_cell_weight}
x-side: &side {extraction_method: upstream_footprint_weighted, footprint_set_id: fp}
comparisons:
  - {comparison_id: c1, kind: common_date, models: [alpha, bravo, ens], reference_variants: [et_corrected_measured], sides: [*side], sources: [ext], sites: [XX-SY1]}
  - {comparison_id: c2, kind: common_date, models: [alpha, bravo, ens], reference_variants: [et_corrected_measured], sides: [*side], sources: [ext], sites: [XX-SY2]}
period_aggregations:
  - {aggregation_id: p, periods: [month]}
metric_sets:
  - {metric_set_id: m1, daily_tolerance: {mean: 0.01, std: 0.001}, period_tolerance: {mean: 0.1, std: 0.01}, period_aggregations: [p], period_products: [partial_sum, complete_total], comparisons: [c1]}
  - {metric_set_id: m2, daily_tolerance: {mean: 0.01, std: 0.001}, period_tolerance: {mean: 0.1, std: 0.01}, period_aggregations: [p], period_products: [partial_sum, complete_total], comparisons: [c2]}
report: {synthetic: true, figure_formats: [png]}
output_dir: outputs
"""


@pytest.fixture(scope="module")
def demo_data(tmp_path_factory):
    root = tmp_path_factory.mktemp("demo_data")
    write_demo(root)
    return root


def make(tmp_path, demo_data, edit=None) -> Path:
    """A small config over the synthetic data in its own directory."""
    data = tmp_path / "data"
    if not data.exists():
        data.mkdir()
        for f in (demo_data / "data").iterdir():
            (data / f.name).write_bytes(f.read_bytes())
    d = yaml.safe_load(SMALL)
    if edit:
        edit(d)
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(d, sort_keys=False), encoding="utf-8")
    return p


def statuses(cfg_path, stages=("analyze",)):
    wf = Workflow(load_config(cfg_path), run_id="probe")
    recs = []
    for s in stages:
        recs += wf.status_of(wf.plan(s))
    return {r.unit_id: r.status for r in recs}


def run(cfg_path, stages=("analyze",), **kw):
    return run_stages(load_config(cfg_path), list(stages), command="test", **kw)


def declared(recs):
    return [r for r in recs if r.reason_code != "not_declared"]


def bump(path: Path) -> None:
    """Rewrite a file with one changed byte and a later mtime."""
    time.sleep(0.01)
    text = path.read_text(encoding="utf-8")
    path.write_text(text + "\n", encoding="utf-8")
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))


# --------------------------------------------------------------------------- caching

def test_second_run_reuses_every_unit(tmp_path, demo_data):
    cfg = make(tmp_path, demo_data)
    _, first = run(cfg)
    first = declared(first)
    assert {r.status for r in first} == {"completed"}
    out = tmp_path / "outputs" / "cohorts" / "c1__m1_metrics.parquet"
    mtime = out.stat().st_mtime_ns
    _, second = run(cfg)
    second = declared(second)
    assert {r.status for r in second} == {"cached"}
    assert out.stat().st_mtime_ns == mtime
    assert {r.unit_id for r in first} == {r.unit_id for r in second}


def test_station_change_invalidates_only_that_station(tmp_path, demo_data):
    cfg = make(tmp_path, demo_data)
    run(cfg)
    bump(tmp_path / "data" / "XX-SY2_daily.csv")
    st = statuses(cfg)
    assert {k for k, v in st.items() if v == "stale"} == {
        "cohorts:c2", "periods:c2__p", "metrics:c2__m2"}
    assert st["cohorts:c1"] == st["metrics:c1__m1"] == "completed"
    _, recs = run(cfg)
    assert {r.unit_id for r in recs if r.status == "completed"} == {
        "cohorts:c2", "periods:c2__p", "metrics:c2__m2"}


def test_config_entry_change_invalidates_only_its_dependents(tmp_path, demo_data):
    cfg = make(tmp_path, demo_data)
    run(cfg)

    def tol(d):
        d["metric_sets"][1]["daily_tolerance"] = {"mean": 0.02, "std": 0.001}

    make(tmp_path, demo_data, tol)
    st = statuses(cfg)
    assert {k for k, v in st.items() if v == "stale"} == {"metrics:c2__m2"}


def test_mask_change_invalidates_only_that_comparison(tmp_path, demo_data):
    cfg = make(tmp_path, demo_data)
    run(cfg)

    def mask(d):
        d["comparisons"][0]["min_retained_frac"] = 0.95

    make(tmp_path, demo_data, mask)
    st = statuses(cfg)
    assert {k for k, v in st.items() if v == "stale"} == {
        "cohorts:c1", "periods:c1__p", "metrics:c1__m1"}


def test_modified_output_invalidates_its_unit(tmp_path, demo_data):
    cfg = make(tmp_path, demo_data)
    run(cfg)
    bump(tmp_path / "outputs" / "cohorts" / "c1__m1_metrics_wide.csv")
    st = statuses(cfg)
    assert {k for k, v in st.items() if v == "stale"} == {"metrics:c1__m1"}


def test_force_rebuilds(tmp_path, demo_data):
    cfg = make(tmp_path, demo_data)
    run(cfg)
    _, recs = run(cfg, force=True)
    assert {r.status for r in declared(recs)} == {"completed"}


def touch(root: Path, *names: str) -> list[Path]:
    out = []
    for n in names:
        p = root / n
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * 10)
        out.append(p)
    return out


def test_raster_change_invalidates_only_the_extraction_that_reads_it(tmp_path, demo_data):
    rasters = tmp_path / "rasters"
    sy1, sy2, late = touch(rasters, "XX-SY1_alpha_et_2021_06_01_00aa1.tif",
                           "XX-SY2_alpha_et_2021_06_01_00aa2.tif",
                           "XX-SY1_alpha_et_2023_06_01_00aa3.tif")

    def ext(d):
        d["openet_rasters"] = [{"root": "rasters"}]
        d["stations"]["XX-SY1"].update(latitude=40.0, longitude=-110.0)
        d["raster_extractions"] = [
            {"name": "x1", "site_id": "XX-SY1", "methods": ["pixel_window"],
             "start": "2021-01-01", "end": "2022-12-31"},
            {"name": "x2", "site_id": "XX-SY2", "methods": ["nearest_tower_pixel"]}]

    cfg = make(tmp_path, demo_data, ext)

    def keys():
        wf = Workflow(load_config(cfg), run_id="k")
        return {u.unit_id: wf.unit_key(u) for u in wf.plan("extract")}

    k0 = keys()
    bump(sy2)
    k1 = keys()
    assert k1["raster_extraction:x1"] == k0["raster_extraction:x1"]
    assert k1["raster_extraction:x2"] != k0["raster_extraction:x2"]
    bump(late)                       # outside x1's declared window: x1 never reads it
    assert keys()["raster_extraction:x1"] == k1["raster_extraction:x1"]
    bump(sy1)                        # a new grid or values on x1's raster
    assert keys()["raster_extraction:x1"] != k1["raster_extraction:x1"]


def test_sha256_identity_ignores_mtime_but_not_content(tmp_path, demo_data):
    rasters = tmp_path / "rasters"
    (sy1,) = touch(rasters, "XX-SY1_alpha_et_2021_06_01_00aa1.tif")

    def ext(d):
        d["openet_rasters"] = [{"root": "rasters"}]
        d["cache"] = {"raster_identity": "sha256"}
        d["raster_extractions"] = [{"name": "x1", "site_id": "XX-SY1",
                                    "methods": ["pixel_window"]}]

    cfg = make(tmp_path, demo_data, ext)

    def key():
        wf = Workflow(load_config(cfg), run_id="k")
        return wf.unit_key(wf.plan("extract")[0])

    k0 = key()
    st = sy1.stat()
    os.utime(sy1, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
    assert key() == k0
    sy1.write_bytes(b"y" * 10)
    assert key() != k0


# --------------------------------------------------------------------------- statuses

def test_run_skip_and_dependency_skip(tmp_path, demo_data):
    def skip(d):
        d["run"] = {"skip": ["cohorts"]}

    cfg = make(tmp_path, demo_data, skip)
    _, recs = run(cfg)
    by = {r.unit_id: r for r in recs}
    assert by["cohorts:c1"].status == "skipped"
    assert by["cohorts:c1"].reason_code == "run_skip"
    assert by["metrics:c1__m1"].status == "skipped"
    assert by["metrics:c1__m1"].reason_code == "dependency_skipped"
    assert not (tmp_path / "outputs" / "cohorts").exists()


def test_missing_input_is_not_available_not_an_error(tmp_path, demo_data):
    def missing(d):
        d["extracted_tables"][0]["path"] = "data/absent.parquet"

    cfg = make(tmp_path, demo_data, missing)
    _, recs = run(cfg)
    by = {r.unit_id: r for r in recs}
    assert by["cohorts:c1"].status == "not_available"
    assert by["cohorts:c1"].reason_code == "input_missing"
    assert by["metrics:c1__m1"].status == "not_available"
    assert by["metrics:c1__m1"].reason_code == "dependency_unavailable"


def test_undeclared_analyses_are_recorded(tmp_path, demo_data):
    cfg = make(tmp_path, demo_data)
    _, recs = run(cfg, stages=("index", "extract", "analyze"))
    by = {r.unit_id: r for r in recs}
    for a in ("representativeness", "ensembles", "raster_extraction", "bootstrap"):
        assert by[a].status == "skipped" and by[a].reason_code == "not_declared"
    assert by["index:footprints_fp"].status == "not_available"


def test_failed_unit_is_recorded_then_raised(tmp_path, demo_data, monkeypatch):
    cfg = make(tmp_path, demo_data)
    import etcompare.metrics as metrics

    def boom(*a, **k):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(metrics, "score_comparison", boom)
    with pytest.raises(RuntimeError, match="synthetic failure"):
        run(cfg)
    runs = sorted((tmp_path / "outputs" / "runs").iterdir())
    st = pd.read_csv(runs[-1] / "analysis_status.csv")
    assert st.loc[st["unit_id"] == "metrics:c1__m1", "status"].item() == "failed"
    import json

    m = json.loads((runs[-1] / "run_manifest.json").read_text(encoding="utf-8"))
    assert m["status"] == "failed" and "synthetic failure" in m["error"]


def test_output_dir_is_required(tmp_path, demo_data):
    def no_out(d):
        del d["output_dir"]

    cfg = make(tmp_path, demo_data, no_out)
    with pytest.raises(ContractError, match="output_dir"):
        Workflow(load_config(cfg), run_id="x")


def test_validate_writes_station_exclusions(tmp_path, demo_data):
    cfg = make(tmp_path, demo_data)
    _, recs = run(cfg, stages=("validate",))
    assert recs[0].status == "completed"
    v = tmp_path / "outputs" / "validation"
    assert (v / "validation_issues.csv").is_file()
    assert (v / "input_exclusions.csv").is_file()


def test_crop_height_missing_policy(tmp_path, demo_data):
    def crop(policy):
        def edit(d):
            d["crop_height_tables"] = [{
                "name": "ch", "path": "data/absent.csv", "site_id": "XX-SY1",
                "date_column": "date", "height_column": "h", "height_units": "cm",
                "season_start_column": "s", "season_end_column": "e", "on_missing": policy}]
            d["period_aggregations"][0].update(periods=["month", "growing_season"],
                                               growing_season_source="ch")
        return edit

    cfg = make(tmp_path, demo_data, crop("error"))
    with pytest.raises(ContractError, match="crop-height table"):
        run(cfg, stages=("validate",))
    cfg = make(tmp_path, demo_data, crop("skip_growing_season"))
    _, recs = run(cfg)
    by = {r.unit_id: r for r in recs}
    assert by["periods:c1__p"].status == "completed"
    assert "growing_season periods skipped" in by["periods:c1__p"].details["notes"][0]
    t = pd.read_csv(tmp_path / "outputs" / "cohorts" / "c1__p_periods.csv")
    assert set(t["period_type"]) == {"month"}
