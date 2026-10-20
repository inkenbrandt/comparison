"""Atomic file writes (guide step 18).

Every output is written to a temporary file in the destination directory and then
renamed over the destination with :func:`os.replace`, which is atomic on one volume. A
crash or a full disk therefore leaves either the previous file or the new one, never a
half-written file that a later run could take as complete. The temporary file is removed
when writing fails.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pandas as pd


@contextmanager
def atomic_path(path: str | os.PathLike[str]) -> Iterator[Path]:
    """Yield a temporary path next to ``path``; rename it over ``path`` on success.

    The temporary name keeps ``path``'s suffix, so writers that infer a format from it
    behave the same.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.stem}.", suffix=f".tmp{path.suffix}",
                               dir=path.parent)
    os.close(fd)
    try:
        yield Path(tmp)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_text_atomic(text: str, path: str | os.PathLike[str], *,
                      encoding: str = "utf-8") -> Path:
    """:meth:`Path.write_text`, atomically (same newline translation)."""
    with atomic_path(path) as tmp:
        tmp.write_text(text, encoding=encoding)
    return Path(path)


def write_csv_atomic(frame: pd.DataFrame, path: str | os.PathLike[str], **kw: Any) -> Path:
    """:meth:`DataFrame.to_csv`, atomically (``index=False`` unless given)."""
    kw.setdefault("index", False)
    with atomic_path(path) as tmp:
        frame.to_csv(tmp, **kw)
    return Path(path)


def write_json_atomic(obj: Any, path: str | os.PathLike[str]) -> Path:
    """Indented, key-sorted JSON, atomically."""
    with atomic_path(path) as tmp, tmp.open("w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, sort_keys=True, default=str)
    return Path(path)
