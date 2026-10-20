"""Run manifests and the output cache (guide step 17).

**Run manifest.** Every CLI invocation writes one JSON manifest
(:class:`RunManifest`, schema :data:`MANIFEST_VERSION`) to
``<output_dir>/runs/<run_id>/run_manifest.json``, and copies it to
``<output_dir>/run_manifest.json`` as the latest. Earlier runs are never
overwritten. It records the configuration hash, input identities and checksums,
package versions, the git revision (with a dirty flag), declared and detected
upstream versions, raster grid IDs, masks, random seeds, exclusion counts per stage
and the completion status of every workflow unit.

**Cache.** A workflow unit (one call that writes a group of tables, e.g. the metrics
of one comparison under one metric set) is skipped when its stamp
``<output_dir>/.etcompare/cache/<stage>/<unit>.json`` holds the same cache key and
every output listed in the stamp still exists with the same size and modification
time. The key is a SHA-256 over the package source (:func:`~etcompare.provenance.code_identity`),
the configuration entries the unit reads, and the identities of its input files,
including upstream outputs by content. A change therefore invalidates exactly the
units that read the changed entry or file.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from etcompare.errors import SchemaError
from etcompare.schemas import ANALYSIS_STATUS, UNIT_STATUSES, CanonicalTable

MANIFEST_VERSION = "1"
STAMP_VERSION = "1"
MANIFEST_NAME = "run_manifest.json"


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def new_run_id(config_hash: str) -> str:
    """``<UTC timestamp>_<first 8 hex of the config hash>``; sortable and unique per second."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{stamp}_{config_hash[:8]}"


def write_json_atomic(obj: Any, path: str | os.PathLike[str]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, indent=2, sort_keys=True, default=str)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path


# --------------------------------------------------------------------------- unit status

@dataclass
class UnitRecord:
    """Status of one workflow unit (one row of ``analysis_status``)."""

    stage: str
    unit_id: str
    analysis: str
    status: str
    reason_code: str | None = None
    reason: str | None = None
    cache_key: str | None = None
    run_id: str | None = None
    finished_utc: str | None = None
    elapsed_s: float | None = None
    outputs: list[str] = field(default_factory=list)
    #: Exclusion counts, grids, masks and seeds this unit contributes to the manifest.
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in UNIT_STATUSES:
            raise ValueError(f"unit {self.unit_id}: status {self.status!r} not in "
                             f"{UNIT_STATUSES}")

    def row(self) -> dict[str, Any]:
        return {"stage": self.stage, "unit_id": self.unit_id, "analysis": self.analysis,
                "status": self.status, "reason_code": self.reason_code, "reason": self.reason,
                "cache_key": self.cache_key, "run_id": self.run_id,
                "finished_utc": self.finished_utc, "elapsed_s": self.elapsed_s,
                "n_outputs": len(self.outputs)}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> UnitRecord:
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})


def status_table(records: list[UnitRecord]) -> CanonicalTable:
    frame = (pd.DataFrame([r.row() for r in records]) if records
             else ANALYSIS_STATUS.empty())
    return CanonicalTable.build(frame, ANALYSIS_STATUS, {"workflow": "etcompare.workflow"})


# --------------------------------------------------------------------------- cache

def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.@=-]+", "_", name)


def output_identity(path: str | os.PathLike[str]) -> dict[str, Any] | None:
    p = Path(path)
    if not p.is_file():
        return None
    st = p.stat()
    return {"path": str(p), "size": st.st_size, "mtime_ns": st.st_mtime_ns}


class Cache:
    """Per-unit stamps under ``<output_dir>/.etcompare/cache``.

    A disabled cache still writes stamps (they hold each unit's last status for the
    report) but never reports a unit as reusable.
    """

    def __init__(self, output_dir: str | os.PathLike[str], *, enabled: bool = True) -> None:
        self.root = Path(output_dir) / ".etcompare" / "cache"
        self.enabled = enabled

    def stamp_path(self, stage: str, unit_id: str) -> Path:
        return self.root / stage / f"{_safe(unit_id)}.json"

    def read(self, stage: str, unit_id: str) -> dict[str, Any] | None:
        p = self.stamp_path(stage, unit_id)
        if not p.is_file():
            return None
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return d if d.get("stamp_version") == STAMP_VERSION else None

    def valid(self, stage: str, unit_id: str, key: str) -> dict[str, Any] | None:
        """The stamp when the unit's outputs can be reused for ``key``; else ``None``."""
        if not self.enabled:
            return None
        d = self.read(stage, unit_id)
        if d is None or d.get("cache_key") != key or d.get("status") not in (
                "completed", "not_available"):
            return None
        for o in d.get("output_identities", []):
            if output_identity(o["path"]) != o:
                return None
        return d

    def write(self, record: UnitRecord) -> Path:
        d = asdict(record)
        d["stamp_version"] = STAMP_VERSION
        d["output_identities"] = [i for i in (output_identity(p) for p in record.outputs)
                                  if i is not None]
        return write_json_atomic(d, self.stamp_path(record.stage, record.unit_id))

    def records(self) -> list[UnitRecord]:
        out = []
        for p in sorted(self.root.glob("*/*.json")):
            d = json.loads(p.read_text(encoding="utf-8"))
            if d.get("stamp_version") == STAMP_VERSION:
                out.append(UnitRecord.from_dict(d))
        return out


# --------------------------------------------------------------------------- manifest

@dataclass
class RunManifest:
    """One run's record. :meth:`to_dict` / :meth:`from_dict` round-trip through JSON."""

    run_id: str
    command: str
    stages: list[str]
    created_utc: str
    config_path: str | None
    config_sha256: str | None
    config_hash: str
    code_identity: str
    etcompare_version: str
    git: dict[str, Any]
    package_versions: dict[str, str | None]
    upstream_declared: list[dict[str, Any]]
    upstream_detected: list[dict[str, Any]]
    cache: dict[str, Any]
    inputs: list[dict[str, Any]] = field(default_factory=list)
    raster_grids: list[dict[str, Any]] = field(default_factory=list)
    masks: list[dict[str, Any]] = field(default_factory=list)
    seeds: list[dict[str, Any]] = field(default_factory=list)
    exclusion_counts: list[dict[str, Any]] = field(default_factory=list)
    analyses: list[dict[str, Any]] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    status: str = "running"
    finished_utc: str | None = None
    error: str | None = None
    manifest_version: str = MANIFEST_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RunManifest:
        if d.get("manifest_version") != MANIFEST_VERSION:
            raise SchemaError(f"run manifest version {d.get('manifest_version')!r} != "
                              f"{MANIFEST_VERSION}")
        names = {f.name for f in fields(cls)}
        missing = [n for n in ("run_id", "config_hash", "code_identity", "git", "analyses",
                               "status") if n not in d]
        extra = set(d) - names
        if missing or extra:
            raise SchemaError(f"run manifest: missing {missing}, unexpected {sorted(extra)}")
        return cls(**d)

    def write(self, output_dir: str | os.PathLike[str]) -> Path:
        """Write ``runs/<run_id>/run_manifest.json`` and the latest copy; return the first."""
        out = Path(output_dir)
        path = write_json_atomic(self.to_dict(), out / "runs" / self.run_id / MANIFEST_NAME)
        write_json_atomic(self.to_dict(), out / MANIFEST_NAME)
        return path

    @classmethod
    def read(cls, path: str | os.PathLike[str]) -> RunManifest:
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
