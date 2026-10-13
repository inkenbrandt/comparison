# Implementation status

Tracks progress against `openet_ec_library_ai_implementation_guide.md`. Newest entries first.

| Step | Status |
|---|---|
| 1 — Inspect inputs, freeze analysis contract | **Done** 2026-10-13 |
| 2 — Package skeleton | **Done** 2026-10-13 |
| 3 — Canonical tables and result objects | **Done** 2026-10-13 |
| 4 — File discovery and upstream adapters | **Done** 2026-10-13 (exported-file adapters; see limitations) |
| 5 — Spatial extraction | Not started (next; needs D-016 footprint-set choice) |
| 6–18 | Not started |

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

**Next:** step 5 (raster extraction). It needs the D-016 choice of footprint set and the `geo` extra.

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
