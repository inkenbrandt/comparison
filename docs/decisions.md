# Decision log

Each entry records a choice, why it was made, and what would change it. Status is **Adopted** (binding), **Provisional** (used until the named evidence arrives), or **Open** (needs the user). Evidence is in [input_contract.md](input_contract.md).

No decision here was chosen because it improves model–station agreement. Quality thresholds, filters and weighting choices are fixed in configuration before scoring.

**Revision 2026-10-13 (b):** The user supplied the timezone (UTC−7), the producer and download notebooks, and the footprint location. As a result:
- D-002, D-003 and D-011 were updated.
- D-006 to D-008 were changed from code-inferred to verified by reproduction.
- D-009 was revised, because set A was not written by the current footprint code.
- D-012 now records why the 61 dates are missing.
- D-016 and D-017 were added.

---

### D-001 — Pre-extracted tables first, rasters second (Adopted, 2026-10-13)
The first working path uses the station CSV and the extracted-OpenET Parquet. Raster re-extraction (guide step 5) is a separate workflow, and its results carry a different extraction-method ID.

### D-002 — Day definition is declared, never inferred (Adopted; US-UTJ values Provisional)
Stations declare `timezone`, `day_definition` and `interval_label`; products declare `day_definition` or `unknown`. Joins use date labels only, and no date is shifted. Products with `unknown` day definitions are flagged `day_alignment=unverified`.
US-UTJ: `timezone: UTC-07:00` (user-provided), `day_definition: local_standard_time`, `interval_label: start` (from `.ini` `TIMESTAMP_START`; unconfirmed). OpenET: `unknown`.

### D-003 — ET units are mm/day (Adopted for OpenET; Provisional for station)
OpenET was requested with `units: "mm"` and `interval: "daily"`. Station ET is supported by the LE/ET ratio and flux-data-qaqc's unit labels. Neither input is converted.

### D-004 — Reference variants and provenance (Adopted)
| Variant ID | Source | Rows |
|---|---|---|
| `et_raw` | `ET` | `raw_available` |
| `et_corrected_measured` | `ET_corr` | `corrected_from_raw` only |
| `et_corrected_inclusive` | `ET_corr` | `corrected_from_raw` ∪ `corrected_filled`, with provenance per row |

`ET_fill` is never a reference. Closure sensitivity (raw vs corrected) uses only `corrected_from_raw` days.

### D-005 — Gap-filled reference days are gridMET-dependent (Adopted)
Filled `ET_corr = gridMET_ETr × ETrF_filtered`, and the OpenET rasters were requested with `reference_et: gridMET`. Results on `corrected_filled` days are always reported separately and never called independent validation.

### D-006 — `ET_fp` is used as delivered (Adopted; verified)
The label is `upstream_footprint_weighted` (bilinear WarpedVRT of 30 m OpenET onto the 10 m footprint grid, footprint set A), and the values are never re-weighted. Reproduction matched to 1e-13. A conservative step-5 re-extraction gets its own method ID, and the two are compared on frozen dates.

### D-007 — `ET_mean` is `source_region_mean`, a varying region (Adopted; verified)
It is the unweighted mean over the footprint-positive cells of the daily footprint grid, so the region changes from day to day. It is never described as a field, window or polygon mean. A fixed-region comparison needs re-extraction.

### D-008 — `valid_weight_fraction` is post-truncation retained weight (Adopted; verified)
It is stored as `fp_weight_retained_frac` with denominator `normalized_truncated_footprint` and never reported as capture. No support threshold is adopted. All 180 rows below 0.90 are SIMS (cropland-only mask); any threshold must be set in configuration and applied to every model.

### D-009 — Footprint weighting method is unknown for set A (Provisional)
The current `footprint_workflow.py` weights by `nldas_et` and excludes negative ET. Set A contains 43 files with negative weights, so an earlier version produced it. Set A therefore gets `weighting_method: et_weighted_unverified`. It is not labelled measured-ET-weighted or NLDAS-weighted until that run's configuration is known.
*Revisit when:* the 2026-10-05 run's settings are known, or set B is adopted (D-016).

### D-010 — Missing raster values (Adopted)
NaN and the declared nodata value are both missing. OpenET zeros are valid. Footprint rasters declare nodata = 0, so zero-weight cells read as missing, which has no effect because zero weights contribute nothing. Valid negative ET is never clipped.

### D-011 — OpenET temporal support is `interpolated_daily`; acquisitions unknown (Adopted)
The request used `interval: "daily"` and every calendar day exists. Without per-pixel acquisition flags, clear-versus-interpolated analyses report `not_available`. Scene-level Landsat cloud cover may be added later as a labelled scene diagnostic, never as a pixel-clear flag.

### D-012 — Coverage gaps have known causes (Adopted)
- DisALEXI, geeSEBAL, PT-JPL and SIMS were not downloaded before 2025.
- 61 station dates lack extraction because the producer rejected those set-A footprints: 18 all-nodata, 43 with negative weights.
- Rankings use the all-model common cohort (260 dates before reference filtering), and per-model coverage is reported separately.

### D-013 — Multi-site filenames are parsed as sets (Adopted)
Prefix order varies because the download notebook prepends `REGION_NAME`, which was spelled both ways. The parser anchors on `_<model>_et_YYYY_MM_DD_<hex>.tif` and treats the prefix as an unordered set.

### D-014 — Private data stays local (Adopted)
Station data, Parquet files, notebooks and rasters are local integration inputs. Tests use synthetic fixtures. When the repository is initialised, these go in `.gitignore`.

### D-015 — Remaining questions (Open)
1. Is the half-hourly station timestamp the start or the end of the interval? (Assumed start.)
2. What were the settings of the 2026-10-05 footprint run (set A): weighting variable, negative-ET handling?
3. Which footprint set should re-extraction use? (D-016)
4. OpenET model versions and download dates, if recorded anywhere.

None of these block steps 2–4.

### D-016 — Footprint set for re-extraction (Open)
Set A (121×121, 2026-10-05) produced the Parquet. Set B (`weighted/`, 125×125, 2026-10-12) is newer, has a matching domain-coverage file and a `daily_mean` companion. Pre-extracted comparisons stay tied to set A. Raster re-extraction (step 5) needs the user to choose a set; results from the two sets are never mixed in one cohort.

### D-017 — Existing comparison outputs are not reused (Adopted)
The `comparison_results*` files from `OpenET_Comparison.ipynb` cell 22 are not inputs or baselines:
- They fit OLS with tower ET as the response.
- They turn zeros into missing values.
- They select rows per model rather than through a common cohort.
- They have no record of the cohort or the filters applied.

Using them would conflict with guide steps 7 and 9.

---

**Revision 2026-10-13 (c):** Steps 2–4 implemented. D-018 to D-025 record the design choices they required.

### D-018 — Python and dependency floors (Adopted)
`requires-python >= 3.11`. Core: numpy ≥ 1.26, pandas ≥ 2.2, pyarrow ≥ 14, scipy ≥ 1.11, pyyaml ≥ 6.0.1. Optional extras: `geo` (rasterio, pyproj, shapely, geopandas) and `plot` (matplotlib). These are lower bounds, not pins. Both ends were tested on 2026-10-13: Python 3.11 with pandas 2.2.0 / numpy 1.26.0 / pyarrow 14.0.0, and Python 3.13 with pandas 3.0.6 / numpy 2.5.4 / pyarrow 26.0.0. Python 3.10 was excluded because it reaches end of life in October 2026.
*Revisit when:* a later step needs a newer API.

### D-019 — Station-day dates in memory and on disk (Adopted)
In memory, dates are naive `datetime64[ns]` at midnight. In Parquet they are Arrow `date32`, so the file records a calendar label rather than an instant, and the metadata names them `station_day_label`. Timezone-aware values and values with a time of day are rejected, never truncated or shifted.

### D-020 — Source columns preserved with prefix `src_` (Adopted)
`station_daily` keeps every source column unchanged as `src_<name>` next to the canonical columns. For example, `src_date` is the original text. The prefix avoids collisions such as the source `date` column.

### D-021 — Duplicate and conflicting raster assets (Adopted)
There are two kinds:
- `duplicate_asset`: the same filename stem at two paths.
- `conflicting_assets`: two stems for the same site, model and day.

Both raise an error by default. In `report` mode the candidates are held out of the manifest until `select_assets` records an explicit choice. There is no "latest file" or averaging heuristic, and mosaicking is deferred to the raster workflow.
Evidence: indexing all of `M:/My Drive/OpenET_Daily_Tiffs` found 1,617 conflicting site/model/day keys in the `US-UTB`, `US-UTE` and `US-UTL_US-UTV` folders. These are repeat downloads with different API suffixes, up to 4 per day. The US-UTJ folder has none.

### D-022 — No ET unit conversion (Adopted)
Only spellings of mm/day are accepted (`mm/day`, `mm d-1`, …). A bare `mm` is rejected because it does not state the period. Any other unit is rejected with an instruction to convert upstream.

### D-023 — Meaning of `day_alignment` (Adopted)
`declared` means the station is declared and the product's `day_definition` is not `unknown`. It records that both sides were declared, not that they were shown to agree. Everything else is `unverified`. All US-UTJ extraction rows are `unverified` (D-002).

### D-024 — Raw-only rows are `inconsistent_flags` (Provisional)
Following contract §4.3 strictly, a row with raw ET, no corrected ET and `ET_gap=False` (for example, a missing closure factor) is classed `inconsistent_flags`. It produces a warning and an exclusion record. US-UTJ has no such rows.
*Revisit when:* a station legitimately has raw-only days that should feed the `et_raw` variant.

### D-025 — Footprint manifests are keyed by set (Adopted)
Footprint discovery does not recurse by default. Its key is `(footprint_set_id, asset_id)`, because sets A and B share filename stems (D-016). The filename layer token (`etw`, `mean`) is stored as text and is never taken as the weighting method (D-009).
