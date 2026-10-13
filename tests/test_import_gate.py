"""Step 2 gate: importing the package touches no network, raster or optional dependency."""

import importlib
import subprocess
import sys
import textwrap

import pytest

from etcompare._optional import require
from etcompare.errors import OptionalDependencyError

SCRIPT = textwrap.dedent(
    """
    import importlib, pkgutil, socket, sys

    def _blocked(*args, **kwargs):
        raise RuntimeError("network access during import")

    socket.socket.connect = _blocked
    socket.create_connection = _blocked
    socket.getaddrinfo = _blocked

    BLOCKED = {"rasterio", "geopandas", "shapely", "pyproj", "fiona", "osgeo", "matplotlib",
               "ee", "requests"}

    class Block:
        def find_spec(self, name, path=None, target=None):
            if name.split(".")[0] in BLOCKED:
                raise ImportError(f"optional dependency {name} imported")
            return None

    sys.meta_path.insert(0, Block())

    import etcompare
    for mod in pkgutil.walk_packages(etcompare.__path__, "etcompare."):
        importlib.import_module(mod.name)
    leaked = BLOCKED & {m.split(".")[0] for m in sys.modules}
    assert not leaked, leaked
    print("ok", etcompare.__version__)
    """
)


def test_import_has_no_side_effects():
    proc = subprocess.run([sys.executable, "-c", SCRIPT], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.startswith("ok")


def test_require_names_the_extra(monkeypatch):
    real = importlib.import_module

    def fake(name, *args):
        if name == "rasterio":
            raise ImportError("no rasterio")
        return real(name, *args)

    monkeypatch.setattr(importlib, "import_module", fake)
    with pytest.raises(OptionalDependencyError, match=r"etcompare\[geo\]"):
        require("rasterio", "Raster extraction")
