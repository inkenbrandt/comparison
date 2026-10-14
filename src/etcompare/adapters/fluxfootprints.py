"""Exported per-timestep footprint stacks (``tools/upstream/run_timestep_footprints.py``).

One ``.npz`` file per month holds ``f`` (T, rows, cols) footprint density per m2 in
north-up raster order, interval-start stamps ``time`` in station time, ``duration_s``,
the affine ``transform`` and ``crs``, and weight series aligned with ``time``
(``nldas_eto_mm_h``, ``le_w_m2``). Nothing is read from fluxfootprints itself.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd

from etcompare.errors import SchemaError
from etcompare.footprint_aggregation import TimestepFootprints
from etcompare.spatial import Grid

_REQUIRED = ("f", "time", "duration_s", "transform", "crs")


def load_timestep_stack(
    path: str | os.PathLike[str], *, site_id: str, value_semantics: str = "density_per_m2",
    interval_label: str = "start",
) -> tuple[TimestepFootprints, pd.DataFrame]:
    """Read one stack file: the footprints and a frame of every weight series it holds."""
    path = Path(path)
    with np.load(path, allow_pickle=False) as z:
        missing = [k for k in _REQUIRED if k not in z.files]
        if missing:
            raise SchemaError(f"{path}: missing array(s) {missing}")
        f = z["f"]
        times = pd.DatetimeIndex(z["time"].astype("datetime64[ns]"))
        durations = z["duration_s"].astype("float64")
        transform = tuple(float(v) for v in z["transform"])
        crs = str(z["crs"])
        weights = pd.DataFrame({k: z[k].astype("float64") for k in z.files
                                if k not in _REQUIRED}, index=times)
    if f.ndim != 3 or len(transform) != 6:
        raise SchemaError(f"{path}: f must be (T, rows, cols) and transform 6 terms")
    grid = Grid(crs, transform, width=f.shape[2], height=f.shape[1])
    stack = TimestepFootprints(site_id, f, grid, times, durations, value_semantics,
                               interval_label)
    return stack, weights
