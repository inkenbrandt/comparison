"""Input identities for reproducibility records.

The run manifest and output cache (:mod:`etcompare.runrecord`, guide step 17) build on
these helpers:

- :func:`file_sha256` and :func:`file_identity` identify inputs. Raster inputs may use
  a ``stat`` identity (path, size and modification time) because hashing thousands of
  rasters on a network drive takes many minutes; ``sha256`` is the opt-in alternative.
- :func:`canonical_hash` hashes plain data (configuration subtrees) independent of key
  order.
- :func:`code_identity` hashes the package source, so a code change invalidates cached
  outputs.
- :func:`git_state` and :func:`detect_upstream` record versions for the run manifest.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any

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


def file_identity(path: str | os.PathLike[str], kind: str = "sha256") -> dict[str, Any]:
    """Identity of one input file: ``sha256`` of its bytes, or ``stat`` (size, mtime).

    A missing file has ``exists: False``; it is an identity too, so creating the file
    later changes every key that includes it.
    """
    p = Path(path)
    if not p.is_file():
        return {"path": str(p), "exists": False}
    if kind == "sha256":
        return {"path": str(p), "exists": True, "sha256": file_sha256(p)}
    if kind == "stat":
        st = p.stat()
        return {"path": str(p), "exists": True, "size": st.st_size, "mtime_ns": st.st_mtime_ns}
    raise ValueError(f"unknown identity kind {kind!r}")


def canonical_json(obj: Any) -> str:
    """JSON with sorted keys and no whitespace; specs pass through ``config.to_plain``."""
    from etcompare.config import to_plain

    return json.dumps(to_plain(obj), sort_keys=True, separators=(",", ":"), default=str)


def canonical_hash(obj: Any) -> str:
    """SHA-256 of :func:`canonical_json`."""
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


#: Presentation modules: they never compute an analysis value, so editing them does not
#: invalidate analysis outputs (only the report).
PRESENTATION_MODULES: tuple[str, ...] = ("plots.py", "reporting.py", "cli.py", "__main__.py",
                                         "runner.py")


@lru_cache(maxsize=4)
def code_identity(exclude: tuple[str, ...] = ()) -> str:
    """SHA-256 over the package's Python sources (relative path and bytes, sorted).

    ``exclude`` names package-relative files left out (e.g. :data:`PRESENTATION_MODULES`).
    """
    root = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for p in sorted(root.rglob("*.py")):
        rel = p.relative_to(root).as_posix()
        if rel in exclude:
            continue
        digest.update(rel.encode("utf-8") + b"\0")
        digest.update(p.read_bytes() + b"\0")
    return digest.hexdigest()


def _git(args: list[str], cwd: Path) -> str | None:
    try:
        proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                              timeout=20, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def git_state(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """Revision and dirty flag of the git checkout holding ``path`` (default: this package).

    ``available: False`` when git or the checkout is absent (an installed wheel).
    """
    cwd = Path(path) if path is not None else Path(__file__).resolve().parent
    rev = _git(["rev-parse", "HEAD"], cwd)
    if rev is None:
        return {"available": False}
    status = _git(["status", "--porcelain", "--untracked-files=no"], cwd) or ""
    return {"available": True, "revision": rev, "dirty": bool(status),
            "dirty_paths": [line[3:] for line in status.splitlines()][:50]}


def detect_upstream() -> list[dict[str, Any]]:
    """Installed versions of the upstream packages, with the commit of an editable install.

    fluxfootprints is installed editable from a sibling checkout (D-056), so its commit is
    read from that checkout's git. flux-data-qaqc is usually not installed: the version that
    produced the station exports is a declaration (``upstream:``), never a detection.
    """
    from importlib.metadata import PackageNotFoundError, distribution

    out = []
    for name in ("fluxfootprints", "flux-data-qaqc", "fluxdataqaqc"):
        try:
            dist = distribution(name)
        except PackageNotFoundError:
            out.append({"name": name, "installed": False})
            continue
        row: dict[str, Any] = {"name": name, "installed": True, "version": dist.version}
        raw = dist.read_text("direct_url.json")
        if raw:
            info = json.loads(raw)
            url = str(info.get("url", ""))
            row["direct_url"] = url
            if info.get("dir_info", {}).get("editable") and url.startswith("file:"):
                from urllib.parse import unquote, urlparse

                local = unquote(urlparse(url).path)
                if os.name == "nt" and local.startswith("/"):
                    local = local[1:]
                git = git_state(local)
                if git.get("available"):
                    row["commit"] = git["revision"]
                    row["dirty"] = git["dirty"]
        out.append(row)
    return out
