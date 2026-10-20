"""Run the declared water balances and scenarios (guide step 15).

A thin wrapper over ``etcompare analyze``. Writes to <output_dir>/water_balance/:

  <id>_terms.*, <id>_residuals.*, <id>__<scenario>_scenarios.*, <id>_report.txt

    uv run python tools/run_water_balance.py examples/us_utj.yaml [--force]
"""
from etcompare.runner import tool_main

if __name__ == "__main__":
    raise SystemExit(tool_main("analyze", ("water_balance",), __doc__))
