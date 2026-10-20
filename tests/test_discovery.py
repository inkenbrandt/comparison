from pathlib import Path

import pandas as pd
import pytest

from etcompare.config import FootprintSetSpec, ProductSpec
from etcompare.errors import DuplicateAssetError, FilenameParseError
from etcompare.io.manifest import (
    discover_footprints,
    discover_openet_rasters,
    load_model_manifest,
    select_assets,
)
from etcompare.io.tables import write_table
from etcompare.registry import ModelRegistry, SiteRegistry

MODELS = ModelRegistry.default()
PRODUCTS = {m: ProductSpec(m, units="mm/day", day_definition="unknown",
                           temporal_support="interpolated_daily",
                           is_ensemble=(m == "ensemble")) for m in MODELS.models}


def touch(root: Path, *names: str) -> list[Path]:
    out = []
    for n in names:
        p = root / n
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"")  # discovery never opens rasters, so empty files suffice
        out.append(p)
    return out


def test_multi_and_single_site_assets(tmp_path):
    touch(tmp_path,
          "multi/XX-UTD_XX-UTG_XX-UTM_XX-UTW_ensemble_et_2021_01_01_9abcf.tif",
          "multi/XX-UTD_XX-UTG_XX-UTM_XX-UTW_ensemble_et_2021_01_01_9abcf.tif.aux.xml",
          "multi/XX-UTD_XX-UTG_XX-UTM_XX-UTW_ensemble_et_2021_01_01_9abcf.tif.ovr",
          "single/XX-UTE_sims_et_2021_01_02_00aa1.tif")
    r = discover_openet_rasters(tmp_path, models=MODELS, products=PRODUCTS)
    m = r.manifest.frame
    assert len(m) == 2 and r.report.ok and r.conflicts.empty
    multi = m.set_index("asset_id").loc["XX-UTD_XX-UTG_XX-UTM_XX-UTW_ensemble_et_2021_01_01_9abcf"]
    assert multi["n_sites"] == 4 and multi["member_sites"] == "XX-UTD;XX-UTG;XX-UTM;XX-UTW"
    assert multi["scaling_source"] == "not_read" and pd.isna(multi["crs"])
    assert multi["temporal_support"] == "interpolated_daily"
    assoc = r.associations.frame
    assert len(assoc) == 5  # one asset opened once, associated with four sites
    assert set(assoc["site_id"]) == {"XX-UTD", "XX-UTG", "XX-UTM", "XX-UTW", "XX-UTE"}


def test_non_recursive(tmp_path):
    touch(tmp_path, "XX-UTE_sims_et_2021_01_02_00aa1.tif",
          "sub/XX-UTE_sims_et_2021_01_03_00aa1.tif")
    assert len(discover_openet_rasters(tmp_path, models=MODELS, products=PRODUCTS,
                                       recursive=False).manifest) == 1


def test_unparseable_error_or_skip(tmp_path):
    touch(tmp_path, "XX-UTE_sims_et_2021_01_02_00aa1.tif", "XX-UTE_sims_et_2021_02_31_00aa1.tif")
    with pytest.raises(FilenameParseError, match="invalid date"):
        discover_openet_rasters(tmp_path, models=MODELS, products=PRODUCTS)
    r = discover_openet_rasters(tmp_path, models=MODELS, products=PRODUCTS,
                                on_unparseable="skip")
    assert len(r.manifest) == 1
    assert "unparseable_filenames" in r.report.codes("warning")
    assert list(r.exclusions.to_frame()["reason_code"]) == ["unparseable_filename"]


def test_duplicate_asset_same_name_two_dirs(tmp_path):
    touch(tmp_path, "a/XX-UTE_sims_et_2021_01_02_00aa1.tif",
          "archive/XX-UTE_sims_et_2021_01_02_00aa1.tif")
    with pytest.raises(DuplicateAssetError, match="duplicate_asset"):
        discover_openet_rasters(tmp_path, models=MODELS, products=PRODUCTS)


def test_two_assets_same_site_model_day_need_selection(tmp_path):
    keep, drop = touch(tmp_path, "XX-UTE_sims_et_2021_01_02_00aa1.tif",
                       "XX-UTE_XX-UTB_sims_et_2021_01_02_bb222.tif")
    touch(tmp_path, "XX-UTE_sims_et_2021_01_03_00aa1.tif")
    with pytest.raises(DuplicateAssetError, match="conflicting_assets"):
        discover_openet_rasters(tmp_path, models=MODELS, products=PRODUCTS)

    r = discover_openet_rasters(tmp_path, models=MODELS, products=PRODUCTS, on_conflict="report")
    assert not r.report.ok
    assert len(r.manifest) == 1  # conflicting candidates held out, never averaged
    assert set(r.conflicts["path"]) == {str(keep), str(drop)}

    with pytest.raises(DuplicateAssetError, match="exactly one"):
        select_assets(r, [keep, drop])
    s = select_assets(r, [keep])
    assert s.report.ok and len(s.manifest) == 2 and s.conflicts.empty
    ex = s.exclusions.to_frame()
    assert list(ex["reason_code"]) == ["not_selected"] and ex["input_key"][0] == str(drop)


def test_explicit_manifest_round_trip(tmp_path):
    touch(tmp_path, "XX-UTE_sims_et_2021_01_02_00aa1.tif",
          "XX-UTD_XX-UTG_ssebop_et_2021_01_02_00aa1.tif")
    r = discover_openet_rasters(tmp_path, models=MODELS, products=PRODUCTS)
    pq = write_table(r.manifest, tmp_path / "manifest.parquet")
    back = load_model_manifest(pq)
    pd.testing.assert_frame_equal(back.manifest.frame, r.manifest.frame)
    csv = tmp_path / "manifest.csv"
    r.manifest.frame.assign(date=r.manifest.frame["date"].dt.strftime("%Y-%m-%d")).to_csv(
        csv, index=False)
    back_csv = load_model_manifest(csv)
    pd.testing.assert_frame_equal(back_csv.manifest.frame, r.manifest.frame)
    (tmp_path / "XX-UTE_sims_et_2021_01_02_00aa1.tif").unlink()
    assert "missing_files" in load_model_manifest(pq).report.codes("error")


def test_footprint_set_discovery(tmp_path):
    root = tmp_path / "XX-UTJ"
    touch(root, "ffp_XX-UTJ_daily_etw_20240605.tif", "ffp_XX-UTJ_monthly_etw_202406.tif",
          "ffp_XX-UTJ_daily_mean_20240605.tif",
          "weighted/ffp_XX-UTJ_daily_etw_20240605.tif")  # set B shares stems
    spec = FootprintSetSpec("A", root, weighting_method="et_weighted_unverified",
                            value_semantics="normalized_cell_weight", layers=("etw",))
    r = discover_footprints(spec)
    f = r.manifest.frame
    assert sorted(f["period"]) == ["day", "month"]
    assert set(f["weighting_method"]) == {"et_weighted_unverified"}
    assert f["domain_capture_frac"].isna().all()
    assert "layers_not_selected" in r.report.codes("info")

    recursive = FootprintSetSpec("A", root, weighting_method="x",
                                 value_semantics="normalized_cell_weight", recursive=True)
    with pytest.raises(DuplicateAssetError):
        discover_footprints(recursive)


def test_site_alias_recorded_in_manifest(tmp_path):
    touch(tmp_path, "XX-UTD_US_UTM_eemetric_et_2021_01_01_9abcf.tif")
    r = discover_openet_rasters(tmp_path, models=MODELS, products=PRODUCTS,
                                sites=SiteRegistry(aliases={"US_UTM": "XX-UTM"}))
    row = r.manifest.frame.iloc[0]
    assert row["member_sites"] == "XX-UTD;XX-UTM"
    assert row["site_aliases_applied"] == "US_UTM=XX-UTM"
    assert r.manifest.attributes["site_aliases_applied"] == ["US_UTM=XX-UTM"]


def write(root: Path, name: str, content: bytes) -> Path:
    p = root / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)
    return p


def test_declared_processing_tag_is_parsed_and_others_refused(tmp_path):
    touch(tmp_path, "XX-UTJ_sims_et_2025_01_01_d04f3_buffer400m_clip.tif")
    with pytest.raises(FilenameParseError):
        discover_openet_rasters(tmp_path, models=MODELS, products=PRODUCTS)
    r = discover_openet_rasters(tmp_path, models=MODELS, products=PRODUCTS,
                                processing_tags=("buffer400m_clip",))
    m = r.manifest.frame
    assert len(m) == 1 and m["model"][0] == "sims" and m["member_sites"][0] == "XX-UTJ"
    assert r.manifest.attributes["processing_tags"] == ["buffer400m_clip"]
    touch(tmp_path, "XX-UTJ_sims_et_2025_01_02_d04f3_buffer500m_clip.tif")
    with pytest.raises(FilenameParseError):
        discover_openet_rasters(tmp_path, models=MODELS, products=PRODUCTS,
                                processing_tags=("buffer400m_clip",))


def test_collapse_identical_keeps_one_copy_and_holds_out_differing(tmp_path):
    a = write(tmp_path, "XX-UTW_sims_et_2021_01_01_7ab87.tif", b"same bytes")
    b = write(tmp_path, "XX-UTW_sims_et_2021_01_01_9a039.tif", b"same bytes")
    c = write(tmp_path, "XX-UTE_geesebal_et_2025_01_18_aa6bd.tif", b"one")
    d = write(tmp_path, "XX-UTE_geesebal_et_2025_01_18_aec2f.tif", b"two")
    write(tmp_path, "XX-UTE_geesebal_et_2025_01_19_aa6bd.tif", b"single")
    r = discover_openet_rasters(tmp_path, models=MODELS, products=PRODUCTS,
                                on_conflict="collapse_identical")
    paths = set(r.manifest.frame["path"])
    assert str(a) in paths and str(b) not in paths              # identical: first path kept
    assert str(c) not in paths and str(d) not in paths          # differing: both held out
    assert len(paths) == 2
    assert r.report.ok and "conflicting_assets_held_out" in r.report.codes("warning")
    assert set(r.conflicts["path"]) == {str(c), str(d)}
    ex = r.exclusions.to_frame().set_index("input_key")
    assert ex.loc[str(b), "reason_code"] == "identical_duplicate_collapsed"
    assert str(a) in ex.loc[str(b), "detail"]
    assert set(ex.loc[[str(c), str(d)], "reason_code"]) == {"conflicting_assets_held_out"}
    assert r.manifest.attributes["on_conflict"] == "collapse_identical"
