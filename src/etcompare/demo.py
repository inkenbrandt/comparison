"""Tiny offline synthetic dataset and configuration (guide steps 17 and 18).

:func:`write_demo` writes, with a fixed seed, everything an end-to-end run needs:

- three synthetic stations ``XX-SY1``, ``XX-SY2`` and ``XX-SY3`` (``XX`` is an ISO 3166
  code assigned to no country, so no real station can be named), two calendar years;
- station daily tables in the flux-data-qaqc export layout: raw ET, closure-corrected ET,
  gap-filled days (``ET_gap``, filled from gridMET-like reference ET), days with neither,
  a closure factor and daily meteorology;
- a pre-extracted model table with five synthetic models (``alpha`` to ``echo``) and an
  official ensemble ``ens``: footprint-weighted and source-region values, retained weight,
  days without a footprint, and a stretch where ``echo`` is missing;
- a station event log (cuttings and one instrument visit);
- ``config.yaml``: every step-7 to step-16 analysis that pre-extracted tables support,
  with ``report.synthetic: true``. No raster or landscape input exists, so raster
  extraction and representativeness are reported as not declared, and the index of the
  declared footprint set as not available.

Every value is generated. Nothing describes an observed station or a real product, and
reports built from it are labelled synthetic on every page and caption.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd

DEMO_SEED = 20261019
SITES = ("XX-SY1", "XX-SY2", "XX-SY3")
MODELS = ("alpha", "bravo", "charlie", "delta", "echo")
OFFICIAL = "ens"
START, END = "2021-01-01", "2022-12-31"


def _ar1(rng: np.random.Generator, n: int, phi: float, sd: float) -> np.ndarray:
    e = rng.normal(0, sd * np.sqrt(1 - phi ** 2), n)
    out = np.empty(n)
    out[0] = rng.normal(0, sd)
    for i in range(1, n):
        out[i] = phi * out[i - 1] + e[i]
    return out


def _station(rng: np.random.Generator, site_index: int, days: pd.DatetimeIndex
             ) -> tuple[pd.DataFrame, np.ndarray]:
    n = len(days)
    doy = days.dayofyear.to_numpy(float)
    season = np.clip(np.sin(np.pi * (doy - 75) / 240), 0, None)
    amp = 5.0 + 0.6 * site_index
    truth = 0.4 + amp * season ** 1.3 + _ar1(rng, n, 0.7, 0.35)
    truth = np.clip(truth, 0.05, None)
    etr = np.clip(truth * 1.3 + 0.4 + rng.normal(0, 0.3, n), 0.2, None)
    cf = 1.25 + 0.12 * np.sin(2 * np.pi * doy / 365) + rng.normal(0, 0.04, n)
    raw = truth / cf
    corr = raw * cf
    gap = np.zeros(n, dtype=bool)
    for _ in range(9):                      # gap-filled runs of 3-20 days
        s = int(rng.integers(0, n - 20))
        gap[s:s + int(rng.integers(3, 21))] = True
    missing = np.zeros(n, dtype=bool)
    missing[rng.choice(np.flatnonzero(~gap), size=12, replace=False)] = True
    frac = pd.Series(np.where(gap | missing, np.nan, corr / etr)).interpolate(
        limit_direction="both").to_numpy()
    et = np.where(gap | missing, np.nan, raw)
    et_corr = np.where(gap, etr * frac, np.where(missing, np.nan, corr))
    frame = pd.DataFrame({
        "date": days.strftime("%Y-%m-%d"),
        "ET": np.round(et, 6),
        "ET_corr": np.round(np.where(gap, et_corr, np.round(et, 6) * np.round(cf, 6)), 6),
        "ET_gap": gap,
        "ebc_cf": np.round(np.where(gap | missing, np.nan, cf), 6),
        "gridMET_ETr": np.round(etr, 4),
        "gridMET_ETo": np.round(etr / 1.2, 4),
        "gridMET_prcp": np.round(np.where(rng.random(n) < 0.12, rng.gamma(1.5, 4.0, n), 0), 2),
        "vpd": np.round(np.clip(0.3 + 2.4 * season + rng.normal(0, 0.25, n), 0.05, None), 3),
        "ws": np.round(np.clip(rng.gamma(4, 0.6, n), 0.2, None), 2),
        "h": np.round(25 + 60 * season - 45 * (truth / truth.max()) + rng.normal(0, 15, n), 1),
        "netrad": np.round(40 + 160 * season + rng.normal(0, 20, n), 1),
    })
    return frame, truth


def _models(rng: np.random.Generator, site_index: int, days: pd.DatetimeIndex,
            truth: np.ndarray, site: str) -> pd.DataFrame:
    n = len(days)
    params = {"alpha": (1.00, 0.10, 0.45), "bravo": (0.85, -0.10, 0.55),
              "charlie": (1.12, 0.15, 0.65), "delta": (0.95, -0.35, 0.50),
              "echo": (1.05, 0.30 + 0.25 * site_index, 0.70)}
    no_footprint = rng.random(n) < 0.04
    values = {}
    for m, (gain, bias, sd) in params.items():
        values[m] = np.clip(gain * truth + bias + _ar1(rng, n, 0.6, sd), 0.0, None)
    values[OFFICIAL] = np.clip(np.mean([values[m] for m in MODELS], axis=0)
                               + rng.normal(0, 0.15, n), 0.0, None)
    rows = []
    echo_start = pd.Timestamp("2021-05-01")
    for m, v in values.items():
        keep = ~no_footprint
        if m == "echo":
            keep &= days >= echo_start
        retained = np.where(m == "delta", rng.uniform(0.62, 1.0, n), rng.uniform(0.93, 1.0, n))
        rows.append(pd.DataFrame({
            "station_id": site, "model": m, "footprint_date": days[keep],
            "ET_fp": np.round(v[keep], 6),
            "ET_mean": np.round(np.clip(v[keep] + rng.normal(-0.05, 0.12, int(keep.sum())),
                                        0.0, None), 6),
            "unity_check": 1.0 + rng.normal(0, 1e-10, int(keep.sum())),
            "valid_weight_fraction": np.round(retained[keep], 6)}))
    return pd.concat(rows, ignore_index=True)


def _events() -> pd.DataFrame:
    rows = []
    k = 900
    for site, offset in zip(SITES, (0, 6, 12), strict=True):
        for year in (2021, 2022):
            for md in ("06-05", "07-15", "08-25"):
                d = pd.Timestamp(f"{year}-{md}") + pd.Timedelta(days=offset)
                rows.append({"event_id": str(k), "stationid": site,
                             "event_date": d.strftime("%Y-%m-%d") + " 09:00:00.000000",
                             "observer": "generator", "event_type": "cutting",
                             "event_source": "synthetic schedule",
                             "event_notes": "synthetic cutting"})
                k += 1
        rows.append({"event_id": str(k), "stationid": site,
                     "event_date": "2022-04-20 10:30:00.000000", "observer": "generator",
                     "event_type": "station visit", "event_source": "synthetic log",
                     "event_notes": "synthetic instrument visit"})
        k += 1
    return pd.DataFrame(rows)


CONFIG = """\
# Synthetic demonstration (generated by etcompare.demo.write_demo, seed {seed}).
# Every value is generated; no station or product here is real. All thresholds are
# Provisional example values, not established standards.

x-station: &station {{timezone: "UTC-07:00", day_definition: local_standard_time, interval_label: start}}
stations:
  XX-SY1: {{<<: *station, name: synthetic one, land_cover: CRO}}
  XX-SY2: {{<<: *station, name: synthetic two, land_cover: CRO}}
  XX-SY3: {{<<: *station, name: synthetic three, land_cover: CRO}}

x-product: &product {{units: mm/day, day_definition: unknown, temporal_support: interpolated_daily, version: synthetic}}
products:
  alpha: *product
  bravo: *product
  charlie: *product
  delta: *product
  echo: *product
  ens: {{<<: *product, is_ensemble: true}}

x-table: &table
  date_column: date
  reference_columns: {{et_raw: ET, et_corrected: ET_corr}}
  gap_flag_column: ET_gap
  closure_factor_column: ebc_cf
  reference_et_column: gridMET_ETr
  units: {{ET: mm/day, ET_corr: mm/day, gridMET_ETr: mm/day}}
station_tables:
  - {{<<: *table, site_id: XX-SY1, path: data/XX-SY1_daily.csv}}
  - {{<<: *table, site_id: XX-SY2, path: data/XX-SY2_daily.csv}}
  - {{<<: *table, site_id: XX-SY3, path: data/XX-SY3_daily.csv}}

extracted_tables:
  - name: synthetic_extractions
    path: data/synthetic_extractions.parquet
    site_column: station_id
    model_column: model
    date_column: footprint_date
    value_columns: {{ET_fp: upstream_footprint_weighted, ET_mean: source_region_mean}}
    units: {{ET_fp: mm/day, ET_mean: mm/day}}
    footprint_set_id: synthetic_fp
    footprint_method: synthetic_reference_et_weighted
    weight_sum_column: unity_check
    retained_weight_column: valid_weight_fraction
    retained_weight_denominator: normalized_truncated_footprint

# Declared so that comparisons can name it; its rasters are not part of the demo, so the
# index unit reports it as not available.
footprint_sets:
  - set_id: synthetic_fp
    root: data/footprints
    weighting_method: synthetic_reference_et_weighted
    temporal_weighting: reference_et_weighted
    value_semantics: normalized_cell_weight

x-fp: &fp {{extraction_method: upstream_footprint_weighted, footprint_set_id: synthetic_fp}}
x-all: &all [alpha, bravo, charlie, delta, echo, ens]
comparisons:
  - {{comparison_id: syn_rank_inclusive, kind: common_date, models: *all, reference_variants: [et_corrected_inclusive], sides: [*fp], sources: [synthetic_extractions]}}
  - {{comparison_id: syn_rank_measured, kind: common_date, models: *all, reference_variants: [et_corrected_measured], sides: [*fp], sources: [synthetic_extractions]}}
  - {{comparison_id: syn_closure, kind: paired_reference, common_models: true, models: *all, reference_variants: [et_raw, et_corrected_measured], sides: [*fp], sources: [synthetic_extractions]}}
  - {{comparison_id: syn_coverage, kind: model_available, models: *all, reference_variants: [et_corrected_inclusive], sides: [*fp], sources: [synthetic_extractions]}}
  - comparison_id: syn_fp_vs_region
    kind: paired_method
    models: *all
    reference_variants: [et_corrected_measured]
    sides: [*fp, {{extraction_method: source_region_mean, footprint_set_id: synthetic_fp}}]
    sources: [synthetic_extractions]
  - {{comparison_id: syn_pair, kind: pairwise_model, models: [alpha, bravo], reference_variants: [et_corrected_measured], sides: [*fp], sources: [synthetic_extractions]}}

period_aggregations:
  - aggregation_id: syn_periods
    periods: [month, growing_season, year]
    growing_season_rule: {{start: "04-01", end: "10-31"}}
    estimation_method: reference_et_fraction
    estimation_max_gap_days: 14
    estimation_min_completeness: 0.5

metric_sets:
  - metric_set_id: syn_metrics
    metrics: [mbe, mae, rmse, bias_corrected_rmse, percent_bias, nrmse_mean, pearson_r,
              pearson_r_squared, ols_slope, ols_intercept, nse]
    daily_tolerance: {{mean: 0.01, std: 0.001}}
    period_tolerance: {{mean: 0.1, std: 0.01}}
    period_aggregations: [syn_periods]
    period_products: [partial_sum, complete_total, estimated_total]
    cumulative_error: true

bootstrap:
  - bootstrap_id: syn_bootstrap
    metric_set: syn_metrics
    block_lengths: [7, 14]
    period_block_lengths: [1]
    seed: {seed}
    n_replicates: 199
    confidence_level: 0.95
    interval_methods: [percentile]
    min_blocks: 10
    min_valid_fraction: 0.9
    segment_boundary: year
    acf_max_lag: 14
    contrasts: true

rankings:
  - ranking_id: syn_ranking
    metric_set: syn_metrics
    bootstrap: syn_bootstrap
    metrics: {{mae: lower, rmse: lower, pearson_r: higher, mbe: closest_to_zero}}
    equivalence_margins: {{}}
    network_summaries: [equal_site_mean, observation_weighted_mean, pooled_rmse]
    formal_tests: false
    year_subsets: false
    comparisons: [syn_rank_inclusive, syn_rank_measured, syn_pair]

diagnostics:
  - diagnostics_id: syn_diagnostics
    metric_set: syn_metrics
    metrics: [mbe, mae, rmse]
    min_bin_days: 20
    bootstrap: syn_bootstrap
    calendar: [season]
    gap_filled_fraction_edges: [0, 0.25, 0.5, 0.75, 1]
    provenance_subsets: true
    closure: true
    net_radiation_variable: rn_daily
    sensible_heat_variable: h_daily
    wind_direction_variable: null
    comparisons: [syn_rank_inclusive, syn_closure]
    variables:
      vpd: {{source: station, column: src_vpd, units: kPa, meaning: synthetic daily VPD, status: verified, edges: [0, 0.5, 1.0, 1.5, 2.0, .inf]}}
      eto: {{source: station, column: src_gridMET_ETo, units: mm/day, meaning: synthetic reference ET, status: provisional, edges: [0, 2, 4, 6, .inf]}}
      h_daily: {{source: station, column: src_h, units: W m-2, meaning: synthetic daily-mean sensible heat, status: verified, edges: [-.inf, 0, 25, 50, .inf]}}
      rn_daily: {{source: station, column: src_netrad, units: W m-2, meaning: synthetic daily-mean net radiation, status: verified}}
      retained_frac: {{source: extraction, column: fp_weight_retained_frac, units: "1", meaning: retained footprint weight, status: verified, edges: [0, 0.8, 0.9, .inf]}}

satellite_support:
  - satellite_support_id: syn_satellite
    metric_set: syn_metrics
    metrics: [mbe, mae, rmse]
    min_subset_days: 20
    sources: []
    comparisons: [syn_rank_inclusive]

event_tables:
  - name: syn_events
    path: data/synthetic_events.csv
    columns: {{event_id: event_id, site: stationid, timestamp: event_date, event_type: event_type,
               event_source: event_source, observer: observer, notes: event_notes}}
    timestamp_format: "%Y-%m-%d %H:%M:%S.%f"
    timestamp_timezone: "UTC-07:00"
    day_rule: station_local_date
    site_mapping: {{XX-SY1: XX-SY1, XX-SY2: XX-SY2, XX-SY3: XX-SY3}}
    site_windows:
      XX-SY1: {{start: "{start}", end: "{end}"}}
      XX-SY2: {{start: "{start}", end: "{end}"}}
      XX-SY3: {{start: "{start}", end: "{end}"}}
    event_types:
      cutting: {{category: management, management_kind: cutting}}
      station visit: {{category: instrument}}
    evidence_classes: {{synthetic schedule: station_log, synthetic log: station_log}}
    approximate_date_patterns: []

event_analyses:
  - event_analysis_id: syn_cutting_response
    event_table: syn_events
    management_kinds: [cutting]
    pre_days: 7
    post_offset_days: 1
    post_days: 7
    min_window_completeness: 0.7
    baseline_min_mm: 0.5
    recovery_fraction: 0.9
    recovery_max_days: 28
    recovery_smoothing_days: 3
    approximate_policy: flag
    comparisons: [syn_rank_measured]

anomalies:
  - anomaly_id: syn_anomaly_climatology
    metric_set: syn_metrics
    method: held_out_climatology
    metrics: [mbe, mae, rmse]
    min_days: 20
    fold_unit: year
    climatology_half_window_days: 15
    min_training_values: 20
    min_training_years: 2
    leap_day: as_feb28
    comparisons: [syn_rank_inclusive]

water_balances:
  - water_balance_id: syn_balance
    site_id: XX-SY1
    period_aggregation: syn_periods
    periods: [month, year]
    boundary: {{boundary_id: tower_point, kind: point, description: synthetic point balance}}
    equation: soil_zone
    sign_convention: inflows_positive
    terms:
      precipitation: {{status: modeled, source: station_column, column: src_gridMET_prcp, units: mm/day, spatial_support: gridded_cell, day_definition: unknown}}
      irrigation: {{status: unknown, source: none, reason: no synthetic irrigation input}}
      capillary_rise: {{status: unknown, source: none, reason: no input}}
      et: {{status: measured, source: station_et, reference_variant: et_corrected_inclusive, spatial_support: tower_footprint}}
      runoff: {{status: unknown, source: none, reason: no input}}
      deep_percolation: {{status: unknown, source: none, reason: no input}}
      storage_change: {{status: unknown, source: none, reason: no input}}

ensembles:
  - ensemble_id: syn_ensemble
    members: [alpha, bravo, charlie, delta, echo]
    official: ens
    reference_variant: et_corrected_measured
    side: *fp
    sources: [synthetic_extractions]
    missing_member_policy: require_all
    combinations: [mean, median]
    metric_set: syn_metrics
    bootstrap: syn_bootstrap
    contrast_metrics: [mae, rmse, mbe]
    designs: [leave_one_year_out, leave_one_site_out]
    fold_unit: year
    buffer_days: 28
    min_training_folds: 2
    min_training_days_per_fold: 120
    min_test_days_per_fold: 120
    method: simplex_least_squares
    regularisation: shrink_to_equal
    shrinkage_grid: [0, 0.25, 0.5, 0.75, 1]
    selection_metric: rmse
    preprocessing: none
    insufficient_data: decline

upstream:
  - {{name: etcompare.demo, version: "{version}", evidence: verified, note: generator of every input}}

decisions:
  - ref: DEMO-1
    status: provisional
    summary: block lengths 7 and 14 days are example sensitivity values; none is adopted
    applies_to: [bootstrap.syn_bootstrap]
  - ref: DEMO-2
    status: open
    summary: no equivalence margin is declared, so equivalence is not assessed
    applies_to: [rankings.syn_ranking]

report:
  title: etcompare synthetic demonstration
  synthetic: true

output_dir: outputs
"""


def write_demo(directory: str | os.PathLike[str], *, seed: int = DEMO_SEED) -> Path:
    """Write the synthetic inputs and ``config.yaml`` under ``directory``; return the config."""
    from etcompare import __version__

    root = Path(directory)
    data = root / "data"
    data.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    days = pd.date_range(START, END, freq="D")
    extractions = []
    for i, site in enumerate(SITES):
        station, truth = _station(rng, i, days)
        station.to_csv(data / f"{site}_daily.csv", index=False)
        extractions.append(_models(rng, i, days, truth, site))
    pd.concat(extractions, ignore_index=True).to_parquet(
        data / "synthetic_extractions.parquet", index=False)
    _events().to_csv(data / "synthetic_events.csv", index=False)
    path = root / "config.yaml"
    path.write_text(CONFIG.format(seed=seed, start=START, end=END, version=__version__),
                    encoding="utf-8")
    return path
