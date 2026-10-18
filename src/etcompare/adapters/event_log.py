"""Adapter for station event logs (guide step 14).

:func:`adapt_event_table` maps a declared event log (:class:`~etcompare.config.EventTableSpec`)
to the canonical ``station_events`` table. Events are defined **only** from the log: the
adapter takes no model, residual or agreement input, and nothing downstream may add or move
an event.

Rules
-----
1. **Refused** (``ContractError`` / ``DataValidationError``, nothing is returned): a missing
   source file, an unmapped column, a null or duplicated event ID anywhere in the table, and,
   for rows of mapped stations, a null station or timestamp, an unparseable timestamp, an end
   before the start, an ``event_type`` that is not declared, and a non-null ``event_source``
   without a declared evidence class.
2. **Station day.** ``day_rule: station_local_date``: the timestamp is declared to be in the
   station's own timezone (checked against the station declaration), and its calendar date is
   the station day. No timestamp is converted or shifted. An entry logged at exactly 00:00 is
   a date-only entry (``start_time`` null).
3. **Excluded with a reason** (exclusion log, never silently): rows of stations that are not
   in ``site_mapping`` (one record per source station), and events of mapped stations that lie
   entirely outside the declared station record (``site_windows``).
4. **Kept on every event:** ``observer``, ``event_source``, ``event_notes`` and every source
   column (prefix ``src_``, D-020), plus the declared ``category``, ``management_kind``,
   ``analysis_role`` and ``evidence_class``. Instrument and confounder events are
   ``overlap_only``: they stay visible and are never analysed as treatments.
   ``management_record`` is True only for events listed in a declared
   ``management_records`` confirmation (which must name kept management events); the
   logged ``event_source`` and its evidence class are not changed by it.
5. **Approximate dates.** ``approximate`` is True only when one of the declared
   ``approximate_date_patterns`` (case-insensitive regular expressions) is found in the notes;
   ``approximate_rule`` records which. Nothing else (missing time, source, observer) makes a
   date approximate.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pandas as pd

from etcompare.adapters.station import SOURCE_PREFIX
from etcompare.config import EventTableSpec, StationSpec, to_plain
from etcompare.errors import ContractError, DataValidationError, SchemaError
from etcompare.io.tables import read_any
from etcompare.provenance import file_sha256
from etcompare.results import AdapterResult, ExclusionLog, format_key
from etcompare.schemas import STATION_EVENTS, CanonicalTable
from etcompare.validation import ValidationReport

STAGE = "event_adapter"


def _text(s: pd.Series) -> pd.Series:
    """Source values as stripped text; nulls and blank strings stay null."""
    if pd.api.types.is_integer_dtype(s.dtype):
        return s.astype("string")
    out = s.map(lambda v: None if v is None or (not isinstance(v, str) and pd.isna(v))
                else str(v).strip())
    out = out.astype("string")
    return out.mask(out == "")


def _timestamps(s: pd.Series, fmt: str, where: str, report: ValidationReport) -> pd.Series:
    if isinstance(s.dtype, pd.DatetimeTZDtype):
        report.error("timezone_aware_timestamps", f"{where}: timezone-aware timestamps; the "
                     "day rule takes naive timestamps declared in the station's timezone",
                     field=where)
        return pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns]")
    if pd.api.types.is_datetime64_dtype(s.dtype):
        parsed = s.astype("datetime64[ns]")
    else:
        text = _text(s)
        parsed = pd.to_datetime(text, format=fmt, errors="coerce").astype("datetime64[ns]")
        bad = text.notna() & parsed.isna()
        if bad.any():
            report.error("unparseable_timestamps", f"{where}: {int(bad.sum())} value(s) do not "
                         f"match {fmt!r}, e.g. {text[bad].iloc[0]!r}", field=where,
                         n_rows=int(bad.sum()))
    return parsed


def _null(s: pd.Series) -> pd.Series:
    """Null or blank source values."""
    if pd.api.types.is_datetime64_any_dtype(s.dtype):
        return s.isna()
    return _text(s).isna()


def approximate_flags(notes: pd.Series, patterns: tuple[str, ...]) -> pd.Series:
    """The first declared pattern found in each note (case-insensitive), else null."""
    compiled = [(p, re.compile(p, re.IGNORECASE)) for p in patterns]

    def first(v: object) -> str | None:
        if v is None or v is pd.NA or not isinstance(v, str):
            return None
        return next((p for p, rx in compiled if rx.search(v)), None)

    return notes.map(first).astype("string")


def adapt_event_table(source: str | os.PathLike[str] | pd.DataFrame, spec: EventTableSpec, *,
                      stations: Mapping[str, StationSpec]) -> AdapterResult:
    """Map a station event log to the canonical ``station_events`` table.

    ``stations`` are the declared stations; every mapped site must be among them, with the
    log's declared timezone. See the module docstring for what is refused, excluded and kept.
    """
    spec.check_stations(stations)
    key = f"event_tables.{spec.name}"
    if isinstance(source, pd.DataFrame):
        df, where, sha = source.copy(), f"event table {spec.name}", None
    else:
        path = Path(source)
        if not path.is_file():
            raise ContractError(f"{key}: event table {path} not found", input=str(path),
                                field="path", config_key=f"{key}.path")
        df, where, sha = read_any(path, dtype=str), os.fspath(path), file_sha256(path)
    df = df.reset_index(drop=True)
    report = ValidationReport(where)
    cols = spec.columns
    missing = [c for c in cols.values() if c not in df.columns]
    if missing:
        raise SchemaError(f"{where}: mapped column(s) {missing} not found; available: "
                          f"{list(df.columns)}")

    # Event IDs are a key of the whole log, so they are checked on every row.
    ids = _text(df[cols["event_id"]])
    if ids.isna().any():
        report.error("null_event_ids", f"{int(ids.isna().sum())} event(s) without an event ID",
                     field=cols["event_id"], n_rows=int(ids.isna().sum()))
    dup = ids.notna() & ids.duplicated(keep=False)
    if dup.any():
        report.error("duplicate_event_ids", f"event ID(s) {sorted(set(ids[dup]))[:10]} occur "
                     "more than once", field=cols["event_id"], n_rows=int(dup.sum()))

    station = _text(df[cols["site"]])
    exclusions = ExclusionLog()
    mapped = station.isin(list(spec.site_mapping)).fillna(False).astype(bool)
    for sid, n in station[~mapped].fillna("<null>").value_counts().sort_index().items():
        exclusions.add(STAGE, format_key(event_table=spec.name, source_station=sid),
                       "station_not_declared", severity="warning" if sid == "<null>" else "info",
                       detail=f"{n} event(s) of a station not in site_mapping")
    rows = df.loc[mapped].reset_index(drop=True)
    ids = ids[mapped].reset_index(drop=True)
    station = station[mapped].reset_index(drop=True)

    ts = _timestamps(rows[cols["timestamp"]], spec.timestamp_format, cols["timestamp"], report)
    null_ts = _null(rows[cols["timestamp"]])
    if null_ts.any():
        report.error("null_timestamps", f"{int(null_ts.sum())} event(s) without a timestamp",
                     field=cols["timestamp"], n_rows=int(null_ts.sum()))
    if "end_timestamp" in cols:
        end_ts = _timestamps(rows[cols["end_timestamp"]], spec.timestamp_format,
                             cols["end_timestamp"], report)
        no_end = _null(rows[cols["end_timestamp"]])
        if no_end.any():
            report.info("single_day_events", f"{int(no_end.sum())} event(s) without an end "
                        "timestamp last one station day", n_rows=int(no_end.sum()))
        end_ts = end_ts.where(~no_end, ts)
    else:
        end_ts = ts
    reversed_ = end_ts.notna() & ts.notna() & (end_ts < ts)
    if reversed_.any():
        report.error("event_end_before_start", f"{int(reversed_.sum())} event(s) end before "
                     "they start", n_rows=int(reversed_.sum()))

    etype = _text(rows[cols["event_type"]])
    undeclared = etype[etype.isna() | ~etype.isin(list(spec.event_types))].fillna("<null>")
    if len(undeclared):
        report.error("undeclared_event_types", f"event type(s) {dict(undeclared.value_counts())} "
                     f"are not declared in {key}.event_types", field=cols["event_type"],
                     config_key=f"{key}.event_types", n_rows=len(undeclared))
    esrc = _text(rows[cols["event_source"]])
    unmapped = esrc[esrc.notna() & ~esrc.isin(list(spec.evidence_classes))]
    if len(unmapped):
        report.error("undeclared_event_sources", f"event source(s) {dict(unmapped.value_counts())}"
                     f" have no evidence class in {key}.evidence_classes",
                     field=cols["event_source"], config_key=f"{key}.evidence_classes",
                     n_rows=len(unmapped))
    if not report.ok:
        raise DataValidationError(report.summary(), report=report)

    site = station.map(spec.site_mapping).astype("string")
    start = ts.dt.normalize()
    timed = ts != start
    types = etype.map(lambda t: spec.event_types[t])
    category = types.map(lambda t: t.category)
    notes = _text(rows[cols["notes"]])
    rule = approximate_flags(notes, spec.approximate_date_patterns)
    frame = pd.DataFrame({
        "site_id": site, "event_id": ids, "event_table": spec.name,
        "source_station_id": station, "start_date": start, "end_date": end_ts.dt.normalize(),
        "start_time": ts.dt.strftime("%H:%M:%S").where(timed, None),
        "event_type": etype, "category": category,
        "management_kind": types.map(lambda t: t.management_kind),
        "analysis_role": category.map(lambda c: "management_event" if c == "management"
                                      else "overlap_only"),
        "event_source": esrc,
        "evidence_class": esrc.map(lambda s: "unknown" if s is None or s is pd.NA
                                   else spec.evidence_classes[s]),
        "observer": _text(rows[cols["observer"]]), "event_notes": notes,
        "approximate": rule.notna(), "approximate_rule": rule,
    })

    # Events entirely outside the declared station record are rejected, with a reason.
    lo = frame["site_id"].map(lambda s: pd.Timestamp(spec.site_windows[s]["start"]))
    hi = frame["site_id"].map(lambda s: pd.Timestamp(spec.site_windows[s]["end"]))
    outside = (frame["end_date"] < lo) | (frame["start_date"] > hi)
    for r in frame[outside].itertuples():
        exclusions.add(STAGE, format_key(event_table=spec.name, site_id=r.site_id,
                                         event_id=r.event_id),
                       "outside_station_record", severity="info",
                       detail=f"{r.event_type} on {r.start_date.date()} is outside the record "
                       f"{spec.site_windows[r.site_id]['start']}..{spec.site_windows[r.site_id]['end']}")
    edge = ~outside & ((frame["start_date"] < lo) | (frame["end_date"] > hi))
    if edge.any():
        report.warning("event_crosses_record_edge", f"{int(edge.sum())} event(s) extend beyond "
                       "the station record; kept with their logged dates", n_rows=int(edge.sum()))
    src = rows.add_prefix(SOURCE_PREFIX)
    frame = pd.concat([frame, src], axis=1).loc[~outside].reset_index(drop=True)

    # Management-record confirmations: only declared, only for kept management events.
    confirmed = spec.confirmed_events()
    kept = frame.set_index("event_id")["category"]
    missing_ids = sorted(set(confirmed) - set(kept.index))
    if missing_ids:
        report.error("confirmed_event_not_found", f"management_records list event(s) "
                     f"{missing_ids} that are not kept events of a mapped station inside its "
                     "record", config_key=f"{key}.management_records")
    not_mgmt = sorted(e for e in set(confirmed) & set(kept.index) if kept[e] != "management")
    if not_mgmt:
        report.error("confirmed_event_not_management", f"management_records list "
                     f"non-management event(s) {not_mgmt}",
                     config_key=f"{key}.management_records")
    if not report.ok:
        raise DataValidationError(report.summary(), report=report)
    frame["management_record"] = frame["event_id"].isin(list(confirmed))
    frame["management_record_source"] = frame["event_id"].map(
        lambda e: confirmed[e].label if e in confirmed else None)

    unknown_mgmt = frame[(frame["category"] == "management")
                         & (frame["evidence_class"] == "unknown")]
    if len(unknown_mgmt):
        report.warning("management_evidence_unknown", f"{len(unknown_mgmt)} management event(s) "
                       "have an unlabelled source (evidence unknown)", n_rows=len(unknown_mgmt))
    counts: dict[str, Any] = {
        "n_events": len(frame), "n_rows_read": len(df),
        "n_excluded_station_not_declared": int((~mapped).sum()),
        "n_excluded_outside_station_record": int(outside.sum()),
        "by_category": {str(k): int(v) for k, v in frame["category"].value_counts().items()},
        "management_by_evidence_class": {
            str(k): int(v) for k, v in
            frame.loc[frame["category"] == "management", "evidence_class"].value_counts().items()},
        "n_approximate": int(frame["approximate"].sum()),
        "n_management_record": int(frame["management_record"].sum()),
    }
    report.info("events", f"{counts['n_events']} event(s) kept: {counts['by_category']}; "
                f"management evidence {counts['management_by_evidence_class']}; "
                f"{counts['n_approximate']} approximate")
    attributes: dict[str, Any] = {
        "adapter": "etcompare.adapters.event_log.adapt_event_table",
        "source": where, "source_sha256": sha, "mapping": to_plain(spec),
        "day_rule": f"{spec.day_rule}: calendar date of the timestamp in "
                    f"{spec.timestamp_timezone}; no conversion or shift",
        "events_defined_from": "event_log_only", "counts": counts,
        "source_column_prefix": SOURCE_PREFIX,
    }
    table = CanonicalTable.build(frame, STATION_EVENTS, attributes, source=where)
    return AdapterResult(table, report, exclusions)
