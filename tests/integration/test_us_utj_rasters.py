"""Opt-in raster checks on the user's Drive (set ETCOMPARE_RASTER_TESTS=1; slow, local only)."""

import dataclasses
import os
from pathlib import Path

import pytest

pytest.importorskip("rasterio")

from etcompare.adapters.extracted import adapt_extracted_table  # noqa: E402
from etcompare.config import load_config  # noqa: E402
from etcompare.extraction import extract_rasters  # noqa: E402
from etcompare.io.manifest import discover_footprints, discover_openet_rasters  # noqa: E402

ROOT = Path(__file__).parents[2]
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("ETCOMPARE_RASTER_TESTS") != "1",
                       reason="set ETCOMPARE_RASTER_TESTS=1 to read rasters on the Drive"),
]


@pytest.fixture(scope="module")
def env():
    cfg = load_config(ROOT / "examples" / "us_utj.yaml")
    if not cfg.openet_rasters[0].root.is_dir():
        pytest.skip("Drive not mounted")
    mman = discover_openet_rasters(cfg.openet_rasters[0].root, models=cfg.model_registry(),
                                   products=cfg.products).manifest
    return cfg, mman


def run(cfg, mman, set_id, start="2025-09-10", end="2025-09-14"):
    fs = cfg.footprint_set(set_id)
    spec = dataclasses.replace(cfg.raster_extractions[0], footprint_set_id=set_id)
    return extract_rasters(spec, station=cfg.stations["US-UTJ"], model_manifest=mman,
                           products=cfg.products,
                           footprint_manifest=discover_footprints(fs).manifest,
                           footprint_set=fs, start=start, end=end)


def test_set_b_extraction(env):
    cfg, mman = env
    r = run(cfg, mman, "US-UTJ_set_B_etw_20261012")
    assert r.report.ok
    built = next(i for i in r.report if i.code == "transfer_built")
    assert "polygon_overlap" in built.message
    assert "15625 footprint cells fully inside" in built.message
    fp = r.frame[r.frame.extraction_method == "footprint_conservative"]
    assert len(fp) == 28  # 7 models x 4 days; 2025-09-13 has negative weights
    assert (fp.fp_frac_in_model_extent - 1).abs().max() < 1e-9
    assert fp.fp_domain_capture_frac.notna().all()
    assert fp.loc[fp.model != "sims", "fp_weight_retained_frac"].min() > 0.999999
    assert "footprint_negative_weights" in set(r.exclusions.to_frame().reason_code)


def test_set_a_conservative_close_to_producer_bilinear(env):
    cfg, mman = env
    r = run(cfg, mman, "US-UTJ_set_A_20261005")
    a = r.frame[r.frame.extraction_method == "footprint_conservative"].set_index(["model", "date"])
    pq = adapt_extracted_table(ROOT / "station_oet_US-UTJ.parquet", cfg.extracted_tables[0],
                               models=cfg.model_registry()).frame
    pq = pq[pq.extraction_method == "upstream_footprint_weighted"].set_index(["model", "date"])
    j = a[["et_mm"]].join(pq[["et_mm"]], rsuffix="_bilinear", how="inner")
    assert len(j) == 28
    assert (j.et_mm - j.et_mm_bilinear).abs().max() < 0.05  # method difference, D-031
