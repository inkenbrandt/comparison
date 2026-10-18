"""Water-balance plausibility checks (guide step 15).

:func:`compute_water_balance` evaluates one declared
:class:`~etcompare.config.WaterBalanceSpec` for one station and writes two tables:

- ``water_balance_terms``: the period total of every term, with its status, source,
  spatial support, completeness and (optionally) volume;
- ``water_balance_residuals``: the residual per period, as two separate rows: one built
  only from measured and modeled terms, and one scenario residual.

Equation and signs
------------------
``residual = precipitation + irrigation + capillary_rise - et - runoff - deep_percolation
- storage_change`` (``equation: soil_zone``, ``sign_convention: inflows_positive``). Every
term is declared explicitly. A term without an input is ``unknown``: it has no value, it is
never set to zero, and every residual that needs it is null with a reason that lists every
unknown term.

Boundary and spatial support
----------------------------
Every term must cover the declared boundary (a polygon such as the tower field, or a
point/depth-only column at the station). A term whose ``spatial_support`` is anything else
(a gridMET cell, the station point under a polygon, the tower footprint, a model pixel) is
flagged ``support_mismatch`` on its row and listed in the residual's
``support_mismatch_terms``; it is never silently treated as covering the boundary. The tower
footprint changes every day and is never a boundary or a field area. Regional diversion is
refused as field irrigation (config).

Periods and completeness (D-041)
--------------------------------
Periods are those of the declared step-8 ``period_aggregation``, with the same calendar
denominators (:func:`etcompare.aggregation.period_calendar`): every calendar day of every
period overlapping the station record, leap days included, whole periods listed even where
they extend past the record (flag ``period_outside_record``). A flux term has a period total
only when **every** calendar day has a value; otherwise only ``partial_sum_mm`` over the
present days is reported, with the counts, and the total is null (``incomplete_period``). A
storage term (``storage_state``, mm at the end of each station day) has ``dS = S(end) -
S(day before start)`` only when both states exist. A residual needs a complete total of
every term.

Station ET
----------
The ET term names its reference variant (``et_raw``, ``et_corrected_measured`` or
``et_corrected_inclusive``; D-004). Gap-filled ET days are counted per period
(``n_days_et_gap_filled``) and flagged ``includes_gap_filled_et_days`` and
``shared_gridmet_drivers`` (D-005).

Day definitions
---------------
A column or table term declares its ``day_definition``. When it is ``unknown`` or differs
from the station's, its rows and the residual are flagged ``day_alignment=unverified``
(D-002); nothing is shifted.

Volumes
-------
Depths are converted to volumes (``depth_mm / 1000 x area_m2``) only for a boundary with a
declared, known area (:func:`depth_to_volume_m3` refuses otherwise), only when the
declaration asks for volumes, and only for terms whose support matches the boundary: a depth
measured over another area is not a volume of the boundary.

Interpretation
--------------
A residual is a plausibility diagnostic. It holds every unmeasured exchange and the errors of
every term, so it is never assigned to ET error and is never used to choose a model,
reference, filter or footprint. This module takes no cohort or model input, and nothing else
in etcompare imports it except :mod:`etcompare.scenarios`.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, TypeAlias

import numpy as np
import pandas as pd

from etcompare.adapters.station import parse_dates, parse_numeric
from etcompare.aggregation import period_calendar
from etcompare.config import (
    WATER_BALANCE_SIGNS,
    PeriodAggregationSpec,
    StationSpec,
    WaterBalanceBoundarySpec,
    WaterBalanceSpec,
    WaterBalanceTermSpec,
    to_plain,
)
from etcompare.errors import ContractError, DataValidationError
from etcompare.io.tables import read_any
from etcompare.provenance import file_sha256
from etcompare.references import reference_values
from etcompare.schemas import (
    WATER_BALANCE_RESIDUALS,
    WATER_BALANCE_TERM_TOTALS,
    CanonicalTable,
    TableSchema,
    check_primary_key,
)
from etcompare.validation import Issue, ValidationReport

INTERPRETATION = ("plausibility diagnostic: the residual holds every unmeasured exchange and "
                  "the errors of every term; never ET error, never a basis for choosing a "
                  "model, reference, filter or footprint")
_DAY = pd.Timedelta(days=1)

StationTables: TypeAlias = CanonicalTable | pd.DataFrame | Sequence[CanonicalTable | pd.DataFrame]


def depth_to_volume_m3(depth_mm: float, boundary: WaterBalanceBoundarySpec) -> float:
    """Volume (m3) of a depth over ``boundary``; refused without a declared, known area."""
    if not boundary.area_known:
        what = ("a point/depth boundary has no area" if boundary.kind == "point"
                else f"boundary {boundary.boundary_id} has no declared area")
        raise ContractError(f"{what}; a depth is not converted to a volume without a known "
                            "area (a tower footprint is never a field area)",
                            config_key="water_balances[].boundary.area_m2")
    return float(depth_mm) / 1000.0 * float(boundary.area_m2)  # type: ignore[arg-type]


@dataclass(frozen=True)
class TermSeries:
    """Daily values of one declared term (``values`` is None for unknown and scenario terms)."""

    term: str
    spec: WaterBalanceTermSpec
    values: pd.Series | None = None
    gap_filled: pd.Series | None = None
    source: str | None = None
    source_sha256: str | None = None


@dataclass(frozen=True)
class BalancePeriod:
    """One period of the step-8 calendar and its calendar days."""

    period_type: str
    label: str
    start: pd.Timestamp
    end: pd.Timestamp
    days: pd.DatetimeIndex


@dataclass(frozen=True)
class PreparedWaterBalance:
    """The inputs of one water balance, read and aligned to its periods."""

    spec: WaterBalanceSpec
    station: StationSpec
    series: Mapping[str, TermSeries]
    periods: tuple[BalancePeriod, ...]
    record: tuple[pd.Timestamp, pd.Timestamp]
    issues: tuple[Issue, ...] = ()


@dataclass(frozen=True)
class WaterBalanceResult:
    """Term totals and residuals of one water balance. Immutable."""

    spec: WaterBalanceSpec
    _tables: Mapping[str, CanonicalTable] = field(repr=False)
    _issues: tuple[Issue, ...] = field(repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_tables", MappingProxyType(dict(self._tables)))

    def _get(self, name: str) -> CanonicalTable:
        t = self._tables[name]
        return CanonicalTable(t.schema, t.frame.copy(), dict(t.attributes))

    @property
    def terms(self) -> CanonicalTable:
        return self._get("water_balance_terms")

    @property
    def residuals(self) -> CanonicalTable:
        return self._get("water_balance_residuals")

    @property
    def report(self) -> ValidationReport:
        return ValidationReport(f"water balance {self.spec.water_balance_id}",
                                list(self._issues))

    def tables(self) -> dict[str, CanonicalTable]:
        return {name: self._get(name) for name in self._tables}


# --------------------------------------------------------------------------- inputs

def _station_frame(station_daily: StationTables, site_id: str) -> pd.DataFrame:
    items = station_daily if isinstance(station_daily, Sequence) else [station_daily]
    frames = [t.frame if isinstance(t, CanonicalTable) else t for t in items]
    frame = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if frame.empty or "site_id" not in frame.columns:
        raise ContractError(f"no station_daily rows for {site_id}")
    frame = frame[frame["site_id"] == site_id]
    if frame.empty:
        raise ContractError(f"no station_daily rows for {site_id}")
    check_primary_key(frame, ["site_id", "date"], table=f"station_daily {site_id}")
    return frame.reset_index(drop=True)


def _numeric(values: pd.Series, where: str) -> pd.Series:
    report = ValidationReport(where)
    out = parse_numeric(values, where, report)
    if not report.ok:
        raise DataValidationError(report.summary(), report=report)
    return out


def _read_table(t: WaterBalanceTermSpec, where: str) -> tuple[pd.Series, str]:
    path = os.fspath(t.path)  # type: ignore[arg-type]
    if not os.path.exists(path):
        raise ContractError(f"{where}: table {path} not found", config_key=f"{where}.path")
    df = read_any(path)
    missing = [c for c in (t.date_column, t.value_column) if c not in df.columns]
    if missing:
        raise ContractError(f"{where}: column(s) {missing} not in {path}",
                            config_key=f"{where}.value_column")
    report = ValidationReport(where)
    dates = parse_dates(df[t.date_column], t.date_format, str(t.date_column), report)
    values = parse_numeric(df[t.value_column], str(t.value_column), report)
    if not report.ok:
        raise DataValidationError(report.summary(), report=report)
    frame = pd.DataFrame({"date": dates, "value": values})
    check_primary_key(frame, ["date"], table=f"{where} ({path})")
    return frame.set_index("date")["value"].sort_index(), file_sha256(path)


def term_series(spec: WaterBalanceSpec, station_daily: StationTables,
                report: ValidationReport | None = None) -> dict[str, TermSeries]:
    """Daily values of every declared term of ``spec`` (unknown and scenario terms: none)."""
    frame = _station_frame(station_daily, spec.site_id)
    by_date = frame.set_index("date")
    items = station_daily if isinstance(station_daily, Sequence) else [station_daily]
    station_sha = next((t.attributes.get("source_sha256") for t in items
                        if isinstance(t, CanonicalTable)
                        and t.attributes.get("station", {}).get("site_id") == spec.site_id),
                       None)
    out: dict[str, TermSeries] = {}
    for term, t in spec.terms.items():
        where = f"water_balances.{spec.water_balance_id}.terms.{term}"
        if t.source in ("none", "scenario"):
            source = None if t.source == "none" else f"scenario daily_mm={t.daily_mm:g}"
            out[term] = TermSeries(term, t, source=source)
            continue
        if t.source == "station_column":
            if t.column not in by_date.columns:
                raise ContractError(f"{where}: column {t.column!r} is not in station_daily (source "
                                    "columns carry the prefix src_, D-020)",
                                    config_key=f"{where}.column")
            values = _numeric(by_date[t.column], f"{where}.{t.column}")
            out[term] = TermSeries(term, t, values.sort_index(), source=t.column,
                                   source_sha256=station_sha)
        elif t.source == "station_et":
            ref = reference_values(frame, str(t.reference_variant)).set_index("date")
            out[term] = TermSeries(term, t, ref["observed_mm"].astype(float).sort_index(),
                                   (ref["reference_provenance"] == "corrected_filled").sort_index(),
                                   source=f"station ET {t.reference_variant}",
                                   source_sha256=station_sha)
        else:
            values, sha = _read_table(t, where)
            out[term] = TermSeries(term, t, values, source=os.fspath(t.path),  # type: ignore[arg-type]
                                   source_sha256=sha)
        vals = out[term].values
        neg = 0 if vals is None else int((vals < 0).sum())
        if neg and report is not None and t.quantity == "flux":
            report.info("negative_values", f"{term}: {neg} negative daily value(s) kept "
                        "(nothing is clipped)", field=term, n_rows=neg)
    return out


def prepare_water_balance(spec: WaterBalanceSpec, station_daily: StationTables, *,
                          period_aggregation: PeriodAggregationSpec, station: StationSpec
                          ) -> PreparedWaterBalance:
    """Read the declared terms and build the step-8 calendar of ``spec``."""
    key = f"water_balances.{spec.water_balance_id}"
    agg = period_aggregation
    if agg.aggregation_id != spec.period_aggregation:
        raise ContractError(f"{key} uses period aggregation {spec.period_aggregation!r}, not "
                            f"{agg.aggregation_id!r}", config_key=f"{key}.period_aggregation")
    if station.site_id != spec.site_id:
        raise ContractError(f"{key} is for {spec.site_id}, not {station.site_id}",
                            config_key=f"{key}.site_id")
    ptypes = spec.periods or agg.periods
    if "growing_season" in ptypes and agg.growing_season_source and \
            not agg.growing_season_windows:
        raise ContractError(f"{key}: growing seasons come from crop-height table "
                            f"{agg.growing_season_source!r}; attach the windows with "
                            "period_aggregation.with_growing_seasons(...)",
                            config_key=f"period_aggregations.{agg.aggregation_id}")
    report = ValidationReport(key)
    series = term_series(spec, station_daily, report)
    frame = _station_frame(station_daily, spec.site_id)
    record = (frame["date"].min(), frame["date"].max())
    periods = []
    for ptype in ptypes:
        cal = period_calendar(ptype, {spec.site_id: record}, agg)
        if cal.empty:
            report.info("no_periods", f"{ptype}: no period overlaps the station record",
                        field=ptype)
            continue
        for (label, start, end), g in cal.groupby(["period_label", "period_start",
                                                   "period_end"], sort=False):
            periods.append(BalancePeriod(ptype, str(label), start, end,
                                         pd.DatetimeIndex(g["date"]).sort_values()))
    periods.sort(key=lambda p: (ptypes.index(p.period_type), p.start))
    return PreparedWaterBalance(spec, station, MappingProxyType(series), tuple(periods), record,
                                tuple(report.issues))


# --------------------------------------------------------------------------- evaluation

@dataclass(frozen=True)
class TermTotal:
    """One term's total over one period (``total`` is None unless complete)."""

    total: float | None
    partial: float | None
    required: int | None
    present: int | None
    status: str
    detail: str | None
    n_gap_filled: int | None = None


def term_total(ts: TermSeries, period: BalancePeriod) -> TermTotal:
    """Period total of one term under the completeness rules of the module docstring."""
    t = ts.spec
    n_exp = len(period.days)
    if t.status == "unknown":
        return TermTotal(None, None, None, None, "unknown_term", f"no input: {t.reason}")
    if t.source == "scenario":
        total = float(t.daily_mm) * n_exp  # type: ignore[arg-type]
        return TermTotal(total, total, n_exp, n_exp, "complete", "scenario value x calendar days")
    values = ts.values
    assert values is not None
    if t.quantity == "storage_state":
        before = period.start - _DAY
        s0 = values.get(before, np.nan)
        s1 = values.get(period.end, np.nan)
        present = int(np.isfinite(s0)) + int(np.isfinite(s1))
        if present == 2:
            return TermTotal(float(s1 - s0), None, 2, 2, "complete",
                             f"S({period.end.date()}) - S({before.date()})")
        return TermTotal(None, None, 2, present, "incomplete_period",
                         f"needs the storage states of {before.date()} and {period.end.date()}")
    v = values.reindex(period.days)
    present = int(v.notna().sum())
    partial = float(v.sum()) if present else None
    n_fill = None
    if ts.gap_filled is not None:
        filled = ts.gap_filled.reindex(period.days)
        n_fill = int((filled.fillna(False).astype(bool) & v.notna()).sum())
    if present == n_exp:
        return TermTotal(partial, partial, n_exp, present, "complete", None, n_fill)
    return TermTotal(None, partial, n_exp, present, "incomplete_period",
                     f"{present} of {n_exp} days have a value; partial sum only", n_fill)


def _join(items: Sequence[str]) -> str | None:
    return ";".join(items) or None


def _frame(rows: list[dict[str, Any]], schema: TableSchema) -> pd.DataFrame:
    frame = pd.DataFrame(rows, columns=schema.names) if rows else schema.empty()
    for c in schema.columns:
        if c.kind in ("float", "int"):
            frame[c.name] = pd.to_numeric(frame[c.name])
    return frame.sort_values(list(schema.primary_key), ignore_index=True)


def _aligned(t: WaterBalanceTermSpec, station: StationSpec) -> bool:
    return t.source == "station_et" or t.day_definition == station.day_definition


def evaluate_water_balance(prepared: PreparedWaterBalance) -> WaterBalanceResult:
    """Term totals and residuals of a prepared water balance (module docstring)."""
    spec, station = prepared.spec, prepared.station
    b = spec.boundary
    report = ValidationReport(f"water_balances.{spec.water_balance_id}",
                              list(prepared.issues))
    first, last = prepared.record
    mismatch = [t for t, s in spec.terms.items()
                if s.status != "unknown" and not b.matches(s.spatial_support)]
    unaligned = [t for t, s in spec.terms.items()
                 if s.status in ("measured", "modeled") and not _aligned(s, station)]
    unknown = [t for t, s in spec.terms.items() if s.status == "unknown"]
    scenario = [t for t, s in spec.terms.items() if s.status == "scenario"]
    et = spec.terms["et"]
    variant = et.reference_variant if et.source == "station_et" else None
    if mismatch:
        report.warning("support_mismatch", f"term(s) {mismatch} do not cover boundary "
                       f"{b.boundary_id} ({b.kind}); flagged, never treated as equal")
    if unknown:
        report.warning("unknown_terms", f"term(s) {unknown} have no input: every residual that "
                       "needs them is unknown (never zero)")
    if unaligned:
        report.warning("day_alignment_unverified", f"term(s) {unaligned} have a day definition "
                       f"other than the station's {station.day_definition!r}")
    term_rows: list[dict[str, Any]] = []
    res_rows: list[dict[str, Any]] = []
    for p in prepared.periods:
        ids = {"water_balance_id": spec.water_balance_id, "site_id": spec.site_id,
               "period_type": p.period_type, "period_label": p.label,
               "period_start": p.start, "period_end": p.end, "boundary_id": b.boundary_id}
        outside = p.start < first or p.end > last
        totals: dict[str, TermTotal] = {}
        common: set[str] = set()
        for term, ts in prepared.series.items():
            t = ts.spec
            tot = term_total(ts, p)
            totals[term] = tot
            flags = set()
            if term in mismatch:
                flags.add("support_mismatch")
            if term in unaligned:
                flags.add("day_alignment=unverified")
            if t.status == "scenario":
                flags.add("scenario_assumption")
            if outside:
                flags.add("period_outside_record")
            if tot.n_gap_filled:
                flags |= {"includes_gap_filled_et_days", "shared_gridmet_drivers"}
            common |= flags - {"scenario_assumption"}
            sign = WATER_BALANCE_SIGNS[term]
            vol, vstat = None, "not_requested"
            if spec.volumes:
                if tot.total is None:
                    vstat = "no_value"
                elif term in mismatch:
                    vstat = "support_mismatch"
                else:
                    vol, vstat = depth_to_volume_m3(tot.total, b), "computed"
            term_rows.append({
                **ids, "term": term, "status": t.status, "source_kind": t.source,
                "source": ts.source, "quantity": t.quantity, "spatial_support": t.spatial_support,
                "support_matches_boundary": None if t.status == "unknown" else term not in mismatch,
                "day_definition": ("station" if t.source == "station_et" else t.day_definition),
                "sign": sign, "n_days_expected": len(p.days), "n_values_required": tot.required,
                "n_values_present": tot.present,
                "period_completeness": (None if not tot.required or tot.present is None
                                        else tot.present / tot.required),
                "partial_sum_mm": tot.partial, "total_mm": tot.total,
                "signed_total_mm": None if tot.total is None else sign * tot.total,
                "total_status": tot.status, "total_detail": tot.detail, "volume_m3": vol,
                "volume_status": vstat,
                "reference_variant": t.reference_variant if term == "et" else None,
                "n_days_et_gap_filled": tot.n_gap_filled if term == "et" else None,
                "flags": _join(sorted(flags)),
            })
        incomplete = [t for t, x in totals.items() if x.status == "incomplete_period"]
        detail = {t: totals[t].detail for t in incomplete}
        n_fill = totals["et"].n_gap_filled
        value = None
        if not unknown and not incomplete:
            value = 0.0
            for name, x in totals.items():
                assert x.total is not None
                value += WATER_BALANCE_SIGNS[name] * x.total
        base_flags = {"plausibility_diagnostic_only", "not_et_error"} | common
        if b.kind == "polygon" and str(b.vintage).lower() == "unknown":
            base_flags.add("boundary_vintage_unknown")
        reasons = []
        if unknown:
            reasons.append(f"unknown terms (no input; never zero): {', '.join(unknown)}")
        if incomplete:
            reasons.append("incomplete terms: " + "; ".join(f"{t} ({d})"
                                                              for t, d in detail.items()))
        for kind in ("measured_modeled", "scenario"):
            why = list(reasons)
            flags = set(base_flags)
            if kind == "measured_modeled" and scenario:
                why.append(f"scenario terms {', '.join(scenario)}: see the scenario residual")
            if kind == "scenario":
                flags.add("scenario_assumption")
                if not scenario:
                    why = ["no scenario term is declared"]
            ok = not why
            status = "computed" if ok else ("not_applicable" if kind == "scenario" and not scenario
                                            else "unknown")
            r_val = value if ok else None
            vol, vstat = None, "not_requested"
            if spec.volumes:
                if r_val is None:
                    vstat = "no_value"
                elif mismatch:
                    vstat = "support_mismatch"
                else:
                    vol, vstat = depth_to_volume_m3(r_val, b), "computed"
            res_rows.append({
                **ids, "residual_kind": kind, "equation": f"{spec.equation_text} "
                f"({spec.sign_convention})", "status": status, "residual_mm": r_val,
                "residual_volume_m3": vol, "volume_status": vstat,
                "reason": "; ".join(why) or None, "unknown_terms": _join(unknown),
                "incomplete_terms": _join(incomplete), "scenario_terms": _join(scenario),
                "support_mismatch_terms": _join(mismatch), "n_days_expected": len(p.days),
                "et_reference_variant": variant, "n_days_et_gap_filled": n_fill,
                "interpretation": INTERPRETATION, "flags": _join(sorted(flags)),
            })
    attrs = {"workflow": "etcompare.water_balance.compute_water_balance",
             "water_balance": to_plain(spec), "equation": spec.equation_text,
             "sign_convention": spec.sign_convention, "interpretation": INTERPRETATION,
             "station_record": [first.date().isoformat(), last.date().isoformat()],
             "term_sources": {t: {"source": s.source, "sha256": s.source_sha256}
                              for t, s in prepared.series.items()}}
    n_comp = sum(r["status"] == "computed" for r in res_rows)
    report.info("residuals", f"{n_comp} of {len(res_rows)} residual rows computed",
                n_rows=len(res_rows))
    tables = {
        "water_balance_terms": CanonicalTable.build(
            _frame(term_rows, WATER_BALANCE_TERM_TOTALS), WATER_BALANCE_TERM_TOTALS, attrs,
            source=spec.water_balance_id),
        "water_balance_residuals": CanonicalTable.build(
            _frame(res_rows, WATER_BALANCE_RESIDUALS), WATER_BALANCE_RESIDUALS, attrs,
            source=spec.water_balance_id),
    }
    return WaterBalanceResult(spec, tables, tuple(report.issues))


def compute_water_balance(spec: WaterBalanceSpec, station_daily: StationTables, *,
                          period_aggregation: PeriodAggregationSpec, station: StationSpec
                          ) -> WaterBalanceResult:
    """Term totals and residuals of ``spec`` on the station record (module docstring).

    ``station_daily`` is the station's canonical table (its ``src_`` columns supply column
    terms, its reference variants the ET term). ``period_aggregation`` is the declared
    step-8 aggregation, with growing-season windows attached when they come from a
    crop-height table. There is deliberately no cohort or model input.
    """
    return evaluate_water_balance(prepare_water_balance(
        spec, station_daily, period_aggregation=period_aggregation, station=station))
