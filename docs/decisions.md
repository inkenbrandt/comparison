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
