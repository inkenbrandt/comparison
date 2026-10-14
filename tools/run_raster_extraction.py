"""Run one declared raster extraction from a config file (stand-in until the step-17 CLI).

Writes into the config's output_dir:
  <name>_extractions.parquet   canonical extractions table (with provenance attributes)
  <name>_exclusions.csv        every exclusion record
  <name>_report.txt            validation report and discovery reports

    uv run python tools/run_raster_extraction.py examples/us_utj.yaml us_utj_set_b_etw \
        [--start 2025-01-01] [--end 2025-12-31] [--strict]
"""
from __future__ import annotations

import argparse
import time

from etcompare.config import load_config
from etcompare.extraction import extract_rasters
from etcompare.io.manifest import discover_footprints, discover_openet_rasters
from etcompare.io.tables import write_table
from etcompare.log import configure_logging
from etcompare.registry import SiteRegistry


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("config")
    ap.add_argument("name")
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()
    configure_logging(json_lines=False)

    cfg = load_config(args.config)
    spec = next(x for x in cfg.raster_extractions if x.name == args.name)
    station = cfg.stations[spec.site_id]
    sites = SiteRegistry(cfg.site_aliases)
    reports = []
    models = []
    for src in cfg.openet_rasters:
        d = discover_openet_rasters(src.root, models=cfg.model_registry(), sites=sites,
                                    products=cfg.products, recursive=src.recursive)
        reports.append(d.report.summary())
        models.append(d.manifest)
    if len(models) != 1:
        raise SystemExit("exactly one openet_rasters source is supported by this script")
    fset = cfg.footprint_set(spec.footprint_set_id) if spec.footprint_set_id else None
    fman = None
    if fset is not None:
        fd = discover_footprints(fset, sites=sites)
        reports.append(fd.report.summary())
        fman = fd.manifest

    t = time.time()
    result = extract_rasters(spec, station=station, model_manifest=models[0],
                             products=cfg.products, footprint_manifest=fman, footprint_set=fset,
                             start=args.start, end=args.end, strict=args.strict)
    elapsed = time.time() - t
    out = cfg.output_dir
    out.mkdir(parents=True, exist_ok=True)
    write_table(result.table, out / f"{spec.name}_extractions.parquet")
    result.exclusions.to_frame().to_csv(out / f"{spec.name}_exclusions.csv", index=False)
    text = "\n\n".join([*reports, result.report.summary(), f"elapsed {elapsed:.0f}s"])
    (out / f"{spec.name}_report.txt").write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
