# Limitations and implementation gaps

This page lists what etcompare does not do, or does only partly, as of version 0.1.0.
Nothing listed here is presented elsewhere as complete. Decision numbers (D-…) refer to
the project's decision log (D-numbers), which is kept with the source repository and not published.

## Not implemented

| Gap | Consequence | Where it shows |
|---|---|---|
| **Pixel-level ensembles** (combining model rasters before extraction) | Every ensemble is built from extracted values (D-065). | Rows are flagged `post_extraction_ensemble`, `not_pixel_level_ensemble`. |
| **Hierarchical intervals across stations** (resampling sites together with common calendar blocks) | Intervals and rankings are station level. Summaries across sites have no interval and are not evidence about a population of sites. | `network_summaries` status `single_site` or no bounds; the ranking report. |
| **Polygon area-weighted extraction of model ET** (a field-polygon support for `raster_extractions`) | Polygons are supported only for landscape description (representativeness), not as an ET extraction method. | `EXTRACTION_METHODS` has no polygon method. |
| **Modified KGE** (Kling et al., 2012) and later variants | Only the 2009 KGE exists. The two are never mixed. | `metrics` (D-044). |
| **Mosaicking** of rasters split across files | Duplicates and conflicts must be resolved by an explicit selection (D-021). | Index conflicts table. |
| **Monthly-raster extraction with monthly footprints** | Period totals are sums of daily extractions. | `aggregation` docs. |
| **Staging of remote-mounted files to local storage** | Not built (user decision, 2026-10-20: work from a local drive). Reading directly from a network or cloud mount is supported but slower: about 23 ms per raster open on Google Drive for desktop against 5 ms on a USB SSD (D-079). | — |
| **Concurrency** | Extraction is serial (user decision, 2026-10-20, after the D-079 benchmark: about 1.5 min per station; I/O-bound by file-open latency). | — |
| **Reuse of a raster shared by several stations or extractions** | Each extraction opens its own rasters. A multi-station file (for example an unclipped download covering several stations) is opened once per station, and once per footprint set of a station. Clipped single-station files are unaffected. | D-079 benchmark: 3,078 opens for 3,077 files in one extraction. |
| **Population inference, formal causal designs for events, controlled event designs** | Event responses are uncontrolled before/after descriptions (D-061). | `event_responses` flags. |

## Partly implemented or conditional

- **Satellite support** needs authoritative clear-observation metadata. Without it, which is
  the case for every station so far (D-011), the analysis is `not_available`.
- **Held-out anomaly skill** needs a multi-year record. Shorter records get only the
  `exploratory` in-sample moving mean.
- **Fitted ensembles** are `declined` when folds are too few or too small. For example, one
  station with one complete calendar year has a single fold (D-066).
- **Equivalence and formal tests** need declared margins and `formal_tests: true`.
  Otherwise results are `not_assessed` and `exploratory`.
- **No block length is adopted** for intervals (D-048). The declared lengths are reported
  side by side.
- **Water balance:** a term without an input makes the residual unknown, (D-064).
- **fluxfootprints labels** need fluxfootprints installed separately (GPL-3.0, not an
  extra; D-056).

## Resilience: known bounds

- **Stall timeout.** A read blocked inside GDAL cannot be cancelled safely. When
  `stall_timeout_s` passes, the run is marked failed and the main thread is interrupted as
  soon as it runs Python again. If it is still blocked after 30 s, the process exits with
  code 1. Checkpoints written so far remain usable.
- **Keep-awake** works on Windows only. On Linux and macOS, prevent sleep yourself, for
  example with `systemd-inhibit` or `caffeinate`.
- **Checkpoints** cover raster extraction only. Other units are cached whole, so an
  interruption inside one of them reruns only that unit.
- **Footprint sets** written by footprint aggregation are atomic, and read back, per file,
  but not as a set. An interrupted aggregation leaves some daily files, and its unit is
  rerun.
- **Coverage reason codes.** A model-day whose raster could not be read appears in cohort
  membership as `modeled_value_null`. The specific reason (`unreadable_raster`,
  `raster_missing`) is in the extraction's exclusions and in the report's skipped-analyses
  table.
- **Cache identity on cloud drives.** Rasters are identified by size and modification time
  by default. Whether Google Drive for desktop keeps modification times stable was not
  verified. `cache.raster_identity: sha256` avoids the question at the cost of reading
  every byte.

## Data semantics the package cannot verify

- **Day alignment.** OpenET's daily day definition is not published with the rasters, so a
  product declared `day_definition: unknown` gives rows `day_alignment=unverified` (D-002).
- **Upstream weighting and units.** Footprint weighting, station units and gap-filling
  semantics are taken from the declarations. etcompare checks what the data can show
  (for example normalisation and negative weights), but it cannot prove an upstream
  method.

## Release status

- Not published to PyPI. The name `etcompare` was free on PyPI, TestPyPI and conda-forge
  on 2026-10-20.
- No Read the Docs project exists yet. The configuration (`.readthedocs.yaml`) is ready.
- The GitHub Actions workflow is written but has not run. The repository has not been
  pushed for this release.
- Tested locally on Windows 11 with Python 3.13 (current dependencies) and Python 3.11
  (lowest declared dependencies, core tests). The CI matrix adds Ubuntu.
