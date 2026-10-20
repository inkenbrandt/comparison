# Public API

Every module below is public. Names starting with an underscore are not. Importing `etcompare` performs no network access, opens no raster and imports no optional dependency; the `geo` and `plot` extras are imported only by the functions that need them.

The stable entry points for most users are:

- the command line, {mod}`etcompare.cli` (`etcompare run config.yaml`);
- {func}`etcompare.config.load_config` and {func}`etcompare.runner.run_stages`;
- the analysis functions {func}`~etcompare.cohorts.build_cohort`, {func}`~etcompare.aggregation.aggregate_cohort`, {func}`~etcompare.metrics.score_comparison`, {func}`~etcompare.bootstrap.bootstrap_comparison`, {func}`~etcompare.ranking.rank_comparison`, {func}`~etcompare.extraction.extract_rasters`;
- {func}`etcompare.io.tables.read_table` for any table etcompare wrote (see {doc}`data_dictionary`).

## Configuration and errors

Everything is declared in a YAML file and validated here; errors name the configuration key to fix.

```{toctree}
:maxdepth: 1

api/etcompare.config
api/etcompare.errors
api/etcompare.registry
```

## Running a configuration

The command line and the workflow it drives; run manifests and the cache; resilient processing (step 18).

```{toctree}
:maxdepth: 1

api/etcompare.cli
api/etcompare.runner
api/etcompare.workflow
api/etcompare.runrecord
api/etcompare.resilience
api/etcompare.provenance
api/etcompare.log
```

## Tables, results and validation

Canonical schemas, Parquet round trips, atomic writes and result objects.

```{toctree}
:maxdepth: 1

api/etcompare.schemas
api/etcompare.io.tables
api/etcompare.io.atomic
api/etcompare.results
api/etcompare.validation
api/etcompare.references
```

## Inputs and adapters

Upstream products mapped onto canonical tables; file discovery.

```{toctree}
:maxdepth: 1

api/etcompare.adapters.station
api/etcompare.adapters.extracted
api/etcompare.adapters.crop_height
api/etcompare.adapters.event_log
api/etcompare.adapters.filenames
api/etcompare.adapters.fluxfootprints
api/etcompare.adapters.fluxfootprints_labels
api/etcompare.io.manifest
```

## Rasters and footprints (geo extra)

Bounded raster access, conservative transfer, extraction, footprint aggregation and weighting sensitivity.

```{toctree}
:maxdepth: 1

api/etcompare.io.rasters
api/etcompare.spatial
api/etcompare.footprints
api/etcompare.extraction
api/etcompare.footprint_aggregation
api/etcompare.footprint_sensitivity
```

## Comparisons

Cohorts, period totals, metrics, intervals and rankings (guide steps 7-11).

```{toctree}
:maxdepth: 1

api/etcompare.cohorts
api/etcompare.aggregation
api/etcompare.metrics
api/etcompare.bootstrap
api/etcompare.ranking
```

## Diagnostics and optional analyses

Guide steps 12-16.

```{toctree}
:maxdepth: 1

api/etcompare.diagnostics
api/etcompare.representativeness
api/etcompare.satellite
api/etcompare.events
api/etcompare.anomalies
api/etcompare.water_balance
api/etcompare.scenarios
api/etcompare.ensemble
```

## Reports and the demonstration

Figures need the plot extra.

```{toctree}
:maxdepth: 1

api/etcompare.reporting
api/etcompare.plots
api/etcompare.demo
```
