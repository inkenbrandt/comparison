"""Bounded raster access (needs the ``geo`` extra).

Rasters are opened one at a time, and only the window needed is read. The
returned arrays are float64, with NaN for every missing value.

Model scaling comes from the product declaration (contract section 9):

- ``declared``: the declared ``scale_factor``, ``add_offset`` and ``nodata``
  are used. A header nodata that differs from the declared one is an error,
  because a header nodata would otherwise be read as valid ET.
- ``from_header``: the GDAL band scale, offset and nodata are used. GDAL
  reports 1/0 when the header carries no scale tags.
- Unset: raster extraction refuses to run.

NaN is always missing; zero is a valid ET value (D-010).
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Any

import numpy as np

from etcompare._optional import require
from etcompare.config import ProductSpec
from etcompare.errors import ContractError, GridError
from etcompare.spatial import Grid, Window


@dataclass(frozen=True)
class RasterHeader:
    path: str
    grid: Grid
    nodata: float | None
    scale: float
    offset: float
    dtype: str
    count: int


def _open(path: str | os.PathLike[str]) -> Any:
    rasterio = require("rasterio", "Reading rasters")
    return rasterio.open(path)


def _header(ds: Any, path: str) -> RasterHeader:
    if ds.crs is None:
        raise GridError(f"raster {path} has no CRS")
    return RasterHeader(
        path=path, grid=Grid.from_affine(ds.crs, ds.transform, ds.width, ds.height),
        nodata=ds.nodata, scale=float(ds.scales[0]), offset=float(ds.offsets[0]),
        dtype=str(ds.dtypes[0]), count=int(ds.count),
    )


def read_header(path: str | os.PathLike[str]) -> RasterHeader:
    with _open(path) as ds:
        return _header(ds, os.fspath(path))


def _scaling(product: ProductSpec, header: RasterHeader) -> tuple[float, float, float | None]:
    product.require_scaling()
    if product.raster_scaling == "from_header":
        return header.scale, header.offset, header.nodata
    declared = product.nodata
    hdr = header.nodata
    if hdr is not None and not math.isnan(hdr) and (declared is None or hdr != declared):
        raise ContractError(
            f"{header.path}: header nodata {hdr} differs from declared nodata {declared}",
            field="nodata", config_key=f"products.{product.model}.nodata",
        )
    return float(product.scale_factor), float(product.add_offset), declared  # type: ignore[arg-type]


def read_model_window(
    path: str | os.PathLike[str], product: ProductSpec, window: Window | None = None,
    *, expect_grid: Grid | None = None,
) -> tuple[np.ndarray, RasterHeader]:
    """Read band 1 of a model raster in ``window``; return (values, header).

    ``expect_grid`` (the full-raster grid) is checked so that a window computed
    for one grid is never applied to another.
    """
    windows = require("rasterio.windows", "Reading rasters")
    with _open(path) as ds:
        header = _header(ds, os.fspath(path))
        if header.count != 1:
            raise GridError(f"{path}: expected 1 band, found {header.count}")
        if expect_grid is not None and header.grid != expect_grid:
            raise GridError(f"{path}: grid {header.grid.grid_id} differs from expected "
                            f"{expect_grid.grid_id}")
        scale, offset, nodata = _scaling(product, header)
        win = None if window is None else windows.Window(
            window.col_off, window.row_off, window.width, window.height)
        raw = ds.read(1, window=win).astype("float64")
    missing = ~np.isfinite(raw)
    if nodata is not None and not math.isnan(nodata):
        missing |= raw == nodata
    values = raw * scale + offset
    values[missing] = np.nan
    return values, header


def write_footprint(path: str | os.PathLike[str], values: np.ndarray, grid: Grid) -> str:
    """Write a footprint as a float64 single-band GeoTIFF without a nodata value.

    float64 keeps the weights exactly; zero cells are zero contribution. The file
    is read back and must be bit-identical to ``values``; otherwise it is removed
    and :class:`OSError` is raised. (A real-station run in a OneDrive-synced folder left
    three footprints with garbage values although the arrays were correct.)
    """
    rasterio = require("rasterio", "Writing rasters")
    from affine import Affine

    arr = np.asarray(values, dtype="float64")
    if arr.shape != grid.shape:
        raise GridError(f"footprint array {arr.shape} != grid {grid.shape}")
    if grid.crs is None:
        raise GridError("cannot write a footprint without a CRS")
    tmp = f"{os.fspath(path)}.part"
    try:
        with rasterio.open(tmp, "w", driver="GTiff", height=grid.height, width=grid.width,
                           count=1, dtype="float64", crs=grid.crs,
                           transform=Affine(*grid.transform)) as ds:
            ds.write(arr, 1)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    with rasterio.open(path) as ds:
        back = ds.read(1)
    if back.shape != arr.shape or not np.array_equal(back, arr, equal_nan=True):
        os.remove(path)
        raise OSError(f"{path}: footprint read back differs from the array written; file "
                      "removed (storage or sync corruption?)")
    return os.fspath(path)


def read_footprint(path: str | os.PathLike[str]) -> tuple[np.ndarray, Grid, float | None]:
    """Read band 1 of a footprint raster: (raw values, grid, header nodata)."""
    with _open(path) as ds:
        header = _header(ds, os.fspath(path))
        if header.count != 1:
            raise GridError(f"{path}: expected 1 band, found {header.count}")
        return ds.read(1).astype("float64"), header.grid, header.nodata
