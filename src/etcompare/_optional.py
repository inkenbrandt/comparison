"""Deferred import of optional dependencies.

Geospatial and plotting packages are imported only inside the functions that
need them, through :func:`require`, so the tabular core works without them.
"""

from __future__ import annotations

import importlib
from types import ModuleType

from etcompare.errors import OptionalDependencyError

_EXTRA_FOR = {
    "rasterio": "geo",
    "pyproj": "geo",
    "shapely": "geo",
    "geopandas": "geo",
    "matplotlib": "plot",
}


def require(module: str, purpose: str) -> ModuleType:
    """Import ``module`` or raise :class:`OptionalDependencyError` naming the extra."""
    try:
        return importlib.import_module(module)
    except ImportError as exc:
        top = module.split(".")[0]
        extra = _EXTRA_FOR.get(top)
        hint = f"pip install 'etcompare[{extra}]'" if extra else f"pip install {top}"
        raise OptionalDependencyError(
            f"{purpose} requires the optional dependency '{top}'; install it with: {hint}"
        ) from exc
