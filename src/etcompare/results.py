"""Result objects shared by adapters and later analysis stages."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from etcompare.schemas import EXCLUSIONS, CanonicalTable, conform
from etcompare.validation import Severity, ValidationReport


def format_key(**parts: object) -> str:
    """Stable text key for an input row or file: ``site_id=XX-UTJ|date=2024-06-05``."""
    out = []
    for k, v in parts.items():
        if isinstance(v, pd.Timestamp):
            v = v.date().isoformat()
        out.append(f"{k}={v}")
    return "|".join(out)


@dataclass
class ExclusionLog:
    """Accumulates exclusion records (stage, key, reason, severity).

    An exclusion is a row-level decision that affects a comparison: a row or
    file left out, or retained with a flag. Validation of whole inputs goes to
    :class:`~etcompare.validation.ValidationReport` instead.
    """

    records: list[dict[str, Any]] = field(default_factory=list)

    def add(
        self,
        stage: str,
        input_key: str,
        reason_code: str,
        *,
        severity: Severity = "warning",
        affected_comparison: str | None = None,
        detail: str | None = None,
    ) -> None:
        self.records.append({
            "stage": stage, "input_key": input_key, "reason_code": reason_code,
            "severity": severity, "affected_comparison": affected_comparison, "detail": detail,
        })

    def add_many(
        self, stage: str, keys: Iterable[str], reason_code: str, **kw: Any
    ) -> None:
        for k in keys:
            self.add(stage, k, reason_code, **kw)

    def extend(self, other: ExclusionLog) -> None:
        self.records.extend(other.records)

    def __len__(self) -> int:
        return len(self.records)

    def to_frame(self) -> pd.DataFrame:
        if not self.records:
            return EXCLUSIONS.empty()
        return conform(pd.DataFrame(self.records), EXCLUSIONS)

    def counts(self) -> pd.Series:
        """Number of records per (stage, reason_code)."""
        df = self.to_frame()
        return df.groupby(["stage", "reason_code"]).size()


@dataclass(frozen=True)
class AdapterResult:
    """Output of an adapter: canonical table, validation report and exclusions."""

    table: CanonicalTable
    report: ValidationReport
    exclusions: ExclusionLog = field(default_factory=ExclusionLog)

    @property
    def frame(self) -> pd.DataFrame:
        return self.table.frame

    @property
    def attributes(self) -> Mapping[str, Any]:
        return self.table.attributes
