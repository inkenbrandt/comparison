"""Compare two raster extractions that differ only in footprint set (guide step 6).

Stand-in until the step-17 CLI. Reads <output_dir>/<name>_extractions.parquet for
each named raster extraction (the first is the baseline), joins the declared
station table, and writes into output_dir:

  <out>_paired.parquet       frozen paired rows, one per footprint set
  <out>_differences.csv      set-minus-baseline modeled-ET differences
  <out>_availability.csv     usable rows per set/method/model before freezing
  <out>_exclusions.csv       every key dropped and why
  <out>_report.txt           validation report

    uv run python tools/run_footprint_sensitivity.py examples/us_utj.yaml \
        us_utj_set_b_etw us_utj_set_b_mean --out us_utj_set_b_etw_vs_mean
"""
from __future__ import annotations

import argparse

from etcompare.adapters.station import adapt_station_daily
from etcompare.config import load_config
from etcompare.footprint_sensitivity import compare_footprint_sets
from etcompare.io.tables import read_table, write_table


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("config")
    ap.add_argument("extractions", nargs="+", help="raster extraction names; first = baseline")
    ap.add_argument("--out", required=True)
    ap.add_argument("--methods", nargs="+",
                    default=["footprint_conservative", "footprint_conservative_common_support"])
    ap.add_argument("--min-retained-frac", type=float)
    ap.add_argument("--common-models", action="store_true")
    args = ap.parse_args()

    cfg = load_config(args.config)
    specs = [next(x for x in cfg.raster_extractions if x.name == n) for n in args.extractions]
    sites = {s.site_id for s in specs}
    if len(sites) != 1:
        raise SystemExit(f"extractions are for different sites {sorted(sites)}")
    (site,) = sites
    st_spec = next(t for t in cfg.station_tables if t.site_id == site)
    station = adapt_station_daily(st_spec.path, st_spec, cfg.stations[site])  # type: ignore[arg-type]
    out = cfg.output_dir
    tables = [read_table(out / f"{s.name}_extractions.parquet") for s in specs]  # type: ignore[operator]
    fsets = [cfg.footprint_set(s.footprint_set_id) for s in specs]  # type: ignore[arg-type]

    r = compare_footprint_sets(tables, fsets, comparison_id=args.out,
                               station_daily=station.table,
                               extraction_methods=args.methods,
                               min_retained_frac=args.min_retained_frac,
                               common_models=args.common_models)
    if r.paired is not None:
        write_table(r.paired, out / f"{args.out}_paired.parquet")  # type: ignore[operator]
    r.differences.to_csv(out / f"{args.out}_differences.csv", index=False)  # type: ignore[operator]
    r.availability.to_csv(out / f"{args.out}_availability.csv", index=False)  # type: ignore[operator]
    r.exclusions.to_frame().to_csv(out / f"{args.out}_exclusions.csv", index=False)  # type: ignore[operator]
    text = f"status {r.status} {r.reason}\n\n{r.report.summary()}"
    (out / f"{args.out}_report.txt").write_text(text + "\n", encoding="utf-8")  # type: ignore[operator]
    print(text)
    if not r.differences.empty:
        cols = ["reference_variant", "extraction_method", "model", "n_days", "baseline_mean_mm",
                "mean_diff_mm", "median_diff_mm", "p95_abs_diff_mm", "max_abs_diff_mm"]
        print(r.differences[cols].round(4).to_string(index=False))


if __name__ == "__main__":
    main()
