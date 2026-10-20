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
  - The model drops some timesteps itself; 10,795 remain (of 12,260 in the window). *Corrected 2026-10-15: this line said 11,295; the rerun reproduced every other figure of D-036 to D-038 exactly (D-043).*
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

---

**Revision 2026-10-14 (g):** Step 7 implemented. D-039 and D-040 added.

### D-039 — Cohort rules (Adopted)
Every comparison is declared as a `ComparisonSpec` (`comparisons:` in the config) and built by `etcompare.cohorts.build_cohort` before any scoring.
- **Candidate unit:** a site/date/model. Candidates are every site/date in the selected extractions or in the station table, crossed with every declared model, so a model with no row on a day is recorded as failing, never silently absent.
- **Own eligibility:** a usable modeled value on every declared side (extraction method + footprint set) and an observation for every declared reference variant (D-004).
- **Kinds:**

  | Kind | Days frozen across | Ranking scope |
  |---|---|---|
  | `common_date` | all declared models (≥ 2) | `all_models` |
  | `pairwise_model` | exactly two models | `pairwise` |
  | `model_available` | nothing; one cohort per model; one side and one variant | `none` (coverage only) |
  | `paired_method` | ≥ 2 sides, per model (all models with `common_models`) | `none` |
  | `paired_reference` | ≥ 2 reference variants, per model (all models with `common_models`) | `none` |

  Every kind also freezes across all of its declared sides and variants. A pairwise cohort may use different days from the all-model cohort; `ranking_scope` records which rankings a cohort can support, so step 11 cannot pool them.
- **Quality rule:** `min_retained_frac` is applied before scoring, identically to every model and footprint side. It does not apply to sides with footprint set `none`. A footprint row with an unknown retained fraction fails as `retained_frac_unknown`. No threshold is adopted for US-UTJ (D-008).
- **Reasons** (`cohort_membership.reason_code`): `extraction_missing`, `modeled_value_null`, `below_min_retained_frac`, `retained_frac_unknown`, `reference_unavailable`, `not_all_models`. The first failing cell gives the code; `detail` lists every failing side or variant, or the ineligible models.
- **Joins:** primary keys are checked on the selected rows before joining, and every merge is validated (`one_to_one` / `many_to_one`).
- **Paired rows** contain no missing values; metrics must use the cohort as the common mask and never drop values per metric.
- **Flags:** `day_alignment=unverified` and `footprint_target_dependence=yes|unknown`. A footprint set whose weighting is not supplied counts as `unknown`.
- **Immutability:** a `Cohort` is a frozen object whose accessors return copies. `membership_sha256` (the fingerprint) hashes the included units, so later stages can check they share a cohort.

`compare_footprint_sets` (step 6) keeps its own implementation. A test shows that `build_cohort` with `paired_method` over two footprint sets gives identical paired rows.

### D-040 — US-UTJ cohort sizes (Information)
From `tools/run_cohorts.py examples/us_utj.yaml`. Primary sides are `footprint_conservative` on `US-UTJ_nldas_eto_20261014`.

| Comparison | Kind | Days |
|---|---|---|
| `utj_rank_corrected_inclusive` | common_date, 7 models | 281 (93 gap-filled reference) |
| `utj_rank_corrected_measured` | common_date, 7 models | 188 |
| `utj_closure_all_models` | paired_reference raw/corrected_measured, all models | 188 |
| `utj_coverage` | model_available | eemetric/ensemble/ssebop 472; disalexi/geesebal/ptjpl 282; sims 281 |
| `utj_footprint_vs_window` | paired_method vs `pixel_window_3x3`, per model | 303 long-record models; 188 for 2025-only models |
| `utj_pair_eemetric_ssebop` | pairwise_model | 303 |
| `utj_set_a_common` | common_date, pre-extracted set A | 260 (88 gap-filled): matches PROJECT_CONTEXT |

The primary common cohort contains all 260 set-A days plus 21 more (281). On every one of those 21 days the producer rejected the set-A footprint. Set B `etw` also lacks 19 of them; the other 2 (2025-04-03, 2025-05-05) are set-B round-off days (D-027). The NLDAS footprints exist on all 21 (D-038). The all-model cohorts are limited to 2025 by the four 2025-only models (D-012). Every cohort that spans both years is single-model or pairwise.

---

**Revision 2026-10-15 (h):** Step 8 implemented. D-041 added.

### D-041 — Period totals and completeness (Adopted)
`etcompare.aggregation.aggregate_cohort(cohort, spec, products=)` sums a cohort's daily pairs into the periods of a `PeriodAggregationSpec` (`period_aggregations:` in the config). The output is a `period_totals` table.
- **Sums, not means.** Daily ET is depth per station day (`mm d-1`); a period total is the sum of daily depths, in `mm`. Daily footprint-extracted values are summed. Extracting a monthly raster with a monthly footprint is a different method and is not produced (cohorts use daily rows only).
- **Periods:** `day`, `month`, `year`, `water_year`, `growing_season`.
  - Water years start in `water_year_start_month` (default 10, the USGS convention) and are labelled by the year they end in: `WY2025` = 2024-10-01 to 2025-09-30.
  - Growing seasons have no default. They are either site/year windows (site metadata) or a month-day rule. A rule whose end precedes its start crosses the year boundary and is labelled `GS<y>-<y+1>`. `02-29` is refused in rules because it does not occur every year. Days outside every season are left out of the growing-season product.
- **Calendar denominators.** `n_days_expected` counts every calendar day of the period, including leap days (February 2024 = 29, WY2024 = 366). Whole periods are listed even where they extend past the inputs, and those rows are flagged `period_outside_inputs`. A day missing from both station and model is still expected. A period with no matched days is reported with null totals and `validity_reason=no_matched_days`.
- **Station-day boundaries.** Days are assigned to periods by their station-day label, and no date is shifted (D-002). Daily flags such as `day_alignment=unverified` carry into the period row, because a product with an unknown day boundary may be offset by part of a day at period edges.
- **Three products, one row each:**

  | Product | Value | Null when |
  |---|---|---|
  | `partial_sum` | sum over the cohort's matched days, labelled with `n_days_matched` | no matched days |
  | `complete_total` | the same sum, only when `n_days_matched = n_days_expected` | any expected day is not a cohort day (`incomplete_period`), or support is not `interpolated_daily` |
  | `estimated_total` | only if declared; `mean_daily_scaling` = partial × expected / matched, for both sides | completeness below the declared `estimation_min_completeness`, no matched days, or unsupported temporal support |

  Partial sums are never scaled by default. An estimated total is labelled with its `estimation_method` and is never an observed total. No estimator is adopted for US-UTJ.
- **Accepted days** are the cohort's days. A gap-filled reference day counts toward a complete total only in a variant that accepts it (`et_corrected_inclusive`), and such days are counted in `n_days_matched_reference_filled`. Because cohorts freeze days across models, sides and variants, every model in a frozen cohort has the same completeness.
- **Temporal support.** Complete and estimated totals need `interpolated_daily`. `overpass_only` cannot support a full-period total, and `unknown` cannot show that it does, so both give `validity_reason=temporal_support_<value>`; partial sums remain. A model with no product declaration raises `ContractError`. Mixed support within a comparison gives a `mixed_temporal_support` warning, and every row carries `temporal_support`.
- **Counts per row:** `n_days_expected`, `n_days_matched`, `n_days_matched_reference_filled`, `n_days_model_available` (non-null model value on that side, before cohort rules), `n_days_reference_measured` and `n_days_reference_filled` (reference values of that variant), and `period_completeness` = matched / expected. Availability counts come from the cohort (`Cohort.model_availability()` / `reference_availability()`), so they cover the comparison's sites and declared period.
- **Provenance:** the table attributes carry the cohort fingerprint (`cohort_membership_sha256`), the aggregation spec and each model's temporal support.

---

**Revision 2026-10-15 (i):** User decisions: estimated totals are wanted, with missing values imputed into a new column, and the growing season comes from the crop-height model. D-042 added; D-041 extended.

### D-042 — Imputed estimated totals and crop-height growing seasons (Adopted 2026-10-15, user decision)
**Growing season.** The season is the crop-height model's declared `season_start`..`season_end` (inclusive), read from `crop_height_tables` by `etcompare.adapters.crop_height`. US-UTJ uses `US-UTJ_20240101_20251230.csv` (zone E, alfalfa; SHA-256 `ab8c3cc2…d1a48`):
- GS2024 = 2024-03-15..2024-12-06 and GS2025 = 2025-03-15..2025-12-06, 267 days each.
- In this file, `active_k` (growth rate) is non-null exactly inside the season, and height is 5 cm (the base) on every day outside it. Height resets show three cuttings each year: 2024-06-11, 07-19 and 09-08; 2025-05-28, 07-05 and 09-07.
- The adapter refuses a table whose active column disagrees with the season, whose season changes within a year, or whose season crosses a calendar year. No height threshold is applied: the crop model's own season is used.

**Imputation into a new column.** The estimators `linear_interpolation` and `reference_et_fraction` write a `daily_estimates` table (`PeriodTotals.daily_estimates`):
- `observed_mm` / `modeled_mm` keep the cohort values. Imputed values appear only in `observed_estimated_mm` / `modeled_estimated_mm`, and `value_source` = `cohort` / `imputed` / `not_imputed`.
- `estimated_total` sums the estimated columns and is withheld (`imputation_incomplete`) unless every expected day has a value. It reports `n_days_imputed`.
- **Paired imputation:** anchors are cohort days only. Every non-cohort day is imputed on both sides, even where one side has a real value. Every model of a frozen cohort therefore has the same real and imputed days, so imputation cannot hand an advantage to a model with missing difficult days (D-039). Real values outside the cohort are deliberately not used.
- **Bounded:** a gap is filled only if it is bracketed by anchors (which may lie outside the period) and is at most `estimation_max_gap_days` long. Nothing is extrapolated.
- **US-UTJ method: `reference_et_fraction`** with gridMET ETr (`station_tables[].reference_et_column: gridMET_ETr`). ET/ETr is interpolated linearly and multiplied by the day's ETr. This is the relation the station's own gap-filling uses (`ET_fill = gridMET_ETr · ETrF_filtered`, D-005), applied identically to the model. Anchors need ETr > 0; a day without ETr stays `not_imputed`.
- **Caveats:** imputed observed and modeled values share the ETr driver and the interpolation, so imputed days are not independent evidence. Interpolating the fraction across a cutting misses the post-cut drop; the gap limit bounds this.
- **Parameters:** `estimation_max_gap_days: 14` and `estimation_min_completeness: 0.5`, adopted as defaults by the user on 2026-10-15, before any scoring. They were not tuned on agreement.

`mean_daily_scaling` remains available but is not used for US-UTJ.

---

**Revision 2026-10-15 (j):** Steps 5–6 outputs regenerated; D-043 added; D-037 timestep count corrected.

### D-043 — Regenerated US-UTJ outputs and verified footprint writes (Information; one Adopted rule)
The step 5–6 outputs were missing from this checkout and were rebuilt with the commands in `IMPLEMENTATION_STATUS.md`:
- **Database fetch:** read-only, 731 parameter rows.
- **Per-timestep footprints:** 10,795 timesteps.
- **Aggregation, then four raster extractions and both footprint comparisons.**

The footprints environment was missing its declared `pyarrow` dependency. Version 26.0.0 was installed with `--link-mode=copy` (OneDrive refuses uv's hard links). The footprints repository's code was not changed.

**Corrupted writes (resolved):** in the first rebuild, the same three daily footprints in both etcompare-built sets were written with garbage values (around ±1e300): 2024-12-05, 2025-06-20 and 2025-06-21.
- The in-memory aggregates were correct: finite, non-negative and summing to 1.
- A single rewrite was correct, inside and outside OneDrive, so the damage happened between the array and the file in that run. A OneDrive-synced output folder is the likely cause, but this was not proven.
- The extraction caught the files as negative-weight footprints; they were not used silently.
- **Rule (Adopted):** `io.rasters.write_footprint` now reads every file back and requires it to be bit-identical to the array. Otherwise it removes the file and raises `OSError`. A test corrupts a file after the rename to check this.
- The rewrite passed this check and an independent scan of all 474 + 391 files.

**Reproduction:** after the rewrite every recorded figure is reproduced exactly.
- NLDAS set: 474 days and 2,549 rows per footprint method. Total-variation distance to set A: median 0.0011 (90th percentile 0.0026). Measured-LE set: 391 days, distance 0.0452, never the closer set (D-036 to D-038).
- Measured LE − NLDAS: +0.002 to +0.015 mm/day, maximum 1.12.
- Set B `etw`: 9,912 rows and 434 footprint days. Set B `mean` − `etw`: 4,706 of 5,098 keys, −0.046 to −0.001 mm/day, maximum 0.48 (D-035).
- All seven cohorts match D-040.

**Period totals** (`utj_periods`; complete totals need every calendar day, estimated totals impute gaps of ≤ 14 days):

| Comparison | Complete months | Estimated months | Longer periods |
|---|---|---|---|
| `utj_rank_corrected_inclusive` (7 models) | 2025-01 to 07, 2025-09 | 2025-01 to 09 (≤ 1 imputed day) | none |
| `utj_rank_corrected_measured` / `utj_closure_all_models` | 2025-09 | 2025-01 to 03, 09 | none |
| `utj_footprint_vs_window` / `utj_pair_eemetric_ssebop` | 2025-09 | 2024-11 to 2025-03, 2025-09 (≤ 10 imputed days) | none |
| `utj_coverage` (per model; not a ranking) | 2024-07 to 2025-09 | the same | WY2025 complete |
| `utj_set_a_common` | none | 2025-02 to 09 | none |

Measured-reference cohorts have no estimates for 2025-04 to 08 because raw station ET is missing for long runs (all of May 2025), well beyond 14 days. GS2025 and calendar 2025 cannot be complete or estimated, because the record ends on 2025-10-09, before the season end (12-06). 2024 periods start mid-year, on 2024-06-05.

---

**Revision 2026-10-15 (k):** Step 9 implemented. D-044 and D-045 added.

### D-044 — Performance metrics (Adopted; US-UTJ tolerances Provisional)
`etcompare.metrics.score_comparison(cohort, metric_spec, products=, period_totals=)` scores a cohort under a `MetricSpec` (`metric_sets:` in the config). The output is a `metrics` table (schema v2). `compute_metrics(O, P, tolerance=, metrics=)` is the core for one sample.

**Definitions.** `P` is modeled ET, `O` is station ET, `e = P − O`, `dX = X − mean(X)`, and `std` is the population standard deviation (`ddof=0`; it cancels in every ratio).

| ID | Definition | Units | Role |
|---|---|---|---|
| `mbe` | `mean(e)` | value | score |
| `mae` | `mean(abs(e))` | value | score |
| `rmse` | `sqrt(mean(e²))` | value | score |
| `bias_corrected_rmse` | `sqrt(mean((e − mean(e))²))` | value | diagnostic |
| `percent_bias` | `100·Σe / ΣO` | % | score |
| `nrmse_mean` | `RMSE / mean(O)` | 1 (fraction) | score |
| `nrmse_mean_percent` (optional) | `100·RMSE / mean(O)` | % | score |
| `pearson_r` | `ΣdO·dP / sqrt(ΣdO²·ΣdP²)` | 1 | score |
| `pearson_r_squared` | `pearson_r²` | 1 | score |
| `ols_slope`, `ols_intercept` | OLS of P on O: `ΣdO·dP / ΣdO²`; `mean(P) − slope·mean(O)` | 1; value | descriptive |
| `nse` (optional) | `1 − Σe² / ΣdO²` | 1 | score |
| `kge_2009` (optional) | `1 − sqrt((r−1)² + (α−1)² + (β−1)²)`, emitted with `kge_2009_r` (= `pearson_r`), `kge_2009_alpha` (= `std(P)/std(O)`), `kge_2009_beta` (= `mean(P)/mean(O)`) | 1 | score; the three are components |
| `mape` (optional) | `100·mean(abs(e)/abs(O))` | % | diagnostic |

"value" units are those of the sample: `mm d-1` for daily pairs, `mm` for period totals.
- **KGE:** only the original Gupta et al. (2009) form with the standard-deviation ratio is implemented. The modified KGE (Kling et al. 2012, coefficient-of-variation ratio) and later variants are not, so they cannot be mixed. *The citation should be checked before formal method documentation (guide §Sources).*
- **Labels that prevent misreading:**
  - `pearson_r_squared` is not the predictive coefficient of determination `1 − SSE/SST`. With P predicting O that quantity is `nse`; a biased model can have `pearson_r_squared = 1` with negative NSE (tested). No metric is called `r2`.
  - `bias_corrected_rmse` removes the in-sample mean error. It is a diagnostic (role `diagnostic`), not evidence that a bias-corrected product has been validated.
  - OLS coefficients are descriptive and carry no uncertainty.
- **Defaults:** `DEFAULT_METRICS` = MBE, MAE, RMSE, bias-corrected RMSE, percent bias, `nrmse_mean`, r, r², slope and intercept. NSE, KGE, `nrmse_mean_percent` and MAPE are computed only when listed. MAPE is never a default and never a ranking basis (role `diagnostic`).

**Undefined values are null with a reason;** nothing is clipped or dropped to make a metric defined:

| Reason | When | Affected |
|---|---|---|
| `empty_cohort` | no pairs | all |
| `no_available_periods` | the period product is unavailable in every period | all |
| `singleton_sample` | n = 1 | bias-corrected RMSE, r, r², slope, intercept, NSE, KGE |
| `constant_observed` / `constant_modeled` | `std ≤ tolerance.std` | r, r², KGE; slope, intercept and NSE only for constant O (a constant P has slope 0) |
| `near_zero_observed_mean` | `abs(mean(O)) ≤ tolerance.mean`, equivalently a near-zero observed total | percent bias, NRMSE, KGE β and KGE |
| `near_zero_observation` | some `abs(O_i) ≤ tolerance.mean` | MAPE |

`pearson_r` is clipped to [−1, 1] only to absorb floating-point round-off. Non-finite input raises an error instead of being dropped.

**Tolerances are mandatory and declared before scoring:** `daily_tolerance` `{mean, std}` in `mm d-1`, plus `period_tolerance` in `mm` when period totals are scored. They decide only when a denominator counts as zero. US-UTJ uses `daily {mean: 0.01, std: 0.001}` and `period {mean: 0.1, std: 0.01}` (**Provisional**: chosen before scoring, not tuned on agreement). In the US-UTJ run neither binds: no metric is null for a tolerance reason (D-045).

**Samples:**
- **Daily pairs:** the cohort's paired rows, one sample per cohort × site × model × side (extraction method + footprint set) × reference variant. Before scoring, every model, side and variant inside a cohort and site must cover the same dates; if not, the call raises.
- **Period totals:** each product (`partial_sum`, `complete_total`, `estimated_total`) is scored separately, never pooled. Its sample is the periods where that product is available, as decided by the step-8 aggregation rules (D-041, D-042). The same availability must hold across every model, side and variant of the cohort, or the call raises.
  - The `PeriodTotals` must come from the same cohort (fingerprint check).
  - Partial-sum metrics combine periods with different numbers of matched days. They are labelled with `n_periods` and `n_days` and are descriptive.
- **Units and support:** daily values (`mm d-1`) and period totals (`mm`) are never pooled; the units come from each table's schema. Every model needs a declared temporal support. Models of a ranking cohort (`freeze_models`) must share one support, or `ContractError` is raised. Each row records its `temporal_support`.
- **Counts per row:** `n_sites`, `n_days` (matched days for period totals), `n_days_reference_filled`, `n_days_imputed` (estimated totals only), `n_periods`, `period_completeness` (matched / expected days over the scored periods), `valid`, `validity_reason`, `metric_role`, `ranking_scope` (copied from the cohort) and the union of row `flags`.
- **Scope:** station-level only (`scope_type=site`). Network summaries are step 11.

**Cumulative error** (`cumulative_error` schema v1) holds running sums of O, P and e over cohort days only, per site, model, side and variant. Missing days are never filled. The first day after a gap has `missing_days_before > 0`, the flag `missing_period_before` and a new `segment` number, so plots can break the curve there.

**Schema change:** `metrics` v1 → v2. New keys: `metric_set_id`, `period_type`, `aggregation_id`, `product` and `footprint_set_id`; without them, paired footprint sets or period products would collide. Nothing wrote v1.

### D-045 — US-UTJ metrics run (Information)
From `tools/run_cohorts.py examples/us_utj.yaml --skip-missing-crop-height` on 2026-10-15 (local outputs `outputs/cohorts/*__utj_metrics_*`).
- **Growing-season periods were not scored.** The crop-height file `US-UTJ_20240101_20251230.csv` (D-042) is not in this checkout, so the runner was told to leave out `growing_season` periods, and every report says so. Month, year and water-year products were scored. *Resolved 2026-10-16: the file was restored and the growing-season products were scored (D-046).*
- **Samples reproduce D-040 and D-043:**
  - Daily: 281 days (`utj_rank_corrected_inclusive`), 188 (`_measured`, closure), 303 (pairwise; long-record models in footprint vs window), 260 (set A).
  - Complete months: 8 for the inclusive ranking (2025-01 to 07, 09), 1 for the measured cohorts (2025-09), 0 for set A.
  - Estimated months: 9 for the inclusive ranking (1 imputed day), 4 for measured (7 imputed), 8 for set A.
- **Independent check:** recomputing MBE, MAE, RMSE, percent bias, r, OLS slope/intercept and NSE from the paired Parquet files with numpy/scipy, and RMSE/MBE for every period product from the `period_totals` files, gives a maximum deviation of 3.6e-15.
- **Undefined metrics** are only `singleton_sample` (single-period products, e.g. the one complete month of the measured cohorts and every one-year partial sum) and `no_available_periods` (no complete or estimated year or water year, because the record ends on 2025-10-09). No tolerance-based null occurs.
- **Daily, inclusive ranking cohort (281 days, 2025):**
  - RMSE ranges from 1.04 (SIMS) to 1.63 (geeSEBAL) mm d⁻¹; ensemble 1.15.
  - MBE ranges from −0.90 (SSEBop) to +0.63 (SIMS) mm d⁻¹; percent bias from −30.6 % to +21.6 %.
  - Pearson r² ranges from 0.72 to 0.92.

  These are point estimates without intervals, on gap-filled-inclusive reference days (93 of 281 gap-filled; D-005). They are not rankings (steps 10–11).
- **Flags:** every row carries `day_alignment=unverified` (the OpenET day boundary is undeclared, D-002/D-023), and rows for periods that extend past the record also carry `period_outside_inputs`.


---

**Revision 2026-10-16 (l):** The step-9 run was repeated with growing seasons (D-046), and step 10 was implemented. D-046 to D-048 were added, and D-045's skipped growing season is resolved.

### D-046 — US-UTJ growing-season metrics (Information)
The crop-height file `US-UTJ_20240101_20251230.csv` was restored at the repository root. Its SHA-256 (`ab8c3cc2…d1a48`) matches D-042. Both crop-height integration tests now run and pass. `tools/run_cohorts.py examples/us_utj.yaml` was rerun without `--skip-missing-crop-height` on 2026-10-16.
- **Inputs:** this checkout lacked the four raster-extraction Parquet files from the D-043 rebuild. They were copied byte-identically (SHA-256 checked) from the other local clone where that rebuild ran. Every cohort reproduces D-040, and the daily metrics of the inclusive ranking reproduce D-045 (RMSE 1.04–1.63 mm d⁻¹, MBE −0.90 to +0.63).
- **Seasons:** GS2024 = 2024-03-15..12-06 and GS2025 = 2025-03-15..12-06, 267 days each (D-042). Both extend past the record (2024-06-05..2025-10-09), so every growing-season row is flagged `period_outside_inputs`.
- **Only partial sums exist.** No cohort has a complete or estimated growing-season total:
  - `complete_total` is `incomplete_period` everywhere.
  - `estimated_total` is `below_min_completeness` for the measured-reference cohorts (122/267 = 0.46 < 0.5). It is `imputation_incomplete` for the inclusive cohorts, because days after 2025-10-09 have no later anchor and nothing is extrapolated.
  - Growing-season metrics therefore exist only for `partial_sum` samples.

  | Comparison | GS2024 matched days | GS2025 matched days |
  |---|---|---|
  | `utj_rank_corrected_inclusive` | none (2025-only models) | 208 of 267 (86 gap-filled) |
  | `utj_rank_corrected_measured`, `utj_closure_all_models` | none | 122 |
  | `utj_footprint_vs_window` | 92 (long-record models) | 122 |
  | `utj_pair_eemetric_ssebop` | 92 | 122 |
  | `utj_coverage` (per model, not a ranking) | 165 (long-record models) | 208–209 |
  | `utj_set_a_common` | none | 196 (81 gap-filled) |

- **Sample sizes:** one-period samples give `singleton_sample` for the spread-based metrics; MBE equals the difference of the two totals, and percent bias is defined. Two-period samples (GS2024 + GS2025: pairwise, and long-record models in footprint-vs-window and coverage) give every metric, but n = 2 periods makes them descriptive only.
- **GS2025 partial sums, inclusive ranking cohort** (208 matched days): observed 805.9 mm; modeled 571.3 mm (SSEBop) to 961.3 mm (SIMS); percent bias −29.1 % to +19.3 %; ensemble −11.9 %. **Measured-reference cohort** (122 days): observed 404.4 mm; percent bias −40.4 % (SSEBop) to +17.3 % (SIMS). These are partial sums over matched days, not season totals, and they are not rankings.
- **Independent checks:**
  - Growing-season partial sums recomputed from the paired files and the crop windows match `period_totals` exactly (maximum deviation 0).
  - MBE, MAE, RMSE and percent bias of those sums recomputed with numpy match the metric tables exactly.

### D-047 — Paired block-bootstrap intervals (Adopted rules; US-UTJ defaults Provisional)
`etcompare.bootstrap.bootstrap_comparison(cohort, metric_spec, bootstrap_spec, products=, period_totals=)` gives an `intervals` table (schema v2) and a `residual_autocorrelation` table. Settings are a `BootstrapSpec` in the new `bootstrap:` config section.

**Estimand.** The sampling variability of a step-9 metric, or of a paired contrast, across station days for **one station and one cohort**. It is conditional on that station, the cohort rule, the reference variant, the extraction method and the period of record. It does not cover other stations, other years or a population of sites. It excludes reference measurement error, footprint uncertainty and method-choice sensitivity (step 15). The point estimate is always the step-9 value on the full cohort.

**Scheme: segment-truncated moving blocks.**
- **Unit: the cohort day.** Whole paired rows are resampled. Inside a cohort and site every model, side and variant covers the same days (checked), and one index draw per replicate is applied to all of them. A day removed from one side of a paired cohort is therefore absent from both sides in every replicate (tested).
- **Segments.** Sorted cohort days are split at every missing calendar day, at sites and at the declared `segment_boundary` (`none`, `year` or `water_year`). Gaps are never compressed.
- **Blocks.** Any cohort day can start a block. A block holds the start day and up to `b − 1` following days of the same segment: it stops at the segment end and never wraps. Starts are drawn uniformly with replacement and blocks are concatenated to the original `n` days; the last block is cut. With `b = 1` this is the iid-day bootstrap. Step-9 daily metrics do not depend on day order, so concatenating blocks creates no false adjacency.
- **Edge handling.** A day `j` days into its segment (0-based) is covered by `min(j + 1, b)` starts, so the first `b − 1` days of each segment are drawn less often (tested). Segments shorter than `b` give only truncated blocks, and rows built on such samples carry `short_segments=<k>`.
- **Support.** `n_blocks = Σ floor(L_s / b)` disjoint full-length blocks. When `n_blocks < min_blocks`, nothing is drawn and the status is `insufficient_support`, with the counts in `status_detail`.
- **Recomputation.** Every replicate sample goes through `metrics.compute_metrics` with the metric set's metrics and tolerance; no formula is re-implemented. A replicate in which a metric is undefined is invalid for that metric.
- **Intervals.** `percentile` gives the `(α/2, 1 − α/2)` quantiles of the valid replicates (numpy's default linear quantile). `basic` gives `2θ − q`. No bounds are given when the estimate is undefined (`estimate_undefined`) or when fewer than `min_valid_fraction` of the replicates are valid (`insufficient_valid_replicates`). Rows whose estimate falls outside the interval are flagged `estimate_outside_interval`, and rows with some undefined replicates are flagged `undefined_replicates=<k>`.
- **Random streams.** Each sample and block length has its own stream, seeded from the declared `seed`, the block unit and length, and the sample identity. Results therefore do not depend on run order, and the same seed reproduces them exactly (tested).

**Period metrics resample whole periods** (one interpretation, never mixed). The unit is an available period of the product. Blocks are runs of consecutive periods (`period_block_lengths`), and a segment ends at a missing period or a season-year boundary. Period totals are never rebuilt from resampled daily blocks. Every model, side and variant shares the available periods (step-9 check), so period indices are paired as well.

**Contrasts** are `metric(target) − metric(baseline)`, computed in every replicate on identical indices:
- `paired_method`: each further side minus the first.
- `paired_reference`: each further variant minus the first.
- `pairwise_model`: the second model minus the first.

With the footprint side first, the MAE contrast is `MAE_window − MAE_footprint`, so positive means the footprint has the smaller error (guide step 11). Contrasts of percent metrics are in percentage points. All-model model contrasts, rankings and network summaries are step 11; `BootstrapResult.replicates()` keeps the paired replicate values for that step.

**Residual autocorrelation diagnostic.** `r_k = mean(d_t·d_{t+k}) / mean(d²)`, where `d = e − mean(e)` and the numerator is averaged over pairs of cohort days exactly `k` calendar days apart. Pairs across a gap keep their true lag. The approximate white-noise bound `1.96/√n_pairs` is reported with it. The diagnostic informs the block-length comparison; it does not select a length.

**Scope and limitations.**
- **Station level only.** A fixed monitored network and a population of sites are different targets. Population inference needs a hierarchical design that resamples sites and, within them, temporal blocks, keeping common calendar blocks across sites where shared weather matters. Neither this design nor network summaries (step 11) is implemented.
- **Stationarity.** The moving-block bootstrap assumes stationary, short-memory dependence. Seasonal error regimes violate this, and blocks shorter than the dependence make intervals too narrow (the AR(1) gate test).
- **Shared drivers.** Gap-filled reference days and imputed days are resampled like any other day; their shared drivers (D-005, D-042) are not modelled.

**Schemas.** `intervals` v1 → v2. New keys: `cohort_id`, `bootstrap_id`, `metric_set_id`, `scope`, `temporal_scale`, `period_type`, `aggregation_id`, `product`, the series columns, `contrast_dimension`, `contrast_baseline`, `interval_method`, `block_unit` and `block_length`. New counts: `n_segments`, `n_blocks`, `n_days`, `n_periods` and `n_replicates_valid`. Also added: `status`, `status_detail`, `units`, `metric_role`, `ranking_scope` and `flags`. Nothing wrote v1. `residual_autocorrelation` v1 is new.

**US-UTJ defaults (Provisional).** They were fixed before any interval was computed and are not tuned on agreement or interval width:
- block lengths 7, 14 and 28 days, all reported side by side as a sensitivity comparison (no length is adopted);
- period block length 1;
- seed 20261016, 2,000 replicates, 95 % level, percentile intervals;
- `min_blocks` 10: fewer than 10 independent blocks give too few distinct block combinations to place the 2.5 % and 97.5 % quantiles;
- `min_valid_fraction` 0.9;
- `segment_boundary: water_year` (start month 10, matching `utj_periods`);
- autocorrelation up to lag 28.

A block length must never be chosen because its intervals are tighter.

### D-048 — US-UTJ bootstrap run (Information)
From `tools/run_cohorts.py examples/us_utj.yaml` on 2026-10-16 (about 80 s for all 7 comparisons). The outputs are the local `outputs/cohorts/*__utj_block_bootstrap_{intervals,autocorrelation}.*` files. They are sampling intervals for one station, not rankings.
- **Support** (daily samples; segments split at gaps and at the WY boundary on 10-01):

  | Comparison | Days | Segments | Blocks at 7 / 14 / 28 days |
  |---|---|---|---|
  | `utj_rank_corrected_inclusive` | 281 | 3 | 39 / 19 / **9** |
  | `utj_rank_corrected_measured`, `utj_closure_all_models` | 188 | 12 | 23 / 10 / **4** |
  | `utj_footprint_vs_window` (long-record models) | 303 | 21 | 37 / 16 / **6** |
  | `utj_footprint_vs_window` (2025-only models) | 188 | 12 | 23 / 10 / **4** |
  | `utj_pair_eemetric_ssebop` | 303 | 21 | 37 / 16 / **6** |
  | `utj_coverage` (eemetric, ensemble, ssebop) | 472 | 4 | 66 / 32 / 16 |
  | `utj_coverage` (2025-only models) | 281–282 | 2–3 | 39–40 / 19 / **9** |
  | `utj_set_a_common` | 260 | 19 | 28 / **9** / **2** |

  Bold counts are below `min_blocks` = 10 and give `insufficient_support`. **28-day intervals therefore exist only for the three long-record models in the coverage cohort**, and set A has no 14-day intervals either. Every daily row with support has status `ok`. No row has undefined replicates or an estimate outside its interval.
- **Period metrics** (whole periods, `b = 1`): intervals exist only where at least 10 periods are available:
  - monthly partial sums: inclusive ranking and set A 10 months, pairwise and the long-record models of footprint-vs-window 16, coverage 10–17;
  - complete and estimated monthly totals of the coverage cohort's long-record models (15 months).

  Every other period product (8 or fewer complete months; years, water years and growing seasons with 1–2 periods) is `insufficient_support` or `estimate_undefined`. Monthly partial sums mix months with unequal matched days and remain descriptive.
- **Residual autocorrelation is strong and long.** Across the 58 daily series, lag-1 autocorrelation ranges 0.43–0.93, lag 7 0.12–0.67, lag 14 −0.10 to 0.54 and lag 28 −0.25 to 0.41. 13 series never fall inside the white-noise bound by lag 28. Mean error evidently varies by season, so the dependence lasts beyond 14 days for many series.
- **Block-length sensitivity.** Moving from 7- to 14-day blocks widens daily MBE/MAE/RMSE intervals by a median factor of 1.09–1.27 per comparison (range 0.76–1.38).

  Together with the autocorrelation, this means the 7- and 14-day intervals are probably **too narrow** for many series (compare the AR(1) gate test), while 28-day blocks lack support at `min_blocks` = 10 in most cohorts. The intervals are reported at every supported length, and no length is adopted.
- **Every row** carries `day_alignment=unverified` (D-023). Gap-filled reference days (93 in the inclusive cohort) are resampled like measured days (D-005).

What would change these results:
- a longer record, which adds blocks;
- a decision on how to handle seasonal non-stationarity, for example seasonal strata or a block length chosen from the autocorrelation diagnostic before scoring;
- a different `min_blocks`.

Each is a user decision to be declared in configuration, never tuned on interval width.

---

**Revision 2026-10-16 (m):** Step 11 implemented. D-049 and D-050 added. Two all-model sensitivity cohorts were added to the example config (`utj_rank_closure`, `utj_rank_methods`). D-040 to D-048 are unchanged.

### D-049 — Rankings, model contrasts and network summaries (Adopted rules; US-UTJ defaults Provisional)
`etcompare.ranking.rank_comparison(cohort, metric_results, bootstrap, spec)` ranks the models of one cohort under a `RankingSpec` (the new `rankings:` config section). It writes four tables: `model_rankings`, `rank_distribution`, `model_contrasts` and `network_summaries`. `ranking_sensitivity(results)` writes a fifth, `ranking_sensitivity`. All five schemas are new at v1.

**What may be ranked.**
- **Cohorts:** only cohorts with ranking scope `all_models` (`common_date`) or `pairwise` (`pairwise_model`). Coverage (`model_available`), `paired_method` and `paired_reference` cohorts are refused, both by the config loader and at run time.
- **Contexts:** models are ranked only inside one frozen context: one cohort fingerprint, site, temporal scale and period product, reference variant and extraction side. `check_rankable` raises when rows differ in any of these fields, or in metric, units or temporal support. A model set that differs from the cohort's also raises. Rankings therefore never pool unequal cohorts (tested).
- **Metrics:** only `score` metrics. Their directions are declared once, in `config.RANKING_DIRECTIONS`:

  | Direction | Metrics | Ranked by |
  |---|---|---|
  | lower is better | `mae`, `rmse`, `nrmse_mean`, `nrmse_mean_percent` | value |
  | higher is better | `pearson_r`, `pearson_r_squared`, `nse`, `kge_2009` | value |
  | closest to zero is better | `mbe`, `percent_bias` | absolute value |

  A ranking spec must restate each direction, and a mismatch is refused. Diagnostic metrics (`bias_corrected_rmse`, `mape`), descriptive ones (OLS coefficients) and KGE components are refused as a ranking basis.

**Ties.** Values are put on a lower-is-better scale. Exact equality is a tie, with no tolerance: identical predictions give bit-identical metrics, and anything else does not tie. A model's rank is its mid-rank. Tied models share their positions equally in the rank distribution, so every model's and every position's frequencies sum to 1, and the top-rank frequencies of a context sum to 1 (tested). If any model's point estimate is undefined, the context is not ranked (`estimate_undefined`): dropping that model would rank an unequal set.

**Uncertainty comes from step 10.**
- **No new draws.** Rank statistics and contrast intervals use `BootstrapResult.replicates()`, computed replicate by replicate on the shared indices. Nothing is redrawn and no metric is re-implemented.
- **No block length is adopted** (D-047, D-048). Every declared block length is reported side by side, each row keyed by `block_unit` and `block_length`.
- **Rank statistics:**
  - `mean_rank`;
  - a rank interval: the α/2 and 1 − α/2 quantiles of the replicate mid-ranks, using numpy `method="lower"` and `"higher"`;
  - `top_rank_frequency`, a **bootstrap stability measure, not the probability that a model is best**;
  - the full position distribution.
- **Statuses replace values.** A rank statistic or interval built on unsupported or undefined replicates has a status instead of a value:
  - `insufficient_support`, carried from step 10;
  - `insufficient_valid_replicates`, when fewer than `min_valid_fraction` of the replicates have every model defined (for contrasts: both models defined);
  - `estimate_undefined`;
  - `not_resampled`, when no block length applies.

  The point rank is still reported under `insufficient_support`, because it is the step-9 estimate.
- **No forced winner.** `leader_status = unique_leader` only when the point-best model's contrast interval (first declared interval method) excludes zero in its favour against every other model. Otherwise the status is `no_unique_leader`, and `leading_models` lists the point-best model or models plus every model not separated from them.

**Contrasts.**
- **Form:** `metric(model_a) − metric(model_b)` for every pair. `model_b` is the model declared earlier, which matches the step-10 pairwise contrast (tested to be identical).
- **Reading the sign:** each row states it in `sign_meaning`. For lower-is-better metrics a negative contrast favours `model_a`; for higher-is-better metrics a positive one does.
- **Closest-to-zero metrics** get two rows:
  - `value`, the signed difference. It does not order the models (`ranking_basis = false`).
  - `absolute_value`, `|metric_a| − |metric_b|`. Here a negative contrast favours `model_a`.
- **Effect size:** the contrast is the practical effect size, in the metric's units (percentage points for percent metrics), reported with its interval, `interval_position` and `favoured_model`.
- **Extraction-method contrasts** keep the step-10 convention `delta_mae = MAE_window − MAE_footprint`, where positive means the footprint has the smaller error. They come from `etcompare.bootstrap`.

**Equivalence.**
- **Margins:** `equivalence_margins` (daily, in metric units) and `period_equivalence_margins` (period products) are declared before ranking.
- **Without a margin** the result is `not_assessed`.
- **With a margin `d`,** for the ranking-basis interval:
  - inside `(−d, d)`: `equivalent`;
  - entirely at or beyond `d` on one side: `practically_different`;
  - otherwise: `inconclusive`.
- **The signed `value` rows** of closest-to-zero metrics are `not_applicable`.
- **An interval crossing zero is never taken as equivalence** (tested).
- **Level:** judging on the reported 95 % interval corresponds to a TOST at 2.5 % per side.

**Multiplicity.**
- **Default:** `formal_tests: false`, so every contrast is labelled `exploratory`.
- **Formal tests** require an explicit `multiplicity` policy (`holm` or `bonferroni`); declaring a policy without formal tests is refused.
- **Formal tests give:**
  - for each ranking-basis contrast with an interval, the percentile-inversion bootstrap p-value `min(1, 2·min(P(d ≤ 0), P(d ≥ 0)))`;
  - an adjustment within one family: one comparison, subset and block length, covering every metric, context and pair;
  - a decision at α = 1 − confidence level.

**Network summaries.**
- **Primary result:** the equal-site mean of station metrics.
- **Optional, labelled alternatives:**
  - observation-count weighting (`n_days` for daily pairs, `n_periods` for period products);
  - square-root-count weighting;
  - `pooled_rmse` = `sqrt(Σ n·RMSE² / Σ n)`. This is the RMSE over all site observations, labelled as *not* the mean station RMSE.
- **Undefined values:** a summary is undefined when any site's metric is.
- **Single site:** `n_sites` is explicit. With one site the status is `single_site`, `network_evidence` is false and the row carries the flag `not_network_evidence`.
- **Gate test:**
  - Duplicating a station's record within the station (every day twice) leaves its station metric unchanged and doubles its observation count.
  - That leaves the equal-site mean unchanged, while the observation-weighted mean changes.
  - A copy added as a separate *site* counts as a new site and does change the equal-site mean. The test documents this.
- **No interval.** A fixed network and a site population are different targets. Population inference needs hierarchical resampling of sites together with common calendar blocks. This is a documented limitation and is not implemented.

**Ranking sensitivity.**
- **What is compared:** point ranks of two contexts that differ in exactly one dimension:
  - station;
  - year subset;
  - temporal scale or product (one side daily);
  - reference variant;
  - extraction side.

  Contexts that differ only in their cohort are labelled `cohort`; any other pair is confounded and skipped.
- **Every context is an internally frozen cohort.** `same_cohort` says whether both contexts share one fingerprint.
- **Year subsets** (`year_subsets: true`) are rebuilt with `build_cohort` under the same rule, with `start` and `end` restricted to one calendar year. They are scored daily, bootstrapped and ranked with `subset=year=<y>`.
- **Closure and extraction sensitivity** need all-model cohorts frozen across both variants or both sides. The paired-reference and paired-method cohorts cannot be ranked, so two `common_date` cohorts were added:
  - `utj_rank_closure`: `et_raw` and `et_corrected_measured`;
  - `utj_rank_methods`: footprint and 3×3 window.

**US-UTJ defaults (Provisional; fixed before any ranking was computed):**
- ranking `utj_model_ranking` on `utj_metrics` and `utj_block_bootstrap`;
- ranked metrics MAE, RMSE, `nrmse_mean`, r, r², NSE, KGE 2009, MBE and percent bias. `nrmse_mean_percent` is left out because it is `nrmse_mean` × 100;
- no equivalence margins, so equivalence is `not_assessed`;
- all four network summaries;
- `formal_tests: false`;
- `year_subsets: true`;
- the six ranking cohorts.

### D-050 — US-UTJ ranking run (Information)
From `tools/run_cohorts.py examples/us_utj.yaml` on 2026-10-16 (2 min 43 s for all 9 comparisons). The outputs are the local `outputs/cohorts/*__utj_model_ranking_*` files and `utj_model_ranking_sensitivity.*`. These are descriptions of one station, not verdicts on which model is best.
- **Independent check:** point ranks recomputed with `scipy.stats.rankdata` from the written paired files match every daily ranking. Metric values and contrast estimates match the step-9 tables to 1.1e-14. Top-rank frequencies sum to 1 in every ranked context.
- **New cohorts:** `utj_rank_closure` and `utj_rank_methods` both have 188 days, the same as `utj_rank_corrected_measured`. Their corrected-measured and footprint rankings equal it exactly (sensitivity dimension `cohort`: no rank changes).
- **Block-length support** (as in D-048):

  | Cohort | Days | Blocks at 7 / 14 / 28 days |
  |---|---|---|
  | inclusive | 281 | 39 / 19 / 9 |
  | measured, closure, methods | 188 | 23 / 10 / 4 |
  | pairwise | 303 | 37 / 16 / 6 |
  | pairwise, 2024 only | 115 | 14 / 6 / 2 |
  | pairwise, 2025 only | 188 | 23 / 10 / 4 |
  | set A | 260 | 28 / 9 / 2 |

  - **28-day blocks mostly lack support:** every 28-day ranking and contrast is `insufficient_support`. Set A and pairwise-2024 also lack 14-day support.
  - **The 7- and 14-day intervals are probably too narrow** given the residual autocorrelation (D-048), so the stability figures below are probably optimistic.
  - No block length is chosen.
- **No unique leader in any all-model cohort.** This holds for every metric, at 7 and at 14 days.
  - Inclusive ranking, daily MAE: 3 of the 21 model-pair intervals exclude zero, at both block lengths.
  - The point leaders are ensemble (MAE) and SIMS (RMSE, KGE). Their top-rank frequencies are 0.55–0.68, with rank intervals from 1–3 to 1–7. Each is not separated from 3 to 6 other models (`leading_models`).
- **Pairwise eemetric/ssebop** (303 days):
  - **Separated:** eemetric's `|MBE|`, `|percent bias|` and KGE are separated at 7 and 14 days, and ssebop's r and r² at 7 days only.
  - **Not separated:** MAE, RMSE and NSE at either length.
  - **Year subsets** (2024: 115 days; 2025: 188 days): eemetric's `|MBE|` and `|percent bias|` are separated at 7 days in both years. The two models' MAE ranks swap between the years; their RMSE ranks do not.
- **Reference variant matters most.** In `utj_rank_closure`, `et_raw` against `et_corrected_measured` moves 6 of the 7 models in MAE and RMSE, by up to 5 positions. SIMS goes from first to sixth in RMSE, and ensemble leads MAE and RMSE on raw ET. This is closure sensitivity, not a statement about which reference is right (D-004).
- **Extraction side matters less.** In `utj_rank_methods`, footprint against 3×3 window moves 6 models in MAE (by up to 3) and 3 in RMSE (by up to 2). Set A against the NLDAS primary (`extraction_method` dimension, different days) moves none in MAE or RMSE.
- **Period products:**
  - Monthly partial sums are the only period samples with rank statistics (10 months in the inclusive cohort and set A, 16 in the pairwise cohort). They mix months with unequal matched days and are descriptive.
  - Complete and estimated months (at most 9 per cohort), years, water years and growing seasons are `insufficient_support` or `estimate_undefined`.
- **Network summaries:** `n_sites = 1` everywhere (status `single_site`, flag `not_network_evidence`). They restate the US-UTJ station metrics and are not network evidence.
- **Not assessed:** equivalence (no margin declared) and formal tests (none requested). Every contrast is exploratory.
- **Gap-filled reference days:** 93 of the 281 inclusive days and 88 of the 260 set-A days share gridMET drivers with the OpenET interpolation (D-005). They are resampled and ranked like measured days, so the inclusive rankings are not independent validation. The measured-reference cohorts avoid this, with fewer days.
- **Every row** carries `day_alignment=unverified` (D-023).

What would change these results:
- more stations, which would make the network summaries evidence;
- a block-length and support decision (D-048);
- declared equivalence margins;
- a declared quality threshold (D-008).

---

**Revision 2026-10-16 (n):** Step 12 implemented. D-051 and D-052 added. D-001 to D-050 are unchanged. Later the same day the user confirmed the station-variable units (D-053).

### D-051 — Closure, coverage and environmental diagnostics (Adopted rules; US-UTJ defaults Provisional)
`etcompare.diagnostics.diagnose_comparison(cohort, spec, metric_spec, station_daily=, extractions=, bootstrap=)` describes one frozen cohort under a `DiagnosticsSpec` (the new `diagnostics:` config section). It writes five tables: `closure_magnitude`, `reference_provenance_periods`, `conditional_errors`, `error_regressions` and `diagnostic_availability`. `reference_rank_changes(sensitivity, diagnostics_id)` writes a sixth, `reference_rank_changes`. All six schemas are new at v1.

**Closure and reference sensitivity.**
- **Never recomputed.** The correction magnitude uses the station's existing columns on `corrected_from_raw` days only:
  - `correction_mm = ET_corr − ET`;
  - `correction_ratio = ET_corr / ET` (days with ET = 0 are counted as missing);
  - the closure factor (`ebc_cf`);
  - declared closure metadata (`ebr`, `ebr_corr`, `ebr_5day_clim`).
- **Distribution:** mean, standard deviation, minimum, 5/25/50/75/95th percentiles and maximum, with counts. It is given for all days, per month (`YYYY-MM`) and per meteorological season (`YYYY-DJF`, with December counted in the following year). Both the station record (`day_set = station_record`) and the cohort's days (`cohort`) are covered.
- **Gap-filled days are excluded and counted** (`n_days_filled_excluded`). Their `ET_corr` is `gridMET_ETr × ETrF_filtered`, not a closure adjustment, even where `ebc_cf` is present (D-004, D-005).
- **Label:** corrected/raw differences are reference sensitivity, not a calibrated uncertainty envelope containing the truth (every row carries the flag `reference_sensitivity_not_uncertainty`).
- **Rank changes are not re-ranked.** They are the `reference_variant` rows of `ranking_sensitivity` (D-049), each labelled with the effect it isolates:

  | Pair | `effect` |
  |---|---|
  | `et_raw` vs `et_corrected_measured`, one cohort | `closure` |
  | the same variants on different cohorts | `closure_and_cohort_days` |
  | `et_corrected_measured` vs `et_corrected_inclusive` | `gap_filling_and_cohort_days` |
  | `et_raw` vs `et_corrected_inclusive` | `closure_and_gap_filling` |

  Only the first isolates closure. Unrestricted ET vs ET_corr mixes closure with gap filling (D-004).

**Gap-filled versus observation-dominated days (D-005).**
- **Subsets of one cohort.** Inside one frozen cohort, metrics on all accepted days are compared with the `corrected_from_raw` and `corrected_filled` subsets (`variable = reference_provenance`).
- **Identical days.** Provenance belongs to the station day, so every model of a frozen cohort has identical subset days (tested; `bin_days_shared`).
- **Names.** "Observation-dominated" is used rather than "observed", because subdaily gap fractions are unknown.
- **Flags.** Filled-day rows carry `shared_gridmet_drivers` and `not_independent_validation`; all-day rows that include filled days carry `includes_gap_filled_days`.
- **Gap-filled fraction.** It is reported per month, per season and overall in `reference_provenance_periods`. As a conditioning variable (`gap_filled_fraction`), each day gets its calendar month's share of filled cohort days.

**Conditional error summaries.**
- **Declared variables.** Every conditioning variable is declared with `source` (`station`, i.e. a `station_daily` column such as `src_vpd` (D-020); or `extraction`, a column of the cohort side's extraction rows), `column`, `units`, `meaning` and `status`:
  - `verified`;
  - `provisional`: used, with rows flagged `variable_provisional`;
  - `unverified`: declared only so that its diagnostic reports `not_available`, with a mandatory `note`.
- **Skips are local.** An undeclared or unverified variable, a missing column, a non-numeric or all-null column, or out-of-range directions give a `diagnostic_availability` row with `status = not_available`, a `reason_code` and a reason. Only that diagnostic is skipped (tested).
- **Bins are fixed before any error exists.** `resolve_bins` receives only the configuration and the station table, never errors. Bins can be:
  - declared fixed `edges` (the default);
  - `quantiles` of the variable's own values over a declared training period (station variables only; tied quantiles are refused);
  - month of year and meteorological season;
  - wind sectors;
  - reference provenance.

  Interval bins are `[lower, upper)`, and the last is `[lower, upper]`; ±inf is allowed at the ends. Days outside the edges, days without a value and, for `measured_days_only` variables, gap-filled days become remainder rows (`bin_index` −1, −2, −3). Every day is therefore accounted for (tested: the bins sum to the series).
- **Metrics are reused.** Each bin's paired sample, per model, reference variant and side, goes through `metrics.compute_metrics` with the metric set's tolerance. The metrics are a subset of `DIAGNOSTIC_METRICS` (MBE, MAE, RMSE, bias-corrected RMSE, percent bias, r).
- **Sparse bins:** fewer than `min_bin_days` days give `sparse_bin` (`empty_bin` with none), with a status and no value.
- **Counts:** `n_days` (this model), `n_days_bin_cohort` (any model), `n_days_reference_filled`, `n_days_series` and `bin_days_shared`. A variable read from extraction rows (retained footprint fraction) can place different days of different models in one bin; such rows are flagged `bin_days_differ_across_models` and do not compare models on identical days.
- **One cohort only.** Rows are keyed by cohort, site, model, variant and side, so unequal cohorts are never pooled (D-039).
- **Optional intervals reuse the step-10 scheme.** The bin's days are split into runs of consecutive calendar days (and at the season-year boundary). `bootstrap.segment_bounds`, `block_support`, `sample_rng` and the replicate routine are reused. Draws are shared by every series with the same bin days, and every block length is reported side by side. Runs shorter than the block hold no full block, so scattered bins mostly give `insufficient_support`. No block length is preferred (D-048).
- **Interpretation:** conditional summaries are associations within one station's record, not causal explanations.

**Process diagnostics.**
- **Sensible heat:** only daily-mean H exists. Its negative bin (edges must include 0) is flagged `negative_daily_mean_h` and `daily_mean_h_indicator`. It is neither a daytime negative-H frequency (`daytime_negative_h_frequency` is always `not_available` with daily input) nor proof of advection.
- **Wind direction:** `wind_sector` uses `n` equal sectors (`n` divides 360), with sector 0 centred on north. Sector `k` = `[k·w − w/2, k·w + w/2)`. 359° and 1° share the north sector, 360° is north, and with 8 sectors 22.5° opens the next sector. Values outside [0, 360] are refused, never wrapped. Without a declared direction variable the analysis is `not_available`.
- **Net radiation:** `error_regressions` gives the OLS slope, intercept and r of `e` on net radiation per series.
  - With a corrected reference, `coupling = energy_balance_corrected_reference` and the flag `mathematical_coupling` are set. The correction scales measured ET by the closure ratio, which depends on net radiation, so error and predictor are coupled.
  - The `et_raw` version is reported alongside where the cohort has that variant. Otherwise a `cohort_lacks_variant` row says so.
- **Satellite support:** no acquisition flags exist (D-011), so clear-versus-interpolated analyses are always `not_available`. Nothing is inferred from missing rows or scene dates.

**Schemas (all v1).**
- `closure_magnitude`, keyed by day set, period and quantity.
- `reference_provenance_periods`.
- `conditional_errors`, keyed by cohort, site, model, variant, side, variable, bin, metric, interval method and block length, so cohorts, variants, sides, variables and bins cannot collide.
- `error_regressions`.
- `diagnostic_availability`.
- `reference_rank_changes`.

They carry explicit counts, statuses and flags.

**US-UTJ defaults (Provisional; fixed before any conditional error was computed).** The `utj_diagnostics` set:
- **Metrics and bins:** MBE, MAE and RMSE of `utj_metrics`; `min_bin_days` 20; intervals from `utj_block_bootstrap` (7/14/28 days).
- **Calendar and provenance:** season and month; gap-filled fraction edges 0/0.25/0.5/0.75/1; provenance subsets on.
- **Closure:** on, with closure metadata `ebr`, `ebr_corr` and `ebr_5day_clim`.
- **Comparisons:** the six ranking cohorts plus `utj_closure_all_models`.
- **Station variables:**

  | Variable | Column | Units | Status | Edges |
  |---|---|---|---|---|
  | `eto_gridmet` | `src_gridMET_ETo` | mm/day | provisional | 0, 2, 4, 6, 8, ∞ |
  | `vpd` | `src_vpd` | kPa | provisional (range consistent with kPa) | 0, 0.5, 1, 1.5, 2, 2.5, ∞ |
  | `wind_speed` | `src_ws_1_1_1` | m s⁻¹ | provisional (height unrecorded) | 0, 1, 1.5, 2, 3, ∞ |
  | `h_daily` | `src_h_1_1_1` | W m⁻² | provisional | −∞, 0, 25, 50, ∞ |
  | `rn_daily` | `src_netrad_1_1_1_final` | W m⁻² | provisional | regression only |
  | `ebr_corr` (closure ratio) | `src_ebr_corr` | 1 | verified (= 1/`ebc_cf`) | 0, 0.7, 0.8, 0.9, ∞; measured days only |
  | `ebr`, `ebr_5day_clim` | `src_ebr`, `src_ebr_5day_clim` | 1 | provisional | closure metadata only |

- **Extraction variables:**

  | Variable | Column | Units | Status | Edges |
  |---|---|---|---|---|
  | `retained_frac` | `fp_weight_retained_frac` | 1 | verified (D-029) | 0, 0.8, 0.9, 0.99, ∞ |
  | `domain_capture` | `fp_domain_capture_frac` | 1 | verified meaning | 0, 0.9, 1, ∞ |

  The top retained-fraction edge is open because 545 rows exceed 1 by round-off (≤ 4.5e-16). This was decided from the variable's values, not from errors.
- **Declared unverified, so `not_available`:** `h_subday_gaps` and `le_subday_gaps` (counts, units and denominators unresolved, contract §10) and `rh` (percent versus fraction unconfirmed; VPD is used instead).
- *Superseded in part by D-053 (same day):* the user confirmed the units, so ETo, VPD, wind speed, daily H, net radiation and RH (%) are now `verified`. `ebr` and `ebr_5day_clim` stay `provisional`.
- **Not declared:** wind direction (absent from the daily table).

Edges are round physical values in the declared units. No bin, threshold or variable was chosen because it changes agreement. Units marked provisional need user confirmation. If a unit proves wrong, its edges must be redeclared in the correct unit before the diagnostics are rerun, never adjusted after seeing errors.

### D-052 — US-UTJ diagnostics run (Information)
From `tools/run_cohorts.py examples/us_utj.yaml` on 2026-10-16 (3 min 35 s for all 9 comparisons; the diagnostics took 4–14 s per comparison). The outputs are the local `outputs/cohorts/*__utj_diagnostics_*` files and `utj_diagnostics__utj_model_ranking_reference_rank_changes.*`. They describe one station; they are not causal explanations, and corrected/raw differences are not uncertainty bounds.
- **Independent check:** MBE, MAE and RMSE recomputed with numpy from the written paired files and the raw CSV match all 3,222 non-sparse bin metrics of the VPD, ETo, H and wind-speed bins to 8.9e-16. Bin day counts match, and every bin below 20 days is `sparse_bin` with no value. The station-record closure mean and count match the CSV exactly. Provenance subsets have identical days for every model in every cohort.
- **Not available** (every comparison): `domain_capture` (no capture values for the NLDAS footprint set), `h_subday_gaps` and `le_subday_gaps` (declared unverified; `rh` was too until D-053), `daytime_negative_h_frequency` (daily input only), `wind_direction_sector` (no direction) and `satellite_support` (D-011). The retained fraction is `not_available` on the 3×3-window side (no footprint). Cohorts without `et_raw` report the raw net-radiation version as `cohort_lacks_variant`. Every other diagnostic ran.
- **Correction magnitude** (303 `corrected_from_raw` station days; 170 gap-filled days excluded):
  - `ET_corr − ET`: mean 0.57 mm d⁻¹, median 0.20, 5–95 % 0.03–2.28.
  - `ET_corr/ET`: mean 1.30, median 1.26, 5–95 % 1.15–1.53. It equals `ebc_cf` to 2.9e-15, as the adapter verified; nothing was recomputed.
  - **Seasonally:** the ratio is highest in 2024-JJA (median 1.49, 39 days) and lowest in 2025-SON (1.20, 38 days). The absolute correction follows ET: about 2 mm d⁻¹ in 2024-JJA against 0.04 mm d⁻¹ in 2025-DJF.
- **Rank changes between reference variants** (daily, whole cohorts; from `ranking_sensitivity`, not re-ranked):

  | Effect | Pair | Models moved, MAE / RMSE | Largest move |
  |---|---|---|---|
  | `closure` | `utj_rank_closure`, `et_raw` vs `et_corrected_measured` (188 days) | 6 / 6 of 7 | 5 positions |
  | `gap_filling_and_cohort_days` | measured (188) vs inclusive (281) | 5 / 2 | 3 (MAE), 1 (RMSE) |
  | `closure_and_gap_filling` | `et_raw` vs inclusive | 4 / 5 | 5 |

  Closure moves the rankings more than adding the gap-filled days does. Both are reference sensitivity (D-050).
- **Gap-filled versus observation-dominated days, inclusive ranking cohort** (281 days: 188 `corrected_from_raw`, 93 `corrected_filled`, identical for all models):
  - **Gap-filled fraction:** 0.33 overall. Per month it ranges from 0 (2025-09) to 1.0 (2025-05, every day filled), and it is 0.43–0.60 in June–August. Gap-filled days are therefore concentrated in the high-ET months, so the filled subset also differs in season.
  - **MAE:** higher on filled days than on measured days for 6 of 7 models (SSEBop only marginally: 0.97 against 0.96). For example, ensemble has 0.93 against 0.70 mm d⁻¹ and SIMS 1.03 against 0.70. eemetric is the exception, with 0.74 against 0.95. Set A (260 days, 88 filled) shows the same pattern.
  - **Interpretation:** filled days share gridMET drivers with OpenET (D-005), so this is not independent validation either way. Season and fill status are confounded.
- **Conditional bins** (daily MAE, `min_bin_days` 20):
  - **Sparse bins** in the 188-day measured cohort: VPD 2–2.5 kPa (14 days), ETo ≥ 8 mm d⁻¹ (18), wind < 1 and ≥ 3 m s⁻¹ (17, 14), closure ratio < 0.7 (16), and most single months. The closure ratio has no day at or above 0.9.
  - **Negative daily-mean H** (31 days in the measured cohort): MAE 0.8–2.8 mm d⁻¹ against 0.3–1.1 in the other H bins. This is a daily-mean indicator, not a daytime negative-H frequency and not evidence of advection. 30 of the 31 days fall in June–September, so season is confounded with it.
  - **Retained fraction:** only SIMS falls below 0.99 (cropland mask, D-008), so its bins hold days that the other models do not have. These rows are flagged `bin_days_differ_across_models`.
- **Intervals per bin** (7 / 14 / 28-day blocks, `min_blocks` 10):
  - Bins of scattered days (VPD, ETo, wind, H, month) have no interval at any length.
  - At 7 days, intervals exist for the provenance subsets, some closure-ratio, gap-fraction and retained-fraction bins, and the MAM and JJA season bins of the inclusive cohort.
  - At 14 days, fewer exist (211 bin × model MAE intervals, against 342 at 7 days): provenance, gap-fraction and retained-fraction bins only.
  - **28-day blocks give no bin interval anywhere** (`insufficient_support`).
  - As in D-048, the 7- and 14-day intervals are probably too narrow, because the residual autocorrelation is long. No block length is chosen.
- **Net radiation** (`utj_closure_all_models`, 188 days, slope of e on Rn):
  - **Raw reference:** −0.009 to −0.003 mm d⁻¹ per W m⁻² (SIMS +0.0075).
  - **Corrected reference:** −0.017 to 0.000.
  - **Coupling:** the corrected-minus-raw slope is about −0.0076 for every model. This shift is the slope of the correction term itself on Rn: the mathematical coupling the raw version is there to expose. Neither slope is a causal effect.
- **Every row** carries `day_alignment=unverified` (D-023), and provisional variables carry `variable_provisional` (after D-053, only `ebr` and `ebr_5day_clim`).

What would change these results:
- confirmed units or meanings of the station variables (their status, and edges where a unit is wrong);
- subdaily gap definitions (they would allow gap diagnostics);
- acquisition metadata (satellite support);
- wind direction;
- a domain-capture product for the NLDAS set;
- a block-length decision (D-048);
- more stations.

### D-053 — US-UTJ station meteorology units confirmed (Adopted 2026-10-16, user decision)
The user confirmed the station-variable units that D-051 had marked provisional, and pointed to `US-UTJ.ini` (repository root, local) as supporting metadata. The `.ini` `[DATA]` section describes the **half-hourly** AmeriFlux-format input, not the daily CSV (contract §2). It gives NETRAD, H, LE and G in W/m², WS in m/s, RH_1_1_1 in % (0–100), WD as azimuth in degrees, and VPD in **hPa**.

| Daily column | Unit (status `verified`) | Evidence |
|---|---|---|
| `gridMET_ETo` | mm/day | user |
| `vpd` | kPa | user. The `.ini`'s hPa is the half-hourly input; the daily range 0.14–3.91 is kPa, so flux-data-qaqc converted it |
| `ws_1_1_1` | m s⁻¹ | user; `.ini` WS m/s. Measurement height is still not recorded |
| `h_1_1_1`, `netrad_1_1_1_final` | W m⁻² (daily means) | user; `.ini` H, NETRAD in W/m² |
| `rh` | % | user; `.ini` RH_1_1_1 in % |

- **`ebr` and `ebr_5day_clim` stay `provisional`.** They are dimensionless, but the closure method and window are unresolved (contract §10).
- **RH is declared without bins.** VPD remains the declared humidity condition, and no bin is added after errors have been seen.
- **Wind direction** exists in the half-hourly input (`WD`), but not in the daily table. A daily wind-sector diagnostic needs a declared daily aggregation of direction upstream (for example a vector mean, or the daytime dominant sector), so it remains `not_available`.
- **Subdaily gap columns** are not described by the `.ini` and stay `unverified`.

Status changes only labels and flags (`variable_provisional` is dropped). Values, bins and edges are unchanged, because the confirmed units are the ones the edges were declared in. The diagnostics were rerun to update the labels.

---

**Revision 2026-10-17 (o):** Step 13 implemented. D-054 to D-058 added. Three corrupted footprint files per etcompare-built set were found in this checkout and restored (D-054); D-001 to D-053 are otherwise unchanged.

### D-054 — US-UTJ landscape inputs (Adopted 2026-10-17, user declarations)
The user was asked which land-cover and vegetation inputs exist. A read-only local search found candidates, and the user confirmed or corrected each one. Nothing was downloaded, and no service or credential was used.

| Input | Declaration | Evidence |
|---|---|---|
| Annual NLCD | `NLCD/Annual_NLCD_H09V10_LndCov_<year>_CU_C1V2.tif`, 1985–2025. Assets 2023, 2024 and 2025 are declared (vintage = year; reference period = that calendar year) | USGS Annual NLCD Collection 1 v1.2 (June 2026), tile H09V10, 30 m. CRS: Albers on WGS84, no EPSG code; declared as its PROJ string and checked against every header. Nodata 250 (header and XML). Legend: the 16 classes of the product XML |
| CDL 2024 | `2024_30m_cdls/2024_30m_cdls.tif`, vintage 2024 | EPSG:5070. No header nodata; code 0 is "Background", declared as nodata. Legend: the 133 named classes of the product's `.aux` Class_Names column (`examples/legends/cdl_2024_legend.csv`); crop names match the FGDC accuracy table. NASS: the 30 m 2024 CDL is a resampling of the official 10 m CDL |
| Field polygons | `Bluff.gpkg`, layer `bluff_fields`; tower field `fieldid 4940083` | 54 polygons, **no CRS in the file**. The user declared EPSG:26912 (NAD83 / UTM 12N) and the tower field. The tower falls inside the field, and its area (98,148 m²) matches the `acres` attribute (24.3). Vintage `unknown` |
| Vegetation index | None declared | A single Planet NDVI scene (`2024-02-17_strip_7114896_composite_ndvi.tif`, 3 m, `acquired` tag) was found. The user chose not to use it: it predates the record and covers only part of the footprint grid |
| Acquisition metadata | None (D-011 stands) | User, 2026-10-17 |

- **An earlier `NLCD/` folder was not NLCD.** It held the LCNext 1.0 stratum map (sampling strata, with change strata 100–104) and reference-plot tables. The user replaced it with Annual NLCD before any use.
- **Reference grid of the pixel supports:** `M:/My Drive/OpenET_Daily_Tiffs/unclipped/US-UTJ_US-CdM/ensemble/US-CdM_US-UTJ_ensemble_et_2025_06_15_2b83d.tif`. Its grid id `bc933ed96e16809f` equals the step-5 model grid. The Drive folder has moved to `unclipped/` since step 5, so `openet_rasters.root` in the example config is now stale (not changed here).
- **Corrupted footprints (D-043 again):** this checkout's `outputs/footprints` still held the three corrupted daily files (2024-12-05, 2025-06-20, 2025-06-21) in both etcompare-built sets.
  - Two of them were all-positive garbage (sums 1.6e308 and 1.6e307), which `prepare_footprint` accepted.
  - The clean versions from the other local clone (sum 1) were copied byte-identically, and the corrupted ones were kept outside the repository. Afterwards all 474 + 391 files match the other clone and pass a sum/range check.
  - The step 7–12 results are unaffected: their extraction tables had been copied clean from that clone (D-046).
  - **Rule (Adopted):** `prepare_footprint` now rejects values no footprint can hold (`invalid_values`): a normalized weight above 1, or a total that is not finite.

### D-055 — Landscape representativeness (Adopted rules; US-UTJ defaults Provisional)
`etcompare.representativeness.assess_landscape` describes the landscape of each support under a `RepresentativenessSpec` (the new `representativeness:` config section). It writes five tables (all new at v1): `landscape_class_fractions`, `landscape_index_summaries`, `landscape_method_differences`, `representativeness_labels` and `representativeness_availability`.

**Transfer.** Each support's cell-integrated weights move onto the layer's native grid by exact area overlap (D-028; polygon path for differing CRSs). Classes and index values never move: no categorical map is interpolated, and a 30 m class is never presented as 10 m information. Polygons are intersected exactly with the layer cells after their edges are densified for reprojection.

**Denominators** (as D-029; never multiplied together or with domain capture):
- `weight_grid_total`: the footprint-grid total, named by `weight_denominator` (`normalized_truncated_footprint` for normalized weights, D-008). Fixed supports use 1 (`window_pixels`, `polygon_area`).
- `valid_frac_of_grid_total` = weight on valid layer cells / grid total.
- `nodata_frac_of_grid_total` and `outside_layer_frac_of_grid_total` are reported explicitly; a `nodata` row carries the nodata weight.
- `class_frac_of_valid` = class weight / valid weight: conditional on valid cells, summing to 1 over the class rows.

**Continuous index.** `m = Σc·v / Σc` and `weighted_std = sqrt(Σc(v − m)² / Σc)` over valid cells: population form, no small-sample correction, because the weights are source fractions, not replicate counts. `n_eff = (Σc)² / Σc²` (Kish). The estimator is written on every row.

**Contours.** The membership comes from the footprint alone: cells in descending weight until the cumulative weight reaches the level × grid total, with ties at the threshold included (so the mass is ≥ the level). Contour rows (`domain = contour_<level>`) are a separate product next to `full_domain`. They never replace it, and the level is a fraction of the truncated grid total, not of the original footprint.

**Temporal matching.** Every asset has a reference period: the vintage year of a map, or the acquisition day of a scene. A footprint day is matched by a declared policy:

| Policy | Admissible assets | Choice |
|---|---|---|
| `previous_only` | reference period ends on or before the day | nearest |
| `nearest` | all | smallest \|days\|; a tie goes to the asset without later information |
| `within_n_days` | \|days\| ≤ n | as `nearest` |

- Every row records `days_from_layer` (day − end for past assets, 0 inside the period, day − start for future ones), `temporal_relation`, `footprint_year`, `layer_year`, `year_mismatch` and `retrospective`. `retrospective` is true when the asset's period ends after the day; this includes a same-year annual map, which is built from the whole year.
- A day without an admissible asset gets a per-day `not_available` row (`no_admissible_asset`) and is never filled.
- An undeclared or unknown policy, a missing legend, vintage or acquisition date, an undeclared nodata, a contour level outside (0, 1) and a CRS that differs from the header are all `ContractError`.

**Fixed supports and differences.** The pixel window (`pixel_window_<n>x<n>`) and nearest pixel come from the declared model grid. Window pixels outside the grid are counted, not added. Polygons are `polygon_<id>`.
- Fixed supports are evaluated on the footprint's **frozen days**: the day set of the footprint set, with the same asset (`day_set` key, `footprint_set_id = none`).
- `landscape_method_differences` holds footprint − support per day, layer, policy, footprint domain and quantity. A class absent on one side has share 0.
- `ET_mean` (`source_region_mean`) is a varying source region (D-007) and never appears as a fixed support.

**Labels.** The station's IGBP class is a site label (`station_metadata`), not a footprint-weighted fraction. Optional fluxfootprints labels (D-056) are stored next to the fractions. Labels never enter a metric, table value or threshold (tested: changing every label leaves all numeric tables identical). Neither the fractions nor the labels are ET accuracy guarantees.

**Keys.** The tables are keyed by analysis, site, date, day set, extraction method, footprint set, layer, matching policy, domain and class (labels: also label source and radius), so none of these can collide.

**US-UTJ defaults (Provisional; fixed before any landscape value was computed, and not chosen for agreement):**
- footprint set `US-UTJ_nldas_eto_20261014`, 2024-06-05..2025-10-09;
- matching policies `nearest` and `previous_only`, both reported, **none adopted**;
- contour level 0.8 (the 80 % contour of Chu et al. 2021);
- supports: 3×3 window and nearest pixel on the step-5 model grid, and the tower field polygon;
- `negative_weight_tolerance` 1e-9 (as D-027).

### D-056 — fluxfootprints representativeness labels (Adopted; installed 2026-10-17 with user authorization)
- **Inspection.** The sibling checkout `../footprints` is fluxfootprints 0.5.1 at commit `6b7311d10ba228c5c463dfbcba58c53867dd9588`. Its working tree differs only in a notebook. `fluxfootprints.representativeness` implements Chu et al. (2021): footprint-weighted versus target-disc composition, a chi-square test on pseudo-counts, and HIGH/MEDIUM/LOW at 80 %/50 % dominant-class cover and α = 0.05. Before step 13 it was installed in neither venv.
- **Installation.** `uv pip install -e ../footprints` into the etcompare venv. It is deliberately **not** a project extra. As an extra, its `numpy >= 2` requirement entered uv's universal resolution and raised the lowest-direct stack to numpy 2.0.0, which broke pyarrow 14 (D-018). `uv run` keeps it; an exact `uv sync` would remove it. fluxfootprints is GPL-3.0.
- **Adapter.** `etcompare.adapters.fluxfootprints_labels` is the only caller, and every import is deferred (the import gate now also blocks `fluxfootprints`, `xarray` and `pyogrio`).
  - It hands over the footprint contribution **already moved onto the map's native grid**, so fluxfootprints never resamples the map. Arrays are reordered to its `(x, y)` convention, with ascending offsets from the tower in metres (axis-aligned metric grid required).
  - Each label row records the version and commit, the upstream thresholds, the radii and α. It carries `label_not_accuracy_guarantee`.
- **Tested** with a fixture against the real API: `p_footprint` equals the analytic 0.8, `p_target` equals an independent disc count, and the label equals fluxfootprints' own `classify_categorical`. Without fluxfootprints the labels are `not_available` (`fluxfootprints_not_available`).
- **US-UTJ parameters:** the upstream defaults `TARGET_RADII` (250–3000 m) and α 0.05 on both maps. The p-value rests on pseudo-counts (upstream note), so it is a comparison aid, not a significance test.

### D-057 — Satellite support (Adopted rules; US-UTJ: not available)
`etcompare.satellite.assess_satellite_support` compares errors of one **frozen** cohort on clear-observation and interpolated days under a `SatelliteSupportSpec` (new `satellite_support:` section). It writes `satellite_support_errors` v1 and availability rows.
- **Sources.** Only authoritative product metadata is accepted: `per_pixel_flag`, `per_site_flag`, or `per_site_count` with a declared `min_clear_count`. Each source must declare its `authority` and `meaning`, and each model has at most one source.
  - Scene-level cloud cover is refused as a source (D-011).
  - A day absent from the metadata is `unknown`. Missing rows are not cloud evidence, filename and scene dates are labels, and a daily value or a non-null interpolated ET value is not a clear observation.
- **Subsets.** A day is `clear_observation` only when every cohort model is clear, and `interpolated` only when every model is interpolated. Both subsets therefore hold identical days for every model.
  - Days with mixed or unknown support are counted and excluded, and each model's own counts are recorded.
  - Metrics come from `compute_metrics` (tested against numpy). A subset below `min_subset_days` gets `sparse_subset` and no value. Cohorts that do not freeze models are refused.
- **Timing bins** (optional, edges from the configuration):
  - `days_since_previous_clear` uses no later acquisition;
  - `days_to_nearest_clear` and `enclosing_gap_days` use later acquisitions and are labelled `retrospective`.

  They are computed from the days on which every model is clear, over the whole support record.
- **Without sources** every analysis is `not_available` (`no_acquisition_metadata`).
- **US-UTJ** (`utj_satellite_support`; `min_subset_days` 20, Provisional, the step-12 floor): no source is declared. The four frozen ranking cohorts (`utj_rank_corrected_inclusive`, `utj_rank_corrected_measured`, `utj_pair_eemetric_ssebop`, `utj_set_a_common`) report `not_available` with zero error rows. The OpenET rasters have a value every day (interpolated daily, D-011), and that does not create overpass flags.

### D-058 — US-UTJ landscape run (Information)
From `tools/run_representativeness.py examples/us_utj.yaml` on 2026-10-17 (67 s). The outputs are the local `outputs/representativeness/utj_landscape_*` files. They describe what each support sees; they are not ET errors or accuracy guarantees.
- **Coverage.** All 474 NLDAS footprint days are usable (after D-054). Every weight lies on valid map cells (valid fraction 1, no nodata, nothing outside the windows). The 80 % contour holds 0.8007 of the grid total on average.
- **Footprint, full domain, mean share of valid weight** (min–max over days):

  | Map | Classes |
  |---|---|
  | NLCD | Cultivated Crops 0.394 (0.007–0.743), Woody Wetlands 0.570 (0.240–0.938), Shrub/Scrub 0.034 |
  | CDL 2024 | Alfalfa 0.696 (0.227–0.956), Shrubland 0.282 (0.042–0.770), Woody Wetlands 0.018 |

  The 80 % contour shifts both toward the tower: NLCD crops 0.376 and woody wetlands 0.623; CDL alfalfa 0.763 and shrubland 0.232.
- **Fixed supports:**

  | Support | NLCD | CDL 2024 |
  |---|---|---|
  | nearest OpenET pixel | Woody Wetlands 1.0 | Alfalfa 1.0 |
  | 3×3 OpenET window | Crops 0.285, Woody Wetlands 0.715 | Alfalfa 0.598, Shrubland 0.402 |
  | tower field 4940083 | Crops 0.900, Woody Wetlands 0.055, Pasture 0.027, Developed open 0.011, Shrub 0.007 | Alfalfa 0.878, Shrubland 0.114, Woody Wetlands 0.008 |

- **The two maps disagree on the strip east of the tower.** NLCD calls it woody wetlands, while CDL calls most of it shrubland and alfalfa. Even the tower's own OpenET pixel is woody wetlands in NLCD and alfalfa in CDL. This is a disagreement between 30 m products. It is not resolved here and is not evidence for either.
- **Footprint − support differences** (full domain, `nearest`, frozen days; mean, 5–95 %):
  - field polygon: NLCD crops −0.506 (−0.856 to −0.230), CDL alfalfa −0.182 (−0.480 to +0.037);
  - 3×3 window: NLCD crops +0.108 (−0.241 to +0.385), CDL alfalfa +0.097 (−0.201 to +0.316).

  The footprint is never the field: on average it places about 30 % (CDL) to 60 % (NLCD) of its valid weight outside crop or alfalfa classes.
- **Years.** 2025 footprints see more crop than 2024 ones (NLCD crops 0.434 vs 0.334; CDL alfalfa 0.716 vs 0.666). NLCD changes in only 4 (2023→2024) and 12 (2024→2025) of 2,809 window cells, so the difference comes from the footprints and the different day sets (2024 covers June–December only).
- **Matching:**
  - NLCD `nearest` uses the same-year map, which is retrospective on 473 of 474 days (all but 2024-12-31). NLCD `previous_only` uses the previous year's map (year mismatch 1). Their means agree to 3 decimals because the map is stable.
  - CDL has only 2024. `nearest` uses it on every day: 191 days of 2024 are retrospective, and the 2025 days have year mismatch 1. Under `previous_only` the 191 days of 2024 are `not_available`, and 283 days remain.
- **fluxfootprints labels:** 20,459 `low` and 1 `medium` (CDL, `nearest`, contour, 250 m). Labels only.
- **Independent checks:**
  - Point sampling of the footprints at 8×8 points per 10 m cell, looked up in each map, reproduces the footprint shares to ≤ 2.7e-4 on three days and both maps.
  - A 200×200 lattice per OpenET pixel reproduces the window shares (0.2852 and 0.5984).
  - Monte Carlo inside the field polygon agrees to ≤ 0.001.
  - fluxfootprints' `p_footprint` equals etcompare's dominant-class share to 1e-6.
- **Restated:**
  - IGBP `CRO` is a station-metadata label, not a footprint-weighted fraction.
  - No acquisition flags exist (D-011), so satellite support is not available (D-057).
  - No intervals are attached to the step-13 outputs. Where step-10 intervals are used elsewhere, 28-day blocks mostly lack support and the 7- and 14-day intervals are probably too narrow (D-048).
  - Gap-filled reference days share gridMET drivers with the OpenET interpolation (D-005).

What would change these results:
- a field-boundary vintage;
- a resolution of the NLCD/CDL disagreement (for example a 10 m CDL or a site survey);
- a declared vegetation-index series with acquisition dates;
- authoritative acquisition metadata;
- a decision on the matching policy and contour level.

None is to be chosen because it changes agreement.

### D-059 — US-UTJ cutting dates come from the station event log (Adopted 2026-10-17, user-provided; revised the same day)
The user stated that the crop-height model's cutting dates come from farm records and photos, and then supplied the station event log `eddy_events_202610171141.csv` (local only, D-014; sha256 `ea3ca891…f34698`; 513 events across 16 stations, 34 for US-UTJ). The six height resets of the zone-E crop-height model (D-042) **match the log's US-UTJ `cutting` events day for day**. The log records the evidence for each event, and it is not the same for all six:

| Cutting | `event_source` | Observer | Note |
|---|---|---|---|
| 2024-06-11 | Planet imagery | PCI | |
| 2024-07-19 | Planet imagery | PCI | |
| 2024-09-08 | camera | DM | "date approximate; hay bales on 2024-09-11" |
| 2025-05-28 | camera | DM | bales by 06-01, removed 06-02 |
| 2025-07-05 | Planet imagery | PCI | |
| 2025-09-07 | camera | DM | baled 09-10, gone by 09-11 |

- **Evidence class for step 14:**
  - `camera` events are photo observations: management-record evidence.
  - `Planet imagery` events are inferred from satellite imagery. Planet is not an OpenET input, so they are not circular with OpenET. They are still remote-sensing inference, not a field record, and step 14 must label them as such.
  - No US-UTJ event is tagged as a farm record, and 2024-09-08 is approximate by its own note (cut a few days before 09-11). Step 14 needs to know which, if any, of these the farm records also confirm.
- **Not in the log for US-UTJ:** irrigation events (the log has `pivot irrigation` events only for other stations) and the four 2023 cuttings before the record (Planet imagery; outside the record).
- **Station events that touch earlier steps** (noted, not acted on):
  - 2025-07-15: the IRGASON was moved to 9.9 ft at azimuth 293°. Footprint heights come from `daily_footprint_parameters` (D-037), and whether that table carries the change is unverified.
  - 2025-12-01: the precipitation bucket factor was changed from 0.1 to 0.254 mm per tip. The station `p_1_1_1` in the record may therefore be scaled low; it is not used by any analysis so far.
- **For step 14:** read events from this log through a declared adapter. Keep `event_source`, observer and notes on every event, use only US-UTJ `cutting` events inside the record, and never derive events from model residuals.

---

**Revision 2026-10-17 (p):** Step 14 started: the event table and its adapter. D-060 added. No event analysis or anomaly has been computed.

### D-060 — Station event tables (Adopted rules; US-UTJ declarations Provisional)
`etcompare.adapters.event_log.adapt_event_table(source, spec, *, stations)` maps a declared event log (`event_tables:` section, `EventTableSpec`) to the new `station_events` table (v1, key `site_id` + `event_id`). It takes no model, residual or cohort input: **events are defined only from the log**, and nothing downstream may add or move one.
- **Refused** (nothing is returned):
  - a missing source file or mapped column;
  - a null or duplicated event ID anywhere in the log;
  - for rows of mapped stations: a null timestamp, a timestamp that does not match the declared format, a timezone-aware timestamp, an end before the start, an `event_type` that is not declared, and a non-null `event_source` without a declared evidence class.
  - In the declaration: no evidence mapping for a value, an unknown category, evidence class or day rule, a management type without a `management_kind` (or a non-management type with one), a mapped site without a record window, and missing `approximate_date_patterns`.
- **Station day.** `day_rule: station_local_date`: the timestamp's calendar date is the station day, and `timestamp_timezone` must equal the station's timezone (checked by the config and the adapter). No conversion is implemented, so a log in another timezone is refused rather than shifted. An entry logged at exactly 00:00 is date-only (`start_time` null); such entries could not be shifted correctly in any case.
- **Excluded with a reason** (exclusion log, stage `event_adapter`):
  - rows of stations outside `site_mapping` (one record per source station; their types and sources are not read or checked);
  - events lying entirely outside the station's declared record window (`outside_station_record`). An event that crosses a window edge is kept with its logged dates and a warning.
- **Kept on every event:**
  - `observer`, `event_source` (as logged, null when unlabelled) and `event_notes`, plus every source column with the prefix `src_` (D-020);
  - the declared `category` (`management`, `instrument`, `confounder`) and `management_kind`;
  - `analysis_role`: `management_event`, or `overlap_only` for instrument and confounder events, which stay visible but are never analysed as treatments;
  - `evidence_class`, mapped per `event_source`: `photo_observation`, `remote_sensing_inference`, `station_log`, `management_record` or `unknown`. An unlabelled source is always `unknown`.
- **Approximate dates.** `approximate` is True only when a declared, case-insensitive regular expression in `approximate_date_patterns` is found in the notes; `approximate_rule` names it. The patterns must refer to the date: the log also says "depth approximate" (US-UTL) and "time estimated" (US-UTE), and neither matches.
- **US-UTJ declaration** (`utj_station_events`, Provisional):
  - The timestamps are in UTC−7 (user-provided).
  - Record window 2024-06-05..2025-10-09 (the station daily table).
  - Types: `cutting` is management (`cutting`); `station visit` and `program update` are instrument.
  - Evidence: `camera` → photo observation; `Planet imagery` and `Sentinel NDVI` → remote-sensing inference; `eddy`, `met`, `both` → `station_log`. These three name the logger a program update went to; this class was added here because the log uses them and every source needs a class.
  - Patterns `\bdate\s+(?:is\s+)?approximate\b` and `\bapproximate\s+date\b`.
- **US-UTJ result** (local log, sha256 `ea3ca891…f34698`):
  - 17 events kept: the 6 cuttings of D-059 and 11 instrument overlaps, among them the 2025-07-15 IRGASON move (13:36). The cuttings match the crop-height resets day for day (tested).
  - Evidence: 3 cuttings are `remote_sensing_inference` (2024-06-11, 2024-07-19, 2025-07-05) and 3 are `photo_observation`. Only 2024-09-08 is approximate.
  - 17 US-UTJ entries are excluded as outside the record: the four 2023 cuttings and 13 entries after 2025-10-09. All 479 rows of the 16 other source stations are excluded as undeclared.
  - The event_source values are kept exactly as logged. **No event is labelled `management_record`** until the user says which cuttings the farm records confirm (Open). *Resolved the same day (D-061): the farm records confirm all six.*
  - There are no irrigation events, and none is inferred from ET, soil moisture or notes. A 2026 note ("will start irrigating in two days") lies outside the record and is not an event.
- **Not yet implemented:** pre/post windows, baselines, cumulative response, recovery, overlap and confounder flags, and anomalies. These need declared settings; none has been chosen. *Implemented the same day (D-061).*

---

**Revision 2026-10-17 (q):** Step 14 completed. D-061 added. The user confirmed that the farm records cover all six cuttings and asked for provisional defaults, to be changed later.

### D-061 — Event responses, anomalies and US-UTJ defaults (Adopted rules; US-UTJ defaults Provisional, chosen by the assistant at the user's request)
**Management records.** `event_tables[].management_records` lists the events that a record confirms, with `record`, `confirmed_by` and `confirmed_on`. The adapter sets `management_record` (and `management_record_source`) on those events only. It refuses an ID that is not a kept management event, and an event listed twice. The logged `event_source` and its evidence class are unchanged. US-UTJ: farm records confirm all six cuttings (user, 2026-10-17). They keep their logged evidence (3 Planet, 3 camera), and 2024-09-08 stays approximate because the confirmation gives no exact date.

**Event responses** (`etcompare.events.analyse_events(cohort, events, spec, *, station_daily=)`; `event_analyses:`; tables `event_responses`, `event_overlaps` and `event_conditions`, all v1):
- **Events** must be a `station_events` table from the adapter (`events_defined_from = event_log_only`), so a table built from residuals is refused. Only `management` events of the declared kinds are analysed; instrument and confounder events appear only in `event_overlaps`.
- **Windows** are calendar days around the event: pre `−pre_days..−1` from the start, post `post_offset..post_offset+post_days−1` from the end, and recovery up to `recovery_max_days`. Only cohort days have values: a gap is never a zero, and a frozen cohort gives every model the same days.
- **Per series** (observed, modeled, and `modeled_minus_observed`):
  - baseline and post means, each only when at least `min_window_completeness` of the window's days are present (else `incomplete_baseline` / `incomplete_post_window`);
  - change, and relative change only for a baseline above `baseline_min_mm`;
  - cumulative response `Σ(ET − baseline)` only when every post day is present;
  - recovery: the first trailing `recovery_smoothing_days` mean, lying wholly after the post offset, that reaches `recovery_fraction × baseline`. A gap before recovery gives `not_identifiable_gap`, a later management event `interrupted_by_management_event`, and no recovery by the horizon `not_recovered_within_horizon`.
  - With `normalize_by`, the same means of ET divided by that variable. This shows how much of a change follows the weather; it is not a correction.
- **Visible confounding:**
  - `event_conditions` gives the declared weather variables (mean or sum) over the pre and post windows of the station record.
  - Overlapping events of every category are listed, with their window (pre/event/post/recovery).
  - Gap-filled reference days are counted per window and flagged `shared_gridmet_drivers` (D-005).
- **Design:** `before_after_uncontrolled` on every row, with the flag `not_causal_evidence`. No control or BACI design exists for US-UTJ, so none is implemented. A before/after difference is never a water-saving claim.
- **Gate (tested):** a synthetic irrigation cutoff of −3 mm/day gives change −3, −60 % and a cumulative response of −21 mm. Incomplete baselines, incomplete post windows and overlapping events are flagged.

**Anomalies** (`etcompare.anomalies.compute_anomalies(cohort, spec, metric_spec)`; `anomalies:`; tables `anomaly_daily` and `anomaly_metrics`, v1). The same method is fitted separately to the observed and modeled series, and raw metrics are reported on the same days next to the anomaly metrics.
- **`held_out_climatology`:** year or water-year folds. Each fold's climatology is fitted on the other folds only, as a circular ±`climatology_half_window_days` day-of-year mean on a 365-day calendar (29 February takes 28 February's day).
  - Sparse-season rule: a day needs `min_training_values` from `min_training_years` distinct training folds; otherwise it gets no value.
  - Metrics are given per fold and pooled (`all_folds`).
  - Tested: perturbing a held-out year leaves its climatology unchanged.
- **`exploratory_moving_mean`:** a centred calendar moving mean of the series itself, needing `min_window_completeness`. It is in-sample: `inference = exploratory`, flagged `no_seasonal_generalization`.

**US-UTJ defaults (Provisional).** They were chosen before any response was computed and not tuned on agreement:
- **Event analysis `utj_cutting_response`** (cuttings only):
  - pre 7 days; post days 1–7 (the part-cut day 0 is left out); completeness 0.7 (≥ 5 of 7 days); `baseline_min_mm` 0.5;
  - recovery to 0.9 of baseline with a 3-day trailing mean, searched for 28 days (shorter than the shortest cutting interval, 38 days);
  - approximate dates flagged, not excluded;
  - weather: gridMET ETr (mean; also `normalize_by`) and gridMET precipitation (sum);
  - cohorts: the two all-model ranking cohorts, the eemetric/ssebop pair and per-model coverage (flagged `days_differ_across_models`).
- **Anomalies:**
  - held-out climatology on water years, ±15 days, ≥ 20 values from ≥ 2 training years;
  - exploratory 31-day moving mean with completeness 0.7;
  - `min_days` 20.

**US-UTJ results** (from `tools/run_events.py`, 4 s; local `outputs/events/`). These are descriptive, from one station and an uncontrolled before/after design.
- **Usable cuttings:**
  - 2024-06-11 has no complete window in any cohort (record start, rejected footprints).
  - 2024-07-19 and 2024-09-08 are usable only in the eemetric/ssebop pair and the coverage cohorts, because the all-model cohorts cover 2025 only.
  - **2025-05-28 lies in May 2025, where every reference day is gap-filled** (14 of 14 window days; `gridMET_ETr × ETrF_filtered`). Its observed "response" (−0.15 mm/day, recovered in 3 days) is the smoothed gap-fill, not an observation of the cut. It is flagged `shared_gridmet_drivers`, and in the measured-reference cohort it is `incomplete`.
- **Observed post-cut change** (corrected reference, mm/day): 2024-07-19 −3.7 to −3.8; 2024-09-08 −2.37 (approximate date); 2025-07-05 −2.55 (6.87 → 4.32, ET/ETr −0.28, recovered after 21 days); 2025-09-07 −1.83 (not recovered within 28 days, late season).
- **Models show a smaller drop after every measured cutting.** Modeled − observed change is positive in all of these cases:
  - 2025-07-05: +0.72 (ssebop) to +2.70 (geesebal, ptjpl);
  - 2025-09-07: +0.15 (geesebal) to +1.53 (disalexi);
  - 2024-07-19: +1.84 to +2.30 (eemetric, ensemble, ssebop);
  - 2024-09-08: +0.20 (ssebop) to +1.42.

  Several models recover 16–18 days sooner than the station after 2025-07-05. This is consistent with interpolated daily products smoothing between acquisitions (D-011). It is not tested here, because acquisition dates are unknown.
- **Independent check:** recomputing the 2025-07-05 and 2025-09-07 baseline, post mean, change, ET/ETr change, cumulative response and recovery day directly from `US-UTJ_daily.csv` reproduces the table exactly.
- **Anomalies:**
  - The held-out climatology is `insufficient_training_years` for the 2025 cohorts (two water-year folds). It is sparse or empty for the eemetric/ssebop pair, whose three water-year folds never cover a day of year from two other folds.
  - **No held-out anomaly skill exists for US-UTJ.**
  - The exploratory moving-mean anomalies (in sample) remove the raw bias: for example, ssebop has raw MBE −0.91 and anomaly MBE ≈ 0 mm/day on the corrected-inclusive cohort. Anomaly r is 0.46–0.83 there and 0.11–0.61 on measured days only. These figures describe short-term agreement, not generalisation.

What would change these: other window lengths, completeness or recovery settings (they must be redeclared before rerunning, never chosen for agreement), a control site or period, more years of record, and acquisition metadata.

---

**Revision 2026-10-17 (r):** Step 15 implemented. D-062 to D-064 added. No input was downloaded, no service or database was queried, and no credential was used. The station gauge `p_1_1_1` and the half-hourly logger files were not used. D-001 to D-061 are unchanged.

### D-062 — Water-balance plausibility checks (Adopted rules; US-UTJ declaration Provisional)
`etcompare.water_balance.compute_water_balance(spec, station_daily, *, period_aggregation, station)` evaluates one `WaterBalanceSpec` (the new `water_balances:` section). It writes `water_balance_terms` and `water_balance_residuals`, both v1.
- **Equation and signs.** `equation: soil_zone` is `residual = P + I + capillary_rise − ET − runoff − deep_percolation − ΔS`, with `sign_convention: inflows_positive`. The equation text is recorded on every residual row and in the table attributes.
- **Every term is declared.** All seven terms must appear; leaving one out raises an error. Each term has a `status` and its own source:

  | Status | Source | Mandatory fields |
  |---|---|---|
  | `measured`, `modeled` | `station_column` (a `station_daily` column, e.g. `src_gridMET_prcp`), `station_et` (ET only) or `table` (CSV/Parquet) | `spatial_support`. Columns and tables also need `units` (a daily depth; a bare `mm` is refused, D-022) and `day_definition` (may be `unknown`). `station_et` needs `reference_variant` |
  | `scenario` | `scenario` | a constant `daily_mm`, a `justification` and the `spatial_support` it assumes |
  | `unknown` | `none` | a `reason` |

  Fields that do not belong to the source are refused.
- **Unknown is never zero.** An unknown term has no value. The residual is null (`status = unknown`), and its `reason` and `unknown_terms` list every unknown term.
- **Scenario residuals are separate.** Each period has two rows: `measured_modeled` (only measured and modeled terms) and `scenario` (at least one scenario term). A scenario term makes the `measured_modeled` residual unknown, with a pointer to the scenario row. The scenario row is `not_applicable` when no scenario term is declared, and it carries `scenario_assumption`.
- **Spatial support.** `spatial_support` is one of `boundary`, `station_point`, `tower_footprint`, `gridded_cell`, `model_pixel` and `regional`.
  - Only `boundary` covers a polygon boundary; a point boundary also matches `station_point`.
  - Every other support is flagged `support_mismatch` on the term row and listed in `support_mismatch_terms`. It is never treated as equal.
  - The residual depth is still computed when every term is known, so the mismatch stays visible rather than hidden.
  - A tower footprint is refused as a boundary: it changes every day and is not a field area.
  - Irrigation with `regional` support is refused: a regional diversion is not field irrigation.
- **Periods and completeness (D-041).** The periods are those of the declared step-8 `period_aggregation` (optionally a subset), built by `aggregation.period_calendar` with the same calendar denominators (leap days included, whole periods listed, `period_outside_record` flagged).
  - A flux term has a `total_mm` only when every calendar day has a value. Otherwise only `partial_sum_mm` is reported, with `n_values_present` / `n_values_required`, and `total_status = incomplete_period`.
  - A storage term (`quantity: storage_state`, mm at `end_of_station_day`) gives ΔS = S(end) − S(day before start) only when both states exist.
  - A residual needs a complete total of every term, and the incomplete terms are named in `reason`.
  - Growing seasons from a crop-height table must be attached first (`with_growing_seasons`), as in step 8.
- **Station ET.** The ET term names its reference variant (D-004). Gap-filled days are counted per period (`n_days_et_gap_filled`) and flagged `includes_gap_filled_et_days` and `shared_gridmet_drivers` (D-005).
- **Day definitions.** A column or table term whose `day_definition` is `unknown` or differs from the station's is flagged `day_alignment=unverified`, on its rows and on the residual. Nothing is shifted (D-002).
- **Volumes.** `volume_m3 = depth_mm / 1000 × area_m2`. A volume is given only when:
  - the declaration sets `volumes: true`; this is refused at load time unless the boundary has a declared `area_m2` and `area_source`;
  - the boundary has a known area; a point boundary never has one, and `depth_to_volume_m3` refuses otherwise;
  - the term (or, for the residual, every term) covers the boundary. A footprint or gridMET depth is not a volume of the field (`volume_status = support_mismatch`).
- **Interpretation.** A residual is a plausibility diagnostic. It holds every unmeasured exchange and the errors of every term. It is never assigned to ET error and never used to choose a model, reference, filter or footprint.
  - Every row carries `plausibility_diagnostic_only` and `not_et_error`, plus an `interpretation` column.
  - `compute_water_balance` takes no cohort or model input, and no module except `etcompare.scenarios` imports `water_balance` (both tested).
- **Gate (tested, `tests/test_water_balance.py`, 47 tests):**
  - a closed synthetic balance gives exactly 0 mm and 0 m³ in every month;
  - an unknown inflow gives a null residual with every unknown term listed;
  - support mismatches are flagged, and no boundary volume is given for them;
  - volumes are refused without a known area (point, and polygon without area);
  - also: incomplete months, storage states, gap-filled ET counts, day alignment, scenario residuals, step-8 denominators, a Parquet round trip and 28 declaration refusals.

### D-063 — Scenario uncertainty (Adopted rules; none declared for US-UTJ)
`etcompare.scenarios.run_scenario(scenario, water_balance, station_daily, *, period_aggregation, station)` propagates one `ScenarioSpec` (the new `scenarios:` section) and writes `scenario_results` v1.
- **Four kinds, never merged.** `UNCERTAINTY_CATEGORIES` = `sampling`, `measurement`, `footprint_parameter` and `method_choice`.
  - `sampling` is the step-10 bootstrap (`intervals` table) and is refused as a scenario category.
  - Each scenario has exactly one category, and its rows carry `uncertainty_category`, `uncertainty_label`, an `interpretation` and the flag `not_bootstrap_interval`. No interval combines two categories.
- **`measurement`: declared distributions only.** Each perturbation declares:
  - a term;
  - a family (`normal`, `lognormal`, `uniform` or `triangular`) with its parameters;
  - a `mode` (`multiplicative`, or `additive` in mm d⁻¹ per day, or mm for ΔS);
  - a `temporal_dependence` (`systematic`: one draw per realization for every day; `independent_daily`: one draw per station day);
  - a `justification`.

  The scenario also declares `dependence.across_terms` (`independent` or `gaussian_copula`) with `correlations` and a justification. Correlations must be positive definite, and correlated terms need the same temporal dependence. `seed`, `n_draws` and `quantiles` are mandatory. There is no default distribution.
  - Draws use `numpy.random.default_rng([seed, hash(scenario_id)])`, systematic first. The same seed and declaration reproduce every draw (tested).
  - Rows hold `base`, `mean`, `sd` and the declared quantiles, with the seed, the number of draws and the declaration's SHA-256. The declaration itself is in the table attributes.
- **Refused:**
  - a perturbation of an `unknown` term (a distribution cannot give it a value; declare a scenario term first);
  - a daily perturbation of a storage state.

  A period whose base residual is unknown stays unknown in every draw. Perturbations never change which days are present, so completeness stays as declared.
- **`method_choice`: deterministic alternatives.** Named alternatives redeclare terms (for example another ET reference variant), each with a justification. The rows are `alternative=declared`, `alternative=<name>`, `min` and `max`, flagged `not_a_probability_distribution`. A range exists only when every alternative has a residual. The corrected/raw ET difference and model spread can enter only this way, never as a distribution.
- **`footprint_parameter`: supplied realizations only.** At least two alternatives are required, each redeclaring terms with `table` sources (term series computed from regenerated footprints), plus a `realization_source`. Distributions are refused for this category, so noise is never added to normalized footprint pixels. The rows are `realization=<name>`, `min`, `median` and `max`.
- **Gate (tested, `tests/test_scenarios.py`, 35 tests):**
  - the same seed reproduces the table and the draws, and another seed differs;
  - results are labelled apart from the bootstrap `intervals` schema, and `sampling` is refused;
  - draws are checked analytically: systematic multiplicative precipitation equals `P_total·(x − 1)`, and February scales by 29/31; independent daily additive ET equals `−Σ x_d`; the storage offset; the copula correlation (0.8 ± 0.03) and its independence;
  - an unknown base stays unknown;
  - method-choice ranges, and footprint realizations with refusals of non-table and noise declarations;
  - 20 declaration refusals and the copula checks.

### D-064 — US-UTJ tower-field water balance (Information; declaration Provisional, chosen by the assistant)
`utj_tower_field_balance` in `examples/us_utj.yaml`, run by `tools/run_water_balance.py` (about 1 s; local `outputs/water_balance/`).
- **Boundary.** The tower field `fieldid 4940083` of `Bluff.gpkg` (EPSG:26912 user-declared; vintage `unknown`, flagged `boundary_vintage_unknown`).
  - `area_m2` 98,147.74 is the polygon's planar area in the declared CRS, equal to its `shape_area` attribute.
  - The `acres` attribute (24.3) is 98,339 m², which agrees to 0.2 % (rounding).
  - `volumes: true`.
- **Terms:**

  | Term | Declaration | Note |
  |---|---|---|
  | precipitation | `modeled`, `src_gridMET_prcp`, mm/day, `gridded_cell`, day definition `unknown` | gridMET cell at the station (complete, 492 days); units Provisional |
  | irrigation | `unknown` | no depths, timing or diversion records; the log has no US-UTJ irrigation events. Nothing is inferred from ET, soil moisture, cuttings or residuals |
  | capillary_rise, runoff, deep_percolation | `unknown` | no input |
  | et | `measured`, `station_et`, `et_corrected_inclusive`, `tower_footprint` | the only variant with complete months; gap-filled days are counted |
  | storage_change | `unknown` | the log mentions CS655 and SoilVue sensors, but no daily series, depth or units was supplied, and the half-hourly logger files were not read |

  **Not used:**
  - `p_1_1_1` (237 of 492 days), because its bucket factor was changed after the record (D-059). Using or rescaling it is the user's decision.
  - Any downloaded or queried input.
- **Result: the water balance is unknown.**
  - All 24 `measured_modeled` residuals (months, growing seasons, years, water years) are null. Every reason lists irrigation, capillary rise, runoff, deep percolation and storage change.
  - All 24 scenario residuals are `not_applicable`.
  - No volume is given: precipitation and ET both mismatch the field's support, and no residual exists.
- **Complete term totals** (reported as term totals only):
  - every month from 2024-07 to 2025-09, and WY2025: ET 888.9 mm with 127 of 365 days gap-filled, and gridMET precipitation 131.9 mm;
  - May 2025 is complete but entirely gap-filled (31 of 31 days, D-061);
  - 2024-06, 2025-10, both calendar years, GS2024, GS2025, WY2024 and WY2026 have partial sums only.

  No difference of the two totals is reported. It would omit five unknown terms, mix a footprint support with a gridMET cell, and could not be read as irrigation or as an ET error.
- **Independent check** (local integration test): July 2025 and WY2025 totals and gap-filled counts recomputed from `US-UTJ_daily.csv` with pandas match. The polygon area recomputed with geopandas matches.
- **Open, for the user:**
  - irrigation records (depths, timing, diversions for this field);
  - a soil-moisture series (sensor, depth, units, and permission to read the logger files);
  - whether and how `p_1_1_1` may be used;
  - the ET variant;
  - the gridMET precipitation units and day definition;
  - the boundary area and vintage;
  - any runoff, deep-percolation or capillary-rise input or scenario;
  - any measurement, method-choice or footprint scenario.

---

**Revision 2026-10-18 (s):** Step 16 implemented. D-065 to D-067 added. The user made every ensemble choice in D-066 on 2026-10-18, before anything was fitted. No input was downloaded or fabricated, and no step-14 or step-15 result was read. D-001 to D-064 are unchanged.

### D-065 — Held-out ensemble experiments (Adopted rules)
`etcompare.ensemble.run_ensemble(spec, observations, extractions, *, products, metric_spec, bootstrap_spec=None, footprint_sets=None)` runs one `EnsembleSpec` (the new `ensembles:` section). It writes eight new v1 tables: `ensemble_days`, `ensemble_predictions`, `ensemble_folds`, `ensemble_weights`, `ensemble_inner_scores`, `ensemble_training_membership`, `ensemble_summary` and `ensemble_improvement`.
- **Three products on the same frozen days:**
  - the downloaded **official** ensemble;
  - the unweighted **mean** and **median** of the declared members;
  - optionally a **fitted weighted** ensemble, whose every prediction is held out.

  Each scope becomes a `common_date` cohort built by `build_cohort` from derived extraction rows. It is scored by `score_comparison`, resampled by `bootstrap_comparison` and contrasted by `rank_comparison`. No metric is re-implemented. The scopes are `baseline` (official, mean, median; nothing fitted, so an evaluation, not a held-out result) and one per fitted design (the same products plus `fitted_weighted`, on its held-out days only).
- **Post-extraction only.** Members are combined from their extracted values on one declared side. This is not a pixel-level ensemble; every row carries `post_extraction_ensemble` and `not_pixel_level_ensemble`, and `ensemble_level = post_extraction`.
- **The official ensemble is never a member.** It is refused by the spec (as `official` and member at once), by the config loader and at run time (any product with `is_ensemble`).
- **Target-dependent footprints are refused.** A side whose footprint set is measured-ET weighted, or whose weighting is not supplied, never enters an ensemble experiment (D-037).
- **Missing members**, a policy fixed in advance:
  - `require_all`: the day is eligible only with every member, and ineligible days are counted (`member_missing`).
  - `reweight_available` with `min_members`: the mean and median use the available members, and fitted weights are renormalised over them. Those rows are flagged `reweighted`, and the folds count them. Training rows are always complete days.
- **Disagreement, not uncertainty.** `member_range_mm` and `member_sd_mm` (population SD) are labelled disagreement and flagged `disagreement_not_confidence_interval`.
- **Folds:**
  - `leave_one_year_out`: groups are calendar years or water years (`fold_unit`), shared across stations, so another station's record never reveals the held-out year.
  - `leave_one_site_out`: groups are stations. With one station it is `not_available`.
  - Random daily splits do not exist as an option.
- **Training rows** are complete-member reference days of the other folds:
  - Gap-filled reference days are excluded unless `gap_filled_training` (`et_corrected_inclusive` only; D-005).
  - For temporal folds, days within `buffer_days` of the held-out fold's calendar period are removed (`buffer_excluded`).
  - Training folds with fewer than `min_training_days_per_fold` days are left out (`training_fold_too_small`).
  - A fold is fitted only with at least `min_training_folds` (≥ 2) training folds, so its inner validation is grouped. Otherwise it is `declined` with the counts.
  - Every candidate day's role in every fold is in `ensemble_training_membership`.
- **Weights.** `simplex_least_squares` finds weights ≥ 0 summing to 1 that minimise the squared error. It solves every member support exactly (2^K − 1 KKT systems, K ≤ 12) and keeps the best feasible one; ties go to the solution nearest equal weights. `preprocessing: none`: no scaling, centring or climatology, so nothing can be learned from held-out rows.
- **Regularisation:**
  - `shrink_to_equal`: w = (1 − α)·w_fit + α/K, where α comes from a declared grid that must include 1.
  - α is chosen by inner grouped leave-one-fold-out validation over the training folds only, with the buffer applied around every inner held-out fold too. The criterion is pooled RMSE from `compute_metrics`, and ties go to the larger α.
  - **α = 1 is the simple mean bit for bit:** equal weights take the mean's own arithmetic, so round-off can never create a spurious contrast (tested).
- **Too little independent data:** `insufficient_data: decline` gives no weights. `exploratory` gives in-sample unregularised weights only, labelled `exploratory_in_sample` and `no_held_out_claim`. They are never held-out predictions and never enter a comparison.
- **Held-out improvement** (`ensemble_improvement`) is read from the step-11 contrasts `metric(fitted) − metric(product)` on held-out days, per station, block length and interval method:
  - `supported` only when the interval excludes zero in the fitted ensemble's favour;
  - `not_supported` when the interval includes zero or favours the other product;
  - `not_assessed` without an interval (`insufficient_support`, `not_resampled`).

  Every contrast is exploratory (D-049), and 7- and 14-day intervals are probably too narrow (D-048).
- **No selection by residuals.** `run_ensemble` takes no event, residual, anomaly or water-balance input, and the module imports none of those modules (tested).
- **Gates (tested, `tests/test_ensemble.py`, 61 tests):**
  - A member that is excellent in 2021–2022 and biased by +4 mm d⁻¹ in 2023 gets weight > 0.5 when 2023 is held out. It beats the mean on the training years but is worse on 2023, and no improvement over the mean or median is reported. A positive control (a consistently better member) is `supported`.
  - Perturbing a held-out year (×3 plus noise, members replaced) leaves that fold's weights, α, inner scores and training membership identical, while the other folds change.
  - The official ensemble is refused as a member (spec, config and run time).
  - The grid {1} reproduces the mean to 0 (atol 1e-12), with contrasts of 0 that are not supported.
  - `require_all` drops and reports a day with a missing member. `reweight_available` gives (w_a·a + w_b·b)/(w_a + w_b) and the mean of the available members, flagged and counted.
  - Two calendar years give `declined` with no weights, predictions or comparison; one year with `exploratory` gives in-sample weights only.
  - The same seed reproduces the intervals, and another seed differs.
  - Also tested: the exact 28-day buffer dates (2021-12-04..31 and 2023-01-01..28 for fold 2022); the training-fold size rule; gap-filled exclusion; leave-one-site-out on three synthetic stations; frozen product days with metrics equal to `compute_metrics`; disagreement equal to the member max − min and SD; the solver against 5,000 random simplex points and every vertex; target-dependent footprints refused; the signature and imports; Parquet round trips; 33 config refusals.

### D-066 — US-UTJ ensemble declaration and run (Adopted 2026-10-18, user decisions; Information)
`utj_ensemble` in `examples/us_utj.yaml`, run by `tools/run_ensembles.py` (about 4 s; local `outputs/ensembles/`).

**User decisions (2026-10-18, before fitting):**

| Choice | Decision |
|---|---|
| Members | disalexi, eemetric, geesebal, ptjpl, sims, ssebop; `ensemble` is the official comparison product |
| Reference | `et_corrected_measured`, so no gap-filled row is used anywhere |
| Missing members | `require_all` |
| Side | primary `footprint_conservative` on `US-UTJ_nldas_eto_20261014` (D-037) |
| Measured-LE footprints | never used |
| Fold unit | calendar year. **Adopted as the project standard** (user, 2026-10-18): the focus is the April–October growing season, and a water year (October start) would split that season at its end |
| Buffer | 28 days (residual autocorrelation lasts beyond 14 days, D-048) |
| Minimum training | 2 training folds, each ≥ 180 days after the buffer |
| Regularisation | `shrink_to_equal`, α ∈ {0, 0.25, 0.5, 0.75, 1}, inner grouped RMSE |
| Insufficient data | `decline` |
| Other stations | inputs exist (user) but were not supplied; leave-one-site-out is built and tested synthetically |

**What the record supports** (primary extraction):
- Coverage: eemetric, ssebop and ensemble have 474 days from 2024-06-05; disalexi, geesebal and ptjpl 282, and sims 281, from 2025-01-01. In set A the four 2025-only models start on 2025-01-03. sims misses 2025-08-02.
- All six members coexist on 281 days (2025-01-01 to 2025-10-09). With the measured reference that is **188 days, 2025-01-01 to 2025-10-08**. The other 115 measured days (2024) have two members only. sims' missing day has no measured reference, so `require_all` drops no 2025 day.

| Design | Folds with six-member measured days | Result |
|---|---|---|
| Leave-one-calendar-year-out (declared) | 2025 only (188) | **declined**: 0 training folds |
| Leave-one-water-year-out (checked, not declared) | WY2025 (180), WY2026 (8) | **declined**: WY2025 has no training fold ≥ 180 days; WY2026's training year keeps 152 days after the buffer |
| Two-member subset eemetric + ssebop, calendar year (checked, not declared) | 2024 (115), 2025 (188) | **declined**: the other year is the only training fold, and it keeps 160 or 92 days after the buffer, below both the 180-day and the two-fold minimum |
| Leave-one-site-out | one station | **not_available** |

**The US-UTJ record cannot support a fitted held-out ensemble.** No weights were fitted, so no held-out improvement can be reported.

**Baseline comparison** (188 frozen days, `et_corrected_measured`; not held out, nothing fitted):

| Product | MAE | RMSE | MBE | r | NSE | KGE 2009 |
|---|---|---|---|---|---|---|
| official ensemble | 0.699 | 1.140 | −0.314 | 0.957 | 0.825 | 0.647 |
| member mean | 0.683 | 1.082 | −0.255 | 0.959 | 0.842 | 0.673 |
| member median | 0.725 | 1.107 | −0.201 | 0.957 | 0.835 | 0.666 |

(mm d⁻¹ for MAE, RMSE and MBE.)
- **No contrast is separated.** Every MAE, RMSE and |MBE| contrast between the three products includes zero at 7 and 14 days (23 and 10 blocks), and 28-day blocks lack support (4 blocks). The 7- and 14-day intervals are probably too narrow (D-048).
- **Disagreement:** the member range is 0.19–7.83 mm d⁻¹ (median 1.87). This is disagreement, not an interval.
- **Not network evidence:** the comparison is station level, with n_sites = 1.
- **Official versus post-extraction mean:** the official ensemble is OpenET's own product, built upstream at pixel level from its members (its method is not reproduced here). The member mean is a post-extraction mean, and their difference mixes the combination method with the extraction order.
- **Independent check** (local integration test): the 188 days, the member mean and median, and the official and mean MAE and RMSE recomputed with pandas from the extraction Parquet and `US-UTJ_daily.csv` match to 1e-12.

### D-067 — Open ensemble questions (Open)
- **Other stations' inputs.** ~~Paths needed.~~ Supplied by the user on 2026-10-18 and surveyed in D-068. None has been declared or extracted yet; the open choices are listed there.
- **A longer US-UTJ record** with all six members in a second calendar year would make leave-one-year-out possible under the declared rule.
- **Whether to keep the declined design declared** (it documents the result), or to declare another design. Any change must be fixed before fitting and never chosen by agreement.
- **Carried over:** the block length and support (D-048), and equivalence margins (D-049); none is declared here.

### D-068 — Survey of the other stations' inputs (Information, 2026-10-18)
The user named three local directories (2026-10-18). They were read only; nothing was copied, declared, extracted, downloaded or queried.

**`C:\Users\Hutto\Documents\database`:**
- `daily_output/<site>_daily.csv` for 11 stations (US-CdM, UTB, UTD, UTE, UTG, UTJ, UTL, UTM, UTV, UTW, UTZ).
  - `US-UTJ_daily.csv` is byte-identical to the repo copy (sha256 3924e02d…).
  - Every file has the 44 US-UTJ columns, except **US-UTB**, which lacks `ET_corr`, `ebc_cf` and the other closure columns. US-UTB therefore has raw ET only and no corrected reference.
- `config/<site>.ini` with `station_latitude`, `station_longitude` and `utc_offset` (−7 at US-UTW), and `config/gridMET_data/` per station.
- Half-hourly data: `ameriflux/*_HH_*.csv` and `database_exports_20260825/*_metflux_qc.parquet` for 10 stations, plus `nldas/nldas_results/<site>_nldas.csv`. These are the inputs of a per-timestep footprint stack like D-037's.
- `eddy_events_202610180951.csv`: 513 rows, with no US-UTJ change from the declared `eddy_events_202610171141.csv`. It holds US-UTW pivot-irrigation events (2021–2022).
- `daily_crop_height_202610180952.csv`: a multi-station, multi-zone height table with another schema than `US-UTJ_20240101_20251230.csv`.
- `dynamic_station_data_202610180955.csv`: instrument changes. It records a US-UTJ IRGASON move on 2025-07-15 "to try to avoid irrigation water". That suggests irrigation, but it gives no depths or timing, so the water-balance irrigation term stays unknown (D-064).

**`C:\Users\Hutto\Documents\OpenET_Daily_Tiffs`:** per-station folders of daily rasters, all seven products.
- **Naming.** Most use a new suffix, `<site>_<model>_et_YYYY_MM_DD_<hash>_buffer400m_clip.tif` (a 400 m buffer clip, EPSG:4326). US-UTB and US-UTE use the older names without it.
- **Extent.** For one mid-record day per station and model, the footprint grid's bounding box lies wholly inside the clipped raster (fraction 1.0 at every footprinted station). This was checked on one day only, not on every day.
- **Shared assets.** US-UTD, UTG, UTM and UTW share identical asset hashes and counts (11,803 files each), consistent with one multi-site request clipped per station.
- **No rasters:** US-UTL has none.
- **Duplicate dates.** Same-site/model/day files with different suffixes:
  - byte-identical at US-UTD, UTM and UTV (737, 737 and 245 groups) and at US-UTB (336 groups);
  - **different content in 186 US-UTE groups** (geesebal, ptjpl), and in one US-UTB group (eemetric 2025-11-09, after its record).

  D-021 raises on both kinds until a selection policy is declared.
- **Coverage gaps.** At US-UTD/UTG/UTM/UTW, geesebal has 114 days and ptjpl 3 days in 2024. The four 2025-only models start in 2025 at US-CdM, UTB, UTJ and UTV.

**`C:\Users\Hutto\Documents\footprints`:** eight stations (US-UTD, UTE, UTG, UTJ, UTL, UTM, UTV, UTW).
- **Layout.** Top-level daily `etw` (set-A style; `daily_mean` too at some stations), `weighted/` (`etw`) and `non_weighted/` (`mean`) daily sets, monthly footprints, per-month `*_daily_audit.csv` and `*_input_data.parquet`.
- **US-UTM** has only the top-level files. **US-CdM** and **US-UTB** have no footprints.
- **Weighting.** The `etw` weighting was verified only for US-UTJ (NLDAS ETo, negatives kept; D-036). For the other stations it is unverified.
- **No pre-extracted tables** exist for other stations, so their OpenET values need a step-5 raster extraction.

**Pre-extraction estimate of six-member support.** A usable day has all six member rasters, a daily footprint and a measured reference (`et_corrected_measured`). These are upper bounds: extraction can still reject a day. The cells give all days / April–October days.

| Station | 2023 | 2024 | 2025 |
|---|---|---|---|
| US-UTD | 74 / 21 | 2 / 2 | 239 / 154 |
| US-UTE | — | 0 | 219 / 146 |
| US-UTG | — | — | 168 / 168 |
| US-UTJ | — | 0 | 188 / 105 |
| US-UTM (top-level footprints) | 84 / 80 | 0 | 191 / 149 |
| US-UTV | 0 | 0 | 252 / 179 |
| US-UTW | 333 / 204 | 3 / 3 | 257 / 188 |

US-UTL has no rasters, and US-CdM and US-UTB have no footprints (US-UTB also has no corrected reference).

What this implies under the declared rule (calendar years, 28-day buffer, ≥ 2 training folds of ≥ 180 days):
- **Leave-one-year-out stays declined.** Only 2023 and 2025 have six-member days (2024 has at most 5 days at any station), so each held-out year has one training year. This holds pooled over stations and at US-UTW, the longest record.
- **Leave-one-site-out becomes possible in 2025.** Six stations have at least 180 such days (US-UTD, UTE, UTJ, UTM, UTV, UTW), and US-UTG has 168. This needs extraction and the choices below first. Stations share calendar days and weather, so site folds are not independent in time.

**Choices needed before any extraction or fit** (none is made here):
- the footprint set for every station: set B `etw` everywhere (the unverified weighting at the other stations), or NLDAS-ETo footprints rebuilt from per-timestep stacks as for US-UTJ (D-037);
- US-UTM's top-level-only footprints;
- the raster root, now the clipped set;
- whether byte-identical duplicates may be collapsed;
- a selection policy for the 186 differing US-UTE pairs;
- each station's day definition, interval label and units (the `.ini` gives coordinates and `utc_offset`);
- whether US-UTB (raw ET only, no footprints), US-CdM (no footprints) and US-UTL (no rasters) are left out;
- whether training and evaluation are restricted to April–October.

*All resolved by the user on 2026-10-19 (D-069).*

---

**Revision 2026-10-19 (t):** The seven-station experiment. D-069 to D-071 added. Under the user's decisions (D-069), footprints were rebuilt for five stations, every station was extracted from the clipped rasters, and the ensemble was run on seven stations. The UGS database was queried read-only for footprint parameters, as for US-UTJ (D-037); nothing was downloaded. D-001 to D-068 are unchanged, apart from D-068's list of choices, which is now resolved.

### D-069 — Seven-station inputs and processing (Adopted 2026-10-19, user decisions)
| Choice | Decision (user) | How it is implemented |
|---|---|---|
| Footprints | Rebuild NLDAS-ETo footprints from half-hourly stacks, as for US-UTJ | `tools/upstream/run_timestep_footprints.py` for US-UTD, UTE, UTG, UTV and UTW, then the US-UTJ aggregation `nldas_eto_weighted` (D-037) via `tools/run_footprint_aggregation.py`. US-UTJ keeps `US-UTJ_nldas_eto_20261014` |
| US-UTM | Use its top-level footprints | `US-UTM_production_etw`, `temporal_weighting: reference_et_weighted`. footprint_workflow_v2-1 weights by NLDAS `eto_grass` (code-inferred), and negative weights may be kept as in set A (D-036). Not verified by reproduction |
| Rasters | The clipped set for every station, US-UTJ re-extracted | `openet_rasters` root `C:/Users/Hutto/Documents/OpenET_Daily_Tiffs`, declared processing tag `buffer400m_clip` |
| Duplicates | Byte-identical copies collapse automatically | `on_conflict: collapse_identical`: SHA-256 per file; 3,531 identical groups keep their first path, and 3,924 copies are logged as dropped. **187 groups that differ in content are held out** (186 at US-UTE for geesebal and ptjpl; 1 at US-UTB). No choice was made for them, so those days are missing |
| Stations | Same as US-UTJ: UTC−7, local standard time, interval start, mm/day | `examples/utah_stations.yaml` |
| Coverage | Leave out US-UTB, US-CdM, US-UTL and US-UTZ | Seven stations: US-UTD, UTE, UTG, UTJ, UTM, UTV and UTW |
| Season | Whole calendar years, April–October reported separately | Ensemble `subsets: [{subset_id: apr_oct, months: 4–10}]`. Folds and training use whole years; each compared scope is also scored on its April–October days as a separate frozen cohort |

**Inputs and parameters:**
- **Heights.** `groundwater.daily_footprint_parameters` was queried read-only (2026-10-18/19; `zone_status = 'primary'`), with the station coordinates from `station_attribute_view`. The first attempt timed out; it succeeded once the user enabled access.
  - zm and z0 match the fluxfootprints alfalfa preset to 5e-4 m at US-UTD, UTE, UTV and UTW, as at US-UTJ.
  - **US-UTG is corn.** Its database values differ from `corn_standard` by up to 0.22 m, and from the production inputs (z0 0.07–0.18 m against 0.15–0.39 m). That cross-check is a diagnostic only; the database values are used.
- **Half-hourly data and NLDAS** come from `C:\Users\Hutto\Documents\database`. The US-UTJ copies are byte-identical to the M: files of D-037.
- **Footprint settings** are unchanged from D-037: ffp_xr, h = 2000 m, 10 m cells, ±600 m, 08:00–20:30 starts.
- **The stack tool** gains `--veg-type`, used only for the zm/z0 cross-check (default `alfalfa`, so US-UTJ is unchanged).

**Code changes:**
- **Processing tags** (`parse_openet_filename(processing_tags=)`; `openet_rasters[].processing_tags`). A declared tag after the API suffix is accepted and recorded; any other trailing text is still a parse error (tested).
- **`collapse_identical`** (`openet_rasters[].on_conflict`). It extends D-021: byte-identical copies collapse, and differing copies are held out with a warning. Nothing is ever chosen or averaged (tested).
- **Per-station footprint sets in an ensemble** (`ensembles[].site_footprint_sets`):
  - `side.footprint_set_id` becomes a label.
  - Each station's rows of its own set are relabelled, with the original set kept in `support_id`.
  - Every mapped set must be target-independent, every station must be mapped, and the mapping is in the table attributes. Rows of another set never enter (tested).
- **Month subsets** (`ensembles[].subsets`). Each scope also gets a frozen cohort of its days in the declared months, with metrics, intervals and contrasts. `ensemble_summary` and `ensemble_improvement` gain a `subset` key. Training is unchanged (tested: identical weights and inner scores).
- **Aggregation targets.** `tools/run_footprint_aggregation.py` picks each method's target set by station prefix (`<site>_...`), so one config can hold several stations.

### D-070 — Seven-station footprints and extractions (Information)
- **Stacks:** 132 monthly stacks (US-UTD 29, UTE 17, UTG 8, UTV 26, UTW 52; 5 months skipped with fewer than 10 records), about 7 min in all.
- **Daily footprints:** US-UTD 831, UTE 457, UTV 726 and UTW 1,543 of 1,544 days written.
- **US-UTG: 41 of 199.** Only 20 % of its half-hours give any footprint mass. Production found the same: its audit marks most US-UTG days "No valid modeled footprint". The cause (for example the corn heights or the shadow-zone setting) is unresolved.
- **Extraction** (`footprint_conservative`): every footprint grid lies wholly inside the clipped model window at every station (14,641 cells, row sums 1). US-UTW has 373 model-days with no valid cell, and US-UTM 90; they count as missing values.
- **The clip changes nothing for US-UTJ.** The clipped re-extraction reproduces the unclipped extraction on all 2,549 rows (max |difference| 1.3e-10 mm d⁻¹; retained fraction 2.7e-11; integration test).
- **Eligible days** (six members, the official ensemble and `et_corrected_measured`), counted independently from the files:

  | Station | 2023 | 2024 | 2025 | Total | April–October |
  |---|---|---|---|---|---|
  | US-UTD | 74 | 2 | 239 | 315 | 177 |
  | US-UTE | — | 0 | 177 | 177 | 107 |
  | US-UTG | — | — | 41 | 41 | 41 |
  | US-UTJ | — | 0 | 188 | 188 | 105 |
  | US-UTM | 77 | 0 | 190 | 267 | 222 |
  | US-UTV | 0 | 0 | 251 | 251 | 179 |
  | US-UTW | 299 | 3 | 257 | 559 | 394 |

  The total is 1,798 station-days (1,225 in April–October). US-UTE loses 41 days to the held-out geesebal/ptjpl conflicts (218 days have the other members).

### D-071 — Seven-station ensemble results (Information; superseded by D-072 on 2026-10-19)
*The figures below are from the C: rasters without a minimum held-out size. D-072 has the current run.*
`utah_ensemble`, run by `tools/run_ensembles.py examples/utah_stations.yaml` (about 1 min; local `outputs/utah/ensembles/`). The settings are those of D-066 (members, reference, `require_all`, calendar-year folds, 28-day buffer, ≥ 2 training folds of ≥ 180 days, α grid) plus per-station footprint sets and the April–October subset.

**Leave-one-year-out: no held-out claim.**
- Years 2023 and 2025 are declined: each has only the other as a training year, because 2024 has 5 six-member days.
- **Year 2024 is formally fitted** (trained on 2023 and 2025), but on **5 held-out days**. No interval has support there, so its improvement is `not_assessed`. The declared rule has no minimum held-out size; such a rule, if wanted, is a user decision.

**Leave-one-site-out: held out, all 7 station folds fitted.**
- US-UTE (177 days) and US-UTG (41) fall below 180 days, so they are held-out test stations only and never training folds.
- **Weights** are stable across folds: SIMS 0.42–0.59, SSEBop 0.22–0.30, geeSEBAL 0–0.13, PT-JPL 0.04–0.12, and DisALEXI and eeMETRIC 0–0.06. α = 0 at four folds and 0.25 at three.

**Held-out RMSE (mm d⁻¹)**, each station predicted with weights fitted on the others:

| Station | Days | Official | Mean | Fitted | Fitted − mean | Fitted − official |
|---|---|---|---|---|---|---|
| US-UTD | 315 | 1.050 | 1.029 | 0.955 | −0.074 | −0.095 |
| US-UTE | 177 | 1.766 | 1.688 | 1.388 | −0.300 | −0.378 |
| US-UTG (corn) | 41 | 1.740 | 1.740 | 2.272 | **+0.532** | **+0.532** |
| US-UTJ | 188 | 1.140 | 1.082 | 0.796 | −0.286 | −0.344 |
| US-UTM | 267 | 1.311 | 1.171 | 1.167 | −0.005 | −0.145 |
| US-UTV | 251 | 1.672 | 1.615 | 1.363 | −0.251 | −0.309 |
| US-UTW | 559 | 1.318 | 1.309 | 1.285 | −0.024 | −0.034 |

- **Point estimates.** The fitted ensemble has the lower RMSE at 6 of 7 stations against both the mean and the official ensemble. The equal-station mean differences are −0.058 (mean) and −0.110 (official).
- **April–October:** 6 of 7 again, with equal-station means of −0.103 and −0.167.
- **Transfer fails at the corn station.** At US-UTG the fitted ensemble is worse (+0.53), on 41 days. All six other stations are alfalfa.
- **Supported improvements are few.** Intervals have support only where a station has enough blocks.
  - At 14-day blocks the RMSE improvement over the mean is `supported` at US-UTJ and US-UTV only. MAE is also supported at US-UTW.
  - At US-UTD, UTM and UTW the RMSE interval includes zero, and US-UTE and US-UTG lack support.
  - At 28-day blocks almost nothing has support.
- **Unchanged US-UTJ baseline.** US-UTJ's baseline (official 1.140, mean 1.082 on 188 days) equals the single-station run (D-066).

**How to read this:**
- These are exploratory, station-level contrasts. There is no network interval: hierarchical site × block resampling is not implemented (D-047, D-049).
- With 7 stations, the equal-station means are network summaries of a small, non-random set of mostly alfalfa sites in one state.
- Site folds share calendar days and weather, and no time buffer applies across stations, so the folds are not independent in time.
- The 7- and 14-day intervals are probably too narrow (D-048).
- One station's footprints (US-UTM) were built differently.
- The fitted weights describe these stations and years; they are not a general weighting of the OpenET models.
- **Independent checks** (integration test, `tests/integration/test_utah_ensemble.py`):
  - eligible days per station equal the count above;
  - member means equal pandas;
  - fitted predictions equal the member values times the fold's weights;
  - no station trains its own fold;
  - the April–October cohort holds only months 4–10.

---

**Revision 2026-10-19 (u):** User decisions on D-071's open items. D-072 added; D-071's figures are superseded.

### D-072 — J:/FLUX rasters, a minimum held-out size, an extraction fix, US-UTG and US-UTM (Adopted 2026-10-19, user decisions; Information)
**Decisions (user, 2026-10-19):**
- **Rasters from `J:/FLUX/OpenET_Daily_Tiffs`**, the user's de-duplicated copy. `J:/FLUX/database` and the footprint folders are byte-identical to the C: copies (checked by SHA-256), and the station tables now point at J:. The footprints are unchanged.
- **Minimum held-out size:** `min_test_days_per_fold: 180`, a mandatory fitting field. A fold with fewer eligible held-out days is declined, with the count as the reason. Both example configs declare it.
- **US-UTG keeps the default height filter.** ffp_xr with `rslayer=False` (production, D-037) drops half-hours with zm ≤ 27.5·z0, the roughness sublayer. That rule matches US-UTG's footprint mass on 99.8 % of 4,615 half-hours (922 predicted, 930 positive). `rslayer=True` (Kljun Eq. 27, zm > 20·z0) would recover March–May and part of July, but not August–October, when the corn is 2.4–2.8 m and zm/z0 is 7–8.
- **Rebuild US-UTM** from its stack: **not done**. The UGS database timed out on two attempts ([database host removed]), so its parameters could not be fetched. US-UTM keeps `US-UTM_production_etw` until access returns.

**What J: contains:**
- **No duplicates**, and US-UTE gains geesebal and ptjpl for 2024. For the other six stations the extracted values equal the C: extractions exactly (same rows, zero difference).
- **Wrong-region US-UTE files.** In 171 of US-UTE's 188 former conflict groups, J: kept the file of **another region** and dropped the file that covers US-UTE (57 geesebal, 114 ptjpl):
  - 2 files span about −114.07 to −113.67°E, 40.58–40.95°N, near US-UTB;
  - the rest span −110.03 to −109.57°E, 40.06–40.26°N, near US-UTV/UTL.

  In 15 groups J: kept the US-UTE file, and in 2 both files were on the US-UTE grid. The wrong-region files cannot be US-UTE data. Inside the station record they give 39 `no_overlap` model-days, which are missing values.
- **US-UTE's J: rasters are unclipped** (862 × 688 cells). D-070 showed that clipping does not change extracted values.

**Extraction bug fixed (`etcompare.extraction`).**
- **The bug.** A raster was declared `no_overlap` from the previous raster's cached grid, without its own header being read. The grid was rechecked only when a window read failed, and no read was attempted once the cached window was empty. So the first wrong-region file made every later raster of every model `no_overlap`: 1,740 US-UTE model-days.
- **The fix.** When the cached plan has no window, the raster's own header is read and the plan is rebuilt for its grid.
- **Regression test:** a wrong-region raster for one model leaves the others' values exact (`tests/test_extraction.py`).
- **Scope.** No earlier extraction was affected: every earlier exclusion file has zero `no_overlap` rows.

**Current results** (`tools/run_ensembles.py examples/utah_stations.yaml`):
- **Eligible station-days:** 1,801 (1,228 in April–October). US-UTE has 180 (2025 only); the others are as in D-070.
- **Leave-one-year-out: declined.** 2023 and 2025 have one training year each; 2024 has 5 held-out days, below 180.
- **Leave-one-site-out: held out, 6 of 7 station folds.**
  - **US-UTG** (41 days) is declined as a held-out station and never trains.
  - **US-UTE** (exactly 180) now trains and is tested.
  - **Weights:** SIMS 0.55–0.71, SSEBop 0.19–0.29, geeSEBAL 0.08–0.16, PT-JPL 0–0.07, and DisALEXI and eeMETRIC 0–0.04. α = 0 at five folds and 0.25 at one.

| Station | Days | Official | Mean | Fitted (RMSE, mm d⁻¹) | Fitted − mean |
|---|---|---|---|---|---|
| US-UTD | 315 | 1.050 | 1.029 | 0.963 | −0.065 |
| US-UTE | 180 | 1.754 | 1.677 | 1.377 | −0.299 |
| US-UTJ | 188 | 1.140 | 1.082 | 0.791 | −0.291 |
| US-UTM | 267 | 1.311 | 1.171 | 1.184 | +0.013 |
| US-UTV | 251 | 1.672 | 1.615 | 1.273 | −0.341 |
| US-UTW | 559 | 1.318 | 1.309 | 1.285 | −0.023 |

- **Point estimates.** The fitted ensemble is lower than the mean at 5 of 6 stations and lower than the official ensemble at 6 of 6. The equal-station mean differences are −0.168 (mean) and −0.229 (official); for April–October they are −0.224 and −0.297.
- **The corn station is no longer evaluated.** With US-UTG declined by the minimum size, the one station where transfer failed (+0.53 in D-071) is out of the summary. The −0.168 is therefore not evidence of transfer to corn.
- **Few improvements are supported.** RMSE against the mean is `supported` at US-UTE and US-UTV at 7-day blocks, and at US-UTV at 14-day blocks. US-UTJ is supported at 7-day blocks only; it was also supported at 14 days in D-071, which shows how sensitive these station-level verdicts are. 28-day blocks lack support.
- **Caveats** as in D-071: exploratory, station level, no network interval, shared weather across site folds, and probably too-narrow 7- and 14-day intervals. US-UTM's footprints differ in method.
- **Integration test** (`tests/integration/test_utah_ensemble.py`, 7 tests):
  - the counts;
  - the declined folds and their reasons;
  - the weights times the members;
  - April–October days only;
  - the clipped-vs-unclipped US-UTJ match;
  - the US-UTE wrong-region files, which are `no_overlap` for geesebal and ptjpl only.

**Open:**
- ~~Whether the 171 US-UTE groups on J: should keep the US-UTE file.~~ Swapped (D-073).
- ~~US-UTM's rebuild, once the database is reachable.~~ Rebuilt with constant heights (D-073).

---

**Revision 2026-10-19 (v):** D-073 added. D-072's results are superseded where D-073 restates them.

### D-073 — US-UTE files swapped on J:, US-UTM rebuilt (Adopted 2026-10-19, user decisions; Information)
**US-UTE wrong-region files swapped** (user: "swap out the wrong region files").
- **The swap.** Each of the 171 J: files on another region's grid (57 geesebal, 114 ptjpl) was moved, not deleted, to `J:/FLUX/wrong_region_quarantine/US-UTE/<model>/`, outside the raster root, so discovery never sees it. The C: file of the same model and date on the US-UTE grid was copied in.
- **Two files without a replacement.** ptjpl 2023-06-05 and 06 are also on another region's grid, and no US-UTE file exists for those dates. They were moved to quarantine too; they predate US-UTE's record (from 2024-06-18).
- **The log** is `J:/FLUX/wrong_region_quarantine/US-UTE_swap_log.csv`: every moved file with its origin and SHA-256, and every replacement with its C: source and SHA-256, checked equal after copying.
  - The first attempt stopped after the 57 geesebal swaps and one quarantine (a missing replacement was read as NaN). It was resumed, and the log was rebuilt from the quarantine folder; no file was lost.
- **Result.** J: US-UTE now has 4,753 files, all on the station grid, with no duplicate dates. The US-UTE extraction has no `no_overlap` rows (3,008 ok model-days).

**US-UTM rebuilt** (user: rebuild; then "rebuild with production's constants").
- **Why the US-UTJ procedure was not possible.** With database access restored, `groundwater.daily_footprint_parameters` has **no US-UTM rows** in any zone (read-only queries, 2026-10-19). The crop-height table has none either.
- **Production's constant heights are used:** crop height 1.1 m, measurement height 4.22 m, zm = 4.22 − 0.7 × 1.1 = 3.45 m and z0 = 0.11 × 1.1 = 0.121 m for every half-hour (`outputs/footprint_inputs/US-UTM_daily_footprint_parameters.parquet`, `zone_status = production_constant`, flagged `constant_heights` in its JSON).
  - The stack tool's printed cross-check says "the DB values"; for US-UTM these are production's constants.
  - The coordinates are station_attribute_view's (38.58785, −109.57749), and the station declaration now uses them too (about 0.5 m from the `.ini`).
- **The new set.** `US-UTM_nldas_eto_20261019`, with the same stack settings and NLDAS aggregation as the other stations: 34 monthly stacks, every half-hour with footprint mass, and 831 of 832 days written. Extraction `utm_nldas_clip` replaces `utm_production_clip` in the ensemble; the production set stays declared for the record.
- **Little changes.** Rebuilt minus production footprint ET over 5,237 model-days has a mean of 0.000, a 95th-percentile |difference| below 0.001 and a maximum of 0.026 mm d⁻¹. US-UTM's eligible days and every result are unchanged to three decimals.
- **The remaining difference in kind:** US-UTM's heights are constant, while the other stations have daily crop heights.

**Current results** (`tools/run_ensembles.py examples/utah_stations.yaml`):
- **Eligible station-days:** 1,839 (1,264 in April–October). US-UTE has 218, all in 2025.
- **Leave-one-year-out: declined** (as in D-072).
- **Leave-one-site-out: held out, 6 of 7 folds.** US-UTG is declined (41 days). SIMS gets 0.56–0.71, SSEBop 0.19–0.29 and geeSEBAL 0.08–0.16. α = 0.25 at US-UTD and 0 elsewhere.

| Station | Days | Official | Mean | Fitted (RMSE, mm d⁻¹) | Fitted − mean |
|---|---|---|---|---|---|
| US-UTD | 315 | 1.050 | 1.029 | 0.964 | −0.065 |
| US-UTE | 218 | 1.684 | 1.611 | 1.321 | −0.290 |
| US-UTJ | 188 | 1.140 | 1.082 | 0.790 | −0.292 |
| US-UTM | 267 | 1.311 | 1.171 | 1.186 | +0.015 |
| US-UTV | 251 | 1.672 | 1.615 | 1.271 | −0.344 |
| US-UTW | 559 | 1.318 | 1.309 | 1.285 | −0.023 |

- **Point estimates.** The fitted ensemble is lower than the mean at 5 of 6 stations and lower than the official ensemble at 6 of 6. The equal-station mean differences are −0.166 (mean) and −0.226 (official); for April–October they are −0.218 and −0.291.
- **Supported improvements.** RMSE against the mean is `supported` at US-UTE and US-UTV at 7- and 14-day blocks, and at US-UTJ at 7-day blocks only. It is not supported at US-UTD, UTM or UTW. 28-day blocks lack support except at US-UTW.
- **Caveats** as in D-071 and D-072: exploratory and station level, with no network interval and shared weather across site folds; the 7- and 14-day intervals are probably too narrow; the corn station is not evaluated.
- **Integration test** (`tests/integration/test_utah_ensemble.py`, 8 tests): the counts above; US-UTE has no `no_overlap`; the US-UTM rebuild agrees with production within 0.03 mm d⁻¹.


---

**Revision 2026-10-19 (w):** Step 17 implemented: workflow, CLI, run manifest, cache, figures, reports and a synthetic demo. D-074 to D-078 added. The choices in D-074 to D-077 were made by the user on 2026-10-19 (the assistant's recommendations, accepted).

### D-074 — Workflow units, statuses and the cache (Adopted 2026-10-19, user decisions)
- **CLI:** stdlib `argparse` (no new dependency); console script `etcompare`, plus `python -m etcompare`. The subcommands `validate`, `index`, `extract`, `analyze`, `report` and `run` are thin layers over `etcompare.runner.run_stages`, which drives `etcompare.workflow.Workflow`.
  - The workflow implements no analysis. It calls the step 5–16 library functions exactly as `tools/run_*.py` did, and those scripts are now wrappers over the same units.
  - On this machine Windows Application Control blocks the generated `etcompare.exe` launcher; `python -m etcompare` is the same CLI.
- **Exit codes:**
  - 0: success.
  - 2: a configuration, contract or data-validation error (`ContractError`, `DataValidationError`, `SchemaError`, filename and duplicate-asset errors, a missing config file).
  - 1: anything else.
- **What runs:** an analysis runs when its configuration section is declared.
  - `run.skip` switches off declared analyses.
  - Undeclared analyses are recorded as `skipped` (`not_declared`).
  - Former flags are configuration now:
    - `--no-bootstrap` → `run.skip: [bootstrap]`;
    - `--skip-missing-crop-height` → `crop_height_tables[].on_missing: skip_growing_season`;
    - `--strict` → `raster_extractions[].strict`;
    - footprint-set comparisons → `footprint_comparisons`;
    - per-timestep stacks → `footprint_stacks`.
- **Units and statuses:** a unit is one call that writes one group of tables (e.g. `metrics:<comparison>__<metric_set>`).
  - The statuses are `completed`, `cached`, `skipped` (`run_skip`, `not_declared`, `dependency_skipped`), `not_available` (`input_missing`, `dependency_unavailable`, `optional_dependency_missing`, …), `failed`, `not_run` and `stale`.
  - A failure is recorded in the stamp and the manifest, then stops the run (no retries; that is step 18).
  - A unit whose optional input is missing is `not_available` with its reason; nothing is filled in.
- **Cache (per unit):** stamps live in `<output_dir>/.etcompare/cache/<stage>/<unit>.json`. The key is a SHA-256 over three things:
  - the package source, without the presentation modules (`plots`, `reporting`, `cli`, `runner`); the report unit includes them;
  - the configuration entries the unit reads;
  - the identities of its input files. Upstream outputs enter by content (SHA-256), so an analysis that reads a raster extraction depends on that file, not on whether the extraction can run now.

  A stamp is reused only when its key matches and every listed output still has the same size and modification time.
- **Invalidation is exact** (tested in `tests/test_workflow.py`). A change invalidates:
  - for a station table, only the units of comparisons that contain that station;
  - for a metric-set entry, only its metrics unit;
  - for `min_retained_frac`, only that comparison's units;
  - for a raster, only the extractions whose site and window include it (a raster outside the declared window does not count);
  - for a modified output, only its own unit.

  A unit that needs an in-memory result of a reused unit (rankings need bootstrap replicates) recomputes it, without rewriting the reused unit's outputs.
- **Raster identity, measured 2026-10-19** (`stat` against SHA-256 of a 150-file sample, cold):

  | Inputs | Files | Size | `stat` | SHA-256 (extrapolated) |
  |---|---|---|---|---|
  | J: tree | 69,957 | 26.3 GB | 14 s | about 11 min |
  | One model on the M: Drive | 1,096 | 2.0 GB | 0.7 s | about 5.7 min (about 40 min for 7 models) |
  | US-UTJ clipped | 4,747 | 0.07 GB | 0.6 s | seconds |

  - **Adopted:** rasters (`.tif`, `.npz`, …) use path, size and modification time (`cache.raster_identity: stat`, the default), and `sha256` is opt-in.
  - Every table and vector input is always hashed with SHA-256.
  - Only the rasters an extraction reads (its site, window and models) enter its key.
  - Whether Drive-streamed modification times are stable was not verified.
- **Regression check (Information).** On 2026-10-19 the committed (pre-step-17) runners and the workflow were run on the same US-UTJ inputs into separate output directories. All 177 Parquet tables (cohorts, events, water balance, ensembles, representativeness) are identical, frames and attributes.

### D-075 — Run manifest (Adopted 2026-10-19, user decisions)
- **One JSON manifest per invocation** (`etcompare.runrecord.RunManifest`, `manifest_version` 1, round-trip tested) at `<output_dir>/runs/<run_id>/run_manifest.json`, copied to `<output_dir>/run_manifest.json`.
  - Runs are never overwritten.
  - `run_id` is the UTC timestamp plus the first 8 hex digits of the configuration hash.
- **Contents:**
  - the configuration path, its file SHA-256 and its canonical hash;
  - the code identity, and the git revision with a dirty flag and the modified paths;
  - package versions;
  - declared upstream versions (`upstream:`, each with an evidence label) next to the detected ones. fluxfootprints' commit is read from its editable checkout. flux-data-qaqc is not installed, so only its declaration exists;
  - every input identity used;
  - per extraction: raster grid IDs, window grids and transfers, and masks (methods, footprint set, negative-weight tolerance, support kinds, nodata);
  - bootstrap and scenario seeds;
  - exclusion counts per stage and unit;
  - every unit's status, reason and cache key; the outputs; the run status and any error.
- The run directory also holds `analysis_status.{parquet,csv}` (schema `analysis_status` v1) and `log.jsonl` (structured JSON lines). Stderr is JSON lines by default.

### D-076 — Figures and reports from saved tables (Adopted 2026-10-19, user decisions)
- **Figures** (`etcompare.plots`) need the `plot` extra; matplotlib ≥ 3.8 is imported only when drawing.
  - Each function takes saved tables and returns the figure plus the exact rows drawn, saved as `<name>_rows.csv` next to the figure. Nothing is recomputed.
  - With no rows to draw it raises `NothingToDraw`, and the report lists the figure as `not_available` with its reason. An empty figure is never written.
  - Formats are PNG and SVG (`report.figure_formats`).
- **The figures:**
  - scorecards, per site: point estimates, with `validity_reason` where a metric is undefined;
  - scatter plots with the 1:1 line;
  - cumulative ET and cumulative error, drawn per `segment` and never joined across a missing period. Missing spans are shaded and counted in the caption (user decision: break, shaded span and count);
  - footprint-benefit and raw/corrected contrasts at every block length side by side. Rows without bounds show their status;
  - conditional errors, with sparse and empty bins marked;
  - coverage: every candidate day with its reason code;
  - ranking-stability tables;
  - ensembles: folds (held out, exploratory or declined); held-out contrasts labelled with `held_out_improvement`; member disagreement, never shown as an interval.
- **Captions** name the units, reference variant, extraction method with footprint set, scale, cohort ID, `n_days` and `n_sites`, all read from the drawn rows (`caption_meta`).
  - They are checked against the comparison's declaration; a disagreement raises `CaptionMismatch`.
  - The coverage caption takes its variants from the same cohort's paired table, because the membership table has none.
- **Reports** (`etcompare.reporting`): Markdown with linked figures, and a self-contained HTML page (figures embedded as PNG, or as SVG when no PNG is produced; light and dark themes). Each report lists:
  - the analyses that ran;
  - a skipped-analyses table (`skipped_analyses` v1): the workflow units, plus the analyses' own status rows (e.g. `not_available` variables, `insufficient_support` intervals, `declined` folds, `unknown` residuals);
  - the open decisions: the declared `decisions:` (a new section; each entry carries a decision-log reference), plus decisions read from status columns: block lengths with none adopted, equivalence `not_assessed`, exploratory contrasts, `single_site` summaries, provisional variables, declined ensembles and unknown water-balance terms.
- **Wording:**
  - Verdicts are carried over as written (`no_unique_leader`, `includes_zero`, `not_supported`, `exploratory`, `single_site`).
  - "network", "validated" and "significant" raise `LanguageError` unless a verdict column licenses them: `network_evidence` true, or a formal `reject_zero`. Nothing licenses "validated".
  - `not_separated` is not a table value and is not used.
- **Synthetic reports:** `report.synthetic: true` labels the title, a banner at the top and at the bottom, every caption and every figure.
  - It also requires every station ID to start with `report.synthetic_site_prefix`.
  - The default prefix is `XX-`, an ISO 3166 code assigned to no country. Site IDs must match the `AA-xxx` pattern, so `SYN-` is impossible.

### D-077 — Synthetic demonstration (Adopted 2026-10-19, user decision: a minimal fixed-seed dataset now, the full demo in step 18)
`etcompare.demo.write_demo(directory, seed=20261019)` writes:
- three stations (`XX-SY1..3`, 2021–2022);
- station tables in the flux-data-qaqc layout: raw, corrected, gap-filled and missing days, the closure factor and meteorology;
- a pre-extracted table with five synthetic models and an official ensemble: footprint-weighted and source-region values, retained weight, days without a footprint, and `echo` missing until May 2021;
- an event log;
- `config.yaml`: every step 7–16 analysis that tables support, `report.synthetic: true` and two example decisions.

Raster extraction and representativeness are not declared. The declared footprint set has no files, so its index is `not_available`.

`etcompare run` on it completes in about 1.5 min, plus about 1 min for the report:
- leave-one-site-out is held out, and leave-one-year-out is declined;
- satellite support is `not_available`;
- the water balance is unknown.

### D-078 — Step-17 runs on the real stations (Information, 2026-10-19/20)
Local and git-ignored (D-014). Real-station reports were authorized by the user on 2026-10-19.

**US-UTJ** (`python -m etcompare run examples/us_utj.yaml`, 5 min 42 s; 77 units completed):
- **Index and all four raster extractions are `not_available`** (`input_missing`): `openet_rasters.root` is stale (D-054). The analyses read the existing extraction outputs by content.
- **Footprint aggregation is `skipped`** (`run_skip`), as declared.
- **Both footprint-set comparisons, now declared in `footprint_comparisons`, reproduce D-035 and D-038:**
  - set B `mean` − `etw`: −0.046 to −0.001 mm d⁻¹, maximum 0.48;
  - measured LE − NLDAS: +0.002 to +0.015, maximum 1.12.
- **Report:** 151 figures, HTML 12 MB.
  - The open decisions list the 11 declared entries plus the ones read from the tables: no block length adopted (7, 14 and 28 days), 20,034 contrast rows `not_assessed`, contrasts exploratory, `single_site` summaries, leave-one-site-out `not_available` and leave-one-year-out `declined`, five unknown water-balance terms.
  - Two figures are `not_available` with their reason: retained-fraction bins on the 3×3-window side (`empty_bin`), and no held-out ensemble contrast (nothing fitted).

**Seven Utah stations** (`python -m etcompare run examples/utah_stations.yaml`; this checkout had no Utah outputs):
- **Index:** J: was indexed in 18 s.
- **Extractions:** all eight completed. Seven took 17–138 s each. `utm_nldas_clip` took 32,382 s of wall time, but 32,305 s of it is a single pause between two consecutive progress records. **The machine was asleep** (established 2026-10-20 from the Windows System log): the last progress record (US-UTM day 950) is at 21:11:24.7 MDT; Kernel-Power event 42 "The system is entering sleep", reason *System Idle*, follows at 21:11:25.1; the system resumed at 06:09:40 (wake source: the USB host controller), and the next record came 10 s later. The AC idle-sleep timeout is 900 s and nothing kept the system awake during a run. J: is a USB SSD (Samsung T7, exFAT), so no spin-down is involved; no disk event was logged and no read failed (the exclusions hold only `footprint_missing` and `no_valid_cells`). Whether the USB drive was briefly disconnected during sleep cannot be established. Step 18 adds `run.keep_awake`.
- **The ensemble reproduces D-073:**
  - 1,839 eligible station-days (1,264 in April–October);
  - leave-one-site-out held out on 6 of 7 folds (1,798 days compared);
  - leave-one-year-out declined;
  - `tests/integration/test_utah_ensemble.py` (8) passes on these outputs.
- **Report:** ensemble section only (no comparisons are declared in that configuration).

---

**Revision 2026-10-20 (x):** Step 18 implemented: resilient processing, the full synthetic demonstration, documentation, notebooks, CI and the 0.1.0 release files. D-079 and D-080 added. The user made the choices on 2026-10-20, accepting the assistant's recommendations except where noted.

### D-079 — Resilient processing (Adopted 2026-10-20, user decisions; example values Provisional)
**Survey before building (2026-10-20):**
- **Raster handles.** Every raster is opened in one `with` block, read in one bounded window, and closed. No handle outlives a read, and no dense stack of rasters exists.
- **Repeated opens.**
  - Within an extraction, only the first raster is opened twice.
  - Footprint-only extractions open each raster's header on days without a usable footprint (54 opens for US-UTJ on J:).
  - Across extractions, rasters are opened again for each footprint set, and on the unclipped Drive tree once per station of a multi-station file. The J: files are all single-station.
- **Failures before step 18.**
  - A non-strict extraction whose every raster was unreadable completed with zero rows.
  - An unreadable raster left no row, so coverage showed only `extraction_missing`.
  - CSV, text and figure writes were not atomic.
  - An extraction lost all progress when interrupted.
  - There was no stall detection.

**Serial benchmark (measured 2026-10-20, instrumented `extract_rasters`, two runs each):**

| | J: `utj_nldas_clip` (clipped, footprint) | M: `us_utj_set_b_etw` (unclipped Drive, 4 methods) |
|---|---|---|
| Rasters / rows | 2,549 / 2,549 | 2,603 / 9,912 |
| Wall time | 22.1 s, 21.7 s | 94.1 s, 88.3 s |
| I/O share | 86 % (opening files: 16.0 s, about 5 ms each) | 95 % (opening files: 71.4 s, about 23 ms each) |
| Computation | 3.1 s | 4.5 s |
| Opens / distinct files | 3,078 / 3,077 | 3,096 / 3,095 |
| Memory | working set 174 → 198 MB | peak 119 → 167 MB |

Both outputs equal the stored tables. The M: table differs only in `source_path`, because the folder moved.

**The 9-hour pause of D-078** was idle sleep (Windows System log: Kernel-Power 42, reason *System Idle*, one second after the last progress record). It was not a drive stall.

**Decisions (user, 2026-10-20):**
- **Concurrency:** none. Extraction is I/O-bound by file-open latency and takes about 1.5 min per station.
- **Keep-awake:** `processing.keep_awake: true` by default (Windows `SetThreadExecutionState`).
- **Retries:**
  - an `OSError` (including `RasterioIOError`) while the file still exists is retried: 3 attempts, 1 s and 4 s backoff, each attempt logged as `io_retry`;
  - a vanished file is `raster_missing` (or `footprint_file_missing`) with no retry; in strict mode it raises `InputMissingError`, a `FileNotFoundError`, exit code 2;
  - etcompare's own errors are never retried.
- **Stall detection:**
  - `io_stall` is logged after `stall_warning_s` (120 s) without a finished raster. Detection is armed only while rasters are being read.
  - `system_suspended` is logged when the watchdog's clock jumps, and the jump is not counted as a stall.
  - `stall_timeout_s` (off by default) fails the run.
- **Staging:** not implemented. The user works from a local drive.
- **Checkpoints:** `processing.checkpoint: month`.
  - Progress is saved under `<output_dir>/.etcompare/checkpoints/<unit>/<cache_key>/` after each calendar month.
  - It is reused only for the identical key, dates and spec, and only when the SHA-256 recorded after the state matches.
  - Checkpoints of other keys are pruned. All of them are removed once the table is written.
- **Unreadable rasters:**
  - `strict` stays `false` by default.
  - Unreadable or missing files keep null rows for every method of their model-day.
  - They are counted in the exclusions and the manifest, and listed in the report's skipped-analyses table (`files_skipped`).
- **Systematic failure:**
  - The run fails (`SystematicFailureError`, exit code 2, manifest `failed`, extraction table not written) when every file of one model, every model raster, every footprint, or more than `max_unreadable_fraction` (0.01, Provisional) of the model rasters or of the footprints is unreadable or missing.
  - The exclusions CSV is written before the error is raised.
- **Atomic outputs:**
  - Every CSV, text, JSON, figure, stamp and manifest is written through `etcompare.io.atomic`: a temporary file, then a rename.
  - `write_footprint` keeps its read-back check (D-043) and now removes its `.part` file when a write fails.
  - The run manifest is written with status `running` at the start.

**No result changed.** Before and after the raster-path changes, every Parquet table was compared (frames, dtypes and the full `etcompare` metadata, attributes included):
- US-UTJ (`examples/us_utj.yaml`, now extracting from the unclipped Drive folder): 197 tables;
- the seven Utah stations (`examples/utah_stations.yaml`, all eight J: extractions): 58 tables.

Both comparisons found no difference. The final runs are in D-080.

### D-080 — First release 0.1.0 (Adopted 2026-10-20, user decisions; Information)
- **Licence:** MIT, chosen by the assistant after the user asked for "an open licence". The copyright holder is Paul Inkenbrandt (user, 2026-10-20). fluxfootprints (GPL-3.0) stays an optional, separately installed dependency, which etcompare imports only when present (D-056).
- **Version** 0.1.0. **Name** `etcompare`: unregistered on PyPI, TestPyPI and conda-forge on 2026-10-20. Nothing was published.
- **Changelog:** `CHANGELOG.md`, Keep a Changelog format.
- **Documentation:** Sphinx with MyST Markdown for Read the Docs (`docs/conf.py`, `.readthedocs.yaml`, `docs/requirements.txt`; theme `sphinx_rtd_theme`). The pages are getting started, configuration, method guide, limitations, public API (autodoc, one page per module), data dictionary, references, this log, the input contract and the changelog.
  - The data dictionary is generated from `etcompare.schemas` (`tools/make_data_dictionary.py`), and a test keeps it current.
  - The build passes with warnings as errors.
  - No Read the Docs project was created.
- **References:** every DOI was checked through the Crossref API (title, authors, year, journal, pages) on 2026-10-20. Huntington et al. (2022) and Kish (1965) have no DOI. The "Volk 2023" attribution in D-037 is listed as unverified: two 2023 papers fit, and the passage was not checked.
- **CI:** `.github/workflows/ci.yml`, written but not pushed. It contains:
  - tests on Ubuntu and Windows × Python 3.11 and 3.13;
  - lint and mypy;
  - the raster demonstration through the console script;
  - a `lowest-direct` job on Python 3.11 (core; geo and plot tests skip);
  - the pinned demonstration environment;
  - a strict Sphinx build.

  No private data is used.
- **Demonstration environment:** `examples/demo/requirements-demo.txt` (`uv pip compile --universal`, Python ≥ 3.11) with exact pins for the demonstration only; the library keeps the lower bounds of D-018. On this machine, Application Control blocks DLLs freshly installed outside the project venv (pyarrow, matplotlib `_path`), so the pinned set could not be run locally. Its CI job is the check.
- **Notebooks** (`notebooks/*.ipynb`, committed through a `.gitignore` exception, outputs stripped):
  - `01_colab_quickstart` (user paths; the Drive mount is a commented cell the user runs);
  - `02_pre_extracted_tables`;
  - `03_raster_workflow`.

  `tests/test_notebooks.py` checks that outputs are stripped and that no private path appears, then runs every code cell on the synthetic demonstration.
- **Full synthetic demonstration:** `write_demo(rasters=True)` and `etcompare demo DIR --rasters` extend the tables-only demonstration, which stays byte-identical. The extension adds:
  - fixed-seed model GeoTIFFs for 3 stations × 6 products, May to July 2022 (30 m, 21 × 21 cells, about 1 % nodata cells);
  - daily footprints (10 m, 15 × 15, about 5 % of days missing);
  - a land-cover map;
  - station coordinates, three raster extractions, a footprint-versus-window comparison and representativeness at `XX-SY1`;
  - an explicit `processing:` section.

  In total: 1,853 files, 8.8 MB, written in 14 s. `etcompare run` completes offline in about 4.5 min with 48 units completed and 2 not declared.
- **Smaller fixes:**
  - invalid YAML is a configuration error (exit 2);
  - `--log-level` filters stderr even when the run log file is attached.
- **Real-file verification (final code, 2026-10-20):**
  - `examples/us_utj.yaml`: 82 units completed, 11 min 32 s. Four extractions from the unclipped Drive folder, 70–83 s each.
  - `examples/utah_stations.yaml`: 20 units completed, 10 min 23 s. Eight J: extractions, 14–140 s each.
  - Compared with the pre-step-18 baselines: 197 and 58 Parquet tables, **0 differences** (frames, dtypes, metadata).
  - No retry, read failure, stall or suspend event occurred; keep-awake was held; all checkpoints were cleared.
  - The version was then bumped to 0.1.0, which changes only the `etcompare_version` written into new files.
