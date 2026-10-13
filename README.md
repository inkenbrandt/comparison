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
