"""Footprint preparation: from raster values to non-negative cell contributions.

- Missing cells (NaN, or the declared nodata value) contribute nothing
  (decisions.md D-010). Their count is reported.
- ``density_per_m2`` values are multiplied by the physical cell area, which
  requires a projected CRS in metres. ``normalized_cell_weight`` and
  ``cell_contribution`` values are already cell-integrated.
- Negative contributions are rejected. The one exception is round-off:
  negative cells whose total mass is at most ``negative_tolerance`` times
  the positive mass are set to zero, and the adjustment is reported.
- A footprint with no positive contribution is rejected.

The denominator of every retained fraction is the footprint grid total, named
by value semantics: for normalized weights this is the domain-truncated,
renormalized footprint (≈1), **not** the original footprint (D-008).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from etcompare.errors import GridError
from etcompare.spatial import Grid, is_metric_projected

DENOMINATOR = {
    "normalized_cell_weight": "normalized_truncated_footprint",
    "cell_contribution": "footprint_grid_total",
    "density_per_m2": "footprint_grid_total",
}


@dataclass(frozen=True)
class PreparedFootprint:
    """Cell contributions ready for transfer, or a rejection status."""

    contribution: np.ndarray | None
    grid: Grid
    status: str  # ok | negative_weights | no_positive_weight
    weight_total: float
    denominator: str
    n_missing_cells: int
    n_negative_cells: int
    negative_mass_ratio: float
    negatives_zeroed: bool

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def detail(self) -> str:
        if self.status == "negative_weights":
            return (f"{self.n_negative_cells} negative cell(s); negative/positive mass "
                    f"{self.negative_mass_ratio:.3g} exceeds the round-off tolerance")
        if self.status == "no_positive_weight":
            return f"no positive contribution ({self.n_missing_cells} missing cells)"
        if self.negatives_zeroed:
            return (f"{self.n_negative_cells} round-off negative cell(s) zeroed "
                    f"(mass ratio {self.negative_mass_ratio:.3g})")
        return "ok"


def prepare_footprint(
    values: np.ndarray,
    grid: Grid,
    *,
    value_semantics: str,
    nodata: float | None,
    negative_tolerance: float = 1e-9,
) -> PreparedFootprint:
    """Validate footprint values and convert them to cell contributions."""
    if value_semantics not in DENOMINATOR:
        raise GridError(f"unsupported footprint value semantics {value_semantics!r}")
    v = np.asarray(values, dtype="float64")
    if v.shape != grid.shape:
        raise GridError(f"footprint array {v.shape} != grid {grid.shape}")
    missing = ~np.isfinite(v)
    if nodata is not None and not math.isnan(nodata):
        missing |= v == nodata
    w = np.where(missing, 0.0, v)
    if value_semantics == "density_per_m2":
        if not is_metric_projected(grid.crs):
            raise GridError("density_per_m2 footprints need a projected CRS in metres to "
                            "convert density to cell contribution")
        w = w * grid.cell_area
    neg = w < 0
    pos_mass = float(w[w > 0].sum())
    neg_mass = float(-w[neg].sum())
    ratio = neg_mass / pos_mass if pos_mass > 0 else (math.inf if neg_mass > 0 else 0.0)
    base = dict(grid=grid, denominator=DENOMINATOR[value_semantics],
                n_missing_cells=int(missing.sum()), n_negative_cells=int(neg.sum()),
                negative_mass_ratio=ratio)
    if neg.any() and ratio > negative_tolerance:
        return PreparedFootprint(None, status="negative_weights", weight_total=math.nan,
                                 negatives_zeroed=False, **base)  # type: ignore[arg-type]
    zeroed = bool(neg.any())
    if zeroed:
        w = np.where(neg, 0.0, w)
    total = float(w.sum())
    if total <= 0:
        return PreparedFootprint(None, status="no_positive_weight", weight_total=0.0,
                                 negatives_zeroed=zeroed, **base)  # type: ignore[arg-type]
    return PreparedFootprint(w, status="ok", weight_total=total, negatives_zeroed=zeroed,
                             **base)  # type: ignore[arg-type]
