"""Per-timestep footprints from the user's fluxfootprints model (upstream, not etcompare core).

Runs with the footprints repository's environment and reproduces the inputs of
footprint_workflow_v2-1.ipynb, but stops before temporal aggregation and saves every
half-hour footprint. etcompare then aggregates the same stack with each temporal
weight (decisions.md D-036), so the weightings differ only in a_t.

Inputs (all read-only):
  --metflux  half-hourly metflux_qc export, DATETIME_END index (end-labelled, UTC-7)
  --params   daily_footprint_parameters export (tools/upstream/fetch_footprint_parameters.py)
  --nldas    hourly NLDAS grass ETo, timestamp_local = hour start (as production assumes)

Choices copied from production (footprint_workflow_v2-1.ipynb) unless stated:
  - interval start = DATETIME_END - 30 min; hours 08-20 of the start label (Volk 2023)
  - zm = effective_measurement_height, z0 = roughness_length (DB, alfalfa preset; checked
    against compute_aerodynamic_params) forward-filled from the daily rows
  - NLDAS ETo hourly -> 30 min by linear interpolation of the start-labelled series
  - ffp_xr, h = 2000 m, dx = dy = 10 m, domain +/-600 m, rslayer=False, smoothing on
  - LE_1_1_1 (W m-2) is saved unchanged as the measured weight series

Output per month: <out>/<site>_<YYYYMM>_timesteps.npz with
  f        (T, rows, cols) float32 density per m2, north-up raster order
  time     interval-start stamps (datetime64[s]); duration_s
  nldas_eto_mm_h, le_w_m2   weights aligned with time (NaN where missing)
  transform (6 affine terms), crs (EPSG string)

    ../footprints/.venv/Scripts/python.exe tools/upstream/run_timestep_footprints.py \
        --site US-UTJ --lat 37.281746 --lon -109.53489 --metflux ... --params ... --nldas ... \
        --out outputs/footprint_timesteps/US-UTJ [--months 2025-07]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

FOOTPRINTS_SRC = Path(__file__).resolve().parents[3] / "footprints" / "src"
REQUIRED = ["mo_length_1_1_1", "v_sigma_1_1_1", "ustar_1_1_1", "wd_1_1_1", "ws_1_1_1"]
START_HOUR, END_HOUR = 8, 20
DX = DY = 10.0
DOMAIN = (-600.0, 600.0, -600.0, 600.0)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_inputs(args: argparse.Namespace, compute_aerodynamic_params) -> pd.DataFrame:
    cols = [c.upper() for c in REQUIRED] + ["LE_1_1_1", "STATIONID"]
    met = pd.read_parquet(args.metflux, columns=cols)
    if met.index.name != "DATETIME_END" or met.index.tz is not None:
        raise SystemExit("metflux export must have a naive DATETIME_END index")
    if set(met["STATIONID"].unique()) != {args.site}:
        raise SystemExit(f"metflux export is not only {args.site}")
    met = met.drop(columns="STATIONID")
    met.columns = [c.lower() for c in met.columns]
    met.index = met.index - pd.Timedelta("30min")
    met.index.name = "datetime_start"
    if met.index.has_duplicates or not met.index.is_monotonic_increasing:
        raise SystemExit("metflux timestamps duplicated or unsorted")
    met = met.replace(-9999, np.nan)

    p = pd.read_parquet(args.params)
    p["height_date"] = pd.to_datetime(p["height_date"])
    p = p.set_index("height_date").sort_index()
    if p.index.has_duplicates:
        raise SystemExit("daily_footprint_parameters has duplicate dates")
    p = p[["crop_height_m", "measurement_height", "effective_measurement_height",
           "roughness_length", "cover_type"]]
    # Forward-fill daily rows onto the half-hours, as production does.
    p30 = p.reindex(p.index.union(met.index)).ffill().reindex(met.index)
    df = met.join(p30)
    df["zm"] = df["effective_measurement_height"].astype(float)
    df["z0"] = df["roughness_length"].astype(float)
    check = compute_aerodynamic_params(df[["measurement_height", "crop_height_m"]].dropna(),
                                       inst_height="measurement_height",
                                       crop_height="crop_height_m", veg_type=args.veg_type)
    j = df.loc[check.index]
    dz = float(np.nanmax(np.abs(check["zm"] - j["zm"])))
    d0 = float(np.nanmax(np.abs(check["z0"] - j["z0"])))
    print(f"zm/z0 vs compute_aerodynamic_params({args.veg_type}): max |dzm| {dz:.2e}, "
          f"|dz0| {d0:.2e} (diagnostic only; zm/z0 are the DB values)")

    n = pd.read_csv(args.nldas, parse_dates=["timestamp_local"]).set_index("timestamp_local")
    if not n.index.is_unique or (n.index.to_series().diff().dropna() != pd.Timedelta("1h")).any():
        raise SystemExit("NLDAS series is not a unique, gap-free hourly series")
    eto = n["eto_grass"].astype(float)
    eto30 = eto.resample("30min").interpolate()
    df["nldas_eto_mm_h"] = eto30.reindex(df.index)

    df = df[(df.index >= args.start) & (df.index <= args.end)]
    df = df[(df.index.hour >= START_HOUR) & (df.index.hour <= END_HOUR)]
    return df


def grid_of(model, lat: float, lon: float) -> tuple[tuple[float, ...], str, bool]:
    """Georeference like fluxfootprints.export_rasters_geotiff (local UTM, cell centres)."""
    from pyproj import CRS, Transformer
    from pyproj.aoi import AreaOfInterest
    from pyproj.database import query_utm_crs_info

    info = query_utm_crs_info(datum_name="WGS 84", area_of_interest=AreaOfInterest(
        west_lon_degree=lon, south_lat_degree=lat, east_lon_degree=lon, north_lat_degree=lat))
    epsg = int(info[0].code)
    x0, y0 = Transformer.from_crs(CRS.from_epsg(4326), CRS.from_epsg(epsg),
                                  always_xy=True).transform(lon, lat)
    x, y = np.asarray(model.x), np.asarray(model.y)
    xres, yres = float(abs(x[1] - x[0])), float(abs(y[1] - y[0]))
    left, top = x.min() + x0 - xres / 2, y.max() + y0 + yres / 2
    return (xres, 0.0, left, 0.0, -yres, top), f"EPSG:{epsg}", bool(y[1] > y[0])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", required=True)
    ap.add_argument("--lat", type=float, required=True)
    ap.add_argument("--lon", type=float, required=True)
    ap.add_argument("--metflux", type=Path, required=True)
    ap.add_argument("--params", type=Path, required=True)
    ap.add_argument("--nldas", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--start", default="2024-06-05")
    ap.add_argument("--end", default="2025-10-09 23:59")
    ap.add_argument("--months", nargs="*")
    ap.add_argument("--veg-type", default="alfalfa",
                    help="fluxfootprints preset for the zm/z0 cross-check only")
    args = ap.parse_args()

    sys.path.insert(0, str(FOOTPRINTS_SRC))
    from fluxfootprints import build_climatology, compute_aerodynamic_params

    args.out.mkdir(parents=True, exist_ok=True)
    df = build_inputs(args, compute_aerodynamic_params)
    months = pd.period_range(df.index.min(), df.index.max(), freq="M")
    if args.months:
        months = [pd.Period(m, "M") for m in args.months]
    logger = logging.getLogger("timestep_footprints")
    summary = []
    for period in months:
        t0 = time.time()
        d = df[df.index.to_period("M") == period]
        n_req = int(d[REQUIRED].notna().all(axis=1).sum())
        row = {"month": str(period), "n_records": len(d), "n_required": n_req}
        if n_req < 10:
            summary.append({**row, "status": "skipped_insufficient_records"})
            continue
        model = build_climatology(
            d, model_type="ffp_xr", ustar="ustar_1_1_1", ol="mo_length_1_1_1",
            umean="ws_1_1_1", sigmav="v_sigma_1_1_1", wind_dir="wd_1_1_1", zm="zm", z0="z0",
            h=2000.0, dx=DX, dy=DY, domain=DOMAIN, rslayer=False, logger=logger, verbosity=0)
        f2d = model.get_footprint_timeseries().transpose("time", "y", "x")
        times = pd.DatetimeIndex(f2d["time"].values)
        transform, crs, y_up = grid_of(model, args.lat, args.lon)
        f = np.asarray(f2d.values, dtype="float64")
        if y_up:
            f = f[:, ::-1, :]
        w = d.reindex(times)
        out = args.out / f"{args.site}_{period.strftime('%Y%m')}_timesteps.npz"
        np.savez_compressed(
            out, f=f.astype("float32"), time=times.values.astype("datetime64[s]"),
            duration_s=np.full(len(times), 1800.0),
            nldas_eto_mm_h=w["nldas_eto_mm_h"].to_numpy(float),
            le_w_m2=w["le_1_1_1"].to_numpy(float),
            transform=np.array(transform), crs=np.array(crs))
        mass = f.sum(axis=(1, 2)) * DX * DY
        summary.append({**row, "status": "ok", "n_timesteps": len(times),
                        "n_positive_mass": int((mass > 0).sum()),
                        "min_value": float(f.min()), "seconds": round(time.time() - t0, 1)})
        print(summary[-1], flush=True)
        del model, f2d, f
    meta = {
        "site": args.site, "lat": args.lat, "lon": args.lon,
        "inputs": {k: {"path": str(getattr(args, k)), "sha256": sha256(getattr(args, k))}
                   for k in ("metflux", "params", "nldas")},
        "fluxfootprints_src": str(FOOTPRINTS_SRC),
        "fluxfootprints_files_sha256": {p.name: sha256(p) for p in sorted(
            (FOOTPRINTS_SRC / "fluxfootprints").glob("*.py"))},
        "settings": {"model": "ffp_xr", "h": 2000.0, "dx": DX, "dy": DY, "domain": DOMAIN,
                     "rslayer": False, "smooth_data": True, "hours": [START_HOUR, END_HOUR],
                     "zm": "effective_measurement_height", "z0": "roughness_length",
                     "nldas": "eto_grass, hourly start labels, linear interpolation to 30 min",
                     "le": "LE_1_1_1 W m-2, unchanged", "value_semantics": "density_per_m2",
                     "time": "interval start, naive UTC-7"},
        "months": summary,
    }
    tag = "_".join(str(m) for m in months) if args.months else "all"
    (args.out / f"{args.site}_timesteps_{tag}.json").write_text(
        json.dumps(meta, indent=2, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
