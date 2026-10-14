"""Guide step 5 acceptance fixtures for grids, transfer and extraction arithmetic."""

import math

import numpy as np
import pytest

from etcompare.errors import GridError
from etcompare.footprints import prepare_footprint
from etcompare.spatial import (
    Grid,
    block_mean,
    transfer_matrix,
    weighted_extract,
    window_covering,
)

UTM = "EPSG:32612"


def grid(x0, y0, size, w, h, crs=UTM):
    return Grid(crs, (size, 0.0, x0, 0.0, -size, y0), w, h)


# ---------------------------------------------------------------- guide fixtures

def test_uniform_et_unchanged_under_any_weights():
    rng = np.random.default_rng(0)
    src, dst = grid(5, 95, 10, 9, 9), grid(0, 100, 30, 4, 4)
    t = transfer_matrix(src, dst)
    for _ in range(5):
        w = rng.random(src.shape)
        r = weighted_extract(t.apply(w), np.full(dst.shape, 3.7), weight_total=w.sum())
        assert r.value == pytest.approx(3.7, abs=1e-12)


def test_two_cell_fixture_and_masking():
    g = grid(0, 10, 10, 2, 1)
    t = transfer_matrix(g, g)
    c = t.apply(np.array([[0.75, 0.25]]))
    r = weighted_extract(c, np.array([[2.0, 6.0]]), weight_total=1.0)
    assert r.value == pytest.approx(3.0)
    masked = weighted_extract(c, np.array([[2.0, np.nan]]), weight_total=1.0)
    assert masked.value == pytest.approx(2.0)
    assert masked.retained_frac == pytest.approx(0.75)
    assert masked.n_cells_valid == 1


def test_zero_et_is_valid():
    r = weighted_extract(np.array([[0.5, 0.5]]), np.array([[0.0, 2.0]]), weight_total=1.0)
    assert r.value == pytest.approx(1.0) and r.n_cells_valid == 2


def test_conservation_aligned():
    rng = np.random.default_rng(1)
    src, dst = grid(7, 93, 10, 8, 8), grid(0, 100, 30, 4, 4)  # src fully inside dst
    t = transfer_matrix(src, dst)
    w = rng.random(src.shape)
    assert t.apply(w).sum() == pytest.approx(w.sum(), rel=1e-12)
    assert np.allclose(t.coverage(), 1.0)


def test_partial_cells_analytic():
    # One 10 m cell spanning x 5..15 over 10 m model cells 0..10 and 10..20.
    src, dst = grid(5, 10, 10, 1, 1), grid(0, 10, 10, 2, 1)
    m = transfer_matrix(src, dst).matrix.toarray()
    assert np.allclose(m, [[0.5, 0.5]])
    # Shifted in both axes: quarter overlaps.
    src2, dst2 = grid(5, 15, 10, 1, 1), grid(0, 20, 10, 2, 2)
    assert np.allclose(transfer_matrix(src2, dst2).matrix.toarray(), 0.25)


def test_non_overlap():
    src, dst = grid(1000, 1000, 10, 2, 2), grid(0, 100, 30, 3, 3)
    assert window_covering(src, dst) is None
    t = transfer_matrix(src, dst)
    assert t.matrix.nnz == 0
    r = weighted_extract(t.apply(np.ones(src.shape)), np.ones(dst.shape), weight_total=4.0)
    assert r.status == "no_overlap" and math.isnan(r.value)


def test_partly_outside_reports_in_extent_fraction():
    src, dst = grid(-10, 10, 10, 2, 1), grid(0, 10, 10, 1, 1)  # left cell outside dst
    t = transfer_matrix(src, dst)
    r = weighted_extract(t.apply(np.array([[0.4, 0.6]])), np.array([[5.0]]), weight_total=1.0)
    assert r.in_extent_frac == pytest.approx(0.6)
    assert r.retained_frac == pytest.approx(0.6)
    assert r.value == pytest.approx(5.0)


def test_differing_model_masks_and_common_support():
    g = grid(0, 10, 10, 3, 1)
    c = np.array([[0.2, 0.3, 0.5]])
    a = np.array([[1.0, np.nan, 3.0]])
    b = np.array([[2.0, 4.0, np.nan]])
    ra = weighted_extract(c, a, weight_total=1.0, grid=g)
    rb = weighted_extract(c, b, weight_total=1.0, grid=g)
    assert ra.support_id != rb.support_id
    common = np.isfinite(a) & np.isfinite(b)
    ca = weighted_extract(c, a, weight_total=1.0, grid=g, extra_mask=common)
    cb = weighted_extract(c, b, weight_total=1.0, grid=g, extra_mask=common)
    assert ca.support_id == cb.support_id
    assert ca.value == pytest.approx(1.0) and cb.value == pytest.approx(2.0)
    assert ca.retained_frac == pytest.approx(0.2)
    none = weighted_extract(c, a, weight_total=1.0, extra_mask=np.zeros_like(common))
    assert none.status == "no_valid_cells"


def test_missing_crs_rejected():
    with pytest.raises(GridError, match="no CRS"):
        transfer_matrix(grid(0, 10, 10, 1, 1, crs=None), grid(0, 10, 10, 1, 1))


def test_negative_contribution_never_reaches_extraction():
    with pytest.raises(GridError, match="negative"):
        weighted_extract(np.array([[-0.1, 1.1]]), np.ones((1, 2)), weight_total=1.0)


# ---------------------------------------------------------------- footprints

def test_prepare_footprint_policies():
    g = grid(0, 20, 10, 2, 2)
    ok = prepare_footprint(np.array([[0.5, 0.0], [np.nan, 0.5]]), g,
                           value_semantics="normalized_cell_weight", nodata=0.0)
    assert ok.ok and ok.weight_total == pytest.approx(1.0) and ok.n_missing_cells == 2
    assert ok.denominator == "normalized_truncated_footprint"

    tiny = prepare_footprint(np.array([[0.5, -1e-12], [0.25, 0.25]]), g,
                             value_semantics="normalized_cell_weight", nodata=0.0)
    assert tiny.ok and tiny.negatives_zeroed and (tiny.contribution >= 0).all()

    neg = prepare_footprint(np.array([[0.5, -1e-4], [0.25, 0.25]]), g,
                            value_semantics="normalized_cell_weight", nodata=0.0)
    assert neg.status == "negative_weights" and neg.contribution is None
    loose = prepare_footprint(np.array([[0.5, -1e-4], [0.25, 0.25]]), g,
                              value_semantics="normalized_cell_weight", nodata=0.0,
                              negative_tolerance=1e-3)
    assert loose.ok and loose.negatives_zeroed

    empty = prepare_footprint(np.full((2, 2), np.nan), g,
                              value_semantics="normalized_cell_weight", nodata=0.0)
    assert empty.status == "no_positive_weight"


# ---------------------------------------------------------------- blocks

def test_block_mean():
    g = grid(0, 50, 10, 5, 5)
    v = np.arange(25, dtype=float).reshape(5, 5)
    r = block_mean(v, g, 25, 25, 3)  # centre pixel (2, 2)
    assert r.value == pytest.approx(v[1:4, 1:4].mean()) and r.n_cells_valid == 9
    v2 = v.copy()
    v2[1, 1] = np.nan
    assert block_mean(v2, g, 25, 25, 3).n_cells_valid == 8
    edge = block_mean(v, g, 5, 45, 3)  # corner pixel: 4 of 9 inside the raster
    assert edge.n_cells_valid == 4 and edge.n_cells == 9
    assert block_mean(v, g, 25, 25, 1).value == 12.0
    assert block_mean(v, g, 500, 25, 3).status == "tower_outside_raster"


# ---------------------------------------------------------------- general path (geo)

def test_polygon_path_equals_aligned_path():
    pytest.importorskip("shapely")
    src, dst = grid(3, 97, 10, 9, 9), grid(0, 100, 30, 4, 4)
    a = transfer_matrix(src, dst, method="aligned_1d_overlap").matrix.toarray()
    p = transfer_matrix(src, dst, method="polygon_overlap").matrix.toarray()
    assert np.allclose(a, p, atol=1e-12)


def test_rotated_source_grid_conserves():
    pytest.importorskip("shapely")
    th = math.radians(30)
    s = 10.0
    rot = Grid(UTM, (s * math.cos(th), -s * math.sin(th), 50.0, s * math.sin(th),
                     s * math.cos(th) * -1, 60.0), 3, 3)
    dst = grid(0, 100, 10, 12, 12)
    t = transfer_matrix(rot, dst)
    assert t.method == "polygon_overlap"
    assert np.allclose(t.coverage(), 1.0, atol=1e-12)


def test_cross_crs_against_point_sampling():
    pytest.importorskip("shapely")
    pyproj = pytest.importorskip("pyproj")
    src = grid(629255.97, 4127759.12, 10, 12, 12)  # 10 m UTM 12N, near US-UTJ
    tr = pyproj.Transformer.from_crs(UTM, "EPSG:4326", always_xy=True)
    lon0, lat0 = tr.transform(629255.97 - 60, 4127759.12 + 60)
    step = 0.000269494585
    dst = Grid("EPSG:4326", (step, 0.0, lon0, 0.0, -step, lat0), 12, 12)
    t = transfer_matrix(src, dst)
    assert np.allclose(t.coverage(), 1.0, atol=1e-9)

    # Independent estimate: 40x40 sample points per source cell.
    n = 40
    sub = (np.arange(n) + 0.5) / n
    X, Y = src.corners()
    rng = np.random.default_rng(3)
    for idx in rng.choice(src.size, 6, replace=False):
        r, c = divmod(int(idx), src.width)
        px = X[r, c] + 10 * sub[None, :].repeat(n, 0)
        py = Y[r, c] - 10 * sub[:, None].repeat(n, 1)
        lon, lat = tr.transform(px.ravel(), py.ravel())
        rows, cols = dst.rowcol(lon, lat)
        cells = np.floor(rows).astype(int) * dst.width + np.floor(cols).astype(int)
        est = np.bincount(cells, minlength=dst.size) / cells.size
        exact = t.matrix.getrow(int(idx)).toarray().ravel()
        assert np.abs(est - exact).max() < 1.5 / n  # sampling error bound


def test_density_conversion_uses_cell_area():
    pytest.importorskip("pyproj")
    g = grid(0, 20, 10, 2, 2)
    d = prepare_footprint(np.full((2, 2), 0.0025), g, value_semantics="density_per_m2",
                          nodata=None)
    assert d.weight_total == pytest.approx(1.0)  # 0.0025 m-2 * 100 m2 * 4 cells
    assert d.denominator == "footprint_grid_total"
    with pytest.raises(GridError, match="projected CRS"):
        prepare_footprint(np.ones((2, 2)), grid(0, 20, 10, 2, 2, crs="EPSG:4326"),
                          value_semantics="density_per_m2", nodata=None)
