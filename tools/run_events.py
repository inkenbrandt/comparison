"""Run the declared event analyses and anomalies (guide step 14) on their cohorts.

Reads the event tables (``event_tables:``), builds every comparison listed by an
``event_analyses:`` or ``anomalies:`` entry, and writes to ``<output_dir>/events/``:

  <event_table>_events.parquet / .csv          the station_events table
  <event_table>_event_exclusions.csv           excluded log rows, with reasons
  <id>__<analysis>_responses.parquet / .csv    event_responses
  <id>__<analysis>_overlaps.csv                event_overlaps
  <id>__<analysis>_conditions.csv              event_conditions
  <id>__<anomaly>_anomaly_daily.parquet / .csv anomaly_daily
  <id>__<anomaly>_anomaly_metrics.parquet / .csv anomaly_metrics
  <id>__report.txt                             validation reports and short summaries

    uv run python tools/run_events.py examples/us_utj.yaml
"""
from __future__ import annotations

import argparse

import pandas as pd

from etcompare.adapters.event_log import adapt_event_table
from etcompare.adapters.extracted import adapt_extracted_table
from etcompare.adapters.station import adapt_station_daily
from etcompare.anomalies import compute_anomalies
from etcompare.cohorts import build_cohort
from etcompare.config import load_config
from etcompare.events import analyse_events
from etcompare.io.tables import read_table, write_table
from etcompare.registry import SiteRegistry


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("config")
    args = ap.parse_args()
    cfg = load_config(args.config)
    out = cfg.output_dir / "events"  # type: ignore[operator]
    out.mkdir(parents=True, exist_ok=True)

    events = {}
    for et in cfg.event_tables:
        r = adapt_event_table(et.path, et, stations=cfg.stations)  # type: ignore[arg-type]
        events[et.name] = r.table
        write_table(r.table, out / f"{et.name}_events.parquet")
        r.frame.to_csv(out / f"{et.name}_events.csv", index=False)
        r.exclusions.to_frame().to_csv(out / f"{et.name}_event_exclusions.csv", index=False)
        print(r.report.summary())

    stations = {t.site_id: adapt_station_daily(t.path, t, cfg.stations[t.site_id]).table  # type: ignore[arg-type]
                for t in cfg.station_tables}
    raster = {x.name for x in cfg.raster_extractions}
    tables = {t.name: t for t in cfg.extracted_tables}

    def load(name: str):
        if name in raster:
            return read_table(cfg.output_dir / f"{name}_extractions.parquet")  # type: ignore[operator]
        t = tables[name]
        return adapt_extracted_table(t.path, t, models=cfg.model_registry(),
                                     sites=SiteRegistry(cfg.site_aliases), products=cfg.products,
                                     stations=cfg.stations).table

    wanted = [c.comparison_id for c in cfg.comparisons
              if any(c.comparison_id in (a.comparisons or ())
                     for a in (*cfg.event_analyses, *cfg.anomalies))]
    for cid in wanted:
        spec = cfg.comparison(cid)
        obs = [stations[s] for s in (spec.sites or tuple(stations)) if s in stations]
        c = build_cohort(obs, [load(s) for s in spec.sources], spec,
                         footprint_sets=cfg.footprint_sets)
        text = f"comparison {cid} ({spec.kind}); membership sha256 {c.fingerprint}"
        for ea in cfg.event_analyses:
            if ea.comparisons is not None and cid not in ea.comparisons:
                continue
            r = analyse_events(c, events[ea.event_table], ea, station_daily=obs)
            stem = f"{cid}__{ea.event_analysis_id}"
            write_table(r.responses, out / f"{stem}_responses.parquet")
            r.responses.frame.to_csv(out / f"{stem}_responses.csv", index=False)
            r.overlaps.frame.to_csv(out / f"{stem}_overlaps.csv", index=False)
            r.conditions.frame.to_csv(out / f"{stem}_conditions.csv", index=False)
            f = r.responses.frame
            show = f[f["series"] != "modeled"].pivot_table(
                index=["event_start_date", "model"], columns="series",
                values=["change_mm", "recovery_days"], aggfunc="first")
            status = f.groupby(["event_start_date", "status"]).size()
            text += (f"\n\nevent analysis {ea.event_analysis_id}:\n{r.report.summary()}\n"
                     f"status per event:\n{status.to_string()}\n{show.round(2).to_string()}")
        for an in cfg.anomalies:
            if an.comparisons is not None and cid not in an.comparisons:
                continue
            r = compute_anomalies(c, an, cfg.metric_set(an.metric_set))
            stem = f"{cid}__{an.anomaly_id}"
            for name, t in (("anomaly_daily", r.daily), ("anomaly_metrics", r.metrics)):
                write_table(t, out / f"{stem}_{name}.parquet")
                t.frame.to_csv(out / f"{stem}_{name}.csv", index=False)
            m = r.metrics.frame
            wide = m.pivot_table(index=["model", "fold", "series_kind"], columns="metric",
                                 values="value", aggfunc="first")
            text += (f"\n\nanomalies {an.anomaly_id}:\n{r.report.summary()}\n"
                     f"statuses: {m['status'].value_counts().to_dict()}\n"
                     f"{wide.round(3).to_string() if len(wide) else '(no values)'}")
        (out / f"{cid}__report.txt").write_text(text, encoding="utf-8")
        print(text, "\n")


if __name__ == "__main__":
    pd.set_option("display.width", 200)
    main()
