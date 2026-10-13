"""Structured logging.

The library only emits records; it never configures the root logger. Modules
log an ``event`` name plus key-value fields::

    log_event(logger, "station_table_read", path=str(path), n_rows=492)

Applications call :func:`configure_logging` to print records as JSON lines
(or plain text) on stderr.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

PACKAGE_LOGGER = "etcompare"
_FIELDS_ATTR = "etcompare_fields"

logging.getLogger(PACKAGE_LOGGER).addHandler(logging.NullHandler())


def get_logger(name: str) -> logging.Logger:
    """Return a logger in the ``etcompare`` hierarchy."""
    if name != PACKAGE_LOGGER and not name.startswith(PACKAGE_LOGGER + "."):
        name = f"{PACKAGE_LOGGER}.{name}"
    return logging.getLogger(name)


def log_event(
    logger: logging.Logger, event: str, level: int = logging.INFO, **fields: Any
) -> None:
    """Log ``event`` with structured ``fields`` attached to the record."""
    if logger.isEnabledFor(level):
        logger.log(level, event, extra={_FIELDS_ATTR: {"event": event, **fields}})


class JsonFormatter(logging.Formatter):
    """One JSON object per record: time, level, logger, event and fields."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
        }
        fields = getattr(record, _FIELDS_ATTR, None)
        if fields:
            payload.update(fields)
        else:
            payload["message"] = record.getMessage()
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class KeyValueFormatter(logging.Formatter):
    """Human-readable ``LEVEL logger event key=value ...`` lines."""

    def format(self, record: logging.LogRecord) -> str:
        fields = dict(getattr(record, _FIELDS_ATTR, None) or {})
        event = fields.pop("event", record.getMessage())
        parts = [record.levelname, record.name, str(event)]
        parts += [f"{k}={v}" for k, v in fields.items()]
        line = " ".join(parts)
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def configure_logging(level: int | str = logging.INFO, *, json_lines: bool = True) -> None:
    """Attach a stderr handler to the package logger (for applications and the CLI)."""
    logger = logging.getLogger(PACKAGE_LOGGER)
    for handler in list(logger.handlers):
        if getattr(handler, "_etcompare_owned", False):
            logger.removeHandler(handler)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter() if json_lines else KeyValueFormatter())
    handler._etcompare_owned = True  # type: ignore[attr-defined]
    logger.addHandler(handler)
    logger.setLevel(level)
