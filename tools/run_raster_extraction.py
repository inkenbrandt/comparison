"""Run declared raster extractions (guide step 5); a thin wrapper over ``etcompare extract``.

Writes into the config's output_dir, for each named (default: every) extraction:
  <name>_extractions.parquet   canonical extractions table (with provenance attributes)
  <name>_exclusions.csv        every exclusion record
  <name>_report.txt            validation report and discovery reports

The period (``start``/``end``) and ``strict`` are declared on the extraction in the config.

    uv run python tools/run_raster_extraction.py examples/us_utj.yaml us_utj_nldas_eto [--force]
"""
from etcompare.runner import tool_main

if __name__ == "__main__":
    raise SystemExit(tool_main("extract", ("raster_extraction",), __doc__))
