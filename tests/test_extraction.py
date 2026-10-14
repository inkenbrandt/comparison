"""End-to-end raster extraction on synthetic GeoTIFFs (needs the geo extra).

Grids are aligned (30 m model, 10 m footprint, same CRS), so the expected
values come from plain 3x3 block sums, independent of the transfer code.
"""

import numpy as np
import pandas as pd
import pytest

rasterio = pytest.importorskip("rasterio")
pyproj = pytest.importorskip("pyproj")
from affine import Affine  # noqa: E402

from etcompare.config import FootprintSetSpec, ProductSpec, RasterExtractionSpec, StationSpec  # noqa: E402
from etcompare.errors import ContractError  # noqa: E402
from etcompare.extraction import extract_rasters  # noqa: E402
from etcompare.io.manifest import discover_footprints, discover_openet_rasters  # noqa: E402
from etcompare.io.tables import read_table, write_table  # noqa: E402
from etcompare.registry import ModelRegistry  # noqa: E402

CRS = "EPSG:32612"
X0, Y0 = 629000.0, 4127300.0
ET_T = Affine(30, 0, X0, 0, -30, Y0)  # 10 x 10 model cells
FP_ORIGIN = (X0 + 60, Y0 - 60)  # footprint 9x9 @10 m covers model rows/cols 2..4
FP_T = Affine(10, 0, FP_ORIGIN[0], 0, -10, FP_ORIGIN[1])
TOWER_UTM = (X0 + 105, Y0 - 105)  # inside model cell (3, 3)
DAYS = ["2024-06-05", "2024-06-06", "2024-06-07"]
MODELS = ModelRegistry.default()


def write(path, arr, transform, nodata):
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", driver="GTiff", height=arr.shape[0], width=arr.shape[1],
                       count=1, dtype="float32", crs=CRS, transform=transform,
                       nodata=nodata) as ds:
        ds.write(arr.astype("float32"), 1)


def et_field(model, day):
    rows, cols = np.mgrid[0:10, 0:10]
    base = 1.0 + 0.1 * rows + 0.03 * cols + (0.5 if model == "sims" else 0.0) + 0.01 * day
    base[0, 0] = 0.0  # zero is valid
    if model == "sims":
        base[2, 4] = -9999  # nodata inside the footprint
    return base


def footprint(day):
    rng = np.random.default_rng(day)
    w = rng.random((9, 9))
    w[0, :] = 0.0  # declared nodata = 0 -> zero weight
    w /= w.sum()
    if day == 1:
        w[4, 4] = -0.01  # genuine negative weights -> reject
    return w


@pytest.fixture
def setup(tmp_path):
    et_root, fp_root = tmp_path / "openet", tmp_path / "fp"
    for i, d in enumerate(DAYS):
        ymd = d.replace("-", "_")
        for model in ("ssebop", "sims"):
            if model == "sims" and i == 2:
                continue
            write(et_root / model / f"US-XXX_{model}_et_{ymd}_abc12.tif", et_field(model, i),
                  ET_T, -9999)
        if i < 2:
            write(fp_root / f"ffp_US-XXX_daily_etw_{d.replace('-', '')}.tif", footprint(i),
                  FP_T, 0.0)
    lon, lat = pyproj.Transformer.from_crs(CRS, "EPSG:4326", always_xy=True).transform(
        *TOWER_UTM)
    station = StationSpec("US-XXX", timezone="UTC-07:00", day_definition="local_standard_time",
                          interval_label="start", latitude=lat, longitude=lon)
    products = {m: ProductSpec(m, units="mm/day", day_definition="unknown",
                               temporal_support="interpolated_daily", raster_scaling="declared",
                               scale_factor=1.0, add_offset=0.0, nodata=-9999.0)
                for m in MODELS.models}
    fset = FootprintSetSpec("SYN", fp_root, weighting_method="synthetic",
                            value_semantics="normalized_cell_weight")
    mman = discover_openet_rasters(et_root, models=MODELS, products=products).manifest
    fman = discover_footprints(fset).manifest
    spec = RasterExtractionSpec("syn", "US-XXX", methods=(
        "footprint", "footprint_common_support", "pixel_window", "nearest_tower_pixel"),
        footprint_set_id="SYN", window_sizes=(3,))
    return dict(spec=spec, station=station, model_manifest=mman, products=products,
                footprint_manifest=fman, footprint_set=fset, et_root=et_root)


def expected_fp(model, day, mask=None):
    stored = footprint(day).astype("float32").astype(float)  # GeoTIFF precision
    c = stored.reshape(3, 3, 3, 3).sum(axis=(1, 3))  # model cells rows/cols 2..4
    et = et_field(model, day)[2:5, 2:5].astype("float32").astype(float)
    valid = et != -9999
    if mask is not None:
        valid &= mask
    return (c[valid] * et[valid]).sum() / c[valid].sum(), c[valid].sum() / stored.sum()


def run(setup, **kw):
    args = {k: v for k, v in setup.items() if k != "et_root"}
    args.update(kw)
    return extract_rasters(**args)


def pick(frame, model, method, date):
    sel = frame[(frame.model == model) & (frame.extraction_method == method)
                & (frame.date == pd.Timestamp(date))]
    assert len(sel) == 1, (model, method, date, len(sel))
    return sel.iloc[0]


def test_footprint_values_match_block_aggregation(setup):
    r = run(setup)
    f = r.frame
    for model in ("ssebop", "sims"):
        row = pick(f, model, "footprint_conservative", DAYS[0])
        val, retained = expected_fp(model, 0)
        assert row.et_mm == pytest.approx(val, rel=1e-12)
        assert row.fp_weight_retained_frac == pytest.approx(retained, rel=1e-12)
        assert row.fp_frac_in_model_extent == pytest.approx(1.0)
        assert row.fp_weight_retained_denominator == "normalized_truncated_footprint"
        assert row.footprint_set_id == "SYN" and row.day_alignment == "unverified"
    assert pick(f, "sims", "footprint_conservative", DAYS[0]).fp_weight_retained_frac < 1
    assert "transfer_built" in r.report.codes()


def test_common_support_uses_intersection(setup):
    f = run(setup).frame
    common = np.ones((3, 3), bool)
    common[0, 2] = False  # sims nodata at model cell (2, 4)
    a = pick(f, "ssebop", "footprint_conservative_common_support", DAYS[0])
    b = pick(f, "sims", "footprint_conservative_common_support", DAYS[0])
    assert a.et_mm == pytest.approx(expected_fp("ssebop", 0, common)[0], rel=1e-12)
    assert a.support_id == b.support_id and a.support_id.startswith("common2:")
    own = pick(f, "ssebop", "footprint_conservative", DAYS[0])
    assert own.support_id != a.support_id and own.et_mm != a.et_mm


def test_pixel_methods(setup):
    f = run(setup).frame
    et = et_field("ssebop", 2).astype("float32").astype(float)
    assert pick(f, "ssebop", "nearest_tower_pixel", DAYS[2]).et_mm == pytest.approx(et[3, 3])
    w = pick(f, "ssebop", "pixel_window_3x3", DAYS[2])
    assert w.et_mm == pytest.approx(et[2:5, 2:5].mean()) and w.n_cells_valid == 9
    s = pick(f, "sims", "pixel_window_3x3", DAYS[0])
    assert s.n_cells_valid == 8  # nodata pixel excluded, not zero-filled
    assert s.footprint_set_id == "none" and pd.isna(s.fp_weight_retained_frac)


def test_rejected_and_missing_footprints(setup):
    r = run(setup)
    f, ex = r.frame, r.exclusions.to_frame()
    fp_days = set(f.loc[f.extraction_method == "footprint_conservative", "date"])
    assert fp_days == {pd.Timestamp(DAYS[0])}
    reasons = dict(zip(ex["input_key"].str[-10:], ex["reason_code"], strict=False))
    assert reasons[DAYS[1]] == "footprint_negative_weights"
    assert reasons[DAYS[2]] == "footprint_missing"
    # Footprint-independent methods still run on those days.
    day2 = f[(f.date == pd.Timestamp(DAYS[1])) & (f.extraction_method == "pixel_window_3x3")]
    assert len(day2) == 2


def test_round_trip(setup, tmp_path):
    r = run(setup)
    back = read_table(write_table(r.table, tmp_path / "x.parquet"))
    pd.testing.assert_frame_equal(back.frame, r.frame)


def test_contract_failures(setup):
    undeclared = {m: ProductSpec(m, units="mm/day", day_definition="unknown",
                                 temporal_support="interpolated_daily") for m in MODELS.models}
    with pytest.raises(ContractError, match="raster scaling unknown"):
        run(setup, products=undeclared)
    no_nodata = {m: ProductSpec(m, units="mm/day", day_definition="unknown",
                                temporal_support="interpolated_daily",
                                raster_scaling="declared", scale_factor=1.0, add_offset=0.0)
                 for m in MODELS.models}
    with pytest.raises(ContractError, match="header nodata -9999"):
        run(setup, products=no_nodata)
    nocoords = StationSpec("US-XXX", timezone="UTC-07:00", day_definition="civil_time",
                           interval_label="start")
    with pytest.raises(ContractError, match="coordinates"):
        run(setup, station=nocoords)


def test_unreadable_raster_is_counted_or_raised(setup):
    bad = setup["et_root"] / "ssebop" / "US-XXX_ssebop_et_2024_06_08_abc12.tif"
    bad.write_bytes(b"not a tiff")
    setup["model_manifest"] = discover_openet_rasters(
        setup["et_root"], models=MODELS, products=setup["products"]).manifest
    r = run(setup)
    assert "unreadable_raster" in r.report.codes("warning")
    assert "unreadable_raster" in set(r.exclusions.to_frame()["reason_code"])
    with pytest.raises(rasterio.errors.RasterioIOError):
        run(setup, strict=True)
