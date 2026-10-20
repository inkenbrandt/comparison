"""Compare raster extractions that differ only in footprint set (guide step 6).

A thin wrapper over ``etcompare extract``. Each comparison is declared in
``footprint_comparisons`` (``comparison_id``, ``extractions`` with the baseline first,
``methods``, ``min_retained_frac``, ``common_models``). Writes into output_dir:

  <id>_paired.parquet       frozen paired rows, one per footprint set
  <id>_differences.csv      set-minus-baseline modeled-ET differences
  <id>_availability.csv     usable rows per set/method/model before freezing
  <id>_exclusions.csv       every key dropped and why
  <id>_report.txt           validation report

    uv run python tools/run_footprint_sensitivity.py examples/us_utj.yaml us_utj_set_b_etw_vs_mean
"""
from etcompare.runner import tool_main

if __name__ == "__main__":
    raise SystemExit(tool_main("extract", ("footprint_comparison",), __doc__))
