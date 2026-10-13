"""Input identities for reproducibility records.

The full run manifest (guide step 17) builds on these helpers.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

_CHUNK = 1 << 20


def file_sha256(path: str | os.PathLike[str]) -> str:
    """SHA-256 hex digest of a file, read in 1 MiB chunks."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        while chunk := fh.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def package_versions(*names: str) -> dict[str, str | None]:
    """Installed distribution versions; ``None`` when a package is absent."""
    from importlib.metadata import PackageNotFoundError, version

    out: dict[str, str | None] = {}
    for name in names or ("etcompare", "numpy", "pandas", "pyarrow", "scipy"):
        try:
            out[name] = version(name)
        except PackageNotFoundError:
            out[name] = None
    return out
