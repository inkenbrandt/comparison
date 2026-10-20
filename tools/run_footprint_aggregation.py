"""Aggregate per-timestep footprint stacks into the declared footprint sets (guide step 6).

A thin wrapper over ``etcompare extract``. Each station's stacks are declared in
``footprint_stacks`` (``site_id``, ``stack_dir``, optional ``compare_sets``). Every
``footprint_aggregations`` method whose target set (``weighting_method:
etcompare_aggregation:<method_id>``, ``set_id`` starting with ``<site>_``) is declared
receives one GeoTIFF per day in its ``root``, plus ``<root>/<set_id>_periods.csv``.
``compare_sets`` writes <output_dir>/<site>_aggregate_vs_existing_footprints.csv
(total-variation distance per day; 0 = identical shape, 1 = disjoint).

    uv run python tools/run_footprint_aggregation.py examples/us_utj.yaml US-UTJ [--force]
"""
from etcompare.runner import tool_main

if __name__ == "__main__":
    raise SystemExit(tool_main("extract", ("footprint_aggregation",), __doc__))
