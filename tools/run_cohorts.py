"""Build the comparison cohorts declared in a config (guide step 7).

Stand-in until the step-17 CLI. For each comparison (all by default), loads its
``sources`` (raster extractions from <output_dir>/<name>_extractions.parquet,
or extracted tables through their adapter) and the station tables of the sites
involved, then writes into <output_dir>/cohorts/:

  <id>_paired.parquet       paired rows of the cohort
  <id>_membership.parquet   every candidate site/date/model with its decision
  <id>_summary.csv          included days per cohort and model
  <id>_exclusions.csv       excluded units and reasons
  <id>_report.txt           validation report and fingerprint

    uv run python tools/run_cohorts.py examples/us_utj.yaml [comparison_id ...]
"""
from __future__ import annotations

import argparse

from etcompare.adapters.extracted import adapt_extracted_table
from etcompare.adapters.station import adapt_station_daily
from etcompare.cohorts import build_cohort
from etcompare.config import load_config
from etcompare.io.tables import read_table, write_table
from etcompare.registry import SiteRegistry


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("config")
    ap.add_argument("comparisons", nargs="*", help="comparison ids (default: all declared)")
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
        (out / f"{cid}_report.txt").write_text(text + "\n", encoding="utf-8")
        print(text, "\n", "-" * 78)


if __name__ == "__main__":
    main()
