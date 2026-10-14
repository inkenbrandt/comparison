"""Footprint weighting sensitivity on frozen dates (guide step 6).

Extractions made with different footprint sets (time-mean, reference-ET-,
modeled-ET- or measured-ET-weighted) are compared only on the same
site/date/model/method keys:

1. Eligibility is decided before any comparison and identically for every
   set: a key is kept only when **every** compared set has a value there
   (and, when declared, passes ``min_retained_frac``). Removing a date from
   one set removes it from all.
2. For each reference variant the frozen keys are further restricted to
   days with that reference value, so each variant has its own cohort and
   every modeled value stays paired with its observation (for resampling).
   The ``none`` cohort compares the sets with no reference restriction.
3. ``common_models=True`` also requires every model present in the input on
   each kept day (an all-model cohort).

Outputs are the paired rows (``paired`` schema, one row per set) and
descriptive set-minus-baseline differences of modeled ET. No agreement with
the station is scored here (step 9), and no set is preferred.

A set whose weights use the target measurements (``measured_et_weighted``)
makes the comparison a sensitivity experiment; an ``unknown`` weighting may
do so. Rows carry a ``footprint_target_dependence=`` flag, and the report
warns. With fewer than two sets holding data, the analysis is skipped
(``not_available``), never filled in.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from etcompare.config import FootprintSetSpec, to_plain
from etcompare.errors import ContractError
from etcompare.references import reference_values
from etcompare.results import ExclusionLog, format_key
from etcompare.schemas import (
    EXTRACTIONS,
    PAIRED,
    REFERENCE_VARIANTS,
    CanonicalTable,
    check_primary_key,
    conform,
)
from etcompare.validation import ValidationReport

STAGE = "footprint_sensitivity"
MODEL_ONLY = "none"
_KEY = ["site_id", "date", "model", "extraction_method"]
DIFFERENCE_COLUMNS = [
    "comparison_id", "reference_variant", "extraction_method", "model", "footprint_set_id",
    "baseline_set_id", "n_days", "baseline_mean_mm", "mean_diff_mm", "median_diff_mm",
    "mean_abs_diff_mm", "p95_abs_diff_mm", "max_abs_diff_mm", "rel_mean_diff",
]


@dataclass(frozen=True)
class FootprintSensitivityResult:
    comparison_id: str
    status: str  # ok | not_available
    reason: str
    paired: CanonicalTable | None
    differences: pd.DataFrame
    availability: pd.DataFrame
    report: ValidationReport
    exclusions: ExclusionLog = field(default_factory=ExclusionLog)
    attributes: dict[str, Any] = field(default_factory=dict)


def _frame(x: CanonicalTable | pd.DataFrame) -> pd.DataFrame:
    return x.frame if isinstance(x, CanonicalTable) else conform(x, EXTRACTIONS)


def compare_footprint_sets(
    extractions: CanonicalTable | pd.DataFrame | Sequence[CanonicalTable | pd.DataFrame],
    footprint_sets: Sequence[FootprintSetSpec],
    *,
    comparison_id: str,
    station_daily: CanonicalTable | pd.DataFrame | None = None,
    reference_variants: Sequence[str] = REFERENCE_VARIANTS,
    extraction_methods: Sequence[str] = ("footprint_conservative",),
    min_retained_frac: float | None = None,
    common_models: bool = False,
) -> FootprintSensitivityResult:
    """Compare extractions from ``footprint_sets`` on frozen keys.

    The first set is the baseline for the differences. ``min_retained_frac``
    applies the same retained-weight threshold to every set; declare it
    before looking at results.
    """
    if len(footprint_sets) < 2:
        raise ContractError("footprint sensitivity needs at least two footprint sets")
    ids = [f.set_id for f in footprint_sets]
    if len(set(ids)) != len(ids):
        raise ContractError(f"duplicate footprint sets {ids}")
    if min_retained_frac is not None and not 0 <= min_retained_frac <= 1:
        raise ContractError("min_retained_frac must be in [0, 1]")
    bad = set(reference_variants) - set(REFERENCE_VARIANTS)
    if bad:
        raise ContractError(f"unknown reference variant(s) {sorted(bad)}")
    parts = extractions if isinstance(extractions, Sequence) else [extractions]
    ext = pd.concat([_frame(p) for p in parts], ignore_index=True)
    # Only compared rows must be unique: footprint-free methods (set 'none') may repeat
    # across runs and are never compared here.
    sel = ext[ext["footprint_set_id"].isin(ids) & ext["extraction_method"].isin(
        extraction_methods) & (ext["period"] == "day")].copy()
    check_primary_key(sel, EXTRACTIONS.primary_key, table="extractions (combined)")

    report = ValidationReport(f"footprint sensitivity {comparison_id}")
    exclusions = ExclusionLog()
    sel["usable"] = sel["et_mm"].notna()
    if min_retained_frac is not None:
        low = sel["usable"] & ~(sel["fp_weight_retained_frac"] >= min_retained_frac)
        sel.loc[low, "usable"] = False
        for r in sel[low].itertuples():
            exclusions.add(STAGE, format_key(site_id=r.site_id, date=r.date, model=r.model,
                                             method=r.extraction_method,
                                             footprint_set=r.footprint_set_id),
                           "below_min_retained_frac", affected_comparison=comparison_id,
                           detail=f"{r.fp_weight_retained_frac:.4f} < {min_retained_frac}")
    availability = (sel.groupby(["footprint_set_id", "extraction_method", "model"])
                    .agg(n_rows=("et_mm", "size"), n_usable=("usable", "sum"))
                    .reset_index())
    attributes: dict[str, Any] = {
        "workflow": "etcompare.footprint_sensitivity.compare_footprint_sets",
        "baseline_set_id": ids[0], "footprint_sets": [to_plain(f) for f in footprint_sets],
        "extraction_methods": list(extraction_methods), "min_retained_frac": min_retained_frac,
        "common_models": common_models, "reference_variants": list(reference_variants),
    }
    present = [i for i in ids if sel.loc[sel["footprint_set_id"] == i, "usable"].any()]
    if len(present) < len(ids):
        lacking = sorted(set(ids) - set(present))
        reason = f"no usable extraction rows for footprint set(s) {lacking}"
        report.info("not_available", reason)
        return FootprintSensitivityResult(comparison_id, "not_available", reason, None,
                                          pd.DataFrame(columns=DIFFERENCE_COLUMNS),
                                          availability, report, exclusions, attributes)

    dep = {f.set_id: f.target_dependence for f in footprint_sets}
    for f in footprint_sets:
        if dep[f.set_id] != "no":
            report.warning(
                "footprint_target_dependence",
                f"{f.set_id}: temporal_weighting={f.temporal_weighting}; its weights "
                f"{'use' if dep[f.set_id] == 'yes' else 'may use'} the target station "
                "measurements, so this comparison is a sensitivity experiment, not "
                "independent validation")

    wide = sel[sel["usable"]].pivot(index=_KEY, columns="footprint_set_id", values="et_mm")
    wide = wide.reindex(columns=ids)
    complete = wide.notna().all(axis=1)
    for key, row in wide[~complete].iterrows():
        missing = [i for i in ids if pd.isna(row[i])]
        exclusions.add(STAGE, format_key(**dict(zip(_KEY, key, strict=True))),
                       "missing_in_footprint_set", affected_comparison=comparison_id,
                       detail=f"no usable value in {missing}")
    # Keys absent from every set are not candidates and are not logged.
    frozen = wide[complete]
    report.info("frozen_keys", f"{len(frozen)} of {len(wide)} site/date/model/method keys have "
                f"a usable value in all {len(ids)} sets", n_rows=len(frozen))

    if common_models:
        models = sorted(sel["model"].unique())
        n_models = frozen.reset_index().groupby(["site_id", "date", "extraction_method"])[
            "model"].transform("nunique").to_numpy()
        keep = n_models == len(models)
        for key in frozen.index[~keep]:
            exclusions.add(STAGE, format_key(**dict(zip(_KEY, key, strict=True))),
                           "not_all_models", affected_comparison=comparison_id,
                           detail=f"needs all of {models}")
        frozen = frozen[keep]
        report.info("common_models", f"{len(frozen)} keys remain with all {len(models)} "
                    "models present", n_rows=len(frozen))

    long_base = frozen.reset_index()
    cohorts: list[tuple[str, pd.DataFrame]] = [(MODEL_ONLY, long_base.assign(
        observed_mm=np.nan, reference_provenance=None))]
    if station_daily is not None:
        for variant in reference_variants:
            ref = reference_values(station_daily, variant)
            j = long_base.merge(ref, on=["site_id", "date"], how="left", validate="many_to_one")
            lost = j["observed_mm"].isna()
            # One record per lost day, not per model/method row.
            for (site, date) in j.loc[lost, ["site_id", "date"]].drop_duplicates().itertuples(
                    index=False):
                exclusions.add(STAGE, format_key(site_id=site, date=date),
                               f"reference_unavailable:{variant}", severity="info",
                               affected_comparison=comparison_id)
            cohorts.append((variant, j[~lost]))
    else:
        report.info("no_station_table", "no station table supplied; only the model-only "
                    "cohort is built")

    paired_rows, diffs = [], []
    for variant, rows in cohorts:
        for method, g in rows.groupby("extraction_method"):
            cohort_id = f"{comparison_id}:{variant}:{method}"
            if variant != MODEL_ONLY:
                for set_id in ids:
                    flags = [f"footprint_target_dependence={dep[set_id]}"] \
                        if dep[set_id] != "no" else []
                    paired_rows.append(pd.DataFrame({
                        "comparison_id": comparison_id, "cohort_id": cohort_id,
                        "site_id": g["site_id"], "date": g["date"], "model": g["model"],
                        "reference_variant": variant, "extraction_method": method,
                        "footprint_set_id": set_id, "observed_mm": g["observed_mm"],
                        "modeled_mm": g[set_id],
                        "reference_provenance": g["reference_provenance"],
                        "flags": ";".join(flags) or None,
                    }))
            for model, m in g.groupby("model"):
                base = m[ids[0]].to_numpy()
                for set_id in ids[1:]:
                    d = m[set_id].to_numpy() - base
                    ad = np.abs(d)
                    diffs.append({
                        "comparison_id": comparison_id, "reference_variant": variant,
                        "extraction_method": method, "model": model,
                        "footprint_set_id": set_id, "baseline_set_id": ids[0],
                        "n_days": len(d), "baseline_mean_mm": float(base.mean()),
                        "mean_diff_mm": float(d.mean()), "median_diff_mm": float(np.median(d)),
                        "mean_abs_diff_mm": float(ad.mean()),
                        "p95_abs_diff_mm": float(np.quantile(ad, 0.95)),
                        "max_abs_diff_mm": float(ad.max()),
                        "rel_mean_diff": float(d.mean() / base.mean()) if base.mean() != 0
                        else np.nan,
                    })
    paired = CanonicalTable.build(
        pd.concat(paired_rows, ignore_index=True) if paired_rows else PAIRED.empty(),
        PAIRED, {**attributes, "comparison_id": comparison_id}, source=comparison_id)
    differences = pd.DataFrame(diffs, columns=DIFFERENCE_COLUMNS)
    report.info("cohorts", "; ".join(
        f"{v}: {rows[['site_id', 'date']].drop_duplicates().shape[0]} days"
        for v, rows in cohorts))
    return FootprintSensitivityResult(comparison_id, "ok", "", paired, differences,
                                      availability, report, exclusions, attributes)
