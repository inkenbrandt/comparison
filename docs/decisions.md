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

### D-009 — Footprint weighting method is unknown for set A (Superseded by D-036)
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
2. ~~What were the settings of the 2026-10-05 footprint run (set A): weighting variable, negative-ET handling?~~ NLDAS grass ETo, negatives kept (D-036).
3. ~~Which footprint set should re-extraction use?~~ Set B (D-016).
4. OpenET model versions and download dates, if recorded anywhere.

None of these block steps 2–4.

### D-016 — Footprint set for re-extraction: set B (Adopted 2026-10-13, user decision; primary replaced by D-037)
Set A (121×121, 2026-10-05) produced the Parquet. Set B (`weighted/` and `non_weighted/`, 125×125, 2026-10-12) is used for raster re-extraction. Its `etw` layer is the primary footprint; its `mean` layer is available for step-6 sensitivity. Pre-extracted comparisons stay tied to set A. Results from different sets are never mixed in one cohort; the `footprint_set_id` column keeps them apart.

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

---

**Revision 2026-10-13 (d):** Step 5 implemented with footprint set B. D-026 to D-031 added.

### D-026 — OpenET raster scaling is declared (Adopted)
The GeoTIFFs have no scale or offset tags; GDAL reports defaults of 1 and 0, which are not metadata. Products therefore declare `raster_scaling: declared` with scale 1, offset 0 and nodata −9999. This rests on the API request (`units: "mm"`, daily), float32 values of daily-ET magnitude, and the header nodata.
On every read, a header nodata that differs from the declared value is an error, so −9999 can never be read as ET. A product without a scaling declaration cannot be extracted from rasters.

### D-027 — Negative footprint weights: reject beyond round-off (Adopted; threshold configurable)
Set B `etw` has negative weights on the same 43 dates as set A, in 28–5,879 cells per file. The negative mass is up to 0.14 % of the positive mass: real negative contributions, not round-off.
Following the guide, a footprint is rejected for the day when its negative-to-positive mass ratio exceeds `negative_weight_tolerance` (default 1e-9). Smaller negatives are zeroed and reported. In the full set-B `etw` run:
- 40 days were rejected.
- 3 days (2024-12-22, 2025-04-03, 2025-05-05) had ratios of 5e-14 to 7e-11; they were zeroed and are listed in the exclusions file.
- 18 days had no weight.

That leaves **434 footprint days**. The producer, which rejected any negative value, kept 431 set-A days.
Raising the tolerance is a scientific choice: it must be set in configuration before scoring and is reported as an adjustment. The `mean` layer has no negative weights (474 usable days).

### D-028 — Conservative transfer onto the native model grid (Adopted)
Footprint contribution is moved onto the native OpenET pixels using exact area fractions; model ET is never resampled.
- Same-CRS, north-up grids use exact 1-D interval overlaps (numpy and scipy only).
- Other grid pairs project the model cell corners into the footprint CRS and intersect polygons with shapely. The only approximation is treating projected cell edges as straight.

Validation:
- The polygon path equals the exact path on aligned grids to 1e-12.
- A UTM → EPSG:4326 transfer agrees with 40×40 point sampling within the sampling error.
- On the real grids, every row sum equals 1 to 12 decimals.

This replaces the producer's bilinear WarpedVRT (D-006) for new extractions, under the method ID `footprint_conservative`.

### D-029 — Extraction method IDs and supports (Adopted)
| ID | Weights | Valid cells | Footprint set |
|---|---|---|---|
| `footprint_conservative` | footprint contribution per model cell | that model's finite cells | declared set |
| `footprint_conservative_common_support` | same | cells finite in **every model present that day** (support ID `commonN:…`; N varies because 2024 has 3 models) | declared set |
| `pixel_window_<n>x<n>` | equal, whole pixels | finite cells in the block; cells outside the raster count as missing | `none` |
| `nearest_tower_pixel` | the tower's pixel | — | `none` |

Retained fractions keep separate denominators:
- `fp_weight_retained_frac`: valid / footprint-grid total; for normalized weights the denominator is `normalized_truncated_footprint`.
- `fp_frac_in_model_extent`: inside the model raster / footprint-grid total.
- `fp_domain_capture_frac`: original domain capture, from the domain-coverage CSV.

They are never multiplied together, because their conditioning differs.

### D-030 — Domain-capture CSV attached to set B by layer (Provisional)
`et_weighted_coverage_pct` is attached to `etw` and `mean_coverage_pct` to `mean`, divided by 100. Values above 1 (11 days for `etw`) are kept as reported.
The mapping is code-inferred from the current fluxfootprints `summarize_periods`. Like the set-B rasters, the CSV may come from an earlier code version.
*Revisit when:* the 2026-10-12 run configuration is known.

### D-031 — Set-A conservative versus producer bilinear (Information)
On 2025-09-10 to 2025-09-14 (28 model-days; 2025-09-13 rejected for negative weights), conservative extraction on the same set-A footprints differs from the Parquet `ET_fp` by at most 0.023 mm/day (≤ 0.9 %). SIMS retained weight is 0.608 vs 0.611. This is the expected size of a method difference, with no sign of a unit or alignment error. It is not a reason to prefer either method.

---

**Revision 2026-10-14 (e):** Step 6 implemented. D-032 to D-035 added.

### D-032 — Temporal weighting is a controlled field, separate from the label (Adopted; US-UTJ `etw` assignment superseded by D-036)
`FootprintSetSpec.temporal_weighting` takes one of `time_mean`, `reference_et_weighted`, `modeled_et_weighted` (e.g. NLDAS), `measured_et_weighted` or `unknown` (the default). The free-text `weighting_method` label stays as written, so an unverified label never becomes a claim. Target dependence follows from the field: `measured_et_weighted` → `yes`, `unknown` → `unknown`, anything else → `no`.

US-UTJ assignments:
- **Set B `mean`: `time_mean`.** fluxfootprints `summarize_periods` (424ec20, code-inferred for the 2026-10-12 run) builds it as the plain mean over timesteps with positive mass. It uses no ET, so it is target-independent whichever code version wrote it.
- **Sets A and B `etw`: `unknown`.** The same function weights by `LE/680.6` (measured LE) by default, without clipping negative values, which is consistent with the negative weights found in both sets (D-027). The current `footprint_workflow.py`, by contrast, passes clipped `nldas_et`. Which ET variable the 2026-10-05 and 2026-10-12 runs used is not recorded. These sets may therefore be measured-ET weighted, so every comparison that uses them carries the flag `footprint_target_dependence=unknown` and is a sensitivity experiment.

*Revisit when:* the run configurations are known (D-015 q2).

### D-033 — Per-timestep aggregation is declared in full (Adopted)
`W_i = Σ_t a_t f_it / Σ_t a_t` over the eligible timesteps, with `a_t = s_t·d_t` if `include_duration` is set and `s_t` otherwise. `TemporalAggregationSpec` has no defaults for these choices:
- the temporal weighting;
- `include_duration`;
- the basis: `original_contribution`, which keeps capture, or `renormalized_per_timestep`, which divides each timestep by its own total;
- the negative-weight policy: `exclude_timestep`, `reject_period` or `keep`;
- the gap policy: missing weight, no footprint mass or negative cells;
- the night policy: excluding night requires a daytime flag;
- an optional minimum temporal coverage.

Stated rules:
- Timesteps are assigned to days by interval: an end-labelled stamp belongs to the day of `stamp − d_t`.
- `temporal_coverage_frac` = eligible duration / period duration.
- Original-basis domain capture = `Σ a_t cap_t / Σ a_t`.
- Per-timestep `normalized_cell_weight` input cannot use the original basis.

Upstream `mean` corresponds to `time_mean`, no duration, original basis, gaps excluded. Upstream `etw` corresponds to `measured_et_weighted`, `keep`, no duration. Both reproduce the upstream formulas to 1e-13 (test). The exported upstream rasters are then renormalized, so neither those rasters nor the domain CSV can be recovered from a daily file.

No per-timestep footprints exist for US-UTJ, so no aggregation was run on real data. Without inputs the result is `not_available`.

### D-034 — Weighting comparisons use frozen keys and per-variant cohorts (Adopted)
`compare_footprint_sets` keeps a site/date/model/method key only when every compared set has a usable value there. An optional `min_retained_frac` is applied identically to every set. Each reference variant then gets its own cohort, restricted to days with that reference value, plus a model-only `none` cohort. Each modeled value stays in a row with its observation (`paired` schema v2, which adds the key `footprint_set_id`). `common_models` optionally also requires every model present.

The outputs are descriptive set-minus-baseline differences of modeled ET only. Station agreement is scored in step 9, and no set is preferred because it agrees better.

### D-035 — US-UTJ set B: `mean` minus `etw` (Information)
The comparison uses `footprint_conservative` on 434 frozen days. These are the `etw` days: the 40 days where `etw` was rejected for negative weights exist only in `mean` and were dropped from both sides.
- **Per-model mean difference:** −0.046 to −0.001 mm/day (−1.8 % to −0.03 % of the `etw` mean).
- **95th-percentile |difference|:** ≤ 0.24 mm/day.
- **Maximum |difference|:** 0.48 mm/day (eemetric).
- **Under `footprint_conservative_common_support`:** mean differences are −0.019 to +0.002 mm/day, and the 95th percentile is ≤ 0.16 mm/day.

The time-mean footprint gives slightly lower ET for every model except geeSEBAL and PT-JPL on common support. Two possible causes: night and low-ET footprints extend over different surfaces, or the cell masks differ. Model–station agreement has not been computed yet (step 9), so the size of this effect relative to model error is not yet known.

This is a sensitivity result, not evidence for either weighting (D-032).

---

**Revision 2026-10-14 (f):** NLDAS-ETo weighting adopted as primary, with measured-LE weighting as a sensitivity set (user decision). D-036 to D-038 added. D-009, D-016, D-027 and D-032 are partly superseded, and D-015 q2 is resolved.

### D-036 — The existing `etw` sets are NLDAS-grass-ETo weighted (Adopted; verified by reproduction)
A per-timestep footprint stack was rebuilt for US-UTJ with the production inputs and settings (D-037). Aggregating it reproduces both footprint sets A and B `etw`:
- NLDAS-ETo weighting vs set A: median relative L1 difference 0.2 %, maximum 0.7 %. Total-variation distance: median 0.0011 (90th percentile 0.0026) to both sets A and B `etw`.
- Measured-LE weighting is about 40× farther (median 0.045) and is never the closer of the two on any day.
- Keeping negative ETo, as `summarize_periods` in footprint_workflow_v2-1.ipynb does, produces negative weights on all 43 negative-weight days of set A. It also produces tiny negatives on 3 more days, which match set B's 3 round-off days (D-027).

Consequences:
- **Weighting:** the `etw` sets are `temporal_weighting: reference_et_weighted`, with target dependence `no`. This supersedes D-009 and D-032 for those sets.
- **"NLDAS ET":** in fluxfootprints this means hourly FAO-56 grass ETo from NLDAS-2 forcing (`eto_grass`), not land-model ET.
- **Negative weights:** their cause is negative hourly ETo (negative net radiation) left unclipped. They do not come from measured LE.
- D-015 question 2 is resolved.

### D-037 — Primary and sensitivity footprints from one stack (Adopted 2026-10-14, user decision)
**Primary** is `US-UTJ_nldas_eto_20261014`, NLDAS-ETo weighted. **Sensitivity** is `US-UTJ_measured_le_20261014`, measured-LE weighted. Both are aggregated by etcompare from the same half-hourly fluxfootprints stack, so they differ only in the temporal weight `s_t`. This replaces set B `etw` as the primary footprint (D-016).

The stack was built upstream by `tools/upstream/run_timestep_footprints.py`, with inputs and settings matching footprint_workflow_v2-1.ipynb:
- **Half-hourly data:** `database_exports_20260825/US-UTJ_metflux_qc.parquet`. Interval start = `DATETIME_END − 30 min`, in UTC−7.
- **Heights:** `groundwater.daily_footprint_parameters` (primary zone E, alfalfa), fetched read-only on 2026-10-14. zm is `effective_measurement_height` and z0 is `roughness_length`, forward-filled. They match `compute_aerodynamic_params(veg_type='alfalfa')` to 5e-4 m.
- **Tower location:** from `station_attribute_view`, 37.281746 / −109.53489. The resulting grid is identical to set A's. The station's coordinates in the config now use these values; AmeriFlux's 4-decimal values are about 9 m off.
- **Model settings:**
  - ffp_xr, h = 2000 m, 10 m cells, domain ±600 m, `rslayer=False`, smoothing on.
  - Only half-hours starting 08:00–20:30 are used (Volk 2023).
  - The model drops some timesteps itself; 11,295 remain.
- **NLDAS:** `eto_grass` interpolated linearly from hourly start labels to 30 min. The radiation timing differs from station SW_IN by a steady ≈0.5 h with no seasonal shift. Neither series uses daylight time, but NLDAS's start-versus-end labelling of hourly averages is unconfirmed.
- **Aggregation, identical for both sets** (`footprint_aggregations` in the config):
  - Durations included (all 30 min).
  - Each timestep renormalized, as production does.
  - Negative weights and missing weights: timestep excluded.
  - Night handling: `include`, because the 08–20 h window already excludes night.

  Production kept negative ETo. The exclusion here is a declared change that removes negative footprint weights.
- **LE:** QC'd `LE_1_1_1` in W m⁻² is used unchanged. It is proportional to ET up to the small variation of λ with temperature.

Coverage:
- **NLDAS:** all 474 days with footprints (median 23 eligible half-hours).
- **Measured LE:** 391 days (median 21). On 83 days every LE half-hour is missing.

The measured-LE set uses the target measurements, so comparisons with it are sensitivity experiments (D-032).

### D-038 — US-UTJ: NLDAS primary vs measured-LE and vs set B `etw` (Information)
Both comparisons use `footprint_conservative` on frozen days.

**NLDAS minus set B `etw`** (434 frozen days):
- Per-model mean differences −0.001 to 0.000 mm/day; maximum |difference| 0.005 mm/day.
- The new primary set therefore reproduces the production footprints. It also adds the 40 days that set B lost to negative weights, giving 474 footprint days and 2,549 rows per method.

**Measured LE minus NLDAS** (391 frozen days):
- Per-model mean differences +0.002 to +0.015 mm/day (≤ 0.7 % of the NLDAS mean). For `et_corrected_measured` (303 days) they are +0.002 to +0.020 mm/day.
- 95th-percentile |difference| ≤ 0.15 mm/day.
- The largest differences fall on days with few usable LE half-hours: 1.12 mm/day on 2024-08-06, which has 2 LE half-hours against 25 for NLDAS. 7 LE days have fewer than 6.
- No minimum temporal coverage is applied. Choosing one is a scientific decision that must be made before scoring, not tuned on agreement.

Both are descriptive differences of modeled ET; station agreement is step 9.
