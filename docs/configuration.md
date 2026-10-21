# Configuration

One YAML file declares an entire analysis. `etcompare.config.load_config` validates it
before anything runs, and an unknown key, value or reference is refused with the key that
needs fixing (exit code 2). Relative paths are resolved against the file's own folder. Keys
starting with `x-` hold YAML anchors and are otherwise ignored.

Every threshold in the example configurations (`examples/*.yaml`, the demonstration) is a
**Provisional** example value fixed before any score was computed. None is an established
standard. Open and provisional choices that outputs depend on are listed in `decisions:`,
and every report repeats them.

An analysis runs when its section is declared. `run.skip` switches declared analyses off.
Every undeclared analysis is reported as `skipped` (`not_declared`).

## Sections

| Section | Validated by | Declares |
|---|---|---|
| `stations` | {class}`~etcompare.config.StationSpec` | site IDs, coordinates, time zone and day definition |
| `products` | {class}`~etcompare.config.ProductSpec` | each model's units, day definition, temporal support, raster scaling |
| `model_aliases`, `site_aliases` | mappings | alternative tokens in filenames and tables |
| `station_tables` | {class}`~etcompare.config.StationTableSpec` | daily station exports and their columns |
| `extracted_tables` | {class}`~etcompare.config.ExtractedTableSpec` | tables of already extracted model values |
| `openet_rasters` | {class}`~etcompare.config.OpenETRasterSource` | folders of daily OpenET GeoTIFFs |
| `footprint_sets` | {class}`~etcompare.config.FootprintSetSpec` | footprint raster sets and their weighting |
| `raster_extractions` | {class}`~etcompare.config.RasterExtractionSpec` | extraction methods per station and period |
| `footprint_aggregations`, `footprint_stacks` | {class}`~etcompare.config.TemporalAggregationSpec`, {class}`~etcompare.config.FootprintStackSpec` | per-timestep footprints aggregated to days |
| `footprint_comparisons` | {class}`~etcompare.config.FootprintComparisonSpec` | footprint-set sensitivity on frozen keys |
| `comparisons` | {class}`~etcompare.config.ComparisonSpec` | cohorts: models, reference variants, extraction sides, rules |
| `period_aggregations`, `crop_height_tables` | {class}`~etcompare.config.PeriodAggregationSpec`, {class}`~etcompare.config.CropHeightTableSpec` | months, seasons, years and their completeness rules |
| `metric_sets` | {class}`~etcompare.config.MetricSpec` | metrics and near-zero tolerances |
| `bootstrap` | {class}`~etcompare.config.BootstrapSpec` | block lengths, replicates, seed, support rules |
| `rankings` | {class}`~etcompare.config.RankingSpec` | ranked metrics, directions, margins, tests |
| `diagnostics` | {class}`~etcompare.config.DiagnosticsSpec` | closure, provenance and conditional-error bins |
| `representativeness` | {class}`~etcompare.config.RepresentativenessSpec` | land-cover layers, polygons, matching policies |
| `satellite_support` | {class}`~etcompare.config.SatelliteSupportSpec` | authoritative clear-observation metadata, if any |
| `event_tables`, `event_analyses`, `anomalies` | {class}`~etcompare.config.EventTableSpec`, {class}`~etcompare.config.EventAnalysisSpec`, {class}`~etcompare.config.AnomalySpec` | management events and anomaly skill |
| `water_balances`, `scenarios` | {class}`~etcompare.config.WaterBalanceSpec`, {class}`~etcompare.config.ScenarioSpec` | water-balance terms and declared uncertainty |
| `ensembles` | {class}`~etcompare.config.EnsembleSpec` | members, folds, fitting and decline rules |
| `run` | {class}`~etcompare.config.RunSpec` | analyses to skip |
| `report` | {class}`~etcompare.config.ReportSpec` | title, formats, synthetic labelling |
| `cache` | {class}`~etcompare.config.CacheSpec` | cache on/off; raster identity `stat` or `sha256` |
| `processing` | {class}`~etcompare.config.ProcessingSpec` | resilient processing (below) |
| `upstream` | {class}`~etcompare.config.UpstreamSpec` | declared versions of upstream software and their evidence |
| `decisions` | {class}`~etcompare.config.DecisionSpec` | open or provisional decisions outputs depend on |
| `output_dir` | path | where every output goes |
| `data_roots` | mapping | named data folders that paths may start with (below) |

The class documentation (see {doc}`api`) lists every field, its default and its rules.

## Data folders (`data_roots:`)

A path may start with `${NAME}`, where `NAME` is declared under `data_roots`. When the data
move to another drive, only that one line changes:

```yaml
data_roots:
  FLUX: "/data/flux"          # a relative folder resolves against this file's folder
station_tables:
  - {site_id: XX-UTJ, path: "${FLUX}/database/daily_output/XX-UTJ_daily.csv", ...}
openet_rasters:
  - root: "${FLUX}/OpenET_Daily_Tiffs"
```

An undeclared name is refused. Environment variables are not read, so a configuration
resolves the same way everywhere.

## Footprint models (`filename_prefix`)

Footprint files are named `<model>_<site>_<daily|monthly>_<layer>_<date>.tif`. The model is
a short name from the fluxfootprints model registry: `ffp`, `ffp_xr`, `km`, `ls` or `wang`.
Several models can sit in one folder with the same site, day and layer, so each footprint
set holds one model: `filename_prefix: ffp` (the default) or another of these. Files of
other models are left out and counted in the discovery report (`products_not_selected`).

## Resilient processing (`processing:`)

None of these settings changes a result. They decide only whether a run waits, retries,
resumes or stops. They are therefore not part of any cache key.

| Field | Default | Meaning |
|---|---|---|
| `keep_awake` | `true` | Asks Windows not to idle-sleep while a run is active (`SetThreadExecutionState`). Elsewhere it does nothing, and the log says so. A long extraction once paused for 9 h because the machine slept (D-078). |
| `io_attempts` | `3` | Tries in all for a raster or footprint read that raises an I/O error while the file still exists. |
| `io_backoff_s` | `[1, 4]` | Seconds to wait before each retry. The last value repeats. |
| `stall_warning_s` | `120` | Logs `io_stall` when no raster has finished for this long. Only raster reading is watched. |
| `stall_timeout_s` | none | When set, a stall this long fails the run (exit code 1). Checkpoints are kept. |
| `checkpoint` | `month` | Saves extraction progress after each calendar month; `none` disables it. |
| `max_unreadable_fraction` | `0.01` | Fraction of model rasters, or of footprints, that may be unreadable or missing before a non-strict extraction fails as systematic. |

These rules always apply, whatever the settings:

- A file that no longer exists is `raster_missing` (or `footprint_file_missing`) at once,
  with no retry.
- Configuration, contract and grid errors are never retried.
- When every file of one model, every model raster or every footprint is unreadable, the
  run fails (exit code 2) and the manifest is marked `failed`. The extraction table is not
  written, and the exclusions CSV lists every file.
- With `raster_extractions[].strict: true`, the first unreadable file stops the run.

Every retry (`io_retry`), final failure (`io_failed`), stall (`io_stall`), suspend
(`system_suspended`), checkpoint and keep-awake request is a structured event in the run's
`log.jsonl`. Skipped files appear in:

- the unit's exclusion counts in the manifest;
- the extraction's exclusions CSV;
- the report's skipped-analyses table.

Their model-days stay in the extraction table as null rows, so coverage shows them.
