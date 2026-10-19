"""Run the declared held-out ensemble experiments (guide step 16).

Loads each ensemble's sources (raster extractions from <output_dir>/<name>_extractions.parquet,
or extracted tables through their adapter) and the station tables, runs
``etcompare.ensemble.run_ensemble`` and writes to ``<output_dir>/ensembles/``:

  <id>_{days,predictions,folds,weights,inner_scores,training_membership,summary,improvement}
      .parquet / .csv                  the eight ensemble tables
  <id>__<scope>_{paired,metrics,intervals,contrasts}.parquet / .csv
                                       the frozen product cohort of each compared scope
                                       (baseline, and every design with held-out predictions)
  <id>_report.txt                      inputs (SHA-256), declaration, status per scope, folds,
                                       metrics and contrasts

    uv run python tools/run_ensembles.py examples/us_utj.yaml [ensemble_id ...]
"""
from __future__ import annotations

import argparse
import json

import pandas as pd

from etcompare.adapters.extracted import adapt_extracted_table
from etcompare.adapters.station import adapt_station_daily
from etcompare.config import load_config, to_plain
from etcompare.ensemble import run_ensemble
from etcompare.io.tables import read_table, write_table
from etcompare.registry import SiteRegistry


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("config")
    ap.add_argument("ensembles", nargs="*", help="ensemble ids (default: all declared)")
    args = ap.parse_args()
    cfg = load_config(args.config)
    out = cfg.output_dir / "ensembles"  # type: ignore[operator]
    out.mkdir(parents=True, exist_ok=True)
    raster = {x.name for x in cfg.raster_extractions}
    tables = {t.name: t for t in cfg.extracted_tables}
    stations = {t.site_id: adapt_station_daily(t.path, t, cfg.stations[t.site_id]).table  # type: ignore[arg-type]
                for t in cfg.station_tables}
    specs = [cfg.ensemble(e) for e in args.ensembles] or list(cfg.ensembles)
    for spec in specs:
        if not spec.sources:
            raise SystemExit(f"ensemble {spec.ensemble_id} declares no sources")
        ext = []
        for name in spec.sources:
            if name in raster:
                ext.append(read_table(cfg.output_dir / f"{name}_extractions.parquet"))  # type: ignore[operator]
            else:
                t = tables[name]
                ext.append(adapt_extracted_table(
                    t.path, t, models=cfg.model_registry(), sites=SiteRegistry(cfg.site_aliases),
                    products=cfg.products, stations=cfg.stations).table)
        sites = spec.sites or tuple(stations)
        obs = [stations[s] for s in sites if s in stations]
        boot = cfg.bootstrap_spec(spec.bootstrap) if spec.bootstrap else None
        r = run_ensemble(spec, obs, ext, products=cfg.products,
                         metric_spec=cfg.metric_set(spec.metric_set), bootstrap_spec=boot,
                         footprint_sets=cfg.footprint_sets)
        stem = spec.ensemble_id
        for name, t in r.tables().items():
            short = name.removeprefix("ensemble_")
            write_table(t, out / f"{stem}_{short}.parquet")
            t.frame.to_csv(out / f"{stem}_{short}.csv", index=False)
        text = [f"ensemble {stem} (post-extraction; not a pixel-level ensemble)"]
        for o in obs:
            text.append(f"station table {o.attributes.get('source')} "
                        f"sha256 {o.attributes.get('source_sha256')}")
        for name in spec.sources:
            path = (cfg.output_dir / f"{name}_extractions.parquet" if name in raster  # type: ignore[operator]
                    else tables[name].path)
            text.append(f"extractions {name}: {path}")
        text += [f"declaration: {json.dumps(to_plain(spec), default=str)}", "",
                 r.report.summary(), "", "status per scope:",
                 r.summary.frame[["scope", "subset", "status", "n_sites", "n_folds",
                                  "n_folds_fitted",
                                  "n_days_compared", "status_detail"]].to_string(index=False)]
        days = r.days.frame
        ref = days[days["reference_provenance"].notna()]
        why = ref["eligibility_reason"].fillna("eligible").value_counts()
        n_avail = ref["n_members_available"].value_counts().sort_index()
        text += ["", f"eligibility of reference days: { {str(k): int(v) for k, v in why.items()} }",
                 "members available on reference days: "
                 f"{ {int(k): int(v) for k, v in n_avail.items()} }"]
        f = r.folds.frame
        if len(f):
            text += ["", "folds:", f[["design", "fold", "status", "n_training_folds",
                                      "n_training_days", "n_buffer_days_removed",
                                      "n_test_days", "alpha_selected",
                                      "status_detail"]].to_string(index=False)]
        if len(r.weights.frame):
            text += ["", "weights:", r.weights.frame.to_string(index=False)]
        for scope, comp in r.comparisons.items():
            pre = f"{stem}__{scope}"
            for name, t in (("paired", comp.cohort.paired), ("metrics", comp.metrics.table),
                            ("contrasts", comp.ranking.contrasts),
                            *((("intervals", comp.bootstrap.intervals),) if comp.bootstrap
                              else ())):
                write_table(t, out / f"{pre}_{name}.parquet")
                t.frame.to_csv(out / f"{pre}_{name}.csv", index=False)
            m = comp.metrics.table.frame
            m = m[(m["temporal_scale"] == "daily") & m["metric"].isin(spec.contrast_metrics)]
            text += ["", f"{scope}: daily metrics on {comp.cohort.paired.frame['date'].nunique()} "
                     f"frozen days (cohort {comp.cohort.fingerprint[:12]})",
                     m.pivot(index=["scope", "model"], columns="metric", values="value")
                     .round(3)
                     .to_string()]
            c = comp.ranking.contrasts.frame
            c = c[c["ranking_basis"].astype(bool) & (c["interval_method"] == "percentile")]
            text += [f"{scope}: contrasts metric(model_a) - metric(model_b) (exploratory):",
                     c[["scope", "metric", "model_a", "model_b", "block_length", "estimate",
                        "lower",
                        "upper", "status", "favoured_model"]].round(3).to_string(index=False)]
        if len(r.improvement.frame):
            cols = ["scope", "subset", "site_id", "metric", "baseline_product", "block_length",
                    "estimate", "lower", "upper", "held_out_improvement"]
            text += ["", "held-out improvement:",
                     r.improvement.frame[cols].round(3).to_string(index=False)]
        (out / f"{stem}_report.txt").write_text("\n".join(text), encoding="utf-8")
        print("\n".join(text), "\n")


if __name__ == "__main__":
    pd.set_option("display.width", 200)
    main()
