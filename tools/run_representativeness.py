"""Landscape representativeness of the declared footprint sets (guide step 13).

Stand-in until the step-17 CLI; needs the ``geo`` extra (and the ``footprints`` extra for
the optional fluxfootprints labels). For each declared ``representativeness`` analysis
(all by default) it reads the daily footprints of its footprint sets, each layer asset on
a window of its native grid, the fixed supports (model-grid pixel window and nearest
pixel; declared polygons), and writes into <output_dir>/representativeness/:

  <id>_class_fractions.parquet / .csv    landscape_class_fractions
  <id>_index_summaries.parquet / .csv    landscape_index_summaries
  <id>_differences.parquet / .csv        landscape_method_differences (frozen days)
  <id>_labels.parquet / .csv             station land-cover label and fluxfootprints labels
  <id>_availability.parquet / .csv       what ran, per day where relevant, and why not
  <id>_report.txt                        inputs, settings and a summary

Fractions and labels describe the landscape of each support; they are not ET accuracy
guarantees. Satellite support needs cohorts and is written by tools/run_cohorts.py.

    uv run python tools/run_representativeness.py examples/us_utj.yaml [id ...]
"""
from __future__ import annotations

import argparse
import time

import numpy as np
import pandas as pd

from etcompare.adapters.fluxfootprints_labels import (
    categorical_labels,
    fluxfootprints_status,
    upstream_parameters,
    version_label,
)
from etcompare.config import RepresentativenessSpec, load_config, to_plain
from etcompare.errors import ContractError
from etcompare.footprints import prepare_footprint
from etcompare.io.manifest import discover_footprints
from etcompare.io.rasters import read_footprint, read_header
from etcompare.io.tables import write_table
from etcompare.provenance import file_sha256, package_versions
from etcompare.registry import SiteRegistry
from etcompare.representativeness import (
    FOOTPRINT_METHOD,
    FootprintDay,
    LabelSource,
    assess_landscape,
    nearest_pixel_support,
    pixel_window_support,
    polygon_support,
    read_layer_asset,
)
from etcompare.spatial import same_crs, transform_coords

VERSIONED = ("etcompare", "numpy", "pandas", "scipy", "rasterio", "pyproj", "shapely",
             "geopandas", "fluxfootprints")


def _tower(lon: float, lat: float, crs: str) -> tuple[float, float]:
    x, y = transform_coords(np.array([lon]), np.array([lat]), "EPSG:4326", crs)
    return float(x[0]), float(y[0])


def run(cfg, spec: RepresentativenessSpec) -> tuple[object, list[str]]:
    station = cfg.stations[spec.site_id]
    lon, lat = station.longitude, station.latitude
    if lon is None or lat is None:
        raise ContractError(f"station {spec.site_id}: coordinates needed",
                            config_key=f"stations.{spec.site_id}.latitude")
    notes: list[str] = []
    unavailable: list[dict] = []

    # ---- footprints
    days: dict[str, list[FootprintDay]] = {}
    unusable: dict[str, dict[pd.Timestamp, str]] = {}
    extent = 0.0
    for set_id in spec.footprint_sets:
        fset = cfg.footprint_set(set_id)
        fd = discover_footprints(fset, sites=SiteRegistry(cfg.site_aliases))
        man = fd.manifest.frame
        man = man[(man["site_id"] == spec.site_id) & (man["period"] == "day")]
        if spec.start:
            man = man[man["date"] >= pd.Timestamp(spec.start)]
        if spec.end:
            man = man[man["date"] <= pd.Timestamp(spec.end)]
        days[set_id], unusable[set_id] = [], {}
        for r in man.sort_values("date").itertuples():
            raw, grid, nod = read_footprint(r.path)
            fp = prepare_footprint(raw, grid, value_semantics=fset.value_semantics, nodata=nod,
                                   negative_tolerance=spec.negative_weight_tolerance)
            if fp.ok:
                days[set_id].append(FootprintDay(pd.Timestamp(r.date), fp, r.asset_id))
                tx, ty = _tower(lon, lat, grid.crs)
                cx, cy = grid.corners()
                extent = max(extent, float(np.hypot(cx - tx, cy - ty).max()))
            else:
                unusable[set_id][pd.Timestamp(r.date)] = f"footprint_{fp.status}"
        notes.append(f"footprint set {set_id}: {len(days[set_id])} usable days, "
                     f"{len(unusable[set_id])} unusable ({fset.weighting_method}; "
                     f"{fset.value_semantics})")

    # ---- layers, read on windows of their native grids
    flux = spec.fluxfootprints_labels
    half = max(extent, max(flux["radii"]) if flux else 0.0) + 60.0
    layers: dict[str, list] = {}
    tower_xy: dict[str, tuple[float, float]] = {}
    for name, layer in spec.layers.items():
        got = []
        for i, a in enumerate(layer.assets):
            try:
                got.append(read_layer_asset(layer, i, x=lon, y=lat, xy_crs="EPSG:4326",
                                            half_width_m=half))
            except OSError as exc:
                unavailable.append({"analysis": "landscape_layer", "layer": name,
                                    "code": "layer_unreadable", "reason": f"{a.path}: {exc}"})
                got = []
                break
            notes.append(f"layer {name} asset {a.path.name}: sha256 {file_sha256(a.path)[:16]}, "
                         f"reference {a.period[0]}..{a.period[1]}, window "
                         f"{got[-1].grid.height}x{got[-1].grid.width} cells")
        if got:
            layers[name] = got
            tower_xy[name] = _tower(lon, lat, got[0].grid.crs)

    # ---- fixed supports
    supports = []
    pixel = {"pixel_window", "nearest_tower_pixel"} & set(spec.methods)
    if pixel:
        try:
            ref = read_header(spec.reference_grid_raster).grid
        except OSError as exc:
            for m in spec.method_ids():
                if not m.startswith("polygon_"):
                    unavailable.append({"analysis": "method_difference", "method": m,
                                        "code": "reference_grid_unreadable", "reason": str(exc)})
            ref = None
        if ref is not None:
            if spec.reference_grid_id and ref.grid_id != spec.reference_grid_id:
                raise ContractError(f"reference grid {ref.grid_id} != declared "
                                    f"{spec.reference_grid_id}",
                                    config_key="representativeness.reference_grid_id")
            tx, ty = _tower(lon, lat, ref.crs)
            if "pixel_window" in spec.methods:
                supports += [pixel_window_support(ref, tx, ty, n) for n in spec.window_sizes]
            if "nearest_tower_pixel" in spec.methods:
                supports.append(nearest_pixel_support(ref, tx, ty))
            notes.append(f"reference grid {ref.grid_id} ({ref.crs}) from "
                         f"{spec.reference_grid_raster}")
    for p in spec.polygons:
        import geopandas as gpd

        gdf = gpd.read_file(p.path, layer=p.layer)
        if gdf.crs is None:
            notes.append(f"polygon {p.polygon_id}: file has no CRS; declared {p.crs} used")
        elif not same_crs(gdf.crs.to_wkt(), p.crs):
            raise ContractError(f"polygon {p.polygon_id}: file CRS differs from declared {p.crs}")
        sel = gdf[gdf[p.id_column].astype(str) == p.feature_id]
        if len(sel) != 1:
            raise ContractError(f"polygon {p.polygon_id}: {len(sel)} features with "
                                f"{p.id_column} = {p.feature_id}")
        s = polygon_support(p.polygon_id, sel.geometry.iloc[0], p.crs)
        supports.append(s)
        tx, ty = _tower(lon, lat, p.crs)
        import shapely

        inside = bool(shapely.contains_xy(sel.geometry.iloc[0], tx, ty))
        notes.append(f"polygon {p.polygon_id}: {p.id_column}={p.feature_id}, area "
                     f"{sel.geometry.iloc[0].area:.0f} m2 (in {p.crs}), tower inside: {inside}, "
                     f"vintage {p.vintage}")

    # ---- optional fluxfootprints labels
    label_source = None
    if flux:
        st = fluxfootprints_status()
        notes.append(f"fluxfootprints: {st}")
        if not st["available"]:
            for name in flux["layers"]:
                unavailable.append({"analysis": "fluxfootprints_labels", "layer": name,
                                    "code": "fluxfootprints_not_available",
                                    "reason": st["reason"]})
        else:
            radii, alpha = flux["radii"], flux["alpha"]

            def labeler(contrib, classes, asset, layer, txy):
                return categorical_labels(contrib, classes, asset.grid, layer, txy, radii=radii,
                                          alpha=alpha)

            label_source = LabelSource(labeler, version_label(st),
                                       {"layers": flux["layers"],
                                        **upstream_parameters(radii, alpha)},
                                       {k: v for k, v in tower_xy.items()})

    t = time.time()
    res = assess_landscape(spec, footprint_days=days, layers=layers, fixed_supports=supports,
                           footprint_unusable=unusable, station_land_cover=station.land_cover,
                           label_source=label_source, unavailable=unavailable)
    notes.append(f"assess_landscape: {time.time() - t:.0f} s")
    return res, notes


def summary(res) -> list[str]:
    out = []
    cf = res.class_fractions.frame
    if len(cf):
        cls = cf[cf["class_role"] == "class"]
        g = cls.groupby(["layer_id", "matching_policy", "extraction_method", "domain",
                         "class_code", "class_name"])["class_frac_of_valid"]
        tab = g.agg(["mean", "min", "max", "size"]).reset_index()
        tab = tab[tab["mean"] >= 0.005]
        out.append("Class share of valid weight (mean / min / max over days):")
        out.append(tab.to_string(index=False, float_format=lambda v: f"{v:.3f}"))
        den = cf.drop_duplicates(["layer_id", "matching_policy", "extraction_method", "domain",
                                  "date"])
        d = den.groupby(["layer_id", "matching_policy", "extraction_method", "domain"]).agg(
            n_days=("date", "size"), valid=("valid_frac_of_grid_total", "mean"),
            nodata=("nodata_frac_of_grid_total", "mean"),
            outside=("outside_layer_frac_of_grid_total", "mean"),
            domain_frac=("domain_frac_of_grid_total", "mean"),
            retro=("retrospective", "mean"), year_mismatch_max=("year_mismatch", "max"))
        out.append("Denominators (means over days):")
        out.append(d.reset_index().to_string(index=False, float_format=lambda v: f"{v:.4f}"))
    df = res.differences.frame
    if len(df):
        q = df[df["quantity_kind"] == "class_fraction"]
        g = q.groupby(["layer_id", "matching_policy", "domain", "compared_method", "quantity"])[
            "difference"].describe(percentiles=[0.05, 0.5, 0.95])
        g = g[(g["mean"].abs() >= 0.005) | (g["max"].abs() >= 0.05)]
        out.append("Footprint minus fixed support, class share of valid weight (frozen days):")
        out.append(g.reset_index().to_string(index=False, float_format=lambda v: f"{v:.3f}"))
    lab = res.labels.frame
    if len(lab):
        fl = lab[lab["label_source"] == "fluxfootprints"]
        if len(fl):
            t = fl.groupby(["layer_id", "matching_policy", "domain", "radius_m", "label"]).size()
            out.append("fluxfootprints labels (days per label; labels, not ET accuracy):")
            out.append(t.unstack("label", fill_value=0).to_string())
        st = lab[lab["label_source"] == "station_metadata"]
        out += [f"station label: {r.label} ({r.label_meaning})" for r in st.itertuples()]
    av = res.availability.frame
    na = av[av["status"] == "not_available"]
    if len(na):
        out.append("Not available:")
        out.append(na.groupby(["analysis", "layer_id", "extraction_method", "matching_policy",
                               "reason_code"]).size().rename("rows").reset_index().to_string(
                                   index=False))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("config")
    ap.add_argument("ids", nargs="*", help="representativeness ids (default: all declared)")
    args = ap.parse_args()
    cfg = load_config(args.config)
    out = cfg.output_dir / "representativeness"  # type: ignore[operator]
    out.mkdir(parents=True, exist_ok=True)
    specs = [cfg.representativeness_spec(i) for i in args.ids] or list(cfg.representativeness)
    for spec in specs:
        t0 = time.time()
        res, notes = run(cfg, spec)
        rid = spec.representativeness_id
        names = {"landscape_class_fractions": "class_fractions",
                 "landscape_index_summaries": "index_summaries",
                 "landscape_method_differences": "differences",
                 "representativeness_labels": "labels",
                 "representativeness_availability": "availability"}
        for k, t in res.tables().items():
            write_table(t, out / f"{rid}_{names[k]}.parquet")
            t.frame.to_csv(out / f"{rid}_{names[k]}.csv", index=False)
        text = "\n".join([
            f"representativeness {rid} ({spec.site_id})",
            "Landscape description of each support; not an ET accuracy guarantee.",
            f"settings: {to_plain(spec)}",
            f"versions: {package_versions(*VERSIONED)}",
            *notes, "", *summary(res), "", res.report.summary(),
            f"footprint method: {FOOTPRINT_METHOD}", f"elapsed {time.time() - t0:.0f} s"])
        (out / f"{rid}_report.txt").write_text(text + "\n", encoding="utf-8")
        print(text)


if __name__ == "__main__":
    main()
