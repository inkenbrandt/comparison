"""Adapter for pre-extracted OpenET tables (e.g. ``station_oet_<site>.parquet``).

Each declared ET column becomes rows of the long ``extractions`` table with
its declared extraction-method label. Values are used as delivered: they are
never re-weighted, clipped or rescaled (decisions.md D-006 to D-008).

Checks, all collected into one report before raising:

- every model token is registered; every site token is a site ID or alias;
- ``(site, model, date)`` is unique and dates are midnight labels;
- model and footprint filenames agree with the row's model, site and date;
- the footprint weight sum is 1 within a tolerance (warning only);
- the retained-weight fraction lies in [0, 1 + tolerance].
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import PurePath
from typing import Any

import numpy as np
import pandas as pd

from etcompare.adapters.filenames import parse_footprint_filename, parse_openet_filename
from etcompare.adapters.station import parse_numeric
from etcompare.config import ExtractedTableSpec, ProductSpec, StationSpec, to_plain
from etcompare.errors import ContractError, DataValidationError, FilenameParseError, SchemaError
from etcompare.io.tables import read_any
from etcompare.log import get_logger, log_event
from etcompare.provenance import file_sha256
from etcompare.registry import ModelRegistry, SiteRegistry
from etcompare.results import AdapterResult, ExclusionLog, format_key
from etcompare.schemas import EXTRACTIONS, CanonicalTable, check_primary_key
from etcompare.validation import ValidationReport

STAGE = "extraction_adapter"
_log = get_logger(__name__)


def _dates(s: pd.Series, where: str, report: ValidationReport) -> pd.Series:
    if isinstance(s.dtype, pd.DatetimeTZDtype):
        report.error("timezone_aware_dates", f"{where}: timezone-aware timestamps")
        return pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns]")
    if pd.api.types.is_datetime64_dtype(s.dtype):
        out = s.astype("datetime64[ns]")
    else:
        out = pd.to_datetime(s, format="%Y-%m-%d", errors="coerce").astype("datetime64[ns]")
        bad = s.notna() & out.isna()
        if bad.any():
            report.error("unparseable_dates", f"{where}: {int(bad.sum())} value(s) are not "
                         f"ISO dates, e.g. {s[bad].iloc[0]!r}", n_rows=int(bad.sum()))
    if out.isna().any():
        report.error("null_dates", f"{where}: {int(out.isna().sum())} null date(s)",
                     n_rows=int(out.isna().sum()))
    timed = out.notna() & (out != out.dt.normalize())
    if timed.any():
        report.error("dates_with_time", f"{where}: {int(timed.sum())} value(s) carry a time of "
                     "day", n_rows=int(timed.sum()))
    return out


def _resolve(values: pd.Series, resolver: Any, what: str, where: str,
             report: ValidationReport) -> tuple[pd.Series, dict[str, str]]:
    mapping, bad, applied = {}, [], {}
    for tok in values.dropna().unique():
        try:
            canon = resolver(str(tok))
        except KeyError:
            bad.append(tok)
            continue
        mapping[tok] = canon
        if canon != tok:
            applied[str(tok)] = canon
    if bad:
        report.error(f"unknown_{what}", f"{where}: unregistered {what} token(s) {sorted(bad)}",
                     n_rows=int(values.isin(bad).sum()))
    if values.isna().any():
        report.error(f"null_{what}", f"{where}: {int(values.isna().sum())} null value(s)")
    return values.map(mapping).astype("string"), applied


def _check_model_files(
    df: pd.DataFrame, col: str, *, models: ModelRegistry, sites: SiteRegistry,
    report: ValidationReport, exclusions: ExclusionLog, keys: pd.Series,
) -> pd.Series:
    """Parse model asset names; flag rows whose model/date/site disagree. Returns stems."""
    parsed: dict[str, Any] = {}
    stems = pd.Series(pd.NA, index=df.index, dtype="string")
    n_unparsed = n_mismatch = 0
    for i, name in df[col].items():
        if pd.isna(name):
            continue
        name = str(name)
        if name not in parsed:
            try:
                parsed[name] = parse_openet_filename(PurePath(name).name, models=models,
                                                     sites=sites)
            except FilenameParseError as exc:
                parsed[name] = exc
        a = parsed[name]
        if isinstance(a, FilenameParseError):
            n_unparsed += 1
            exclusions.add(STAGE, keys[i], "unparseable_model_asset", severity="error",
                           detail=str(a))
            continue
        stems[i] = a.asset_id
        problems = []
        if a.model != df.at[i, "_model"]:
            problems.append(f"model {a.model} != {df.at[i, '_model']}")
        if pd.Timestamp(a.date) != df.at[i, "_date"]:
            problems.append(f"date {a.date} != {df.at[i, '_date'].date()}")
        if df.at[i, "_site"] not in a.sites:
            problems.append(f"site {df.at[i, '_site']} not in {list(a.sites)}")
        if problems:
            n_mismatch += 1
            exclusions.add(STAGE, keys[i], "model_asset_mismatch", severity="error",
                           detail="; ".join(problems))
    if n_unparsed:
        report.error("unparseable_model_asset", f"{col}: {n_unparsed} unparseable name(s)",
                     n_rows=n_unparsed)
    if n_mismatch:
        report.error("model_asset_mismatch", f"{col}: {n_mismatch} row(s) disagree with their "
                     "filename's model, date or sites", n_rows=n_mismatch)
    else:
        report.info("model_asset_consistent", f"{col}: filename model, date and sites agree with "
                    f"every parsed row ({len(df) - n_unparsed})")
    return stems


def _check_footprint_files(
    df: pd.DataFrame, col: str, *, sites: SiteRegistry, report: ValidationReport,
    exclusions: ExclusionLog, keys: pd.Series,
) -> None:
    n_bad = 0
    for i, name in df[col].items():
        if pd.isna(name):
            continue
        try:
            a = parse_footprint_filename(PurePath(str(name)).name, sites=sites)
        except FilenameParseError as exc:
            n_bad += 1
            exclusions.add(STAGE, keys[i], "footprint_asset_mismatch", severity="error",
                           detail=str(exc))
            continue
        problems = []
        if a.period != "day":
            problems.append(f"period {a.period} is not daily")
        if pd.Timestamp(a.date) != df.at[i, "_date"]:
            problems.append(f"date {a.date} != {df.at[i, '_date'].date()}")
        if a.site_id != df.at[i, "_site"]:
            problems.append(f"site {a.site_id} != {df.at[i, '_site']}")
        if problems:
            n_bad += 1
            exclusions.add(STAGE, keys[i], "footprint_asset_mismatch", severity="error",
                           detail="; ".join(problems))
    if n_bad:
        report.error("footprint_asset_mismatch", f"{col}: {n_bad} row(s) unparseable or "
                     "inconsistent with the row's site/date/period", n_rows=n_bad)
    else:
        report.info("footprint_asset_consistent", f"{col}: footprint names agree with every row")


def adapt_extracted_table(
    source: str | os.PathLike[str] | pd.DataFrame,
    spec: ExtractedTableSpec,
    *,
    models: ModelRegistry,
    sites: SiteRegistry | None = None,
    products: Mapping[str, ProductSpec] | None = None,
    stations: Mapping[str, StationSpec] | None = None,
) -> AdapterResult:
    """Map a pre-extracted OpenET table to the long ``extractions`` table.

    ``day_alignment`` is ``declared`` only when the station and the product
    both declare a day definition other than ``unknown``; otherwise
    ``unverified``.
    """
    sites = sites or SiteRegistry()
    if isinstance(source, pd.DataFrame):
        df, where, sha = source.copy(), f"extracted table {spec.name}", None
    else:
        path = os.fspath(source)
        df, where, sha = read_any(path), path, file_sha256(path)
    df = df.reset_index(drop=True)
    report = ValidationReport(where)
    exclusions = ExclusionLog()

    optional = [spec.weight_sum_column, spec.retained_weight_column, spec.model_asset_column,
                spec.footprint_asset_column, spec.source_path_column]
    needed = [spec.site_column, spec.model_column, spec.date_column, *spec.value_columns,
              *[c for c in optional if c]]
    missing = [c for c in needed if c not in df.columns]
    if missing:
        raise SchemaError(f"{where}: mapped column(s) {missing} not found; available: "
                          f"{list(df.columns)}")

    df["_site"], site_aliases = _resolve(df[spec.site_column],
                                         lambda t: sites.resolve(t)[0], "site", where, report)
    df["_model"], model_aliases = _resolve(df[spec.model_column], models.resolve, "model",
                                           where, report)
    df["_date"] = _dates(df[spec.date_column], spec.date_column, report)
    values = {c: parse_numeric(df[c], c, report) for c in spec.value_columns}
    wsum = (parse_numeric(df[spec.weight_sum_column], spec.weight_sum_column, report)
            if spec.weight_sum_column else None)
    retained = (parse_numeric(df[spec.retained_weight_column], spec.retained_weight_column,
                              report) if spec.retained_weight_column else None)
    if not report.ok:
        raise DataValidationError(report.summary(), report=report)
    check_primary_key(df, ["_site", "_model", "_date"], table=f"{where} (site, model, date)")

    if products is not None:
        undeclared = sorted(set(df["_model"]) - set(products))
        if undeclared:
            raise ContractError(f"{where}: model(s) {undeclared} have no product declaration",
                                config_key=f"products.{undeclared[0]}")
    keys = pd.Series([format_key(site_id=s, model=m, date=d) for s, m, d in
                      zip(df["_site"], df["_model"], df["_date"], strict=True)], index=df.index)

    tol = spec.weight_sum_tolerance
    if wsum is not None:
        off = (wsum - 1).abs() > tol
        dev = float((wsum - 1).abs().max()) if wsum.notna().any() else float("nan")
        if off.any():
            report.warning("weight_sum_not_unity", f"{spec.weight_sum_column}: {int(off.sum())} "
                           f"row(s) differ from 1 by more than {tol:g}", n_rows=int(off.sum()))
        report.info("weight_sum", f"{spec.weight_sum_column}: max |sum - 1| = {dev:.3g}; a sum "
                    "of 1 does not establish original footprint capture")
    if retained is not None:
        out = retained.notna() & ((retained < 0) | (retained > 1 + tol))
        if out.any():
            report.error("retained_fraction_out_of_range", f"{spec.retained_weight_column}: "
                         f"{int(out.sum())} value(s) outside [0, 1]", n_rows=int(out.sum()))
            for i in df.index[out]:
                exclusions.add(STAGE, keys[i], "retained_fraction_out_of_range",
                               severity="error", detail=f"{retained[i]!r}")

    model_stems = None
    if spec.model_asset_column:
        model_stems = _check_model_files(df, spec.model_asset_column, models=models,
                                         sites=sites, report=report, exclusions=exclusions,
                                         keys=keys)
    if spec.footprint_asset_column:
        _check_footprint_files(df, spec.footprint_asset_column, sites=sites, report=report,
                               exclusions=exclusions, keys=keys)
    if not report.ok:
        raise DataValidationError(report.summary(), report=report)

    for col, v in values.items():
        if v.isna().any():
            report.info("null_et", f"{col}: {int(v.isna().sum())} null value(s) in present rows "
                        "(distinct from absent rows)", n_rows=int(v.isna().sum()))
        if (v < 0).any():
            report.info("negative_et", f"{col}: {int((v < 0).sum())} negative value(s) retained",
                        n_rows=int((v < 0).sum()))

    def alignment(site: str, model: str) -> str:
        st = stations.get(site) if stations else None
        pr = products.get(model) if products else None
        return "declared" if st is not None and pr is not None and \
            pr.day_definition != "unknown" else "unverified"

    align = pd.Series([alignment(s, m) for s, m in zip(df["_site"], df["_model"], strict=True)],
                      index=df.index)
    if (align == "unverified").any():
        report.info("day_alignment_unverified", f"{int((align == 'unverified').sum())} source "
                    "row(s) joined by date label without a declared product day definition",
                    n_rows=int((align == "unverified").sum()))

    parts = []
    for col, method in spec.value_columns.items():
        parts.append(pd.DataFrame({
            "site_id": df["_site"], "date": df["_date"], "period": "day",
            "model": df["_model"], "extraction_method": method,
            "footprint_set_id": spec.footprint_set_id, "footprint_method": spec.footprint_method,
            "et_mm": values[col],
            "fp_weight_sum": wsum if wsum is not None else np.nan,
            "fp_weight_retained_frac": retained if retained is not None else np.nan,
            "fp_weight_retained_denominator": spec.retained_weight_denominator,
            "support_id": None,
            "day_alignment": align,
            "model_asset_id": model_stems if model_stems is not None else None,
            "footprint_asset_id": (df[spec.footprint_asset_column].astype("string")
                                   if spec.footprint_asset_column else None),
            "source_path": (df[spec.source_path_column].astype("string")
                            if spec.source_path_column else None),
        }))
    long = pd.concat(parts, ignore_index=True).sort_values(
        ["site_id", "model", "extraction_method", "date"], kind="stable")

    attributes: dict[str, Any] = {
        "adapter": "etcompare.adapters.extracted.adapt_extracted_table",
        "source": where, "source_sha256": sha, "n_source_rows": len(df),
        "mapping": to_plain(spec), "declared_units": dict(spec.units),
        "site_aliases_applied": site_aliases, "model_aliases_applied": model_aliases,
    }
    table = CanonicalTable.build(long.reset_index(drop=True), EXTRACTIONS, attributes,
                                 source=where)
    log_event(_log, "extracted_table_adapted", source=where, n_source_rows=len(df),
              n_rows=len(table), models=sorted(set(df["_model"])))
    return AdapterResult(table, report, exclusions)
