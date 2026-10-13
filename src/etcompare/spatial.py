"""Grids, conservative contribution transfer and extraction arithmetic (guide step 5).

Footprint contribution is moved **onto the native model grid**; model ET is
never resampled. For each footprint cell *i* and model cell *j*,
``T[i, j]`` is the fraction of cell *i*'s area lying in cell *j*. Then

    c_j = sum_i w_i * T[i, j]                  (contribution in model cell j)
    ET  = sum_{j valid} c_j * ET_j / sum_{j valid} c_j

where *valid* means ``c_j > 0`` and the model value is finite. Because each
row of ``T`` sums to the fraction of the cell inside the model grid,
``sum_j c_j`` equals the footprint contribution inside the model extent; no
contribution is created or lost.

Two ways to build ``T``:

``aligned_1d_overlap``
    Same CRS, both grids north-up (no rotation): exact separable interval
    overlaps, using numpy and scipy only.
``polygon_overlap``
    Any other pair: model cell corners are projected into the footprint CRS
    and joined by straight edges, then each footprint cell is intersected
    exactly with the model quadrilaterals (shapely). Treating projected
    edges as straight is the only approximation. For ~30 m cells it is
    far below floating-point noise in the weights. Needs the ``geo`` extra.

Grids without a CRS raise :class:`~etcompare.errors.GridError`.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
import scipy.sparse as sp

from etcompare._optional import require
from etcompare.errors import GridError

Transform = tuple[float, float, float, float, float, float]
TransferMethod = Literal["aligned_1d_overlap", "polygon_overlap"]
_ROT_TOL = 1e-12


@dataclass(frozen=True)
class Grid:
    """A raster grid: CRS string, affine ``(a, b, c, d, e, f)`` and shape.

    ``x = a*col + b*row + c`` and ``y = d*col + e*row + f`` give the corner
    of cell ``(row, col)``; this is rasterio's ``Affine`` order.
    """

    crs: str | None
    transform: Transform
    width: int
    height: int

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise GridError(f"degenerate grid shape {self.height}x{self.width}")
        if self.cell_area == 0:
            raise GridError(f"degenerate grid transform {self.transform}")

    @classmethod
    def from_affine(cls, crs: Any, affine: Any, width: int, height: int) -> Grid:
        """Build from a rasterio/affine ``Affine`` and any CRS object or string."""
        crs_text = None if crs is None else (crs.to_string() if hasattr(crs, "to_string")
                                             else str(crs))
        return cls(crs_text or None, tuple(float(v) for v in tuple(affine)[:6]),  # type: ignore[arg-type]
                   int(width), int(height))

    @property
    def size(self) -> int:
        return self.width * self.height

    @property
    def shape(self) -> tuple[int, int]:
        return self.height, self.width

    @property
    def cell_area(self) -> float:
        a, b, _, d, e, _ = self.transform
        return abs(a * e - b * d)

    @property
    def axis_aligned(self) -> bool:
        a, b, _, d, e, _ = self.transform
        scale = max(abs(a), abs(e))
        return abs(b) <= _ROT_TOL * scale and abs(d) <= _ROT_TOL * scale

    @property
    def grid_id(self) -> str:
        """Stable short hash of CRS, transform (rounded to 1e-9) and shape."""
        t = ",".join(f"{v:.9f}" for v in self.transform)
        key = f"{self.crs}|{t}|{self.width}x{self.height}"
        return hashlib.sha1(key.encode()).hexdigest()[:16]

    def corners(self) -> tuple[np.ndarray, np.ndarray]:
        """Corner coordinates, each of shape ``(height + 1, width + 1)``."""
        a, b, c, d, e, f = self.transform
        rows, cols = np.mgrid[0:self.height + 1, 0:self.width + 1].astype("float64")
        return a * cols + b * rows + c, d * cols + e * rows + f

    def centers(self) -> tuple[np.ndarray, np.ndarray]:
        a, b, c, d, e, f = self.transform
        rows, cols = np.mgrid[0:self.height, 0:self.width].astype("float64") + 0.5
        return a * cols + b * rows + c, d * cols + e * rows + f

    def rowcol(self, x: np.ndarray | float, y: np.ndarray | float
               ) -> tuple[np.ndarray, np.ndarray]:
        """Fractional ``(row, col)`` of map coordinates (inverse affine)."""
        a, b, c, d, e, f = self.transform
        det = a * e - b * d
        dx, dy = np.asarray(x, dtype="float64") - c, np.asarray(y, dtype="float64") - f
        col = (e * dx - b * dy) / det
        row = (-d * dx + a * dy) / det
        return row, col

    def window(self, row_off: int, col_off: int, height: int, width: int) -> Grid:
        """The sub-grid starting at ``(row_off, col_off)``."""
        a, b, c, d, e, f = self.transform
        return Grid(self.crs, (a, b, c + a * col_off + b * row_off,
                               d, e, f + d * col_off + e * row_off), width, height)


@dataclass(frozen=True)
class Window:
    """Row/column window into a grid (offsets and size, in cells)."""

    row_off: int
    col_off: int
    height: int
    width: int

    def union(self, other: Window) -> Window:
        r0, c0 = min(self.row_off, other.row_off), min(self.col_off, other.col_off)
        r1 = max(self.row_off + self.height, other.row_off + other.height)
        c1 = max(self.col_off + self.width, other.col_off + other.width)
        return Window(r0, c0, r1 - r0, c1 - c0)


def same_crs(a: str | None, b: str | None) -> bool:
    """True when both CRS strings denote the same CRS (pyproj used if installed)."""
    if a is None or b is None:
        raise GridError("grid has no CRS; extraction requires a declared CRS on every raster")
    if a == b:
        return True
    try:
        import pyproj
    except ImportError:
        return False
    return bool(pyproj.CRS.from_user_input(a) == pyproj.CRS.from_user_input(b))


def transform_coords(x: np.ndarray, y: np.ndarray, src_crs: str, dst_crs: str
                     ) -> tuple[np.ndarray, np.ndarray]:
    """Transform coordinates between CRSs (identity when they are the same)."""
    if same_crs(src_crs, dst_crs):
        return np.asarray(x, dtype="float64"), np.asarray(y, dtype="float64")
    pyproj = require("pyproj", "Transforming coordinates between CRSs")
    tr = pyproj.Transformer.from_crs(src_crs, dst_crs, always_xy=True)
    xx, yy = tr.transform(np.asarray(x, dtype="float64"), np.asarray(y, dtype="float64"))
    if not (np.all(np.isfinite(xx)) and np.all(np.isfinite(yy))):
        raise GridError(f"coordinates could not be transformed from {src_crs} to {dst_crs}")
    return np.asarray(xx), np.asarray(yy)


def is_metric_projected(crs: str | None) -> bool:
    """True for a projected CRS with metre axes (needs pyproj)."""
    if crs is None:
        raise GridError("grid has no CRS")
    pyproj = require("pyproj", "Checking CRS units")
    c = pyproj.CRS.from_user_input(crs)
    return bool(c.is_projected and all(ax.unit_name in ("metre", "meter")
                                       for ax in c.axis_info[:2]))


# --------------------------------------------------------------------------- windows

def window_covering(src: Grid, dst: Grid, *, margin: int = 1) -> Window | None:
    """Window of ``dst`` cells that covers ``src``'s extent, clipped to ``dst``.

    The outline of ``src`` is sampled at every cell corner along its edges so
    that curved projected outlines are covered. Returns ``None`` when the two
    grids do not overlap.
    """
    X, Y = src.corners()
    ring_x = np.concatenate([X[0, :], X[-1, :], X[:, 0], X[:, -1]])
    ring_y = np.concatenate([Y[0, :], Y[-1, :], Y[:, 0], Y[:, -1]])
    x, y = transform_coords(ring_x, ring_y, src.crs, dst.crs)  # type: ignore[arg-type]
    rows, cols = dst.rowcol(x, y)
    r0 = max(int(math.floor(rows.min())) - margin, 0)
    r1 = min(int(math.ceil(rows.max())) + margin, dst.height)
    c0 = max(int(math.floor(cols.min())) - margin, 0)
    c1 = min(int(math.ceil(cols.max())) + margin, dst.width)
    if r1 <= r0 or c1 <= c0:
        return None
    return Window(r0, c0, r1 - r0, c1 - c0)


def window_around_point(dst: Grid, x: float, y: float, half: int) -> Window | None:
    """Window of ``(2*half+1)^2`` cells centred on the cell containing ``(x, y)``, clipped."""
    row, col = (int(math.floor(float(v))) for v in dst.rowcol(x, y))
    r0, c0 = max(row - half, 0), max(col - half, 0)
    r1, c1 = min(row + half + 1, dst.height), min(col + half + 1, dst.width)
    if r1 <= r0 or c1 <= c0:
        return None
    return Window(r0, c0, r1 - r0, c1 - c0)


# --------------------------------------------------------------------------- transfer

def _edges(n: int, origin: float, step: float) -> tuple[np.ndarray, np.ndarray]:
    e = origin + step * np.arange(n + 1, dtype="float64")
    return np.minimum(e[:-1], e[1:]), np.maximum(e[:-1], e[1:])


def _overlap_1d(src_lo: np.ndarray, src_hi: np.ndarray, dst_lo: np.ndarray,
                dst_hi: np.ndarray) -> np.ndarray:
    o = np.minimum(src_hi[:, None], dst_hi[None, :]) - np.maximum(src_lo[:, None],
                                                                  dst_lo[None, :])
    o = np.clip(o, 0.0, None)
    return o / (src_hi - src_lo)[:, None]


def _aligned_transfer(src: Grid, dst: Grid) -> sp.csr_matrix:
    sa, _, sc, _, se, sf = src.transform
    da, _, dc, _, de, df = dst.transform
    ox = _overlap_1d(*_edges(src.width, sc, sa), *_edges(dst.width, dc, da))
    oy = _overlap_1d(*_edges(src.height, sf, se), *_edges(dst.height, df, de))
    return sp.kron(sp.csr_matrix(oy), sp.csr_matrix(ox), format="csr")


def _cell_polygons(X: np.ndarray, Y: np.ndarray) -> Any:
    shapely = require("shapely", "Conservative transfer between non-aligned grids")
    ring = np.stack([
        np.stack([X[:-1, :-1], Y[:-1, :-1]], -1), np.stack([X[:-1, 1:], Y[:-1, 1:]], -1),
        np.stack([X[1:, 1:], Y[1:, 1:]], -1), np.stack([X[1:, :-1], Y[1:, :-1]], -1),
        np.stack([X[:-1, :-1], Y[:-1, :-1]], -1),
    ], axis=2)  # (H, W, 5, 2)
    return shapely.polygons(ring.reshape(-1, 5, 2))


def _polygon_transfer(src: Grid, dst: Grid) -> sp.csr_matrix:
    shapely = require("shapely", "Conservative transfer between non-aligned grids")
    src_polys = _cell_polygons(*src.corners())
    DX, DY = dst.corners()
    dx, dy = transform_coords(DX.ravel(), DY.ravel(), dst.crs, src.crs)  # type: ignore[arg-type]
    dst_polys = _cell_polygons(dx.reshape(DX.shape), dy.reshape(DY.shape))
    if not bool(shapely.is_valid(dst_polys).all()):
        raise GridError("projected model cells are not valid polygons; the grid pair is "
                        "unsupported (e.g. it crosses a projection singularity)")
    tree = shapely.STRtree(dst_polys)
    si, di = tree.query(src_polys, predicate="intersects")
    area = shapely.area(shapely.intersection(src_polys[si], dst_polys[di]))
    frac = area / shapely.area(src_polys)[si]
    keep = frac > 0
    return sp.csr_matrix((frac[keep], (si[keep], di[keep])), shape=(src.size, dst.size))


@dataclass(frozen=True)
class Transfer:
    """Sparse area-fraction matrix from a source grid to a destination grid."""

    matrix: sp.csr_matrix
    src: Grid
    dst: Grid
    method: TransferMethod

    def apply(self, values: np.ndarray) -> np.ndarray:
        """Move cell-integrated ``values`` (src shape) onto ``dst`` (sums are conserved)."""
        v = np.asarray(values, dtype="float64")
        if v.shape != self.src.shape:
            raise GridError(f"values shape {v.shape} != source grid {self.src.shape}")
        return np.asarray(self.matrix.T @ v.ravel()).reshape(self.dst.shape)

    def coverage(self) -> np.ndarray:
        """Fraction of each source cell lying inside the destination grid."""
        return np.asarray(self.matrix.sum(axis=1)).ravel().reshape(self.src.shape)


def transfer_matrix(src: Grid, dst: Grid, *, method: TransferMethod | None = None) -> Transfer:
    """Area-fraction transfer from ``src`` cells to ``dst`` cells.

    ``method=None`` chooses ``aligned_1d_overlap`` when the CRSs match and both
    grids are north-up, otherwise ``polygon_overlap``. Rows never sum above 1
    (checked to 1e-9).
    """
    aligned = same_crs(src.crs, dst.crs) and src.axis_aligned and dst.axis_aligned
    chosen: TransferMethod = method or ("aligned_1d_overlap" if aligned else "polygon_overlap")
    if chosen == "aligned_1d_overlap":
        if not aligned:
            raise GridError("aligned_1d_overlap needs the same CRS and north-up grids")
        m = _aligned_transfer(src, dst)
    else:
        m = _polygon_transfer(src, dst)
    m.eliminate_zeros()
    over = np.asarray(m.sum(axis=1)).ravel().max(initial=0.0)
    if over > 1 + 1e-9:
        raise GridError(f"transfer rows sum to {over:.12f} > 1; overlap computation failed")
    return Transfer(m, src, dst, chosen)


# --------------------------------------------------------------------------- extraction

def support_id(grid: Grid, mask: np.ndarray, prefix: str = "") -> str:
    """Identifier of a set of valid cells on ``grid``."""
    h = hashlib.sha1(grid.grid_id.encode())
    h.update(np.packbits(np.asarray(mask, dtype=bool).ravel()).tobytes())
    return f"{prefix}{h.hexdigest()[:16]}"


@dataclass(frozen=True)
class WeightedResult:
    """Contribution-weighted model value and its weight accounting.

    Denominators are explicit:

    - ``retained_frac = weight_valid / weight_total`` (footprint grid total);
    - ``in_extent_frac = weight_in_extent / weight_total``;
    - ``valid_given_extent = weight_valid / weight_in_extent``.
    """

    value: float
    status: str  # ok | no_valid_cells | no_overlap
    weight_total: float
    weight_in_extent: float
    weight_valid: float
    n_cells_valid: int
    support_id: str | None

    @property
    def retained_frac(self) -> float:
        return self.weight_valid / self.weight_total if self.weight_total > 0 else math.nan

    @property
    def in_extent_frac(self) -> float:
        return self.weight_in_extent / self.weight_total if self.weight_total > 0 else math.nan

    @property
    def valid_given_extent(self) -> float:
        return (self.weight_valid / self.weight_in_extent if self.weight_in_extent > 0
                else math.nan)


def weighted_extract(
    contribution: np.ndarray,
    values: np.ndarray,
    *,
    weight_total: float,
    grid: Grid | None = None,
    extra_mask: np.ndarray | None = None,
    support_prefix: str = "",
) -> WeightedResult:
    """``sum(c*v)/sum(c)`` over cells with ``c > 0``, finite ``v`` and ``extra_mask``.

    ``contribution`` must already be non-negative and cell-integrated on the
    same grid as ``values``. Zero model values are valid; only non-finite
    values are missing.
    """
    c = np.asarray(contribution, dtype="float64")
    v = np.asarray(values, dtype="float64")
    if c.shape != v.shape:
        raise GridError(f"contribution shape {c.shape} != values shape {v.shape}")
    if (c < 0).any():
        raise GridError("negative contribution reached extraction; reject it upstream")
    in_extent = float(c.sum())
    valid = (c > 0) & np.isfinite(v)
    if extra_mask is not None:
        valid &= np.asarray(extra_mask, dtype=bool)
    w_valid = float(c[valid].sum())
    sid = support_id(grid, valid, support_prefix) if grid is not None else None
    if in_extent <= 0:
        return WeightedResult(math.nan, "no_overlap", weight_total, 0.0, 0.0, 0, None)
    if w_valid <= 0:
        return WeightedResult(math.nan, "no_valid_cells", weight_total, in_extent, 0.0, 0, sid)
    value = float(np.sum(c[valid] * v[valid]) / w_valid)
    return WeightedResult(value, "ok", weight_total, in_extent, w_valid, int(valid.sum()), sid)


@dataclass(frozen=True)
class BlockResult:
    """Unweighted mean over a block of whole model pixels."""

    value: float
    status: str  # ok | no_valid_cells | tower_outside_raster
    n_cells_valid: int
    n_cells: int
    support_id: str | None

    @property
    def valid_frac(self) -> float:
        return self.n_cells_valid / self.n_cells if self.n_cells else math.nan


def block_mean(values: np.ndarray, grid: Grid, x: float, y: float, size: int) -> BlockResult:
    """Mean of the ``size x size`` pixel block centred on the pixel containing ``(x, y)``.

    ``(x, y)`` are in ``grid``'s CRS. Pixel boundaries: whole pixels only; a
    pixel is in the block or not. Pixels outside ``values`` count as missing
    (``n_cells`` stays ``size**2``).
    """
    if size < 1 or size % 2 == 0:
        raise ValueError("block size must be odd and positive")
    v = np.asarray(values, dtype="float64")
    row, col = (int(math.floor(float(t))) for t in grid.rowcol(x, y))
    if not (0 <= row < grid.height and 0 <= col < grid.width):
        return BlockResult(math.nan, "tower_outside_raster", 0, size * size, None)
    h = size // 2
    r0, r1 = max(row - h, 0), min(row + h + 1, grid.height)
    c0, c1 = max(col - h, 0), min(col + h + 1, grid.width)
    mask = np.zeros(grid.shape, dtype=bool)
    mask[r0:r1, c0:c1] = True
    valid = mask & np.isfinite(v)
    sid = support_id(grid, valid, f"block{size}:")
    if not valid.any():
        return BlockResult(math.nan, "no_valid_cells", 0, size * size, sid)
    return BlockResult(float(v[valid].mean()), "ok", int(valid.sum()), size * size, sid)
