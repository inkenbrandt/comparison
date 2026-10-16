"""Build the declared comparison cohorts, their period totals and metrics (steps 7-9).

Stand-in until the step-17 CLI. For each comparison (all by default), loads its
``sources`` (raster extractions from <output_dir>/<name>_extractions.parquet,
or extracted tables through their adapter) and the station tables of the sites
involved, then writes into <output_dir>/cohorts/:

  <id>_paired.parquet       paired rows of the cohort
  <id>_membership.parquet   every candidate site/date/model with its decision
  <id>_summary.csv          included days per cohort and model
  <id>_exclusions.csv       excluded units and reasons
  <id>_report.txt           validation report and fingerprint

and, for every declared period aggregation that applies to the comparison:

  <id>__<agg>_periods.parquet   period_totals rows (partial, complete, estimated)
  <id>__<agg>_periods.csv       the same, for inspection
  <id>__<agg>_summary.csv       periods and days per cohort/model/variant/period type
  <id>__<agg>_daily_estimates.parquet / .csv
                                daily values with imputations in separate columns
                                (imputation methods only)

and, for every declared metric set that applies to the comparison (step 9):

  <id>__<set>_metrics.parquet / .csv
                                metrics rows: daily pairs and each period product of
                                the listed aggregations, kept apart (metrics schema)
  <id>__<set>_metrics_wide.csv  one row per scored sample, one column per metric
  <id>__<set>_cumulative_error.parquet / .csv
                                running daily error sums with missing-period flags

plus metric_definitions.csv (exact formulas, units and roles), and, for every declared
bootstrap whose metric set applies (step 10):

  <id>__<bootstrap>_intervals.parquet / .csv
                                paired block-bootstrap intervals of every metric and
                                paired contrast, at every declared block length
  <id>__<bootstrap>_autocorrelation.parquet / .csv
                                residual (daily error) autocorrelation by calendar lag

Growing seasons declared with ``growing_season_source`` are read from the named
crop-height table; reference ET for ``reference_et_fraction`` comes from the
station tables' ``reference_et_column``. A missing crop-height file stops the
run unless --skip-missing-crop-height is given; then only the growing-season
periods are left out, and every report says so.

    uv run python tools/run_cohorts.py examples/us_utj.yaml [comparison_id ...] [--no-bootstrap]
"""
from __future__ import annotations

import argparse
import time
from dataclasses import replace

import pandas as pd

from etcompare.adapters.crop_height import adapt_crop_height, growing_season_windows
from etcompare.adapters.extracted import adapt_extracted_table
from etcompare.adapters.station import adapt_station_daily
from etcompare.aggregation import aggregate_cohort
from etcompare.bootstrap import bootstrap_comparison
from etcompare.cohorts import build_cohort
from etcompare.config import load_config
from etcompare.io.tables import read_table, write_table
from etcompare.metrics import definitions_frame, score_comparison
from etcompare.registry import SiteRegistry


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("config")
    ap.add_argument("comparisons", nargs="*", help="comparison ids (default: all declared)")
    ap.add_argument("--skip-missing-crop-height", action="store_true",
                    help="leave out growing-season periods whose crop-height file is absent")
    ap.add_argument("--no-bootstrap", action="store_true",
                    help="skip the declared bootstrap intervals (step 10)")
    args = ap.parse_args()

    cfg = load_config(args.config)
    out = cfg.output_dir / "cohorts"  # type: ignore[operator]
    out.mkdir(parents=True, exist_ok=True)
    specs = [cfg.comparison(c) for c in args.comparisons] or list(cfg.comparisons)
    raster = {x.name: x for x in cfg.raster_extractions}
    tables = {t.name: t for t in cfg.extracted_tables}
    cache: dict[str, object] = {}

    def load(name: str):
        if name not in cache:
            if name in raster:
                cache[name] = read_table(cfg.output_dir / f"{name}_extractions.parquet")  # type: ignore[operator]
            else:
                t = tables[name]
                cache[name] = adapt_extracted_table(
                    t.path, t, models=cfg.model_registry(), sites=SiteRegistry(cfg.site_aliases),
                    products=cfg.products, stations=cfg.stations).table
        return cache[name]

    stations = {}
    for t in cfg.station_tables:
        stations[t.site_id] = adapt_station_daily(t.path, t, cfg.stations[t.site_id]).table  # type: ignore[arg-type]

    aggregations = []
    skipped: list[str] = []
    for agg in cfg.period_aggregations:
        if agg.growing_season_source and "growing_season" in agg.periods:
            crop = cfg.crop_height_table(agg.growing_season_source)
            if args.skip_missing_crop_height and not crop.path.exists():  # type: ignore[union-attr]
                note = (f"{agg.aggregation_id}: growing_season periods SKIPPED; crop-height file "
                        f"{crop.path} not found (--skip-missing-crop-height)")
                print("WARNING", note)
                skipped.append(note)
                agg = replace(agg, periods=tuple(p for p in agg.periods if p != "growing_season"),
                              growing_season_source=None)
            else:
                r = adapt_crop_height(crop.path, crop)  # type: ignore[arg-type]
                print(r.report.summary())
                agg = agg.with_growing_seasons(growing_season_windows(r))
        aggregations.append(agg)
    if cfg.metric_sets:
        definitions_frame().to_csv(out / "metric_definitions.csv", index=False)

    for spec in specs:
        if not spec.sources:
            raise SystemExit(f"comparison {spec.comparison_id} declares no sources")
        ext = [load(s) for s in spec.sources]
        sites = spec.sites or tuple(stations)
        obs = [stations[s] for s in sites if s in stations]
        c = build_cohort(obs, ext, spec, footprint_sets=cfg.footprint_sets)  # type: ignore[arg-type]
        cid = spec.comparison_id
        write_table(c.paired, out / f"{cid}_paired.parquet")
        write_table(c.membership, out / f"{cid}_membership.parquet")
        summary = c.summary()
        summary.to_csv(out / f"{cid}_summary.csv", index=False)
        c.exclusions.to_frame().to_csv(out / f"{cid}_exclusions.csv", index=False)
        m = c.membership.frame
        reasons = m[~m["included"].astype(bool)].groupby(["cohort_id", "reason_code"]).size()
        text = (f"comparison {cid} ({spec.kind}, ranking scope {spec.ranking_scope})\n"
                f"status {c.status}; membership sha256 {c.fingerprint}\n\n"
                f"{c.report.summary()}\n\n{summary.to_string(index=False)}\n\n"
                f"exclusions by reason:\n{reasons.to_string()}")
        for note in skipped:
            text += f"\n\nWARNING {note}"
        totals = []
        for agg in aggregations:
            if agg.comparisons is not None and cid not in agg.comparisons:
                continue
            t = aggregate_cohort(c, agg, products=cfg.products, reference_et=obs)
            totals.append(t)
            stem = f"{cid}__{agg.aggregation_id}"
            write_table(t.table, out / f"{stem}_periods.parquet")
            t.table.frame.to_csv(out / f"{stem}_periods.csv", index=False)
            s = t.summary()
            s.to_csv(out / f"{stem}_summary.csv", index=False)
            if t.daily_estimates is not None:
                write_table(t.daily_estimates, out / f"{stem}_daily_estimates.parquet")
                t.daily_estimates.frame.to_csv(out / f"{stem}_daily_estimates.csv", index=False)
            text += (f"\n\nperiod totals {agg.aggregation_id}:\n{t.report.summary()}\n"
                     f"{s.to_string(index=False)}")
        for ms in cfg.metric_sets:
            if ms.comparisons is not None and cid not in ms.comparisons:
                continue
            res = score_comparison(c, ms, products=cfg.products, period_totals=totals)
            stem = f"{cid}__{ms.metric_set_id}"
            write_table(res.table, out / f"{stem}_metrics.parquet")
            res.table.frame.to_csv(out / f"{stem}_metrics.csv", index=False)
            wide = res.wide()
            wide.to_csv(out / f"{stem}_metrics_wide.csv", index=False)
            if res.cumulative is not None:
                write_table(res.cumulative, out / f"{stem}_cumulative_error.parquet")
                res.cumulative.frame.to_csv(out / f"{stem}_cumulative_error.csv", index=False)
            daily = wide[wide["product"] == "daily_pairs"].drop(
                columns=["scope", "temporal_scale", "period_type", "aggregation_id", "product",
                         "n_periods"])
            text += (f"\n\nmetrics {ms.metric_set_id}:\n{res.report.summary()}\n"
                     f"daily pairs:\n{daily.to_string(index=False)}")
            for bsp in ([] if args.no_bootstrap else cfg.bootstrap):
                if bsp.metric_set != ms.metric_set_id or (
                        bsp.comparisons is not None and cid not in bsp.comparisons):
                    continue
                t0 = time.perf_counter()
                boot = bootstrap_comparison(c, ms, bsp, products=cfg.products,
                                            period_totals=totals, metric_results=res)
                stem = f"{cid}__{bsp.bootstrap_id}"
                write_table(boot.intervals, out / f"{stem}_intervals.parquet")
                boot.intervals.frame.to_csv(out / f"{stem}_intervals.csv", index=False)
                write_table(boot.autocorrelation, out / f"{stem}_autocorrelation.parquet")
                boot.autocorrelation.frame.to_csv(out / f"{stem}_autocorrelation.csv",
                                                  index=False)
                text += (f"\n\nbootstrap {bsp.bootstrap_id} ({time.perf_counter() - t0:.0f} s; "
                         "station-level sampling intervals, not rankings):\n"
                         f"{boot.report.summary()}\n{_bootstrap_text(boot)}")
        (out / f"{cid}_report.txt").write_text(text + "\n", encoding="utf-8")
        print(text, "\n", "-" * 78)


def _bootstrap_text(boot) -> str:
    """Status counts, daily RMSE/MBE intervals side by side per block length, ACF summary."""
    f = boot.intervals.frame
    status = (f.groupby(["temporal_scale", "block_length", "status"]).size()
              .unstack(fill_value=0))
    d = f[(f["product"] == "daily_pairs") & (f["metric"].isin(["mbe", "rmse"]))
          & (f["interval_method"] == "percentile")].copy()
    d["interval"] = [f"{lo:.3f}..{hi:.3f}" if s == "ok" else s
                     for lo, hi, s in zip(d["lower"], d["upper"], d["status"], strict=True)]
    keys = ["cohort_id", "model", "reference_variant", "extraction_method",
            "contrast_dimension", "contrast_baseline", "metric", "estimate", "n_days"]
    wide = d.pivot_table(index=keys, columns="block_length", values="interval",
                         aggfunc="first").reset_index()
    wide.columns = [f"b={c}" if isinstance(c, int | float) else c for c in wide.columns]
    a = boot.autocorrelation.frame
    lags = [k for k in (1, 7, 14, 28) if k in set(a["lag"])]
    acf = a[a["lag"].isin(lags)].pivot_table(
        index=["cohort_id", "model", "reference_variant", "extraction_method"],
        columns="lag", values="autocorrelation").round(2)
    below = a[a["autocorrelation"].abs() < a["white_noise_bound"]]
    first = below.groupby(["cohort_id", "model", "reference_variant",
                           "extraction_method"])["lag"].min().rename("first_lag_within_bound")
    acf = acf.join(first)
    acf.columns = [f"lag{c}" if isinstance(c, int) else c for c in acf.columns]
    with pd.option_context("display.width", 250, "display.max_columns", 30):
        return (f"interval rows by status:\n{status.to_string()}\n\n"
                f"daily MBE / RMSE percentile intervals by block length:\n"
                f"{wide.to_string(index=False)}\n\n"
                f"residual autocorrelation of daily errors:\n{acf.to_string()}")


if __name__ == "__main__":
    main()
