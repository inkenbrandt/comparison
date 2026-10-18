"""Management-event responses (guide step 14).

:func:`analyse_events` describes how the observed and modeled series of one
:class:`~etcompare.cohorts.Cohort` change around logged management events, under an
:class:`~etcompare.config.EventAnalysisSpec`. It writes three tables:

- ``event_responses``: baseline, change, cumulative response and recovery per event and
  series (observed, modeled, and modeled minus observed);
- ``event_overlaps``: every other logged event inside an event's windows;
- ``event_conditions``: declared weather variables over the same windows.

Events
------
Events come only from a ``station_events`` table built by
:func:`etcompare.adapters.event_log.adapt_event_table`, i.e. from the station log. They
are never defined, moved or selected from residuals or agreement: this function receives
the cohort only to read the values around the logged dates. Only ``management`` events of
the declared ``management_kinds`` are analysed; instrument and confounder events (station
visits, program updates, sensor moves, weather notes) appear only as overlaps.

Windows (day 0 = the event's station day)
-----------------------------------------
- **pre** (baseline): days ``-pre_days .. -1`` before the start;
- **post**: days ``post_offset_days .. post_offset_days + post_days - 1`` after the end;
- **recovery search**: trailing windows of ``recovery_smoothing_days`` lying entirely after
  ``post_offset_days``, ending no later than ``recovery_max_days``.

Windows are calendar days. Only cohort days have values, so a missing day is a gap, never
a zero, and every model of a frozen cohort uses the same days.

Quantities (computed separately for the observed and the modeled series)
--------------------------------------------------------------------------
- ``baseline_mean_mm`` and ``post_mean_mm``: window means, each only when at least
  ``min_window_completeness`` of the window's calendar days are cohort days (otherwise the
  status is ``incomplete_baseline`` and/or ``incomplete_post_window``).
- ``change_mm = post_mean - baseline_mean``; ``relative_change_percent`` only for a
  baseline above ``baseline_min_mm``.
- ``cumulative_response_mm = sum over the post window of (ET - baseline)``, only when every
  post day is present (a sum over a gap would be a partial sum).
- ``recovery_days``: days after the event's end until the trailing mean first reaches
  ``recovery_fraction x baseline``. It is identifiable only while the smoothing windows are
  complete: a gap before recovery gives ``not_identifiable_gap``, a later management event
  ``interrupted_by_management_event``, and no recovery by ``recovery_max_days``
  ``not_recovered_within_horizon``.
- With ``normalize_by`` (e.g. gridMET ETr), the same baseline/post means of ET divided by
  that variable, and their change. This shows how much of a change follows the weather; it
  is not a weather correction.
- ``modeled_minus_observed`` rows hold the modeled quantity minus the observed one.

Interpretation
--------------
The design is an uncontrolled before/after comparison (``design =
before_after_uncontrolled``; no control site or matched period exists). A before/after ET
difference is therefore never causal evidence, for example of water savings: weather,
crop stage and other events change at the same time. Every row carries the flags
``before_after_uncontrolled`` and ``not_causal_evidence``, and also:

- ``evidence_<class>`` and ``management_record`` / ``no_management_record``, from the log;
- ``approximate_date`` when the log calls the date approximate (``approximate_policy:
  exclude`` leaves such events out, with a report entry);
- ``overlapping_management_event``, ``instrument_event_in_window`` and
  ``confounder_event_in_window``, with the IDs in ``overlapping_event_ids``;
- ``includes_gap_filled_reference_days`` and ``shared_gridmet_drivers`` when a window holds
  gap-filled reference days (D-005);
- ``days_differ_across_models`` for cohorts that do not freeze days across models;
- the cohort rows' own flags (e.g. ``day_alignment=unverified``).

Unknown or missing weather columns skip only their condition rows and the normalisation.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, TypeAlias

import numpy as np
import pandas as pd

from etcompare.cohorts import Cohort
from etcompare.config import EventAnalysisSpec, to_plain
from etcompare.errors import ContractError
from etcompare.schemas import (
    EVENT_CONDITIONS,
    EVENT_OVERLAPS,
    EVENT_RESPONSES,
    CanonicalTable,
    TableSchema,
)
from etcompare.validation import Issue, ValidationReport

_SERIES = ["model", "reference_variant", "extraction_method", "footprint_set_id"]
_DAY = pd.Timedelta(days=1)

StationTables: TypeAlias = CanonicalTable | pd.DataFrame | Sequence[CanonicalTable | pd.DataFrame]


@dataclass(frozen=True)
class EventAnalysisResult:
    """Event responses, overlaps and conditions of one cohort. Immutable."""

    spec: EventAnalysisSpec
    comparison_id: str
    _tables: Mapping[str, CanonicalTable] = field(repr=False)
    _issues: tuple[Issue, ...] = field(repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_tables", MappingProxyType(dict(self._tables)))

    def _get(self, name: str) -> CanonicalTable:
        t = self._tables[name]
        return CanonicalTable(t.schema, t.frame.copy(), dict(t.attributes))

    @property
    def responses(self) -> CanonicalTable:
        return self._get("event_responses")

    @property
    def overlaps(self) -> CanonicalTable:
        return self._get("event_overlaps")

    @property
    def conditions(self) -> CanonicalTable:
        return self._get("event_conditions")

    @property
    def report(self) -> ValidationReport:
        return ValidationReport(f"events {self.comparison_id}", list(self._issues))

    def tables(self) -> dict[str, CanonicalTable]:
        return {name: self._get(name) for name in self._tables}


def _frame(rows: list[dict[str, Any]], schema: TableSchema) -> pd.DataFrame:
    frame = pd.DataFrame(rows, columns=schema.names) if rows else schema.empty()
    for c in schema.columns:
        if c.kind in ("float", "int"):
            frame[c.name] = pd.to_numeric(frame[c.name])
    return frame.sort_values(list(schema.primary_key), ignore_index=True)


def _station_frame(station_daily: StationTables | None) -> pd.DataFrame | None:
    if station_daily is None:
        return None
    items = station_daily if isinstance(station_daily, Sequence) else [station_daily]
    frames = [t.frame if isinstance(t, CanonicalTable) else t for t in items]
    return pd.concat(frames, ignore_index=True) if frames else None


def _days(first: pd.Timestamp, last: pd.Timestamp) -> pd.DatetimeIndex:
    return pd.date_range(first, last, freq="D")


def _check_events(events: CanonicalTable, spec: EventAnalysisSpec) -> pd.DataFrame:
    if not isinstance(events, CanonicalTable) or events.schema.name != "station_events" or \
            events.attributes.get("events_defined_from") != "event_log_only":
        raise ContractError("events must be a station_events table from "
                            "etcompare.adapters.event_log.adapt_event_table; events are "
                            "defined only from the station event log")
    f = events.frame
    other = sorted(set(f["event_table"]) - {spec.event_table})
    if other:
        raise ContractError(f"event analysis {spec.event_analysis_id} reads event table "
                            f"{spec.event_table!r}, but the events come from {other}")
    return f


def recovery(values: pd.Series, end: pd.Timestamp, baseline: float | None,
             spec: EventAnalysisSpec, next_management: pd.Timestamp | None
             ) -> tuple[int | None, str]:
    """Recovery day after ``end`` and its status (see the module docstring).

    ``values`` is the series' daily values indexed by station day (cohort days only).
    """
    if baseline is None:
        return None, "baseline_unavailable"
    if baseline <= spec.baseline_min_mm:
        return None, "baseline_below_min"
    target = spec.recovery_fraction * baseline
    k = spec.recovery_smoothing_days
    first = end + (spec.post_offset_days + k - 1) * _DAY
    for d in _days(first, end + spec.recovery_max_days * _DAY):
        if next_management is not None and d >= next_management:
            return None, "interrupted_by_management_event"
        win = _days(d - (k - 1) * _DAY, d)
        if not win.isin(values.index).all():
            return None, "not_identifiable_gap"
        if float(values.loc[win].mean()) >= target:
            return int((d - end).days), "recovered"
    return None, "not_recovered_within_horizon"


def _window_mean(values: pd.Series, days: pd.DatetimeIndex, need: float
                 ) -> tuple[float | None, int]:
    present = days[days.isin(values.index)]
    n = len(present)
    if n == 0 or n / len(days) < need:
        return None, n
    return float(values.loc[present].mean()), n


def analyse_events(cohort: Cohort, events: CanonicalTable, spec: EventAnalysisSpec, *,
                   station_daily: StationTables | None = None) -> EventAnalysisResult:
    """Responses of ``cohort`` to the logged management events (module docstring).

    ``events`` is the ``station_events`` table of ``spec.event_table``; ``station_daily``
    supplies the declared weather variables (optional: without it they are skipped).
    """
    cid = cohort.comparison_id
    aid = spec.event_analysis_id
    report = ValidationReport(f"events {cid}/{aid}")
    ev = _check_events(events, spec)
    station = _station_frame(station_daily)
    weather: dict[str, pd.DataFrame] = {}
    for name, var in spec.weather_variables.items():
        if station is None or var.column not in station.columns:
            report.warning("weather_variable_unavailable", f"weather variable {name} "
                           f"({var.column}) is not in the station table; its conditions and "
                           "any normalisation by it are skipped")
            continue
        values = pd.to_numeric(station[var.column], errors="coerce")
        weather[name] = pd.DataFrame({"site_id": station["site_id"], "date": station["date"],
                                      "value": values})
    norm = spec.normalize_by if spec.normalize_by in weather else None

    paired = cohort.paired.frame
    frozen = cohort.spec.freeze_models
    resp: list[dict[str, Any]] = []
    overlaps: list[dict[str, Any]] = []
    conditions: list[dict[str, Any]] = []
    sites = sorted(paired["site_id"].unique()) if len(paired) else []
    w = spec.windows
    horizon = max(w["post"][1], w["recovery"][1])
    for site in sites:
        site_ev = ev[ev["site_id"] == site].sort_values(["start_date", "event_id"])
        mgmt = site_ev[(site_ev["category"] == "management")
                       & site_ev["management_kind"].isin(list(spec.management_kinds))]
        if spec.approximate_policy == "exclude" and mgmt["approximate"].any():
            dropped = mgmt.loc[mgmt["approximate"].astype(bool), "event_id"].tolist()
            report.info("approximate_events_excluded", f"{site}: event(s) {dropped} have "
                        "approximate dates and are excluded (approximate_policy: exclude)")
            mgmt = mgmt[~mgmt["approximate"].astype(bool)]
        if not len(mgmt):
            report.info("no_management_events", f"{site}: no management event of kinds "
                        f"{list(spec.management_kinds)} in the event table")
            continue
        all_mgmt = site_ev[site_ev["category"] == "management"]
        site_weather = {n: d[d["site_id"] == site].set_index("date")["value"]
                        for n, d in weather.items()}
        contexts = []
        for e in mgmt.itertuples():
            start, end = e.start_date, e.end_date
            pre = _days(start + w["pre"][0] * _DAY, start + w["pre"][1] * _DAY)
            post = _days(end + w["post"][0] * _DAY, end + w["post"][1] * _DAY)
            span_end = end + horizon * _DAY
            later = all_mgmt[(all_mgmt["start_date"] > end)
                             & (all_mgmt["start_date"] <= span_end)]["start_date"]
            next_mgmt = later.min() if len(later) else None
            ov = site_ev[(site_ev["event_id"] != e.event_id) & (site_ev["end_date"] >= pre[0])
                         & (site_ev["start_date"] <= span_end)]
            ov_flags: set[str] = set()
            for o in ov.itertuples():
                d = o.start_date
                win = ("pre" if d < start else "event" if d <= end else
                       "post" if d <= post[-1] else "recovery")
                overlaps.append({"event_analysis_id": aid, "site_id": site,
                                 "event_id": e.event_id, "other_event_id": o.event_id,
                                 "other_event_type": o.event_type, "other_category": o.category,
                                 "other_analysis_role": o.analysis_role,
                                 "other_start_date": d, "offset_days": int((d - start).days),
                                 "window": win, "other_event_notes": o.event_notes})
                ov_flags.add({"management": "overlapping_management_event",
                              "instrument": "instrument_event_in_window",
                              "confounder": "confounder_event_in_window"}[o.category])
            for name, var in spec.weather_variables.items():
                if name not in site_weather:
                    continue
                vals = site_weather[name]
                for label, days in (("pre", pre), ("post", post)):
                    x = vals.reindex(days).dropna()
                    value = (None if not len(x) else float(x.mean()) if var.aggregation == "mean"
                             else float(x.sum()))
                    conditions.append({"event_analysis_id": aid, "site_id": site,
                                       "event_id": e.event_id, "variable": name,
                                       "window": label, "column": var.column,
                                       "aggregation": var.aggregation, "value": value,
                                       "units": var.units, "n_days": len(x),
                                       "n_days_expected": len(days)})
            flags = {"before_after_uncontrolled", "not_causal_evidence",
                     f"evidence_{e.evidence_class}",
                     "management_record" if e.management_record else "no_management_record",
                     *ov_flags}
            if e.approximate:
                flags.add("approximate_date")
            if not frozen:
                flags.add("days_differ_across_models")
            if spec.normalize_by and norm is None:
                flags.add("not_normalized_weather_unavailable")
            contexts.append((e, pre, post, next_mgmt, flags,
                             ";".join(sorted(ov["event_id"])) or None))

        site_rows = paired[paired["site_id"] == site]
        for keys, s in site_rows.groupby(["cohort_id", *_SERIES], sort=True):
            coh, *series = keys
            s = s.set_index("date").sort_index()
            filled = s.index[s["reference_provenance"] == "corrected_filled"]
            row_flags = {t for v in s["flags"].dropna() for t in str(v).split(";") if t}
            ratio_den = (site_weather[norm].reindex(s.index) if norm else None)
            for e, pre, post, next_mgmt, flags, ov_ids in contexts:
                n_pre_fill = int(pre.isin(filled).sum())
                n_post_fill = int(post.isin(filled).sum())
                f = set(flags) | row_flags
                if n_pre_fill or n_post_fill:
                    f |= {"includes_gap_filled_reference_days", "shared_gridmet_drivers"}
                base = {"event_analysis_id": aid, "comparison_id": cid, "cohort_id": coh,
                        "site_id": site, "event_id": e.event_id,
                        **dict(zip(_SERIES, series, strict=True)),
                        "event_start_date": e.start_date, "event_end_date": e.end_date,
                        "management_kind": e.management_kind,
                        "evidence_class": e.evidence_class,
                        "management_record": bool(e.management_record),
                        "approximate": bool(e.approximate),
                        "design": "before_after_uncontrolled",
                        "pre_start": pre[0], "pre_end": pre[-1], "post_start": post[0],
                        "post_end": post[-1], "n_pre_days_expected": len(pre),
                        "n_post_days_expected": len(post),
                        "n_pre_days_reference_filled": n_pre_fill,
                        "n_post_days_reference_filled": n_post_fill,
                        "overlapping_event_ids": ov_ids, "flags": ";".join(sorted(f))}
                out = {}
                for name, col in (("observed", "observed_mm"), ("modeled", "modeled_mm")):
                    out[name] = _quantities(s[col].astype(float), ratio_den, e, pre, post,
                                            next_mgmt, spec)
                    resp.append({**base, "series": name, **out[name]})
                resp.append({**base, "series": "modeled_minus_observed",
                             **_difference(out["observed"], out["modeled"])})
    attrs = {"workflow": "etcompare.events.analyse_events", "event_analysis": to_plain(spec),
             "comparison_id": cid, "cohort_membership_sha256": cohort.fingerprint,
             "event_table_sha256": events.attributes.get("source_sha256"),
             "events_defined_from": "event_log_only",
             "design": "before_after_uncontrolled; not causal evidence",
             "normalized_by": norm}
    tables = {"event_responses": CanonicalTable.build(_frame(resp, EVENT_RESPONSES),
                                                      EVENT_RESPONSES, attrs, source=cid),
              "event_overlaps": CanonicalTable.build(
                  _frame(_unique(overlaps, EVENT_OVERLAPS), EVENT_OVERLAPS), EVENT_OVERLAPS,
                  attrs, source=cid),
              "event_conditions": CanonicalTable.build(
                  _frame(conditions, EVENT_CONDITIONS), EVENT_CONDITIONS, attrs, source=cid)}
    return EventAnalysisResult(spec, cid, tables, tuple(report.issues))


def _unique(rows: list[dict[str, Any]], schema: TableSchema) -> list[dict[str, Any]]:
    seen: dict[tuple[Any, ...], dict[str, Any]] = {}
    for r in rows:
        seen.setdefault(tuple(r[k] for k in schema.primary_key), r)
    return list(seen.values())


def _quantities(v: pd.Series, den: pd.Series | None, e: Any, pre: pd.DatetimeIndex,
                post: pd.DatetimeIndex, next_mgmt: pd.Timestamp | None,
                spec: EventAnalysisSpec) -> dict[str, Any]:
    need = spec.min_window_completeness
    baseline, n_pre = _window_mean(v, pre, need)
    post_mean, n_post = _window_mean(v, post, need)
    status = ("ok" if baseline is not None and post_mean is not None else
              "incomplete_baseline_and_post_window" if baseline is None and post_mean is None
              else "incomplete_baseline" if baseline is None else "incomplete_post_window")
    details = []
    change = relative = cumulative = None
    if status == "ok":
        change = post_mean - baseline  # type: ignore[operator]
        if baseline > spec.baseline_min_mm:  # type: ignore[operator]
            relative = 100.0 * change / baseline  # type: ignore[operator]
        else:
            details.append(f"baseline {baseline:.3g} <= baseline_min_mm "
                           f"{spec.baseline_min_mm:g}: no relative change")
    if baseline is not None:
        if n_post == len(post):
            cumulative = float((v.loc[post] - baseline).sum())
        else:
            details.append(f"post window has {len(post) - n_post} missing day(s): no "
                           "cumulative response")
    fractions: dict[str, float | None] = {"baseline_fraction": None, "post_fraction": None,
                                          "fraction_change": None}
    if den is not None:
        ratio = (v / den.where(den > 0)).dropna()
        bf, _ = _window_mean(ratio, pre, need)
        pf, _ = _window_mean(ratio, post, need)
        fractions = {"baseline_fraction": bf, "post_fraction": pf,
                     "fraction_change": None if bf is None or pf is None else pf - bf}
    rec_days, rec_status = recovery(v, e.end_date, baseline, spec, next_mgmt)
    return {"n_pre_days": n_pre, "n_post_days": n_post, "baseline_mean_mm": baseline,
            "post_mean_mm": post_mean, "change_mm": change, "relative_change_percent": relative,
            "cumulative_response_mm": cumulative, **fractions, "recovery_days": rec_days,
            "recovery_status": rec_status, "status": status,
            "status_detail": "; ".join(details) or None}


def _difference(o: Mapping[str, Any], m: Mapping[str, Any]) -> dict[str, Any]:
    def diff(k: str) -> float | None:
        a, b = o[k], m[k]
        return None if a is None or b is None or (isinstance(a, float) and np.isnan(a)) \
            else float(b) - float(a)

    both = o["recovery_status"] == m["recovery_status"] == "recovered"
    return {"n_pre_days": o["n_pre_days"], "n_post_days": o["n_post_days"],
            **{k: diff(k) for k in ("baseline_mean_mm", "post_mean_mm", "change_mm",
                                    "relative_change_percent", "cumulative_response_mm",
                                    "baseline_fraction", "post_fraction", "fraction_change")},
            "recovery_days": (m["recovery_days"] - o["recovery_days"]) if both else None,
            "recovery_status": "recovered" if both else "not_applicable",
            "status": o["status"],
            "status_detail": "modeled minus observed"
                             + ("" if both else "; recovery days need both series recovered")}
