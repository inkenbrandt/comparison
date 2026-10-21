"""The real-station example configurations (examples/us_utj.yaml, utah_stations.yaml).

They name real stations and local paths, so they and these tests stay out of the source
distribution (D-014); they run from a repository checkout.
"""

from pathlib import Path

import pytest
import yaml

from etcompare.config import ContractError, RasterExtractionSpec, load_config  # noqa: F401

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
EXAMPLE = EXAMPLES / "us_utj.yaml"
pytestmark = pytest.mark.skipif(not EXAMPLE.exists(), reason="example configs not present")


def test_load_example_config():
    cfg = load_config(EXAMPLE)
    assert set(cfg.products) == {"disalexi", "eemetric", "ensemble", "geesebal", "ptjpl",
                                 "sims", "ssebop"}
    assert cfg.model_registry().ensembles == {"ensemble"}
    (st,) = cfg.station_tables
    assert st.path.name == "US-UTJ_daily.csv" and st.path.is_absolute()
    (ex,) = cfg.extracted_tables
    assert ex.value_columns["ET_mean"] == "source_region_mean"
    assert cfg.footprint_sets[0].recursive is False


def test_example_raster_extraction_declared():
    cfg = load_config(EXAMPLE)
    x, m, nldas, le = cfg.raster_extractions
    assert x.footprint_set_id == "US-UTJ_set_B_etw_20261012"
    fs = cfg.footprint_set(x.footprint_set_id)
    assert fs.domain_capture_column == "et_weighted_coverage_pct"
    assert fs.temporal_weighting == "reference_et_weighted" and fs.target_dependence == "no"
    assert cfg.products["sims"].nodata == -9999.0
    mean = cfg.footprint_set(m.footprint_set_id)
    assert mean.temporal_weighting == "time_mean" and mean.target_dependence == "no"
    # Primary NLDAS-ETo set and measured-LE sensitivity set, same rules (D-036, D-037).
    assert cfg.footprint_set(nldas.footprint_set_id).target_dependence == "no"
    assert cfg.footprint_set(le.footprint_set_id).target_dependence == "yes"
    assert len({e.negative_weight_tolerance for e in (x, m, nldas, le)}) == 1
    aggs = {a.method_id: a for a in cfg.footprint_aggregations}
    assert aggs["nldas_eto_weighted"].weight_column == "nldas_eto_mm_h"
    assert aggs["measured_le_weighted"].target_dependence == "yes"
    shared = ("include_duration", "basis", "negative_weight_policy", "gap_policy",
              "night_policy")
    assert all(getattr(aggs["nldas_eto_weighted"], f) == getattr(aggs["measured_le_weighted"], f)
               for f in shared)


@pytest.mark.parametrize("name", ["us_utj.yaml", "utah_stations.yaml"])
def test_examples_declare_the_step_17_sections(name):
    c = load_config(EXAMPLES / name)
    assert c.run.skip == ("footprint_aggregation",)       # built footprints are never rewritten
    assert c.footprint_stacks and c.decisions and c.upstream
    assert {u.name for u in c.upstream} >= {"fluxfootprints", "flux-data-qaqc"}
    raw = yaml.safe_load((EXAMPLES / name).read_text(encoding="utf-8"))
    assert all(d["ref"].startswith("D-") for d in raw["decisions"])


def test_example_us_utj_declarations():

    from etcompare.config import load_config

    cfg = load_config(EXAMPLE)
    d = cfg.diagnostics_spec("utj_diagnostics")
    assert d.min_bin_days == 20 and d.bootstrap == "utj_block_bootstrap"
    assert d.wind_direction_variable is None
    assert "utj_closure_all_models" in d.comparisons
    status = {k: v.status for k, v in d.variables.items()}
    assert {k for k, s in status.items() if s == "unverified"} == {
        "h_subday_gaps", "le_subday_gaps"}
    # Units confirmed by the user on 2026-10-16 (D-053); ebr/ebr_5day_clim windows unresolved.
    assert {k for k, s in status.items() if s == "provisional"} == {"ebr", "ebr_5day_clim"}
    assert d.variables["vpd"].units == "kPa" and d.variables["rh"].units == "%"
    assert not d.variables["rh"].binned
    assert all(v.column.startswith("src_") for v in d.variables.values() if v.source == "station")
    assert d.variables["ebr_corr"].measured_days_only
    assert all(v.quantiles is None for v in d.variables.values())  # fixed edges only


@pytest.mark.parametrize("name", ["utah_stations.yaml", "flux_drive.yaml"])
def test_flux_examples_read_from_the_data_root(name):
    # Every data path starts with ${FLUX} (D-081), so a moved drive is one line.
    cfg = load_config(EXAMPLES / name)
    root = cfg.data_roots["FLUX"]
    assert all(t.path.is_relative_to(root) for t in cfg.station_tables)
    assert all(r.root.is_relative_to(root) for r in cfg.openet_rasters)
    raw = (EXAMPLES / name).read_text(encoding="utf-8")
    assert "J:/FLUX/" not in raw and "C:/Users/Hutto/Documents/footprints" not in raw.replace(
        "the C:/Users/Hutto/Documents copy", "")


FLUX = load_config(EXAMPLES / "flux_drive.yaml").data_roots["FLUX"] \
    if (EXAMPLES / "flux_drive.yaml").exists() else Path("/nonexistent")


@pytest.mark.skipif(not (FLUX / "footprints").is_dir(), reason="FLUX data folder not present")
def test_flux_drive_footprint_sets_discover():
    from etcompare.io.manifest import discover_footprints
    from etcompare.registry import SiteRegistry

    cfg = load_config(EXAMPLES / "flux_drive.yaml")
    for fs in cfg.footprint_sets:
        r = discover_footprints(fs, sites=SiteRegistry(cfg.site_aliases))
        f = r.manifest.frame
        assert len(f) and set(f["layer_token"]) == set(fs.layers or ())
        assert set(f["site_id"]) == {fs.set_id.split("_")[0]}
    # The top level of US-UTW also holds ffp_xr, km, ls and wang footprints: other models,
    # never mixed in.
    from etcompare.config import FootprintSetSpec

    xr = FootprintSetSpec("utw_xr", FLUX / "footprints" / "US-UTW", weighting_method="x",
                          value_semantics="normalized_cell_weight", layers=("etw",),
                          filename_prefix="ffp_xr")
    r = discover_footprints(xr, sites=SiteRegistry(cfg.site_aliases))
    assert len(r.manifest) and r.manifest.frame["asset_id"].str.startswith("ffp_xr_").all()
    assert "products_not_selected" in r.report.codes("info")


NOTEBOOK = EXAMPLES / "flux_drive.ipynb"


def test_flux_drive_notebook_outputs_stripped():
    import json

    nb = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    code = [c for c in nb["cells"] if c["cell_type"] == "code"]
    assert code and all(c["outputs"] == [] and c["execution_count"] is None for c in code)


@pytest.mark.skipif(not (FLUX / "footprints").is_dir(), reason="FLUX data folder not present")
def test_flux_drive_notebook_runs(monkeypatch):
    # Reads the folder; the last section needs the US-UTJ extraction in outputs/flux_drive.
    import json

    pytest.importorskip("matplotlib")
    import matplotlib

    matplotlib.use("Agg")
    cfg = load_config(EXAMPLES / "flux_drive.yaml")
    x = next(x for x in cfg.raster_extractions if x.site_id == "US-UTJ")
    if not (cfg.output_dir / f"{x.name}_extractions.parquet").exists():
        pytest.skip("run examples/flux_drive.yaml first")
    monkeypatch.chdir(EXAMPLES)
    ns: dict = {"__name__": "__main__"}
    for c in json.loads(NOTEBOOK.read_text(encoding="utf-8"))["cells"]:
        if c["cell_type"] == "code":
            exec(compile("".join(c["source"]), NOTEBOOK.name, "exec"), ns)  # noqa: S102
    assert len(ns["pairs"]) and set(ns["pairs"]["model"]) == set(cfg.products)
