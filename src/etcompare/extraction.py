"""Raster extraction workflow for one station (guide step 5; needs the ``geo`` extra).

For each day with model rasters for the station:

1. The day's footprint, if one exists, is read once and validated
   (:mod:`etcompare.footprints`). A rejected footprint becomes one
   exclusion record and suppresses only the footprint methods.
2. Each model raster is read once, in a bounded window covering the
   footprint grid and the tower pixel block.
3. Every declared method is computed (see
   :class:`~etcompare.config.RasterExtractionSpec`):

   ============================================  ==================================
   method id                                     meaning
   ============================================  ==================================
   ``footprint_conservative``                    footprint contribution moved onto
                                                 the native model grid; per-model
                                                 valid cells
   ``footprint_conservative_common_support``     same, restricted to cells valid in
                                                 every model present that day
   ``pixel_window_<n>x<n>``                      unweighted mean of whole pixels
   ``nearest_tower_pixel``                       pixel containing the tower
   ============================================  ==================================

Rows whose value is undefined are kept with ``et_mm`` null, and an exclusion
record gives the reason. Transfer matrices are computed once per grid pair
and reused across days.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from etcompare.config import (
    FootprintSetSpec,
    ProductSpec,
    RasterExtractionSpec,
    StationSpec,
    to_plain,
)
from etcompare.errors import (
    ContractError,
    EtcompareError,
    GridError,
    InputMissingError,
    SystematicFailureError,
)
from etcompare.footprints import PreparedFootprint, prepare_footprint
from etcompare.io.rasters import read_footprint, read_header, read_model_window
from etcompare.log import get_logger, log_event
from etcompare.provenance import package_versions
from etcompare.resilience import Checkpoint, RetryPolicy, beat, call_with_retries, rest
from etcompare.results import AdapterResult, ExclusionLog, format_key
from etcompare.schemas import EXTRACTIONS, CanonicalTable
from etcompare.spatial import (
    Grid,
    Transfer,
    Window,
    block_mean,
    transfer_matrix,
    transform_coords,
    weighted_extract,
    window_around_point,
    window_covering,
)
from etcompare.validation import ValidationReport

STAGE = "raster_extraction"
FOOTPRINT = "footprint_conservative"
FOOTPRINT_COMMON = "footprint_conservative_common_support"
NEAREST = "nearest_tower_pixel"
_log = get_logger(__name__)


def pixel_window_id(n: int) -> str:
    return f"pixel_window_{n}x{n}"


@dataclass(frozen=True)
class _Plan:
    """What to read from one model grid, and how footprints map onto it."""

    window: Window | None
    grid: Grid | None  # window grid
    transfer: Transfer | None
    tower_xy: tuple[float, float] | None


def _make_plan(model_grid: Grid, fp_grid: Grid | None, station: StationSpec, half: int | None,
               report: ValidationReport) -> _Plan:
    win: Window | None = None
    if fp_grid is not None:
        win = window_covering(fp_grid, model_grid)
        if win is None:
            report.warning("footprint_outside_model_raster",
                           f"footprint grid {fp_grid.grid_id} does not overlap model grid "
                           f"{model_grid.grid_id}")
    tower = None
    if half is not None:
        tx, ty = transform_coords(np.array([station.longitude]), np.array([station.latitude]),
                                  "EPSG:4326", model_grid.crs)  # type: ignore[arg-type]
        tower = (float(tx[0]), float(ty[0]))
        wp = window_around_point(model_grid, tower[0], tower[1], half)
        if wp is None:
            report.warning("tower_outside_model_raster",
                           f"station {station.site_id} lies outside model grid "
                           f"{model_grid.grid_id}")
        else:
            win = wp if win is None else win.union(wp)
    if win is None:
        return _Plan(None, None, None, tower)
    wgrid = model_grid.window(win.row_off, win.col_off, win.height, win.width)
    transfer = None
    if fp_grid is not None and window_covering(fp_grid, model_grid) is not None:
        transfer = transfer_matrix(fp_grid, wgrid)
        cov = transfer.coverage()
        full = int((cov >= 1 - 1e-9).sum())
        partial = int(((cov > 0) & (cov < 1 - 1e-9)).sum())
        report.info("transfer_built",
                    f"{transfer.method} {fp_grid.grid_id} -> {wgrid.grid_id}: "
                    f"{full} footprint cells fully inside the model window, {partial} partly, "
                    f"{fp_grid.size - full - partial} outside; max row sum "
                    f"{cov.max():.12f}")
    return _Plan(win, wgrid, transfer, tower)


def _day_alignment(product: ProductSpec) -> str:
    return "declared" if product.day_definition != "unknown" else "unverified"


def extract_rasters(
    spec: RasterExtractionSpec,
    *,
    station: StationSpec,
    model_manifest: CanonicalTable,
    products: Mapping[str, ProductSpec],
    footprint_manifest: CanonicalTable | None = None,
    footprint_set: FootprintSetSpec | None = None,
    start: str | pd.Timestamp | None = None,
    end: str | pd.Timestamp | None = None,
    strict: bool = False,
    retry: RetryPolicy | None = None,
    max_unreadable_fraction: float | None = None,
    checkpoint: Checkpoint | None = None,
) -> AdapterResult:
    """Extract model ET at one station with every method in ``spec``.

    ``start``/``end`` override the period declared in ``spec`` (inclusive labels).

    ``strict=True`` raises on the first unreadable raster. Otherwise the file is
    recorded as an ``unreadable_raster`` exclusion (``raster_missing`` when it no
    longer exists; ``unreadable_footprint`` / ``footprint_file_missing`` for
    footprints), counted, and its model-day keeps null rows for every method, so
    coverage shows it. Contract violations (undeclared scaling, missing coordinates,
    missing CRS) always raise.

    Step 18 (none of these changes a value):

    - ``retry``: transient read errors are retried
      (:func:`~etcompare.resilience.call_with_retries`); default: one attempt.
    - Skipping stops being safe when every file of one model, every model raster or
      every footprint fails, or (with ``max_unreadable_fraction``) when more than that
      fraction of either fails: :class:`~etcompare.errors.SystematicFailureError` is
      raised, carrying the report and exclusions.
    - ``checkpoint``: progress is saved after each calendar month and restored when the
      checkpoint was written for the same cache key, dates and spec; the result equals
      an uninterrupted run (frame and attributes).
    """
    site = station.site_id
    if spec.site_id != site:
        raise ContractError(f"extraction {spec.name} is for {spec.site_id}, not {site}")
    methods = set(spec.methods)
    uses_fp = bool({"footprint", "footprint_common_support"} & methods)
    sizes = sorted({*(spec.window_sizes if "pixel_window" in methods else ()),
                    *((1,) if "nearest_tower_pixel" in methods else ())})
    if uses_fp:
        if footprint_manifest is None or footprint_set is None:
            raise ContractError(f"extraction {spec.name}: footprint methods need a footprint "
                                "manifest and its footprint set declaration")
        if footprint_set.set_id != spec.footprint_set_id:
            raise ContractError(f"extraction {spec.name} declares footprint set "
                                f"{spec.footprint_set_id}, got {footprint_set.set_id}")
    if sizes and (station.latitude is None or station.longitude is None):
        raise ContractError(f"station {site}: coordinates needed for pixel methods",
                            config_key=f"stations.{site}.latitude")

    mm = model_manifest.frame
    members = mm["member_sites"].str.split(";")
    assets = mm[members.map(lambda s: site in s) & (mm["period"] == "day")]
    if spec.models is not None:
        assets = assets[assets["model"].isin(spec.models)]
    start = start if start is not None else spec.start
    end = end if end is not None else spec.end
    if start is not None:
        assets = assets[assets["date"] >= pd.Timestamp(start)]
    if end is not None:
        assets = assets[assets["date"] <= pd.Timestamp(end)]
    for model in sorted(assets["model"].unique()):
        if model not in products:
            raise ContractError(f"model {model} has no product declaration",
                                config_key=f"products.{model}")
        products[model].require_scaling()

    fps: dict[pd.Timestamp, Any] = {}
    if uses_fp:
        fm = footprint_manifest.frame  # type: ignore[union-attr]
        fm = fm[(fm["footprint_set_id"] == footprint_set.set_id) & (fm["site_id"] == site)  # type: ignore[union-attr]
                & (fm["period"] == "day")]
        fps = {r.date: r for r in fm.itertuples()}

    try:
        return _extract(spec, site, station, assets, products, footprint_set, fps, methods,
                        uses_fp, sizes, strict, retry, max_unreadable_fraction, checkpoint)
    finally:
        rest()  # raster reading ended: no stall watch during analyses


def _extract(spec: RasterExtractionSpec, site: str, station: StationSpec, assets: pd.DataFrame,
             products: Mapping[str, ProductSpec], footprint_set: FootprintSetSpec | None,
             fps: dict[pd.Timestamp, Any], methods: set[str], uses_fp: bool, sizes: list[int],
             strict: bool, retry: RetryPolicy | None, max_unreadable_fraction: float | None,
             checkpoint: Checkpoint | None) -> AdapterResult:
    report = ValidationReport(f"raster extraction {spec.name} ({site})")
    exclusions = ExclusionLog()
    plans: dict[tuple[str, str | None], _Plan] = {}
    #: (transfer method, window grid id) per plan key, in order of first use (attributes).
    plan_info: dict[tuple[str, str | None], tuple[str | None, str | None]] = {}
    known_grid: Grid | None = None
    half = (max(sizes) // 2) if sizes else None
    rows: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    #: Files attempted and files unreadable or missing, per model and for footprints.
    tried: dict[str, int] = {}
    failed: dict[str, int] = {}
    fp_set_id = footprint_set.set_id if footprint_set else "none"
    fp_method = footprint_set.weighting_method if footprint_set else "none"

    def bump(key: str) -> None:
        counts[key] = counts.get(key, 0) + 1

    def read(fn: Any, path: str, what: str) -> Any:
        return call_with_retries(fn, path, policy=retry, what=what)

    def plan_for(grid: Grid, fp_grid: Grid | None) -> _Plan:
        key = (grid.grid_id, fp_grid.grid_id if fp_grid is not None else None)
        if key not in plans:
            # A plan restored from a checkpoint already reported its transfer.
            plans[key] = _make_plan(grid, fp_grid, station, half,
                                    ValidationReport("restored") if key in plan_info
                                    else report)
            p = plans[key]
            plan_info.setdefault(key, (p.transfer.method if p.transfer else None,
                                       p.grid.grid_id if p.grid else None))
        return plans[key]

    def emit(asset: Any, method: str, value: float, *, status: str, fp_row: Any = None,
             wsum: float | None = None, retained: float | None = None, in_extent: float | None
             = None, denom: str | None = None, support: str | None = None,
             ncells: int | None = None, grid: Grid | None = None, record: bool = True) -> None:
        footprint_based = method in (FOOTPRINT, FOOTPRINT_COMMON)
        rows.append({
            "site_id": site, "date": asset.date, "period": "day", "model": asset.model,
            "extraction_method": method,
            "footprint_set_id": fp_set_id if footprint_based else "none",
            "footprint_method": fp_method if footprint_based else "none",
            "et_mm": value if status == "ok" else math.nan,
            "fp_weight_sum": wsum, "fp_weight_retained_frac": retained,
            "fp_weight_retained_denominator": denom, "support_id": support,
            "fp_frac_in_model_extent": in_extent,
            "fp_domain_capture_frac": (getattr(fp_row, "domain_capture_frac", None)
                                       if footprint_based and fp_row is not None else None),
            "n_cells_valid": ncells, "model_grid_id": grid.grid_id if grid else None,
            "day_alignment": _day_alignment(products[asset.model]),
            "model_asset_id": asset.asset_id,
            "footprint_asset_id": fp_row.asset_id if footprint_based and fp_row is not None
            else None,
            "source_path": asset.path,
        })
        bump(f"{method}:{status}")
        if status != "ok" and record:
            exclusions.add(STAGE, format_key(site_id=site, date=asset.date, model=asset.model,
                                             method=method), status, severity="warning")

    def method_ids(fp_ok: bool) -> list[str]:
        return ([FOOTPRINT] if "footprint" in methods and fp_ok else []) + \
            ([FOOTPRINT_COMMON] if "footprint_common_support" in methods and fp_ok
             else []) + \
            ([NEAREST] if "nearest_tower_pixel" in methods else []) + \
            ([pixel_window_id(n) for n in spec.window_sizes]
             if "pixel_window" in methods else [])

    dates = [pd.Timestamp(d) for d in sorted(assets["date"].unique())]
    start_at = 0
    if checkpoint is not None:
        state = checkpoint.load()
        if state is not None and state.get("dates") == dates and state.get("spec") == \
                to_plain(spec):
            rows.extend(state["rows"])
            exclusions.records.extend(state["exclusions"])
            counts.update(state["counts"])
            plan_info.update(state["plan_info"])
            report.issues.extend(state["issues"])
            tried.update(state["tried"])
            failed.update(state["failed"])
            start_at = int(state["n_done"])
            log_event(_log, "extraction_resumed", site_id=site, unit=spec.name,
                      days_done=start_at, days_total=len(dates), n_rows=len(rows))

    for n_day, date in enumerate(dates[start_at:], start_at + 1):
        group = assets[assets["date"] == date]
        fp_row = fps.get(date)
        prepared: PreparedFootprint | None = None
        if uses_fp:
            if fp_row is None:
                exclusions.add(STAGE, format_key(site_id=site, date=date), "footprint_missing",
                               severity="info", detail=f"no daily footprint in {fp_set_id}")
                bump("footprint_missing")
            else:
                tried["footprint"] = tried.get("footprint", 0) + 1
                try:
                    raw, fgrid, fnodata = read(lambda p=fp_row.path: read_footprint(p),
                                               fp_row.path, "footprint")
                    prepared = prepare_footprint(
                        raw, fgrid, value_semantics=footprint_set.value_semantics,  # type: ignore[union-attr]
                        nodata=fnodata, negative_tolerance=spec.negative_weight_tolerance)
                except (OSError, EtcompareError) as exc:
                    if strict or isinstance(exc, GridError):
                        raise
                    code = ("footprint_file_missing" if isinstance(exc, InputMissingError)
                            else "unreadable_footprint")
                    exclusions.add(STAGE, fp_row.path, code, severity="error",
                                   detail=str(exc))
                    bump(code)
                    failed["footprint"] = failed.get("footprint", 0) + 1
                    prepared = None
                beat(fp_row.path)
                if prepared is not None and not prepared.ok:
                    exclusions.add(STAGE, format_key(site_id=site, date=date),
                                   f"footprint_{prepared.status}", detail=prepared.detail())
                    bump(f"footprint_{prepared.status}")
                elif prepared is not None and prepared.negatives_zeroed:
                    exclusions.add(STAGE, format_key(site_id=site, date=date),
                                   "footprint_roundoff_negatives_zeroed", severity="info",
                                   detail=prepared.detail())
                    bump("footprint_roundoff_negatives_zeroed")

        fp_ok = prepared is not None and prepared.ok
        fp_grid = prepared.grid if fp_ok else None  # type: ignore[union-attr]
        loaded: list[tuple[Any, np.ndarray, _Plan, np.ndarray | None]] = []
        for asset in group.itertuples():
            product = products[asset.model]
            tried[asset.model] = tried.get(asset.model, 0) + 1
            try:
                if known_grid is None:
                    known_grid = read(lambda p=asset.path: read_header(p).grid, asset.path,
                                      "raster_header")
                plan = plan_for(known_grid, fp_grid)
                if plan.window is None:
                    # The cached grid is the previous raster's: check this raster's own grid
                    # before declaring that it does not overlap (D-072).
                    own = read(lambda p=asset.path: read_header(p).grid, asset.path,
                               "raster_header")
                    if own.grid_id != known_grid.grid_id:
                        known_grid = own
                        plan = plan_for(known_grid, fp_grid)
                if plan.window is None:
                    for mid in method_ids(fp_ok):
                        emit(asset, mid, math.nan, status="no_overlap", fp_row=fp_row)
                    beat(asset.path)
                    continue
                try:
                    values, _ = read(lambda p=asset.path, w=plan.window, g=known_grid, pr=product:
                                     read_model_window(p, pr, w, expect_grid=g),
                                     asset.path, "raster_window")
                except GridError:
                    # A raster on a different grid: re-plan for its own grid.
                    known_grid = read(lambda p=asset.path: read_header(p).grid, asset.path,
                                      "raster_header")
                    plan = plan_for(known_grid, fp_grid)
                    values, _ = read(lambda p=asset.path, w=plan.window, g=known_grid, pr=product:
                                     read_model_window(p, pr, w, expect_grid=g),
                                     asset.path, "raster_window")
            except ContractError:
                raise
            except Exception as exc:  # OSError, etcompare errors, rasterio.errors.*
                if strict or not (isinstance(exc, OSError | EtcompareError)
                                  or type(exc).__module__.split(".")[0] == "rasterio"):
                    raise
                code = ("raster_missing" if isinstance(exc, InputMissingError)
                        else "unreadable_raster")
                exclusions.add(STAGE, asset.path, code, severity="error", detail=str(exc))
                bump(code)
                failed[asset.model] = failed.get(asset.model, 0) + 1
                # Null rows keep the model-day visible in coverage; the file-level
                # exclusion above says why.
                for mid in method_ids(fp_ok):
                    emit(asset, mid, math.nan, status=code, fp_row=fp_row, record=False)
                beat(asset.path)
                continue
            beat(asset.path)

            contrib = None
            if fp_ok and plan.transfer is not None:
                contrib = plan.transfer.apply(prepared.contribution)  # type: ignore[union-attr,arg-type]
            loaded.append((asset, values, plan, contrib))

            if "footprint" in methods and fp_row is not None and prepared is not None and fp_ok:
                if contrib is None:
                    emit(asset, FOOTPRINT, math.nan, status="no_overlap", fp_row=fp_row)
                else:
                    r = weighted_extract(contrib, values, weight_total=prepared.weight_total,
                                         grid=plan.grid, support_prefix="model:")
                    emit(asset, FOOTPRINT, r.value, status=r.status, fp_row=fp_row,
                         wsum=r.weight_total, retained=r.retained_frac,
                         in_extent=r.in_extent_frac, denom=prepared.denominator,
                         support=r.support_id, ncells=r.n_cells_valid, grid=plan.grid)
            for n in sizes:
                if plan.tower_xy is None:
                    continue
                b = block_mean(values, plan.grid, plan.tower_xy[0], plan.tower_xy[1], n)  # type: ignore[arg-type]
                method = NEAREST if n == 1 and "nearest_tower_pixel" in methods else None
                for mid in ([method] if method else []) + (
                        [pixel_window_id(n)] if "pixel_window" in methods
                        and n in spec.window_sizes else []):
                    emit(asset, mid, b.value, status=b.status, support=b.support_id,
                         ncells=b.n_cells_valid, grid=plan.grid)

        if "footprint_common_support" in methods and fp_ok and loaded:
            usable = [x for x in loaded if x[3] is not None]
            grids = {x[2].grid.grid_id for x in usable}  # type: ignore[union-attr]
            if len(grids) > 1:
                for asset, *_ in usable:
                    emit(asset, FOOTPRINT_COMMON, math.nan, status="grids_differ",
                         fp_row=fp_row)
            elif usable:
                common = np.all(np.stack([np.isfinite(v) for _, v, _, _ in usable]), axis=0)
                prefix = f"common{len(usable)}:"
                for asset, values, plan, contrib in usable:
                    r = weighted_extract(contrib, values,  # type: ignore[arg-type]
                                         weight_total=prepared.weight_total,  # type: ignore[union-attr]
                                         grid=plan.grid, extra_mask=common,
                                         support_prefix=prefix)
                    emit(asset, FOOTPRINT_COMMON, r.value, status=r.status, fp_row=fp_row,
                         wsum=r.weight_total, retained=r.retained_frac,
                         in_extent=r.in_extent_frac, denom=prepared.denominator,  # type: ignore[union-attr]
                         support=r.support_id, ncells=r.n_cells_valid, grid=plan.grid)
        if n_day % 50 == 0:
            log_event(_log, "extraction_progress", site_id=site, days_done=n_day,
                      days_total=len(dates), n_rows=len(rows))
        if checkpoint is not None and n_day < len(dates) and (
                dates[n_day].to_period("M") != date.to_period("M")):
            checkpoint.save({"dates": dates, "spec": to_plain(spec), "n_done": n_day,
                             "rows": rows, "exclusions": exclusions.records,
                             "counts": counts, "plan_info": plan_info,
                             "issues": report.issues, "tried": tried, "failed": failed},
                            through=date.date().isoformat(), n_rows=len(rows))

    report.info("extraction_counts", f"{counts}")
    for code in ("unreadable_raster", "raster_missing", "unreadable_footprint",
                 "footprint_file_missing"):
        if counts.get(code):
            report.warning(code, f"{counts[code]} file(s) could not be read and were skipped; "
                           "coverage reflects this", n_rows=counts[code])
    _check_systematic(spec.name, tried, failed, max_unreadable_fraction, report, exclusions)
    frame = pd.DataFrame(rows, columns=EXTRACTIONS.names) if rows else EXTRACTIONS.empty()
    frame = frame.sort_values(["site_id", "model", "extraction_method", "date"],
                              kind="stable").reset_index(drop=True)
    attributes: dict[str, Any] = {
        "workflow": "etcompare.extraction.extract_rasters",
        "spec": to_plain(spec), "station": to_plain(station),
        "footprint_set": to_plain(footprint_set) if footprint_set else None,
        "transfers": {f"{k[0]}<-{k[1]}": v[0] for k, v in plan_info.items()},
        "window_grids": {f"{k[0]}<-{k[1]}": v[1] for k, v in plan_info.items()},
        "counts": counts,
        "package_versions": package_versions("etcompare", "numpy", "pandas", "scipy",
                                             "rasterio", "pyproj", "shapely"),
    }
    table = CanonicalTable.build(frame, EXTRACTIONS, attributes, source=spec.name)
    log_event(_log, "raster_extraction_done", site_id=site, n_days=len(dates),
              n_rows=len(table), **{k.replace(":", "_"): v for k, v in counts.items()})
    return AdapterResult(table, report, exclusions)


def _check_systematic(name: str, tried: Mapping[str, int], failed: Mapping[str, int],
                      max_fraction: float | None, report: ValidationReport,
                      exclusions: ExclusionLog) -> None:
    """Raise :class:`SystematicFailureError` when skipping would hide a systematic fault.

    Always: every file of one model, every model raster, or every footprint unreadable or
    missing. With ``max_fraction``: more than that fraction of the model rasters, or of
    the footprints, attempted.
    """
    problems = []
    models = {m: n for m, n in tried.items() if m != "footprint"}
    n_models, bad_models = sum(models.values()), sum(failed.get(m, 0) for m in models)
    for m, n in sorted(models.items()):
        if n and failed.get(m, 0) == n:
            problems.append(f"every {m} raster ({n}) is unreadable or missing")
    groups = [("model rasters", n_models, bad_models),
              ("footprints", tried.get("footprint", 0), failed.get("footprint", 0))]
    for what, n, bad in groups:
        if n and bad == n:
            problems.append(f"every one of the {n} {what} is unreadable or missing")
        elif n and max_fraction is not None and bad / n > max_fraction:
            problems.append(f"{bad} of {n} {what} ({bad / n:.1%}) are unreadable or missing, "
                            f"above processing.max_unreadable_fraction = {max_fraction:g}")
    if problems:
        msg = (f"raster extraction {name}: " + "; ".join(problems) + ". This looks "
               "systematic (wrong path, format or drive), so the run stops instead of "
               "skipping; the exclusions list every file.")
        report.error("systematic_read_failure", msg)
        err = SystematicFailureError(msg, details={
            "files_tried": dict(tried), "files_failed": dict(failed)})
        err.report = report
        err.exclusions = exclusions  # type: ignore[attr-defined]
        raise err
