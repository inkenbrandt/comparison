"""Run the declared water balances and scenarios (guide step 15).

Reads the station tables, attaches crop-height growing seasons to the declared step-8 period
aggregation when needed, and writes to ``<output_dir>/water_balance/``:

  <id>_terms.parquet / .csv            water_balance_terms
  <id>_residuals.parquet / .csv        water_balance_residuals
  <id>__<scenario>_scenarios.parquet / .csv   scenario_results (declared scenarios only)
  <id>_report.txt                      inputs (SHA-256), declaration, validation, summary

    uv run python tools/run_water_balance.py examples/us_utj.yaml
"""
from __future__ import annotations

import argparse
import json

import pandas as pd

from etcompare.adapters.crop_height import adapt_crop_height, growing_season_windows
from etcompare.adapters.station import adapt_station_daily
from etcompare.config import load_config, to_plain
from etcompare.io.tables import write_table
from etcompare.scenarios import run_scenario
from etcompare.water_balance import compute_water_balance


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("config")
    args = ap.parse_args()
    cfg = load_config(args.config)
    out = cfg.output_dir / "water_balance"  # type: ignore[operator]
    out.mkdir(parents=True, exist_ok=True)
    stations = {t.site_id: adapt_station_daily(t.path, t, cfg.stations[t.site_id]).table  # type: ignore[arg-type]
                for t in cfg.station_tables}
    for wb in cfg.water_balances:
        agg = cfg.period_aggregation(wb.period_aggregation)
        periods = wb.periods or agg.periods
        if "growing_season" in periods and agg.growing_season_source:
            crop = cfg.crop_height_table(agg.growing_season_source)
            agg = agg.with_growing_seasons(
                growing_season_windows(adapt_crop_height(crop.path, crop)))  # type: ignore[arg-type]
        station = stations[wb.site_id]
        kw = {"period_aggregation": agg, "station": cfg.stations[wb.site_id]}
        r = compute_water_balance(wb, station, **kw)
        stem = wb.water_balance_id
        for name, t in (("terms", r.terms), ("residuals", r.residuals)):
            write_table(t, out / f"{stem}_{name}.parquet")
            t.frame.to_csv(out / f"{stem}_{name}.csv", index=False)
        res = r.residuals.frame
        terms = r.terms.frame
        main_rows = res[res["residual_kind"] == "measured_modeled"]
        wide = (terms.set_index(["period_type", "period_label", "term"])["total_mm"]
                .unstack("term")[list(wb.terms)])
        text = [f"water balance {stem}: {wb.equation_text} ({wb.sign_convention})",
                f"station table {station.attributes.get('source')} "
                f"sha256 {station.attributes.get('source_sha256')}",
                f"declaration: {json.dumps(to_plain(wb), default=str)}",
                "", r.report.summary(), "",
                "residual status (measured/modeled terms): "
                f"{main_rows['status'].value_counts().to_dict()}",
                "unknown terms: "
                f"{main_rows['unknown_terms'].value_counts(dropna=False).to_dict()}",
                "incomplete terms: "
                f"{main_rows['incomplete_terms'].value_counts(dropna=False).to_dict()}",
                "support mismatch: "
                f"{main_rows['support_mismatch_terms'].value_counts(dropna=False).to_dict()}",
                "volume status: "
                f"{res['volume_status'].value_counts().to_dict()}", "",
                "complete term totals (mm; blank = no full-period total):",
                wide.round(1).to_string(), "",
                "ET term (gap-filled days per period):",
                terms[terms["term"] == "et"][["period_type", "period_label", "n_values_present",
                                              "n_days_expected", "n_days_et_gap_filled",
                                              "total_status"]].to_string(index=False)]
        for sc in cfg.scenarios:
            if sc.water_balance != wb.water_balance_id:
                continue
            s = run_scenario(sc, wb, station, **kw)
            write_table(s.results, out / f"{stem}__{sc.scenario_id}_scenarios.parquet")
            s.results.frame.to_csv(out / f"{stem}__{sc.scenario_id}_scenarios.csv", index=False)
            text += ["", f"scenario {sc.scenario_id} ({sc.category}):", s.report.summary()]
        if not any(sc.water_balance == wb.water_balance_id for sc in cfg.scenarios):
            text += ["", "scenarios: none declared"]
        (out / f"{stem}_report.txt").write_text("\n".join(text), encoding="utf-8")
        print("\n".join(text), "\n")


if __name__ == "__main__":
    pd.set_option("display.width", 200)
    main()
