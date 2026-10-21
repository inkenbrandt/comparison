# Changelog

All notable changes are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). Decision numbers (D-…) refer to
`docs/decisions.md`.

## [Unreleased]

### Added
- `data_roots:` names data folders that configuration paths may start with (`${NAME}`), so a
  moved data drive is a one-line change (D-081).
- Footprint filenames of every fluxfootprints model parse (`ffp_`, `ffp_xr_`, `km_`, `ls_`,
  `wang_`). Each footprint set declares its model (`filename_prefix`, default `ffp`), and
  discovery leaves out files of other models (D-081).
- `examples/flux_drive.ipynb` reads the FLUX data folder with `examples/flux_drive.yaml`:
  stations, footprint sets, OpenET rasters, the extraction run and a first look at its output.
  Like the real-station configurations, it stays out of the source distribution.

## [0.1.0] - 2026-10-20

First usable release, published to PyPI on 2026-10-20.

### Added
- **Inputs (guide steps 1–4).**
  - Declared input contract; station-table, pre-extracted-table, crop-height and event-log
    adapters.
  - Raster and footprint discovery with explicit duplicate and conflict handling (D-021,
    D-069).
- **Raster workflow (steps 5–6).**
  - Bounded-window extraction with conservative transfer onto the native model grid (D-028).
  - Footprint, common-support, pixel-window and tower-pixel methods (D-029).
  - Footprint aggregation from per-timestep stacks; footprint-weighting sensitivity on
    frozen keys (D-032 to D-034).
- **Comparisons (steps 7–11).**
  - Frozen cohorts with a reason for every excluded candidate (D-039).
  - Period totals with calendar completeness and optional estimates (D-041, D-042).
  - Transparent metrics (D-044).
  - Paired, segment-truncated moving-block bootstrap intervals (D-047).
  - Rankings with rank stability, contrasts, equivalence margins and network summaries
    (D-049).
- **Diagnostics and optional analyses (steps 12–16).**
  - Closure, provenance and conditional-error diagnostics (D-051).
  - Landscape representativeness (D-055) and satellite support (D-057).
  - Management events and anomalies (D-060, D-061).
  - Water balance and declared scenarios (D-062, D-063).
  - Held-out post-extraction ensembles (D-065).
- **Workflow, reports and CLI (step 17).**
  - `etcompare validate|index|extract|analyze|report|run`.
  - Per-unit cache with exact invalidation (D-074) and versioned run manifests (D-075).
  - Reports and figures built only from saved tables, with a skipped-analyses table and
    open decisions (D-076).
- **Resilient processing (step 18; D-079).** A new `processing:` configuration section:
  - finite retries of transient read errors, each logged as `io_retry`;
  - `raster_missing` for files deleted after indexing;
  - keep-awake on Windows;
  - stall and system-suspend detection with an optional stall timeout;
  - monthly extraction checkpoints keyed by the unit's cache key;
  - a systematic-failure rule: every file of a model, every file, or more than
    `max_unreadable_fraction` unreadable fails the run.
- **Unreadable files are visible.**
  - The model-days of unreadable or missing rasters keep null rows, so they show in
    coverage.
  - Skipped files are listed in the report's skipped-analyses table.
- **Atomic outputs.** Every output (CSV, text, JSON, figures, footprint files, stamps,
  manifests, Parquet) is written to a temporary file and renamed.
- **Run manifests** are written with status `running` at the start of a run.
- **Synthetic demonstration.**
  - `etcompare demo DIR [--rasters]`.
  - `--rasters` writes fixed-seed synthetic GeoTIFFs, footprints and a land-cover map, so
    that index, extraction and representativeness run offline.
  - The tables-only demonstration is unchanged.
- **Documentation.**
  - Sphinx (MyST) documentation for Read the Docs: getting started, configuration, method
    guide, limitations, public API, data dictionary generated from the schemas, and
    references checked against Crossref.
- **Release tooling.**
  - Notebooks: a Colab quickstart, pre-extracted tables, and the raster workflow.
  - GitHub Actions CI.
  - A pinned demonstration environment (`examples/demo/requirements-demo.txt`).
  - MIT licence.

### Changed
- Invalid YAML is a configuration error (exit code 2), not exit code 1.
- `--log-level` now filters stderr even when the run's log file is attached.
- The local real-station example configuration reads rasters from their new folder (D-054). The
  extracted values equal the earlier extraction except `source_path`.

### Known gaps
See `docs/limitations.md`.
