"""File discovery and asset manifests (guide step 4).

Discovery walks directories with :mod:`pathlib`, parses filenames and returns
a manifest. It never opens a raster, so ``crs``, scale and nodata stay null
(``scaling_source = "not_read"``) until bounded raster access (step 5) reads
the headers. An explicit manifest file is the preferred reproducible input.

Duplicates are never averaged or silently dropped:

- ``duplicate_asset``: the same asset ID (filename stem) at two paths.
- ``conflicting_assets``: two different assets for the same site, model and day.

With ``on_conflict="error"`` (default) either raises
:class:`~etcompare.errors.DuplicateAssetError`. With ``"report"`` the
conflicting candidates are held out of the manifest, listed in
``result.conflicts`` and the report carries an error until
:func:`select_assets` records an explicit choice.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

import pandas as pd

from etcompare.adapters.filenames import (
    DEFAULT_EXTENSIONS,
    parse_footprint_filename,
    parse_openet_filename,
)
from etcompare.config import FootprintSetSpec, ProductSpec, to_plain
from etcompare.errors import DuplicateAssetError, EtcompareError, FilenameParseError, SchemaError
from etcompare.io.tables import read_any, read_metadata, read_table
from etcompare.log import get_logger, log_event
from etcompare.registry import ModelRegistry, SiteRegistry
from etcompare.results import ExclusionLog
from etcompare.schemas import (
    ASSET_SITES,
    FOOTPRINT_MANIFEST,
    MODEL_MANIFEST,
    CanonicalTable,
)
from etcompare.validation import ValidationReport

OnUnparseable = Literal["error", "skip"]
OnConflict = Literal["error", "report"]

_log = get_logger(__name__)
_CONFLICT_COLUMNS = ["conflict_type", "conflict_key", "asset_id", "path"]


@dataclass(frozen=True)
class DiscoveryResult:
    """Manifest of accepted assets plus everything held out and why.

    ``candidates`` keeps every parsed row (including conflicting ones) so that
    :func:`select_assets` can resolve conflicts without re-scanning.
    """

    manifest: CanonicalTable
    associations: CanonicalTable | None
    conflicts: pd.DataFrame
    report: ValidationReport
    exclusions: ExclusionLog
    candidates: pd.DataFrame = field(repr=False)


def list_files(
    root: str | os.PathLike[str], *, recursive: bool, extensions: Sequence[str]
) -> list[Path]:
    """Sorted files under ``root`` whose names end with one of ``extensions``."""
    root = Path(root)
    if not root.is_dir():
        raise EtcompareError(f"discovery root {root} is not a directory")
    exts = tuple(e.lower() for e in extensions)
    it = root.rglob("*") if recursive else root.iterdir()
    return sorted(p for p in it if p.name.lower().endswith(exts) and p.is_file())


def _find_conflicts(rows: pd.DataFrame, id_cols: list[str], key_cols: list[str]) -> pd.DataFrame:
    """Rows sharing an asset ID at different paths, or sharing a site/model/day key."""
    out = []
    if rows.empty:
        return pd.DataFrame(columns=_CONFLICT_COLUMNS)
    dup_id = rows.duplicated(id_cols, keep=False)
    for _, g in rows[dup_id].groupby(id_cols, sort=True):
        key = "|".join(f"{c}={g.iloc[0][c]}" for c in id_cols)
        out += [("duplicate_asset", key, r.asset_id, r.path) for r in g.itertuples()]
    if key_cols:
        ex = rows.assign(_site=rows["member_sites"].str.split(";")).explode("_site") \
            if "member_sites" in rows else rows.assign(_site=rows["site_id"])
        kc = ["_site", *key_cols]
        distinct = ex.drop_duplicates([*kc, "asset_id"])
        clash = distinct.duplicated(kc, keep=False)
        for _, g in distinct[clash].groupby(kc, sort=True):
            vals = g.iloc[0]
            key = f"site_id={vals['_site']}|" + "|".join(
                f"{c}={pd.Timestamp(vals[c]).date() if c == 'date' else vals[c]}"
                for c in key_cols)
            out += [("conflicting_assets", key, r.asset_id, r.path) for r in g.itertuples()]
    return pd.DataFrame(out, columns=_CONFLICT_COLUMNS).drop_duplicates()


def _finish(
    rows: pd.DataFrame,
    *,
    schema: Any,
    id_cols: list[str],
    key_cols: list[str],
    on_conflict: OnConflict,
    report: ValidationReport,
    exclusions: ExclusionLog,
    attributes: Mapping[str, Any],
    with_associations: bool,
) -> DiscoveryResult:
    conflicts = _find_conflicts(rows, id_cols, key_cols)
    if not conflicts.empty:
        summary = conflicts.groupby("conflict_type")["conflict_key"].nunique().to_dict()
        examples = conflicts.drop_duplicates("conflict_key").head(3)["conflict_key"].tolist()
        msg = (f"{summary} -- duplicates need an explicit selection (select_assets) or a "
               f"mosaicking policy; they are never averaged. e.g. {examples}")
        if on_conflict == "error":
            raise DuplicateAssetError(f"{report.subject}: {msg}")
        report.error("unresolved_asset_conflicts", msg, n_rows=len(conflicts))
        for r in conflicts.itertuples():
            exclusions.add("discovery", r.path, "unresolved_" + r.conflict_type,
                           severity="error", detail=r.conflict_key)
    held = set(conflicts["path"])
    accepted = rows[~rows["path"].isin(held)]
    manifest = CanonicalTable.build(accepted.reset_index(drop=True), schema, attributes)
    assoc = None
    if with_associations:
        a = manifest.frame[["asset_id", "member_sites", "model", "date"]].copy()
        a["site_id"] = a.pop("member_sites").str.split(";")
        a = a.explode("site_id")[["asset_id", "site_id", "model", "date"]]
        assoc = CanonicalTable.build(a.reset_index(drop=True), ASSET_SITES)
    return DiscoveryResult(manifest, assoc, conflicts.reset_index(drop=True), report, exclusions,
                           rows.reset_index(drop=True))


def _product_fields(model: str, products: Mapping[str, ProductSpec] | None) -> dict[str, Any]:
    p = products.get(model) if products is not None else None
    if p is None:
        return {"product_version": "unknown", "temporal_support": None, "day_definition": None,
                "units": None}
    return {"product_version": p.version, "temporal_support": p.temporal_support,
            "day_definition": p.day_definition, "units": p.units}


def openet_manifest_from_paths(
    paths: Iterable[str | os.PathLike[str]],
    *,
    models: ModelRegistry,
    sites: SiteRegistry | None = None,
    products: Mapping[str, ProductSpec] | None = None,
    on_unparseable: OnUnparseable = "error",
    on_conflict: OnConflict = "error",
    subject: str = "openet rasters",
    attributes: Mapping[str, Any] | None = None,
) -> DiscoveryResult:
    """Build a model manifest from an explicit list of raster paths."""
    report = ValidationReport(subject)
    exclusions = ExclusionLog()
    rows, failures = [], []
    for p in paths:
        p = Path(p)
        try:
            a = parse_openet_filename(p, models=models, sites=sites)
        except FilenameParseError as exc:
            failures.append((p, str(exc)))
            continue
        rows.append({
            "asset_id": a.asset_id, "path": str(p), "model": a.model, "variable": a.variable,
            "date": pd.Timestamp(a.date), "period": "day", "member_sites": ";".join(a.sites),
            "n_sites": len(a.sites),
            "site_aliases_applied": ";".join(f"{k}={v}" for k, v in a.site_aliases_applied)
            or None,
            **_product_fields(a.model, products),
            "scale_factor": None, "add_offset": None, "nodata": None,
            "scaling_source": "not_read", "crs": None,
        })
    if failures:
        if on_unparseable == "error":
            shown = "\n  ".join(m for _, m in failures[:10])
            raise FilenameParseError(f"{len(failures)} unparseable filename(s):\n  {shown}")
        report.warning("unparseable_filenames", f"{len(failures)} file(s) skipped",
                       n_rows=len(failures))
        for p, m in failures:
            exclusions.add("discovery", str(p), "unparseable_filename", detail=m)
    if products is None:
        report.warning("products_undeclared", "no product declarations supplied; units, "
                       "day_definition and temporal_support are null")
    else:
        missing = sorted({r["model"] for r in rows} - set(products))
        if missing:
            report.warning("products_undeclared", f"models {missing} have no product declaration")
    frame = pd.DataFrame(rows, columns=MODEL_MANIFEST.names)
    aliases = sorted({r["site_aliases_applied"] for r in rows if r["site_aliases_applied"]})
    attrs = {"site_aliases_applied": aliases, **(attributes or {})}
    result = _finish(frame, schema=MODEL_MANIFEST, id_cols=["asset_id"],
                     key_cols=["model", "date", "variable"], on_conflict=on_conflict,
                     report=report, exclusions=exclusions, attributes=attrs,
                     with_associations=True)
    log_event(_log, "openet_manifest_built", n_assets=len(result.manifest),
              n_unparseable=len(failures), n_conflicts=len(result.conflicts))
    return result


def discover_openet_rasters(
    root: str | os.PathLike[str],
    *,
    models: ModelRegistry,
    sites: SiteRegistry | None = None,
    products: Mapping[str, ProductSpec] | None = None,
    recursive: bool = True,
    extensions: Sequence[str] = DEFAULT_EXTENSIONS,
    on_unparseable: OnUnparseable = "error",
    on_conflict: OnConflict = "error",
) -> DiscoveryResult:
    """Index OpenET rasters under ``root`` (recursively by default)."""
    files = list_files(root, recursive=recursive, extensions=extensions)
    return openet_manifest_from_paths(
        files, models=models, sites=sites, products=products, on_unparseable=on_unparseable,
        on_conflict=on_conflict, subject=f"openet rasters under {root}",
        attributes={"root": str(root), "recursive": recursive},
    )


def discover_footprints(
    spec: FootprintSetSpec,
    *,
    sites: SiteRegistry | None = None,
    extensions: Sequence[str] = DEFAULT_EXTENSIONS,
    on_unparseable: OnUnparseable = "error",
    on_conflict: OnConflict = "error",
) -> DiscoveryResult:
    """Index one declared footprint set. Weighting and value semantics come from ``spec``."""
    report = ValidationReport(f"footprint set {spec.set_id}")
    exclusions = ExclusionLog()
    rows, failures, other_layers = [], [], 0
    for p in list_files(spec.root, recursive=spec.recursive, extensions=extensions):
        try:
            a = parse_footprint_filename(p, sites=sites)
        except FilenameParseError as exc:
            failures.append((p, str(exc)))
            continue
        if spec.layers is not None and a.layer_token not in spec.layers:
            other_layers += 1
            continue
        rows.append({
            "footprint_set_id": spec.set_id, "asset_id": a.asset_id, "path": str(p),
            "site_id": a.site_id, "date": pd.Timestamp(a.date), "period": a.period,
            "layer_token": a.layer_token, "weighting_method": spec.weighting_method,
            "value_semantics": spec.value_semantics, "domain_capture_frac": None,
            "temporal_coverage_frac": None,
        })
    if failures:
        if on_unparseable == "error":
            shown = "\n  ".join(m for _, m in failures[:10])
            raise FilenameParseError(f"{len(failures)} unparseable filename(s):\n  {shown}")
        report.warning("unparseable_filenames", f"{len(failures)} file(s) skipped",
                       n_rows=len(failures))
        for p, m in failures:
            exclusions.add("discovery", str(p), "unparseable_filename", detail=m)
    if other_layers:
        report.info("layers_not_selected", f"{other_layers} file(s) with layers outside "
                    f"{list(spec.layers or [])} ignored", n_rows=other_layers)
    frame = pd.DataFrame(rows, columns=FOOTPRINT_MANIFEST.names)
    if spec.domain_capture_csv is not None:
        frame = _attach_domain_capture(frame, spec, report)
    else:
        report.info("capture_unknown", "no domain-capture table declared; "
                    "domain_capture_frac is null")
    report.info("temporal_coverage_unknown", "temporal coverage per period is not available; "
                "temporal_coverage_frac is null")
    return _finish(frame, schema=FOOTPRINT_MANIFEST, id_cols=["footprint_set_id", "asset_id"],
                   key_cols=["period", "date", "layer_token"], on_conflict=on_conflict,
                   report=report, exclusions=exclusions,
                   attributes={"footprint_set": to_plain(spec)}, with_associations=False)


def _attach_domain_capture(
    frame: pd.DataFrame, spec: FootprintSetSpec, report: ValidationReport
) -> pd.DataFrame:
    """Join daily original-domain capture onto daily footprints by date label."""
    path = spec.domain_capture_csv
    col, dcol = spec.domain_capture_column, spec.domain_capture_date_column
    table = read_any(path)  # type: ignore[arg-type]
    missing = [c for c in (dcol, col) if c not in table.columns]
    if missing:
        raise SchemaError(f"{path}: column(s) {missing} not found")
    dates = pd.to_datetime(table[dcol], errors="coerce")
    if dates.isna().any() or (dates != dates.dt.normalize()).any():
        raise SchemaError(f"{path}: {dcol} must hold daily date labels")
    if dates.duplicated().any():
        raise SchemaError(f"{path}: duplicated dates in {dcol}")
    values = pd.to_numeric(table[col], errors="raise")
    if spec.domain_capture_units == "percent":
        values = values / 100.0
    capture = pd.Series(values.to_numpy(), index=dates.astype("datetime64[ns]").to_numpy())
    daily = frame["period"] == "day"
    out = frame.copy()
    out["domain_capture_frac"] = out["domain_capture_frac"].astype("float64")
    out.loc[daily, "domain_capture_frac"] = out.loc[daily, "date"].map(capture).to_numpy()
    source = f"{path.name}:{col}"  # type: ignore[union-attr]
    out["domain_capture_source"] = None
    out.loc[daily & out["domain_capture_frac"].notna(), "domain_capture_source"] = source
    n_attached = int(out["domain_capture_frac"].notna().sum())
    over = int((out["domain_capture_frac"] > 1).sum())
    report.info("domain_capture_attached", f"{source}: attached to {n_attached} of "
                f"{int(daily.sum())} daily footprints; {over} value(s) > 1 retained as "
                "reported (discretization)", n_rows=n_attached)
    return out


def select_assets(result: DiscoveryResult, keep_paths: Iterable[str | os.PathLike[str]]
                  ) -> DiscoveryResult:
    """Resolve conflicts by naming the path to keep in each conflicting group.

    Unselected candidates are recorded as ``not_selected`` exclusions. Raises
    :class:`DuplicateAssetError` if any group still has zero or several assets.
    """
    keep = {str(Path(p)) for p in keep_paths}
    cand = result.candidates
    conflict_paths = set(result.conflicts["path"])
    unknown = keep - conflict_paths
    if unknown:
        raise DuplicateAssetError(f"selected path(s) are not in any conflict: {sorted(unknown)}")
    for key, g in result.conflicts.groupby("conflict_key"):
        n = len(set(g["path"]) & keep)
        if n != 1:
            raise DuplicateAssetError(f"conflict {key}: select exactly one path, got {n}")
    dropped = conflict_paths - keep
    rows = cand[~cand["path"].isin(dropped)]
    exclusions = ExclusionLog()
    exclusions.add_many("discovery", sorted(dropped), "not_selected", severity="info",
                        detail="explicit selection among conflicting assets")
    report = ValidationReport(result.report.subject)
    report.extend(i for i in result.report if i.code != "unresolved_asset_conflicts")
    report.info("assets_selected", f"{len(keep)} asset(s) selected explicitly; "
                f"{len(dropped)} dropped", n_rows=len(dropped))
    schema = result.manifest.schema
    is_model = schema is MODEL_MANIFEST
    out = _finish(rows, schema=schema,
                  id_cols=["asset_id"] if is_model else ["footprint_set_id", "asset_id"],
                  key_cols=["model", "date", "variable"] if is_model
                  else ["period", "date", "layer_token"],
                  on_conflict="error", report=report, exclusions=exclusions,
                  attributes={**result.manifest.attributes,
                              "selected_paths": sorted(keep)},
                  with_associations=is_model)
    return replace(out, exclusions=_merge(result.exclusions, exclusions, dropped=conflict_paths))


def _merge(old: ExclusionLog, new: ExclusionLog, *, dropped: set[str]) -> ExclusionLog:
    merged = ExclusionLog([r for r in old.records
                           if not (r["reason_code"].startswith("unresolved_")
                                   and r["input_key"] in dropped)])
    merged.extend(new)
    return merged


def load_model_manifest(
    path: str | os.PathLike[str],
    *,
    check_paths: bool = True,
    on_conflict: OnConflict = "error",
) -> DiscoveryResult:
    """Load an explicit model manifest (CSV or Parquet) and validate it.

    ``check_paths`` verifies that each listed file exists; rasters are not opened.
    """
    path = Path(path)
    report = ValidationReport(f"model manifest {path}")
    if path.suffix.lower() in (".parquet", ".pq") and read_metadata(path) is not None:
        frame = read_table(path, MODEL_MANIFEST).frame
    else:
        frame = read_any(path, dtype=str, keep_default_na=False, na_values=[""]) \
            if path.suffix.lower() == ".csv" else read_any(path)
        frame = _cast_manifest_csv(frame)
    # Conformance (including the primary-key check) runs in _finish, after conflict
    # detection, so duplicated asset IDs are reported as conflicts rather than key errors.
    if check_paths:
        missing = [p for p in frame["path"] if not Path(p).is_file()]
        if missing:
            report.error("missing_files", f"{len(missing)} listed file(s) do not exist, e.g. "
                         f"{missing[:3]}", n_rows=len(missing))
    exclusions = ExclusionLog()
    result = _finish(frame, schema=MODEL_MANIFEST, id_cols=["asset_id"],
                     key_cols=["model", "date", "variable"], on_conflict=on_conflict,
                     report=report, exclusions=exclusions,
                     attributes={"manifest_path": str(path)}, with_associations=True)
    return result


def _cast_manifest_csv(frame: pd.DataFrame) -> pd.DataFrame:
    """Numeric columns of a CSV manifest read as text."""
    out = frame.copy()
    for c in MODEL_MANIFEST.columns:
        if c.name in out and c.kind in ("float", "int"):
            try:
                out[c.name] = pd.to_numeric(out[c.name])
            except (ValueError, TypeError) as exc:
                raise SchemaError(f"manifest column {c.name}: {exc}") from exc
    return out
