"""Adapter for daily station tables such as the flux-data-qaqc daily export.

Maps declared columns to the ``station_daily`` schema and derives reference
provenance (input_contract.md section 4.3) from the combination of non-null
values and the gap flag::

    corrected_from_raw  raw present,  corrected present, gap False
    corrected_filled    raw missing,  corrected present, gap True
    reference_missing   raw missing,  corrected missing
    inconsistent_flags  anything else (reported, never coerced)

Nothing is clipped, filled, shifted or converted. Every source column is kept
with the prefix ``src_``.
"""

from __future__ import annotations

import os
from typing import Any

import numpy as np
import pandas as pd

from etcompare.config import StationSpec, StationTableSpec, to_plain
from etcompare.errors import ContractError, DataValidationError, SchemaError
from etcompare.io.tables import read_any
from etcompare.log import get_logger, log_event
from etcompare.provenance import file_sha256
from etcompare.results import AdapterResult, ExclusionLog, format_key
from etcompare.schemas import REFERENCE_PROVENANCE, STATION_DAILY, CanonicalTable
from etcompare.validation import Severity, ValidationReport

SOURCE_PREFIX = "src_"
STAGE = "station_adapter"
_TRUE = {"true", "1"}
_FALSE = {"false", "0"}
_log = get_logger(__name__)


def parse_dates(s: pd.Series, fmt: str, where: str, report: ValidationReport) -> pd.Series:
    """Parse station-day labels strictly; record unparseable, null or timed values."""
    if isinstance(s.dtype, pd.DatetimeTZDtype):
        report.error("timezone_aware_dates", f"{where}: timezone-aware timestamps; declare the "
                     "station day and convert before adapting")
        return pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns]")
    if pd.api.types.is_datetime64_dtype(s.dtype):
        parsed = s.astype("datetime64[ns]")
    else:
        parsed = pd.to_datetime(s, format=fmt, errors="coerce").astype("datetime64[ns]")
        bad = s.notna() & parsed.isna()
        if bad.any():
            report.error("unparseable_dates", f"{where}: {int(bad.sum())} value(s) do not match "
                         f"{fmt!r}, e.g. {s[bad].iloc[0]!r}", field=where, n_rows=int(bad.sum()))
    if s.isna().any():
        report.error("null_dates", f"{where}: {int(s.isna().sum())} null date(s)",
                     n_rows=int(s.isna().sum()))
    timed = parsed.notna() & (parsed != parsed.dt.normalize())
    if timed.any():
        report.error("dates_with_time", f"{where}: {int(timed.sum())} value(s) carry a time of "
                     "day; station-day labels are never truncated", n_rows=int(timed.sum()))
    return parsed


def parse_numeric(s: pd.Series, where: str, report: ValidationReport) -> pd.Series:
    if pd.api.types.is_bool_dtype(s.dtype):
        report.error("non_numeric", f"{where}: boolean values where numbers are expected")
        return pd.Series(np.nan, index=s.index)
    out = pd.to_numeric(s, errors="coerce").astype("float64")
    bad = s.notna() & out.isna()
    if bad.any():
        report.error("non_numeric", f"{where}: {int(bad.sum())} non-numeric value(s), e.g. "
                     f"{s[bad].iloc[0]!r}", n_rows=int(bad.sum()))
    return out


def parse_flag(s: pd.Series, where: str, report: ValidationReport) -> pd.Series:
    """Strict boolean parsing: bools or the strings true/false/1/0 (any case)."""
    if pd.api.types.is_bool_dtype(s.dtype):
        return s.astype("boolean")

    def conv(v: Any) -> Any:
        if v is None or v is pd.NA or (isinstance(v, float) and np.isnan(v)):
            return pd.NA
        if isinstance(v, bool | np.bool_):
            return bool(v)
        if isinstance(v, int | np.integer) and v in (0, 1):
            return bool(v)
        t = str(v).strip().lower()
        if t in _TRUE:
            return True
        if t in _FALSE:
            return False
        return "__bad__"

    conv_vals = s.map(conv)
    bad = conv_vals.map(lambda v: isinstance(v, str))
    if bad.any():
        report.error("non_boolean_flag", f"{where}: {int(bad.sum())} value(s) are not booleans, "
                     f"e.g. {s[bad].iloc[0]!r}", n_rows=int(bad.sum()))
        conv_vals = conv_vals.mask(bad, pd.NA)
    return conv_vals.astype("boolean")


def derive_provenance(raw: pd.Series, corrected: pd.Series, gap: pd.Series) -> pd.Series:
    """Reference provenance from non-null values and the gap flag (contract 4.3)."""
    has_raw = raw.notna().to_numpy()
    has_corr = corrected.notna().to_numpy()
    gap_true = gap.eq(True).fillna(False).to_numpy(dtype=bool)
    gap_false = gap.eq(False).fillna(False).to_numpy(dtype=bool)
    out = np.select(
        [has_raw & has_corr & gap_false, ~has_raw & has_corr & gap_true, ~has_raw & ~has_corr],
        ["corrected_from_raw", "corrected_filled", "reference_missing"],
        default="inconsistent_flags",
    )
    return pd.Series(out, index=raw.index, dtype="string")


def adapt_station_daily(
    source: str | os.PathLike[str] | pd.DataFrame,
    spec: StationTableSpec,
    station: StationSpec,
    *,
    closure_rtol: float = 1e-6,
) -> AdapterResult:
    """Map a daily station table to the canonical ``station_daily`` table.

    Raises :class:`~etcompare.errors.DataValidationError` (with a full report)
    when any error-level issue is found, and
    :class:`~etcompare.errors.DuplicateKeyError` for duplicated dates.
    """
    if station.site_id != spec.site_id:
        raise ContractError(f"station table declares site {spec.site_id} but station spec is "
                            f"{station.site_id}", config_key=f"stations.{spec.site_id}")
    if isinstance(source, pd.DataFrame):
        df, where, sha = source.copy(), f"station table for {spec.site_id}", None
    else:
        path = os.fspath(source)
        df, where, sha = read_any(path), path, file_sha256(path)
    df = df.reset_index(drop=True)
    report = ValidationReport(where)
    exclusions = ExclusionLog()

    raw_col, corr_col = spec.reference_columns["et_raw"], spec.reference_columns["et_corrected"]
    needed = [spec.date_column, raw_col, corr_col, spec.gap_flag_column]
    if spec.closure_factor_column:
        needed.append(spec.closure_factor_column)
    missing = [c for c in needed if c not in df.columns]
    if missing:
        raise SchemaError(f"{where}: mapped column(s) {missing} not found; available: "
                          f"{list(df.columns)}")

    dates = parse_dates(df[spec.date_column], spec.date_format, spec.date_column, report)
    raw = parse_numeric(df[raw_col], raw_col, report)
    corr = parse_numeric(df[corr_col], corr_col, report)
    gap = parse_flag(df[spec.gap_flag_column], spec.gap_flag_column, report)
    cf = (parse_numeric(df[spec.closure_factor_column], spec.closure_factor_column, report)
          if spec.closure_factor_column else None)
    if not report.ok:
        raise DataValidationError(report.summary(), report=report)

    prov = derive_provenance(raw, corr, gap)
    counts = {k: int((prov == k).sum()) for k in REFERENCE_PROVENANCE}
    report.info("reference_provenance", f"provenance counts {counts}")
    n_inc = counts["inconsistent_flags"]
    if n_inc:
        report.warning("inconsistent_flags", f"{n_inc} row(s) have value/flag combinations "
                       "outside the contract; retained as inconsistent_flags", n_rows=n_inc)
        for d in dates[prov == "inconsistent_flags"]:
            exclusions.add(STAGE, format_key(site_id=spec.site_id, date=d), "inconsistent_flags",
                           detail="not usable as a reference until resolved")
    gap_no_fill = int((gap.eq(True).fillna(False) & corr.isna()).sum())
    if gap_no_fill:
        report.warning("gap_flag_without_fill", f"{gap_no_fill} row(s) flagged as gap-filled "
                       "with no corrected value", n_rows=gap_no_fill)

    for col, s in ((raw_col, raw), (corr_col, corr)):
        neg = int((s < 0).sum())
        if neg:
            report.info("negative_et", f"{col}: {neg} negative value(s) retained (no clipping)",
                        n_rows=neg)

    if cf is not None:
        m = (prov == "corrected_from_raw") & cf.notna()
        ok = np.isclose(corr[m], raw[m] * cf[m], rtol=closure_rtol, atol=0)
        n_ok, n_m = int(ok.sum()), int(m.sum())
        sev: Severity = "info" if n_ok == n_m else "warning"
        report.add(sev, "closure_relation", f"{corr_col} = {raw_col} * "
                   f"{spec.closure_factor_column} on {n_ok}/{n_m} corrected_from_raw rows "
                   "(diagnostic of upstream semantics)", n_rows=n_m - n_ok)

    valid = dates.dropna().sort_values()
    if len(valid):
        full = pd.date_range(valid.iloc[0], valid.iloc[-1], freq="D")
        n_gaps = len(full.difference(pd.DatetimeIndex(valid)))
        report.info("calendar", f"{valid.iloc[0].date()}..{valid.iloc[-1].date()}: "
                    f"{valid.nunique()} dates, {n_gaps} calendar date(s) absent (not filled)",
                    n_rows=n_gaps)

    canonical = pd.DataFrame({
        "site_id": spec.site_id,
        "date": dates,
        "et_raw_mm": raw,
        "et_corrected_mm": corr,
        "gap_filled": gap,
        "closure_factor": cf if cf is not None else np.nan,
        "raw_available": raw.notna(),
        "reference_provenance": prov,
    })
    src = df.add_prefix(SOURCE_PREFIX)
    clash = set(src.columns) & set(canonical.columns)
    if clash:
        raise SchemaError(f"{where}: prefixed source columns collide with canonical names "
                          f"{sorted(clash)}")
    frame = pd.concat([canonical, src], axis=1)

    attributes: dict[str, Any] = {
        "adapter": "etcompare.adapters.station.adapt_station_daily",
        "source": where, "source_sha256": sha,
        "station": to_plain(station), "mapping": to_plain(spec),
        "declared_units": dict(spec.units), "reference_provenance_counts": counts,
        "source_column_prefix": SOURCE_PREFIX,
    }
    table = CanonicalTable.build(frame, STATION_DAILY, attributes, source=where)
    log_event(_log, "station_table_adapted", site_id=spec.site_id, source=where,
              n_rows=len(table), **counts)
    return AdapterResult(table, report, exclusions)
