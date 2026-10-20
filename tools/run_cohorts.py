"""Cohorts, period totals, metrics, intervals, rankings, diagnostics and satellite support.

Guide steps 7-13. A thin wrapper over ``etcompare analyze`` that runs only these units
(all declared comparisons, or those named), reusing every unit whose cache is valid.
Writes into <output_dir>/cohorts/ (the same files as before step 17):

  <id>_paired.parquet, _membership.parquet, _summary.csv, _exclusions.csv, _report.txt
  <id>__<agg>_periods.*, _summary.csv, _daily_estimates.*      period totals (step 8)
  <id>__<set>_metrics.*, _metrics_wide.csv, _cumulative_error.*  metrics (step 9)
  <id>__<bootstrap>_intervals.*, _autocorrelation.*             intervals (step 10)
  <id>__<ranking>_{rankings,rank_distribution,contrasts,network}.*  (step 11)
  <ranking>_sensitivity.*, <diag>__<ranking>_reference_rank_changes.*
  <id>__<diag>_{closure,provenance,conditional,regressions,availability}.*  (step 12)
  <id>__<sat>_satellite_{errors,availability}.*                 (step 13)

Former flags are configuration now: ``--no-bootstrap`` is ``run.skip: [bootstrap]``, and
``--skip-missing-crop-height`` is ``crop_height_tables[].on_missing: skip_growing_season``.

    uv run python tools/run_cohorts.py examples/us_utj.yaml [comparison_id ...] [--force]
"""
from etcompare.runner import tool_main

ANALYSES = ("cohorts", "periods", "metrics", "bootstrap", "rankings", "ranking_sensitivity",
            "diagnostics", "satellite_support")

if __name__ == "__main__":
    raise SystemExit(tool_main("analyze", ANALYSES, __doc__))
