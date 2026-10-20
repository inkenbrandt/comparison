"""Exception hierarchy.

Every error names what is wrong and, where a declaration is missing, the
configuration key that supplies it (input_contract.md section 9).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from etcompare.validation import ValidationReport


class EtcompareError(Exception):
    """Base class for all etcompare errors."""


class ContractError(EtcompareError, ValueError):
    """A mandatory semantic (unit, day definition, mapping, label) was not declared.

    Raised before any join or calculation. Never resolved by guessing.
    """

    def __init__(
        self,
        message: str,
        *,
        input: str | None = None,
        field: str | None = None,
        config_key: str | None = None,
    ) -> None:
        if config_key and "(config:" not in message:
            message = f"{message} (config: {config_key})"
        super().__init__(message)
        self.input = input
        self.field = field
        self.config_key = config_key


class SchemaError(EtcompareError, ValueError):
    """A table does not conform to its canonical schema."""


class DuplicateKeyError(SchemaError):
    """A table has duplicated or null primary-key values."""


class DataValidationError(EtcompareError, ValueError):
    """Data failed validation; ``report`` holds every issue found."""

    def __init__(self, message: str, report: ValidationReport | None = None) -> None:
        super().__init__(message)
        self.report = report


class FilenameParseError(EtcompareError, ValueError):
    """A filename is ambiguous, malformed or names an unregistered model."""


class DuplicateAssetError(EtcompareError):
    """Two assets claim the same identity; an explicit selection policy is required."""


class GridError(EtcompareError, ValueError):
    """A raster grid is missing a CRS, is degenerate, or needs an unsupported transfer."""


class OptionalDependencyError(EtcompareError, ImportError):
    """An optional dependency needed for this operation is not installed."""


class InputMissingError(EtcompareError, FileNotFoundError):
    """A file that was indexed no longer exists (never retried)."""


class SystematicFailureError(DataValidationError):
    """Too many inputs failed for skipping to be safe (e.g. every raster of a model).

    ``details`` holds the counts recorded in the run manifest.
    """

    def __init__(self, message: str, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}
