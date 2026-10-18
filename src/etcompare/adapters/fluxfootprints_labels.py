"""Optional fluxfootprints representativeness labels (guide step 13).

fluxfootprints (sibling package, GPL-3.0) implements Chu et al. (2021): it compares the
footprint-weighted land-cover composition with that of a target disc around the tower
and reduces the comparison to HIGH / MEDIUM / LOW. This adapter is the only place that
calls it. Everything is imported inside the functions, so etcompare imports without it.

The upstream class is stored as a **label** next to etcompare's quantitative fractions
(``representativeness_labels``). It never enters a metric or threshold and is not a
quantitative ET accuracy guarantee.

Grid hand-over: the footprint contribution has already been moved onto the land-cover
map's native grid by exact area overlap (:mod:`etcompare.representativeness`), so the
map is never resampled for fluxfootprints. The north-up ``(row, col)`` arrays are
re-ordered to fluxfootprints' ``(x, y)`` convention with ascending cell-centre offsets
from the tower in metres; this needs an axis-aligned grid in a metric CRS.
"""

from __future__ import annotations

import json
import math
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from typing import Any

import numpy as np

from etcompare.config import LandscapeLayerSpec
from etcompare.errors import GridError
from etcompare.spatial import Grid, is_metric_projected

#: The upstream functions this adapter is tested against.
API = ("fluxfootprints.representativeness.categorical_representativeness",)


def _git_commit(path: Path) -> str | None:
    """Commit of a git checkout containing ``path``, read from ``.git`` (no subprocess)."""
    for p in (path, *path.parents):
        git = p / ".git"
        if git.is_dir():
            head = (git / "HEAD").read_text(encoding="utf-8").strip()
            if not head.startswith("ref: "):
                return head
            ref = head[5:]
            loose = git / ref
            if loose.is_file():
                return loose.read_text(encoding="utf-8").strip()
            packed = git / "packed-refs"
            if packed.is_file():
                for line in packed.read_text(encoding="utf-8").splitlines():
                    if line.endswith(" " + ref):
                        return line.split()[0]
            return None
    return None


def fluxfootprints_status() -> dict[str, Any]:
    """Whether fluxfootprints and its representativeness API are importable.

    Returns ``available``, ``version``, ``commit`` (for a source checkout), ``location``
    and ``reason`` (when not available).
    """
    out: dict[str, Any] = {"available": False, "version": None, "commit": None,
                           "location": None, "reason": None}
    try:
        dist = distribution("fluxfootprints")
    except PackageNotFoundError:
        out["reason"] = "fluxfootprints is not installed in this environment"
        return out
    out["version"] = dist.version
    direct = dist.read_text("direct_url.json")
    if direct:
        url = json.loads(direct).get("url", "")
        if url.startswith("file:"):
            from urllib.parse import unquote, urlparse

            p = unquote(urlparse(url).path)
            if len(p) > 2 and p[0] == "/" and p[2] == ":":  # /C:/... on Windows
                p = p[1:]
            path = Path(p)
            out["location"] = str(path)
            out["commit"] = _git_commit(path)
    try:
        import importlib

        mod = importlib.import_module("fluxfootprints.representativeness")
    except Exception as exc:  # ImportError or an upstream import-time failure
        out["reason"] = f"fluxfootprints.representativeness cannot be imported: {exc}"
        return out
    if not hasattr(mod, "categorical_representativeness"):
        out["reason"] = "fluxfootprints has no categorical_representativeness"
        return out
    out["available"] = True
    return out


def version_label(status: dict[str, Any]) -> str:
    v = f"fluxfootprints {status.get('version')}"
    return v + (f" @ {status['commit'][:7]}" if status.get("commit") else "")


def to_xy(grid: Grid, array: np.ndarray, tower_xy: tuple[float, float]
          ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """``(values[x, y], x, y)`` with ascending cell-centre offsets from the tower (m)."""
    if not grid.axis_aligned:
        raise GridError("fluxfootprints labels need an axis-aligned (north-up) grid")
    if not is_metric_projected(grid.crs):
        raise GridError("fluxfootprints labels need a projected CRS in metres")
    a, _, c, _, e, f = grid.transform
    if not math.isclose(abs(a), abs(e), rel_tol=1e-9):
        raise GridError("fluxfootprints labels need square cells")
    xc = c + a * (np.arange(grid.width) + 0.5) - tower_xy[0]
    yc = f + e * (np.arange(grid.height) + 0.5) - tower_xy[1]
    v = np.asarray(array, dtype="float64")
    if a < 0:
        xc, v = xc[::-1], v[:, ::-1]
    if e < 0:
        yc, v = yc[::-1], v[::-1, :]
    return v.T.copy(), xc, yc


def categorical_labels(contribution: np.ndarray, classes: np.ndarray, grid: Grid,
                       layer: LandscapeLayerSpec, tower_xy: tuple[float, float], *,
                       radii: tuple[float, ...], alpha: float) -> list[dict[str, Any]]:
    """fluxfootprints ``categorical_representativeness`` for one footprint and map.

    ``contribution`` and ``classes`` are on the map's native ``grid`` (tower in its CRS).
    Nodata cells become NaN, as fluxfootprints expects. Returns one dict per radius with
    ``radius_m``, ``label``, ``dominant_class``, ``p_footprint``, ``p_target`` (fractions),
    ``chi2`` and ``p_value``.
    """
    from etcompare._optional import require

    rep = require("fluxfootprints.representativeness", "fluxfootprints representativeness labels")
    k = np.asarray(classes, dtype="float64").copy()
    if not math.isnan(layer.nodata):
        k[k == layer.nodata] = np.nan
    w, x, y = to_xy(grid, contribution, tower_xy)
    lc, _, _ = to_xy(grid, k, tower_xy)
    frame = rep.categorical_representativeness(w, lc, x, y, radii=radii, alpha=alpha)
    out = []
    for r in frame.itertuples(index=False):
        dom = r.dominant_class
        out.append({"radius_m": float(r.radius), "label": str(getattr(r.level, "value", r.level)),
                    "dominant_class": None if dom is None or (isinstance(dom, float)
                                                              and math.isnan(dom))
                    else str(int(dom)),
                    "p_footprint": float(r.p_footprint), "p_target": float(r.p_target),
                    "chi2": float(r.chi2), "p_value": float(r.p_value)})
    return out


def upstream_parameters(radii: tuple[float, ...], alpha: float) -> dict[str, Any]:
    """The upstream thresholds the labels depend on (recorded with every label row)."""
    from etcompare._optional import require

    rep = require("fluxfootprints.representativeness", "fluxfootprints representativeness labels")
    return {"radii_m": list(radii), "alpha": alpha,
            "high_cover_percent": getattr(rep, "HIGH_COVER_PERCENT", None),
            "medium_cover_percent": getattr(rep, "MEDIUM_COVER_PERCENT", None),
            "api": list(API)}
