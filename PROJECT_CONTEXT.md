# Project context: OpenET–eddy covariance comparison library

**Context version:** 1.0, prepared 2026-10-12 (America/Denver).  
**Audience:** AI coding agents implementing the comparison library.  
**Working package name:** `etcompare` (provisional).  
**Companion specification:** `openet_ec_library_ai_implementation_guide.md`.  
**Progress tracking:** Maintain a separate `IMPLEMENTATION_STATUS.md` during development.

> **Superseded in part (2026-10-13).** Step 1 produced `docs/input_contract.md` and `docs/decisions.md`. Where they conflict with this file, they take precedence. Main changes:
> - **Extraction semantics are verified:** `ET_fp`, `ET_mean` and `valid_weight_fraction` were reproduced from the producer notebook (`OpenET_Comparison.ipynb`). `ET_mean` averages a footprint area that changes daily. `valid_weight_fraction` is retained weight after truncation, not capture.
> - **Units and reference ET:** OpenET units are mm/day per the API request, which also used gridMET reference ET.
> - **Station time:** UTC−7 (fixed offset).
> - **Missing days:** the 61 unextracted dates are rejected footprints (18 empty, 43 with negative weights).
> - **Two US-UTJ footprint sets exist:** the top-level files (used for the Parquet) and a newer `weighted/` set. The weighting method of the set actually used is unverified.
>
> The tables and counts below are still correct as a snapshot of the two example files.

## 1. Purpose and evidence status

Create a reusable Python library to compare quality-controlled eddy covariance (EC) station evapotranspiration against individual OpenET models and the supplied OpenET ensemble. Evaluate accuracy, spatial extraction choices, energy-balance correction, temporal aggregation, environmental conditions, and uncertainty in model comparisons.

This file records project background and verified properties of two example inputs. It does not report model performance or claim the library has been implemented. The implementation guide defines the detailed work plan and tests.

Interpret statements using these categories:

- **User-provided:** Information explicitly supplied about the workflow.
- **Verified:** Computed from the two example files inspected for this context.
- **Proposed:** A library design choice that can be configured or revised.
- **Unresolved:** Requires upstream processing configuration, source code, metadata, or user confirmation. Never silently promote an unresolved detail to fact.

The data findings here apply to these exact example files, not necessarily all stations or future exports. If files change, regenerate the input profile and record the change. Updated verified metadata takes precedence over this snapshot.

## 2. Existing workflow and project boundaries

The user has already:

1. Processed EC measurements into daily values and performed QA/QC using `flux-data-qaqc`: https://github.com/inkenbrandt/flux-data-qaqc.
2. Generated weighted footprint rasters using `fluxfootprints`: https://github.com/inkenbrandt/footprints.
3. Downloaded daily ET rasters from multiple OpenET models for station areas.
4. Supplied a table containing footprint-weighted and unweighted spatial ET summaries, plus a daily station export.

Build on prepared data. The initial library should accept the supplied tables directly; raster re-extraction is an additional workflow, not a prerequisite for every comparison. Do not rerun raw EC processing or energy-balance correction by default. Do not alter upstream repositories or publish a package without authorization.

The source-path strings show a Colab/Google Drive workflow. Keep paths configurable and support ordinary Windows/Linux paths. Do not require access to the original Drive paths to analyze the supplied Parquet table.

## 3. Example file inventory

| Property | Station observations | Extracted OpenET estimates |
|---|---|---|
| Filename | `US-UTJ_daily.csv` | `station_oet_US-UTJ.parquet` |
| Rows × columns | 492 × 44 | 2,336 × 10 |
| Station | `US-UTJ`, assigned from filename | `US-UTJ`, present in every row |
| Date column | `date`, ISO date text | `footprint_date`, timezone-naive `datetime64[ns]` |
| Date range | 2024-06-05 through 2025-10-09 | 2024-06-05 through 2025-10-09 |
| Distinct dates | 492; uninterrupted calendar sequence | 431; not a complete daily sequence |
| Key | Assigned `site_id` + date | `station_id` + `model` + `footprint_date` |
| Duplicate keys | None | None |
| ET availability | Varies substantially by column | No null values in any of the 10 columns |

Neither input provides a complete scientific units dictionary, station timezone, station coordinates, product-version record, or raster grid definition. Pandas/Arrow serialization metadata in the Parquet file is not equivalent to scientific provenance.

**SHA-256 fingerprints of inspected bytes:**

```text
US-UTJ_daily.csv
3924e02d30d2e8aed8daea30fc56fd9b2c002031f7fa53e8f378bc8e3652c60f

station_oet_US-UTJ.parquet
e0d5c1afff7d7ca5663c1ded28e0705302754b9cbea44d562f84eae0ae544743
```

## 4. OpenET table: complete schema and mappings

The five identifier/path fields are strings, `footprint_date` is a timestamp without timezone, and all four numeric columns are float64.

| Input column | Proposed use | Interpretation and limitations |
|---|---|---|
| `station_id` | `site_id` | Preserve exact identifier |
| `model` | `model` | Preserve lower-case product labels; ensemble is a separate product |
| `footprint_file` | Provenance / footprint identifier | Identifier stem, without an extension in the example |
| `openet_file` | Provenance / raster filename | Can contain multiple station IDs |
| `openet_path` | Source path | Original runtime path; treat as provenance unless locally resolvable |
| `footprint_date` | `date` | Day label for pairing; verify station-day semantics |
| `ET_fp` | Modeled ET, method `footprint_weighted` | Already extracted; do not apply footprint weights again |
| `ET_mean` | Modeled ET, method `source_region_mean` | Unweighted regional mean per workflow description; exact extent and masking unresolved |
| `unity_check` | Weight normalization diagnostic | Approximately 1 throughout; not proof of complete original footprint capture |
| `valid_weight_fraction` | Source-support diagnostic | Fraction label suggests retained valid contribution; exact denominator must be checked in generating code |

Use `source_region_mean` as the neutral method label until the extraction geometry is verified. Do not relabel `ET_mean` as a field mean, 3×3 mean, or basin mean from the column name alone.

Representative source identifiers:

```text
footprint_file: ffp_US-UTJ_daily_etw_20240605
openet_file: US-CdM_US-UTJ_eemetric_et_2024_06_05_779b6.tif
openet_path: /content/drive/MyDrive/OpenET_Daily_Tiffs/US-UTJ_US-CdM/eemetric/US-CdM_US-UTJ_eemetric_et_2024_06_05_779b6.tif
```

The `daily_etw` stem suggests ET-weighted daily aggregation but does not establish the weight definition, input ET variant, nighttime treatment, or handling of negative fluxes. Verify these from the footprint-generation workflow.

Verified numerical diagnostics:

- `ET_fp` ranges approximately 0.000007–12.328382; `ET_mean` ranges 0.004108–9.322854.
- `unity_check` is approximately 1, with standard deviation about 3.13e-9; use a numerical tolerance, not exact equality.
- `valid_weight_fraction` ranges approximately 0.213974–1.0.
- 180 extraction rows have `valid_weight_fraction < 0.90`. This is a descriptive count, not an adopted quality threshold, and rows are model-date combinations rather than distinct days.
- No `valid_weight_fraction` exceeds `1 + 1e-8`.

Proposed ET unit is mm per station day, consistent with the intended comparison. Treat units as unresolved until verified from the export workflow; numeric magnitude alone is not sufficient. Preserve already-scaled values unless the actual export metadata specifies another conversion.

## 5. Unequal model coverage

| Model | Extracted rows/dates | First date | Last date | Paired with non-null `ET` | Paired with non-null `ET_corr` | Paired `ET_gap=True` days |
|---|---:|---|---|---:|---:|---:|
| `eemetric` | 431 | 2024-06-05 | 2025-10-09 | 274 | 429 | 155 |
| `ensemble` | 431 | 2024-06-05 | 2025-10-09 | 274 | 429 | 155 |
| `ssebop` | 431 | 2024-06-05 | 2025-10-09 | 274 | 429 | 155 |
| `disalexi` | 261 | 2025-01-03 | 2025-10-09 | 172 | 261 | 89 |
| `geesebal` | 261 | 2025-01-03 | 2025-10-09 | 172 | 261 | 89 |
| `ptjpl` | 261 | 2025-01-03 | 2025-10-09 | 172 | 261 | 89 |
| `sims` | 260 | 2025-01-03 | 2025-10-09 | 172 | 260 | 88 |

These are availability counts before additional quality, source-support, or scientific-eligibility filtering. Do not infer why any day/model is absent: missing footprint generation, missing exports, extraction failures, and satellite availability are distinct possible causes.

All seven products share 260 dates. Of those, 172 have raw ET and 88 have gap-filled corrected ET with raw ET absent. All 260 have non-null corrected ET.

The same source has 61 station dates with no OpenET extraction rows. Missing rows are different from null values within present rows.

## 6. Station table: ET reference semantics

The most important finding is that `ET_corr` is not a purely closure-adjusted version of measured ET on every date. It also contains gap-filled values.

| Column | Non-null count | Proposed role |
|---|---:|---|
| `ET` | 303 | Uncorrected daily station ET input; prior subdaily processing still applies |
| `ET_corr` | 473 | Corrected reference, including gap-filled values on flagged days |
| `ET_fill` | 472 | Auxiliary filling series; exact intended semantics unresolved |
| `ET_gap` | 492 | Boolean flag; 170 True, 322 False |
| `ET_fill_val` | 170 | Values specifically associated with flagged filled days in this example |
| `ebc_cf` | 458 | Closure correction factor; preserve as metadata |

The following relationships were verified:

| Raw ET present | Corrected ET present | `ET_gap` | Days |
|---|---|---|---:|
| Yes | Yes | False | 303 |
| No | Yes | True | 170 |
| No | No | False | 19 |

On all 303 raw-ET days, `ET_corr = ET * ebc_cf` within floating-point precision. On all 170 flagged days, `ET_corr` equals `ET_fill_val` within numerical tolerance. `ET_fill` differs from `ET_corr` on 302 rows with both values present, so these columns are not interchangeable.

**Do not use `ET_gap=False` alone as proof that measured ET exists:** 19 such rows have neither raw nor corrected ET. Derive explicit reference provenance from the combination of non-null values and flags, preserving original columns.

Proposed derived categories for this adapter:

- `raw_available`: `ET.notna()`; does not assert absence of subdaily interpolation.
- `corrected_from_raw`: `ET.notna() & ET_corr.notna() & ~ET_gap`.
- `corrected_filled`: `ET.isna() & ET_corr.notna() & ET_gap`.
- `reference_missing`: `ET.isna() & ET_corr.isna()`.
- `inconsistent_flags`: any unanticipated combination; report rather than silently coercing.

No non-null `ET`, `ET_corr`, `ET_fill`, or `ET_fill_val` values are zero or negative in these examples. This does not authorize clipping negative values in other inputs.

## 7. Remaining station columns

Preserve all source columns. The following meanings are provisional mappings based on their names and workflow, not verified measurement-unit metadata.

| Source columns | Intended role / verification needed |
|---|---|
| `date`, `DOY` | Date and integer day-of-year; compute pairing from date |
| `le_1_1_1`, `h_1_1_1`, `g_1`, `netrad_1_1_1_final` | LE, H, ground heat, net radiation; verify W/m² and daily aggregation |
| `LE_corr`, `H_corr` | Corrected flux products; `LE_corr` is also populated on filled ET days |
| `flux`, `energy`, `flux_corr` | Energy-balance diagnostic terms; verify definitions |
| `ebr`, `ebr_corr`, `ebr_5day_clim`, `ebc_cf` | Closure diagnostics; inspect processing configuration before interpreting windows or ratios |
| `lw_in_1_1_1`, `lw_out_1_1_1`, `sw_in_1_1_1`, `sw_out_1_1_1`, `rso` | Radiation inputs/diagnostics; verify units and derivations |
| `ta_final`, `t_min`, `t_max`, `t_dew` | Air/dew-point temperature; verify degrees Celsius |
| `rh` | Relative humidity; verify percent versus fraction |
| `es`, `vp_final`, `vpd` | Vapor pressures and deficit; verify kPa |
| `ws_1_1_1` | Wind speed; verify m/s and measurement height |
| `p_1_1_1` | Precipitation candidate; confirm measurement and aggregation |
| `gridMET_ETr`, `gridMET_ETo`, `gridMET_prcp` | Gridded reference ET and precipitation; all 492 rows populated |
| `ETrF`, `ETrF_filtered` | Reference-ET fractions; `ETrF` has 303 non-null values, filtered series 472 |
| `H_subday_gaps`, `LE_subday_gaps`, `G_subday_gaps`, `Rn_subday_gaps` | Gap diagnostics; counts/units/denominators unresolved; each has 237 non-null values |

Together with the six fields in Section 6, these groups account for all 44 CSV columns. Never interpret null gap diagnostics as zero gaps or convert them to observed-data percentages without knowing their definitions.

There are 67 negative `h_1_1_1` values among 316 non-null values. These are from a daily export. They cannot supply a daytime negative-H frequency or prove advection. Wind direction is not present, so wind-sector diagnostics require another input.

## 8. Calendar completeness and evaluation implications

The station CSV contains a complete sequence of date labels but incomplete ET measurements. In May 2025, raw ET is missing on all 31 days, while corrected ET is present on all 31. Accordingly, a comparison for that month uses reconstructed station reference values, not raw-available EC ET.

The input begins partway through June 2024 and ends partway through October 2025. Neither year is fully represented. Do not present annual totals from this example as complete calendar-year totals. No full-year held-out evaluation is available merely because two calendar-year labels occur.

For every monthly/seasonal result, report expected calendar days, available model days, raw-available reference days, filled-reference days, matched days, and completeness. Missing paired days cannot disappear from the denominator. Distinguish partial sums from full-calendar totals and do not extrapolate by day count by default.

## 9. Initial comparison plan for these inputs

Implement the pre-extracted-table path first, while raster-specific modules follow the companion guide.

1. Assign `site_id='US-UTJ'` to the CSV through an explicit adapter argument, and validate it against the Parquet station IDs.
2. Parse and preserve day labels. Join on site/date using a many-model-rows-to-one-station-row relationship. Never infer UTC or shift dates from the user's timezone.
3. Reshape `ET_fp` and `ET_mean` to separate extraction methods while preserving source diagnostics and identifiers.
4. Build an all-seven-product cohort for controlled rankings. There are 260 available dates before station-reference restrictions.
5. Build the matched raw/corrected sensitivity cohort from the 172 common dates with both reference variants and `ET_gap=False`.
6. Build a separate corrected-inclusive analysis across the 260 common dates, explicitly identifying its 88 filled-reference dates.
7. Retain model-specific coverage summaries; their 2024/2025 differences must not masquerade as fair head-to-head rankings.
8. Apply any adopted weight-support or quality filters before scoring, recalculating all counts. If comparing extraction methods or references, freeze dates across both sides.
9. Report daily bias, MAE, RMSE, correlation, and appropriate totals with cohort metadata. Add paired block intervals once the cohort and calendar logic are verified.

The 172 and 260 counts are regression checks for these exact unfiltered files, not hard-coded library requirements. The official ensemble is a comparison product; do not treat it as a seventh independent member when fitting a new ensemble from six models.

## 10. Unresolved input contract

| Missing detail | Consequence / resolution |
|---|---|
| Station coordinates, land cover, height, timezone/day definition | Obtain station metadata before spatial or process interpretation |
| ET units and raster scaling | Verify upstream exports; proposed mm/day is not metadata confirmation |
| OpenET collection/model versions and export options | Record exact versions; distinguish overpass-only from interpolated daily data |
| Reason for missing model dates | Inspect extraction/export logs; do not infer cloud cover from missing rows |
| `ET_mean` extent, masks, and averaging method | Inspect extraction code; retain neutral source-region label |
| `valid_weight_fraction` denominator and `unity_check` stage | Inspect extraction code; sum near one does not establish full source capture |
| Footprint density versus integrated weights, original capture, grids/CRS | Requires rasters and generation metadata; table alone cannot verify conservation |
| Meaning of `etw`, which ET was used, negative/night/gap policy | Requires footprint-generation configuration |
| Closure and gap-fill algorithms and versions | Existing numerical relations do not establish full processing methodology |
| Subdaily valid fractions and gap diagnostic meaning | Cannot label raw-available days as fully observed |
| Cloud-free acquisition flags and interpolation support | Cannot analyze clear versus interpolated days from filenames alone |
| Land cover, NDVI/EVI, soil moisture, management events, independent water balance | Optional diagnostic modules remain unavailable without added inputs |

Do not block schema adapters, synthetic testing, or table-based development on all optional metadata. Production calculations requiring unresolved units/day semantics must request explicit configuration and label provisional outputs accordingly.

## 11. Scientific rules to retain across sessions

- Compare competing models on identical dates for each declared ranking.
- Separate closure sensitivity from gap-filling sensitivity. Here, unrestricted `ET` versus `ET_corr` comparisons mix both effects.
- Treat measured-ET-weighted footprint extraction as a distinct method that partly depends on the reference observations.
- Do not multiply or weight the already-extracted `ET_fp` by station ET again.
- Treat normalized footprint sum, raster-valid contribution, and full modeled-domain capture as different quantities.
- Do not reconstruct common pixel masks or alternate footprint aggregations from these summary tables.
- Treat corrected/raw differences and model spread as sensitivity/disagreement, not automatically calibrated uncertainty bounds.
- Do not treat shared meteorological drivers in station gap-filling and satellite interpolation as independent validation evidence.
- Preserve missing days in time-dependent analysis; do not compress gaps into consecutive daily samples for block resampling.
- Distinguish Pearson correlation squared from predictive R², and bias-corrected diagnostic RMSE from a validated calibrated product.
- Fit ensemble weights and preprocessing only on training groups. One station cannot establish transfer to other sites.
- Do not select a model, reference, quality threshold, or extraction method merely because it maximizes agreement on the evaluation data.

## 12. Literature context

The companion implementation guide lists nine supplied studies and their bibliographic details. Use Volk et al. (2024) for the national paired-comparison framework, Knipper et al. (2026) for closure/advection and management-response sensitivity, Reitz et al. (2025) for complementary metrics and held-out weighting, and Huntington et al. (2022) for Upper Colorado River Basin footprint comparisons.

Retain the evidence distinctions already identified: Djaman uses crop-coefficient reference ET; Tawalbeh reconstructs the comparison-period reference from historical coefficients; Montazar uses residual energy balance for its irrigated reference and soil-water depletion for deficit assessment; Osorio-Diaz addresses water-balance residuals with unresolved exchanges. Do not describe all nine as equivalent contemporaneous direct latent-heat EC validations.

This context's new numerical findings derive from the two supplied data files, not those publications. Consult the papers for scientific claims; consult input metadata for station-specific definitions.

## 13. Coding-agent startup and maintenance

Read this file, `openet_ec_library_ai_implementation_guide.md`, repository instructions, and `IMPLEMENTATION_STATUS.md` at the start of each session. Begin with the table adapter and cohort logic supported by these examples. Add analytical tests for the critical gap-flag combinations and unequal model coverage.

Keep context stable. Update it when verified input semantics or scope changes, including the date and evidence. Record implementation progress, test outcomes, and next tasks in the status file. Record algorithms, parameter decisions, and unresolved technical questions in dedicated method/decision documentation.

Do not claim the source raster paths are accessible until checked in the user's runtime. Do not expose or package private station data publicly without permission. Use small synthetic fixtures for distributable tests and keep user-supplied examples as local integration inputs unless authorized otherwise.

**Current handoff (updated 2026-10-20, after step 18):** All 18 guide steps are implemented. Version 0.1.0 (MIT) is ready on branch `step18-release`. It is unpublished, unpushed, and has no Read the Docs project yet.
- **Resilient processing (step 18; D-079):** a new `processing:` section.
  - Keep-awake on Windows; the D-078 9-hour pause was idle sleep.
  - Retries of transient read errors: 3 attempts, 1 s and 4 s backoff, logged as `io_retry`.
  - `raster_missing` for files that vanished after indexing.
  - Stall and suspend detection; `stall_timeout_s` is off by default.
  - Monthly extraction checkpoints keyed by the unit's cache key.
  - Unreadable files keep null rows, are counted in exclusions and the manifest, and are listed in the report.
  - A systematic failure (every file of a model, every file, or more than `max_unreadable_fraction`) stops the run with exit 2.
  - Atomic writes for every output; the manifest is written as `running` at the start.
  - **No concurrency and no staging** (user decisions). The serial benchmark: J: 22 s and M: 90 s per US-UTJ extraction, 86–95 % file-open I/O.
  - The real-station tables (US-UTJ 197, Utah 58) are bit-identical before and after.
- **Release (D-080):**
  - Sphinx/MyST documentation for Read the Docs: method guide, configuration, limitations, API, a generated data dictionary, references checked against Crossref.
  - `CHANGELOG.md`, GitHub Actions CI (not pushed), and a pinned demonstration environment (`examples/demo/requirements-demo.txt`).
  - Three committed notebooks, outputs stripped.
  - `etcompare demo DIR --rasters`: a full offline raster demonstration.
  - `examples/us_utj.yaml` now reads the `unclipped/` Drive folder (D-054 resolved).
- **Open:** publishing (PyPI, Read the Docs, pushing CI) needs the user's authorization; every decision listed in `decisions:` of the example configs; the gaps in `docs/limitations.md`.

**Previous handoff (2026-10-19, after step 17):** Guide steps 1–17 are implemented, and step 18 (resilient processing and a first release) is next.
- **Workflow and CLI (step 17; D-074 to D-077):** `etcompare validate|index|extract|analyze|report|run config.yaml` (`python -m etcompare …` where the console script is blocked). The CLI and `tools/run_*.py` are thin layers over `etcompare.workflow.Workflow` units, which call the step 5–16 functions unchanged (177 US-UTJ tables reproduced bit for bit).
  - Everything is declared in the configuration. The former flags are `run.skip`, `crop_height_tables[].on_missing`, `raster_extractions[].strict`, `footprint_stacks` and `footprint_comparisons`.
  - Per-unit cache keyed on the code, the configuration entries read and the input identities. Rasters use size + mtime by default (`cache.raster_identity: sha256` opt-in; hashing J: takes about 11 min). Invalidation is exact and tested.
  - Each run writes a manifest (config hash, inputs, versions, git, upstream, grids, masks, seeds, exclusions, unit statuses) and a JSON log under `<output_dir>/runs/<run_id>/`.
  - Reports (Markdown plus self-contained HTML; PNG/SVG figures with the rows they drew) are built only from saved tables, with a skipped-analyses table, open decisions (`decisions:` plus status columns) and guarded wording.
  - `etcompare.demo.write_demo` writes a fixed-seed synthetic dataset and config (stations `XX-SY1..3`, labelled synthetic throughout).
  - **Open:** none new; every earlier open decision is now listed in `decisions:` of the example configs and in each report.
- **Ensembles (step 16):** `etcompare.ensemble.run_ensemble` (D-065) compares the official ensemble, the mean and median of the declared members, and an optional fitted ensemble on the same frozen days. It reuses `build_cohort`, `score_comparison`, `bootstrap_comparison` and `rank_comparison`.
  - Ensembles are post-extraction, never pixel-level. The official ensemble is never a member, and measured-LE footprints never enter.
  - Fitted weights are simplex least squares shrunk toward equal weights. α is chosen by inner grouped folds on training data, with a time buffer. Every fold's membership, weights, α, inner scores and member availability are saved.
  - Too little independent data gives `declined` (or `exploratory`, in sample only). One station gives `not_available` for leave-one-site-out. Member spread is disagreement, not an interval.
- **US-UTJ (D-066, user decisions 2026-10-18): the fitted ensemble is declined.**
  - The declaration: six members, `et_corrected_measured`, `require_all`, calendar-year folds, a 28-day buffer, ≥ 2 training folds of ≥ 180 days, α ∈ {0, 0.25, 0.5, 0.75, 1}, and the primary NLDAS footprints.
  - The six coexist only in 2025 (188 measured days), so there is one fold. Water-year folds and an eemetric + ssebop pair would be declined too. No weights and no held-out improvement exist.
  - Baseline on 188 frozen days (nothing fitted): MAE official 0.699, mean 0.683, median 0.725 mm d⁻¹. No contrast is separated. `tools/run_ensembles.py` runs it.
  - **Calendar years are the project standard** (user, 2026-10-18): the focus is the April–October growing season, which a water year would split.
  - **Seven stations (D-069 to D-071, 2026-10-19):** `examples/utah_stations.yaml` declares US-UTD, UTE, UTG, UTJ, UTM, UTV and UTW.
    - Footprints were rebuilt as NLDAS-ETo weighted from stacks (US-UTM uses its production set), and OpenET is the clipped raster set (identical to the unclipped values for US-UTJ).
    - Leave-one-year-out has no held-out claim.
    - Leave-one-site-out is held out on 1,798 station-days: lower RMSE than the member mean at 6 of 7 stations (equal-station mean −0.058 mm d⁻¹), but worse at the corn station US-UTG. Few station intervals are separated, and there is no network interval.
    - **Update (D-072, 2026-10-19):** the rasters now come from `J:/FLUX/OpenET_Daily_Tiffs`, and `min_test_days_per_fold: 180` is set. An extraction bug that hid rasters after a wrong-region file is fixed.
      - Leave-one-year-out is declined.
      - Leave-one-site-out: 6 of 7 folds fitted (US-UTG declined at 41 days). RMSE is lower than the mean at 5 of 6 stations (equal-station mean −0.168 mm d⁻¹).
      - US-UTG keeps the default ffp_xr roughness-sublayer filter (zm > 27.5·z0), which explains its missing footprints.
    - **Update (D-073):** the wrong-region US-UTE files were swapped on J: (quarantined, with a log), and US-UTM was rebuilt with production's constant heights (the UGS parameter table has no US-UTM rows).
      - 1,839 eligible days.
      - Leave-one-site-out: fitted RMSE below the mean at 5 of 6 stations (equal-station mean −0.166 mm d⁻¹), supported at 14-day blocks at US-UTE and US-UTV.
    - **Open:** constant heights at US-UTM, the corn station below the minimum, and no network interval.
- **Water balance (step 15):** `etcompare.water_balance.compute_water_balance` (D-062) checks `residual = P + I + CR − ET − RO − DP − ΔS` over a declared boundary and the step-8 periods.
  - Every term is declared. An unknown term is never zero and makes the residual unknown, with every unknown term listed.
  - Scenario residuals are separate rows. Support mismatches are flagged, never equated.
  - A period total needs every calendar day (D-041), and ET gap-filled days are counted.
  - Volumes need a known area. Residuals are plausibility diagnostics only: never ET error, never a selection basis.
- **Scenarios (step 15):** `etcompare.scenarios.run_scenario` (D-063) takes only declared distributions (with justification and dependence) or declared alternatives. Measurement, footprint-parameter and method-choice results are kept separate, and sampling is the step-10 bootstrap. Footprint uncertainty comes only from supplied realizations.
- **US-UTJ (D-064): the water balance is unknown.**
  - Irrigation, capillary rise, runoff, deep percolation and storage change have no input.
  - The tower field (98,147.74 m², vintage unknown) is the boundary. ET (corrected-inclusive, footprint support) and gridMET precipitation (cell support) are flagged support mismatches. WY2025 term totals are ET 888.9 mm (127 days gap-filled) and P 131.9 mm, reported as totals only.
  - `p_1_1_1` is not used (bucket factor, D-059). No scenario is declared. `tools/run_water_balance.py` runs it.
  - **Open:** irrigation records, a soil-moisture series and permission to read logger files, use of `p_1_1_1`, the ET variant, gridMET units and day definition, the boundary area and vintage, other terms and any scenarios.
- **Station events (step 14):** `etcompare.adapters.event_log.adapt_event_table` (D-060) reads the declared `event_tables:` log into `station_events`. Events come only from the log. Each keeps its observer, source and notes, an evidence class and an `approximate` flag set by a declared text rule. Instrument events are `overlap_only`.
  - For US-UTJ: 6 cuttings (3 Planet, 3 camera; 2024-09-08 approximate), all confirmed by farm records (user, 2026-10-17), and 11 instrument overlaps. There are no irrigation events.
- **Event responses (step 14):** `etcompare.events.analyse_events` (D-061) gives the baseline, change, cumulative response and recovery per cutting and series, with overlaps, weather and gap-filled days kept visible. The design is uncontrolled before/after, never causal evidence.
  - **US-UTJ:** after every cutting with measured reference days, every model drops less than the station (by 0.15–2.70 mm/day).
  - 2025-05-28 cannot be assessed: its windows are entirely gap-filled.
- **Anomalies (step 14):** `etcompare.anomalies.compute_anomalies` (D-061) offers a held-out climatology and an exploratory moving mean. US-UTJ has too few years for held-out skill; only in-sample exploratory anomalies exist.
- `tools/run_events.py` runs both. All step-14 defaults are Provisional (chosen by the assistant at the user's request).
- **Open:** the step-14 defaults, whether mostly gap-filled windows should be excluded, a control design, and the class for the `eddy`/`met`/`both` sources.
- **Landscape representativeness (step 13):** `etcompare.representativeness.assess_landscape` (D-055) describes what each support sees. Supports are the daily footprint, the D-029 3×3 window and nearest pixel on the model grid, and declared polygons.
  - Weights move onto each map's native grid by exact area overlap; classes are never resampled.
  - Denominators are separate: valid / grid total, nodata, outside, and class / valid. The weighted index SD is the population form, with Kish `n_eff`.
  - 80 % contour rows come from the footprint alone and sit next to the full-domain rows.
  - Every declared matching policy runs side by side, with days-from-layer, year mismatch and `retrospective` on every row. A day without an admissible asset is `not_available`, never filled.
  - Settings are in the `representativeness:` section (Provisional). `tools/run_representativeness.py` runs it.
  - **Inputs (user, 2026-10-17; D-054):** Annual NLCD 2023–2025, CDL 2024 (30 m), and `Bluff.gpkg` (EPSG:26912 and tower field 4940083 declared; vintage unknown). No NDVI and no acquisition metadata.
  - **US-UTJ run (D-058):**
    - The footprint is never the field. Full domain: NLCD crops 0.39 and woody wetlands 0.57; CDL alfalfa 0.70 and shrubland 0.28. The field polygon is about 0.9 crop in both maps.
    - NLCD and CDL disagree on the riparian strip east of the tower; even the tower's OpenET pixel differs (woody wetlands vs alfalfa).
    - Independent point sampling reproduces the shares to ≤ 2.7e-4.
  - **fluxfootprints labels** (0.5.1 @ 6b7311d, installed editable but not as an extra; D-056) are stored as labels only: almost all `low`, never an accuracy guarantee.
  - IGBP `CRO` is a site label, not a footprint fraction.
  - Three corrupted footprint files per etcompare-built set were restored in this checkout, and `prepare_footprint` now rejects impossible values.
- **Satellite support (step 13):** `etcompare.satellite` (D-057) accepts only authoritative clear-observation flags or counts. Clear and interpolated subsets are identical across models of a frozen cohort, and metrics come via `compute_metrics`. For US-UTJ there is no metadata (D-011), so `run_cohorts.py` writes `not_available` for the four ranking cohorts.
  - **Open:** the matching policy, the contour level, which map describes the riparian strip, the polygon vintage, a vegetation-index series, a stale `openet_rasters.root`, and the carried-over decisions below.
- **Diagnostics (step 12):** `etcompare.diagnostics.diagnose_comparison` (D-051) describes one frozen cohort.
  - **Closure:** correction magnitude from the existing columns (`ET_corr − ET`, the ratio, `ebc_cf`), on `corrected_from_raw` days only. It is labelled reference sensitivity, not uncertainty.
  - **Provenance:** observation-dominated vs gap-filled subsets with identical days across models, and the gap-filled fraction per period.
  - **Conditional errors** per model and declared bin, via `compute_metrics`. Bins are fixed before any error exists, and sparse bins carry a status instead of a value.
  - **Process diagnostics:** a labelled negative daily-mean H bin; wind sectors wrapping at 0/360; net-radiation regressions, corrected (coupled) and raw.
  - **`not_available` rows with reasons:** undeclared or unverified variables, satellite support (D-011), and wind direction for US-UTJ.
  - **Rank changes between reference variants** are taken from `ranking_sensitivity`, never re-ranked. Settings are in the `diagnostics:` section (Provisional).
  - **US-UTJ run (D-052):**
    - Closure (raw vs corrected, same days) moves 6 of 7 models by up to 5 positions, more than adding gap-filled days does.
    - Gap-filled days are concentrated in May–August 2025 and share gridMET drivers (D-005).
    - 28-day blocks give no bin interval, and the 7- and 14-day intervals are probably too narrow (D-048).
    - Station meteorology units were confirmed by the user, with `US-UTJ.ini` as support (D-053). The `.ini`'s hPa VPD is the half-hourly input; the daily export is kPa. The subdaily gap columns remain unverified.
  - Conditional errors are associations, not causes.
  - **Open:**
    - the closure window behind `ebr` and `ebr_5day_clim`;
    - a daily wind direction, which exists only half-hourly;
    - the diagnostic defaults (`min_bin_days` 20, bin edges);
    - the carried-over decisions below.
- **Rankings (step 11):** `etcompare.ranking.rank_comparison` (D-049) ranks models only inside frozen `all_models`/`pairwise` cohorts, per context, and only on score metrics. Directions are declared once, in `config.RANKING_DIRECTIONS`.
  - Rank stability and model-contrast intervals come from the step-10 replicates, at every block length side by side.
  - A unique leader is declared only when intervals separate the best model from all others.
  - Equivalence is `not_assessed` without a declared margin, and every contrast is exploratory unless formal tests are requested.
  - Settings come from the `rankings:` section (Provisional).
  - **US-UTJ run (D-050):**
    - No all-model cohort has a unique leader, and raw versus corrected reference moves the rankings most.
    - Network summaries have `n_sites = 1` and are not network evidence.
    - 28-day blocks lack support, and the 7- and 14-day intervals are probably too narrow (D-048).
  - Rankings describe one station; they are not a verdict on which model is best.
  - **Open:** equivalence margins, formal tests, the block-length/support decision, and whether to keep the two added sensitivity cohorts (`utj_rank_closure`, `utj_rank_methods`).
- **Metrics (step 9):** `etcompare.metrics.score_comparison` (D-044) scores daily pairs and each period product separately. Growing-season products were scored on 2026-10-16 (D-046); only partial sums exist, because the record ends before the season ends.
- **Intervals (step 10):** `etcompare.bootstrap.bootstrap_comparison` (D-047) gives station-level paired moving-block intervals.
  - Whole cohort days are resampled with shared indices. Blocks stay inside gap-free, water-year-bounded segments.
  - Metrics are recomputed with `compute_metrics` in every replicate. Period metrics resample whole periods.
  - Settings come from the `bootstrap:` section (7/14/28-day sensitivity lengths, `min_blocks` 10; all Provisional), and no block length is adopted.
  - The US-UTJ run (D-048) found strong, long residual autocorrelation (lag 14 up to 0.54). The 7- and 14-day intervals are probably too narrow, while 28-day blocks mostly lack support. How to handle this is an open user decision.
  - Intervals are not rankings; step 11 builds rankings on them.

Background from steps 1–8: Period totals (matched-day partial sums, complete-calendar totals, optional estimated totals) are built from cohorts by `etcompare.aggregation.aggregate_cohort` and declared in `period_aggregations:` (D-041). Estimated totals use paired `reference_et_fraction` imputation (gridMET ETr) kept in separate `*_estimated_mm` columns, and growing seasons come from the zone-E crop-height model `US-UTJ_20240101_20251230.csv` (D-042). Primary footprints are NLDAS-grass-ETo weighted (`US-UTJ_nldas_eto_20261014`), built by etcompare from a per-timestep fluxfootprints stack; a measured-LE-weighted set from the same stack is the sensitivity case. The existing set A/B `etw` files were shown to be NLDAS-ETo weighted (D-036). Comparison cohorts are declared in the config's `comparisons:` section and built by `etcompare.cohorts.build_cohort` (D-039, D-040). `IMPLEMENTATION_STATUS.md` holds the details. Station-level metrics, intervals, rankings and diagnostics exist for US-UTJ. Its network summaries cover one site only.
