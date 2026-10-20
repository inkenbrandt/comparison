# etcompare

Working name for a Python library that compares OpenET model estimates with
quality-controlled eddy covariance (EC) station ET.

Status: under development; see `IMPLEMENTATION_STATUS.md`. The scientific
contract for inputs is in `docs/input_contract.md` and design decisions are in
`docs/decisions.md`.

## Install (development)

```bash
uv sync                    # core: numpy, pandas, pyarrow, scipy, pyyaml
uv sync --extra geo        # optional raster/geospatial dependencies
uv run pytest
```

The tabular core never imports the geospatial or plotting extras, needs no
credentials, and makes no network calls.

## Command line

Everything an analysis needs is declared in a YAML configuration (see
`examples/us_utj.yaml`); the command line only chooses the stage.

```bash
etcompare validate config.yaml   # read every declared table through its adapter
etcompare index    config.yaml   # index OpenET rasters and footprint sets
etcompare extract  config.yaml   # footprint aggregation, raster extraction, footprint sets
etcompare analyze  config.yaml   # cohorts, periods, metrics, intervals, rankings, ...
etcompare report   config.yaml   # figures, Markdown/HTML report, skipped-analyses table
etcompare run      config.yaml   # all five; units whose cache is valid are reused
python -m etcompare run config.yaml   # the same, where a console script is blocked
```

Options: `--force` (rebuild instead of reusing cached outputs), `--log-format json|text`
and `--log-level`. Exit codes: 0 success, 2 configuration or data-validation error,
1 anything else. Each invocation writes `<output_dir>/runs/<run_id>/run_manifest.json`
(config hash, input checksums, package versions, git revision, upstream versions,
grids, masks, seeds, exclusion counts, unit statuses) and a JSON-lines log; the latest
manifest is copied to `<output_dir>/run_manifest.json`. The report needs the `plot`
extra (`uv sync --extra plot`).

Offline synthetic demonstration (every value generated; labelled synthetic throughout):

```bash
python -c "from etcompare.demo import write_demo; write_demo('demo')"
python -m etcompare run demo/config.yaml       # report in demo/outputs/report/
```
