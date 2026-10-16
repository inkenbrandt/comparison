# Input contract

**Status:** Frozen for implementation steps 2–4. First issued 2026-10-13; revised the same day after the user supplied the producer notebooks, the timezone, and the footprint location. Revise only with a dated entry in [decisions.md](decisions.md).
**Scope:** The inputs `etcompare` accepts, what each field means, and which semantics must be declared before a calculation may run.
**Evidence labels:** **Verified** (computed from the files, or reproduced from the producing code), **Code-inferred** (read from code whose use for these exact files is not proven), **User-provided**, **Unresolved**. An unresolved item is never promoted to fact by a default.

Regenerate the station and extraction profiles with:

```bash
uv run --no-project --with pandas --with pyarrow python tools/profile_inputs.py \
    US-UTJ_daily.csv station_oet_US-UTJ.parquet
```

## 1. Inputs inspected

| Input | Location inspected | Identity / notes |
|---|---|---|
| Station daily table | `US-UTJ_daily.csv` (local only) | sha256 `3924e02d…c60f`. Export of flux-data-qaqc daily output from `Shareddrives/UGS_Flux/Data_Processing/final_processing_workflow/fluxqaqc/daily_output` |
| Extracted OpenET table | `station_oet_US-UTJ.parquet` (local only) | sha256 `e0d5c1af…4743`; `parquet-cpp-arrow 23.0.1`. Byte-identical to `M:/My Drive/footprint_weighted_results/station_oet_US-UTJ.parquet`, written 2026-10-06 (after set A, before set B) |
| Extraction notebook (producer) | `OpenET_Comparison.ipynb`, cell 20 | sha256 `1191745f…af75`. **Reproduces Parquet rows to ~1e-13** (§5.3) |
| Download notebook | `download_and_organize_openet_rasters_PI (1).ipynb` | sha256 `34b3c381…8a00`. The saved state is a template (region/years set for another site); parameters below are its request body |
| OpenET daily rasters | `M:/My Drive/OpenET_Daily_Tiffs/US-UTJ_US-CdM/<model>/` | Headers and pixels sampled from 13 files |
| US-UTJ footprints, set A (used) | `M:/My Drive/footprints/US-UTJ/ffp_US-UTJ_daily_etw_*.tif` | 492 daily + 17 monthly files, written 2026-10-05. **Set A produced the Parquet** |
| US-UTJ footprints, set B | `M:/My Drive/footprints/US-UTJ/weighted/` and `non_weighted/` | 492 daily `etw` + 492 daily `mean` (+ monthly), written 2026-10-12. Different grid (§7). **Not used by the Parquet** |
| Footprint domain coverage | `M:/My Drive/footprints/domain_outputs/US-UTJ_daily_domain_results.csv` | Written 2026-10-12, so it matches **set B**, not set A |
| Footprint processing log | `M:/My Drive/footprints/logs/footprint_processing.log` | No US-UTJ entries |
| Station metadata | `footprints/data/station_config/AmeriFlux-sites.csv`; `flux_notebooks/data/station_config/US-UTJ.ini` | Sibling repositories |
| Footprint code | `footprints/docs/notebooks/footprint_workflow.py`; `fluxfootprints` 0.5.1 at `424ec20` | Current code; **not** the version that wrote set A (§7) |
| Station processing code | flux-data-qaqc 0.2.1 local copy | Relations reproduced numerically (§4.2); version used for the export unconfirmed |
| Existing comparison outputs | `M:/My Drive/footprint_weighted_results/comparison_results*.parquet`, `*.csv`, scatter PNGs | Produced by `OpenET_Comparison.ipynb` cell 22 (OLS). Not used as inputs or baselines (D-017) |

## 2. Stations

| Field | US-UTJ value | Evidence | Mandatory? |
|---|---|---|---|
| `site_id` | `US-UTJ` | Verified in every Parquet row; the CSV has no site column, so the adapter receives it explicitly | Yes |
| Name | UFLUX Bluff | AmeriFlux-sites.csv | No |
| Latitude, longitude | 37.2818, −109.5348 | AmeriFlux-sites.csv and `.ini` agree | Spatial workflows only |
| Elevation | 1281 m | `.ini` | No |
| Land cover | IGBP `CRO` | AmeriFlux-sites.csv, `.ini` | No |
| Timezone | **Fixed UTC−7 (MST, no daylight saving)** | User-provided; agrees with `.ini` `utc_offset = -7` | Yes |
| Interval label | Start of interval (`TIMESTAMP_START`) | Code-inferred from `.ini` `datestring_col`; not confirmed for this export | Yes |

The `.ini` file's `url_ameriflux` points to `US-UTW` (copy error). Its `[DATA]` names describe the half-hourly AmeriFlux-format input, not the daily CSV columns.

## 3. Measurement-day definition

| Product | What the label means | Evidence |
|---|---|---|
| Station CSV `date` | Calendar day 00:00–24:00 UTC−7, from half-hourly records labelled by interval start | Timezone user-provided; start label code-inferred |
| Footprint `YYYYMMDD` | `resample("1D")` on the footprint input's naive timestamp index | Code-inferred. Assumed to share the station's UTC−7 basis; not confirmed |
| OpenET date | OpenET API `interval: "daily"` product date; day boundary **unresolved** | Download notebook |
| Parquet `footprint_date` | Equals the footprint filename date and the OpenET filename date in 2336/2336 rows; naive midnight | Verified |

**Contract:** each station declares `timezone` and `day_definition` (`local_standard_time` or `civil_time`) plus `interval_label` (`start` / `end`). Validation fails if any is missing. Each model product declares `day_definition` or `unknown`. A product with `unknown` is joined by label, and every output is flagged `day_alignment=unverified`. The library never shifts dates.

US-UTJ configuration: `timezone: "UTC-07:00"`, `day_definition: local_standard_time`, `interval_label: start` (provisional). OpenET `day_definition: unknown`.

## 4. Station daily table

### 4.1 Structure (verified)
492 rows × 44 columns; ISO `date`; 2024-06-05 to 2025-10-09; uninterrupted daily sequence; no duplicates. `ET_gap` is boolean (170 True). There are no zero or negative ET values, which does not license clipping in other inputs.

### 4.2 ET columns, units and provenance

| Column | n | Meaning | Evidence |
|---|---:|---|---|
| `ET` | 303 | Uncorrected daily ET | `le_1_1_1/ET` ratio 28.11–29.19, consistent with daily-mean LE (W m⁻²) → mm day⁻¹. Units **mm/day**: strongly supported; flux-data-qaqc labels ET in mm |
| `ET_corr` | 473 | Energy-balance-corrected ET **plus gap-filled values** | Verified: `= ET·ebc_cf` (303/303); `= ET_fill_val` on flagged days (170/170) |
| `ebc_cf` | 458 | Closure correction factor `= 1/ebr_corr` | Verified 458/458; 1.009–1.577 |
| `ETrF` | 303 | `ET_corr / gridMET_ETr` (corrected, not raw) | Verified 303/303 |
| `ETrF_filtered` | 472 | Filtered, smoothed, interpolated `ETrF` | Code-inferred |
| `ET_fill` | 472 | `gridMET_ETr · ETrF_filtered`, every day | Verified 472/472 |
| `ET_fill_val` | 170 | `ET_fill` on `ET_gap` days | Verified |
| `ET_gap` | 492 | True where `ET_corr` was missing and was filled | Code-inferred; consistent |

Closure method: flux-data-qaqc `ebr` family with a 5-day climatology fallback. The exact method, window and package version are **unresolved**.

### 4.3 Derived reference provenance

| Category | Rule | US-UTJ days |
|---|---|---:|
| `corrected_from_raw` | `ET` present, `ET_corr` present, `ET_gap` False | 303 |
| `corrected_filled` | `ET` missing, `ET_corr` present, `ET_gap` True | 170 |
| `reference_missing` | `ET` and `ET_corr` missing | 19 |
| `inconsistent_flags` | Anything else; reported, never coerced | 0 |

Subdaily valid fractions are unavailable. The `*_subday_gaps` columns pass through uninterpreted.

### 4.4 Other columns
These columns are mapped by name only; their units are **unresolved** unless the observed range noted beside them says otherwise.

| Columns | Role |
|---|---|
| `le_1_1_1`, `h_1_1_1`, `g_1`, `netrad_1_1_1_final`, `LE_corr`, `H_corr` | Energy fluxes |
| `flux`, `energy`, `flux_corr`, `ebr`, `ebr_corr`, `ebr_5day_clim` | Closure diagnostics |
| `sw_in_1_1_1`, `sw_out_1_1_1`, `lw_in_1_1_1`, `lw_out_1_1_1`, `rso` | Radiation |
| `ta_final`, `t_min`, `t_max`, `t_dew` | Temperatures (range consistent with °C) |
| `rh` | 11.7–80.2 (consistent with %) |
| `es`, `vp_final`, `vpd` | `vpd` 0.14–3.91 (consistent with kPa) |
| `ws_1_1_1` | Wind speed |
| `p_1_1_1` | Station precipitation (237 non-null) |
| `gridMET_ETr`, `gridMET_ETo`, `gridMET_prcp` | gridMET reference ET and precipitation; complete |
| `DOY` | Not used for joins |

Wind direction is absent. The 67 negative daily `h_1_1_1` values cannot indicate daytime advection.

## 5. Extracted OpenET table

### 5.1 Schema (verified)

| Column | Type | Canonical use |
|---|---|---|
| `station_id` | string | `site_id` |
| `model` | string | `disalexi, eemetric, ensemble, geesebal, ptjpl, sims, ssebop` |
| `footprint_file` | string | Footprint asset ID (`ffp_US-UTJ_daily_etw_YYYYMMDD`, set A) |
| `openet_file` | string | OpenET asset name |
| `openet_path` | string | Provenance; Colab path |
| `footprint_date` | timestamp[ns], naive | `date` label |
| `ET_fp` | double | `upstream_footprint_weighted` |
| `ET_mean` | double | `source_region_mean` |
| `unity_check` | double | Sum of finite footprint weights; \|x−1\| ≤ 1.01e-8 |
| `valid_weight_fraction` | double | Retained footprint weight (§5.3) |

Primary key `(station_id, model, footprint_date)`: no duplicates, no nulls. Filename model and date agree with the columns in every row. Site lists in filenames appear in both orders because the download notebook renames API files to `f"{REGION_NAME}_{name[15:]}"` and `REGION_NAME` was spelled both ways. The 5-hex suffix comes from the API.

### 5.2 Coverage (verified)

| Model | Rows | First | Last | Rasters in station window |
|---|---:|---|---|---:|
| eemetric, ensemble, ssebop | 431 each | 2024-06-05 | 2025-10-09 | 492 |
| disalexi, geesebal, ptjpl | 261 each | 2025-01-03 | 2025-10-09 | 282 |
| sims | 260 | 2025-01-03 | 2025-10-09 | 281 |

- The four 2025-only models have no downloaded rasters before 2025-01-01, so their 2024 gap comes from download scope.
- **The 61 station dates with no extraction come from footprint set A.** A daily footprint exists for all 492 dates, but the producer's `load_footprint` rejects 61 of them: **18 are entirely nodata** and **43 contain negative weights**. These reasons were determined here by reading the 61 files. `skipped_footprints.csv` in `footprint_weighted_results` was overwritten by a later US-UTE-only run (2026-10-09) and has no US-UTJ rows. The rejections are concentrated in June 2024 (18) and Oct 2024–Jan 2025 (24).
- All seven products share 260 dates: 172 with raw ET and 88 with filled reference ET.

### 5.3 Extraction semantics (verified by reproduction)

`OpenET_Comparison.ipynb` cell 20 produced the Parquet. Recomputing ensemble 2024-06-06, geesebal 2025-09-12 and sims 2025-09-12 with it reproduces `ET_fp`, `ET_mean` and `valid_weight_fraction` within 1e-13.

1. The footprint is read with a mask (declared nodata = 0 and NaN become missing), then set to 0. Footprints with any negative weight, or no positive weight, are rejected.
2. OpenET is read through a GDAL `WarpedVRT` that **bilinearly resamples** the 30 m EPSG:4326 raster onto the 10 m UTM footprint grid; source nodata (−9999) and NaN become NaN. This is not conservative regridding, and it does not create 10 m ET information.
3. `valid` = footprint weight > 0 **and** finite resampled ET.
4. `ET_fp = Σ_valid φ·ET / Σ_valid φ`.
5. `ET_mean` = **unweighted mean of resampled ET over the valid cells**. The region is the footprint-positive part of the 1.21 km footprint grid and **changes daily**: 14,641 cells on 2024-06-06 but 9,602 on 2025-09-12. It is not a field, fixed window or polygon.
6. `valid_weight_fraction = Σ_valid φ / Σ φ`. The denominator is the domain-truncated, renormalised footprint (≈1), so this is retained weight **after** truncation, not original footprint capture.

All 180 rows with `valid_weight_fraction < 0.90` are SIMS. SIMS marks non-cropland pixels −9999 (≈465k cells on 2025-09-12).

## 6. OpenET rasters

### 6.1 Request (download notebook)

```json
{"interval": "daily", "variable": "ET", "reference_et": "gridMET",
 "units": "mm", "resample": 0, "geometry": "<polygon from shapefiles/bluff_cedar.shp>",
 "model": "<one model per request>", "date_range": ["<month start>", "<month end>"]}
```

The endpoint is `https://utah.openet-api.org/raster/export/stack`.
- **Units:** `units: "mm"` with `interval: "daily"`, so ET is **mm per day**. Verified at the request level.
- **Model versions:** "current model versions" at download time (user-provided). Neither the versions nor the download dates are recorded, so the version is `api_default_unrecorded`.
- **`resample: 0`:** meaning not documented here; **unresolved**.
- **Reference ET:** gridMET, the same product the station gap-fill uses (D-005).

### 6.2 Files (verified on samples)

| Property | Value |
|---|---|
| Layout | `<sites>/<model>/<sites>_<model>_et_YYYY_MM_DD_<5-hex>.tif` + `.aux.xml`, `.ovr` |
| Grid | EPSG:4326; 0.000269494585° (≈30 m); 1327 × 1256; origin (−109.817157, 37.588027). **One identical grid** across models and dates; covers US-UTJ and US-CdM |
| Band | float32, description `et`, `AREA_OR_POINT=Area` |
| Scale / offset | 1.0 / 0.0 |
| Missing values | NaN on 1,199,917 cells outside the request polygon (fixed); −9999 for model-specific gaps inside it. Both are missing |
| Zeros | Valid values (geesebal 2025-09-12: 39,686 cells) |
| Temporal support | A raster for every calendar day: the interpolated daily product. **No clear-observation or acquisition metadata** |
| Record | Long-record products 2023-01-01 to 2025-12-31; 2025-only products 2025-01-01 to 2025-12-31 (sims missing one date in the station window) |

## 7. Footprint rasters (US-UTJ)

| Property | Set A (top level; produced the Parquet) | Set B (`weighted/`, `non_weighted/`; **adopted for re-extraction**, D-016) |
|---|---|---|
| Written | 2026-10-05 | 2026-10-12 |
| CRS / cell | EPSG:32612, 10 m | EPSG:32612, 10 m |
| Shape / origin | 121 × 121; (629275.97, 4127739.12) | 125 × 125; (629255.97, 4127759.12) |
| Declared nodata | 0.0 | 0.0 |
| Values | Normalised cell weights; usable files sum to 1 | Normalised cell weights; usable files sum to 1 ± 1.1e-8 (all 984 daily files profiled) |
| Problem files | 18 all-nodata; 43 with negative weights | `etw`: 18 all-NaN, 43 with negative weights (same dates as set A; 28–5,879 negative cells; negative/positive mass up to 1.4e-3) → 431 usable. `mean`: 18 all-NaN, 0 negative → 474 usable |
| Layers | `daily_etw`, `monthly_etw` | `daily_etw`, `daily_mean`, `monthly_etw`, `monthly_mean` |
| Domain coverage CSV | None | `US-UTJ_daily_domain_results.csv` (474 days): `et_weighted_coverage_pct` 85.9–101.4 % (11 > 100 % from discretisation) and `mean_coverage_pct` 87.3–104.2 %. Code-inferred meaning (fluxfootprints `summarize_periods`): per-timestep footprint mass inside the domain before renormalisation, averaged over the day (plain or ET-weighted mean). Attached to the `etw` and `mean` layers respectively |

Semantics:
- **Value type:** normalised cell weights, not density per m². Each timestep is renormalised over the grid, so set A carries **no original-capture information**. The domain-coverage CSV describes set B and must not be attached to set-A extractions.
- **Weighting variable:** the current `footprint_workflow.py` weights by `nldas_et` clipped at 0, refuses to export negative weights and writes NaN as nodata. Sets A **and B** contain negative-weight `etw` files and nodata 0, so both were written by **earlier workflow versions**. Their weighting variable and negative-value handling are **unresolved** (D-009, D-027).
- **OpenET scaling (raster workflow):** the GeoTIFFs carry no scale/offset tags (GDAL reports defaults 1/0) and header nodata −9999. Scaling is therefore declared in configuration: scale 1, offset 0, nodata −9999 (D-026).
- **Day grouping:** naive-index calendar day (§3).
- **Layer construction (code-inferred, fluxfootprints `summarize_periods` at 424ec20):**
  - `mean` = plain mean over timesteps with positive mass.
  - `etw` = `Σ f_t·ET_t / Σ ET_t`, with ET = `LE/680.6` by default and negative ET not clipped. The current workflow passes clipped `nldas_et` instead.
  - Neither layer has a duration factor. The exported rasters are renormalized to sum 1.
  - The domain CSV's `valid_timesteps` counts timesteps with positive mass (7–26 per day); the timestep length is not recorded.
  - Controlled `temporal_weighting`: set B `mean` = `time_mean`; the `etw` sets = `reference_et_weighted`: NLDAS grass ETo with negative values kept, verified by reproduction (D-036).
- **Per-timestep footprints:** none were on the Drive. A US-UTJ stack was rebuilt from the production inputs (D-037). It is the source of the primary NLDAS-ETo set and the measured-LE sensitivity set.

## 8. Optional inputs

| Input | Status |
|---|---|
| Meteorology | Daily station T, RH, VPD, wind speed, radiation, precipitation; gridMET ETo/ETr/precip. No wind direction; no daytime H |
| Land cover, NDVI/EVI | IGBP class only |
| Irrigation / cutting records | Not supplied |
| Soil moisture | Not in the daily CSV |
| Satellite acquisitions | Not supplied. `OpenET_Comparison.ipynb` cell 15 queries Landsat C2 L2 scene metadata (cloud cover is scene-level) from Planetary Computer. Scene-level cloud cover is not a per-pixel clear-observation flag at the station |
| Polygons | Download polygon `bluff_cedar.shp` (export extent only, not a field boundary). No field polygons |

## 9. Mandatory semantics and validation behaviour

Validation runs before any join or calculation. A missing mandatory item raises `ContractError` naming the input, the field and the configuration key. An optional item left blank is recorded as `unknown` and disables only the analyses that need it.

| Item | Level | Failure message pattern |
|---|---|---|
| Station `site_id` (CSV adapter argument) | Mandatory | `station table <path> has no site column; pass site_id=` |
| Station `timezone`, `day_definition`, `interval_label` | Mandatory | `station US-UTJ: day_definition not declared (config: stations.US-UTJ.day_definition)` |
| Units for each reference-variant column | Mandatory | `ET units for column 'ET_corr' not declared; magnitude is not evidence` |
| Reference-variant mapping (`et_raw`, `et_corrected`, gap flag) | Mandatory | `reference variant 'et_corrected' has no source column` |
| Model ET units | Mandatory | as above |
| Model `day_definition` | Mandatory, may be `unknown` | Outputs flagged |
| Model `temporal_support` (`interpolated_daily` / `overpass_only` / `unknown`) | Mandatory, may be `unknown` | Complete and estimated period totals are withheld for `overpass_only` and `unknown` (`validity_reason=temporal_support_<value>`); partial sums remain (D-041) |
| Raster scale, offset, nodata (raster workflow) | Mandatory | Header values accepted only when declared `from_header`; NaN is always missing |
| Raster CRS (raster workflow) | Mandatory | `raster <path> has no CRS` |
| Footprint `value_semantics`, `weighting_method`, footprint set ID (raster workflow) | Mandatory | `footprint value semantics not declared` |
| Extraction-method label for each pre-extracted column | Mandatory | `column 'ET_mean' needs an extraction_method label` |
| Crop-height table (growing season): date, height and units, season start/end columns, site | Mandatory when `growing_season_source` is declared | `crop_height_tables.<name>: height_units=... is not one of ['cm', 'm']`; season inconsistencies raise `DataValidationError` (D-042) |
| Station reference ET column and units (`reference_et_fraction` imputation) | Mandatory for that method | `ET units for column 'gridMET_ETr' not declared; magnitude is not evidence` |
| Metric near-zero tolerances (`metric_sets[].daily_tolerance`; `period_tolerance` when period totals are scored) | Mandatory when metrics are computed | `metric_sets.<id>: daily_tolerance (mean, std in mm d-1) not declared` (D-044) |
| Bootstrap settings (`bootstrap[]`: block lengths, seed, replicate count, confidence level, `min_blocks`, `segment_boundary`) | Mandatory when intervals are computed | `bootstrap.<id>: seed not declared; intervals must be reproducible`; too few disjoint blocks give `status=insufficient_support`, not an interval (D-047) |
| Ranking settings (`rankings[]`: metric set, bootstrap, ranked metrics with directions matching `RANKING_DIRECTIONS`; `multiplicity` when `formal_tests` is true) | Mandatory when rankings are computed | `rankings.<id>: metric 'bias_corrected_rmse' cannot be ranked`; `formal_tests needs an explicit multiplicity policy`; a coverage comparison gives `has ranking scope 'none'`. Equivalence margins are optional: without one, equivalence is `not_assessed` (D-049) |
| Coordinates, land cover, product/model version, closure method, gap-fill method, original capture, acquisition flags | Optional | `unknown`; dependent analyses report `not_available` |

A filename date gives a label, never temporal support. Pandas metadata in a Parquet file is serialisation metadata, not scientific provenance.

## 10. Remaining unresolved items

| Item | Best lead |
|---|---|
| Interval label (start vs end) of the half-hourly station data | Logger/EasyFlux configuration |
| OpenET daily day boundary; meaning of `resample: 0`; model versions | OpenET API documentation; download dates on the tracking sheet |
| Footprint set A: weighting variable and negative-ET handling | The workflow version run on 2026-10-05 |
| Which footprint set is authoritative for re-extraction | User decision (D-016) |
| flux-data-qaqc version, closure method/window, gap-fill settings | The processing configuration for `daily_output` |
| Units of `*_subday_gaps`, radiation and closure terms | flux-data-qaqc `units` dictionary at export time |
