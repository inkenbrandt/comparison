"""Landscape representativeness of the declared footprint sets (guide step 13).

A thin wrapper over ``etcompare analyze``; needs the ``geo`` extra (without it the unit is
``not_available``). Writes into <output_dir>/representativeness/:

  <id>_class_fractions.*, _index_summaries.*, _differences.*, _labels.*, _availability.*
  <id>_report.txt                        inputs, settings and a summary

Fractions and labels describe the landscape of each support; they are not ET accuracy
guarantees. Satellite support needs cohorts and is part of tools/run_cohorts.py.

    uv run python tools/run_representativeness.py examples/us_utj.yaml [id ...] [--force]
"""
from etcompare.runner import tool_main

if __name__ == "__main__":
    raise SystemExit(tool_main("analyze", ("representativeness",), __doc__))
