"""Adapter for daily crop-height tables that carry the crop model's growing season.

The growing season of each year is the declared ``season_start``..``season_end``
(inclusive) of the crop model. The adapter maps the table to the ``crop_height``
schema and checks that the season is consistent:

- one season (start, end) per site and year, with start <= end;
- the declared ``active_column`` (e.g. a growth-rate column) is non-null exactly
  inside the season, when given (error otherwise: the season would be ambiguous);
- height outside the season is reported (information only).

:func:`growing_season_windows` turns the table into
:class:`~etcompare.config.GrowingSeasonWindow` objects labelled ``GS<year>`` for
:meth:`~etcompare.config.PeriodAggregationSpec.with_growing_seasons`. Nothing is
inferred from height thresholds.
"""

from __future__ import annotations

import os
from typing import Any

import pandas as pd

from etcompare.adapters.station import SOURCE_PREFIX, parse_dates, parse_numeric
from etcompare.config import HEIGHT_UNITS, CropHeightTableSpec, GrowingSeasonWindow, to_plain
from etcompare.errors import DataValidationError, SchemaError
from etcompare.io.tables import read_any
from etcompare.provenance import file_sha256
from etcompare.results import AdapterResult, ExclusionLog
from etcompare.schemas import CROP_HEIGHT, CanonicalTable
from etcompare.validation import ValidationReport


def adapt_crop_height(source: str | os.PathLike[str] | pd.DataFrame,
                      spec: CropHeightTableSpec) -> AdapterResult:
    """Map a crop-height table to the canonical ``crop_height`` table.

    Raises :class:`~etcompare.errors.DataValidationError` when the dates, the
    season columns or the active column are inconsistent.
    """
    if isinstance(source, pd.DataFrame):
        df, where, sha = source.copy(), f"crop height table {spec.name}", None
    else:
        df, where, sha = read_any(os.fspath(source)), os.fspath(source), file_sha256(source)
    df = df.reset_index(drop=True)
    report = ValidationReport(where)
    cols = [spec.date_column, spec.height_column, spec.season_start_column,
            spec.season_end_column, *[c for c in (spec.site_column, spec.active_column) if c]]
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise SchemaError(f"{where}: mapped column(s) {missing} not found; available: "
                          f"{list(df.columns)}")

    dates = parse_dates(df[spec.date_column], spec.date_format, spec.date_column, report)
    start = parse_dates(df[spec.season_start_column], spec.date_format,
                        spec.season_start_column, report)
    end = parse_dates(df[spec.season_end_column], spec.date_format, spec.season_end_column,
                      report)
    height = parse_numeric(df[spec.height_column], spec.height_column, report)
    if not report.ok:
        raise DataValidationError(report.summary(), report=report)
    site = (df[spec.site_column].astype("string") if spec.site_column
            else pd.Series(spec.site_id, index=df.index, dtype="string"))

    frame = pd.DataFrame({"site_id": site, "date": dates,
                          "height_cm": height * HEIGHT_UNITS[spec.height_units],
                          "season_start": start, "season_end": end})
    frame["in_season"] = (frame["date"] >= start) & (frame["date"] <= end)

    bad = frame["season_start"] > frame["season_end"]
    if bad.any():
        report.error("season_reversed", f"{int(bad.sum())} row(s) with season start after end",
                     n_rows=int(bad.sum()))
    year = frame["date"].dt.year
    n_seasons = frame.groupby([frame["site_id"], year])[["season_start", "season_end"]].nunique()
    multi = n_seasons[(n_seasons > 1).any(axis=1)]
    if len(multi):
        report.error("several_seasons_per_year", "season columns change within a year for "
                     f"{[f'{s}/{y}' for s, y in multi.index]}")
    off_year = start.dt.year != year
    if off_year.any():
        report.error("season_in_other_year", f"{int(off_year.sum())} row(s) whose season starts "
                     "in another calendar year; cross-year crop seasons are not supported",
                     n_rows=int(off_year.sum()))
    if spec.active_column:
        active = df[spec.active_column].notna().to_numpy()
        mismatch = int((active != frame["in_season"].to_numpy()).sum())
        if mismatch:
            report.error("active_outside_season",
                         f"{spec.active_column} is non-null on {mismatch} day(s) that disagree "
                         "with the declared season", n_rows=mismatch)
        else:
            report.info("active_matches_season",
                        f"{spec.active_column} is non-null exactly inside the declared season")
    if not report.ok:
        raise DataValidationError(report.summary(), report=report)

    out_of_season = frame.loc[~frame["in_season"], "height_cm"]
    report.info("height_outside_season", f"height outside the season: "
                f"{out_of_season.min() if len(out_of_season) else 'n/a'}.."
                f"{out_of_season.max() if len(out_of_season) else 'n/a'} cm "
                f"on {len(out_of_season)} day(s)")
    for (s, y), g in frame.groupby([frame["site_id"], year]):
        st, en = g["season_start"].iloc[0], g["season_end"].iloc[0]
        covered = g["date"].min() <= st and g["date"].max() >= en
        report.info("season", f"{s} {y}: {st.date()}..{en.date()} "
                    f"({(en - st).days + 1} days){'' if covered else '; table does not cover it'}")

    src = df.add_prefix(SOURCE_PREFIX)
    frame = pd.concat([frame, src], axis=1)
    attributes: dict[str, Any] = {
        "adapter": "etcompare.adapters.crop_height.adapt_crop_height",
        "source": where, "source_sha256": sha, "mapping": to_plain(spec),
    }
    table = CanonicalTable.build(frame, CROP_HEIGHT, attributes, source=where)
    return AdapterResult(table, report, ExclusionLog())


def growing_season_windows(crop: CanonicalTable | AdapterResult
                           ) -> tuple[GrowingSeasonWindow, ...]:
    """One window per site and year, labelled ``GS<year>``, from the declared season."""
    t = crop.table if isinstance(crop, AdapterResult) else crop
    f = t.frame
    seasons = f[["site_id", "season_start", "season_end"]].drop_duplicates()
    return tuple(GrowingSeasonWindow(str(r.site_id), f"GS{r.season_start.year}",
                                     r.season_start.date().isoformat(),
                                     r.season_end.date().isoformat())
                 for r in seasons.sort_values(["site_id", "season_start"]).itertuples())
