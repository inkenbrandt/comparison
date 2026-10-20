"""Run the declared event analyses and anomalies (guide step 14) on their cohorts.

A thin wrapper over ``etcompare analyze``. Writes to <output_dir>/events/:

  <event_table>_events.*, <event_table>_event_exclusions.csv
  <id>__<analysis>_responses.*, _overlaps.csv, _conditions.csv, _report.txt
  <id>__<anomaly>_anomaly_daily.*, _anomaly_metrics.*, _report.txt

    uv run python tools/run_events.py examples/us_utj.yaml [--force]
"""
from etcompare.runner import tool_main

if __name__ == "__main__":
    raise SystemExit(tool_main("analyze", ("events", "anomalies"), __doc__))
