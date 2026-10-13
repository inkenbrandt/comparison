"""Profile a directory of daily footprint rasters (read-only).

Reports grid identity, declared nodata, weight sums, negative-weight and
all-nodata files, so footprint-set facts in docs/input_contract.md can be
regenerated. Needs the `geo` extra.

    uv run python tools/profile_footprints.py "M:/My Drive/footprints/US-UTJ/weighted" \
        [--glob "ffp_*_daily_*.tif"] [--csv out.csv]
"""
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio


def profile(path: Path) -> dict:
    with rasterio.open(path) as ds:
        a = ds.read(1).astype("float64")
        grid = (ds.crs.to_string() if ds.crs else None, tuple(round(v, 6) for v in ds.transform)[:6],
                ds.width, ds.height)
        nodata = ds.nodata
    finite = np.isfinite(a)
    declared = finite & (a == nodata) if nodata is not None else np.zeros_like(finite)
    vals = a[finite & ~declared]
    return {
        "file": path.name, "grid": grid, "nodata": nodata,
        "n_nan": int((~finite).sum()), "n_declared_nodata": int(declared.sum()),
        "n_positive": int((vals > 0).sum()), "n_negative": int((vals < 0).sum()),
        "min_negative": float(vals[vals < 0].min()) if (vals < 0).any() else np.nan,
        "sum_all": float(vals.sum()), "sum_positive": float(vals[vals > 0].sum()),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path)
    ap.add_argument("--glob", default="ffp_*_daily_*.tif")
    ap.add_argument("--csv", type=Path)
    args = ap.parse_args()
    rows = [profile(p) for p in sorted(args.root.glob(args.glob))]
    df = pd.DataFrame(rows)
    print(f"{args.root} ({args.glob}): {len(df)} files")
    for g, n in Counter(df["grid"]).items():
        print(f"  grid {g}: {n}")
    print(f"  nodata values: {dict(Counter(df['nodata']))}")
    empty = df["n_positive"] == 0
    neg = df["n_negative"] > 0
    print(f"  all-nodata/no positive weight: {int(empty.sum())}")
    print(f"  with negative weights: {int(neg.sum())} "
          f"(most negative {df['min_negative'].min():.3g})")
    print(f"  with NaN cells: {int((df['n_nan'] > 0).sum())}")
    ok = ~empty & ~neg
    s = df.loc[ok, "sum_all"]
    print(f"  usable files: {int(ok.sum())}; weight sum range {s.min():.10f}..{s.max():.10f}")
    if args.csv:
        df.to_csv(args.csv, index=False)


if __name__ == "__main__":
    main()
