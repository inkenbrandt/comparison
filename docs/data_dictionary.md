# Data dictionary

Every table etcompare writes, generated from `etcompare.schemas` (`python tools/make_data_dictionary.py`). Parquet files carry the schema name and version, the column units and the date semantics in their `etcompare` metadata. Dates are station-day labels (Arrow `date32`), never instants (D-019).

Column kinds: `string`, `float` (float64), `int` (nullable Int64), `bool` (nullable boolean), `date` (station-day label), `timestamp`. *Key* columns form the primary key and are never null.

| Table | Version | Description |
|---|---|---|
| [`stations`](#stations) | 1 | One row per station with its measurement-day definition. |
| [`station_daily`](#station_daily) | 1 | Daily station ET with reference provenance. Source columns pass through with prefix 'src_'. |
| [`model_manifest`](#model_manifest) | 1 | One row per OpenET raster asset; multi-site assets list every member site. |
| [`asset_sites`](#asset_sites) | 1 | Association of each raster asset with each member site. |
| [`footprint_manifest`](#footprint_manifest) | 1 | One row per footprint raster within a declared footprint set. |
| [`extractions`](#extractions) | 1 | Spatially extracted model ET, one row per site/date/model/method. |
| [`paired`](#paired) | 2 | Observation-model pairs belonging to one cohort of one comparison. |
| [`cohort_membership`](#cohort_membership) | 1 | Every candidate site/date/model of a cohort with its inclusion decision and reason. |
| [`period_totals`](#period_totals) | 1 | Period sums of a cohort's daily depths: matched-day partial sums, complete-calendar totals and optional estimated totals, with calendar completeness. |
| [`daily_estimates`](#daily_estimates) | 1 | A cohort's daily pairs on every calendar day of the aggregated periods. Original values are kept; imputed values appear only in the *_estimated_mm columns. |
| [`crop_height`](#crop_height) | 1 | Daily crop height and the crop model's growing season. |
| [`metrics`](#metrics) | 2 | Performance metrics of one cohort on its common mask, with explicit sample counts and validity. Daily pairs and each period product are separate rows, never pooled. |
| [`cumulative_error`](#cumulative_error) | 1 | Running sums of a cohort's paired daily values and errors (e = modeled - observed) over cohort days only; days outside the cohort are never filled, and the first day after a run of missing days is flagged. |
| [`intervals`](#intervals) | 2 | Paired block-bootstrap intervals of station-level metrics and paired contrasts of one cohort. One row per target, metric, block length and interval method; null bounds carry a status. Intervals are sampling intervals for one station, not rankings. |
| [`residual_autocorrelation`](#residual_autocorrelation) | 1 | Autocorrelation of daily errors e = modeled - observed of one cohort, at exact calendar lags (pairs of cohort days k days apart; missing days are never compressed). A diagnostic for choosing bootstrap block lengths, not a selection rule. |
| [`model_rankings`](#model_rankings) | 1 | Station-level ranks of the models of one frozen ranking cohort, per context, metric and bootstrap block length, with rank stability from shared-index replicates. Ranks are descriptive; top_rank_frequency is a bootstrap stability measure, not the probability that a model is best. |
| [`rank_distribution`](#rank_distribution) | 1 | Per-model distribution of rank positions over the valid bootstrap replicates of a model_rankings row with status ok. Tied models share their positions equally, so each model's frequencies and each position's frequencies sum to 1. |
| [`model_contrasts`](#model_contrasts) | 1 | Paired model contrasts metric(model_a) - metric(model_b) inside one frozen ranking context, computed replicate by replicate on shared bootstrap indices, with practical equivalence and (optional) formal tests. model_b is the model declared earlier in the comparison. |
| [`network_summaries`](#network_summaries) | 1 | Summaries of station-level metrics across the sites of one frozen cohort. The equal-site mean is the primary network result; observation weightings are labelled alternatives, and pooled RMSE (over all site observations) is not the mean station RMSE. With n_sites = 1 a row is a single station, not network evidence. No interval: site-population inference needs hierarchical resampling (not implemented). |
| [`ranking_sensitivity`](#ranking_sensitivity) | 1 | How point ranks change between two ranking contexts that differ in one dimension (station, year, temporal scale, reference variant, extraction method) or only in their frozen cohort. Each context is an internally frozen cohort; same_cohort says whether both use the same cohort (same days). |
| [`closure_magnitude`](#closure_magnitude) | 1 | Size of the existing energy-balance correction on corrected_from_raw days only (never recomputed): ET_corr - ET, ET_corr / ET and the closure factor, plus declared closure metadata, by period. Gap-filled days are excluded and counted. A correction magnitude is reference sensitivity, not a calibrated uncertainty envelope containing the truth. |
| [`reference_provenance_periods`](#reference_provenance_periods) | 1 | Reference provenance of a cohort's days per period: observation-dominated (corrected_from_raw) and gap-filled (corrected_filled) days and the gap-filled fraction, a conditioning variable. Gap-filled reference ET shares gridMET drivers with the OpenET interpolation (D-005). |
| [`conditional_errors`](#conditional_errors) | 1 | Metrics of paired errors e = P - O per model inside bins of a declared conditioning variable, within one frozen cohort. Bins come from the configuration (fixed edges, training quantiles of the variable, calendar, sectors or reference provenance), never from the errors. Sparse bins carry a status, not a value. Conditional summaries describe associations, not causes. |
| [`error_regressions`](#error_regressions) | 1 | Ordinary least squares of daily error e = P - O on a declared predictor (net radiation) per model and reference variant. With an energy-balance-corrected reference the error and the predictor are mathematically coupled through the correction; the uncorrected reference version is reported alongside where the cohort has it. Descriptive only. |
| [`diagnostic_availability`](#diagnostic_availability) | 1 | Which diagnostics ran and which were skipped, with reasons. An undeclared or unverified variable, a missing column, an all-null column or absent metadata skips only the diagnostic that needs it. |
| [`reference_rank_changes`](#reference_rank_changes) | 1 | Point-rank changes between reference variants, taken from ranking_sensitivity (never re-ranked), with the effect each pair isolates. raw vs corrected-measured on one cohort is closure sensitivity; corrected-measured vs corrected-inclusive mixes gap-filled days and different cohort days (D-004). Reference sensitivity, not an uncertainty bound. |
| [`landscape_class_fractions`](#landscape_class_fractions) | 1 | Weight of a support (footprint, pixel window, nearest pixel, polygon) on each class of a categorical map, moved onto the map's native grid by exact area overlap (classes are never resampled). class_frac_of_valid is conditional on valid cells and sums to 1 over the class rows; nodata weight is a separate row. Landscape description only: not an ET accuracy guarantee. |
| [`landscape_index_summaries`](#landscape_index_summaries) | 1 | Support-weighted mean and variability of a continuous vegetation index on its native grid. weighted_std = sqrt(sum c_j (v_j - m)^2 / sum c_j) over valid cells (population form, no small-sample correction); n_eff = (sum c_j)^2 / sum c_j^2 (Kish effective number of cells). Landscape description only: not an ET accuracy guarantee. |
| [`landscape_method_differences`](#landscape_method_differences) | 1 | Footprint minus fixed-support landscape quantities on frozen footprint days: the same day, layer asset and matching policy on both sides. A difference describes what the supports see; it is not an ET error. ET_mean (source_region_mean) is a varying source region (D-007) and never appears as a fixed support. |
| [`representativeness_labels`](#representativeness_labels) | 1 | Qualitative labels kept next to the quantitative fractions: the station's metadata land cover (a site label, not a footprint-weighted fraction) and optional fluxfootprints HIGH/MEDIUM/LOW classes. A label never enters a metric or threshold and is not a quantitative ET accuracy guarantee. |
| [`satellite_support_errors`](#satellite_support_errors) | 1 | Metrics (compute_metrics) of e = P - O on clear-observation and interpolated days of one frozen cohort, identical across models, plus declared acquisition-timing bins. Support comes only from authoritative product metadata; unknown support is never counted as either subset. |
| [`representativeness_availability`](#representativeness_availability) | 1 | Which landscape and satellite-support analyses ran, per day where relevant, and which were not available, with reasons. A day without an admissible layer asset is not_available, never filled. |
| [`station_events`](#station_events) | 1 | Events of a declared station event log, one row per event inside the station record. Events come only from the log, never from model residuals or agreement. Source columns pass through with prefix 'src_'. |
| [`event_responses`](#event_responses) | 1 | Before/after response of each cohort series to each management event: baseline, change, cumulative response and recovery for the observed and modeled series and their difference. An uncontrolled before/after difference is not causal evidence. |
| [`event_overlaps`](#event_overlaps) | 1 | Every other logged event (management, instrument or confounder) inside a management event's windows. Kept visible; instrument and confounder events are never treatments. |
| [`event_conditions`](#event_conditions) | 1 | Declared weather variables summarised over each event's windows on the station record, so weather confounding stays visible next to the responses. |
| [`anomaly_daily`](#anomaly_daily) | 1 | Daily anomalies of a cohort: each value minus its baseline (held-out climatology or exploratory moving mean), fitted separately to the observed and modeled series. |
| [`anomaly_metrics`](#anomaly_metrics) | 1 | Metrics of raw values and of anomalies on the same days, per fold and pooled. Raw bias is reported next to anomaly skill because demeaning removes part of the bias. |
| [`water_balance_terms`](#water_balance_terms) | 1 | Period totals of each declared water-balance term. A total exists only when every calendar day (or both storage states) of the period has a value; otherwise the partial sum is reported with its day count and is never a full-period total. |
| [`water_balance_residuals`](#water_balance_residuals) | 1 | Water-balance residuals per period: a plausibility diagnostic holding every unmeasured exchange and the error of every term; never ET error and never a basis for choosing a model, reference, filter or footprint. Residuals from measured/modeled terms and scenario residuals are separate rows. |
| [`scenario_results`](#scenario_results) | 1 | Declared scenario uncertainty of a water-balance residual: one uncertainty category per scenario (measurement, footprint_parameter or method_choice), never merged and never a bootstrap interval (sampling uncertainty is the step-10 intervals table). |
| [`ensemble_days`](#ensemble_days) | 1 | Member availability, eligibility under the declared missing-member policy, and member disagreement for every candidate day of a post-extraction ensemble experiment. Member spread is disagreement between models, never a confidence interval. |
| [`ensemble_predictions`](#ensemble_predictions) | 1 | Daily values of each compared product. 'baseline' rows (official, mean, median) have no fitted parameters; fitted rows are held-out predictions from weights fitted on other folds only. |
| [`ensemble_folds`](#ensemble_folds) | 1 | One row per held-out fold: training membership counts, buffer, member availability, preprocessing and the regularisation chosen by inner grouped validation on training folds only, or why the fold was declined. |
| [`ensemble_weights`](#ensemble_weights) | 1 | Member weights of each fitted fold (nonnegative, summing to one), before and after shrinkage toward equal weights; exploratory in-sample weights are labelled so and have no selected weight. |
| [`ensemble_inner_scores`](#ensemble_inner_scores) | 1 | Inner grouped leave-one-fold-out validation of each shrinkage candidate, on training folds only (held-out rows never enter). |
| [`ensemble_training_membership`](#ensemble_training_membership) | 1 | Role of every candidate site-day in every fold: training, held out, or excluded with the reason. |
| [`ensemble_summary`](#ensemble_summary) | 1 | Status of each ensemble scope: the no-fit baseline comparison and each fold design (held_out, declined, exploratory or not_available, with the reason). Every ensemble here is built after spatial extraction, not at pixel level; one station is not network evidence. |
| [`ensemble_improvement`](#ensemble_improvement) | 1 | Held-out contrasts of the fitted ensemble against each unfitted product, taken from the step-11 contrasts on the frozen cohort. An improvement is 'supported' only when the interval excludes zero in the fitted ensemble's favour on held-out days. |
| [`analysis_status`](#analysis_status) | 1 | One row per workflow unit: what ran, what was reused, skipped or unavailable, and why. |
| [`skipped_analyses`](#skipped_analyses) | 1 | Every analysis, or part of one, that produced no result, with its reason. |
| [`exclusions`](#exclusions) | 1 | Rows removed or flagged, with stage and reason. |

## stations

Version 1. One row per station with its measurement-day definition.

Primary key: `site_id`.

| Column | Kind | Unit | Null | Description |
|---|---|---|---|---|
| `site_id` | string |  | key | Station identifier, preserved exactly |
| `name` | string |  | yes | Station name |
| `latitude` | float | degree_north | yes | Latitude |
| `longitude` | float | degree_east | yes | Longitude |
| `elevation_m` | float | m | yes | Elevation |
| `land_cover` | string |  | yes | Land-cover class (e.g. IGBP code) |
| `timezone` | string |  | no | Fixed offset or IANA zone of the station day |
| `day_definition` | string |  | no | How the station day is bounded; one of `local_standard_time`, `civil_time` |
| `interval_label` | string |  | no | Whether subdaily stamps label interval start or end; one of `start`, `end` |

## station_daily

Version 1. Daily station ET with reference provenance. Source columns pass through with prefix 'src_'.

Primary key: `site_id`, `date`.

Extra columns are allowed and kept.

| Column | Kind | Unit | Null | Description |
|---|---|---|---|---|
| `site_id` | string |  | key | Station identifier |
| `date` | date |  | key | Station-day label |
| `et_raw_mm` | float | mm d-1 | yes | Uncorrected daily station ET |
| `et_corrected_mm` | float | mm d-1 | yes | Energy-balance-corrected ET; includes gap-filled values where gap_filled is True |
| `gap_filled` | bool |  | yes | Source gap flag for the corrected series |
| `closure_factor` | float | 1 | yes | Energy-balance closure correction factor (optional column) |
| `raw_available` | bool |  | no | et_raw_mm is non-null |
| `reference_provenance` | string |  | no | Derived reference category (contract 4.3); one of `corrected_from_raw`, `corrected_filled`, `reference_missing`, `inconsistent_flags` |
| `subdaily_valid_fraction` | float | 1 | yes | Fraction of subdaily records observed (optional column) |
| `reference_et_mm` | float | mm d-1 | yes | Daily reference ET (declared column, e.g. gridMET ETr); used only to impute (optional column) |

## model_manifest

Version 1. One row per OpenET raster asset; multi-site assets list every member site.

Primary key: `asset_id`.

| Column | Kind | Unit | Null | Description |
|---|---|---|---|---|
| `asset_id` | string |  | key | Filename stem; unique |
| `path` | string |  | no | Local path when indexed |
| `model` | string |  | no | Canonical model name |
| `product_version` | string |  | no | Declared product/model version or 'unknown' |
| `variable` | string |  | no | Variable token from the filename |
| `date` | date |  | no | Period start label |
| `period` | string |  | no | Temporal period of the asset; one of `day`, `month` |
| `member_sites` | string |  | no | Sorted ';'-joined site IDs from the filename |
| `n_sites` | int |  | no | Number of member sites |
| `site_aliases_applied` | string |  | yes | ';'-joined alias=site mappings used |
| `temporal_support` | string |  | yes | Declared temporal support; one of `interpolated_daily`, `overpass_only`, `unknown` |
| `day_definition` | string |  | yes | Declared product day definition; one of `local_standard_time`, `civil_time`, `utc`, `unknown` |
| `units` | string |  | yes | Declared ET units |
| `scale_factor` | float |  | yes | Raster scale factor |
| `add_offset` | float |  | yes | Raster offset |
| `nodata` | float |  | yes | Raster nodata value |
| `scaling_source` | string |  | no | Origin of scale/offset/nodata; one of `declared`, `from_header`, `not_read` |
| `crs` | string |  | yes | Raster CRS (null until the raster header is read) |

## asset_sites

Version 1. Association of each raster asset with each member site.

Primary key: `asset_id`, `site_id`.

| Column | Kind | Unit | Null | Description |
|---|---|---|---|---|
| `asset_id` | string |  | key | Asset identifier |
| `site_id` | string |  | key | Member site |
| `model` | string |  | no | Canonical model name |
| `date` | date |  | no | Period start label |

## footprint_manifest

Version 1. One row per footprint raster within a declared footprint set.

Primary key: `footprint_set_id`, `asset_id`.

| Column | Kind | Unit | Null | Description |
|---|---|---|---|---|
| `footprint_set_id` | string |  | key | Declared footprint set |
| `asset_id` | string |  | key | Filename stem; unique within the set |
| `path` | string |  | no | Local path |
| `site_id` | string |  | no | Station |
| `date` | date |  | no | Period start label |
| `period` | string |  | no | Temporal period; one of `day`, `month` |
| `layer_token` | string |  | no | Filename layer token (e.g. 'etw'); not a method claim |
| `weighting_method` | string |  | no | Declared temporal weighting method |
| `value_semantics` | string |  | no | Declared meaning of cell values; one of `normalized_cell_weight`, `cell_contribution`, `density_per_m2` |
| `domain_capture_frac` | float | 1 | yes | Original modeled-domain capture, if supplied |
| `domain_capture_source` | string |  | yes | Where domain_capture_frac came from (optional column) |
| `temporal_coverage_frac` | float | 1 | yes | Fraction of the period's timesteps used |

## extractions

Version 1. Spatially extracted model ET, one row per site/date/model/method.

Primary key: `site_id`, `date`, `period`, `model`, `extraction_method`, `footprint_set_id`.

| Column | Kind | Unit | Null | Description |
|---|---|---|---|---|
| `site_id` | string |  | key | Station |
| `date` | date |  | key | Station-day label (period start) |
| `period` | string |  | key | Temporal period; one of `day`, `month` |
| `model` | string |  | key | Canonical model name |
| `extraction_method` | string |  | key | Stable extraction-method identifier |
| `footprint_set_id` | string |  | key | Footprint set used (or 'none') |
| `footprint_method` | string |  | no | Declared footprint weighting method |
| `et_mm` | float | mm d-1 | yes | Extracted model ET |
| `fp_weight_sum` | float | 1 | yes | Sum of footprint weights over finite cells |
| `fp_weight_retained_frac` | float | 1 | yes | Retained footprint weight |
| `fp_weight_retained_denominator` | string |  | yes | Denominator of the retained fraction |
| `support_id` | string |  | yes | Identifier of the valid-cell support, when known |
| `fp_frac_in_model_extent` | float | 1 | yes | Footprint contribution inside the model raster extent / footprint grid total (optional column) |
| `fp_domain_capture_frac` | float | 1 | yes | Original modeled-domain capture of the footprint (separate denominator; never multiplied with the retained fractions) (optional column) |
| `n_cells_valid` | int |  | yes | Model cells contributing to et_mm (optional column) |
| `model_grid_id` | string |  | yes | Identity of the model raster grid (optional column) |
| `day_alignment` | string |  | no | Whether day definitions are declared on both sides; one of `declared`, `unverified` |
| `model_asset_id` | string |  | yes | Model raster asset |
| `footprint_asset_id` | string |  | yes | Footprint raster asset |
| `source_path` | string |  | yes | Original source path (provenance only) |

## paired

Version 2. Observation-model pairs belonging to one cohort of one comparison.

Primary key: `comparison_id`, `cohort_id`, `site_id`, `date`, `model`, `reference_variant`, `extraction_method`, `footprint_set_id`.

| Column | Kind | Unit | Null | Description |
|---|---|---|---|---|
| `comparison_id` | string |  | key | Comparison identifier |
| `cohort_id` | string |  | key | Cohort identifier |
| `site_id` | string |  | key | Station |
| `date` | date |  | key | Station-day label |
| `model` | string |  | key | Canonical model name |
| `reference_variant` | string |  | key | Reference variant; one of `et_raw`, `et_corrected_measured`, `et_corrected_inclusive` |
| `extraction_method` | string |  | key | Extraction method |
| `footprint_set_id` | string |  | key | Footprint set of the extraction (or 'none') |
| `observed_mm` | float | mm d-1 | yes | Station reference ET |
| `modeled_mm` | float | mm d-1 | yes | Model ET |
| `reference_provenance` | string |  | no | Reference provenance; one of `corrected_from_raw`, `corrected_filled`, `reference_missing`, `inconsistent_flags` |
| `flags` | string |  | yes | ';'-joined row flags |

## cohort_membership

Version 1. Every candidate site/date/model of a cohort with its inclusion decision and reason.

Primary key: `comparison_id`, `cohort_id`, `site_id`, `date`, `model`.

| Column | Kind | Unit | Null | Description |
|---|---|---|---|---|
| `comparison_id` | string |  | key | Comparison identifier |
| `cohort_id` | string |  | key | Cohort identifier |
| `site_id` | string |  | key | Station |
| `date` | date |  | key | Station-day label |
| `model` | string |  | key | Canonical model name |
| `included` | bool |  | no | Unit belongs to the cohort |
| `reason_code` | string |  | yes | Stable reason for exclusion; null when included |
| `detail` | string |  | yes | Every failing side/reference, or the models that failed |

## period_totals

Version 1. Period sums of a cohort's daily depths: matched-day partial sums, complete-calendar totals and optional estimated totals, with calendar completeness.

Primary key: `comparison_id`, `cohort_id`, `site_id`, `model`, `reference_variant`, `extraction_method`, `footprint_set_id`, `period_type`, `period_label`, `product`.

| Column | Kind | Unit | Null | Description |
|---|---|---|---|---|
| `comparison_id` | string |  | key | Comparison identifier |
| `cohort_id` | string |  | key | Cohort identifier |
| `site_id` | string |  | key | Station |
| `model` | string |  | key | Canonical model name |
| `reference_variant` | string |  | key | Reference variant; one of `et_raw`, `et_corrected_measured`, `et_corrected_inclusive` |
| `extraction_method` | string |  | key | Extraction method (daily values summed) |
| `footprint_set_id` | string |  | key | Footprint set of the extraction (or 'none') |
| `period_type` | string |  | key | Kind of period; one of `day`, `month`, `growing_season`, `year`, `water_year` |
| `period_label` | string |  | key | e.g. 2025-06, 2025, WY2025, GS2025 |
| `product` | string |  | key | Which total this row is; one of `partial_sum`, `complete_total`, `estimated_total` |
| `period_start` | date |  | no | First station day of the period |
| `period_end` | date |  | no | Last station day of the period (inclusive) |
| `observed_total_mm` | float | mm | yes | Station reference ET summed over the product's days; null when the product is unavailable |
| `modeled_total_mm` | float | mm | yes | Model ET summed over the same days |
| `n_days_expected` | int |  | no | Calendar station days in the period |
| `n_days_matched` | int |  | no | Cohort days in the period (both sides accepted) |
| `n_days_matched_reference_filled` | int |  | no | Matched days whose reference is gap-filled (corrected_filled) |
| `n_days_model_available` | int |  | no | Days with a non-null model value on this side, before cohort rules |
| `n_days_reference_measured` | int |  | no | Days with a reference value of this variant that is not gap-filled |
| `n_days_reference_filled` | int |  | no | Days with a gap-filled reference value of this variant |
| `period_completeness` | float | 1 | no | n_days_matched / n_days_expected |
| `temporal_support` | string |  | no | Declared temporal support of the model product; one of `interpolated_daily`, `overpass_only`, `unknown` |
| `estimation_method` | string |  | yes | Estimator of an estimated_total row |
| `n_days_imputed` | int |  | yes | Imputed days in an estimated_total row (null otherwise, and for mean_daily_scaling) (optional column) |
| `validity_reason` | string |  | yes | Why the totals are null, if they are |
| `flags` | string |  | yes | ';'-joined flags (daily flags of the period plus period flags) |

## daily_estimates

Version 1. A cohort's daily pairs on every calendar day of the aggregated periods. Original values are kept; imputed values appear only in the *_estimated_mm columns.

Primary key: `comparison_id`, `cohort_id`, `site_id`, `date`, `model`, `reference_variant`, `extraction_method`, `footprint_set_id`.

| Column | Kind | Unit | Null | Description |
|---|---|---|---|---|
| `comparison_id` | string |  | key | Comparison identifier |
| `cohort_id` | string |  | key | Cohort identifier |
| `site_id` | string |  | key | Station |
| `date` | date |  | key | Station-day label |
| `model` | string |  | key | Canonical model name |
| `reference_variant` | string |  | key | Reference variant; one of `et_raw`, `et_corrected_measured`, `et_corrected_inclusive` |
| `extraction_method` | string |  | key | Extraction method |
| `footprint_set_id` | string |  | key | Footprint set of the extraction (or 'none') |
| `observed_mm` | float | mm d-1 | yes | Cohort reference ET; null on days outside the cohort |
| `modeled_mm` | float | mm d-1 | yes | Cohort model ET; null on days outside the cohort |
| `observed_estimated_mm` | float | mm d-1 | yes | observed_mm, or the imputed value |
| `modeled_estimated_mm` | float | mm d-1 | yes | modeled_mm, or the imputed value |
| `value_source` | string |  | no | cohort / imputed / not_imputed (both sides alike); one of `cohort`, `imputed`, `not_imputed` |
| `gap_length_days` | int |  | yes | Length of the run of non-cohort days containing this day (null on cohort days) |
| `reference_et_mm` | float | mm d-1 | yes | Reference ET used by reference_et_fraction |
| `estimation_method` | string |  | no | Imputation method |

## crop_height

Version 1. Daily crop height and the crop model's growing season.

Primary key: `site_id`, `date`.

Extra columns are allowed and kept.

| Column | Kind | Unit | Null | Description |
|---|---|---|---|---|
| `site_id` | string |  | key | Station |
| `date` | date |  | key | Station-day label |
| `height_cm` | float | cm | yes | Crop height |
| `season_start` | date |  | no | First day of this year's growing season |
| `season_end` | date |  | no | Last day of this year's growing season |
| `in_season` | bool |  | no | season_start <= date <= season_end |

## metrics

Version 2. Performance metrics of one cohort on its common mask, with explicit sample counts and validity. Daily pairs and each period product are separate rows, never pooled.

Primary key: `comparison_id`, `cohort_id`, `metric_set_id`, `scope`, `temporal_scale`, `period_type`, `aggregation_id`, `product`, `model`, `reference_variant`, `extraction_method`, `footprint_set_id`, `metric`.

| Column | Kind | Unit | Null | Description |
|---|---|---|---|---|
| `comparison_id` | string |  | key | Comparison identifier |
| `cohort_id` | string |  | key | Cohort identifier |
| `metric_set_id` | string |  | key | Declared metric set (tolerances, metric list) |
| `scope` | string |  | key | Site ID or group label the metric summarizes |
| `scope_type` | string |  | no | 'site' or 'group'; one of `site`, `group` |
| `temporal_scale` | string |  | key | daily pairs or period totals; one of `daily`, `period` |
| `period_type` | string |  | key | Period type of period totals; 'none' for daily; one of `none`, `day`, `month`, `growing_season`, `year`, `water_year` |
| `aggregation_id` | string |  | key | Period aggregation scored; 'none' for daily |
| `product` | string |  | key | daily_pairs, or the period product scored; one of `daily_pairs`, `partial_sum`, `complete_total`, `estimated_total` |
| `model` | string |  | key | Canonical model name |
| `reference_variant` | string |  | key | Reference variant; one of `et_raw`, `et_corrected_measured`, `et_corrected_inclusive` |
| `extraction_method` | string |  | key | Extraction method |
| `footprint_set_id` | string |  | key | Footprint set of the extraction (or 'none') |
| `metric` | string |  | key | Stable metric identifier (etcompare.metrics) |
| `value` | float |  | yes | Metric value; null when undefined |
| `units` | string |  | no | Units of value |
| `valid` | bool |  | no | value is defined |
| `validity_reason` | string |  | yes | Why value is null, if it is |
| `metric_role` | string |  | no | score / diagnostic / descriptive / component; one of `score`, `diagnostic`, `descriptive`, `component` |
| `ranking_scope` | string |  | no | Rankings the cohort supports (all_models / pairwise / none); one of `all_models`, `pairwise`, `none` |
| `n_sites` | int |  | no | Sites contributing |
| `n_days` | int |  | no | Days contributing (matched days for period totals) |
| `n_days_reference_filled` | int |  | no | Contributing days with gap-filled reference |
| `n_days_imputed` | int |  | yes | Imputed days inside scored estimated totals |
| `n_periods` | int |  | yes | Periods contributing (period totals only) |
| `period_completeness` | float | 1 | yes | Matched / expected days over the scored periods |
| `temporal_support` | string |  | yes | Declared temporal support of the model product; one of `interpolated_daily`, `overpass_only`, `unknown` |
| `flags` | string |  | yes | ';'-joined flags of the scored rows |

## cumulative_error

Version 1. Running sums of a cohort's paired daily values and errors (e = modeled - observed) over cohort days only; days outside the cohort are never filled, and the first day after a run of missing days is flagged.

Primary key: `comparison_id`, `cohort_id`, `site_id`, `model`, `reference_variant`, `extraction_method`, `footprint_set_id`, `date`.

| Column | Kind | Unit | Null | Description |
|---|---|---|---|---|
| `comparison_id` | string |  | key | Comparison identifier |
| `cohort_id` | string |  | key | Cohort identifier |
| `site_id` | string |  | key | Station |
| `model` | string |  | key | Canonical model name |
| `reference_variant` | string |  | key | Reference variant; one of `et_raw`, `et_corrected_measured`, `et_corrected_inclusive` |
| `extraction_method` | string |  | key | Extraction method |
| `footprint_set_id` | string |  | key | Footprint set of the extraction (or 'none') |
| `date` | date |  | key | Station-day label |
| `observed_mm` | float | mm d-1 | no | Station reference ET |
| `modeled_mm` | float | mm d-1 | no | Model ET |
| `error_mm` | float | mm d-1 | no | modeled_mm - observed_mm |
| `cumulative_observed_mm` | float | mm | no | Sum of observed_mm over cohort days so far |
| `cumulative_modeled_mm` | float | mm | no | Sum of modeled_mm over cohort days so far |
| `cumulative_error_mm` | float | mm | no | Sum of error_mm over cohort days so far |
| `n_days_cumulated` | int |  | no | Cohort days summed so far |
| `missing_days_before` | int |  | yes | Calendar days missing since the previous cohort day (0 when consecutive; null on the first day) |
| `segment` | int |  | no | Uninterrupted run number, starting at 1; increments after every missing period |
| `flags` | string |  | yes | ';'-joined flags; missing_period_before marks a gap |

## intervals

Version 2. Paired block-bootstrap intervals of station-level metrics and paired contrasts of one cohort. One row per target, metric, block length and interval method; null bounds carry a status. Intervals are sampling intervals for one station, not rankings.

Primary key: `comparison_id`, `cohort_id`, `bootstrap_id`, `metric_set_id`, `scope`, `temporal_scale`, `period_type`, `aggregation_id`, `product`, `model`, `reference_variant`, `extraction_method`, `footprint_set_id`, `contrast_dimension`, `contrast_baseline`, `metric`, `interval_method`, `block_unit`, `block_length`.

| Column | Kind | Unit | Null | Description |
|---|---|---|---|---|
| `comparison_id` | string |  | key | Comparison identifier |
| `cohort_id` | string |  | key | Cohort identifier |
| `bootstrap_id` | string |  | key | Declared bootstrap settings |
| `metric_set_id` | string |  | key | Metric set whose metrics are resampled |
| `scope` | string |  | key | Site ID the interval refers to (station level only) |
| `scope_type` | string |  | no | 'site' (network summaries are step 11); one of `site` |
| `temporal_scale` | string |  | key | daily pairs or period totals; one of `daily`, `period` |
| `period_type` | string |  | key | Period type of period totals; 'none' for daily; one of `none`, `day`, `month`, `growing_season`, `year`, `water_year` |
| `aggregation_id` | string |  | key | Period aggregation; 'none' for daily |
| `product` | string |  | key | daily_pairs, or the period product; one of `daily_pairs`, `partial_sum`, `complete_total`, `estimated_total` |
| `model` | string |  | key | Model (the minuend of a contrast) |
| `reference_variant` | string |  | key | Reference variant (minuend of a contrast); one of `et_raw`, `et_corrected_measured`, `et_corrected_inclusive` |
| `extraction_method` | string |  | key | Extraction method (minuend of a contrast) |
| `footprint_set_id` | string |  | key | Footprint set (minuend of a contrast) or 'none' |
| `contrast_dimension` | string |  | key | 'none' for a metric; otherwise the dimension in which the contrast's two sides differ; one of `none`, `extraction_side`, `reference_variant`, `model` |
| `contrast_baseline` | string |  | key | 'none', or the subtrahend: a side label (method@footprint_set), reference variant or model |
| `metric` | string |  | key | Stable metric identifier (etcompare.metrics) |
| `interval_method` | string |  | key | percentile or basic; one of `percentile`, `basic` |
| `block_unit` | string |  | key | Resampling unit: site_day (daily pairs) or period; one of `site_day`, `period` |
| `block_length` | int |  | key | Nominal block length in block units (1 = iid units) |
| `estimate` | float |  | yes | Step-9 point estimate on the cohort (for a contrast: metric(target) - metric(baseline)); null when undefined |
| `lower` | float |  | yes | Lower bound; null unless status is ok |
| `upper` | float |  | yes | Upper bound; null unless status is ok |
| `units` | string |  | no | Units of estimate and bounds |
| `confidence_level` | float | 1 | no | Nominal two-sided level |
| `seed` | int |  | no | Declared random seed |
| `n_replicates_requested` | int |  | no | Requested replicates |
| `n_replicates_valid` | int |  | no | Replicates with a defined statistic |
| `n_segments` | int |  | no | Independent uninterrupted segments of the resampled sample |
| `n_blocks` | int |  | no | Disjoint full-length blocks the segments hold (the support compared with min_blocks) |
| `n_days` | int |  | no | Days in the scored sample (matched days for period totals) |
| `n_periods` | int |  | yes | Periods in the scored sample (period totals only) |
| `status` | string |  | no | ok, or why no interval is given; one of `ok`, `insufficient_support`, `estimate_undefined`, `insufficient_valid_replicates` |
| `status_detail` | string |  | yes | Reason behind a non-ok status |
| `metric_role` | string |  | no | Role of the metric (etcompare.metrics); one of `score`, `diagnostic`, `descriptive`, `component` |
| `ranking_scope` | string |  | no | Rankings the cohort supports; one of `all_models`, `pairwise`, `none` |
| `flags` | string |  | yes | ';'-joined flags (metric flags plus resampling flags) |

## residual_autocorrelation

Version 1. Autocorrelation of daily errors e = modeled - observed of one cohort, at exact calendar lags (pairs of cohort days k days apart; missing days are never compressed). A diagnostic for choosing bootstrap block lengths, not a selection rule.

Primary key: `comparison_id`, `cohort_id`, `site_id`, `model`, `reference_variant`, `extraction_method`, `footprint_set_id`, `lag`.

| Column | Kind | Unit | Null | Description |
|---|---|---|---|---|
| `comparison_id` | string |  | key | Comparison identifier |
| `cohort_id` | string |  | key | Cohort identifier |
| `site_id` | string |  | key | Station |
| `model` | string |  | key | Canonical model name |
| `reference_variant` | string |  | key | Reference variant; one of `et_raw`, `et_corrected_measured`, `et_corrected_inclusive` |
| `extraction_method` | string |  | key | Extraction method |
| `footprint_set_id` | string |  | key | Footprint set of the extraction (or 'none') |
| `lag` | int |  | key | Calendar lag in days |
| `n_days` | int |  | no | Cohort days of the series |
| `n_pairs` | int |  | no | Pairs of cohort days exactly lag days apart |
| `autocorrelation` | float | 1 | yes | mean over pairs of d_t*d_(t+lag) / mean(d**2), d = e - mean(e); null without pairs or with constant errors |
| `white_noise_bound` | float | 1 | yes | 1.96 / sqrt(n_pairs): approximate 95% bound for an uncorrelated series |

## model_rankings

Version 1. Station-level ranks of the models of one frozen ranking cohort, per context, metric and bootstrap block length, with rank stability from shared-index replicates. Ranks are descriptive; top_rank_frequency is a bootstrap stability measure, not the probability that a model is best.

Primary key: `ranking_id`, `comparison_id`, `cohort_id`, `subset`, `metric_set_id`, `bootstrap_id`, `scope`, `temporal_scale`, `period_type`, `aggregation_id`, `product`, `reference_variant`, `extraction_method`, `footprint_set_id`, `metric`, `model`, `block_unit`, `block_length`.

| Column | Kind | Unit | Null | Description |
|---|---|---|---|---|
| `ranking_id` | string |  | key | Declared ranking (rankings[] in the config) |
| `comparison_id` | string |  | key | Comparison identifier |
| `cohort_id` | string |  | key | Cohort identifier |
| `subset` | string |  | key | 'all', or the frozen sub-cohort ranked (e.g. 'year=2025') |
| `metric_set_id` | string |  | key | Metric set of the point estimates |
| `bootstrap_id` | string |  | key | Bootstrap whose replicates are used ('none' if none) |
| `scope` | string |  | key | Site ID (station level) |
| `temporal_scale` | string |  | key | daily pairs or period totals; one of `daily`, `period` |
| `period_type` | string |  | key | Period type of period totals; 'none' for daily; one of `none`, `day`, `month`, `growing_season`, `year`, `water_year` |
| `aggregation_id` | string |  | key | Period aggregation; 'none' for daily |
| `product` | string |  | key | daily_pairs, or the period product; one of `daily_pairs`, `partial_sum`, `complete_total`, `estimated_total` |
| `reference_variant` | string |  | key | Reference variant; one of `et_raw`, `et_corrected_measured`, `et_corrected_inclusive` |
| `extraction_method` | string |  | key | Extraction method |
| `footprint_set_id` | string |  | key | Footprint set of the extraction (or 'none') |
| `metric` | string |  | key | Ranked metric (role score) |
| `model` | string |  | key | Canonical model name |
| `block_unit` | string |  | key | Resampling unit: site_day (daily pairs) or period; one of `site_day`, `period` |
| `block_length` | int |  | key | Bootstrap block length in block units; 0 = not resampled |
| `scope_type` | string |  | no | 'site' (station level); one of `site` |
| `direction` | string |  | no | Better direction of the metric (RANKING_DIRECTIONS); one of `lower`, `higher`, `closest_to_zero` |
| `value` | float |  | yes | Step-9 point estimate; null when undefined |
| `units` | string |  | no | Units of value |
| `rank` | float |  | yes | Point mid-rank (1 = best; tied models share the average of their positions); null unless every model's estimate is defined |
| `n_models` | int |  | no | Models ranked in the context |
| `n_tied` | int |  | yes | Models sharing this model's point value (including itself) |
| `mean_rank` | float |  | yes | Mean replicate mid-rank; null unless status is ok |
| `rank_lower` | float |  | yes | Lower rank bound: lower quantile of replicate mid-ranks |
| `rank_upper` | float |  | yes | Upper rank bound: higher quantile of replicate mid-ranks |
| `top_rank_frequency` | float | 1 | yes | Share of valid replicates in which the model holds position 1 (ties split it); a bootstrap stability measure |
| `confidence_level` | float | 1 | yes | Level of the rank interval and of contrast intervals |
| `leader_status` | string |  | no | unique_leader only when the point-best model's contrast interval excludes zero in its favour against every other model; one of `unique_leader`, `no_unique_leader`, `not_assessed` |
| `leading_models` | string |  | yes | ';'-joined point-best model(s) and every model not separated from it by its contrast interval |
| `leader_interval_method` | string |  | yes | Interval method used for leader_status; one of `percentile`, `basic` |
| `seed` | int |  | yes | Bootstrap seed |
| `n_replicates_requested` | int |  | no | Requested replicates |
| `n_replicates_valid` | int |  | no | Replicates in which every model's metric is defined |
| `n_sites` | int |  | no | Sites in the context (1: station level) |
| `n_days` | int |  | no | Days in the scored sample (matched days for period totals) |
| `n_days_reference_filled` | int |  | no | Scored days with a gap-filled reference |
| `n_periods` | int |  | yes | Periods in the scored sample (period totals only) |
| `n_segments` | int |  | yes | Uninterrupted segments of the resampled sample |
| `n_blocks` | int |  | yes | Disjoint full-length blocks (the support compared with min_blocks) |
| `status` | string |  | no | ok, or why rank statistics are missing; one of `ok`, `insufficient_support`, `estimate_undefined`, `insufficient_valid_replicates`, `not_resampled` |
| `status_detail` | string |  | yes | Reason behind a non-ok status |
| `ranking_scope` | string |  | no | all_models or pairwise; one of `all_models`, `pairwise` |
| `cohort_membership_sha256` | string |  | no | Cohort fingerprint |
| `flags` | string |  | yes | ';'-joined flags of the ranked metric rows |

## rank_distribution

Version 1. Per-model distribution of rank positions over the valid bootstrap replicates of a model_rankings row with status ok. Tied models share their positions equally, so each model's frequencies and each position's frequencies sum to 1.

Primary key: `ranking_id`, `comparison_id`, `cohort_id`, `subset`, `metric_set_id`, `bootstrap_id`, `scope`, `temporal_scale`, `period_type`, `aggregation_id`, `product`, `reference_variant`, `extraction_method`, `footprint_set_id`, `metric`, `model`, `block_unit`, `block_length`, `position`.

| Column | Kind | Unit | Null | Description |
|---|---|---|---|---|
| `ranking_id` | string |  | key | Declared ranking (rankings[] in the config) |
| `comparison_id` | string |  | key | Comparison identifier |
| `cohort_id` | string |  | key | Cohort identifier |
| `subset` | string |  | key | 'all', or the frozen sub-cohort ranked (e.g. 'year=2025') |
| `metric_set_id` | string |  | key | Metric set of the point estimates |
| `bootstrap_id` | string |  | key | Bootstrap whose replicates are used ('none' if none) |
| `scope` | string |  | key | Site ID (station level) |
| `temporal_scale` | string |  | key | daily pairs or period totals; one of `daily`, `period` |
| `period_type` | string |  | key | Period type of period totals; 'none' for daily; one of `none`, `day`, `month`, `growing_season`, `year`, `water_year` |
| `aggregation_id` | string |  | key | Period aggregation; 'none' for daily |
| `product` | string |  | key | daily_pairs, or the period product; one of `daily_pairs`, `partial_sum`, `complete_total`, `estimated_total` |
| `reference_variant` | string |  | key | Reference variant; one of `et_raw`, `et_corrected_measured`, `et_corrected_inclusive` |
| `extraction_method` | string |  | key | Extraction method |
| `footprint_set_id` | string |  | key | Footprint set of the extraction (or 'none') |
| `metric` | string |  | key | Ranked metric |
| `model` | string |  | key | Canonical model name |
| `block_unit` | string |  | key | Resampling unit: site_day (daily pairs) or period; one of `site_day`, `period` |
| `block_length` | int |  | key | Bootstrap block length in block units; 0 = not resampled |
| `position` | int |  | key | Rank position (1 = best) |
| `frequency` | float | 1 | no | Share of valid replicates at this position |
| `n_replicates_valid` | int |  | no | Valid replicates |

## model_contrasts

Version 1. Paired model contrasts metric(model_a) - metric(model_b) inside one frozen ranking context, computed replicate by replicate on shared bootstrap indices, with practical equivalence and (optional) formal tests. model_b is the model declared earlier in the comparison.

Primary key: `ranking_id`, `comparison_id`, `cohort_id`, `subset`, `metric_set_id`, `bootstrap_id`, `scope`, `temporal_scale`, `period_type`, `aggregation_id`, `product`, `reference_variant`, `extraction_method`, `footprint_set_id`, `metric`, `model_a`, `model_b`, `contrast_basis`, `interval_method`, `block_unit`, `block_length`.

| Column | Kind | Unit | Null | Description |
|---|---|---|---|---|
| `ranking_id` | string |  | key | Declared ranking (rankings[] in the config) |
| `comparison_id` | string |  | key | Comparison identifier |
| `cohort_id` | string |  | key | Cohort identifier |
| `subset` | string |  | key | 'all', or the frozen sub-cohort ranked (e.g. 'year=2025') |
| `metric_set_id` | string |  | key | Metric set of the point estimates |
| `bootstrap_id` | string |  | key | Bootstrap whose replicates are used ('none' if none) |
| `scope` | string |  | key | Site ID (station level) |
| `temporal_scale` | string |  | key | daily pairs or period totals; one of `daily`, `period` |
| `period_type` | string |  | key | Period type of period totals; 'none' for daily; one of `none`, `day`, `month`, `growing_season`, `year`, `water_year` |
| `aggregation_id` | string |  | key | Period aggregation; 'none' for daily |
| `product` | string |  | key | daily_pairs, or the period product; one of `daily_pairs`, `partial_sum`, `complete_total`, `estimated_total` |
| `reference_variant` | string |  | key | Reference variant; one of `et_raw`, `et_corrected_measured`, `et_corrected_inclusive` |
| `extraction_method` | string |  | key | Extraction method |
| `footprint_set_id` | string |  | key | Footprint set of the extraction (or 'none') |
| `metric` | string |  | key | Contrasted metric |
| `model_a` | string |  | key | Minuend model |
| `model_b` | string |  | key | Subtrahend (baseline) model, declared earlier |
| `contrast_basis` | string |  | key | value: metric_a - metric_b; absolute_value: \|metric_a\| - \|metric_b\| (closest_to_zero metrics); one of `value`, `absolute_value` |
| `interval_method` | string |  | key | percentile or basic; one of `percentile`, `basic` |
| `block_unit` | string |  | key | Resampling unit: site_day (daily pairs) or period; one of `site_day`, `period` |
| `block_length` | int |  | key | Bootstrap block length in block units; 0 = not resampled |
| `scope_type` | string |  | no | 'site' (station level); one of `site` |
| `direction` | string |  | no | Better direction of the metric; one of `lower`, `higher`, `closest_to_zero` |
| `ranking_basis` | bool |  | no | This basis orders the models (false for the signed difference of a closest_to_zero metric) |
| `sign_meaning` | string |  | no | How to read the sign of estimate |
| `estimate` | float |  | yes | Point contrast (practical effect size, in units); null when undefined |
| `lower` | float |  | yes | Lower bound; null unless status is ok |
| `upper` | float |  | yes | Upper bound; null unless status is ok |
| `units` | string |  | no | Units of estimate, bounds and margin |
| `interval_position` | string |  | yes | Where the interval lies relative to zero; one of `above_zero`, `below_zero`, `includes_zero` |
| `favoured_model` | string |  | yes | Model favoured by an interval excluding zero (ranking basis only); null otherwise |
| `equivalence_margin` | float |  | yes | Predeclared practical-equivalence margin (+/-) |
| `equivalence` | string |  | no | Interval inside (-margin, margin): equivalent; entirely beyond it: practically_different; an interval crossing zero is not equivalence; one of `not_assessed`, `not_applicable`, `no_interval`, `equivalent`, `practically_different`, `inconclusive` |
| `confidence_level` | float | 1 | yes | Nominal two-sided level |
| `seed` | int |  | yes | Bootstrap seed |
| `n_replicates_requested` | int |  | no | Requested replicates |
| `n_replicates_valid` | int |  | no | Replicates with both metrics defined |
| `n_sites` | int |  | no | Sites (1: station level) |
| `n_days` | int |  | no | Days in the scored sample |
| `n_periods` | int |  | yes | Periods in the scored sample (period totals only) |
| `n_segments` | int |  | yes | Uninterrupted segments of the resampled sample |
| `n_blocks` | int |  | yes | Disjoint full-length blocks |
| `status` | string |  | no | ok, or why no interval is given; one of `ok`, `insufficient_support`, `estimate_undefined`, `insufficient_valid_replicates`, `not_resampled` |
| `status_detail` | string |  | yes | Reason behind a non-ok status |
| `inference` | string |  | no | formal only for a tested ranking-basis contrast under a declared multiplicity policy; everything else is exploratory; one of `exploratory`, `formal` |
| `p_value` | float | 1 | yes | Percentile-inversion bootstrap p-value (formal tests only) |
| `p_value_adjusted` | float | 1 | yes | Multiplicity-adjusted p-value |
| `multiplicity_policy` | string |  | yes | Declared policy (formal tests only); one of `holm`, `bonferroni` |
| `family_size` | int |  | yes | Contrasts in the multiplicity family |
| `test_decision` | string |  | yes | reject_zero / not_rejected at 1 - confidence_level; one of `reject_zero`, `not_rejected` |
| `ranking_scope` | string |  | no | all_models or pairwise; one of `all_models`, `pairwise` |
| `cohort_membership_sha256` | string |  | no | Cohort fingerprint |
| `flags` | string |  | yes | ';'-joined flags of both metric rows |

## network_summaries

Version 1. Summaries of station-level metrics across the sites of one frozen cohort. The equal-site mean is the primary network result; observation weightings are labelled alternatives, and pooled RMSE (over all site observations) is not the mean station RMSE. With n_sites = 1 a row is a single station, not network evidence. No interval: site-population inference needs hierarchical resampling (not implemented).

Primary key: `ranking_id`, `comparison_id`, `cohort_id`, `subset`, `metric_set_id`, `bootstrap_id`, `temporal_scale`, `period_type`, `aggregation_id`, `product`, `reference_variant`, `extraction_method`, `footprint_set_id`, `model`, `metric`, `summary_type`.

| Column | Kind | Unit | Null | Description |
|---|---|---|---|---|
| `ranking_id` | string |  | key | Declared ranking (rankings[] in the config) |
| `comparison_id` | string |  | key | Comparison identifier |
| `cohort_id` | string |  | key | Cohort identifier |
| `subset` | string |  | key | 'all', or the frozen sub-cohort ranked (e.g. 'year=2025') |
| `metric_set_id` | string |  | key | Metric set of the point estimates |
| `bootstrap_id` | string |  | key | Bootstrap whose replicates are used ('none' if none) |
| `temporal_scale` | string |  | key | daily pairs or period totals; one of `daily`, `period` |
| `period_type` | string |  | key | Period type of period totals; 'none' for daily; one of `none`, `day`, `month`, `growing_season`, `year`, `water_year` |
| `aggregation_id` | string |  | key | Period aggregation; 'none' for daily |
| `product` | string |  | key | daily_pairs, or the period product; one of `daily_pairs`, `partial_sum`, `complete_total`, `estimated_total` |
| `reference_variant` | string |  | key | Reference variant; one of `et_raw`, `et_corrected_measured`, `et_corrected_inclusive` |
| `extraction_method` | string |  | key | Extraction method |
| `footprint_set_id` | string |  | key | Footprint set of the extraction (or 'none') |
| `model` | string |  | key | Canonical model name |
| `metric` | string |  | key | Summarized metric |
| `summary_type` | string |  | key | How station metrics are combined; one of `equal_site_mean`, `observation_weighted_mean`, `sqrt_observation_weighted_mean`, `pooled_rmse` |
| `scope_type` | string |  | no | 'network'; one of `network` |
| `summary_label` | string |  | no | Human-readable label of the summary |
| `primary` | bool |  | no | True for the equal-site mean |
| `weighting` | string |  | no | Site weight: equal, n_obs, sqrt_n_obs, or pooled; one of `equal`, `n_obs`, `sqrt_n_obs`, `pooled` |
| `observation_unit` | string |  | no | What n_obs counts: day (daily pairs) or period; one of `day`, `period` |
| `value` | float |  | yes | Summary value; null unless every site's metric is defined |
| `units` | string |  | no | Units of value |
| `n_sites` | int |  | no | Sites summarized |
| `sites` | string |  | no | ';'-joined site IDs |
| `n_days` | int |  | no | Days over all sites |
| `n_days_reference_filled` | int |  | no | Gap-filled reference days over all sites |
| `n_periods` | int |  | yes | Periods over all sites (period totals only) |
| `network_evidence` | bool |  | no | False when n_sites < 2 |
| `status` | string |  | no | ok, single_site or estimate_undefined; one of `ok`, `single_site`, `estimate_undefined` |
| `status_detail` | string |  | yes | Reason behind a non-ok status |
| `cohort_membership_sha256` | string |  | yes | Cohort fingerprint (if known) |
| `flags` | string |  | yes | ';'-joined flags |

## ranking_sensitivity

Version 1. How point ranks change between two ranking contexts that differ in one dimension (station, year, temporal scale, reference variant, extraction method) or only in their frozen cohort. Each context is an internally frozen cohort; same_cohort says whether both use the same cohort (same days).

Primary key: `ranking_id`, `dimension`, `context_a`, `context_b`, `metric`, `model`.

| Column | Kind | Unit | Null | Description |
|---|---|---|---|---|
| `ranking_id` | string |  | key | Declared ranking |
| `dimension` | string |  | key | The one dimension in which the contexts differ; one of `station`, `year`, `temporal_scale`, `reference_variant`, `extraction_method`, `cohort` |
| `context_a` | string |  | key | Context label: comparison\|subset\|scope\|scale\|variant\|side |
| `context_b` | string |  | key | Context label of the other context |
| `metric` | string |  | key | Ranked metric |
| `model` | string |  | key | Canonical model name |
| `rank_a` | float |  | no | Point mid-rank in context_a |
| `rank_b` | float |  | no | Point mid-rank in context_b |
| `rank_change` | float |  | no | rank_b - rank_a (positive: worse in context_b) |
| `n_models` | int |  | no | Models ranked in both contexts |
| `same_cohort` | bool |  | no | Both contexts use one cohort fingerprint |
| `comparison_a` | string |  | no | Comparison of context_a |
| `comparison_b` | string |  | no | Comparison of context_b |
| `n_days_a` | int |  | no | Days scored in context_a |
| `n_days_b` | int |  | no | Days scored in context_b |
| `n_periods_a` | int |  | yes | Periods scored in context_a |
| `n_periods_b` | int |  | yes | Periods scored in context_b |

## closure_magnitude

Version 1. Size of the existing energy-balance correction on corrected_from_raw days only (never recomputed): ET_corr - ET, ET_corr / ET and the closure factor, plus declared closure metadata, by period. Gap-filled days are excluded and counted. A correction magnitude is reference sensitivity, not a calibrated uncertainty envelope containing the truth.

Primary key: `diagnostics_id`, `comparison_id`, `cohort_id`, `site_id`, `day_set`, `period_type`, `period_label`, `quantity`.

| Column | Kind | Unit | Null | Description |
|---|---|---|---|---|
| `diagnostics_id` | string |  | key | Declared diagnostics (diagnostics[] in the config) |
| `comparison_id` | string |  | key | Comparison whose sites (and, for day_set cohort, days) are summarised |
| `cohort_id` | string |  | key | Cohort identifier; 'none' for the station record |
| `site_id` | string |  | key | Station |
| `day_set` | string |  | key | station_record: every corrected_from_raw station day of the comparison's period; cohort: those that are cohort days; one of `station_record`, `cohort` |
| `period_type` | string |  | key | all, month (YYYY-MM) or meteorological season (YYYY-DJF, December counted with the following year); one of `all`, `month`, `season` |
| `period_label` | string |  | key | 'all', 'YYYY-MM' or 'YYYY-SSS' |
| `quantity` | string |  | key | correction_mm (ET_corr - ET), correction_ratio (ET_corr / ET), closure_factor, or a declared closure-metadata variable |
| `quantity_units` | string |  | no | Units of the statistics |
| `quantity_definition` | string |  | no | Definition or declared meaning |
| `n_days` | int |  | no | corrected_from_raw days with a value |
| `n_days_filled_excluded` | int |  | no | corrected_filled days in the period, excluded |
| `n_days_value_missing` | int |  | no | corrected_from_raw days without a value (or with ET = 0 for the ratio) |
| `mean` | float |  | yes | Mean |
| `std` | float |  | yes | Population standard deviation |
| `min` | float |  | yes | Minimum |
| `q05` | float |  | yes | 5th percentile (numpy linear) |
| `q25` | float |  | yes | 25th percentile |
| `median` | float |  | yes | Median |
| `q75` | float |  | yes | 75th percentile |
| `q95` | float |  | yes | 95th percentile |
| `max` | float |  | yes | Maximum |
| `status` | string |  | no | ok or no_days; one of `ok`, `no_days` |
| `flags` | string |  | yes | ';'-joined flags |

## reference_provenance_periods

Version 1. Reference provenance of a cohort's days per period: observation-dominated (corrected_from_raw) and gap-filled (corrected_filled) days and the gap-filled fraction, a conditioning variable. Gap-filled reference ET shares gridMET drivers with the OpenET interpolation (D-005).

Primary key: `diagnostics_id`, `comparison_id`, `cohort_id`, `site_id`, `reference_variant`, `period_type`, `period_label`.

| Column | Kind | Unit | Null | Description |
|---|---|---|---|---|
| `diagnostics_id` | string |  | key | Declared diagnostics |
| `comparison_id` | string |  | key | Comparison identifier |
| `cohort_id` | string |  | key | Cohort identifier |
| `site_id` | string |  | key | Station |
| `reference_variant` | string |  | key | Reference variant; one of `et_raw`, `et_corrected_measured`, `et_corrected_inclusive` |
| `period_type` | string |  | key | all, month or season; one of `all`, `month`, `season` |
| `period_label` | string |  | key | 'all', 'YYYY-MM' or 'YYYY-SSS' |
| `n_days` | int |  | no | Cohort days in the period |
| `n_days_corrected_from_raw` | int |  | no | Observation-dominated reference days |
| `n_days_corrected_filled` | int |  | no | Gap-filled reference days |
| `gap_filled_fraction` | float | 1 | yes | n_days_corrected_filled / n_days |
| `flags` | string |  | yes | ';'-joined flags (shared_gridmet_drivers when any day is filled) |

## conditional_errors

Version 1. Metrics of paired errors e = P - O per model inside bins of a declared conditioning variable, within one frozen cohort. Bins come from the configuration (fixed edges, training quantiles of the variable, calendar, sectors or reference provenance), never from the errors. Sparse bins carry a status, not a value. Conditional summaries describe associations, not causes.

Primary key: `diagnostics_id`, `comparison_id`, `cohort_id`, `site_id`, `model`, `reference_variant`, `extraction_method`, `footprint_set_id`, `variable`, `bin_index`, `metric`, `interval_method`, `block_length`.

| Column | Kind | Unit | Null | Description |
|---|---|---|---|---|
| `diagnostics_id` | string |  | key | Declared diagnostics |
| `comparison_id` | string |  | key | Comparison identifier |
| `cohort_id` | string |  | key | Cohort identifier |
| `site_id` | string |  | key | Station |
| `model` | string |  | key | Canonical model name |
| `reference_variant` | string |  | key | Reference variant; one of `et_raw`, `et_corrected_measured`, `et_corrected_inclusive` |
| `extraction_method` | string |  | key | Extraction method |
| `footprint_set_id` | string |  | key | Footprint set of the extraction (or 'none') |
| `variable` | string |  | key | Conditioning variable (declared name, season, month, gap_filled_fraction or reference_provenance) |
| `bin_index` | int |  | key | Bin position; negative for days accounted for but not binned (-1 outside edges, -2 variable missing, -3 excluded provenance) |
| `metric` | string |  | key | Metric (etcompare.metrics) |
| `interval_method` | string |  | key | percentile / basic, or 'none' without intervals |
| `block_length` | int |  | key | Bootstrap block length in days; 0 = not resampled |
| `bin_label` | string |  | no | e.g. '[0.5, 1)', 'JJA', 'corrected_filled' |
| `bin_lower` | float |  | yes | Lower edge (intervals and sectors) |
| `bin_upper` | float |  | yes | Upper edge |
| `bin_closed` | string |  | yes | Which edges belong to the bin: left or both |
| `bin_kind` | string |  | no | interval, category, sector, or a non-binned remainder; one of `interval`, `category`, `sector`, `outside_edges`, `variable_missing`, `excluded_provenance` |
| `bin_source` | string |  | no | Where the bins come from; one of `fixed_edges`, `training_quantiles`, `calendar`, `sectors`, `provenance` |
| `variable_column` | string |  | yes | Source column of the variable |
| `variable_units` | string |  | yes | Declared units of the variable |
| `variable_status` | string |  | yes | verified / provisional / derived |
| `value` | float |  | yes | Metric on the bin sample (compute_metrics); null unless status ok |
| `units` | string |  | no | Units of value |
| `status` | string |  | no | ok, or why there is no value; one of `ok`, `sparse_bin`, `empty_bin`, `outside_edges`, `variable_missing`, `excluded_provenance`, `estimate_undefined` |
| `status_detail` | string |  | yes | Reason behind a non-ok status |
| `n_days` | int |  | no | This model's days in the bin |
| `n_days_bin_cohort` | int |  | no | Cohort days in the bin for any model |
| `n_days_reference_filled` | int |  | no | This model's bin days with gap-filled reference |
| `n_days_series` | int |  | no | This model's cohort days (all bins) |
| `min_bin_days` | int |  | no | Declared minimum bin size |
| `bin_days_shared` | bool |  | no | Every model of the cohort has the same days in this bin |
| `lower` | float |  | yes | Interval lower bound; null unless interval_status is ok |
| `upper` | float |  | yes | Interval upper bound |
| `confidence_level` | float | 1 | yes | Nominal two-sided level |
| `n_segments` | int |  | yes | Uninterrupted runs of bin days |
| `n_blocks` | int |  | yes | Disjoint full-length blocks of the bin's runs |
| `n_replicates_valid` | int |  | yes | Replicates with a defined metric |
| `interval_status` | string |  | no | ok, or why no interval is given; one of `ok`, `insufficient_support`, `estimate_undefined`, `insufficient_valid_replicates`, `not_resampled`, `no_estimate` |
| `interval_detail` | string |  | yes | Reason behind a non-ok interval status |
| `flags` | string |  | yes | ';'-joined flags |

## error_regressions

Version 1. Ordinary least squares of daily error e = P - O on a declared predictor (net radiation) per model and reference variant. With an energy-balance-corrected reference the error and the predictor are mathematically coupled through the correction; the uncorrected reference version is reported alongside where the cohort has it. Descriptive only.

Primary key: `diagnostics_id`, `comparison_id`, `cohort_id`, `site_id`, `model`, `reference_variant`, `extraction_method`, `footprint_set_id`, `predictor`.

| Column | Kind | Unit | Null | Description |
|---|---|---|---|---|
| `diagnostics_id` | string |  | key | Declared diagnostics |
| `comparison_id` | string |  | key | Comparison identifier |
| `cohort_id` | string |  | key | Cohort identifier |
| `site_id` | string |  | key | Station |
| `model` | string |  | key | Canonical model name |
| `reference_variant` | string |  | key | Reference variant; one of `et_raw`, `et_corrected_measured`, `et_corrected_inclusive` |
| `extraction_method` | string |  | key | Extraction method |
| `footprint_set_id` | string |  | key | Footprint set of the extraction (or 'none') |
| `predictor` | string |  | key | Declared predictor variable |
| `predictor_column` | string |  | no | Source column |
| `predictor_units` | string |  | no | Declared units |
| `predictor_status` | string |  | no | verified / provisional |
| `slope` | float |  | yes | d e / d predictor; null when undefined |
| `slope_units` | string |  | no | mm d-1 per predictor unit |
| `intercept` | float | mm d-1 | yes | e at predictor = 0 |
| `pearson_r` | float | 1 | yes | Correlation of e and the predictor |
| `n_days` | int |  | no | Days with a predictor value |
| `n_days_predictor_missing` | int |  | no | Cohort days without a predictor value |
| `n_days_reference_filled` | int |  | no | Used days with gap-filled reference |
| `status` | string |  | no | ok or estimate_undefined; one of `ok`, `estimate_undefined` |
| `status_detail` | string |  | yes | Reason behind a non-ok status |
| `coupling` | string |  | no | energy_balance_corrected_reference (coupled), uncorrected_reference, or includes_gap_filled_days |
| `flags` | string |  | yes | ';'-joined flags |

## diagnostic_availability

Version 1. Which diagnostics ran and which were skipped, with reasons. An undeclared or unverified variable, a missing column, an all-null column or absent metadata skips only the diagnostic that needs it.

Primary key: `diagnostics_id`, `comparison_id`, `cohort_id`, `analysis`, `variable`, `reference_variant`, `extraction_method`, `footprint_set_id`.

| Column | Kind | Unit | Null | Description |
|---|---|---|---|---|
| `diagnostics_id` | string |  | key | Declared diagnostics |
| `comparison_id` | string |  | key | Comparison identifier |
| `cohort_id` | string |  | key | Cohort identifier, or 'all' |
| `analysis` | string |  | key | Diagnostic (closure_magnitude, conditional_error, ...) |
| `variable` | string |  | key | Variable it needs, or 'none' |
| `reference_variant` | string |  | key | Reference variant, or 'all' |
| `extraction_method` | string |  | key | Extraction method, or 'all' |
| `footprint_set_id` | string |  | key | Footprint set, or 'all' |
| `status` | string |  | no | available or not_available; one of `available`, `not_available` |
| `reason_code` | string |  | yes | Stable reason when not_available |
| `reason` | string |  | yes | Explanation (also for labelled available diagnostics) |
| `variable_column` | string |  | yes | Declared source column |
| `variable_units` | string |  | yes | Declared units |
| `variable_status` | string |  | yes | Declared status |
| `n_days_cohort` | int |  | yes | Cohort days considered |
| `n_days_with_value` | int |  | yes | Of those, days with a variable value |
| `flags` | string |  | yes | ';'-joined flags |

## reference_rank_changes

Version 1. Point-rank changes between reference variants, taken from ranking_sensitivity (never re-ranked), with the effect each pair isolates. raw vs corrected-measured on one cohort is closure sensitivity; corrected-measured vs corrected-inclusive mixes gap-filled days and different cohort days (D-004). Reference sensitivity, not an uncertainty bound.

Primary key: `diagnostics_id`, `ranking_id`, `context_a`, `context_b`, `metric`, `model`.

| Column | Kind | Unit | Null | Description |
|---|---|---|---|---|
| `diagnostics_id` | string |  | key | Declared diagnostics |
| `ranking_id` | string |  | key | Declared ranking |
| `context_a` | string |  | key | Context label of ranking_sensitivity |
| `context_b` | string |  | key | Context label of ranking_sensitivity |
| `metric` | string |  | key | Ranked metric |
| `model` | string |  | key | Canonical model name |
| `variant_a` | string |  | no | Reference variant of context_a; one of `et_raw`, `et_corrected_measured`, `et_corrected_inclusive` |
| `variant_b` | string |  | no | Reference variant of context_b; one of `et_raw`, `et_corrected_measured`, `et_corrected_inclusive` |
| `effect` | string |  | no | What the pair isolates; one of `closure`, `closure_and_cohort_days`, `gap_filling_and_cohort_days`, `closure_and_gap_filling`, `other` |
| `rank_a` | float |  | no | Point mid-rank in context_a |
| `rank_b` | float |  | no | Point mid-rank in context_b |
| `rank_change` | float |  | no | rank_b - rank_a (positive: worse in context_b) |
| `n_models` | int |  | no | Models ranked |
| `same_cohort` | bool |  | no | Both contexts use one cohort (same days) |
| `comparison_a` | string |  | no | Comparison of context_a |
| `comparison_b` | string |  | no | Comparison of context_b |
| `n_days_a` | int |  | no | Days scored in context_a |
| `n_days_b` | int |  | no | Days scored in context_b |

## landscape_class_fractions

Version 1. Weight of a support (footprint, pixel window, nearest pixel, polygon) on each class of a categorical map, moved onto the map's native grid by exact area overlap (classes are never resampled). class_frac_of_valid is conditional on valid cells and sums to 1 over the class rows; nodata weight is a separate row. Landscape description only: not an ET accuracy guarantee.

Primary key: `representativeness_id`, `site_id`, `date`, `day_set`, `extraction_method`, `footprint_set_id`, `layer_id`, `matching_policy`, `domain`, `class_code`.

| Column | Kind | Unit | Null | Description |
|---|---|---|---|---|
| `representativeness_id` | string |  | key | Declared representativeness analysis |
| `site_id` | string |  | key | Station |
| `date` | date |  | key | Footprint station-day label |
| `day_set` | string |  | key | Footprint set whose usable days this row belongs to (fixed supports are evaluated on those frozen days) |
| `extraction_method` | string |  | key | footprint_conservative, pixel_window_<n>x<n>, nearest_tower_pixel or polygon_<id> (D-029 naming) |
| `footprint_set_id` | string |  | key | Footprint set of the support, 'none' for fixed supports |
| `layer_id` | string |  | key | Declared landscape layer |
| `matching_policy` | string |  | key | Declared policy matching layer dates to the day |
| `domain` | string |  | key | full_domain (footprint grid), contour_<level> (footprint cumulative-contribution contour) or full_support (fixed supports) |
| `class_code` | string |  | key | Legend code, or 'nodata' |
| `class_role` | string |  | no | class or nodata; one of `class`, `nodata` |
| `class_name` | string |  | no | Legend name ('nodata' for the nodata row) |
| `class_weight` | float | 1 | no | Domain weight on this class (or on nodata) |
| `class_frac_of_valid` | float | 1 | yes | class_weight / weight_valid; null for nodata |
| `class_frac_of_grid_total` | float | 1 | no | class_weight / weight_grid_total |
| `weight_denominator` | string |  | no | What weight_grid_total is: e.g. normalized_truncated_footprint (D-008), window_pixels, polygon_area |
| `weight_grid_total` | float | 1 | no | Support total: footprint-grid total, or 1 for fixed supports |
| `weight_domain` | float | 1 | no | Weight inside the domain (= total for full_domain; contour mass for contours) |
| `weight_in_layer_extent` | float | 1 | no | Domain weight on cells of the layer raster window |
| `weight_valid` | float | 1 | no | Domain weight on valid (non-nodata) layer cells |
| `weight_nodata` | float | 1 | no | Domain weight on nodata layer cells inside the extent |
| `valid_frac_of_grid_total` | float | 1 | no | weight_valid / weight_grid_total (separate denominator; never multiplied with domain capture) |
| `nodata_frac_of_grid_total` | float | 1 | no | weight_nodata / weight_grid_total |
| `outside_layer_frac_of_grid_total` | float | 1 | no | (weight_domain - weight_in_layer_extent) / weight_grid_total |
| `domain_frac_of_grid_total` | float | 1 | no | weight_domain / weight_grid_total (contour mass for contours) |
| `n_cells_valid` | int |  | no | Layer cells with positive weight and a valid value |
| `layer_asset` | string |  | no | File of the layer used for this day |
| `layer_vintage` | int |  | yes | Declared vintage year (categorical maps) |
| `layer_reference_start` | date |  | no | Start of the asset's reference period |
| `layer_reference_end` | date |  | no | End of the asset's reference period |
| `days_from_layer` | int | d | no | day - reference_end (past, > 0), 0 inside the period, day - reference_start (future, < 0) |
| `temporal_relation` | string |  | no | past, contains_day or future; one of `past`, `contains_day`, `future` |
| `footprint_year` | int |  | no | Calendar year of the day |
| `layer_year` | int |  | no | Calendar year of the asset's reference end |
| `year_mismatch` | int |  | no | footprint_year - layer_year |
| `retrospective` | bool |  | no | The asset's reference period ends after the day, so the row uses later information |
| `contour_level` | float | 1 | yes | Declared contour level of a contour domain |
| `flags` | string |  | yes | ';'-joined flags |

## landscape_index_summaries

Version 1. Support-weighted mean and variability of a continuous vegetation index on its native grid. weighted_std = sqrt(sum c_j (v_j - m)^2 / sum c_j) over valid cells (population form, no small-sample correction); n_eff = (sum c_j)^2 / sum c_j^2 (Kish effective number of cells). Landscape description only: not an ET accuracy guarantee.

Primary key: `representativeness_id`, `site_id`, `date`, `day_set`, `extraction_method`, `footprint_set_id`, `layer_id`, `matching_policy`, `domain`.

| Column | Kind | Unit | Null | Description |
|---|---|---|---|---|
| `representativeness_id` | string |  | key | Declared representativeness analysis |
| `site_id` | string |  | key | Station |
| `date` | date |  | key | Footprint station-day label |
| `day_set` | string |  | key | Footprint set whose usable days this row belongs to (fixed supports are evaluated on those frozen days) |
| `extraction_method` | string |  | key | footprint_conservative, pixel_window_<n>x<n>, nearest_tower_pixel or polygon_<id> (D-029 naming) |
| `footprint_set_id` | string |  | key | Footprint set of the support, 'none' for fixed supports |
| `layer_id` | string |  | key | Declared landscape layer |
| `matching_policy` | string |  | key | Declared policy matching layer dates to the day |
| `domain` | string |  | key | full_domain (footprint grid), contour_<level> (footprint cumulative-contribution contour) or full_support (fixed supports) |
| `index_units` | string |  | no | Declared units of the index |
| `weighted_mean` | float |  | no | sum c_j v_j / sum c_j over valid cells |
| `weighted_std` | float |  | no | Weighted population standard deviation |
| `std_estimator` | string |  | no | Exact estimator of weighted_std |
| `n_eff` | float |  | no | Effective number of contributing cells |
| `weight_denominator` | string |  | no | What weight_grid_total is: e.g. normalized_truncated_footprint (D-008), window_pixels, polygon_area |
| `weight_grid_total` | float | 1 | no | Support total: footprint-grid total, or 1 for fixed supports |
| `weight_domain` | float | 1 | no | Weight inside the domain (= total for full_domain; contour mass for contours) |
| `weight_in_layer_extent` | float | 1 | no | Domain weight on cells of the layer raster window |
| `weight_valid` | float | 1 | no | Domain weight on valid (non-nodata) layer cells |
| `weight_nodata` | float | 1 | no | Domain weight on nodata layer cells inside the extent |
| `valid_frac_of_grid_total` | float | 1 | no | weight_valid / weight_grid_total (separate denominator; never multiplied with domain capture) |
| `nodata_frac_of_grid_total` | float | 1 | no | weight_nodata / weight_grid_total |
| `outside_layer_frac_of_grid_total` | float | 1 | no | (weight_domain - weight_in_layer_extent) / weight_grid_total |
| `domain_frac_of_grid_total` | float | 1 | no | weight_domain / weight_grid_total (contour mass for contours) |
| `n_cells_valid` | int |  | no | Layer cells with positive weight and a valid value |
| `layer_asset` | string |  | no | File of the layer used for this day |
| `layer_vintage` | int |  | yes | Declared vintage year (categorical maps) |
| `layer_reference_start` | date |  | no | Start of the asset's reference period |
| `layer_reference_end` | date |  | no | End of the asset's reference period |
| `days_from_layer` | int | d | no | day - reference_end (past, > 0), 0 inside the period, day - reference_start (future, < 0) |
| `temporal_relation` | string |  | no | past, contains_day or future; one of `past`, `contains_day`, `future` |
| `footprint_year` | int |  | no | Calendar year of the day |
| `layer_year` | int |  | no | Calendar year of the asset's reference end |
| `year_mismatch` | int |  | no | footprint_year - layer_year |
| `retrospective` | bool |  | no | The asset's reference period ends after the day, so the row uses later information |
| `contour_level` | float | 1 | yes | Declared contour level of a contour domain |
| `flags` | string |  | yes | ';'-joined flags |

## landscape_method_differences

Version 1. Footprint minus fixed-support landscape quantities on frozen footprint days: the same day, layer asset and matching policy on both sides. A difference describes what the supports see; it is not an ET error. ET_mean (source_region_mean) is a varying source region (D-007) and never appears as a fixed support.

Primary key: `representativeness_id`, `site_id`, `date`, `footprint_set_id`, `layer_id`, `matching_policy`, `domain`, `compared_method`, `quantity`.

| Column | Kind | Unit | Null | Description |
|---|---|---|---|---|
| `representativeness_id` | string |  | key | Declared representativeness analysis |
| `site_id` | string |  | key | Station |
| `date` | date |  | key | Footprint station-day label |
| `footprint_set_id` | string |  | key | Footprint set (baseline side) |
| `layer_id` | string |  | key | Declared landscape layer |
| `matching_policy` | string |  | key | Declared matching policy |
| `domain` | string |  | key | Footprint domain: full_domain or contour_<level> |
| `compared_method` | string |  | key | Fixed support compared with the footprint |
| `quantity` | string |  | key | class_frac_of_valid:<code>, nodata_frac_of_grid_total, index_mean or index_std |
| `quantity_kind` | string |  | no | Kind of quantity; one of `class_fraction`, `nodata_fraction`, `index_mean`, `index_std` |
| `footprint_value` | float |  | no | Footprint side (0 for a class absent on that side) |
| `method_value` | float |  | no | Fixed-support side |
| `difference` | float |  | no | footprint_value - method_value |
| `units` | string |  | no | Units of the quantity |
| `layer_asset` | string |  | no | Asset used on both sides |
| `retrospective` | bool |  | no | The asset uses information from after the day |
| `flags` | string |  | yes | ';'-joined flags |

## representativeness_labels

Version 1. Qualitative labels kept next to the quantitative fractions: the station's metadata land cover (a site label, not a footprint-weighted fraction) and optional fluxfootprints HIGH/MEDIUM/LOW classes. A label never enters a metric or threshold and is not a quantitative ET accuracy guarantee.

Primary key: `representativeness_id`, `site_id`, `date_label`, `day_set`, `layer_id`, `matching_policy`, `domain`, `label_source`, `radius_m`.

| Column | Kind | Unit | Null | Description |
|---|---|---|---|---|
| `representativeness_id` | string |  | key | Declared representativeness analysis |
| `site_id` | string |  | key | Station |
| `date_label` | string |  | key | ISO day, or 'all' for a station-level label |
| `day_set` | string |  | key | Footprint set, or 'none' |
| `layer_id` | string |  | key | Layer the label describes, or 'station_metadata' |
| `matching_policy` | string |  | key | Matching policy, or 'none' |
| `domain` | string |  | key | Footprint domain, or 'none' |
| `label_source` | string |  | key | station_metadata or fluxfootprints; one of `station_metadata`, `fluxfootprints` |
| `radius_m` | float | m | key | Target-area radius of a fluxfootprints label; 0 otherwise |
| `label` | string |  | no | The label (e.g. IGBP 'CRO', or 'high'/'medium'/'low') |
| `label_meaning` | string |  | no | What the label states and what it does not |
| `dominant_class` | string |  | yes | fluxfootprints dominant footprint class |
| `p_footprint` | float | 1 | yes | fluxfootprints dominant-class footprint share |
| `p_target` | float | 1 | yes | Its share of the target disc |
| `chi2` | float |  | yes | fluxfootprints chi-square statistic |
| `p_value` | float |  | yes | Its p-value (pseudo-counts; see fluxfootprints) |
| `source_version` | string |  | no | Version and commit of the label source |
| `source_parameters` | string |  | yes | Upstream thresholds and parameters (JSON) |
| `layer_asset` | string |  | yes | Layer asset used |
| `flags` | string |  | yes | ';'-joined flags (always label_not_accuracy_guarantee) |

## satellite_support_errors

Version 1. Metrics (compute_metrics) of e = P - O on clear-observation and interpolated days of one frozen cohort, identical across models, plus declared acquisition-timing bins. Support comes only from authoritative product metadata; unknown support is never counted as either subset.

Primary key: `satellite_support_id`, `comparison_id`, `cohort_id`, `site_id`, `model`, `reference_variant`, `extraction_method`, `footprint_set_id`, `variable`, `subset`, `metric`.

| Column | Kind | Unit | Null | Description |
|---|---|---|---|---|
| `satellite_support_id` | string |  | key | Declared satellite-support analysis |
| `comparison_id` | string |  | key | Comparison identifier |
| `cohort_id` | string |  | key | Cohort identifier |
| `site_id` | string |  | key | Station |
| `model` | string |  | key | Canonical model name |
| `reference_variant` | string |  | key | Reference variant; one of `et_raw`, `et_corrected_measured`, `et_corrected_inclusive` |
| `extraction_method` | string |  | key | Extraction method |
| `footprint_set_id` | string |  | key | Footprint set of the extraction (or 'none') |
| `variable` | string |  | key | support_status, or an acquisition-gap variable |
| `subset` | string |  | key | all_cohort_days, clear_observation, interpolated, or a bin label |
| `metric` | string |  | key | Metric (etcompare.metrics) |
| `bin_index` | int |  | yes | Bin position for gap variables; null for support subsets |
| `bin_lower` | float | d | yes | Lower edge (days) |
| `bin_upper` | float | d | yes | Upper edge (days) |
| `value` | float |  | yes | Metric on the subset; null unless status ok |
| `units` | string |  | no | Units of value |
| `status` | string |  | no | ok, or why there is no value; one of `ok`, `sparse_subset`, `empty_subset`, `estimate_undefined`, `outside_edges`, `variable_missing` |
| `status_detail` | string |  | yes | Reason behind a non-ok status |
| `n_days` | int |  | no | Days in the subset (identical for every model) |
| `n_days_cohort` | int |  | no | Cohort days |
| `n_days_mixed_support` | int |  | no | Cohort days whose support differs across models (excluded from both subsets) |
| `n_days_unknown_support` | int |  | no | Cohort days with no support record for some model |
| `n_days_model_clear` | int |  | no | This model's clear days in the cohort |
| `n_days_model_interpolated` | int |  | no | This model's interpolated days |
| `n_days_model_unknown` | int |  | no | This model's days without a support record |
| `min_subset_days` | int |  | no | Declared minimum subset size |
| `support_sources` | string |  | no | Support source IDs and authorities used |
| `retrospective` | bool |  | no | The variable uses acquisitions after the day |
| `flags` | string |  | yes | ';'-joined flags |

## representativeness_availability

Version 1. Which landscape and satellite-support analyses ran, per day where relevant, and which were not available, with reasons. A day without an admissible layer asset is not_available, never filled.

Primary key: `analysis_id`, `site_id`, `analysis`, `layer_id`, `extraction_method`, `day_set`, `matching_policy`, `domain`, `date_label`.

| Column | Kind | Unit | Null | Description |
|---|---|---|---|---|
| `analysis_id` | string |  | key | Declared representativeness or satellite-support ID |
| `site_id` | string |  | key | Station |
| `analysis` | string |  | key | landscape_class_fractions, landscape_index_summaries, method_difference, fluxfootprints_labels, satellite_support, ... |
| `layer_id` | string |  | key | Layer, or 'none' |
| `extraction_method` | string |  | key | Support, or 'all' |
| `day_set` | string |  | key | Footprint set or comparison, or 'all' |
| `matching_policy` | string |  | key | Matching policy, or 'all' |
| `domain` | string |  | key | Domain, or 'all' |
| `date_label` | string |  | key | ISO day, or 'all' |
| `status` | string |  | no | available or not_available; one of `available`, `not_available` |
| `reason_code` | string |  | yes | Stable reason when not_available |
| `reason` | string |  | yes | Explanation (also for labelled available analyses) |
| `n_days` | int |  | yes | Days concerned |
| `flags` | string |  | yes | ';'-joined flags |

## station_events

Version 1. Events of a declared station event log, one row per event inside the station record. Events come only from the log, never from model residuals or agreement. Source columns pass through with prefix 'src_'.

Primary key: `site_id`, `event_id`.

Extra columns are allowed and kept.

| Column | Kind | Unit | Null | Description |
|---|---|---|---|---|
| `site_id` | string |  | key | Station (from the declared site mapping) |
| `event_id` | string |  | key | Source event ID, preserved as text |
| `event_table` | string |  | no | Declared event table name |
| `source_station_id` | string |  | no | Station ID as written in the log |
| `start_date` | date |  | no | Station day of the event timestamp (declared day rule) |
| `end_date` | date |  | no | Last station day; equals start_date without an end column |
| `start_time` | string |  | yes | Time of day as logged (HH:MM:SS), null for date-only entries (logged at exactly 00:00) |
| `event_type` | string |  | no | Source event_type, preserved exactly |
| `category` | string |  | no | Declared category of the event type; one of `management`, `instrument`, `confounder` |
| `management_kind` | string |  | yes | Declared kind for management events; null otherwise; one of `cutting`, `irrigation`, `irrigation_cutoff`, `planting`, `other` |
| `analysis_role` | string |  | no | management_event, or overlap_only (instrument and confounder events: never a treatment); one of `management_event`, `overlap_only` |
| `event_source` | string |  | yes | Source event_source, preserved exactly (null when unlabelled) |
| `evidence_class` | string |  | no | Declared evidence class of event_source (unknown when unlabelled); one of `management_record`, `photo_observation`, `remote_sensing_inference`, `station_log`, `unknown` |
| `observer` | string |  | yes | Observer as logged |
| `event_notes` | string |  | yes | Notes as logged |
| `approximate` | bool |  | no | A declared approximate-date pattern matches the notes |
| `approximate_rule` | string |  | yes | The pattern that matched, if any |
| `management_record` | bool |  | no | A declared management record confirms the event (event_source and evidence_class stay as logged) |
| `management_record_source` | string |  | yes | The confirming record, who confirmed it and when |

## event_responses

Version 1. Before/after response of each cohort series to each management event: baseline, change, cumulative response and recovery for the observed and modeled series and their difference. An uncontrolled before/after difference is not causal evidence.

Primary key: `event_analysis_id`, `comparison_id`, `cohort_id`, `site_id`, `event_id`, `model`, `reference_variant`, `extraction_method`, `footprint_set_id`, `series`.

| Column | Kind | Unit | Null | Description |
|---|---|---|---|---|
| `event_analysis_id` | string |  | key | Declared event analysis |
| `comparison_id` | string |  | key | Comparison |
| `cohort_id` | string |  | key | Cohort |
| `site_id` | string |  | key | Station |
| `event_id` | string |  | key | Event (from the station event log) |
| `model` | string |  | key | Model |
| `reference_variant` | string |  | key | Reference variant; one of `et_raw`, `et_corrected_measured`, `et_corrected_inclusive` |
| `extraction_method` | string |  | key | Extraction method |
| `footprint_set_id` | string |  | key | Footprint set, or 'none' |
| `series` | string |  | key | observed, modeled or modeled_minus_observed; one of `observed`, `modeled`, `modeled_minus_observed` |
| `event_start_date` | date |  | no | Event station day |
| `event_end_date` | date |  | no | Last event station day |
| `management_kind` | string |  | no | Management kind |
| `evidence_class` | string |  | no | Evidence class of the logged source |
| `management_record` | bool |  | no | Confirmed by a management record |
| `approximate` | bool |  | no | The log calls the date approximate |
| `design` | string |  | no | Comparison design; one of `before_after_uncontrolled` |
| `pre_start` | date |  | no | First baseline day |
| `pre_end` | date |  | no | Last baseline day |
| `post_start` | date |  | no | First post day |
| `post_end` | date |  | no | Last post day |
| `n_pre_days_expected` | int |  | no | Calendar days of the baseline window |
| `n_pre_days` | int |  | no | Cohort days in the baseline window |
| `n_post_days_expected` | int |  | no | Calendar days of the post window |
| `n_post_days` | int |  | no | Cohort days in the post window |
| `n_pre_days_reference_filled` | int |  | no | Gap-filled reference days in the baseline |
| `n_post_days_reference_filled` | int |  | no | Gap-filled reference days after the event |
| `baseline_mean_mm` | float | mm d-1 | yes | Mean daily ET of the baseline window |
| `post_mean_mm` | float | mm d-1 | yes | Mean daily ET of the post window |
| `change_mm` | float | mm d-1 | yes | post_mean - baseline_mean |
| `relative_change_percent` | float | % | yes | 100 * change / baseline |
| `cumulative_response_mm` | float | mm | yes | Sum over the post window of (ET - baseline); only with every post day present |
| `baseline_fraction` | float | 1 | yes | Baseline mean of ET / normalising variable |
| `post_fraction` | float | 1 | yes | Post-window mean of ET / normalising variable |
| `fraction_change` | float | 1 | yes | post_fraction - baseline_fraction |
| `recovery_days` | int | d | yes | Days after the event until the smoothed ET reaches recovery_fraction of the baseline (difference rows: modeled - observed) |
| `recovery_status` | string |  | no | Recovery outcome; one of `recovered`, `not_recovered_within_horizon`, `not_identifiable_gap`, `interrupted_by_management_event`, `baseline_unavailable`, `baseline_below_min`, `not_applicable` |
| `status` | string |  | no | ok, or why the change is not computed; one of `ok`, `incomplete_baseline`, `incomplete_post_window`, `incomplete_baseline_and_post_window` |
| `status_detail` | string |  | yes | Further detail |
| `overlapping_event_ids` | string |  | yes | ';'-joined IDs of other events in the windows |
| `flags` | string |  | yes | ';'-joined flags |

## event_overlaps

Version 1. Every other logged event (management, instrument or confounder) inside a management event's windows. Kept visible; instrument and confounder events are never treatments.

Primary key: `event_analysis_id`, `site_id`, `event_id`, `other_event_id`.

| Column | Kind | Unit | Null | Description |
|---|---|---|---|---|
| `event_analysis_id` | string |  | key | Declared event analysis |
| `site_id` | string |  | key | Station |
| `event_id` | string |  | key | Analysed management event |
| `other_event_id` | string |  | key | Overlapping event |
| `other_event_type` | string |  | no | Logged type |
| `other_category` | string |  | no | Declared category; one of `management`, `instrument`, `confounder` |
| `other_analysis_role` | string |  | no | management_event or overlap_only; one of `management_event`, `overlap_only` |
| `other_start_date` | date |  | no | Overlapping event's station day |
| `offset_days` | int | d | no | Other start minus this event's start |
| `window` | string |  | no | pre, event, post or recovery (where the other event starts); one of `pre`, `event`, `post`, `recovery` |
| `other_event_notes` | string |  | yes | Notes as logged |

## event_conditions

Version 1. Declared weather variables summarised over each event's windows on the station record, so weather confounding stays visible next to the responses.

Primary key: `event_analysis_id`, `site_id`, `event_id`, `variable`, `window`.

| Column | Kind | Unit | Null | Description |
|---|---|---|---|---|
| `event_analysis_id` | string |  | key | Declared event analysis |
| `site_id` | string |  | key | Station |
| `event_id` | string |  | key | Management event |
| `variable` | string |  | key | Declared weather variable |
| `window` | string |  | key | pre or post; one of `pre`, `post` |
| `column` | string |  | no | station_daily column |
| `aggregation` | string |  | no | mean or sum |
| `value` | float |  | yes | Window summary (null when no day has a value) |
| `units` | string |  | no | Declared units (sums: per window) |
| `n_days` | int |  | no | Days with a value |
| `n_days_expected` | int |  | no | Calendar days of the window |

## anomaly_daily

Version 1. Daily anomalies of a cohort: each value minus its baseline (held-out climatology or exploratory moving mean), fitted separately to the observed and modeled series.

Primary key: `anomaly_id`, `comparison_id`, `cohort_id`, `site_id`, `date`, `model`, `reference_variant`, `extraction_method`, `footprint_set_id`.

| Column | Kind | Unit | Null | Description |
|---|---|---|---|---|
| `anomaly_id` | string |  | key | Declared anomaly analysis |
| `comparison_id` | string |  | key | Comparison |
| `cohort_id` | string |  | key | Cohort |
| `site_id` | string |  | key | Station |
| `date` | date |  | key | Station-day label |
| `model` | string |  | key | Model |
| `reference_variant` | string |  | key | Reference variant; one of `et_raw`, `et_corrected_measured`, `et_corrected_inclusive` |
| `extraction_method` | string |  | key | Extraction method |
| `footprint_set_id` | string |  | key | Footprint set, or 'none' |
| `method` | string |  | no | Anomaly method |
| `fold` | string |  | no | Held-out fold label, or 'in_sample' |
| `observed_mm` | float | mm d-1 | yes | Observed ET |
| `modeled_mm` | float | mm d-1 | yes | Modeled ET |
| `observed_baseline_mm` | float | mm d-1 | yes | Baseline fitted to the observed series |
| `modeled_baseline_mm` | float | mm d-1 | yes | Baseline fitted to the modeled series |
| `observed_anomaly_mm` | float | mm d-1 | yes | observed - observed baseline |
| `modeled_anomaly_mm` | float | mm d-1 | yes | modeled - modeled baseline |
| `status` | string |  | no | ok or baseline_unavailable; one of `ok`, `baseline_unavailable` |
| `n_training_values` | int |  | yes | Values behind the baseline |
| `n_training_years` | int |  | yes | Distinct training folds behind the climatology |
| `reference_provenance` | string |  | yes | Reference provenance; one of `corrected_from_raw`, `corrected_filled`, `reference_missing`, `inconsistent_flags` |

## anomaly_metrics

Version 1. Metrics of raw values and of anomalies on the same days, per fold and pooled. Raw bias is reported next to anomaly skill because demeaning removes part of the bias.

Primary key: `anomaly_id`, `comparison_id`, `cohort_id`, `site_id`, `model`, `reference_variant`, `extraction_method`, `footprint_set_id`, `fold`, `series_kind`, `metric`.

| Column | Kind | Unit | Null | Description |
|---|---|---|---|---|
| `anomaly_id` | string |  | key | Declared anomaly analysis |
| `comparison_id` | string |  | key | Comparison |
| `cohort_id` | string |  | key | Cohort |
| `site_id` | string |  | key | Station |
| `model` | string |  | key | Model |
| `reference_variant` | string |  | key | Reference variant; one of `et_raw`, `et_corrected_measured`, `et_corrected_inclusive` |
| `extraction_method` | string |  | key | Extraction method |
| `footprint_set_id` | string |  | key | Footprint set, or 'none' |
| `fold` | string |  | key | Held-out fold, 'all_folds' (pooled held-out days) or 'in_sample' |
| `series_kind` | string |  | key | raw or anomaly; one of `raw`, `anomaly` |
| `metric` | string |  | key | Metric ID |
| `method` | string |  | no | Anomaly method |
| `inference` | string |  | no | held_out or exploratory; one of `held_out`, `exploratory` |
| `value` | float |  | yes | Metric value; null with a status when undefined |
| `units` | string |  | no | Units |
| `status` | string |  | no | Status; one of `ok`, `sparse_sample`, `empty_sample`, `estimate_undefined`, `insufficient_training_years` |
| `status_detail` | string |  | yes | Further detail |
| `n_days` | int |  | no | Days in the sample |
| `n_days_cohort` | int |  | no | Cohort days of the series |
| `n_days_reference_filled` | int |  | no | Gap-filled reference days in the sample |
| `training_folds` | string |  | yes | ';'-joined training folds (held-out rows) |
| `flags` | string |  | yes | ';'-joined flags |

## water_balance_terms

Version 1. Period totals of each declared water-balance term. A total exists only when every calendar day (or both storage states) of the period has a value; otherwise the partial sum is reported with its day count and is never a full-period total.

Primary key: `water_balance_id`, `site_id`, `period_type`, `period_label`, `term`.

| Column | Kind | Unit | Null | Description |
|---|---|---|---|---|
| `water_balance_id` | string |  | key | Declared water balance |
| `site_id` | string |  | key | Station |
| `period_type` | string |  | key | Period type of the step-8 aggregation; one of `day`, `month`, `growing_season`, `year`, `water_year` |
| `period_label` | string |  | key | Period label (e.g. 2025-07, WY2025, GS2025) |
| `term` | string |  | key | Water-balance term; one of `precipitation`, `irrigation`, `capillary_rise`, `et`, `runoff`, `deep_percolation`, `storage_change` |
| `period_start` | date |  | no | First calendar day of the period |
| `period_end` | date |  | no | Last calendar day of the period |
| `boundary_id` | string |  | no | Declared boundary every term must cover |
| `status` | string |  | no | measured, modeled, scenario or unknown; one of `measured`, `modeled`, `scenario`, `unknown` |
| `source_kind` | string |  | no | Where the values come from; one of `station_column`, `station_et`, `table`, `scenario`, `none` |
| `source` | string |  | yes | Column, table, reference variant or scenario value |
| `quantity` | string |  | no | flux (summed) or storage_state (differenced); one of `flux`, `storage_state` |
| `spatial_support` | string |  | yes | Declared spatial support of the values; one of `boundary`, `station_point`, `tower_footprint`, `gridded_cell`, `model_pixel`, `regional` |
| `support_matches_boundary` | bool |  | yes | The term covers exactly the boundary (null for an unknown term) |
| `day_definition` | string |  | yes | Declared day definition of the term's values |
| `sign` | int |  | no | +1 inflow, -1 outflow or storage increase |
| `n_days_expected` | int |  | no | Calendar days of the period |
| `n_values_required` | int |  | yes | Values a complete total needs: every day (flux) or 2 states (storage) |
| `n_values_present` | int |  | yes | Values present among those required |
| `period_completeness` | float | 1 | yes | n_values_present / n_values_required |
| `partial_sum_mm` | float | mm | yes | Sum over the n_values_present days only (flux); never a full-period total |
| `total_mm` | float | mm | yes | Full-period total; null unless total_status is complete |
| `signed_total_mm` | float | mm | yes | sign x total_mm |
| `total_status` | string |  | no | complete, incomplete_period or unknown_term; one of `complete`, `incomplete_period`, `unknown_term` |
| `total_detail` | string |  | yes | Further detail |
| `volume_m3` | float | m3 | yes | total_mm x boundary area (known area and matching support) |
| `volume_status` | string |  | no | Why a volume is or is not given; one of `computed`, `not_requested`, `area_unknown`, `support_mismatch`, `no_value` |
| `reference_variant` | string |  | yes | Station ET reference variant (ET term); one of `et_raw`, `et_corrected_measured`, `et_corrected_inclusive` |
| `n_days_et_gap_filled` | int |  | yes | Gap-filled station ET days among the present days |
| `flags` | string |  | yes | ';'-joined flags |

## water_balance_residuals

Version 1. Water-balance residuals per period: a plausibility diagnostic holding every unmeasured exchange and the error of every term; never ET error and never a basis for choosing a model, reference, filter or footprint. Residuals from measured/modeled terms and scenario residuals are separate rows.

Primary key: `water_balance_id`, `site_id`, `period_type`, `period_label`, `residual_kind`.

| Column | Kind | Unit | Null | Description |
|---|---|---|---|---|
| `water_balance_id` | string |  | key | Declared water balance |
| `site_id` | string |  | key | Station |
| `period_type` | string |  | key | Period type of the step-8 aggregation; one of `day`, `month`, `growing_season`, `year`, `water_year` |
| `period_label` | string |  | key | Period label (e.g. 2025-07, WY2025, GS2025) |
| `residual_kind` | string |  | key | measured_modeled or scenario; one of `measured_modeled`, `scenario` |
| `period_start` | date |  | no | First calendar day of the period |
| `period_end` | date |  | no | Last calendar day of the period |
| `boundary_id` | string |  | no | Declared boundary every term must cover |
| `equation` | string |  | no | Declared equation and sign convention |
| `status` | string |  | no | computed, unknown or not_applicable; one of `computed`, `unknown`, `not_applicable` |
| `residual_mm` | float | mm | yes | Residual depth; null unless computed |
| `residual_volume_m3` | float | m3 | yes | Residual volume over the boundary area |
| `volume_status` | string |  | no | Why a volume is or is not given; one of `computed`, `not_requested`, `area_unknown`, `support_mismatch`, `no_value` |
| `reason` | string |  | yes | Why the residual is unknown or not applicable |
| `unknown_terms` | string |  | yes | ';'-joined terms with no input |
| `incomplete_terms` | string |  | yes | ';'-joined terms without a complete period total |
| `scenario_terms` | string |  | yes | ';'-joined scenario terms |
| `support_mismatch_terms` | string |  | yes | ';'-joined terms whose spatial support differs from the boundary |
| `n_days_expected` | int |  | no | Calendar days of the period |
| `et_reference_variant` | string |  | yes | Station ET reference variant; one of `et_raw`, `et_corrected_measured`, `et_corrected_inclusive` |
| `n_days_et_gap_filled` | int |  | yes | Gap-filled station ET days in the period |
| `interpretation` | string |  | no | How the residual may be read |
| `flags` | string |  | yes | ';'-joined flags |

## scenario_results

Version 1. Declared scenario uncertainty of a water-balance residual: one uncertainty category per scenario (measurement, footprint_parameter or method_choice), never merged and never a bootstrap interval (sampling uncertainty is the step-10 intervals table).

Primary key: `scenario_id`, `site_id`, `period_type`, `period_label`, `statistic`.

| Column | Kind | Unit | Null | Description |
|---|---|---|---|---|
| `scenario_id` | string |  | key | Declared scenario |
| `site_id` | string |  | key | Station |
| `period_type` | string |  | key | Period type; one of `day`, `month`, `growing_season`, `year`, `water_year` |
| `period_label` | string |  | key | Period label |
| `statistic` | string |  | key | base, mean, sd, q<level>, min, median, max, alternative=<name> or realization=<name> |
| `water_balance_id` | string |  | no | Water balance |
| `uncertainty_category` | string |  | no | Uncertainty category of the scenario; one of `measurement`, `footprint_parameter`, `method_choice` |
| `uncertainty_label` | string |  | no | Human-readable category label |
| `period_start` | date |  | no | First calendar day of the period |
| `period_end` | date |  | no | Last calendar day of the period |
| `residual_kind` | string |  | no | Kind of the base residual; one of `measured_modeled`, `scenario` |
| `value` | float | mm | yes | Residual statistic; null unless computed |
| `status` | string |  | no | computed or unknown; one of `computed`, `unknown` |
| `status_detail` | string |  | yes | Further detail (e.g. which terms are unknown) |
| `n_draws` | int |  | no | Draws (measurement) or alternatives incl. the declared one |
| `n_valid_draws` | int |  | yes | Draws or alternatives with a computed residual |
| `seed` | int |  | yes | Declared seed (measurement scenarios) |
| `scenario_sha256` | string |  | no | SHA-256 of the scenario declaration |
| `interpretation` | string |  | no | How the value may be read |
| `flags` | string |  | yes | ';'-joined flags |

## ensemble_days

Version 1. Member availability, eligibility under the declared missing-member policy, and member disagreement for every candidate day of a post-extraction ensemble experiment. Member spread is disagreement between models, never a confidence interval.

Primary key: `ensemble_id`, `site_id`, `date`.

| Column | Kind | Unit | Null | Description |
|---|---|---|---|---|
| `ensemble_id` | string |  | key | Declared ensemble experiment |
| `site_id` | string |  | key | Station |
| `date` | date |  | key | Station-day label |
| `observed_mm` | float | mm d-1 | yes | Station reference ET of the declared variant |
| `reference_provenance` | string |  | yes | Reference provenance (null: no reference); one of `corrected_from_raw`, `corrected_filled`, `reference_missing`, `inconsistent_flags` |
| `n_members_declared` | int |  | no | Declared members |
| `n_members_available` | int |  | no | Members with an extracted value |
| `members_available` | string |  | yes | ';'-joined members with a value |
| `members_missing` | string |  | yes | ';'-joined members without a value |
| `official_available` | bool |  | yes | The official ensemble has a value (null: none declared) |
| `eligible` | bool |  | no | Usable under the missing-member policy, with a reference |
| `eligibility_reason` | string |  | yes | Why the day is not eligible |
| `reweighted` | bool |  | no | Combinations use only the available members (policy reweight_available) |
| `member_range_mm` | float | mm d-1 | yes | max - min of the available members: disagreement, not a confidence interval |
| `member_sd_mm` | float | mm d-1 | yes | Population SD of the available members: disagreement, not a confidence interval |
| `flags` | string |  | yes | ';'-joined flags |

## ensemble_predictions

Version 1. Daily values of each compared product. 'baseline' rows (official, mean, median) have no fitted parameters; fitted rows are held-out predictions from weights fitted on other folds only.

Primary key: `ensemble_id`, `scope`, `site_id`, `date`, `product`.

| Column | Kind | Unit | Null | Description |
|---|---|---|---|---|
| `ensemble_id` | string |  | key | Declared ensemble experiment |
| `scope` | string |  | key | baseline, or the fold design of the fitted product; one of `baseline`, `leave_one_year_out`, `leave_one_site_out` |
| `site_id` | string |  | key | Station |
| `date` | date |  | key | Station-day label |
| `product` | string |  | key | Product ID (the official model, member_mean, member_median or fitted_weighted) |
| `product_kind` | string |  | no | official, mean, median or fitted; one of `official`, `mean`, `median`, `fitted` |
| `value_mm` | float | mm d-1 | no | Product ET |
| `observed_mm` | float | mm d-1 | yes | Station reference ET |
| `reference_provenance` | string |  | yes | Reference provenance; one of `corrected_from_raw`, `corrected_filled`, `reference_missing`, `inconsistent_flags` |
| `fold` | string |  | yes | Held-out fold of a fitted prediction |
| `inference` | string |  | no | no_fitted_parameters or held_out; one of `no_fitted_parameters`, `held_out` |
| `n_members_used` | int |  | yes | Members combined (null for the official product) |
| `reweighted` | bool |  | no | Weights renormalised over the available members |
| `weights_used` | string |  | yes | ';'-joined member=weight actually applied (fitted) |
| `flags` | string |  | yes | ';'-joined flags |

## ensemble_folds

Version 1. One row per held-out fold: training membership counts, buffer, member availability, preprocessing and the regularisation chosen by inner grouped validation on training folds only, or why the fold was declined.

Primary key: `ensemble_id`, `design`, `fold`.

| Column | Kind | Unit | Null | Description |
|---|---|---|---|---|
| `ensemble_id` | string |  | key | Declared ensemble experiment |
| `design` | string |  | key | Fold design; one of `leave_one_year_out`, `leave_one_site_out` |
| `fold` | string |  | key | Held-out fold (year, water year or site); 'in_sample' for an exploratory fit |
| `status` | string |  | no | fitted, declined or exploratory_in_sample; one of `fitted`, `declined`, `exploratory_in_sample` |
| `status_detail` | string |  | yes | Why the fold was declined |
| `held_out_start` | date |  | yes | First calendar day of a temporal fold |
| `held_out_end` | date |  | yes | Last calendar day of a temporal fold |
| `training_folds` | string |  | yes | ';'-joined training folds used |
| `n_training_folds` | int |  | no | Training folds used |
| `n_training_days` | int |  | no | Training site-days |
| `n_training_days_gap_filled` | int |  | no | Gap-filled reference training site-days |
| `n_buffer_days_removed` | int |  | no | Candidate training site-days removed by the buffer |
| `n_small_fold_days_excluded` | int |  | no | Site-days of training folds below min_training_days_per_fold |
| `n_test_days` | int |  | no | Held-out site-days |
| `n_test_days_reweighted` | int |  | no | Held-out site-days with reweighted members |
| `n_inner_folds` | int |  | no | Inner validation folds |
| `alpha_selected` | float | 1 | yes | Shrinkage toward equal weights chosen by inner validation (1 = simple mean) |
| `alpha_grid` | string |  | no | ';'-joined candidate alphas |
| `selection_metric` | string |  | yes | Inner validation criterion |
| `method` | string |  | yes | Weight estimator |
| `regularisation` | string |  | yes | Regularisation toward equal weights |
| `preprocessing` | string |  | yes | Feature preprocessing fitted on training rows |
| `buffer_days` | int |  | yes | Declared buffer (days each side of a held-out fold) |
| `member_availability` | string |  | no | ';'-joined member=days with a value among the fold's candidate days |
| `flags` | string |  | yes | ';'-joined flags |

## ensemble_weights

Version 1. Member weights of each fitted fold (nonnegative, summing to one), before and after shrinkage toward equal weights; exploratory in-sample weights are labelled so and have no selected weight.

Primary key: `ensemble_id`, `design`, `fold`, `member`.

| Column | Kind | Unit | Null | Description |
|---|---|---|---|---|
| `ensemble_id` | string |  | key | Declared ensemble experiment |
| `design` | string |  | key | Fold design; one of `leave_one_year_out`, `leave_one_site_out` |
| `fold` | string |  | key | Held-out fold, or 'in_sample' |
| `member` | string |  | key | Member product |
| `weight_unregularised` | float | 1 | no | Simplex least-squares weight on training rows |
| `alpha` | float | 1 | yes | Selected shrinkage |
| `weight` | float | 1 | yes | (1 - alpha) * weight_unregularised + alpha / K |
| `inference` | string |  | no | held_out_fold_weights or exploratory_in_sample; one of `held_out_fold_weights`, `exploratory_in_sample` |
| `n_training_days` | int |  | no | Training site-days |
| `flags` | string |  | yes | ';'-joined flags |

## ensemble_inner_scores

Version 1. Inner grouped leave-one-fold-out validation of each shrinkage candidate, on training folds only (held-out rows never enter).

Primary key: `ensemble_id`, `design`, `fold`, `alpha`.

| Column | Kind | Unit | Null | Description |
|---|---|---|---|---|
| `ensemble_id` | string |  | key | Declared ensemble experiment |
| `design` | string |  | key | Fold design; one of `leave_one_year_out`, `leave_one_site_out` |
| `fold` | string |  | key | Outer held-out fold |
| `alpha` | float | 1 | key | Shrinkage candidate |
| `selection_metric` | string |  | no | Criterion |
| `value` | float |  | yes | Pooled criterion over all inner held-out days |
| `units` | string |  | no | Units of value |
| `n_days` | int |  | no | Inner held-out site-days |
| `n_inner_folds` | int |  | no | Inner folds |
| `status` | string |  | no | ok or estimate_undefined; one of `ok`, `estimate_undefined` |
| `selected` | bool |  | no | The chosen candidate (ties go to the larger alpha) |

## ensemble_training_membership

Version 1. Role of every candidate site-day in every fold: training, held out, or excluded with the reason.

Primary key: `ensemble_id`, `design`, `fold`, `site_id`, `date`.

| Column | Kind | Unit | Null | Description |
|---|---|---|---|---|
| `ensemble_id` | string |  | key | Declared ensemble experiment |
| `design` | string |  | key | Fold design; one of `leave_one_year_out`, `leave_one_site_out` |
| `fold` | string |  | key | Held-out fold |
| `site_id` | string |  | key | Station |
| `date` | date |  | key | Station-day label |
| `group` | string |  | no | Fold group of the day |
| `role` | string |  | no | Role in this fold; one of `training`, `held_out`, `buffer_excluded`, `training_fold_too_small`, `members_incomplete`, `gap_filled_reference`, `not_eligible` |
| `in_training` | bool |  | no | Row used to fit this fold's weights |

## ensemble_summary

Version 1. Status of each ensemble scope: the no-fit baseline comparison and each fold design (held_out, declined, exploratory or not_available, with the reason). Every ensemble here is built after spatial extraction, not at pixel level; one station is not network evidence.

Primary key: `ensemble_id`, `scope`, `subset`.

| Column | Kind | Unit | Null | Description |
|---|---|---|---|---|
| `ensemble_id` | string |  | key | Declared ensemble experiment |
| `scope` | string |  | key | baseline or a fold design; one of `baseline`, `leave_one_year_out`, `leave_one_site_out` |
| `subset` | string |  | key | 'all' days of the scope, or a declared month subset |
| `status` | string |  | no | Status of the scope; one of `evaluated`, `held_out`, `declined`, `exploratory`, `not_available` |
| `status_detail` | string |  | yes | Reason behind the status |
| `ensemble_level` | string |  | no | Where the ensemble is built; one of `post_extraction` |
| `products` | string |  | yes | ';'-joined compared products |
| `members` | string |  | no | ';'-joined members |
| `missing_member_policy` | string |  | no | Declared missing-member policy |
| `reference_variant` | string |  | no | Reference variant; one of `et_raw`, `et_corrected_measured`, `et_corrected_inclusive` |
| `extraction_method` | string |  | no | Extraction method of the members |
| `footprint_set_id` | string |  | no | Footprint set of the members |
| `n_sites` | int |  | no | Stations with eligible days |
| `n_folds` | int |  | yes | Folds with eligible days |
| `n_folds_fitted` | int |  | yes | Folds with fitted held-out weights |
| `n_days_compared` | int |  | yes | Site-days of the frozen comparison cohort |
| `comparison_id` | string |  | yes | Comparison of the frozen product cohort |
| `cohort_membership_sha256` | string |  | yes | Fingerprint of that cohort |
| `interpretation` | string |  | no | How the scope may be read |
| `flags` | string |  | yes | ';'-joined flags |

## ensemble_improvement

Version 1. Held-out contrasts of the fitted ensemble against each unfitted product, taken from the step-11 contrasts on the frozen cohort. An improvement is 'supported' only when the interval excludes zero in the fitted ensemble's favour on held-out days.

Primary key: `ensemble_id`, `scope`, `subset`, `site_id`, `metric`, `baseline_product`, `block_unit`, `block_length`, `interval_method`.

| Column | Kind | Unit | Null | Description |
|---|---|---|---|---|
| `ensemble_id` | string |  | key | Declared ensemble experiment |
| `scope` | string |  | key | Fold design; one of `leave_one_year_out`, `leave_one_site_out` |
| `subset` | string |  | key | 'all' held-out days, or a declared month subset |
| `site_id` | string |  | key | Station (contrasts are station level) |
| `metric` | string |  | key | Contrasted metric |
| `baseline_product` | string |  | key | Product the fitted ensemble is compared with |
| `block_unit` | string |  | key | Resampling unit; one of `site_day`, `period` |
| `block_length` | int |  | key | Bootstrap block length; 0 = not resampled |
| `interval_method` | string |  | key | Interval method; one of `percentile`, `basic` |
| `contrast_basis` | string |  | no | value or absolute_value; one of `value`, `absolute_value` |
| `estimate` | float |  | yes | metric(fitted) - metric(baseline) on held-out days |
| `lower` | float |  | yes | Lower bound |
| `upper` | float |  | yes | Upper bound |
| `units` | string |  | no | Units |
| `sign_meaning` | string |  | no | How to read the sign |
| `status` | string |  | no | Interval status; one of `ok`, `insufficient_support`, `estimate_undefined`, `insufficient_valid_replicates`, `not_resampled` |
| `favoured_model` | string |  | yes | Product favoured by an interval excluding zero |
| `held_out_improvement` | string |  | no | supported, not_supported or not_assessed; one of `supported`, `not_supported`, `not_assessed` |
| `n_days` | int |  | no | Held-out site-days compared |
| `flags` | string |  | yes | ';'-joined flags |

## analysis_status

Version 1. One row per workflow unit: what ran, what was reused, skipped or unavailable, and why.

Primary key: `stage`, `unit_id`.

| Column | Kind | Unit | Null | Description |
|---|---|---|---|---|
| `stage` | string |  | key | Workflow stage |
| `unit_id` | string |  | key | Unit identifier (analysis:ids) |
| `analysis` | string |  | no | Analysis kind |
| `status` | string |  | no | Unit status; one of `completed`, `cached`, `skipped`, `not_available`, `failed`, `not_run`, `stale` |
| `reason_code` | string |  | yes | Stable reason identifier |
| `reason` | string |  | yes | Human-readable reason |
| `cache_key` | string |  | yes | SHA-256 of everything the outputs depend on |
| `run_id` | string |  | yes | Run that produced the outputs |
| `finished_utc` | string |  | yes | When the unit finished (ISO 8601, UTC) |
| `elapsed_s` | float | s | yes | Wall time |
| `n_outputs` | int |  | yes | Output files |

## skipped_analyses

Version 1. Every analysis, or part of one, that produced no result, with its reason.

| Column | Kind | Unit | Null | Description |
|---|---|---|---|---|
| `source` | string |  | no | workflow unit or analysis table; one of `workflow`, `table` |
| `stage` | string |  | yes | Workflow stage |
| `unit_id` | string |  | yes | Workflow unit |
| `analysis` | string |  | no | Analysis |
| `detail` | string |  | yes | Part of the analysis (variable, layer, design, ...) |
| `status` | string |  | no | Status as written by the analysis |
| `reason_code` | string |  | yes | Stable reason identifier |
| `reason` | string |  | yes | Reason |
| `table` | string |  | yes | Table the row was read from |
| `n_rows` | int |  | yes | Rows of that table with this status and reason |

## exclusions

Version 1. Rows removed or flagged, with stage and reason.

| Column | Kind | Unit | Null | Description |
|---|---|---|---|---|
| `stage` | string |  | no | Processing stage |
| `input_key` | string |  | no | Key of the affected input row or file |
| `reason_code` | string |  | no | Stable reason identifier |
| `severity` | string |  | no | Severity; one of `error`, `warning`, `info` |
| `affected_comparison` | string |  | yes | Comparison affected, if specific |
| `detail` | string |  | yes | Human-readable detail |
