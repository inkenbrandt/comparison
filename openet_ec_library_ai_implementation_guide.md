# AI implementation guide: OpenET–eddy covariance comparison library

## Purpose and scope

Build a reusable Python library, provisionally named `etcompare`, for evaluating downloaded OpenET model estimates against quality-controlled eddy covariance (EC) station ET. The name is a working name; check availability before distributing it.

The user already has daily station ET processed with `flux-data-qaqc`, footprint rasters generated with `fluxfootprints`, and daily OpenET GeoTIFFs. Build on those products. Do not recreate raw EC processing, energy-balance correction, footprint modeling, or satellite ET retrieval in the core package.

Deliver a working library, an offline demonstration, a configurable command-line workflow, Colab-compatible examples, scientific-method documentation, and reproducible analysis outputs. Real-data results require the user's actual inputs; never present synthetic examples as observed performance.

This guide specifies proposed APIs, not functions that already exist. The comparison design follows the papers discussed below; block resampling, strict comparison cohorts, and several engineering decisions are recommended extensions rather than exact reproductions of those studies.

## Instructions to the implementing AI

Work through the numbered steps in dependency order. At each step:

1. Read the existing implementation, repository instructions, and relevant method requirements.
2. Implement the smallest coherent change that satisfies the step.
3. Run the targeted acceptance checks. Use independently calculated expectations where scientific correctness is involved.
4. Update `IMPLEMENTATION_STATUS.md` with completed work, checks, decisions, and limitations.
5. Continue to the next step when its dependencies are satisfied. Ask only about unresolved scientific choices that cannot be represented safely as explicit configuration.

Do not invent upstream function signatures, data columns, geospatial metadata, uncertainty estimates, or bibliographic details. Inspect the actual installed versions and sample files. Keep core algorithms independent of upstream packages through adapters. Do not modify the two upstream repositories unless explicitly requested.

Record assumptions rather than silently choosing methods that improve agreement. Never tune filtering, closure choice, footprint weighting, or model weights on the same observations used for final performance claims.

## Step 1 — Inspect inputs and freeze the analysis contract

Inspect representative station tables, station metadata, footprint rasters, OpenET rasters, and any existing comparison tables. If unavailable, proceed using documented synthetic fixtures and mark real-data integration as unverified.

Write `docs/input_contract.md` and `docs/decisions.md`. Establish:

- Station identifiers, coordinates, timezone, and the definition of a measurement day, including local standard time versus civil time.
- Daily ET units and whether each station column is measured, corrected, gap-filled, or otherwise reconstructed.
- Energy-balance correction method and flags from the existing processing.
- Footprint values: probability density per square metre, cell-integrated contribution, or normalized weights; aggregation method; temporal coverage; original domain capture fraction, if available.
- OpenET product version, model versions where available, scale factor, offset, nodata, units, export method, and overpass-only versus interpolated daily support.
- Raster CRS, affine transform, grid alignment, and extent. Identify multi-station rasters.
- Existing extraction methods and polygon definitions.
- Available meteorology, land cover, NDVI/EVI, irrigation/cutting records, and soil-moisture data.

**Gate:** Missing mandatory semantics must produce an actionable validation error, not a guessed conversion. An unknown optional field remains unknown. A filename date does not establish the product's temporal support.

## Step 2 — Create the package skeleton

Use a `src` layout, `pyproject.toml`, typed public APIs, structured logging, and test fixtures generated locally. Select compatible dependency versions after checking their official documentation and the target environment; do not blindly pin the newest releases.

Suggested modules:

| Module | Responsibility |
|---|---|
| `config`, `schemas`, `validation` | Configuration, contracts, validation reports |
| `io.station`, `io.manifest`, `io.rasters` | Reading, indexing, and bounded raster access |
| `adapters` | Explicit mappings for upstream exported products |
| `spatial`, `footprints` | Extraction and source-area diagnostics |
| `cohorts`, `aggregation` | Comparable samples and calendar totals |
| `metrics`, `bootstrap`, `ranking` | Performance and uncertainty |
| `diagnostics`, `events`, `anomalies` | Conditional and process-oriented comparisons |
| `ensemble` | Optional held-out model weighting |
| `plots`, `reporting`, `provenance`, `cli` | Delivery and reproducibility |

Keep the tabular core usable without geospatial packages. Suggested dependencies are NumPy, pandas, SciPy, and a Parquet engine; geospatial extras may use rasterio, pyproj, shapely, and geopandas. Plotting and machine-learning extras should be optional. Avoid mandatory Earth Engine access or credentials.

**Gate:** Importing the base package performs no network access, loads no raster, and does not require optional dependencies. Installation and basic tests work in a clean environment.

## Step 3 — Define canonical tables and result objects

Use explicit schemas, units, unique keys, and metadata. Required input dates are station-day labels; timestamps must be converted using a declared day definition before joining.

| Table | Essential fields |
|---|---|
| Stations | `site_id`, longitude, latitude, timezone, day definition, land-cover class |
| Station daily | `site_id`, `date`, `et_raw_mm`, `et_corrected_mm`, quality flags, observed/gap-filled fractions when known |
| Model manifest | `asset_id`, path, model, product/version, date or period, member sites, temporal support, units, scaling, CRS |
| Footprint manifest | `asset_id`, path, site, date/period, weighting method, value semantics, domain capture, temporal coverage |
| Extractions | site, date/period, model, extraction method, footprint method, ET, retained contribution, support diagnostics, source IDs |
| Paired observations | comparison ID, cohort ID, site/date, model, reference variant, observed ET, modeled ET, flags |
| Metrics | comparison/cohort IDs, site/group, temporal scale, model, metric, value, units, sample counts, validity reason |
| Intervals | comparison ID, metric/contrast, estimate, bounds, confidence level, method, seed, block length, valid replicates |
| Exclusions | stage, input key, reason code, severity, affected comparison |

Include `n_sites`, `n_days`, `n_periods`, and period completeness as appropriate. Do not use one ambiguous `n` everywhere. Use stable method identifiers and carry them into exports.

**Gate:** Duplicated primary keys are detected before joins. Round trips through Parquet preserve keys, date meaning, nulls, units metadata, and identifiers.

## Step 4 — Build file discovery and upstream adapters

Discover files recursively using `pathlib`; accept an explicit manifest as the preferred reproducible input. Parse the filename

`US-UTD_US-UTG_US-UTM_US-UTW_ensemble_et_2021_01_01_9abcf.tif`

into four site associations, model `ensemble`, and date `2021-01-01`. Parse from the model/date suffix with a configurable model registry; do not split on every underscore indiscriminately. Represent one asset with multiple station associations so it can be opened once.

Permit explicit aliases such as `US_UTM` to `US-UTM`, recording the mapping. Do not rename source files automatically. Reject ambiguous filenames and invalid dates. Read station CSV/Parquet with user-defined column mappings; accept pre-extracted model tables to bypass raster processing entirely.

Inspect `https://github.com/inkenbrandt/footprints` and `https://github.com/inkenbrandt/flux-data-qaqc` at a recorded commit when implementing adapters. Support exported files first; make direct object adapters optional and version-tested.

**Gate:** Test multi-site files, single-site files, model aliases, malformed dates, duplicate assets, and two assets for the same site/model/day. Duplicates require an explicit selection or mosaicking policy; never average them silently.

## Step 5 — Implement spatial extraction with auditable weight accounting

For valid cells V, calculate `ET_fp = sum(w_i * ET_i) / sum(w_i)`. Convert footprint density to cell contribution using physical cell area before weighting. For normalized weights, preserve their documented normalization basis. A sum of one does not prove that the original modeled footprint was fully captured.

Implement four extraction modes: dynamic footprint, declared fixed pixel window, polygon area-weighted mean, and optional nearest tower pixel as a diagnostic. Document pixel-boundary treatment for each mode.

Prefer integrating footprint contribution onto the native OpenET pixel grid. Use conservative area-overlap transfer for cell-integrated contribution; ordinary interpolation of probability weights is not conservative. Handle same-CRS aligned grids first, then rotated or differing-CRS grids with an explicitly validated method. For unsupported cases, raise a clear error rather than silently applying an inappropriate resampler. Reprojection must account for area correctly. Do not imply that resampling a 30 m ET pixel onto a 10 m footprint creates new ET information.

Track independently:

- Original modeled domain capture fraction, if supplied.
- Contribution inside the available ET extent.
- Contribution retained after model nodata and quality masking.
- Contribution within the target field or land-cover class.
- Temporal coverage of the footprint product.

Name denominators explicitly. For example, valid contribution conditional on the raster domain is not full-footprint capture. Do not multiply unrelated fractions unless their conditioning is compatible.

Support model-specific valid support and an intersection across compared models. Record a support identifier. Pixel-level intersection comparisons require rasters; pre-extracted tables cannot reconstruct them.

Reject negative footprint contributions, zero total contribution, all-nodata ET, missing CRS, and unknown scaling. A configurable small floating-point tolerance may handle roundoff only; report any adjustment. Do not equate zero ET with nodata or clip valid negative station ET automatically.

**Acceptance fixtures:** Uniform ET extracts unchanged under any valid weights; ET values `[2, 6]` with weights `[0.75, 0.25]` yield `3`; masking the second cell yields `2` with retained contribution `0.75`; conserved regridding preserves total contribution within declared numerical tolerance. Test non-overlap, partial cells, shifted grids, density conversion, and differing model masks.

## Step 6 — Support footprint aggregation sensitivity

Consume existing time-mean, reference-ET-weighted, and measured-ET-weighted footprints. If per-timestep footprints and weights are available, optionally implement aggregation:

`W_i = sum_t(a_t * f_i,t) / sum_t(a_t)`

Define whether `a_t` includes interval duration and which timesteps are eligible. Record the handling of negative ET, night periods, gaps, and pre-normalized/truncated footprints. Averaging individually renormalized footprints and aggregating original contributions need not produce the same result.

Do not recreate alternative daily weighting methods from a single existing daily raster: missing information cannot be recovered. Return `not_available` with a reason.

Compare weighting methods on fixed dates and reference variants. Measured-ET weighting is a sensitivity experiment because it uses the target measurements in spatial extraction. In resampling, keep extraction and corresponding observation paired.

**Gate:** Equal temporal weights reproduce the intended duration-weighted mean. Changing all temporal weights by one common factor leaves the aggregate unchanged. Missing inputs produce a skipped analysis, not invented alternatives.

## Step 7 — Create explicit comparison cohorts

Implement a comparison specification and immutable cohort object. Suggested API: `build_cohort(observations, extractions, comparison_spec)` returning paired rows plus inclusion/exclusion decisions.

Support:

- All-model common-date cohorts for rankings.
- Model-specific available-date cohorts for coverage summaries.
- Paired-method cohorts for extraction comparisons.
- Paired-reference cohorts for corrected/raw comparisons.
- Explicit pairwise-model cohorts when the all-model intersection is too restrictive.

Keep a fixed cohort across every dimension claimed to be a controlled comparison. Distinguish pairwise rankings from all-model rankings; they may use different days. Apply quality thresholds before scoring, independent of observed model agreement.

Prevent many-to-many merges. Do not drop missing values separately inside each metric; use the cohort's common eligibility mask and identify any metric-specific mathematical undefinedness.

**Gate:** A model missing difficult days cannot gain an advantage in the common-date ranking fixture. Removing a date from one paired method removes it from both sides. Export cohort membership and rejection reasons.

## Step 8 — Implement temporal aggregation and completeness

Daily ET input is depth per declared day. Monthly and seasonal totals are sums of daily depths, not means. Aggregate daily footprint-extracted estimates first; treat monthly-raster extraction with monthly footprints as a distinct method.

Support daily, calendar-month, configured growing-season, calendar-year, and water-year periods. Growing seasons are site/year metadata or an explicit rule, not a universal April–October assumption.

Maintain three distinct products:

1. Matched-day partial sums, clearly labeled with days included.
2. Complete-calendar totals requiring all expected days to have accepted values, whether measured or explicitly accepted gap-filled values.
3. Optional estimated full-period totals using a separately documented gap-filling/scaling method, never presented as directly observed totals.

Do not scale partial sums by calendar length by default. Overpass-only files cannot support full-month totals without an explicit interpolation product. Document leap days, station-day boundaries, and cross-year seasons. Compare monthly products only when units and temporal support match.

**Gate:** In a 31-day month with 30 valid days at 2 mm/day, partial sum is 60 mm and the complete total is unavailable. A fully covered month is 62 mm. Missing both products on the same day does not make a complete month.

## Step 9 — Implement transparent performance metrics

Let `P` be modeled ET, `O` station ET, and `e = P - O`. Implement:

| Metric | Definition |
|---|---|
| MBE | `mean(e)` |
| MAE | `mean(abs(e))` |
| RMSE | `sqrt(mean(e**2))` |
| Bias-corrected RMSE | `sqrt(mean((e - mean(e))**2))` |
| Percent bias | `100 * sum(e) / sum(O)` |
| Normalized RMSE | `RMSE / mean(O)`, optionally multiplied by 100 and labeled |
| Pearson r and r squared | Correlation and its square; label explicitly |
| OLS slope/intercept | Regress `P` on `O`; descriptive, not an uncertainty-free calibration |
| Cumulative error | Running sum of paired daily `e`, with missing-period flags |

Optional NSE and KGE must state exact definitions. For original KGE, document correlation, standard-deviation ratio, and mean ratio; do not mix KGE variants. Report mathematical failures as null plus reason. Require configurable near-zero tolerances for percentage denominators. Do not use daily MAPE as a default ranking metric.

Bias-corrected RMSE removes in-sample mean error for diagnosis; it is not evidence that a bias-corrected predictive product has been validated. Pearson r squared differs from predictive coefficient of determination; do not label both identically.

**Acceptance fixture:** For `O=[1,2,3]` and `P=[2,3,4]`, MBE, MAE, and RMSE equal 1; bias-corrected RMSE equals 0; slope equals 1; intercept equals 1; Pearson r squared equals 1; percent bias equals 50. Also test constant series, empty cohorts, singleton samples, and near-zero observed totals.

## Step 10 — Implement paired block-bootstrap intervals

Add moving-block resampling over regular calendar sequences. Preserve missing-day structure; do not compress gaps into apparently consecutive observations. Build eligible blocks inside declared uninterrupted segments and site/season-year boundaries. Document the estimand and edge handling.

Resample whole paired rows, including every model and reference variant, using identical indices for all sides of a contrast. Recompute metrics in every replicate; for monthly metrics, resample at the specified monthly unit or rebuild monthly totals under a documented daily scheme. Do not mix the two interpretations.

Make block length configurable; inspect residual autocorrelation and compare several plausible lengths. Defaults such as 7, 14, and 28 daily samples are sensitivity candidates, not universal scientific standards. Report block length, seed, requested/valid replicate counts, interval method, number of independent segments, and small-sample limitations.

For network inference, distinguish a fixed monitored network from a population of sites. The latter requires a hierarchical approach resampling sites and temporal blocks. Preserve common calendar resampling across sites when shared weather dependence matters; independent site resampling alone does not resolve that dependence.

**Gate:** Identical predictions yield zero paired differences in every replicate. Repeated seed yields identical results. A temporally correlated synthetic error series demonstrates why iid-day resampling can be overconfident. Too few eligible blocks yields `insufficient_support`, not a precise interval.

## Step 11 — Add ranking and paired contrasts

Calculate station-level metrics before network summaries. Provide equal-site summaries as the primary network result, optionally observation-count and square-root-count weighting with explicit labels. Mean station RMSE is not pooled RMSE.

Implement `delta_mae = MAE_field - MAE_footprint` so positive means footprint improvement. For model contrasts use `metric_A - metric_B` and document whether lower or higher is better.

Report practical effect size and its interval; do not force a unique winner when evidence is inconclusive. Expose ranking sensitivity across station, year, temporal scale, reference variant, and extraction method. A resampled top-rank frequency is a bootstrap stability measure, not the probability that a model is scientifically true or universally best.

Allow a predeclared practical equivalence margin. An interval crossing zero is not itself evidence of equivalence. If many formal hypothesis tests are requested, expose a multiplicity policy and mark exploratory analyses.

**Gate:** Duplicating one station's record does not change equal-site summaries when its station metric is unchanged. A known small-error advantage has the documented contrast sign. No ranking silently pools unequal cohorts.

## Step 12 — Add closure, coverage, and environmental diagnostics

Use existing raw/corrected ET and metadata. Never recompute correction unless a later explicit extension requests it. Report correction magnitude and rank changes. Treat corrected/raw differences as reference sensitivity, not a calibrated uncertainty envelope containing truth.

Add error summaries versus season, ETo, VPD, wind speed/direction, daytime sensible heat, closure ratio, gap-filled fraction, footprint capture, within-field fraction, and satellite support. Use independently defined bins or declared training-data quantiles. Record bin edges and sample sizes.

Negative daytime sensible heat is an advection indicator, not proof of horizontal advection. Do not substitute a daily-mean H value for a daytime sign diagnostic without labeling the change. With corrected ET, regression on net radiation can contain mathematical coupling; offer uncorrected-reference diagnostics and explain this limitation.

Gap-filled EC and interpolated OpenET may share meteorological drivers. Compare observation-dominated days with the broader accepted set, preserving provenance rather than implying fully independent validation.

**Gate:** Unknown meteorology skips only the affected diagnostic. Sparse bins are flagged. Wind directions wrap correctly at 0/360 degrees.

## Step 13 — Evaluate landscape representativeness and satellite support

Compute footprint-weighted land-cover fractions and continuous vegetation-index mean/variability, plus differences from fixed windows and polygons. Match land-cover vintage and vegetation acquisition date with an explicit policy. Preserve temporal mismatch metadata.

Optionally call tested `fluxfootprints` representativeness APIs. Do not equate a HIGH/MEDIUM/LOW classification with a quantitative ET accuracy guarantee. Keep full-domain diagnostics separate from metrics restricted to an 80% contour.

Accept per-pixel or per-site clear-observation flags/counts from authoritative product metadata. A scene date can include clouds at the station; a non-null interpolated ET value is not a clear observation. Record each model's support if it differs.

Compare errors on clear-observation and interpolated days; optionally use nearest valid acquisition distance and enclosing acquisition-gap duration. Label retrospective diagnostics that use future acquisitions. Without acquisition metadata, report this analysis as unavailable.

**Gate:** A mixed-land-cover fixture returns analytically known weighted fractions. A raster with daily values but no acquisition metadata does not invent overpass flags.

## Step 14 — Add anomalies and management-event analysis

For multi-year data, estimate site seasonal climatologies from training years and calculate held-out anomalies. Fit the same declared climatology method separately to observed and modeled series; report raw bias alongside anomaly skill because demeaning removes part of the bias. Keep leap-day and sparse-season rules explicit.

In a single year, use a declared exploratory detrending method and do not claim independent seasonal generalization. Any fitted standardization or smoothing choices used for validation belong inside training folds.

Accept event tables containing site, event ID/type, start/end dates, optional treatment/control, and source. Implement user-defined pre/post windows, baseline-relative ET change, cumulative response, and recovery metrics where identifiable. Keep overlapping events and weather confounding visible.

Matched controls or before-after/control-impact contrasts are optional when the design supports them. Do not infer causal water savings from an uncontrolled before/after ET difference. Define events from management records rather than favorable model residuals.

**Gate:** A synthetic irrigation cutoff with a known ET decrease yields the expected change; incomplete baselines and overlapping events are flagged. Held-out anomalies use no test observations to fit climatologies.

## Step 15 — Add optional water-balance checks and scenario uncertainty

Use a declared boundary and sign convention. One supported soil-zone equation is:

`residual = precipitation + irrigation + capillary_rise - ET - runoff - deep_percolation - storage_change`

Every term must cover the same area and period. If a term is unknown, do not silently set it to zero. Allow explicit scenario assumptions and distinguish them from measurements. Regional diversion is not automatically field irrigation.

Report residuals as plausibility diagnostics. Do not assign the entire residual to ET error. Convert ET depths to volumes only for an appropriate known area; a changing tower footprint is not automatically a fixed field or basin area.

Optional Monte Carlo uncertainty needs justified distributions and dependencies. Keep sampling uncertainty, measurement uncertainty, footprint-parameter uncertainty, and method-choice sensitivity separate. The corrected/raw difference and model spread are not default probability distributions. Footprint uncertainty requires regenerating footprints or supplied alternative realizations, not arbitrary noise on normalized pixels.

**Gate:** A closed synthetic water balance yields zero residual. Unknown inflows remain unknown. Scenario uncertainty is labeled separately from bootstrap performance intervals.

## Step 16 — Implement held-out ensemble experiments

Compare the downloaded official ensemble, simple mean/median of available members under a declared eligibility rule, and an optional fitted weighted ensemble. Do not include the official ensemble as an additional independent member among its component models. Pixel-level ensemble construction and ensemble construction after spatial extraction are different operations; label both honestly.

Begin with nonnegative weights summing to one and regularization toward equal weights. Select any regularization using training data only. Provide leave-one-year-out evaluation for temporal transfer and leave-one-site-out evaluation for spatial transfer. Use nested grouped validation for hyperparameter selection; random daily splits are not acceptable for transfer claims. Add time buffers where preprocessing windows or temporal dependence would leak information across folds.

Save each fold's training membership, held-out predictions, weights, preprocessing, and member availability. Choose the missing-member policy in advance. Reweighting available members changes the fitted ensemble; report it explicitly. If there is too little independent data, return an exploratory result or decline fitting with a reason.

**Gate:** Held-out rows never influence weights, climatologies, feature scaling, or tuning. A synthetic model good only during training does not receive a falsely reported held-out improvement. Ensemble spread is labeled disagreement, not a confidence interval unless separately calibrated and validated.

## Step 17 — Build plots, reports, configuration, and CLI

Deliver scorecards, scatterplots with a 1:1 line, cumulative ET/error plots, footprint-benefit intervals, raw/corrected sensitivity plots, conditional-error plots, coverage plots, and ranking-stability tables. Every figure should identify units, reference variant, extraction method, scale, cohort, and sample size in its labels or caption. Do not connect cumulative curves across missing periods without a visible gap or annotation.

Generate Markdown or HTML reports plus CSV/Parquet tables and PNG/SVG figures. Reports must state which analyses were skipped and why. Treat a missing optional input as an explicit unavailable result, not as a successful empty plot.

Proposed CLI (implement and document these names before using them in examples):

```bash
etcompare validate config.yaml
etcompare index config.yaml
etcompare extract config.yaml
etcompare analyze config.yaml
etcompare report config.yaml
etcompare run config.yaml
```

Configuration must expose input paths, mappings, temporal support, units, reference variants, extraction methods, model roster, cohort policy, quality rules, period definitions, bootstrap settings, and output location. Any example thresholds are provisional configuration values, not established standards.

Persist a run manifest with configuration hash, input identities/checksums, package versions, git revision, upstream versions when known, raster grid identities, masks, random seed, exclusion counts, and analysis completion status.

**Gate:** Changing one relevant input, mask, grid, or configuration invalidates the associated cache. Reports generated from the synthetic run contain internally consistent sample counts and no claims about real stations.

## Step 18 — Make processing resilient and release a usable first version

Process bounded raster windows and one manageable time/site group at a time. Reuse an opened multi-station/model asset where useful; avoid loading all rasters into one dense array. Never share unsafe raster dataset handles between workers. Benchmark serial operation before adding concurrency.

Use `pathlib` paths compatible with Windows, Linux, and Colab. Support optional staging of remote-mounted files to local temporary storage, finite retries for transient I/O errors, atomic outputs, resumable extraction, and structured error logs. A corrupt raster may be skipped in non-strict mode, but must be counted and reflected in coverage. Systematic configuration errors should fail the run rather than silently skip everything.

Required release artifacts:

- Installable package and documented public API.
- Unit/integration tests for scientific invariants and failure cases above.
- Tiny offline synthetic dataset generated by a script with a fixed seed.
- Example configuration and end-to-end demo.
- Colab notebook using paths supplied by the user; Google Drive mount is an optional user-executed cell.
- A notebook for pre-extracted tables and a separate raster workflow example.
- Method guide, data dictionary, references, limitations, and changelog.
- A run on representative real files when provided, with an explicit verification summary.

Test the supported operating environments in CI where practical. Pin a reproducible demo environment without unnecessarily imposing narrow dependency pins on all library users. Do not publish to PyPI, create public data releases, or alter upstream repositories without authorization.

**Final gate:** A fresh user can install the package, run the offline demonstration, inspect paired data and exclusions, reproduce its metrics, and generate the report without credentials. Unsupported optional analyses are clearly marked. All implementation gaps are listed; no placeholder function is presented as complete.

## Suggested implementation sessions

| Session | Steps | Tangible outcome |
|---|---|---|
| 1 | 1–4 | Contracts, package skeleton, indexed inputs, adapters |
| 2 | 5–6 | Verified raster extraction and footprint sensitivity |
| 3 | 7–9 | Matched cohorts, aggregation, metric engine |
| 4 | 10–11 | Paired intervals and defensible rankings |
| 5 | 12–14 | Environmental, representativeness, and event diagnostics |
| 6 | 15–16 | Optional water balance and held-out ensembles |
| 7 | 17–18 | Reports, CLI, examples, resilient execution, release checks |

For a smaller first release, complete steps 1–11 and 17–18; expose steps 12–16 as clearly planned extensions. Do not implement an ensemble before the cohort and validation infrastructure is reliable.

## Reusable prompt for each coding session

> Read this guide, repository instructions, `IMPLEMENTATION_STATUS.md`, and relevant existing code. Implement the next incomplete step and its dependencies using the proposed APIs only after defining them. Preserve previous working behavior. Verify scientific calculations with the specified fixtures and targeted tests. Update documentation and the status file with completed requirements, checks, assumptions, and remaining limitations. Do not fabricate data or report unrun tests. Finish with the files changed, verified behavior, and the next incomplete step. Continue through subsequent steps if context and authorization permit.

## Scientific sources and how to use them

These sources informed the preceding comparison framework. Verify exact passages and bibliographic metadata when writing the package's formal method documentation. Distinguish published procedures from the additional methods proposed in this guide. Do not bundle the supplied PDFs into a public package without checking redistribution rights.

1. **Volk et al. (2024), “Assessing the accuracy of OpenET satellite-based evapotranspiration data to support water resource and land management applications.”** Nature Water. DOI: https://doi.org/10.1038/s44221-023-00181-7. Foundation for paired multi-scale evaluation, energy-balance-corrected references, and spatial sampling. Its reference-ET-weighted footprints differ from measured-ET weighting.
2. **Knipper et al. (2026), “Evaluating OpenET evapotranspiration estimates in California vineyards: Insights from the GRAPEX experiment.”** Agricultural and Forest Meteorology. DOI: https://doi.org/10.1016/j.agrformet.2026.111505. Supports temporal-scale, advection, closure-sensitivity, and management-response analyses.
3. **Reitz et al. (2025), “Performance Mapping and Weighting for the Evapotranspiration Models of the OpenET Ensemble.”** Water Resources Research. DOI: https://doi.org/10.1029/2024WR038899. Supports complementary metrics, environmental controls, and performance weighting evaluated using held-back sites. The simpler local ensemble proposed here is an extension, not a reproduction of its mapping workflow.
4. **Huntington et al. (2022), “Appendix G: Upper Colorado River Basin OpenET Intercomparison Summary.”** Report prepared for the U.S. Bureau of Reclamation. Supports reference-ET-weighted footprint sampling and showing closed/unclosed station ET. No DOI is supplied here.
5. **Sabiston et al., “Comparing field-scale eddy covariance measurements and crop coefficient estimates of alfalfa evapotranspiration to OpenET model estimates and exploring water budget implications in a dryland environment.”** Irrigation Science, online 2024, volume 43 (2025). DOI: https://doi.org/10.1007/s00271-024-00941-7. Supports extraction-area sensitivity; do not carry over unpaired distribution tests as the main test for paired daily accuracy.
6. **Djaman et al. (2023), “Accuracy of Estimated Crop Evapotranspiration Using Locally Developed Crop Coefficients against Satellite-Derived Crop Evapotranspiration in a Semiarid Climate.”** Agronomy. DOI: https://doi.org/10.3390/agronomy13071937. Useful for growth-stage and seasonal comparisons; reference is crop-coefficient-based ET.
7. **Tawalbeh et al. (2024), “Assessing Satellite-Derived OpenET Platform Evapotranspiration of Mature Pecan Orchard in the Mesilla Valley, New Mexico.”** Remote Sensing. DOI: https://doi.org/10.3390/rs16081429. Reference ET for 2017–2021 is reconstructed using coefficients derived from older measurements, not contemporary direct latent-heat EC throughout that period.
8. **Montazar et al. (2026), “Field-scale evaluation of OpenET for quantifying consumptive water savings under deficit irrigation in alfalfa.”** Agricultural Water Management. DOI: https://doi.org/10.1016/j.agwat.2026.110337. Useful for event and cumulative-response comparisons; its irrigated reference uses residual energy balance, while deficit comparisons use soil-water depletion.
9. **Osorio-Diaz et al. (2026), “Agricultural water balance and variability in Utah irrigation canal companies with OpenET products and spatial data.”** Irrigation Science. DOI: https://doi.org/10.1007/s00271-026-01172-8. Supports regional water-balance plausibility checks, with unresolved exchanges retained in the residual.

Upstream software: https://github.com/inkenbrandt/footprints and https://github.com/inkenbrandt/flux-data-qaqc. Record the actual versions used rather than assuming the current README describes the user's installed version.
