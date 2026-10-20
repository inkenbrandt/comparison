"""Temporal aggregation and calendar completeness (guide step 8).

:func:`aggregate_cohort` sums a :class:`~etcompare.cohorts.Cohort`'s daily
depths into periods declared by a :class:`~etcompare.config.PeriodAggregationSpec`
and returns a :class:`PeriodTotals` (``period_totals`` schema).

Rules
-----
1. Daily ET is depth per station day (``mm d-1``), so a period total is the
   **sum** of daily depths (``mm``), never a mean. Daily footprint-extracted
   values are summed; extracting a monthly raster with a monthly footprint is a
   different method and is not produced here (cohorts use daily rows only).
2. Periods follow **station-day labels**. Dates are never shifted; a product
   whose day definition is unknown keeps its ``day_alignment=unverified`` flag
   in the period row, because its values may be offset by part of a day at
   period boundaries.
3. The calendar, not the data, gives ``n_days_expected``: every calendar day of
   the period, leap days included (February 2024 has 29, WY2024 has 366). A day
   missing from both sides is still an expected day, so missing days can never
   leave the denominator. Periods that extend beyond the comparison's inputs are
   flagged ``period_outside_inputs``.
4. Three products, each a separate row:

   - ``partial_sum``: the cohort's matched days in the period, labelled with
     ``n_days_matched``. Never scaled to the calendar length.
   - ``complete_total``: only when every expected day is a cohort day (each
     with an accepted reference value of the variant: measured, or gap-filled
     for ``et_corrected_inclusive``) and the product's temporal support is
     ``interpolated_daily``. Otherwise null, with ``validity_reason``.
   - ``estimated_total``: only when the spec declares an ``estimation_method``,
     and only when completeness reaches the declared minimum. It is an estimate,
     not an observed total.

     - ``mean_daily_scaling`` multiplies both partial sums by
       ``n_days_expected / n_days_matched``.
     - ``linear_interpolation`` and ``reference_et_fraction`` impute daily values
       (see *Imputation*) and sum the cohort values plus the imputed values. The
       total is withheld (``imputation_incomplete``) unless every expected day of
       the period has a value.

5. Overpass-only (or undeclared ``unknown``) temporal support cannot support a
   full-period total: complete and estimated totals are withheld with
   ``validity_reason=temporal_support_<support>``; partial sums remain.
6. Counts are explicit: expected, matched, matched gap-filled-reference days,
   model-available days (before cohort rules) and measured / gap-filled
   reference days of the variant. Completeness is matched / expected.

Imputation
----------
Imputed values never overwrite data: :attr:`PeriodTotals.daily_estimates`
(``daily_estimates`` schema) keeps ``observed_mm`` / ``modeled_mm`` as the
cohort values and puts the imputed values only in ``observed_estimated_mm`` /
``modeled_estimated_mm``, with ``value_source`` = ``cohort`` / ``imputed`` /
``not_imputed``.

- **Paired:** the anchors are the cohort's days, and every non-cohort day is
  imputed on both sides (station and model) with the same method, even where
  one side has a real value. Real values outside the cohort are not used. Every
  model of a frozen cohort therefore has identical real and imputed days, so no
  model gains from missing difficult days.
- **Bracketed, bounded gaps:** a run of consecutive missing days is imputed only
  if it has an anchor on both sides (anchors outside the period count) and is at
  most ``estimation_max_gap_days`` long. Nothing is extrapolated.
- ``linear_interpolation``: daily ET is interpolated linearly in time.
- ``reference_et_fraction``: the ratio ET / reference ET is interpolated linearly
  and multiplied by the day's reference ET (``reference_et_mm`` of the station
  table). Anchors need positive reference ET; a missing day without reference ET
  stays ``not_imputed``. This is the station's own gap-filling relation
  (``ET_fill = ETr * ETrF``; D-005), so imputed observed and modeled values share
  the reference-ET driver and are not independent evidence.

Water years are labelled by the calendar year in which they end. Growing
seasons are declared per site/year, read from a crop-height table
(:mod:`etcompare.adapters.crop_height`), or given by a month-day rule; a rule
whose end precedes its start crosses the year boundary and is labelled
``GS<start year>-<end year>``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, TypeAlias

import numpy as np
import pandas as pd

from etcompare.cohorts import Cohort
from etcompare.config import PeriodAggregationSpec, ProductSpec, to_plain
from etcompare.errors import ContractError
from etcompare.schemas import (
    DAILY_ESTIMATES,
    PERIOD_TOTALS,
    CanonicalTable,
    check_primary_key,
)
from etcompare.validation import Issue, ValidationReport

_GROUP = ["comparison_id", "cohort_id", "site_id", "model", "reference_variant",
          "extraction_method", "footprint_set_id"]
_SIDE = ["extraction_method", "footprint_set_id"]
#: A 0-d object array holding None: the null branch of a string-or-null ``np.where``.
_NONE = np.asarray(None, dtype=object)
DAILY_SUPPORT = "interpolated_daily"


@dataclass(frozen=True)
class PeriodTotals:
    """Period totals of one cohort under one aggregation spec. Immutable."""

    spec: PeriodAggregationSpec
    comparison_id: str
    _table: CanonicalTable = field(repr=False)
    _issues: tuple[Issue, ...] = field(repr=False)
    _daily: CanonicalTable | None = field(default=None, repr=False)
    attributes: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "attributes", MappingProxyType(dict(self.attributes)))

    @property
    def table(self) -> CanonicalTable:
        t = self._table
        return CanonicalTable(t.schema, t.frame.copy(), dict(t.attributes))

    @property
    def daily_estimates(self) -> CanonicalTable | None:
        """Daily values with imputations in separate columns (imputation methods only)."""
        t = self._daily
        return None if t is None else CanonicalTable(t.schema, t.frame.copy(),
                                                     dict(t.attributes))

    @property
    def report(self) -> ValidationReport:
        return ValidationReport(f"period totals {self.comparison_id}/{self.spec.aggregation_id}",
                                list(self._issues))

    def summary(self) -> pd.DataFrame:
        """Periods and days per cohort, model, side, variant and period type."""
        f = self._table.frame
        keys = ["cohort_id", "model", "reference_variant", *_SIDE, "period_type"]
        part = f[f["product"] == "partial_sum"]
        g = part.groupby(keys)
        out = pd.DataFrame({
            "n_periods": g.size(),
            "n_periods_with_matched_days": g["n_days_matched"].apply(lambda s: int((s > 0).sum())),
            "n_days_expected": g["n_days_expected"].sum().astype(int),
            "n_days_matched": g["n_days_matched"].sum().astype(int),
        })
        comp = f[f["product"] == "complete_total"]
        n_complete = comp[comp["observed_total_mm"].notna()].groupby(keys).size()
        out["n_periods_complete"] = n_complete.reindex(out.index, fill_value=0).astype(int)
        return out.reset_index()


# --------------------------------------------------------------------------- calendar

def _md(text: str) -> tuple[int, int]:
    return int(text[:2]), int(text[3:5])


def _periods(period_type: str, site_id: str, first: pd.Timestamp, last: pd.Timestamp,
             spec: PeriodAggregationSpec) -> list[tuple[str, pd.Timestamp, pd.Timestamp]]:
    """(label, start, end) of every period of ``period_type`` overlapping [first, last]."""
    ts = pd.Timestamp
    if period_type == "day":
        return [(d.date().isoformat(), d, d) for d in pd.date_range(first, last)]
    if period_type == "month":
        return [(str(p), p.start_time.normalize(), p.end_time.normalize())
                for p in pd.period_range(first, last, freq="M")]
    if period_type == "year":
        return [(str(y), ts(y, 1, 1), ts(y, 12, 31)) for y in range(first.year, last.year + 1)]
    if period_type == "water_year":
        m = spec.water_year_start_month

        def wy(d: pd.Timestamp) -> int:
            return d.year + 1 if m > 1 and d.month >= m else d.year

        out = []
        for y in range(wy(first), wy(last) + 1):
            start = ts(y - 1 if m > 1 else y, m, 1)
            out.append((f"WY{y}", start, ts(start.year + 1, m, 1) - pd.Timedelta(days=1)))
        return out
    if period_type == "growing_season":
        rule = spec.growing_season_rule
        if rule is None:
            return [(w.label, ts(w.start), ts(w.end)) for w in spec.growing_season_windows
                    if w.site_id == site_id and ts(w.end) >= first and ts(w.start) <= last]
        if rule.sites is not None and site_id not in rule.sites:
            return []
        (sm, sd), (em, ed) = _md(rule.start), _md(rule.end)
        cross = rule.crosses_year
        out = []
        for y in range(first.year - 1, last.year + 1):
            start, end = ts(y, sm, sd), ts(y + int(cross), em, ed)
            if end >= first and start <= last:
                out.append((f"GS{y}-{y + 1}" if cross else f"GS{y}", start, end))
        return out
    raise ValueError(f"unknown period type {period_type!r}")


def period_calendar(period_type: str, site_ranges: Mapping[str, tuple[pd.Timestamp, pd.Timestamp]],
                    spec: PeriodAggregationSpec) -> pd.DataFrame:
    """One row per site, period and calendar day of each period overlapping the site's range.

    Columns ``site_id``, ``period_label``, ``period_start``, ``period_end``, ``date``.
    Whole periods are listed, including days outside the range: they are expected days.
    """
    parts = []
    for site, (first, last) in site_ranges.items():
        for label, start, end in _periods(period_type, site, first, last, spec):
            days = pd.date_range(start, end)
            parts.append(pd.DataFrame({"site_id": site, "period_label": label,
                                       "period_start": start, "period_end": end, "date": days}))
    cols = ["site_id", "period_label", "period_start", "period_end", "date"]
    if not parts:
        return pd.DataFrame({c: pd.Series(dtype="datetime64[ns]" if c not in
                                          ("site_id", "period_label") else object)
                             for c in cols})
    return pd.concat(parts, ignore_index=True)[cols]


def _union_flags(values: pd.Series) -> str | None:
    tokens = sorted({t for v in values.dropna() for t in str(v).split(";") if t})
    return ";".join(tokens) or None


# --------------------------------------------------------------------------- aggregation

RefET: TypeAlias = CanonicalTable | pd.DataFrame | Sequence[CanonicalTable | pd.DataFrame]


def _reference_et(reference_et: RefET | None) -> pd.DataFrame | None:
    if reference_et is None:
        return None
    parts = reference_et if isinstance(reference_et, Sequence) else [reference_et]
    frames = [p.frame if isinstance(p, CanonicalTable) else p for p in parts]
    cols = ["site_id", "date", "reference_et_mm"]
    for f in frames:
        if not set(cols) <= set(f.columns):
            raise ContractError("reference ET table lacks site_id/date/reference_et_mm; declare "
                                "station_tables[].reference_et_column",
                                config_key="station_tables[].reference_et_column")
    out = pd.concat([f[cols] for f in frames], ignore_index=True)
    check_primary_key(out, ["site_id", "date"], table="reference ET")
    return out


def aggregate_cohort(cohort: Cohort, spec: PeriodAggregationSpec, *,
                     products: Mapping[str, ProductSpec],
                     reference_et: RefET | None = None) -> PeriodTotals:
    """Sum ``cohort``'s daily pairs into the periods declared by ``spec``.

    ``products`` supplies each model's declared temporal support; a model without
    a declaration raises :class:`~etcompare.errors.ContractError`.
    ``reference_et`` (``station_daily`` tables with ``reference_et_mm``) is needed
    by ``reference_et_fraction`` imputation. When the spec reads its growing
    season from a crop-height table, attach the windows first with
    :meth:`~etcompare.config.PeriodAggregationSpec.with_growing_seasons`.
    """
    cs = cohort.spec
    cid = cs.comparison_id
    aid = spec.aggregation_id
    report = ValidationReport(f"period totals {cid}/{aid}")
    if ("growing_season" in spec.periods and spec.growing_season_source
            and not spec.growing_season_windows):
        raise ContractError(
            f"period totals {cid}/{aid}: growing seasons come from crop-height table "
            f"{spec.growing_season_source!r}; read it with adapters.crop_height and attach the "
            "windows with spec.with_growing_seasons(...)",
            config_key=f"period_aggregations.{aid}.growing_season_source")
    ref_et = _reference_et(reference_et)
    if spec.estimation_method == "reference_et_fraction" and ref_et is None:
        raise ContractError(f"period totals {cid}/{aid}: reference_et_fraction needs reference "
                            "ET; pass reference_et= (station tables with reference_et_mm)",
                            config_key="station_tables[].reference_et_column")
    missing = [m for m in cs.models if m not in products]
    if missing:
        raise ContractError(f"period totals {cid}: temporal support of model(s) {missing} not "
                            "declared; complete totals need it", config_key="products")
    support = {m: products[m].temporal_support for m in cs.models}
    if len(set(support.values())) > 1:
        report.warning("mixed_temporal_support",
                       f"models differ in temporal support {support}; compare period totals "
                       "only between products with the same support")
    for m, s in support.items():
        if s != DAILY_SUPPORT:
            report.warning("no_full_period_support",
                           f"{m}: temporal support {s!r}; complete and estimated totals are "
                           "withheld (partial sums only)", field=m)

    membership = cohort.membership.frame
    paired = cohort.paired.frame
    if membership.empty:
        report.warning("no_candidates", "the cohort has no candidate days; nothing to aggregate")
    ranges = {s: (g["date"].min(), g["date"].max()) for s, g in membership.groupby("site_id")}
    groups = membership[["cohort_id", "site_id", "model"]].drop_duplicates()
    sides = pd.DataFrame([(s.extraction_method, s.footprint_set_id) for s in cs.sides],
                         columns=_SIDE)
    variants = pd.DataFrame({"reference_variant": list(cs.reference_variants)})
    groups = (groups.merge(sides, how="cross").merge(variants, how="cross")
              .assign(comparison_id=cid))
    mavail = cohort.model_availability()
    ravail = cohort.reference_availability()
    ravail = ravail[ravail["reference_provenance"].notna()].copy()
    ravail["_filled"] = ravail["reference_provenance"] == "corrected_filled"

    calendars = {}
    for ptype in spec.periods:
        cal = period_calendar(ptype, ranges, spec)
        if cal.empty:
            report.info("no_periods", f"{ptype}: no period overlaps the cohort's days",
                        field=ptype)
        else:
            calendars[ptype] = cal
    daily = None
    if spec.imputes and calendars:
        targets = pd.concat([c[["site_id", "date"]] for c in calendars.values()]
                            ).drop_duplicates()
        daily = _impute(paired, groups, targets, spec, ref_et)
        n_imp = int((daily["value_source"] == "imputed").sum())
        n_not = int((daily["value_source"] == "not_imputed").sum())
        report.info("imputation", f"{spec.estimation_method}: {n_imp} imputed and {n_not} "
                    f"not-imputed group-days (max gap {spec.estimation_max_gap_days} days)",
                    n_rows=n_imp)

    frames = []
    for ptype, cal in calendars.items():
        day_map = cal[["site_id", "date", "period_label"]]
        check_primary_key(day_map, ["site_id", "date"], table=f"{ptype} calendar")
        periods = (cal.groupby(["site_id", "period_label", "period_start", "period_end"])
                   .size().rename("n_days_expected").reset_index())
        # The skeleton is deliberately every cohort/model/side/variant x every period of
        # its site, so periods without matched days still report their expected days.
        rows = groups.merge(periods, on="site_id", how="inner", validate="many_to_many")

        p = paired.merge(day_map, on=["site_id", "date"], how="inner", validate="many_to_one")
        p["_filled"] = p["reference_provenance"] == "corrected_filled"
        g = p.groupby([*_GROUP, "period_label"])
        matched = pd.DataFrame({
            "_obs": g["observed_mm"].sum(), "_mod": g["modeled_mm"].sum(),
            "n_days_matched": g.size(),
            "n_days_matched_reference_filled": g["_filled"].sum(),
            "_flags": g["flags"].agg(_union_flags),
        }).reset_index()
        rows = rows.merge(matched, on=[*_GROUP, "period_label"], how="left",
                          validate="one_to_one")

        ma = (mavail[mavail["available"]]
              .merge(day_map, on=["site_id", "date"], how="inner", validate="many_to_one")
              .groupby(["site_id", "model", *_SIDE, "period_label"]).size()
              .rename("n_days_model_available").reset_index())
        rows = rows.merge(ma, on=["site_id", "model", *_SIDE, "period_label"], how="left",
                          validate="many_to_one")
        ra = ravail.merge(day_map, on=["site_id", "date"], how="inner", validate="many_to_one")
        rg = ra.groupby(["site_id", "reference_variant", "period_label"])["_filled"]
        ra = pd.DataFrame({"n_days_reference_filled": rg.sum(),
                           "n_days_reference_measured": rg.size() - rg.sum()}).reset_index()
        rows = rows.merge(ra, on=["site_id", "reference_variant", "period_label"], how="left",
                          validate="many_to_one")
        if daily is not None:
            d = daily.merge(day_map, on=["site_id", "date"], how="inner",
                            validate="many_to_one")
            d["_has"] = d["value_source"] != "not_imputed"
            d["_imp"] = d["value_source"] == "imputed"
            dg = d.groupby([*_GROUP, "period_label"])
            est = pd.DataFrame({
                "_est_obs": dg["observed_estimated_mm"].sum(),
                "_est_mod": dg["modeled_estimated_mm"].sum(),
                "_n_est": dg["_has"].sum(), "_n_imp": dg["_imp"].sum(),
            }).reset_index()
            rows = rows.merge(est, on=[*_GROUP, "period_label"], how="left",
                              validate="one_to_one")
        for c in ("n_days_matched", "n_days_matched_reference_filled", "n_days_model_available",
                  "n_days_reference_filled", "n_days_reference_measured"):
            rows[c] = rows[c].fillna(0).astype(int)
        rows["period_type"] = ptype
        frames.append(rows)

    if frames:
        rows = pd.concat(frames, ignore_index=True)
        out = _products(rows, spec, support, ranges)
    else:
        out = PERIOD_TOTALS.empty()
    attributes: dict[str, Any] = {
        "workflow": "etcompare.aggregation.aggregate_cohort",
        "aggregation": to_plain(spec), "comparison_id": cid,
        "cohort_membership_sha256": cohort.fingerprint,
        "cohort_kind": cs.kind, "ranking_scope": cs.ranking_scope,
        "temporal_support": support,
        "date_semantics": "periods follow station-day labels; dates are never shifted",
    }
    table = CanonicalTable.build(out, PERIOD_TOTALS, attributes, source=f"{cid}/{aid}")
    daily_table = None
    if daily is not None:
        daily_table = CanonicalTable.build(daily[DAILY_ESTIMATES.names], DAILY_ESTIMATES,
                                           attributes, source=f"{cid}/{aid} daily")

    f = table.frame
    for ptype, g in f[f["product"] == "partial_sum"].groupby("period_type"):
        comp = f[(f["product"] == "complete_total") & (f["period_type"] == ptype)]
        n_comp = int(comp["observed_total_mm"].notna().sum())
        report.info("period_completeness",
                    f"{ptype}: {n_comp} complete of {len(comp)} period rows; "
                    f"{int((g['n_days_matched'] == 0).sum())} rows without matched days",
                    field=str(ptype), n_rows=len(g))
    return PeriodTotals(spec, cid, table, tuple(report.issues), daily_table, attributes)


def _products(rows: pd.DataFrame, spec: PeriodAggregationSpec, support: Mapping[str, str],
              ranges: Mapping[str, tuple[pd.Timestamp, pd.Timestamp]]) -> pd.DataFrame:
    rows = rows.copy()
    n_exp = rows["n_days_expected"].to_numpy()
    n_match = rows["n_days_matched"].to_numpy()
    rows["period_completeness"] = n_match / n_exp
    rows["temporal_support"] = rows["model"].map(support)
    first = rows["site_id"].map({s: r[0] for s, r in ranges.items()})
    last = rows["site_id"].map({s: r[1] for s, r in ranges.items()})
    outside = (rows["period_start"] < first) | (rows["period_end"] > last)
    rows["flags"] = [
        _union_flags(pd.Series([f, "period_outside_inputs" if o else None]))
        for f, o in zip(rows["_flags"], outside, strict=True)]
    has = n_match > 0
    support_ok = (rows["temporal_support"] == DAILY_SUPPORT).to_numpy()
    support_reason = "temporal_support_" + rows["temporal_support"]

    def product(name: str, ok: np.ndarray, reason: pd.Series | np.ndarray,
                scale: np.ndarray | float = 1.0, method: str | None = None,
                values: tuple[str, str] = ("_obs", "_mod")) -> pd.DataFrame:
        r = rows.copy()
        r["product"] = name
        r["observed_total_mm"] = np.where(ok, r[values[0]] * scale, np.nan)
        r["modeled_total_mm"] = np.where(ok, r[values[1]] * scale, np.nan)
        r["validity_reason"] = pd.Series(np.where(ok, _NONE, reason), index=r.index, dtype=object)
        r["estimation_method"] = method
        r["n_days_imputed"] = np.nan
        return r

    complete = n_match == n_exp
    out = [
        product("partial_sum", has, np.full(len(rows), "no_matched_days", dtype=object)),
        product("complete_total", support_ok & complete,
                np.where(~support_ok, support_reason, "incomplete_period")),
    ]
    if spec.estimation_method is not None:
        threshold = float(spec.estimation_min_completeness)  # type: ignore[arg-type]
        enough = rows["period_completeness"].to_numpy() >= threshold
        reason = np.where(~support_ok, support_reason,
                          np.where(~has, "no_matched_days",
                                   np.where(~enough, "below_min_completeness",
                                            "imputation_incomplete")))
        if spec.imputes:
            filled = (rows["_n_est"].fillna(0).to_numpy() == n_exp)
            ok = support_ok & has & enough & filled
            r = product("estimated_total", ok, reason, 1.0, spec.estimation_method,
                        ("_est_obs", "_est_mod"))
            r["n_days_imputed"] = rows["_n_imp"].fillna(0).astype(float)
        else:
            scale = np.divide(n_exp, n_match, out=np.full(len(rows), np.nan),
                              where=n_match > 0)
            r = product("estimated_total", support_ok & has & enough, reason, scale,
                        spec.estimation_method)
        out.append(r)
    frame = pd.concat(out, ignore_index=True)
    frame = frame.sort_values(["comparison_id", "cohort_id", "site_id", "model",
                               "reference_variant", *_SIDE, "period_type", "period_start",
                               "product"], ignore_index=True)
    return frame[PERIOD_TOTALS.names]


def _impute(paired: pd.DataFrame, groups: pd.DataFrame, targets: pd.DataFrame,
            spec: PeriodAggregationSpec, ref_et: pd.DataFrame | None) -> pd.DataFrame:
    """Daily estimates on every target day of every group (module docstring, *Imputation*)."""
    method = str(spec.estimation_method)
    max_gap = int(spec.estimation_max_gap_days)  # type: ignore[arg-type]
    by_group = {k: g for k, g in paired.groupby(_GROUP)}
    by_site = {k: pd.DatetimeIndex(g["date"]).sort_values() for k, g in targets.groupby("site_id")}
    etr_by_site = ({k: g.set_index("date")["reference_et_mm"] for k, g in ref_et.groupby("site_id")}
                   if ref_et is not None else {})
    parts = []
    for key in groups[_GROUP].itertuples(index=False, name=None):
        site = key[_GROUP.index("site_id")]
        days = by_site.get(site)
        if days is None or not len(days):
            continue
        g = by_group.get(key)
        anchors = (g.set_index("date")[["observed_mm", "modeled_mm"]] if g is not None
                   else pd.DataFrame(columns=["observed_mm", "modeled_mm"], dtype=float))
        lo = min(days.min(), anchors.index.min()) if len(anchors) else days.min()
        hi = max(days.max(), anchors.index.max()) if len(anchors) else days.max()
        idx = pd.date_range(lo, hi)
        obs = anchors["observed_mm"].reindex(idx).astype(float)
        mod = anchors["modeled_mm"].reindex(idx).astype(float)
        cohort = obs.notna()
        etr = (etr_by_site.get(site, pd.Series(dtype=float)).reindex(idx).astype(float)
               if method == "reference_et_fraction" else pd.Series(np.nan, index=idx))
        if method == "reference_et_fraction":
            usable = cohort & (etr > 0)
            yo, ym = (obs / etr).where(usable), (mod / etr).where(usable)
        else:
            usable = cohort
            yo, ym = obs, mod
        missing = ~usable
        run = (missing != missing.shift()).cumsum()
        run_len = missing.groupby(run).transform("sum")
        bracketed = (usable.cumsum() > 0) & (usable[::-1].cumsum()[::-1] > 0)
        fillable = missing & bracketed & (run_len <= max_gap)
        io = yo.interpolate(limit_area="inside").where(fillable)
        im = ym.interpolate(limit_area="inside").where(fillable)
        if method == "reference_et_fraction":
            io, im = io * etr, im * etr
        imputed = ~cohort & io.notna() & im.notna()
        est_o = obs.where(cohort, io.where(imputed))
        est_m = mod.where(cohort, im.where(imputed))
        source = np.where(cohort, "cohort", np.where(imputed, "imputed", "not_imputed"))
        f = pd.DataFrame({
            "date": idx, "observed_mm": obs.to_numpy(), "modeled_mm": mod.to_numpy(),
            "observed_estimated_mm": est_o.to_numpy(), "modeled_estimated_mm": est_m.to_numpy(),
            "value_source": source,
            "gap_length_days": np.where(cohort, np.nan, run_len.where(missing).to_numpy()),
            "reference_et_mm": etr.to_numpy() if method == "reference_et_fraction" else np.nan,
        })
        f = f[f["date"].isin(days)]
        for col, v in zip(_GROUP, key, strict=True):
            f[col] = v
        parts.append(f)
    cols = [c for c in DAILY_ESTIMATES.names if c != "estimation_method"]
    out = (pd.concat(parts, ignore_index=True) if parts else
           pd.DataFrame({c: pd.Series(dtype=object) for c in cols}))
    out["estimation_method"] = method
    return out.sort_values([*_GROUP, "date"], ignore_index=True)
