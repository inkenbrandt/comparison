"""Run the declared held-out ensemble experiments (guide step 16).

A thin wrapper over ``etcompare analyze``. Writes to <output_dir>/ensembles/:

  <id>_{days,predictions,folds,weights,inner_scores,training_membership,summary,improvement}.*
  <id>__<scope>_{paired,metrics,intervals,contrasts}.*   each compared scope
  <id>_report.txt

    uv run python tools/run_ensembles.py examples/us_utj.yaml [ensemble_id ...] [--force]
"""
from etcompare.runner import tool_main

if __name__ == "__main__":
    raise SystemExit(tool_main("analyze", ("ensembles",), __doc__))
