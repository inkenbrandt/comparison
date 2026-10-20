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
