"""Sphinx configuration (Read the Docs: .readthedocs.yaml; local: python -m sphinx docs docs/_build)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import etcompare  # noqa: E402

project = "etcompare"
author = "The etcompare contributors"
copyright = "2026, The etcompare contributors"
release = etcompare.__version__
version = ".".join(release.split(".")[:2])

extensions = [
    "myst_parser",
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx.ext.intersphinx",
]
source_suffix = {".md": "markdown", ".rst": "restructuredtext"}
root_doc = "index"
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

myst_enable_extensions = ["colon_fence", "deflist", "dollarmath"]
myst_heading_anchors = 3

# The optional extras are not needed to document the API: their imports are deferred in
# the package, and these are mocked for the few modules that name them at import time.
autodoc_mock_imports = ["rasterio", "pyproj", "shapely", "geopandas", "matplotlib", "affine",
                        "fluxfootprints", "xarray", "pyogrio"]
autodoc_member_order = "bysource"
autodoc_typehints = "description"
autodoc_default_options = {"members": True, "show-inheritance": False}
napoleon_google_docstring = False
napoleon_numpy_docstring = False

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "pandas": ("https://pandas.pydata.org/docs", None),
    "numpy": ("https://numpy.org/doc/stable", None),
}

html_theme = "sphinx_rtd_theme"
html_title = f"etcompare {release}"
suppress_warnings = ["myst.header", "myst.xref_missing"]


def _sections_to_rubrics(app, what, name, obj, options, lines):  # noqa: ARG001
    """Render docstring section headings as rubrics: every module has its own "Rules" or
    "Interpretation" section, and as section targets they would collide across pages."""
    i = 0
    while i < len(lines) - 1:
        title, under = lines[i], lines[i + 1]
        if (title.strip() and not title.startswith(" ") and under
                and set(under) <= set("-=~^") and len(under) >= len(title.strip())):
            lines[i:i + 2] = [f".. rubric:: {title.strip()}", ""]
        i += 1


def setup(app):
    app.connect("autodoc-process-docstring", _sections_to_rubrics)
