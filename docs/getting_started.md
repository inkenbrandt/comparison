# Getting started

## Install

etcompare needs Python 3.11 or later. The core (tables, cohorts, metrics, intervals,
rankings, diagnostics, ensembles, reports without figures) depends only on numpy, pandas,
pyarrow, scipy and PyYAML.

```bash
pip install "etcompare[geo,plot]"   # once published; until then, from a checkout:
pip install -e ".[geo,plot]"
```

- `geo` (rasterio, pyproj, shapely, geopandas): raster indexing and extraction, footprint
  aggregation, landscape representativeness.
- `plot` (matplotlib): figures in the report.

The dependency versions are lower bounds, not pins (decision D-018). For an exactly pinned
environment that reproduces the demonstration, see {ref}`pinned-demo`.

The optional fluxfootprints representativeness labels need
[fluxfootprints](https://github.com/inkenbrandt/footprints) installed separately (GPL-3.0;
it is deliberately not an extra, D-056). Without it those labels are `not_available`.

## Run the offline demonstration

No data, network access or credentials are needed.

```bash
etcompare demo demo --rasters      # writes demo/data/ and demo/config.yaml
etcompare run demo/config.yaml     # validate, index, extract, analyze, report
```

Where the console script is blocked (some managed Windows machines), use
`python -m etcompare` instead of `etcompare`.

`--rasters` adds synthetic GeoTIFFs (about 1,850 small files, 9 MB), so index, raster
extraction and representativeness run end to end. Without `--rasters` the demonstration
uses pre-extracted tables only and needs no `geo` extra. Every value is generated with a
fixed seed. Station IDs start with `XX-`, an ISO 3166 code assigned to no country, and
every page and caption of the report is labelled synthetic.

The full raster demonstration takes about 4–5 minutes. A second `etcompare run` reuses
every cached unit and finishes in seconds.

### Inspect the outputs

| What | Where |
|---|---|
| Paired observation–model rows of a comparison | `demo/outputs/cohorts/<comparison>_paired.parquet` (and `.csv`) |
| Every candidate day, kept or excluded, with its reason | `demo/outputs/cohorts/<comparison>_membership.parquet` |
| Exclusions of an extraction (files, footprints, cells) | `demo/outputs/<extraction>_exclusions.csv` |
| Metrics | `demo/outputs/cohorts/<comparison>__<metric_set>_metrics.parquet` |
| Unit statuses of the run | `demo/outputs/runs/<run_id>/analysis_status.csv` |
| Run manifest (inputs, versions, seeds, exclusion counts) | `demo/outputs/runs/<run_id>/run_manifest.json` |
| Report | `demo/outputs/report/report.html` and `report.md` |

### Reproduce a metric

The metrics are plain functions of the paired rows. For example, the daily MAE of one model
at one station:

```python
import pandas as pd

paired = pd.read_parquet("demo/outputs/cohorts/syn_rank_measured_paired.parquet")
one = paired[(paired.model == "alpha") & (paired.site_id == "XX-SY1")]
print(len(one), (one.modeled_mm - one.observed_mm).abs().mean())

metrics = pd.read_parquet("demo/outputs/cohorts/syn_rank_measured__syn_metrics_metrics.parquet")
print(metrics.query("model == 'alpha' and scope == 'XX-SY1' and metric == 'mae' "
                    "and product == 'daily_pairs'")[["value", "n_days"]])
```

The two values and the day counts are equal. The test suite checks this reproduction for
every model, station and metric of the demonstration.

## Command line

```text
etcompare validate config.yaml   read every declared table through its adapter
etcompare index    config.yaml   index rasters and footprint sets
etcompare extract  config.yaml   footprint aggregation, raster extraction, footprint sets
etcompare analyze  config.yaml   cohorts, periods, metrics, intervals, rankings, ...
etcompare report   config.yaml   figures, Markdown/HTML report, skipped-analyses table
etcompare run      config.yaml   all five; units whose cache is valid are reused
etcompare demo     DIRECTORY     write the synthetic demonstration (--rasters for GeoTIFFs)
```

Options:
- `--force` rebuilds instead of reusing the cache.
- `--log-format json|text` and `--log-level` set stderr logging. The run log
  `runs/<run_id>/log.jsonl` is always JSON lines.

Exit codes:
- **0:** success.
- **2:** a configuration, contract or data-validation error. This includes invalid YAML, an
  indexed file that disappeared, and a systematic read failure.
- **1:** any other error. This includes a stall timeout.

## Notebooks and Colab

The `notebooks/` folder holds three notebooks:

- `01_colab_quickstart.ipynb`: install, the demonstration, or your own files by path. Mounting
  Google Drive is an optional cell you run yourself.
- `02_pre_extracted_tables.ipynb`: the table-only workflow, from a pre-extracted table to
  metrics and intervals.
- `03_raster_workflow.ipynb`: indexing, extraction and representativeness from GeoTIFFs.

Every path is a variable you set. Nothing assumes a drive letter or a Drive folder.

(pinned-demo)=
## A pinned environment for the demonstration

`examples/demo/requirements-demo.txt` pins every package, so the demonstration can be
reproduced exactly:

```bash
python -m venv .venv-demo
.venv-demo/bin/pip install -r examples/demo/requirements-demo.txt   # Windows: .venv-demo\Scripts\pip
.venv-demo/bin/pip install --no-deps -e .
```

The pins apply to the demonstration only. The library itself declares lower bounds.
