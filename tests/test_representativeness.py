"""Landscape representativeness (guide step 13).

Expected values are independent of the code under test: class shares are rectangle
areas worked out by hand (partial cells and a shifted grid included), index moments
are written out for weights 0.75/0.25, contours are listed by hand from binary-exact
weights, and matching dates are counted on a calendar.
"""

import math

import numpy as np
import pandas as pd
import pytest

from etcompare.config import (
    LandscapeLayerSpec,
    LayerAssetSpec,
    MatchingPolicySpec,
    RepresentativenessSpec,
    config_from_dict,
)
from etcompare.errors import ContractError
from etcompare.footprints import prepare_footprint
from etcompare.io.tables import read_table, write_table
from etcompare.representativeness import (
    FOOTPRINT_METHOD,
    FootprintDay,
    LabelSource,
    LayerAsset,
    assess_landscape,
    class_composition,
    contour_mask,
    index_summary,
    match_asset,
    nearest_pixel_support,
    pixel_window_support,
)
from etcompare.spatial import Grid, transfer_matrix

CRS = "EPSG:32612"
LEGEND = {82: "Cultivated Crops", 90: "Woody Wetlands"}
NODATA = 250.0


def grid(x0, y0, step, w, h):
    return Grid(CRS, (step, 0.0, x0, 0.0, -step, y0), w, h)


# 30 m map, 4 x 4 cells, x 0..120, y 0..120: x < 60 is class 82, x >= 60 class 90, and
# the top-right cell (x 90..120, y 90..120) is nodata.
LC_GRID = grid(0.0, 120.0, 30.0, 4, 4)
LC = np.array([[82, 82, 90, NODATA]] + [[82, 82, 90, 90]] * 3, dtype=float)


def cat_layer(**kw):
    base = dict(layer_id="lc", kind="categorical", source="synthetic", crs=CRS, nodata=NODATA,
                legend=LEGEND, assets=(LayerAssetSpec("lc_2024.tif", vintage=2024),))
    base.update(kw)
    return LandscapeLayerSpec(**base)


def idx_layer(**kw):
    base = dict(layer_id="ndvi", kind="continuous", source="synthetic", crs=CRS,
                nodata=-9999.0, units="1", scale_factor=1.0, add_offset=0.0,
                acquisition_date_source="synthetic scene metadata",
                assets=(LayerAssetSpec("ndvi_a.tif", acquisition_date="2024-06-01"),))
    base.update(kw)
    return LandscapeLayerSpec(**base)


def rspec(layers=None, policies=("nearest",), **kw):
    base = dict(representativeness_id="rep", site_id="US-AAA", footprint_sets=("SET",),
                layers=layers or {"lc": cat_layer()},
                matching_policies=tuple(MatchingPolicySpec(p) if isinstance(p, str) else p
                                        for p in policies))
    base.update(kw)
    return RepresentativenessSpec(**base)


def asset(values=LC, g=LC_GRID, layer_id="lc", i=0, start="2024-01-01", end="2024-12-31",
          vintage=2024):
    return LayerAsset(layer_id, i, f"{layer_id}_{i}", g, np.asarray(values, float),
                      pd.Timestamp(start), pd.Timestamp(end), vintage)


def footprint(values, g):
    fp = prepare_footprint(np.asarray(values, float), g, value_semantics="normalized_cell_weight",
                           nodata=None)
    assert fp.ok
    return fp


# ----------------------------------------------------------------------- class fractions


def test_mixed_land_cover_analytic_fractions_shifted_grid_partial_cells():
    # Uniform footprint on 10 m cells over x 25..95, y 25..115 (shifted by 25 m from the
    # 30 m map, so cells straddle the map edges at x = 30, 60, 90 and y = 30, 60, 90).
    fg = grid(25.0, 115.0, 10.0, 7, 9)
    fp = footprint(np.full((9, 7), 1 / 63), fg)
    contrib = transfer_matrix(fg, LC_GRID).apply(fp.contribution)
    comp = class_composition(contrib, LC, nodata=NODATA, legend=LEGEND,
                             weight_grid_total=fp.weight_total)
    # Areas: rectangle 70 x 90 = 6300 m2; x < 60: 35 x 90 = 3150 (class 82);
    # x 60..95: 3150, of which x 90..95, y 90..115 (5 x 25 = 125) is nodata.
    total, a82, a90, anod = 6300.0, 3150.0, 3025.0, 125.0
    frac = comp.frac_of_valid()
    assert frac[82] == pytest.approx(a82 / (a82 + a90), abs=1e-12)
    assert frac[90] == pytest.approx(a90 / (a82 + a90), abs=1e-12)
    assert sum(frac.values()) == pytest.approx(1.0, abs=1e-12)
    f = comp.weights.fractions()
    assert f["valid_frac_of_grid_total"] == pytest.approx((a82 + a90) / total, abs=1e-12)
    assert f["nodata_frac_of_grid_total"] == pytest.approx(anod / total, abs=1e-12)
    assert f["outside_layer_frac_of_grid_total"] == pytest.approx(0.0, abs=1e-12)
    assert comp.weights.weight_denominator == "footprint_grid_total"


def test_partial_cell_weights_and_outside_extent():
    # 0.75 on a 10 m cell inside class 82 (x 10..20); 0.25 on a cell straddling x = 60
    # (x 55..65): half in 82, half in 90.
    fg = grid(10.0, 50.0, 5.0, 12, 2)  # 5 m cells x 10..70, y 40..50
    w = np.zeros((2, 12))
    w[:, 0:2] = 0.75 / 4  # x 10..20
    w[:, 9:11] = 0.25 / 4  # x 55..65
    contrib = transfer_matrix(fg, LC_GRID).apply(w)
    comp = class_composition(contrib, LC, nodata=NODATA, legend=LEGEND, weight_grid_total=1.0)
    assert comp.class_weights[82] == pytest.approx(0.75 + 0.125, abs=1e-12)
    assert comp.class_weights[90] == pytest.approx(0.125, abs=1e-12)
    # A footprint half outside the map: x -20..20 at the left edge.
    fg2 = grid(-20.0, 50.0, 10.0, 4, 1)
    contrib2 = transfer_matrix(fg2, LC_GRID).apply(np.full((1, 4), 0.25))
    comp2 = class_composition(contrib2, LC, nodata=NODATA, legend=LEGEND, weight_grid_total=1.0)
    f = comp2.weights.fractions()
    assert f["outside_layer_frac_of_grid_total"] == pytest.approx(0.5, abs=1e-12)
    assert f["valid_frac_of_grid_total"] == pytest.approx(0.5, abs=1e-12)
    assert comp2.frac_of_valid() == {82: pytest.approx(1.0)}


def test_class_missing_from_legend_raises_and_nodata_class_conflict():
    with pytest.raises(ContractError, match="not in the declared legend"):
        class_composition(np.ones((4, 4)), LC, nodata=NODATA, legend={82: "crop"},
                          weight_grid_total=16.0)
    with pytest.raises(ContractError, match="also a legend class"):
        cat_layer(legend={82: "crop", 250: "nodata"})


def test_assess_landscape_end_to_end_rows_and_denominators():
    fg = grid(25.0, 115.0, 10.0, 7, 9)
    days = [FootprintDay(pd.Timestamp("2024-06-05"), footprint(np.full((9, 7), 1 / 63), fg))]
    res = assess_landscape(rspec(), footprint_days={"SET": days}, layers={"lc": [asset()]},
                           station_land_cover="CRO")
    cf = res.class_fractions.frame
    fp = cf[cf["extraction_method"] == FOOTPRINT_METHOD]
    classes = fp[fp["class_role"] == "class"].set_index("class_code")
    assert classes.loc["82", "class_frac_of_valid"] == pytest.approx(3150 / 6175, abs=1e-12)
    assert classes["class_frac_of_valid"].sum() == pytest.approx(1.0, abs=1e-12)
    nod = fp[fp["class_role"] == "nodata"].iloc[0]
    assert pd.isna(nod["class_frac_of_valid"])
    assert nod["class_frac_of_grid_total"] == pytest.approx(125 / 6300, abs=1e-12)
    assert set(fp["weight_denominator"]) == {"normalized_truncated_footprint"}
    assert np.allclose(fp["valid_frac_of_grid_total"], 6175 / 6300, rtol=0, atol=1e-12)
    assert set(fp["domain"]) == {"full_domain"}
    labels = res.labels.frame
    igbp = labels[labels["label_source"] == "station_metadata"].iloc[0]
    assert igbp["label"] == "CRO" and "not a footprint-weighted fraction" in igbp["label_meaning"]
    assert "label_not_accuracy_guarantee" in igbp["flags"]
    av = res.availability.frame  # no vegetation index declared: said so, nothing substituted
    row = av[av["analysis"] == "landscape_continuous"].iloc[0]
    assert (row["status"], row["reason_code"]) == ("not_available",
                                                   "no_continuous_layer_declared")
    assert len(res.index_summaries.frame) == 0


# ----------------------------------------------------------------------- continuous index


def test_uniform_index_zero_variability_and_hand_computed_moments():
    g = grid(0.0, 60.0, 30.0, 2, 2)
    c = np.array([[0.75, 0.25], [0.0, 0.0]])
    s = index_summary(c, np.full((2, 2), 0.4), weight_grid_total=1.0)
    assert s.weighted_mean == pytest.approx(0.4) and s.weighted_std == 0.0
    a, b = 0.2, 0.6
    s = index_summary(c, np.array([[a, b], [np.nan, 0.9]]), weight_grid_total=1.0)
    assert s.weighted_mean == pytest.approx(0.75 * a + 0.25 * b, abs=1e-15)  # 0.3
    assert s.weighted_std == pytest.approx(math.sqrt(0.75 * 0.25) * abs(a - b), abs=1e-15)
    assert s.n_eff == pytest.approx(1 / (0.75**2 + 0.25**2))  # 1.6
    # Through the conservative transfer: 10 m footprint cells, 0.75 inside the left map
    # cell and 0.25 inside the right one.
    fg = grid(0.0, 60.0, 10.0, 6, 3)
    w = np.zeros((3, 6))
    w[:, 0:3] = 0.75 / 9
    w[:, 3:6] = 0.25 / 9
    vals = np.array([[a, b], [np.nan, np.nan]])
    s2 = index_summary(transfer_matrix(fg, g).apply(w), vals, weight_grid_total=1.0)
    assert s2.weighted_mean == pytest.approx(0.3, abs=1e-12)
    assert s2.weighted_std == pytest.approx(math.sqrt(0.1875) * 0.4, abs=1e-12)
    assert s2.weights.weight_nodata == 0.0


def test_index_layer_rows_and_nodata_weight():
    g = grid(0.0, 60.0, 30.0, 2, 2)
    fg = grid(0.0, 60.0, 30.0, 2, 2)
    fp = footprint([[0.5, 0.25], [0.25, 0.0]], fg)
    days = [FootprintDay(pd.Timestamp("2024-06-03"), fp)]
    vals = np.array([[0.2, 0.6], [np.nan, 0.1]])
    res = assess_landscape(rspec(layers={"ndvi": idx_layer()}),
                           footprint_days={"SET": days},
                           layers={"ndvi": [asset(vals, g, "ndvi", start="2024-06-01",
                                                  end="2024-06-01", vintage=None)]})
    row = res.index_summaries.frame.iloc[0]
    assert row["weighted_mean"] == pytest.approx((0.5 * 0.2 + 0.25 * 0.6) / 0.75)
    assert row["weight_nodata"] == pytest.approx(0.25)
    assert row["valid_frac_of_grid_total"] == pytest.approx(0.75)
    assert row["days_from_layer"] == 2 and row["temporal_relation"] == "past"
    assert not row["retrospective"]
    assert "population form" in row["std_estimator"]


# ----------------------------------------------------------------------- contours


def test_contour_mask_by_hand_and_ties():
    w = np.array([[0.5, 0.25], [0.125, 0.125]])
    m = contour_mask(w, 0.75)
    assert m.tolist() == [[True, True], [False, False]]
    # 0.8 needs a 0.125 cell; both tied 0.125 cells are taken, so the mass is 1.0.
    assert contour_mask(w, 0.8).all()
    with pytest.raises(ValueError):
        contour_mask(w, 1.0)


def test_contour_rows_differ_from_full_domain_and_depend_on_footprint_only():
    # Map: left column class 82, right column 90; footprint 0.5 in 82, 0.5 in 90 but the
    # 90 half is spread (0.25, 0.125, 0.125).
    g = grid(0.0, 60.0, 30.0, 2, 2)
    lc_a = np.array([[82, 90], [90, 90]], float)
    lc_b = np.array([[90, 82], [82, 82]], float)  # a different map: same contour mask
    fp = footprint([[0.5, 0.25], [0.125, 0.125]], g)
    days = [FootprintDay(pd.Timestamp("2024-06-05"), fp)]
    out = {}
    for name, lc in (("a", lc_a), ("b", lc_b)):
        res = assess_landscape(rspec(contour_levels=(0.75,)), footprint_days={"SET": days},
                               layers={"lc": [asset(lc, g)]})
        out[name] = res.class_fractions.frame
    fa = out["a"][out["a"]["class_role"] == "class"]
    full = fa[fa["domain"] == "full_domain"].set_index("class_code")["class_frac_of_valid"]
    cont = fa[fa["domain"] == "contour_0.75"].set_index("class_code")["class_frac_of_valid"]
    assert full.to_dict() == pytest.approx({"82": 0.5, "90": 0.5})
    assert cont.to_dict() == pytest.approx({"82": 2 / 3, "90": 1 / 3})
    c_rows = fa[fa["domain"] == "contour_0.75"]
    assert (c_rows["weight_domain"] == 0.75).all() and (c_rows["weight_grid_total"] == 1.0).all()
    assert (c_rows["contour_level"] == 0.75).all()
    assert set(fa["domain"]) == {"full_domain", "contour_0.75"}  # both products kept
    # The contour mass and membership are the same under the other map.
    fb = out["b"]
    assert (fb[fb["domain"] == "contour_0.75"]["weight_domain"] == 0.75).all()
    fbc = fb[(fb["domain"] == "contour_0.75") & (fb["class_role"] == "class")]
    assert fbc.set_index("class_code")["class_frac_of_valid"].to_dict() == pytest.approx(
        {"82": 1 / 3, "90": 2 / 3})


# ----------------------------------------------------------------------- matching


ANNUAL = [("2023-01-01", "2023-12-31"), ("2024-01-01", "2024-12-31")]
SCENES = [("2024-06-01", "2024-06-01"), ("2024-06-17", "2024-06-17")]


def test_matching_policies_pick_expected_assets():
    near, prev = MatchingPolicySpec("nearest"), MatchingPolicySpec("previous_only")
    w30 = MatchingPolicySpec("within_n_days", n_days=30)
    m = match_asset("2024-06-05", ANNUAL, near)
    assert (m.asset_index, m.days_from_layer, m.temporal_relation, m.retrospective) == (
        1, 0, "contains_day", True)
    m = match_asset("2024-06-05", ANNUAL, prev)  # 2023 map; 157 days after 2023-12-31
    assert (m.asset_index, m.days_from_layer, m.retrospective) == (0, 157, False)
    m = match_asset("2025-03-01", ANNUAL, near)
    assert (m.asset_index, m.days_from_layer, m.temporal_relation) == (1, 60, "past")
    assert match_asset("2025-03-01", ANNUAL, w30) is None
    assert match_asset("2023-05-01", ANNUAL, prev) is None
    # Scenes: equal distance (8 days) goes to the past scene; 7 days ahead picks the future
    # one under nearest (retrospective) but the past one under previous_only.
    m = match_asset("2024-06-09", SCENES, near)
    assert (m.asset_index, m.days_from_layer, m.retrospective) == (0, 8, False)
    m = match_asset("2024-06-10", SCENES, near)
    assert (m.asset_index, m.days_from_layer, m.temporal_relation, m.retrospective) == (
        1, -7, "future", True)
    assert match_asset("2024-06-10", SCENES, prev).asset_index == 0
    assert match_asset("2024-06-10", SCENES, MatchingPolicySpec("within_n_days", n_days=5)) \
        is None
    m = match_asset("2024-06-17", SCENES, prev)  # same-day scene: not later information
    assert (m.asset_index, m.days_from_layer, m.retrospective) == (1, 0, False)


def test_days_without_admissible_asset_are_not_available_and_retrospective_labelled():
    g = grid(0.0, 60.0, 30.0, 2, 2)
    fp = footprint([[0.5, 0.25], [0.125, 0.125]], g)
    days = [FootprintDay(pd.Timestamp(d), fp) for d in ("2024-06-05", "2025-03-01")]
    lc = np.array([[82, 90], [90, 90]], float)
    pol = (MatchingPolicySpec("previous_only"), MatchingPolicySpec("within_n_days", n_days=30))
    res = assess_landscape(rspec(policies=pol), footprint_days={"SET": days},
                           layers={"lc": [asset(lc, g)]})  # only a 2024 map
    cf = res.class_fractions.frame
    got = {(p, d.date().isoformat()) for p, d in zip(cf["matching_policy"], cf["date"],
                                                     strict=True)}
    # previous_only: 2024-06-05 has no earlier map; 2025-03-01 uses 2024 (60 days, past).
    # within_30_days: 2024-06-05 uses 2024 (contains the day, retrospective); 2025-03-01
    # is 60 days away, so not available.
    assert got == {("previous_only", "2025-03-01"), ("within_30_days", "2024-06-05")}
    av = res.availability.frame
    na = av[av["reason_code"] == "no_admissible_asset"]
    assert set(zip(na["matching_policy"], na["date_label"], strict=True)) == {
        ("previous_only", "2024-06-05"), ("within_30_days", "2025-03-01")}
    retro = cf[cf["matching_policy"] == "within_30_days"]
    assert retro["retrospective"].all() and retro["flags"].str.contains(
        "retrospective_layer_match").all()
    assert not cf[cf["matching_policy"] == "previous_only"]["retrospective"].any()
    assert (cf[cf["matching_policy"] == "previous_only"]["year_mismatch"] == 1).all()
    summary = av[(av["status"] == "available") & (av["matching_policy"] == "within_30_days")]
    assert summary["flags"].iloc[0] == "policy_may_use_later_information"


# ----------------------------------------------------------------------- fixed supports


def test_fixed_supports_on_frozen_days_and_differences():
    g = grid(0.0, 120.0, 30.0, 4, 4)
    lc = np.array([[82, 82, 90, 90]] * 4, float)
    ref = grid(0.0, 120.0, 30.0, 4, 4)  # model grid = map grid here
    win = pixel_window_support(ref, 45.0, 75.0, 3)  # tower in cell (1, 1)
    near = nearest_pixel_support(ref, 45.0, 75.0)
    fg = grid(0.0, 120.0, 30.0, 4, 4)
    w = np.zeros((4, 4))
    w[1, 1], w[1, 2] = 0.75, 0.25
    days = [FootprintDay(pd.Timestamp("2024-06-05"), footprint(w, fg))]
    res = assess_landscape(rspec(), footprint_days={"SET": days}, layers={"lc": [asset(lc, g)]},
                           fixed_supports=[win, near])
    cf = res.class_fractions.frame
    w3 = cf[(cf["extraction_method"] == "pixel_window_3x3") & (cf["class_role"] == "class")]
    # 3x3 block rows 0-2, cols 0-2: 6 cells of 82, 3 of 90.
    assert w3.set_index("class_code")["class_frac_of_valid"].to_dict() == pytest.approx(
        {"82": 6 / 9, "90": 3 / 9})
    assert set(w3["footprint_set_id"]) == {"none"} and set(w3["day_set"]) == {"SET"}
    assert set(w3["domain"]) == {"full_support"}
    d = res.differences.frame.set_index(["compared_method", "quantity"])
    assert d.loc[("pixel_window_3x3", "class_frac_of_valid:82"), "difference"] == \
        pytest.approx(0.75 - 6 / 9)
    assert d.loc[("nearest_tower_pixel", "class_frac_of_valid:90"), "difference"] == \
        pytest.approx(0.25 - 0.0)  # class absent on the pixel side has share 0
    assert set(res.differences.frame["date"]) == {pd.Timestamp("2024-06-05")}


def test_window_partly_outside_grid_is_reported():
    ref = grid(0.0, 90.0, 30.0, 3, 3)
    s = pixel_window_support(ref, 5.0, 85.0, 3)  # corner pixel: 4 of 9 inside
    assert s.weights.shape == (2, 2) and np.allclose(s.weights, 0.25)
    assert "5 window pixel(s) outside the grid" in s.note


def test_polygon_support_exact_area_shares():
    pytest.importorskip("shapely")
    shapely = pytest.importorskip("shapely")
    from etcompare.representativeness import polygon_cell_fractions

    poly = shapely.box(45.0, 30.0, 75.0, 90.0)  # x 45..75 straddles x = 60
    frac = polygon_cell_fractions(poly, CRS, LC_GRID)
    assert frac.sum() == pytest.approx(1.0, abs=1e-12)
    comp = class_composition(frac, LC, nodata=NODATA, legend=LEGEND, weight_grid_total=1.0)
    assert comp.frac_of_valid() == pytest.approx({82: 0.5, 90: 0.5})


# ----------------------------------------------------------------------- labels


def _stub_labeler(level):
    def lab(contrib, classes, asset_, layer, tower_xy):
        return [{"radius_m": 250.0, "label": level, "dominant_class": 82, "p_footprint": 0.9,
                 "p_target": 0.9, "chi2": 0.0, "p_value": 1.0}]
    return lab


def test_labels_never_enter_numbers():
    fg = grid(25.0, 115.0, 10.0, 7, 9)
    days = [FootprintDay(pd.Timestamp("2024-06-05"), footprint(np.full((9, 7), 1 / 63), fg))]
    ref = grid(0.0, 120.0, 30.0, 4, 4)
    outs = []
    for level in ("high", "low"):
        src = LabelSource(_stub_labeler(level), "stub 0", {"layers": ("lc",)},
                          {"lc": (60.0, 60.0)})
        outs.append(assess_landscape(
            rspec(contour_levels=(0.8,)), footprint_days={"SET": days},
            layers={"lc": [asset()]}, fixed_supports=[pixel_window_support(ref, 60.0, 60.0, 3)],
            label_source=src))
    for name in ("class_fractions", "index_summaries", "differences"):
        pd.testing.assert_frame_equal(getattr(outs[0], name).frame, getattr(outs[1], name).frame)
    labs = outs[0].labels.frame
    fl = labs[labs["label_source"] == "fluxfootprints"]
    assert set(fl["label"]) == {"high"} and set(fl["domain"]) == {"full_domain", "contour_0.8"}
    assert fl["flags"].str.contains("label_not_accuracy_guarantee").all()
    assert "not an ET accuracy guarantee" in fl["label_meaning"].iloc[0]
    # No label column exists in the numeric tables.
    for name in ("class_fractions", "index_summaries", "differences"):
        assert "label" not in getattr(outs[0], name).frame.columns


def test_fluxfootprints_adapter_against_real_api():
    pytest.importorskip("fluxfootprints.representativeness")
    pytest.importorskip("pyproj")
    from etcompare.adapters.fluxfootprints_labels import categorical_labels, fluxfootprints_status

    st = fluxfootprints_status()
    assert st["available"] and st["version"]
    # 30 m map 41 x 41 around the tower at the centre; class 82 west of x = +45 m, 90 east.
    n = 41
    g = Grid(CRS, (30.0, 0.0, -615.0, 0.0, -30.0, 615.0), n, n)
    xc = -615.0 + 30.0 * (np.arange(n) + 0.5)
    lc = np.where(xc[None, :] < 45.0, 82.0, 90.0).repeat(n, axis=0)
    w = np.zeros((n, n))
    w[20, 19], w[20, 20], w[20, 22] = 0.5, 0.3, 0.2  # 0.8 on 82, 0.2 on 90 (x = 45..75)
    layer = cat_layer(legend={82: "crop", 90: "wet"})
    rows = categorical_labels(w, lc, g, layer, (0.0, 0.0), radii=(250.0,), alpha=0.05)
    assert len(rows) == 1
    r = rows[0]
    assert r["dominant_class"] == "82"
    assert r["p_footprint"] == pytest.approx(0.8, abs=1e-12)
    # Independent target share: cell centres within 250 m of the tower, share of class 82.
    X, Y = np.meshgrid(xc, xc[::-1])
    disc = np.hypot(X, Y) <= 250.0
    assert r["p_target"] == pytest.approx(float((lc[disc] == 82).mean()), abs=1e-12)
    from fluxfootprints.representativeness import classify_categorical

    assert r["label"] == str(classify_categorical(100 * r["p_footprint"], 100 * r["p_target"],
                                                  r["p_value"]).value)


def test_to_xy_orientation():
    pytest.importorskip("pyproj")
    from etcompare.adapters.fluxfootprints_labels import to_xy

    g = grid(0.0, 60.0, 30.0, 2, 2)
    a = np.array([[1.0, 2.0], [3.0, 4.0]])  # row 0 is north
    v, x, y = to_xy(g, a, (30.0, 30.0))
    assert x.tolist() == [-15.0, 15.0] and y.tolist() == [-15.0, 15.0]
    assert v[0, 1] == 1.0 and v[1, 0] == 4.0  # west-north = 1; east-south = 4


# ----------------------------------------------------------------------- tables


def test_tables_round_trip_parquet(tmp_path):
    fg = grid(25.0, 115.0, 10.0, 7, 9)
    days = [FootprintDay(pd.Timestamp("2024-06-05"), footprint(np.full((9, 7), 1 / 63), fg))]
    ref = grid(0.0, 120.0, 30.0, 4, 4)
    vals = np.linspace(0.1, 0.8, 16).reshape(4, 4)
    res = assess_landscape(rspec(layers={"lc": cat_layer(), "ndvi": idx_layer()},
                                 contour_levels=(0.8,)), footprint_days={"SET": days},
                           layers={"lc": [asset()],
                                   "ndvi": [asset(vals, LC_GRID, "ndvi", start="2024-06-01",
                                                  end="2024-06-01", vintage=None)]},
                           fixed_supports=[pixel_window_support(ref, 60.0, 60.0, 3)],
                           footprint_unusable={"SET": {pd.Timestamp("2024-06-06"):
                                                       "footprint_negative_weights"}},
                           station_land_cover="CRO")
    for name, t in res.tables().items():
        assert len(t.frame), name
        back = read_table(write_table(t, tmp_path / f"{name}.parquet"))
        pd.testing.assert_frame_equal(back.frame, t.frame)
    av = res.availability.frame
    assert ((av["analysis"] == "footprint") & (av["date_label"] == "2024-06-06")).any()


# ----------------------------------------------------------------------- config


BASE = {
    "stations": {"US-AAA": {"timezone": "UTC-07:00", "day_definition": "local_standard_time",
                            "interval_label": "start"}},
    "footprint_sets": [{"set_id": "SET", "root": ".", "weighting_method": "m",
                        "value_semantics": "normalized_cell_weight"}],
}


def rep_body(**kw):
    body = {"representativeness_id": "rep", "site_id": "US-AAA", "footprint_sets": ["SET"],
            "matching_policies": ["nearest"], "contour_levels": [0.8],
            "layers": {"lc": {"kind": "categorical", "source": "s", "crs": CRS, "nodata": 250,
                              "legend": {82: "crop"},
                              "assets": [{"path": "a.tif", "vintage": 2024}]}}}
    body.update(kw)
    return body


def lay(**kw):
    d = rep_body()["layers"]["lc"]
    d.update(kw)
    return {"lc": d}


def ndvi(**kw):
    d = {"kind": "continuous", "source": "s", "crs": CRS, "nodata": -9999, "units": "1",
         "scale_factor": 1.0, "add_offset": 0.0, "acquisition_date_source": "tag",
         "assets": [{"path": "n.tif", "acquisition_date": "2024-06-01"}]}
    d.update(kw)
    return {"ndvi": d}


def test_config_accepts_example_body():
    cfg = config_from_dict({**BASE, "representativeness": [rep_body()]})
    r = cfg.representativeness_spec("rep")
    assert r.layers["lc"].assets[0].period == ("2024-01-01", "2024-12-31")
    assert r.matching_policies[0].policy_id == "nearest"


@pytest.mark.parametrize("body, match", [
    (rep_body(layers=lay(legend=None)), "needs a class legend"),
    (rep_body(layers=lay(assets=[{"path": "a.tif"}])), "vintage not declared"),
    (rep_body(layers=ndvi(assets=[{"path": "n.tif"}])), "acquisition_date not declared"),
    (rep_body(matching_policies=["closest"]), "not one of"),
    (rep_body(matching_policies=[]), "no matching policy declared"),
    (rep_body(matching_policies=[{"policy": "within_n_days"}]), "needs n_days"),
    (rep_body(contour_levels=[1.0]), r"must lie in \(0, 1\)"),
    (rep_body(contour_levels=[0.0]), r"must lie in \(0, 1\)"),
    (rep_body(layers=lay(nodata=None)), "nodata not declared"),
    (rep_body(layers=ndvi(nodata=None)), "nodata not declared"),
    (rep_body(layers=lay(crs=None)), "crs not declared"),
    (rep_body(layers=ndvi(units=None)), "units not declared"),
    (rep_body(layers=ndvi(acquisition_date_source=None)), "acquisition_date_source"),
    (rep_body(layers=lay(assets=[{"path": "a.tif", "vintage": 2024},
                                 {"path": "b.tif", "vintage": 2024}])), "overlap"),
    (rep_body(methods=["footprint", "pixel_window"]), "reference_grid_raster"),
    (rep_body(methods=["pixel_window"], reference_grid_raster="r.tif"), "include footprint"),
    (rep_body(methods=["footprint", "polygon"]), "polygons must be declared together"),
    (rep_body(footprint_sets=["NOPE"]), "not declared"),
    (rep_body(site_id="US-ZZZ"), "not declared"),
    (rep_body(min_level="high"), "unknown configuration key"),
    (rep_body(fluxfootprints_labels={"layers": ["lc"], "radii": [250]}), "alpha"),
])
def test_config_rejections(body, match):
    with pytest.raises(ContractError, match=match):
        config_from_dict({**BASE, "representativeness": [body]})


def test_polygon_needs_crs_and_vintage():
    poly = {"polygon_id": "field", "path": "f.gpkg", "id_column": "fieldid", "feature_id": 1,
            "source": "s", "vintage": "unknown"}
    with pytest.raises(ContractError, match="crs not declared"):
        config_from_dict({**BASE, "representativeness": [rep_body(methods=["footprint", "polygon"],
                                                                  polygons=[poly])]})
    ok = config_from_dict({**BASE, "representativeness": [rep_body(
        methods=["footprint", "polygon"], polygons=[{**poly, "crs": "EPSG:26912"}])]})
    assert ok.representativeness[0].method_ids() == ["polygon_field"]
