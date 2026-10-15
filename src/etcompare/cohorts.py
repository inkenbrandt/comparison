"""Explicit comparison cohorts (guide step 7).

:func:`build_cohort` turns station observations, extractions and a
:class:`~etcompare.config.ComparisonSpec` into an immutable :class:`Cohort`:
the paired rows (``paired`` schema) plus an inclusion decision, with a reason,
for every candidate site/date/model (``cohort_membership`` schema).

Rules
-----
1. Candidates are every site/date in the selected extractions or the station
   table for those sites (inside ``start``/``end``), crossed with every declared
   model. A model with no row on a day is a candidate that fails; it is never
   silently absent.
2. A candidate's own eligibility needs a usable modeled value on **every**
   declared side and an observation for **every** declared reference variant.
   Quality rules (``min_retained_frac``) are applied here, before any scoring,
   identically to every model and footprint side, and without looking at
   model-station agreement.
3. Freezing: for ``common_date``, ``pairwise_model`` and ``common_models``
   cohorts a day is kept only when every model is eligible, so a model missing
   difficult days cannot be scored on easier ones. Otherwise each model has its
   own cohort (``<comparison_id>:<model>``).
4. Every kept unit appears once per side and reference variant, so removing a
   date from one side removes it from all. Paired rows carry no missing values:
   later metrics use this common mask and must not drop values on their own.

Joins check primary keys first and use ``validate=`` so a duplicated input can
never multiply rows. Rows are flagged ``day_alignment=unverified`` and
``footprint_target_dependence=yes|unknown`` where applicable.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, TypeAlias

import numpy as np
import pandas as pd

from etcompare.config import ComparisonSpec, FootprintSetSpec, to_plain
from etcompare.references import reference_values
from etcompare.results import ExclusionLog, format_key
from etcompare.schemas import (
    COHORT_MEMBERSHIP,
    EXTRACTIONS,
    PAIRED,
    STATION_DAILY,
    CanonicalTable,
    TableSchema,
    check_primary_key,
    conform,
)
from etcompare.validation import Issue, ValidationReport

STAGE = "cohort"
COMMON = "common"
_UNIT = ["site_id", "date", "model"]

Tables: TypeAlias = CanonicalTable | pd.DataFrame | Sequence[CanonicalTable | pd.DataFrame]


@dataclass(frozen=True)
class Cohort:
    """Paired rows and membership decisions of one comparison. Immutable.

    Accessors return copies, so callers cannot change the cohort that later
    stages (aggregation, metrics, resampling) share. ``fingerprint`` is a
    SHA-256 of the included units: two analyses on the same cohort have the
    same fingerprint.
    """

    spec: ComparisonSpec
    status: str  # ok | empty
    _paired: CanonicalTable = field(repr=False)
    _membership: CanonicalTable = field(repr=False)
    _issues: tuple[Issue, ...] = field(repr=False)
    _exclusions: tuple[Mapping[str, Any], ...] = field(repr=False)
    _model_availability: pd.DataFrame = field(repr=False)
    _reference_availability: pd.DataFrame = field(repr=False)
    attributes: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "attributes", MappingProxyType(dict(self.attributes)))

    @property
    def comparison_id(self) -> str:
        return self.spec.comparison_id

    @property
    def fingerprint(self) -> str:
        return str(self.attributes["membership_sha256"])

    @property
    def paired(self) -> CanonicalTable:
        return _copy(self._paired)

    @property
    def membership(self) -> CanonicalTable:
        return _copy(self._membership)

    @property
    def report(self) -> ValidationReport:
        return ValidationReport(f"cohort {self.comparison_id}", list(self._issues))

    @property
    def exclusions(self) -> ExclusionLog:
        return ExclusionLog([dict(r) for r in self._exclusions])

    @property
    def cohort_ids(self) -> list[str]:
        return sorted(self._membership.frame["cohort_id"].unique())

    def model_availability(self) -> pd.DataFrame:
        """Every candidate site/date/model and side: is a non-null modeled value present?

        Columns ``site_id``, ``date``, ``model``, ``extraction_method``,
        ``footprint_set_id``, ``available``. Availability is before cohort rules
        (quality thresholds and freezing), within the comparison's sites and period.
        """
        return self._model_availability.copy()

    def reference_availability(self) -> pd.DataFrame:
        """Every candidate site/date and reference variant with its provenance (null if absent).

        Columns ``site_id``, ``date``, ``reference_variant``, ``reference_provenance``.
        """
        return self._reference_availability.copy()

    def units(self, cohort_id: str | None = None) -> pd.DataFrame:
        """Included ``cohort_id``/site/date/model units."""
        m = self._membership.frame
        m = m[m["included"].astype(bool)]
        if cohort_id is not None:
            m = m[m["cohort_id"] == cohort_id]
        return m[["cohort_id", *_UNIT]].reset_index(drop=True)

    def summary(self) -> pd.DataFrame:
        """Included days per cohort and model, with filled-reference days counted apart."""
        m = self._membership.frame
        g = m.groupby(["cohort_id", "model"])
        out = pd.DataFrame({"n_candidates": g.size(),
                            "n_days": g["included"].sum().astype(int)})
        p = self._paired.frame
        if len(p):
            filled = (p[p["reference_provenance"] == "corrected_filled"]
                      .drop_duplicates(["cohort_id", *_UNIT])
                      .groupby(["cohort_id", "model"]).size())
            out["n_days_reference_filled"] = filled.reindex(out.index, fill_value=0)
        else:
            out["n_days_reference_filled"] = 0
        n_sites = (m[m["included"].astype(bool)].groupby(["cohort_id", "model"])["site_id"]
                   .nunique())
        out["n_sites"] = n_sites.reindex(out.index, fill_value=0)
        return out.reset_index()


def _copy(t: CanonicalTable) -> CanonicalTable:
    return CanonicalTable(t.schema, t.frame.copy(), dict(t.attributes))


def _concat(x: Tables, schema: TableSchema) -> pd.DataFrame:
    parts = x if isinstance(x, Sequence) else [x]
    frames = [p.frame if isinstance(p, CanonicalTable) else conform(p, schema) for p in parts]
    return pd.concat(frames, ignore_index=True) if frames else schema.empty()


def _dependence(set_id: str, sets: Mapping[str, FootprintSetSpec] | None) -> str:
    if set_id == "none":
        return "no"
    spec = (sets or {}).get(set_id)
    return spec.target_dependence if spec is not None else "unknown"


def build_cohort(
    observations: Tables,
    extractions: Tables,
    spec: ComparisonSpec,
    *,
    footprint_sets: Mapping[str, FootprintSetSpec] | Sequence[FootprintSetSpec] | None = None,
) -> Cohort:
    """Build the cohort declared by ``spec``.

    ``observations`` are ``station_daily`` tables; ``extractions`` are
    ``extractions`` tables (only daily rows of the declared sides and models are
    used). ``footprint_sets`` supplies each footprint set's temporal weighting;
    a footprint side whose set is not supplied is flagged as possibly
    target-dependent.
    """
    if footprint_sets is not None and not isinstance(footprint_sets, Mapping):
        footprint_sets = {f.set_id: f for f in footprint_sets}
    cid = spec.comparison_id
    report = ValidationReport(f"cohort {cid}")
    exclusions = ExclusionLog()

    # ---- inputs: select, then check keys before any join
    ext = _concat(extractions, EXTRACTIONS)
    side_keys = pd.MultiIndex.from_tuples(
        [(s.extraction_method, s.footprint_set_id) for s in spec.sides])
    in_side = pd.MultiIndex.from_frame(ext[["extraction_method", "footprint_set_id"]]).isin(
        side_keys)
    sel = ext[in_side & (ext["period"] == "day") & ext["model"].isin(spec.models)].copy()
    obs = _concat(observations, STATION_DAILY)
    if spec.sites is not None:
        sel = sel[sel["site_id"].isin(spec.sites)]
    sites = list(spec.sites) if spec.sites is not None else sorted(sel["site_id"].unique())
    obs = obs[obs["site_id"].isin(sites)]

    def in_period(frame: pd.DataFrame) -> pd.DataFrame:
        keep = pd.Series(True, index=frame.index)
        if spec.start:
            keep &= frame["date"] >= pd.Timestamp(spec.start)
        if spec.end:
            keep &= frame["date"] <= pd.Timestamp(spec.end)
        return frame[keep]

    sel, obs = in_period(sel), in_period(obs)
    check_primary_key(sel, EXTRACTIONS.primary_key, table=f"extractions ({cid})")
    check_primary_key(obs, STATION_DAILY.primary_key, table=f"station_daily ({cid})")
    for m in spec.models:
        if not (sel["model"] == m).any():
            report.warning("model_without_rows", f"model {m} has no rows on any declared side",
                           field=m)

    # ---- candidate units
    days = pd.concat([sel[["site_id", "date"]], obs[["site_id", "date"]]]).drop_duplicates()
    units = days.merge(pd.DataFrame({"model": list(spec.models)}), how="cross")
    units = units.sort_values(_UNIT, ignore_index=True)
    reasons: list[pd.Series] = []
    labels: list[str] = []

    # ---- side cells
    for i, side in enumerate(spec.sides):
        s = sel[(sel["extraction_method"] == side.extraction_method)
                & (sel["footprint_set_id"] == side.footprint_set_id)]
        m = units[_UNIT].merge(
            s[[*_UNIT, "et_mm", "fp_weight_retained_frac", "day_alignment"]],
            on=_UNIT, how="left", validate="one_to_one", indicator=True)
        code = np.where(m["_merge"] == "left_only", "extraction_missing",
                        np.where(m["et_mm"].isna(), "modeled_value_null", ""))
        thr = spec.min_retained_frac
        if thr is not None and side.footprint_set_id != "none":
            rf = m["fp_weight_retained_frac"]
            code = np.where(code != "", code,
                            np.where(rf.isna(), "retained_frac_unknown",
                                     np.where(rf < thr, "below_min_retained_frac", "")))
        units[f"_mod{i}"] = m["et_mm"].to_numpy()
        units[f"_align{i}"] = m["day_alignment"].to_numpy()
        reasons.append(pd.Series(code, index=units.index))
        labels.append(side.label)

    # ---- reference cells (one value per site/date, shared by every model)
    for v in spec.reference_variants:
        ref = reference_values(obs, v)
        m = units[["site_id", "date"]].merge(ref, on=["site_id", "date"], how="left",
                                             validate="many_to_one")
        units[f"_obs:{v}"] = m["observed_mm"].to_numpy()
        units[f"_prov:{v}"] = m["reference_provenance"].to_numpy()
        reasons.append(pd.Series(np.where(m["observed_mm"].isna(), "reference_unavailable", ""),
                                 index=units.index))
        labels.append(v)

    # ---- availability before cohort rules (for period completeness, step 8)
    model_avail = pd.concat([
        units[_UNIT].assign(extraction_method=side.extraction_method,
                            footprint_set_id=side.footprint_set_id,
                            available=units[f"_mod{i}"].notna())
        for i, side in enumerate(spec.sides)], ignore_index=True)
    first_model = units["model"] == spec.models[0]
    reference_avail = pd.concat([
        units.loc[first_model, ["site_id", "date"]].assign(
            reference_variant=v, reference_provenance=units.loc[first_model, f"_prov:{v}"])
        for v in spec.reference_variants], ignore_index=True)

    # ---- own eligibility, then freezing
    codes = np.column_stack([r.to_numpy(dtype=object) for r in reasons])
    failing = codes != ""
    own_ok = pd.Series(~failing.any(axis=1), index=units.index)
    first = codes[np.arange(len(codes)), failing.argmax(axis=1)] if len(codes) else codes
    units["reason_code"] = pd.Series(np.where(own_ok, None, first), index=units.index,
                                     dtype=object)
    units["detail"] = pd.Series([
        ";".join(f"{labels[j]}:{codes[r, j]}" for j in np.flatnonzero(failing[r])) or None
        for r in range(len(units))], index=units.index, dtype=object)
    if spec.freeze_models:
        units["cohort_id"] = f"{cid}:{COMMON}"
        all_ok = own_ok.groupby([units["site_id"], units["date"]]).transform("all")
        bad = (units[~own_ok].groupby(["site_id", "date"])["model"]
               .agg(lambda x: ",".join(sorted(x))).rename("_bad_models"))
        units = units.merge(bad, on=["site_id", "date"], how="left", validate="many_to_one")
        knock = (own_ok & ~all_ok).to_numpy()
        units.loc[knock, "reason_code"] = "not_all_models"
        units.loc[knock, "detail"] = "ineligible: " + units.loc[knock, "_bad_models"]
        included = all_ok.to_numpy()
    else:
        units["cohort_id"] = cid + ":" + units["model"]
        included = own_ok.to_numpy()
    units["included"] = included

    membership = CanonicalTable.build(
        units[["cohort_id", *_UNIT, "included", "reason_code", "detail"]].assign(
            comparison_id=cid)[COHORT_MEMBERSHIP.names],
        COHORT_MEMBERSHIP, {"comparison_id": cid}, source=cid)
    for r in units[~units["included"]].itertuples():
        exclusions.add(STAGE, format_key(cohort=r.cohort_id, site_id=r.site_id, date=r.date,
                                         model=r.model),
                       str(r.reason_code), severity="info", affected_comparison=cid,
                       detail=r.detail)

    # ---- paired rows: every kept unit once per side and reference variant
    kept = units[units["included"]]
    dep = {s.footprint_set_id: _dependence(s.footprint_set_id, footprint_sets)
           for s in spec.sides}
    for set_id, d in dep.items():
        if d != "no":
            report.warning("footprint_target_dependence",
                           f"{set_id}: footprint weights {'use' if d == 'yes' else 'may use'} "
                           "the target station measurements; this comparison is a sensitivity "
                           "experiment, not independent validation", field=set_id)
    rows = []
    for i, side in enumerate(spec.sides):
        for v in spec.reference_variants:
            flags = pd.Series("", index=kept.index)
            flags = flags.where(kept[f"_align{i}"] == "declared", "day_alignment=unverified")
            if dep[side.footprint_set_id] != "no":
                tag = f"footprint_target_dependence={dep[side.footprint_set_id]}"
                flags = flags.where(flags == "", flags + ";") + tag
            rows.append(pd.DataFrame({
                "comparison_id": cid, "cohort_id": kept["cohort_id"],
                "site_id": kept["site_id"], "date": kept["date"], "model": kept["model"],
                "reference_variant": v, "extraction_method": side.extraction_method,
                "footprint_set_id": side.footprint_set_id,
                "observed_mm": kept[f"_obs:{v}"], "modeled_mm": kept[f"_mod{i}"],
                "reference_provenance": kept[f"_prov:{v}"],
                "flags": flags.replace("", None),
            }))
    paired_frame = pd.concat(rows, ignore_index=True) if len(kept) else PAIRED.empty()

    digest = hashlib.sha256("\n".join(
        f"{r.cohort_id}|{r.site_id}|{r.date.date().isoformat()}|{r.model}"
        for r in kept.sort_values(["cohort_id", *_UNIT]).itertuples()).encode()).hexdigest()
    attributes: dict[str, Any] = {
        "workflow": "etcompare.cohorts.build_cohort", "comparison": to_plain(spec),
        "kind": spec.kind, "ranking_scope": spec.ranking_scope,
        "freeze_models": spec.freeze_models, "membership_sha256": digest,
        "footprint_target_dependence": dep,
    }
    paired = CanonicalTable.build(paired_frame, PAIRED, attributes, source=cid)

    for cohort_id, g in units.groupby("cohort_id"):
        k = g[g["included"]]
        n_days = k[["site_id", "date"]].drop_duplicates().shape[0]
        report.info("cohort_size", f"{cohort_id}: {n_days} site-days, "
                    f"{k['site_id'].nunique()} site(s), {len(k)} site/date/model units of "
                    f"{len(g)} candidates", n_rows=len(k))
    if len(paired_frame):
        n_filled = paired_frame.loc[paired_frame["reference_provenance"] == "corrected_filled",
                                    ["site_id", "date"]].drop_duplicates().shape[0]
        if n_filled:
            report.info("reference_filled_days",
                        f"{n_filled} site-days use gap-filled reference ET (corrected_filled); "
                        "report them separately, not as independent validation (D-005)",
                        n_rows=n_filled)
    status = "ok" if len(kept) else "empty"
    if status == "empty":
        report.warning("empty_cohort", "no candidate unit satisfies the cohort rule")
    return Cohort(spec, status, paired, membership, tuple(report.issues),
                  tuple(exclusions.records), model_avail, reference_avail, attributes)
