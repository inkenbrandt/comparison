# Implementation status

Tracks progress against `openet_ec_library_ai_implementation_guide.md`. Newest entries first.

| Step | Status |
|---|---|
| 1 — Inspect inputs, freeze analysis contract | **Done** 2026-10-13 |
| 2 — Package skeleton | **Done** 2026-10-13 |
| 3 — Canonical tables and result objects | **Done** 2026-10-13 |
| 4 — File discovery and upstream adapters | **Done** 2026-10-13 (exported-file adapters; see limitations) |
| 5 — Spatial extraction | **Done** 2026-10-13 (footprint set B; polygon area-weighted mode not implemented: no field polygons) |
| 6 — Footprint aggregation sensitivity | **Done** 2026-10-14 (NLDAS-ETo primary and measured-LE sensitivity footprints built from one per-timestep stack) |
| 7 — Comparison cohorts | Not started (next) |
| 8–18 | Not started |

## Step 6 addendum — NLDAS-ETo primary, measured-LE sensitivity (2026-10-14)

**User decision:** rely on NLDAS weights and also provide measured-LE weights. The user also chose the inputs: database heights (read-only), the production 08–20 h window, and per-timestep renormalization.

**Run it**
```bash
F=../footprints/.venv/Scripts/python.exe        # footprints repo environment (upstream)
Q="M:/Shared drives/UGS_Flux/Data_Processing"
$F tools/upstream/fetch_footprint_parameters.py US-UTJ outputs/footprint_inputs      # read-only SELECTs
$F tools/upstream/run_timestep_footprints.py --site US-UTJ --lat 37.281746 --lon -109.53489 \
   --metflux "$Q/final_database_tables/database_exports_20260825/US-UTJ_metflux_qc.parquet" \
   --params outputs/footprint_inputs/US-UTJ_daily_footprint_parameters.parquet \
   --nldas "$Q/final_processing_workflow/footprints/nldas/nldas_results/US-UTJ_nldas.csv" \
   --out outputs/footprint_timesteps/US-UTJ                                          # ≈1 min
uv run python tools/run_footprint_aggregation.py examples/us_utj.yaml US-UTJ \
   outputs/footprint_timesteps/US-UTJ --compare-sets US-UTJ_set_A_20261005 US-UTJ_set_B_etw_20261012
uv run python tools/run_raster_extraction.py examples/us_utj.yaml us_utj_nldas_eto       # ≈3.5 min
uv run python tools/run_raster_extraction.py examples/us_utj.yaml us_utj_measured_le     # ≈3 min
uv run python tools/run_footprint_sensitivity.py examples/us_utj.yaml us_utj_nldas_eto \
   us_utj_measured_le --out us_utj_nldas_vs_measured_le
```

### Delivered
- **[tools/upstream/](tools/upstream/):** these scripts run with the footprints repo environment, outside the etcompare core.
  - `fetch_footprint_parameters.py`: read-only queries of the heights and the tower location.
  - `run_timestep_footprints.py`: saves every half-hour footprint from fluxfootprints `ffp_xr`, using the production inputs, as monthly `.npz` stacks with full provenance (input SHA-256 and fluxfootprints file hashes).
- **[src/etcompare/adapters/fluxfootprints.py](src/etcompare/adapters/fluxfootprints.py):** `load_timestep_stack`.
- **Aggregation:** `combine_results` in [footprint_aggregation.py](src/etcompare/footprint_aggregation.py).
- **Config:**
  - New `footprint_aggregations` YAML section, so aggregation specs are now declared in config.
  - `TemporalAggregationSpec.weight_column`.
  - New footprint sets `US-UTJ_nldas_eto_20261014` (primary) and `US-UTJ_measured_le_20261014`.
  - New raster extractions `us_utj_nldas_eto` (all methods) and `us_utj_measured_le`.
  - Station coordinates now come from the database (37.281746, −109.53489).
- **[tools/run_footprint_aggregation.py](tools/run_footprint_aggregation.py):** writes the declared sets, checks the written semantics against the config, and measures distance to existing sets.
- **Fix:** `compare_footprint_sets` now checks keys on the compared rows only, so footprint-free pixel rows repeated across runs no longer collide.

### Findings
- **The existing `etw` sets are NLDAS-grass-ETo weighted with negative ETo kept** (D-036). The reproduction matches set A to a median 0.2 % (maximum 0.7 %) relative L1. Negative weights reappear on all 43 days, and on set B's 3 round-off days. This resolves D-009 and D-015 q2.
- **"NLDAS ET"** in fluxfootprints means FAO-56 grass ETo computed from NLDAS-2 forcing, not land-model ET.
- **Station time** is fixed UTC−7: the SW_IN centroid falls at 12.1–12.6 h. NLDAS radiation sits a steady ≈0.5 h later; its hour-labelling convention is unconfirmed.
- **Coverage:** NLDAS 474 days; measured LE 391 days (83 days have no LE).
- **Comparisons** (D-038):
  - NLDAS − set B `etw`: ≤ 0.005 mm/day.
  - Measured LE − NLDAS: per-model mean +0.002 to +0.015 mm/day, 95th-percentile |difference| ≤ 0.15. The largest differences fall on days with few LE half-hours.

### Checks
- 151 tests pass (3 opt-in skipped), including synthetic tests of `combine_results`, the stack loader and the `weight_column` rules.
- ruff and mypy are clean.
- The opt-in Drive raster tests pass.

### Open
- **Minimum temporal coverage for the LE set:** none is declared. The user needs to choose one before scoring (D-038).
- **NLDAS hour-label convention:** a ≈0.5 h ambiguity.
- **Other stations:** the per-timestep stacks (455 MB) are local outputs; they are not generated for other stations.

## Step 6 — 2026-10-14

**Run the checks**
```bash
uv sync --extra geo && uv run pytest                    # 149 passed, 3 skipped (opt-in Drive tests)
uv run ruff check src tests && uv run mypy              # clean
uv run python tools/run_raster_extraction.py examples/us_utj.yaml us_utj_set_b_mean   # ≈4 min
uv run python tools/run_footprint_sensitivity.py examples/us_utj.yaml \
    us_utj_set_b_etw us_utj_set_b_mean --out us_utj_set_b_etw_vs_mean
```

### Delivered
- **[src/etcompare/footprint_aggregation.py](src/etcompare/footprint_aggregation.py)** builds period footprints from per-timestep footprints with `W_i = Σ a_t f_it / Σ a_t` (D-033).
  - Every choice is declared in `TemporalAggregationSpec` (in `config`): the weighting, `include_duration`, the original-contribution or per-timestep-renormalized basis, and the negative-weight, gap and night policies, plus an optional minimum coverage.
  - Each period reports eligible counts, exclusions by reason, `temporal_coverage_frac` (denominator: period duration) and, on the original basis, domain capture.
  - Missing stacks or weights return `not_available`.
  - `write_footprint_set` writes the aggregates as a regular footprint set (GeoTIFFs, a capture CSV and a JSON provenance file), so step-5 discovery and extraction consume them unchanged.
- **[src/etcompare/footprint_sensitivity.py](src/etcompare/footprint_sensitivity.py)**: `compare_footprint_sets` builds the frozen-key comparison (D-034).
  - Per-variant cohorts plus a model-only cohort.
  - An optional uniform `min_retained_frac` and `common_models`.
  - Outputs: `paired` rows, set-minus-baseline differences, pre-freeze availability, and an exclusion for every dropped key.
  - Measured- or unknown-weighted sets give a warning and the row flag `footprint_target_dependence=`.
- **[src/etcompare/references.py](src/etcompare/references.py)**: `reference_values` applies the D-004 variant rules. Step 7 will reuse it.
- **Config:** `FootprintSetSpec.temporal_weighting` is a controlled field with derived `target_dependence` (D-032). The example declares set B `mean` = `time_mean`, the `etw` sets = `unknown`, and a second raster extraction `us_utj_set_b_mean`.
- **Schema:** `paired` v2 adds the key `footprint_set_id`; nothing used v1.
- **I/O:** `io.rasters.write_footprint` writes float64 GeoTIFFs atomically.
- **Tools:** [tools/run_footprint_sensitivity.py](tools/run_footprint_sensitivity.py) is a stand-in until the step-17 CLI.

### Gate checks (guide step 6)
In [tests/test_footprint_aggregation.py](tests/test_footprint_aggregation.py) and [tests/test_footprint_sensitivity.py](tests/test_footprint_sensitivity.py); expected values come from direct numpy:
- **Duration weighting:** equal temporal weights with unequal durations reproduce `Σ d_t f_t / Σ d_t` to 1e-14. Without durations the result is the plain mean.
- **Scale invariance:** multiplying all weights by 7.3 leaves W unchanged (1e-14). Series weights are matched by stamp, not by position.
- **Missing inputs:** no stack, or no weights for a weighted method, gives `not_available` with a reason, and nothing is built.
- **Basis:** the renormalized basis differs from the original basis and drops capture. Original-basis capture equals `Σ a cap / Σ a`. Normalized per-timestep input with the original basis is rejected.
- **Policies:**
  - Negative weights are excluded, rejected, or kept with negative aggregate cells counted.
  - Gap exclusion or rejection works as declared.
  - Night exclusion needs a flag.
  - End-labelled midnight stamps go to the previous day.
  - Coverage thresholds apply, with monthly denominators.
- **Upstream reproduction:** fluxfootprints `summarize_periods` `mean` and `etw` formulas are reproduced to 1e-13.
- **Round trip:** the aggregates are written, rediscovered and read back bit-identical, with capture attached.
- **Frozen dates:**
  - A date missing in one set is removed from both.
  - Cohorts per variant keep each observation paired; filled days appear only in the inclusive variant.
  - The retained threshold drops a day on both sides.
  - The `common_models` rule applies.
  - Target-dependence flags are set.
  - A set with no data is skipped as `not_available`.

### US-UTJ results (set B, local outputs, git-ignored)
- **`mean` extraction:**
  - 474 footprint days (18 with no weight, 0 negative); 2,549 rows per footprint method; 0 errors.
  - Retained weight is 1.000 except SIMS (minimum 0.192) and DisALEXI (minimum 0.997).
  - Domain capture 0.873–1.042.
- **Comparison with `etw`:**
  - 4,706 of 5,098 keys are frozen. Every key is an `etw` key; the 40 days on which `etw` was rejected for negative weights are dropped from `mean` too.
  - Cohorts: 434 days with no reference restriction, 276 for `et_raw` and `et_corrected_measured`, and 432 for `et_corrected_inclusive`.
  - `mean` − `etw`: mean differences −0.046 to −0.001 mm/day; 95th-percentile |difference| ≤ 0.24; maximum 0.48 (D-035).
  - Descriptive only, and flagged as possibly target-dependent (D-032).

### Limitations
- **Per-timestep aggregation is untested on real data:** no per-timestep footprints or weight series exist for US-UTJ. Re-weighting the daily rasters is refused by design.
- **`etw` weighting is unknown:** which ET variable it used (measured LE or NLDAS) is unrecorded, so its target dependence is `unknown`.
- **`temporal_coverage_frac` is still null in footprint manifests:** the domain CSV's `valid_timesteps` (7–26 per day) has no declared timestep length or expected count.
- **No YAML block for aggregation specs yet:** they are Python objects until the step-17 CLI.
- **No station-agreement metrics:** these are step 9. `paired` rows are ready for steps 7–10.

**Next:** step 7 (explicit comparison cohorts). Generalize the frozen-key logic into `build_cohort` and an immutable cohort object.

## Step 5 — 2026-10-13

**D-016 decided by the user: footprint set B.** Its `etw` layer is the primary footprint; its `mean` layer is declared for step 6.

**Run the checks**
```bash
uv sync --extra geo && uv run pytest                    # 128 passed, 1 skipped
ETCOMPARE_RASTER_TESTS=1 uv run pytest tests/integration # real Drive rasters (≈20 s)
uv run python tools/run_raster_extraction.py examples/us_utj.yaml us_utj_set_b_etw
```

### Delivered
- [src/etcompare/spatial.py](src/etcompare/spatial.py) — the transfer core:
  - `Grid`/`Window` types and conservative contribution transfer onto the native model grid (D-028). The exact path for aligned grids in the same CRS needs only numpy and scipy; other grid pairs use a shapely polygon-overlap path.
  - Weighted extraction, which tracks the retained, in-extent and valid-given-extent fractions with named denominators, and support IDs.
  - Pixel-block means.
- [src/etcompare/footprints.py](src/etcompare/footprints.py) prepares footprints:
  - NaN or nodata cells get zero weight.
  - `density_per_m2` values are converted to contributions using cell area (projected metre CRS required).
  - Negative weights are rejected beyond a round-off tolerance (D-027); zero-total footprints are rejected.
- [src/etcompare/io/rasters.py](src/etcompare/io/rasters.py) reads rasters in bounded windows with declared scaling. A header nodata that differs from the declaration is an error (D-026).
- [src/etcompare/extraction.py](src/etcompare/extraction.py) runs the per-station workflow:
  - Methods: `footprint_conservative`, `footprint_conservative_common_support`, `pixel_window_<n>x<n>` and `nearest_tower_pixel` (D-029).
  - Each footprint is read once per day and each raster once. Transfer matrices are cached per grid pair.
  - Unreadable files become counted exclusions (or raise in strict mode). Contract violations always raise.
- **Config:**
  - `ProductSpec.raster_scaling`/`scale_factor`/`add_offset`/`nodata`.
  - `FootprintSetSpec.domain_capture_*`.
  - `RasterExtractionSpec`, including a declared `start`/`end` period.
  - Top-level `x-` anchor keys are allowed.
- **Schema:** optional extraction columns `fp_frac_in_model_extent`, `fp_domain_capture_frac`, `n_cells_valid`, `model_grid_id`, plus footprint-manifest `domain_capture_source`. The set-B domain-capture CSV is attached by layer (D-030).
- **Tools:** [tools/profile_footprints.py](tools/profile_footprints.py) profiles footprint sets. [tools/run_raster_extraction.py](tools/run_raster_extraction.py) is a stand-in runner until the step-17 CLI.

### Gate checks (guide step 5)
- **Synthetic** ([tests/test_spatial.py](tests/test_spatial.py), [tests/test_extraction.py](tests/test_extraction.py)):
  - Uniform ET is unchanged under any weights.
  - ET `[2, 6]` with weights `[0.75, 0.25]` gives 3. Masking the second cell gives 2 with retained 0.75.
  - Transfer conserves total contribution.
  - Analytic cases pass: non-overlap, partial and shifted cells, a rotated grid.
  - The polygon path equals the exact path to 1e-12. UTM → EPSG:4326 agrees with point sampling.
  - Density conversion, differing model masks and the common support behave as specified.
  - Rejected for: negative weights, zero total, missing CRS, undeclared scaling, mismatched nodata.
  - Zero ET is valid, and nodata is never zero-filled.
  - The end-to-end GeoTIFF workflow matches independent 3×3 block sums to 1e-12.
- **Real data:** the set-A conservative method differs from the producer's bilinear `ET_fp` by ≤ 0.023 mm/day on 28 model-days (D-031).

### Full US-UTJ run (set B `etw`, 2024-06-05 to 2025-10-09, 34 min over the Drive mount)
- **Outputs:** `outputs/us_utj_set_b_etw_{extractions.parquet, exclusions.csv, report.txt, run.log}`. They are local and git-ignored.
- **Rows and errors:** 9,912 rows, 0 null values, 0 errors or warnings. The transfer is polygon overlap: all 15,625 footprint cells lie inside the model window, and row sums equal 1 to 12 decimals.
- **Footprint days:** 434 = 492 − 18 with no weight − 40 with negative weights. A further 3 round-off days were zeroed and reported.
- **Per-model footprint coverage:** 434 days for eemetric, ensemble and ssebop; 263 for disalexi, geesebal and ptjpl; 262 for SIMS.
- **Pixel methods:** 2,603 model-days.
- **Retained weight:** 1.000 for every model except SIMS (mean 0.782, minimum 0.206; cropland mask).
- **Domain capture** (from the CSV): minimum 0.859.
- **Compared with the Parquet (set A, bilinear; descriptive only):**
  - All 2,336 Parquet model-days are present.
  - Median per-model difference ≤ 0.014 mm/day; 95th percentile ≤ 0.043; maximum 0.105.
  - The 3 round-off dates exist only in set B.

### Limitations
- **Polygon area-weighted mode:** not implemented. No field polygons exist (contract §8), and it was left out rather than shipped untested. It is a planned step-5 extension.
- **Weighting method:** set B's is still unverified. Set B was not written by the current footprint code, since its `etw` files have negative weights and nodata 0 (D-009, D-027).
- **Domain-capture mapping:** code-inferred (D-030).
- **No retries or staging:** there are no I/O retries and no local staging of Drive files yet (step 18). A run reads about 2,600 rasters in about 34 min.
- **Common support varies by year:** it covers the models present each day (3 in 2024, 7 in 2025), and the `commonN:` support ID records N. Cross-model rankings must use step-7 cohorts.
- **Step 6 inputs:** the set-B `mean` layer is declared but not yet extracted.

**Next (at the time):** step 6. Superseded by the Step 6 entry above.

## Steps 2–4 — 2026-10-13

**Run the checks**
```bash
uv sync && uv run pytest                     # 100 passed, 1 skipped (Drive opt-in)
uv run ruff check src tests && uv run mypy   # clean
uv run --isolated --resolution lowest-direct --python 3.11 pytest   # oldest supported deps
```

### Step 2 — package skeleton
- `pyproject.toml` (hatchling build, `src` layout, `py.typed`), with `geo`/`plot` extras and a `dev` group. Dependency floors and the reasons for them are in D-018. `.gitignore` excludes private data (D-014). `README.md`.
- Infrastructure modules:
  - `errors` (`ContractError` and others)
  - `log` (structured events, JSON-lines formatter; the library never configures the root logger)
  - `validation` (`ValidationReport`)
  - `_optional` (deferred optional imports with an install hint)
  - `provenance` (sha256, package versions)
  - `registry` (models, site aliases)
- `config`: dataclasses for every mandatory declaration in contract §9, a strict YAML loader that rejects unknown keys, and error messages that name the config key. Example config: [examples/us_utj.yaml](examples/us_utj.yaml).
- **Gate verified:**
  - A subprocess import of every submodule, with sockets blocked and geo/plot packages blocked, succeeds and leaks no optional module.
  - The built wheel imports in an isolated environment.
  - Tests pass on the newest stack (pandas 3.0.6) and the oldest allowed one (pandas 2.2.0, numpy 1.26.0, pyarrow 14.0.0, Python 3.11).

### Step 3 — canonical tables
- [src/etcompare/schemas.py](src/etcompare/schemas.py) has 10 schemas: stations, station_daily, model_manifest, asset_sites, footprint_manifest, extractions, paired, metrics, intervals, exclusions.
  - Each schema declares column kinds, units, nullability, allowed values and a primary key. Counts are named (`n_sites`/`n_days`/`n_periods`); no table has a bare `n`.
  - `conform()` validates and coerces strictly. It rejects timezone-aware dates, times of day and type mismatches.
  - `check_primary_key()` runs before joins.
- [src/etcompare/io/tables.py](src/etcompare/io/tables.py) writes Parquet atomically. Metadata records the schema name and version, units, key and date semantics, plus provenance attributes. Dates are stored as `date32` (D-019).
- [src/etcompare/results.py](src/etcompare/results.py) provides `ExclusionLog` (stage, input key, reason code, severity) and `AdapterResult`.
- **Gate verified:**
  - Duplicate and null keys raise `DuplicateKeyError`.
  - Every schema round-trips through Parquet, as do the real US-UTJ tables, with identical frames. This covers nulls in every kind, identifiers with leading zeros, negative ET, extra columns, units and attributes.

### Step 4 — discovery and adapters
- [src/etcompare/adapters/filenames.py](src/etcompare/adapters/filenames.py) parses OpenET and footprint filenames.
  - OpenET names are anchored on `_<model>_<var>_YYYY_MM_DD_<hex>.tif` using the model registry. The prefix is parsed as an unordered set of site tokens, so explicit aliases containing `_` (e.g. `US_UTM`) work.
  - Rejected: invalid dates, unregistered models, wrong variable/suffix/extension, repeated sites. Each rejection has a specific message.
- [src/etcompare/io/manifest.py](src/etcompare/io/manifest.py) handles discovery and manifests.
  - Indexing uses `pathlib`; rasters are never opened (`scaling_source=not_read`). Each multi-site asset is one manifest row plus one `asset_sites` row per site.
  - Supports explicit manifests (CSV/Parquet) with an existence check. Footprint-set discovery is non-recursive by default (D-025).
  - Duplicate and conflicting assets raise an error, or are held out until `select_assets` records an explicit choice (D-021).
- [src/etcompare/adapters/station.py](src/etcompare/adapters/station.py): station daily table → `station_daily`.
  - Strict date, number and flag parsing. Reference provenance follows contract §4.3.
  - Inconsistent rows produce a warning plus an exclusion record. Reported as diagnostics: the closure relation, calendar gaps and negative values.
  - Source columns are kept as `src_*` (D-020).
- [src/etcompare/adapters/extracted.py](src/etcompare/adapters/extracted.py): pre-extracted table → long `extractions`.
  - Method labels are declared, and values are used as delivered.
  - Checks: model, site and date validity; key uniqueness; filename-versus-row consistency for both asset columns; the unity tolerance (warning); the retained-fraction range.
  - Sets `day_alignment` (D-023).
- **Gate verified (synthetic):** multi-site and single-site files, model and site aliases, malformed dates, duplicate assets in two folders, two assets for one site/model/day (error → report → explicit selection), non-recursive discovery, and the layer filter.

### Real-data verification (local only)
- [tests/integration/test_us_utj.py](tests/integration/test_us_utj.py) runs through `examples/us_utj.yaml`. It is skipped automatically when the files are absent.
- **Station:** reproduces sha256 and 492 rows. Provenance is 303 / 170 / 19 / 0, and `ET_corr = ET·ebc_cf` holds on 303/303. There are no calendar gaps, and all 44 source columns are preserved.
- **Extractions:** 4,672 long rows. Per-model coverage is 431×3 / 261×3 / 260. All 180 rows with retained weight < 0.90 are SIMS, the maximum |unity − 1| is ≤ 1.01e-8, and filenames are consistent in every row.
- **Across the two tables:** 61 station dates have no extraction; 260 dates are common to all models, of which 172 are `corrected_from_raw` and 88 `corrected_filled`.
- **Drive indexing (read-only listing, not a test):**
  - `OpenET_Daily_Tiffs/US-UTJ_US-CdM`: 4,747 rasters in 12 s, no parse failures or conflicts. Ranges: long-record models 2023-01-01–2025-12-31 (1,096 each); 2025-only models 365, SIMS 364.
  - Footprint set A: 492 daily + 17 monthly `etw` files, with set B excluded.
  - Whole `OpenET_Daily_Tiffs` root: 29,502 assets accepted. 1,617 conflicting site/model/day keys were held out in other site folders (D-021). 41,002 files were skipped as unparseable: `clipped/`, `archive_openet/`, and `castledale` (a region name, not a site ID).

### Limitations
- **flux-data-qaqc and fluxfootprints:** the adapters read *exported files* only. Direct object adapters, version-tested against a recorded upstream commit (guide step 4), are not implemented.
- **Raster headers** (CRS, scale, nodata) are not read. `io.rasters` and bounded raster access belong to step 5, so those manifest columns are null.
- **Footprint manifest** `domain_capture_frac` and `temporal_coverage_frac` are always null. The set-B domain-coverage CSV is not attached (contract §7).
- **No CLI yet** (step 17). Configuration is used through `load_config`.
- **Site tokens** must match the AmeriFlux pattern or an explicit alias, so region-named folders such as `castledale` need a separate mapping that has not been designed.
- **License and authors** are not set in `pyproject.toml`; this is the user's decision. The repository is not a git repo, so no commit was made.

**Next (at the time):** step 5. Superseded by the Step 5 entry above.

## Step 1 revision — 2026-10-13 (b)

The user supplied the timezone (UTC−7), `OpenET_Comparison.ipynb` (Parquet producer), the OpenET download notebook, and the footprint location `M:/My Drive/footprints`.

**Checks run**
- Recomputed `ET_fp`, `ET_mean` and `valid_weight_fraction` for ensemble 2024-06-06, geesebal 2025-09-12 and sims 2025-09-12 with the producer method on the set-A footprints and Drive rasters. All matched within 1e-13.
- Drive copy of the Parquet is byte-identical to the local file.
- Profiled all 492 set-A daily footprints. The 61 unextracted dates are 18 all-nodata and 43 negative-weight files, rejected by the producer.
- Compared footprint sets A (121×121, 2026-10-05) and B (`weighted/`, 125×125, 2026-10-12); the domain-coverage CSV belongs to set B.

**Resolved:** OpenET units (mm/day by request), reference ET (gridMET), clip polygon, extraction semantics, cause of the 61 missing dates, site-order variation in filenames.
**Still open:** the decisions.md D-015 questions and the footprint-set choice (D-016).

## Step 1 — 2026-10-13

**Delivered**
- [docs/input_contract.md](docs/input_contract.md): inventory, station and day-definition contract, station/extraction/raster/footprint semantics, mandatory-versus-optional fields and their validation errors, unresolved items with leads.
- [docs/decisions.md](docs/decisions.md): D-001 to D-015.
- [tools/profile_inputs.py](tools/profile_inputs.py): read-only profiler that regenerates the table facts.

**Checks run**
- SHA-256 of both example files matches PROJECT_CONTEXT.md; all counts there are reproduced (492/303/473/170/19, 2336 rows, 431/261/260 coverage, 260 common dates with 172 raw and 88 filled, 61 dates without extraction, 180 low-support rows).
- Verified relations: `ET_corr = ET·ebc_cf` (303/303), `ET_corr = ET_fill_val` on gap days (170/170), `ET_fill = gridMET_ETr·ETrF_filtered` (472/472), `ETrF = ET_corr/gridMET_ETr` (303/303), `ebc_cf = 1/ebr_corr` (458/458).
- Sampled 13 OpenET rasters on the Drive mount: one shared EPSG:4326 grid, float32, scale 1, NaN outside the export region plus −9999 nodata inside it.
- Sampled 3 US-CdM footprint rasters: EPSG:32612, 10 m, 121×121, weights summing to 1.

**New findings beyond PROJECT_CONTEXT.md**
- All 180 rows with `valid_weight_fraction < 0.90` are SIMS (cropland-only nodata mask).
- `daily_etw` footprints are NLDAS-ET-weighted in the current workflow code. *Superseded by revision (b): the footprints actually used came from an earlier version, so the weighting is unverified (D-009).*
- Gap-filled reference values are built from gridMET ETr (D-005).
- 2025-only models lack 2024 downloads; the 61 missing extraction days come from the footprint side (D-012).
- No local code writes the Parquet's exact schema; the producer is unconfirmed.

**Limitations**
- US-UTJ footprint rasters, audit files, OpenET version metadata and the Parquet producer were not available.
- The validation errors in contract §9 are specified, not implemented; they become code in steps 2–3.
- Real-data integration is limited to profiling; no performance results exist.

**Open questions:** decisions.md D-015.
