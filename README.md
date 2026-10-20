# etcompare

etcompare compares daily OpenET model evapotranspiration with quality-controlled eddy
covariance (EC) station ET. It builds on products you already have: flux-data-qaqc daily
exports, fluxfootprints footprint rasters, and daily OpenET GeoTIFFs or a table of values
already extracted from them. One YAML file declares the whole analysis. etcompare then:

- extracts model ET over footprints or fixed windows;
- builds frozen comparison cohorts;
- scores daily pairs and period totals;
- adds paired block-bootstrap intervals and rank stability;
- runs diagnostics, representativeness, event, water-balance and held-out ensemble
  analyses when they are declared;
- writes a report that states what was skipped and why.

Version 0.1.0 (MIT), on PyPI: `pip install "etcompare[geo,plot]"`.

- **Documentation:** `docs/`, built with Sphinx for Read the Docs (method guide,
  configuration, limitations, public API, data dictionary, references).
- **Changes:** `CHANGELOG.md`.
- **Design decisions:** `docs/decisions.md`.
- **Development status:** `IMPLEMENTATION_STATUS.md`.

## Install

```bash
pip install "etcompare[geo,plot]"   # geo = rasters, plot = figures
pip install -e ".[geo,plot]"       # or from a checkout
# or, for development:
uv sync --extra geo --extra plot
```

The core (tables, cohorts, metrics, intervals, rankings, reports without figures) needs
only numpy, pandas, pyarrow, scipy and PyYAML. Importing etcompare makes no network calls
and needs no credentials.

## Try it offline

```bash
etcompare demo demo --rasters      # fixed-seed synthetic stations, models, rasters, footprints
etcompare run demo/config.yaml     # about 4–5 minutes; the report is in demo/outputs/report/
```

Every value in the demonstration is generated. Its stations (`XX-SY1..3`) and models are not
real, and every report page and caption says so. Without `--rasters`, the demonstration
uses pre-extracted tables only and needs no `geo` extra.

Where the console script is blocked (for example by Windows Application Control), use
`python -m etcompare` instead. The notebooks in `notebooks/` cover:

- a Colab quickstart (Google Drive is optional);
- pre-extracted tables;
- the raster workflow.

## Command line

```bash
etcompare validate config.yaml   # read every declared table through its adapter
etcompare index    config.yaml   # index OpenET rasters and footprint sets
etcompare extract  config.yaml   # footprint aggregation, raster extraction, footprint sets
etcompare analyze  config.yaml   # cohorts, periods, metrics, intervals, rankings, ...
etcompare report   config.yaml   # figures, Markdown/HTML report, skipped-analyses table
etcompare run      config.yaml   # all five; units whose inputs did not change are reused
etcompare demo     DIRECTORY     # write the synthetic demonstration (--rasters adds GeoTIFFs)
```

Options:
- `--force` rebuilds instead of reusing the cache.
- `--log-format json|text` and `--log-level` control stderr logging.

Exit codes:
- **0:** success.
- **2:** a configuration or data-validation error, including a systematic read failure.
- **1:** any other error.

Each run writes `<output_dir>/runs/<run_id>/run_manifest.json` and a JSON-lines log. The
manifest records the configuration hash, input checksums, versions, git revision, grids,
masks, seeds, exclusion counts and unit statuses.

Long raster runs are protected by the `processing:` section:

- keep-awake on Windows;
- finite retries of transient read errors;
- stall and suspend detection;
- monthly checkpoints, so an interrupted extraction resumes to identical outputs;
- unreadable files skipped and counted;
- a run that fails outright when failures look systematic.

See `docs/configuration.md`.

## Limitations

See `docs/limitations.md` for the full list. In short:

- results are station level;
- ensembles are built after extraction, never pixel-level;
- there is no hierarchical interval across stations;
- there is no polygon ET extraction;
- staging and concurrency are not implemented.

Real-station data and reports stay local and are git-ignored.
