# Method guide

This guide states what each analysis computes, what it does not, and which choices are
declared rather than built in. The formulas live in the module documentation
({doc}`api`). Decisions and their evidence are in {doc}`decisions` (D-numbers).

The comparison design draws on published OpenET evaluations (see {doc}`references`). Several
parts are this package's own extensions, not reproductions of those studies, and each is
marked as such below:

- strict frozen cohorts;
- segment-truncated block resampling;
- rank-stability statistics;
- the post-extraction ensemble.

## Principles

- **Declared, never inferred.** The following are declarations, and missing ones are
  refused before any join:
  - units;
  - the station-day definition;
  - the OpenET day definition;
  - raster scaling and nodata;
  - footprint value semantics;
  - temporal weighting;
  - reference variants.

  Unverified items are labelled (`day_alignment=unverified`, `status: provisional`).
- **Missing is not zero.** Missing days and values stay missing. They are counted, and they
  never leave a denominator.
- **Identical days for compared things.** Models, extraction methods and reference
  variants are compared on the same frozen days.
- **No tuning on the evaluation data.** Quality thresholds, bins, block lengths, margins
  and ensemble designs are fixed in the configuration before scoring. Nothing is selected
  because it improves agreement.
- **Station level.** Every interval and ranking describes one station's record. A summary
  across sites with one site is labelled `single_site`.

## 1. Inputs and adapters

- **Station tables.** Daily flux-data-qaqc exports are read through a declared column
  mapping. Every source column is kept as `src_<name>` (D-020). The reference provenance
  of each day is derived from the combination of values and flags (D-004):
  - `corrected_from_raw`: raw ET with its closure correction;
  - `corrected_filled`: a gap-filled corrected value;
  - `reference_missing`;
  - `inconsistent_flags`.

  The reference variants are `et_raw`, `et_corrected_measured` (raw-based days only) and
  `et_corrected_inclusive` (gap-filled days too). `ET_fill` is never a reference.
- **Gap-filled reference days** are built from gridMET reference ET. OpenET's daily
  interpolation shares those drivers, so agreement on those days is not independent
  evidence (D-005). Every analysis keeps them identifiable.
- **Pre-extracted tables** are reshaped so that each value column becomes its own
  extraction method. The labels stay neutral: `source_region_mean` for an unweighted
  regional mean whose geometry is unverified (D-007). Already footprint-weighted values
  are never weighted again (D-006).
- **Raster discovery** parses filenames into sites, model and date. It never opens a
  raster and never picks between duplicates. Duplicate and conflicting assets raise an
  error, or are held out until an explicit selection is made (D-021, D-069).

## 2. Spatial extraction (rasters)

For each station-day, the footprint contribution is moved **onto the native model grid**
by exact area overlap (conservative transfer; D-028). The model raster is never resampled.

With `c_j` the contribution in model cell *j*:

$$ET = \frac{\sum_{j \in valid} c_j\,ET_j}{\sum_{j \in valid} c_j}$$

Extraction methods (D-029):

| Method | Meaning |
|---|---|
| `footprint_conservative` | footprint-weighted mean over the model's own valid cells |
| `footprint_conservative_common_support` | the same, restricted to cells valid in every model that day |
| `pixel_window_<n>x<n>` | unweighted mean of whole pixels centred on the tower pixel |
| `nearest_tower_pixel` | the pixel containing the tower (diagnostic) |

The following are separate quantities, reported side by side and never multiplied (D-008):

- the retained weight fraction (`fp_weight_retained_frac`), whose denominator is the
  truncated, renormalised footprint;
- the fraction inside the model extent;
- the original domain capture.

A footprint is validated before it is used:

- negative weights beyond round-off are rejected (D-027);
- impossible values are rejected (D-043, D-054);
- a footprint with no positive weight is rejected.

A rejected footprint suppresses only the footprint methods for that day.

**Resilience (step 18).** Rasters are read one at a time, only in the window needed.
Retries, unreadable-file handling, checkpoints and the systematic-failure rules are
described in {doc}`configuration`. None of them changes a value.

## 3. Footprint aggregation and weighting sensitivity

Per-timestep footprints are aggregated to station days as

$$W_i = \frac{\sum_t a_t f_{it}}{\sum_t a_t}$$

with a declared temporal weight `a_t`. The options are time-mean, reference-ET weighted,
modelled-ET weighted, or measured-ET weighted, the last optionally multiplied by the
interval duration (D-032, D-033). A single daily raster cannot be re-weighted.

Footprint sets are compared only on frozen keys (D-034). A measured-ET-weighted footprint
depends on the observations it is later compared with. It is therefore a sensitivity
case, flagged `footprint_target_dependence`.

Reference-ET-weighted footprints follow the practice of the national OpenET evaluation
(Volk et al., 2024) and of the Upper Colorado River Basin intercomparison (Huntington et
al., 2022). Weighting by measured ET differs from that practice.

## 4. Cohorts

A comparison declares models, reference variants and extraction sides. **Candidates** are
every site-day crossed with every declared model. A model without a row on a day is a
failing candidate, never silently absent.

A candidate is eligible when every side has a usable value and every reference variant has
an observation. Quality rules (`min_retained_frac`) apply before scoring, identically to
every model.

**Freezing.** For `common_date`, `pairwise_model` and `common_models` cohorts, a day is kept
only when every model is eligible. A model missing difficult days therefore cannot be
scored on easier ones (D-039). The membership table gives the reason for every excluded
candidate. This strict freezing is a design choice of this package.

## 5. Periods and completeness

A period total is the **sum** of daily depths, and the calendar sets the denominator.
There are three separate products (D-041):

| Product | Rule |
|---|---|
| `partial_sum` | the matched days only, labelled with their count, never scaled |
| `complete_total` | only when every calendar day is a cohort day and the temporal support is daily |
| `estimated_total` | only when declared. Missing days are imputed by mean scaling, linear interpolation or the reference-ET fraction (paired on both sides, bracketed gaps only, D-042), and imputed values stay in `*_estimated_mm` columns |

Growing seasons come from a declared month-day rule, a declared table, or a crop-height
model.

## 6. Metrics

The metrics are MBE, MAE, RMSE, bias-corrected RMSE (diagnostic), percent bias, NRMSE,
Pearson r and r², the OLS slope and intercept (descriptive), and optionally NSE, KGE and
MAPE (D-044).

- **Common mask.** Metrics use the cohort's common mask; no value is dropped per metric.
- **r² is not predictive R².** `pearson_r_squared` is the squared correlation. The
  predictive coefficient `1 − SSE/SST` is NSE (Nash and Sutcliffe, 1970), and a biased
  model can have r² = 1.
- **KGE** is the original form of Gupta et al. (2009) with the standard-deviation ratio.
  The modified KGE of Kling et al. (2012) is not implemented and is never mixed with it.
- **Undefined values.** A metric that cannot be computed is null with a stable
  `validity_reason`: `singleton_sample`, `constant_observed` or `near_zero_observed_mean`.
- **Daily and period values are never pooled.**

Using complementary error metrics rather than one score follows the OpenET evaluation
literature (Volk et al., 2024; Reitz et al., 2025).

## 7. Intervals (paired moving-block bootstrap)

Each interval describes the sampling variability of one station's metric, or of a paired
contrast, across its station-days (D-047). The scheme is a moving-block bootstrap (Künsch,
1989) adapted here:

- **Shared draws.** Whole cohort days are resampled, with one index draw shared by every
  model, side and variant. A contrast is therefore always computed on identical days.
- **Segments.** Blocks stay inside gap-free segments, also split at a declared year
  boundary. They never span a missing day and never wrap around.
- **Metrics.** Each replicate is rescored with the same metric function.
- **Support.** With fewer than `min_blocks` full blocks, the status is
  `insufficient_support` and no interval is given.
- **Block lengths** are declared and reported side by side. None is adopted by default.
  For US-UTJ the residual autocorrelation suggests that 7- and 14-day intervals are too
  narrow (D-048).
- **Period metrics** resample whole periods.

An interval excludes reference measurement error, footprint-model uncertainty and
method-choice sensitivity (§10), and it is not a ranking.

## 8. Rankings and contrasts

Models are ranked only inside one frozen all-model or pairwise cohort and context, and only
on score metrics with directions declared in one place (D-049).

- **Ties** share mid-ranks.
- **Rank stability** comes from the bootstrap replicates: mean rank, rank interval and the
  rank distribution. The top-rank frequency is a stability measure, not the probability
  that a model is best.
- **Leaders.** A `unique_leader` requires the leader's contrast intervals to exclude
  every other model. Otherwise `no_unique_leader` lists every model not separated from
  the leader.
- **Equivalence** needs a declared margin; without one it is `not_assessed`. Contrasts are
  `exploratory` unless formal tests (Holm or Bonferroni) are declared.
- **Network summaries.** The equal-site mean is primary, and pooled RMSE is labelled as
  such. They carry no interval, because a population of sites needs a hierarchical design
  that is not implemented.

## 9. Diagnostics, representativeness and satellite support

- **Closure** (D-051). The magnitude of the existing correction is computed from the
  station's own columns, on raw-based days only. It is labelled reference sensitivity, not
  uncertainty.
- **Provenance subsets.** Observation-dominated and gap-filled subsets are compared on
  identical days.
- **Conditional errors** are computed in bins fixed before any error is seen. Sparse bins
  carry a status, not a value. These are associations, not causes.
- **Process diagnostics**:
  - daily-mean negative H, which is not a daytime frequency;
  - wind sectors wrapping at 0/360;
  - error against net radiation, corrected and raw, because the corrected reference is
    coupled to net radiation.
- **Landscape representativeness** (D-055). Each support's weights are moved onto each
  land-cover map's native grid by exact overlap; class codes are never resampled.
  - Valid, nodata and outside fractions are reported separately.
  - The continuous index uses a weighted mean, the population SD and the Kish effective
    number of cells.
  - The footprint contours are built from the footprint alone.

  Representativeness framing follows Chu et al. (2021). The optional fluxfootprints
  HIGH/MEDIUM/LOW labels are labels, never accuracy guarantees (D-056).
- **Satellite support** (D-057). Clear-observation versus interpolated days are compared
  only with authoritative per-pixel or per-site metadata. Without such metadata the
  analysis is `not_available` (D-011). Missing rows and scene cloud cover are never used as
  evidence.

## 10. Events, anomalies, water balance and scenarios

- **Management events** come only from the station log. Responses are uncontrolled
  before/after descriptions, never causal estimates (D-060, D-061). Event analysis is
  motivated by Knipper et al. (2026) and Montazar et al. (2026).
- **Anomaly skill** has two forms:
  - a held-out climatology, fitted on other years only;
  - an in-sample moving mean, labelled `exploratory`.

  Raw-value metrics on the same days are reported next to the anomalies.
- **Water balance** (D-062). Each term is declared:

  $$residual = P + I + CR - ET - RO - DP - \Delta S$$

  An unknown term makes the residual unknown; it is never set to zero. Support mismatches
  are flagged. A residual is a plausibility check, never ET error. Osorio-Diaz et al.
  (2026) motivate regional water-balance checks with unresolved exchanges kept in the
  residual.
- **Scenarios** (D-063). Declared measurement distributions, supplied footprint
  realisations and named method-choice alternatives are kept apart from one another and
  from bootstrap sampling intervals.

## 11. Ensembles

Ensembles are built **after extraction**, from members' extracted values. They are not
pixel-level ensembles (D-065).

- **What is compared.** The official ensemble (never a member), the member mean and median,
  and an optional fitted ensemble, all on the same frozen days.
- **Fitted weights** are simplex least squares, shrunk toward equal weights. The shrinkage
  is chosen by inner grouped folds on training data only.
- **Folds** leave out a year or a site, with a time buffer. Random daily splits are never
  used.
- **Declined folds.** A fold with too few training folds or held-out days is `declined`
  with its reason.
- **Member spread** is disagreement, not an interval.

Weighting evaluated at held-back sites follows Reitz et al. (2025). This simpler local
ensemble is an extension, not a reproduction of their mapping workflow.

## 12. Reports, cache and provenance

- **Reports** are built only from saved tables (D-076). Captions name the units, reference
  variant, extraction method, scale, cohort and sample size, and are checked against the
  declaration.
- **Figures** without rows are `not_available`; cumulative curves are broken at missing
  periods.
- **Wording.** The words "network", "validated" and "significant" are refused unless a
  verdict column licenses them.
- **Cache** (D-074). Each unit's key covers the code, the configuration entries the unit
  reads and its input identities, so a change invalidates exactly the units that read it.
- **Run manifest** (D-075). Each run's manifest records the configuration hash, input
  identities, package and upstream versions, grids, masks, seeds, exclusion counts and
  every unit's status. It is written as `running` when the run starts and updated to
  `completed` or `failed` at the end. A run killed outright therefore leaves a `running`
  manifest behind. Earlier runs' manifests are never overwritten.
- **Atomic outputs.** Every output (Parquet, CSV, text, figures, footprints, stamps,
  manifests) is written to a temporary file and renamed. Footprints are also read back and
  compared bit for bit (D-043).
