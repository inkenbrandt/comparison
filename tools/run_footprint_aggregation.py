"""Aggregate per-timestep footprint stacks into the declared footprint sets (guide step 6).

Stand-in until the step-17 CLI. For every ``footprint_aggregations`` entry in the config,
the footprint set whose ``weighting_method`` is ``etcompare_aggregation:<method_id>``
receives one GeoTIFF per day (written to its ``root``), built from the monthly stacks of
tools/upstream/run_timestep_footprints.py. Writes <root>/<set_id>_periods.csv.

``--compare-sets`` reports, per day, the total-variation distance
0.5 * sum |a - b| between each new aggregate and the same day's raster in existing sets
(both renormalized over their common cells). 0 = identical shape, 1 = disjoint.

    uv run python tools/run_footprint_aggregation.py examples/us_utj.yaml US-UTJ \
        outputs/footprint_timesteps/US-UTJ \
        --compare-sets US-UTJ_set_A_20261005 US-UTJ_set_B_etw_20261012
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np
import pandas as pd

from etcompare.adapters.fluxfootprints import load_timestep_stack
from etcompare.config import load_config
from etcompare.footprint_aggregation import (
    aggregate_footprints,
    combine_results,
    write_footprint_set,
)
from etcompare.io.manifest import discover_footprints
from etcompare.io.rasters import read_footprint
from etcompare.spatial import Grid


def common_window(a: Grid, b: Grid) -> tuple[slice, slice, slice, slice] | None:
    """Index windows of the overlap of two north-up grids with equal cells and CRS."""
    if a.crs != b.crs or a.transform[0] != b.transform[0] or a.transform[4] != b.transform[4]:
        return None
    dc = (b.transform[2] - a.transform[2]) / a.transform[0]
    dr = (b.transform[5] - a.transform[5]) / a.transform[4]
    if abs(dc - round(dc)) > 1e-6 or abs(dr - round(dr)) > 1e-6:
        return None
    dc, dr = round(dc), round(dr)  # b's origin in a's cells
    r0, c0 = max(0, dr), max(0, dc)
    r1, c1 = min(a.height, dr + b.height), min(a.width, dc + b.width)
    if r1 <= r0 or c1 <= c0:
        return None
    return (slice(r0, r1), slice(c0, c1), slice(r0 - dr, r1 - dr), slice(c0 - dc, c1 - dc))


def tv_distance(a: np.ndarray, b: np.ndarray) -> float:
    a = np.where(np.isfinite(a) & (a > 0), a, 0.0)
    b = np.where(np.isfinite(b) & (b > 0), b, 0.0)
    if a.sum() <= 0 or b.sum() <= 0:
        return math.nan
    return float(0.5 * np.abs(a / a.sum() - b / b.sum()).sum())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("config")
    ap.add_argument("site")
    ap.add_argument("stack_dir", type=Path)
    ap.add_argument("--compare-sets", nargs="*", default=[])
    args = ap.parse_args()
    cfg = load_config(args.config)
    files = sorted(args.stack_dir.glob(f"{args.site}_*_timesteps.npz"))
    if not files:
        raise SystemExit(f"no stacks in {args.stack_dir}")

    # One target set per aggregation method and station: the set whose weighting_method is
    # the method's tag and whose set_id starts with '<site>_'. A method without such a set
    # is skipped for this station.
    targets = {}
    for agg in cfg.footprint_aggregations:
        tag = f"etcompare_aggregation:{agg.method_id}"
        sets = [f for f in cfg.footprint_sets
                if f.weighting_method == tag and f.set_id.startswith(f"{args.site}_")]
        if len(sets) > 1:
            raise SystemExit(f"{args.site}: more than one footprint set with weighting_method "
                             f"{tag}")
        if not sets:
            print(f"{args.site}: no footprint set for {tag}; method skipped")
            continue
        targets[agg.method_id] = (agg, sets[0])
    if not targets:
        raise SystemExit(f"{args.site}: no footprint set to write")

    chunks: dict[str, list] = {m: [] for m in targets}
    for path in files:
        stack, weights = load_timestep_stack(path, site_id=args.site)
        for m, (agg, _) in targets.items():
            w = None if agg.weight_column is None else weights[agg.weight_column]
            chunks[m].append(aggregate_footprints(stack, agg, weights=w))
        print(f"{path.name}: {len(stack.times)} timesteps", flush=True)

    results = {}
    for m, (_agg, declared) in targets.items():
        r = combine_results(chunks[m])
        results[m] = r
        fs = write_footprint_set(r, declared.root, set_id=declared.set_id,
                                 layer=declared.layers[0] if declared.layers else None)
        for f in ("value_semantics", "temporal_weighting", "weighting_method"):
            if getattr(fs, f) != getattr(declared, f):
                raise SystemExit(f"{declared.set_id}: written {f}={getattr(fs, f)!r} but "
                                 f"config declares {getattr(declared, f)!r}")
        summary = r.summary()
        summary.to_csv(Path(declared.root) / f"{declared.set_id}_periods.csv", index=False)
        ok = summary[summary.status == "ok"]
        print(f"\n{declared.set_id}: {len(ok)} of {len(summary)} days written; "
              f"status {summary.status.value_counts().to_dict()}")
        print(f"  eligible timesteps/day median {ok.n_eligible.median():.0f} "
              f"(min {ok.n_eligible.min()}), temporal coverage median "
              f"{ok.temporal_coverage_frac.median():.3f}")
        print("  timestep exclusions:", r.report.to_frame().query(
            "code == 'aggregation_counts'").message.str.extract(r"exclusions (\{.*\})")[0]
            .tolist())

    if args.compare_sets:
        rows = []
        for set_id in args.compare_sets:
            fs = cfg.footprint_set(set_id)
            man = discover_footprints(fs).manifest.frame
            man = man[man.period == "day"].set_index("date")
            for m, r in results.items():
                for p in r.periods:
                    if not p.ok or p.date not in man.index:
                        continue
                    other, grid, nodata = read_footprint(man.loc[p.date, "path"])
                    if nodata is not None and not math.isnan(nodata):
                        other = np.where(other == nodata, 0.0, other)
                    win = common_window(p.grid, grid)
                    if win is None:
                        raise SystemExit(f"{set_id}: grid not aligned with the aggregate")
                    neg = bool((other < 0).any())
                    rows.append({"date": p.date, "aggregate": m, "existing_set": set_id,
                                 "tv_distance": tv_distance(p.contribution[win[0], win[1]],
                                                            other[win[2], win[3]]),
                                 "existing_has_negative": neg})
        d = pd.DataFrame(rows)
        out = cfg.output_dir / f"{args.site}_aggregate_vs_existing_footprints.csv"  # type: ignore[operator]
        d.to_csv(out, index=False)
        print("\nTotal-variation distance to existing sets (median, p90; days):")
        print(d.groupby(["existing_set", "aggregate"]).tv_distance.describe(
            percentiles=[0.5, 0.9])[["count", "50%", "90%"]].round(4).to_string())
        neg = d[d.existing_has_negative]
        if len(neg):
            print("\nOn days where the existing set has negative weights:")
            print(neg.groupby(["existing_set", "aggregate"]).tv_distance.median().round(4)
                  .to_string())
        w = d.pivot_table(index=["existing_set", "date"], columns="aggregate",
                          values="tv_distance")
        if {"nldas_eto_weighted", "measured_le_weighted"} <= set(w.columns):
            closer = (w["measured_le_weighted"] < w["nldas_eto_weighted"]).groupby(
                level=0).mean()
            print("\nShare of days where the measured-LE aggregate is closer:")
            print(closer.round(3).to_string())


if __name__ == "__main__":
    main()
