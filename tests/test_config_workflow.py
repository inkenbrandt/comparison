"""Step 17 configuration sections: run, report, cache, upstream, decisions, stacks,
footprint comparisons, and the former command-line flags that became fields."""

from pathlib import Path

import pytest
import yaml

from etcompare.config import WORKFLOW_ANALYSES, config_from_dict, load_config
from etcompare.errors import ContractError

EXAMPLES = Path(__file__).parents[1] / "examples"

BASE = {
    "stations": {"US-UTJ": {"timezone": "UTC-07:00", "day_definition": "local_standard_time",
                            "interval_label": "start"}},
    "products": {"a": {"units": "mm/day", "day_definition": "unknown",
                       "temporal_support": "interpolated_daily"}},
    "footprint_sets": [
        {"set_id": "A", "root": "a", "weighting_method": "w", "value_semantics":
         "normalized_cell_weight"},
        {"set_id": "B", "root": "b", "weighting_method": "w", "value_semantics":
         "normalized_cell_weight"}],
    "raster_extractions": [
        {"name": "xa", "site_id": "US-UTJ", "methods": ["footprint"], "footprint_set_id": "A"},
        {"name": "xb", "site_id": "US-UTJ", "methods": ["footprint"], "footprint_set_id": "B"},
        {"name": "xw", "site_id": "US-UTJ", "methods": ["pixel_window"]}],
    "output_dir": "out",
}


def cfg(**extra):
    return config_from_dict({**BASE, **extra}, base_dir=Path("."))


def test_defaults():
    c = cfg()
    assert c.run.skip == () and c.cache.raster_identity == "stat" and c.cache.enabled
    assert c.report.formats == ("markdown", "html")
    assert c.report.figure_formats == ("png", "svg")
    assert not c.report.synthetic and c.upstream == () and c.decisions == ()


def test_run_skip_names_known_analyses():
    assert cfg(run={"skip": ["bootstrap"]}).run.skip == ("bootstrap",)
    with pytest.raises(ContractError, match="unknown analyses"):
        cfg(run={"skip": ["bootstrapping"]})
    with pytest.raises(ContractError, match="must be a list"):
        cfg(run={"skip": "bootstrap"})
    with pytest.raises(ContractError, match="unknown configuration key"):
        cfg(run={"no_bootstrap": True})
    assert "bootstrap" in WORKFLOW_ANALYSES


def test_report_section():
    with pytest.raises(ContractError, match="report.formats"):
        cfg(report={"formats": ["pdf"]})
    with pytest.raises(ContractError, match="report.figure_formats"):
        cfg(report={"figure_formats": []})
    with pytest.raises(ContractError, match="not declared"):
        cfg(report={"comparisons": ["nope"]})
    with pytest.raises(ContractError, match="never names a real station"):
        cfg(report={"synthetic": True})
    syn = {**BASE["stations"]["US-UTJ"]}
    c = config_from_dict({**BASE, "stations": {"XX-SY1": syn}, "raster_extractions": [],
                          "report": {"synthetic": True}})
    assert c.report.synthetic


def test_cache_section():
    assert cfg(cache={"raster_identity": "sha256"}).cache.raster_identity == "sha256"
    with pytest.raises(ContractError, match="raster_identity"):
        cfg(cache={"raster_identity": "md5"})


def test_upstream_needs_version_or_commit_and_evidence():
    c = cfg(upstream=[{"name": "fluxfootprints", "version": "0.5.1", "evidence": "verified"}])
    assert c.upstream[0].version == "0.5.1"
    with pytest.raises(ContractError, match="version or a commit"):
        cfg(upstream=[{"name": "x", "evidence": "declared"}])
    with pytest.raises(ContractError, match="evidence"):
        cfg(upstream=[{"name": "x", "version": "1", "evidence": "rumour"}])


def test_decisions_point_at_declared_sections_and_entries():
    d = {"ref": "D-048", "status": "open", "summary": "s", "applies_to": ["raster_extractions.xa"]}
    assert cfg(decisions=[d]).decisions[0].applies_to == ("raster_extractions.xa",)
    with pytest.raises(ContractError, match="no entry"):
        cfg(decisions=[{**d, "applies_to": ["raster_extractions.zz"]}])
    with pytest.raises(ContractError, match="names no configuration section"):
        cfg(decisions=[{**d, "applies_to": ["boots"]}])
    with pytest.raises(ContractError, match="status"):
        cfg(decisions=[{**d, "status": "adopted"}])
    with pytest.raises(ContractError, match="duplicate decision"):
        cfg(decisions=[d, d])


def test_footprint_comparisons_replace_the_command_line():
    c = cfg(footprint_comparisons=[{"comparison_id": "ab", "extractions": ["xa", "xb"],
                                    "min_retained_frac": 0.9}])
    fc = c.footprint_comparisons[0]
    assert fc.methods == ("footprint_conservative", "footprint_conservative_common_support")
    with pytest.raises(ContractError, match="at least two"):
        cfg(footprint_comparisons=[{"comparison_id": "ab", "extractions": ["xa"]}])
    with pytest.raises(ContractError, match="not declared"):
        cfg(footprint_comparisons=[{"comparison_id": "ab", "extractions": ["xa", "zz"]}])
    with pytest.raises(ContractError, match="needs a footprint set"):
        cfg(footprint_comparisons=[{"comparison_id": "ab", "extractions": ["xa", "xw"]}])
    with pytest.raises(ContractError, match="min_retained_frac"):
        cfg(footprint_comparisons=[{"comparison_id": "ab", "extractions": ["xa", "xb"],
                                    "min_retained_frac": 1.5}])


def test_footprint_stacks():
    c = cfg(footprint_stacks=[{"site_id": "US-UTJ", "stack_dir": "s", "compare_sets": ["A"]}])
    assert c.footprint_stacks[0].stack_dir == Path("s")
    with pytest.raises(ContractError, match="station not declared"):
        cfg(footprint_stacks=[{"site_id": "US-UTX", "stack_dir": "s"}])
    with pytest.raises(ContractError, match="compare_sets"):
        cfg(footprint_stacks=[{"site_id": "US-UTJ", "stack_dir": "s", "compare_sets": ["Z"]}])


def test_former_flags_are_fields():
    x = {**BASE["raster_extractions"][0], "strict": True}
    assert cfg(raster_extractions=[x]).raster_extractions[0].strict
    with pytest.raises(ContractError, match="strict"):
        cfg(raster_extractions=[{**x, "strict": "yes"}])
    crop = {"name": "c", "path": "c.csv", "site_id": "US-UTJ", "date_column": "date",
            "height_column": "h", "height_units": "cm", "season_start_column": "s",
            "season_end_column": "e"}
    assert cfg(crop_height_tables=[crop]).crop_height_tables[0].on_missing == "error"
    with pytest.raises(ContractError, match="on_missing"):
        cfg(crop_height_tables=[{**crop, "on_missing": "ignore"}])


@pytest.mark.parametrize("name", ["us_utj.yaml", "utah_stations.yaml"])
def test_examples_declare_the_step_17_sections(name):
    c = load_config(EXAMPLES / name)
    assert c.run.skip == ("footprint_aggregation",)       # built footprints are never rewritten
    assert c.footprint_stacks and c.decisions and c.upstream
    assert {u.name for u in c.upstream} >= {"fluxfootprints", "flux-data-qaqc"}
    raw = yaml.safe_load((EXAMPLES / name).read_text(encoding="utf-8"))
    assert all(d["ref"].startswith("D-") for d in raw["decisions"])
