"""Validation reports.

A :class:`ValidationReport` collects every issue found in an input before any
join or calculation, so one run surfaces all problems at once.
:meth:`ValidationReport.raise_for_errors` turns error-severity issues into a
:class:`~etcompare.errors.DataValidationError`.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass, field
from typing import Literal

import pandas as pd

from etcompare.errors import DataValidationError

Severity = Literal["error", "warning", "info"]
SEVERITIES: tuple[Severity, ...] = ("error", "warning", "info")


@dataclass(frozen=True)
class Issue:
    """One validation finding.

    ``code`` is a stable snake_case identifier; ``n_rows`` counts affected rows
    when the issue is row-level.
    """

    severity: Severity
    code: str
    message: str
    source: str | None = None
    field: str | None = None
    config_key: str | None = None
    n_rows: int | None = None

    def __post_init__(self) -> None:
        if self.severity not in SEVERITIES:
            raise ValueError(f"severity must be one of {SEVERITIES}, got {self.severity!r}")


@dataclass
class ValidationReport:
    """Ordered collection of :class:`Issue` objects for one input or stage."""

    subject: str
    issues: list[Issue] = field(default_factory=list)

    def add(
        self,
        severity: Severity,
        code: str,
        message: str,
        *,
        source: str | None = None,
        field: str | None = None,
        config_key: str | None = None,
        n_rows: int | None = None,
    ) -> Issue:
        issue = Issue(severity, code, message, source, field, config_key, n_rows)
        self.issues.append(issue)
        return issue

    def error(self, code: str, message: str, **kw: object) -> Issue:
        return self.add("error", code, message, **kw)  # type: ignore[arg-type]

    def warning(self, code: str, message: str, **kw: object) -> Issue:
        return self.add("warning", code, message, **kw)  # type: ignore[arg-type]

    def info(self, code: str, message: str, **kw: object) -> Issue:
        return self.add("info", code, message, **kw)  # type: ignore[arg-type]

    def extend(self, issues: Iterable[Issue]) -> None:
        self.issues.extend(issues)

    def __iter__(self) -> Iterator[Issue]:
        return iter(self.issues)

    def __len__(self) -> int:
        return len(self.issues)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def ok(self) -> bool:
        """True when no error-severity issue was recorded."""
        return not self.errors

    def codes(self, severity: Severity | None = None) -> set[str]:
        return {i.code for i in self.issues if severity is None or i.severity == severity}

    def to_frame(self) -> pd.DataFrame:
        cols = [f for f in Issue.__dataclass_fields__]
        return pd.DataFrame([asdict(i) for i in self.issues], columns=cols)

    def summary(self) -> str:
        lines = [f"{self.subject}: {len(self.errors)} error(s), {len(self.warnings)} warning(s)"]
        for i in self.issues:
            count = f" [{i.n_rows} rows]" if i.n_rows is not None else ""
            lines.append(f"  {i.severity.upper()} {i.code}: {i.message}{count}")
        return "\n".join(lines)

    def raise_for_errors(self) -> None:
        """Raise :class:`DataValidationError` if any error-severity issue exists."""
        if self.errors:
            raise DataValidationError(self.summary(), report=self)
