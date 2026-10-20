"""Declared workflow: validate, index, extract and analyze as cacheable units (guide step 17).

The command-line interface (:mod:`etcompare.cli`) and the ``tools/run_*.py`` scripts are
thin layers over :class:`Workflow`. It runs the library functions of steps 5–16 exactly
as declared in the configuration; no analysis is implemented here.

**Units.** Each unit is one library call that writes one group of tables, e.g. the metrics
of one comparison under one metric set (``metrics:<comparison>__<metric_set>``). Every
declared unit gets a status (:data:`etcompare.schemas.UNIT_STATUSES`):

- ``completed`` / ``cached``: ran now, or reused because its cache key is unchanged
  (:mod:`etcompare.runrecord`);
- ``skipped``: listed in ``run.skip``, not declared, or a unit it needs was skipped;
- ``not_available``: an optional input or a needed upstream output is missing; the
  reason is recorded, and nothing is filled in;
- ``failed``: it raised. The error stops the run (no retries; step 18).

A unit that needs an in-memory result of another (a ranking needs bootstrap replicates)
recomputes it when the other unit was reused, but never rewrites the other unit's
outputs. Units that read another stage's outputs (an analysis reading a raster
extraction) depend on those files by content: they run when the files exist, even if the
producing unit cannot run now (e.g. its rasters moved), and that unit's own status says
so. Output paths are those of the former ``tools/run_*.py`` scripts.

**Cache keys** hash the package source (without the presentation modules, which only the
report depends on), the configuration entries the unit reads and the
identities of its input files (station tables, extracted tables, upstream outputs by
SHA-256; rasters by ``cache.raster_identity``). Nothing else enters a key, so changing
one input, mask, grid or configuration entry invalidates exactly the units that read it.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import pandas as pd

from etcompare.config import (
    WORKFLOW_ANALYSES,
    BootstrapSpec,
    ComparisonSpec,
    Config,
    MetricSpec,
    PeriodAggregationSpec,
    RankingSpec,
    RepresentativenessSpec,
    to_plain,
)
from etcompare.errors import ContractError, EtcompareError
from etcompare.io.tables import read_table, write_table
from etcompare.log import get_logger, log_event
from etcompare.provenance import (
    PRESENTATION_MODULES,
    canonical_hash,
    code_identity,
    file_identity,
)
from etcompare.results import ExclusionLog
from etcompare.runrecord import Cache, UnitRecord, utc_now
from etcompare.schemas import CanonicalTable

_log = get_logger("workflow")

#: Stage of each analysis.
ANALYSIS_STAGE: dict[str, str] = {
    "footprint_aggregation": "extract", "raster_extraction": "extract",
    "footprint_comparison": "extract",
    **{a: "analyze" for a in WORKFLOW_ANALYSES if a not in (
        "footprint_aggregation", "raster_extraction", "footprint_comparison")},
}
_RASTER_SUFFIXES = (".tif", ".tiff", ".nc", ".npz", ".vrt", ".img")


@dataclass
class Outcome:
    """What a unit's run function returns: completed, or not_available with a reason."""

    status: str = "completed"
    reason_code: str | None = None
    reason: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class Unit:
    """One cacheable call. ``key`` returns plain data; ``None`` means never cached."""

    stage: str
    analysis: str
    unit_id: str
    key: Callable[[], Any]
    run: Callable[[], Outcome]
    #: (stage, unit_id) of units whose status gates this one.
    requires: tuple[tuple[str, str], ...] = ()
    #: Returns (reason_code, reason) when a needed input is missing.
    blocked: Callable[[], tuple[str, str] | None] | None = None


def _not_declared(analysis: str) -> UnitRecord:
    return UnitRecord(ANALYSIS_STAGE.get(analysis, "analyze"), analysis, analysis, "skipped",
                      "not_declared", f"no {analysis} declared in the configuration")


class Workflow:
    """Plans and executes the declared units of one configuration.

    ``force=True`` ignores valid cache stamps (outputs are rebuilt).
    """

    def __init__(self, cfg: Config, *, run_id: str, force: bool = False) -> None:
        if cfg.output_dir is None:
            raise ContractError("output_dir not declared", config_key="output_dir")
        self.cfg = cfg
        self.out: Path = Path(cfg.output_dir)
        self.run_id = run_id
        self.cache = Cache(self.out, enabled=cfg.cache.enabled and not force)
        self.records: dict[tuple[str, str], UnitRecord] = {}
        self.inputs: dict[str, dict[str, Any]] = {}
        self._outputs: list[str] = []
        self._memo: dict[Any, Any] = {}

    # ------------------------------------------------------------------ identities

    def ident(self, path: str | Path | None, kind: str | None = None) -> dict[str, Any]:
        """Identity of an input file (memoised for the run); rasters follow ``cache``."""
        if path is None:
            return {"path": None}
        p = Path(path)
        if kind is None:
            kind = (self.cfg.cache.raster_identity if p.suffix.lower() in _RASTER_SUFFIXES
                    else "sha256")
        try:
            mtime = p.stat().st_mtime_ns
        except OSError:
            mtime = None
        memo = ("ident", str(p), kind, mtime)
        if memo not in self._memo:
            self._memo[memo] = file_identity(p, kind)
            self.inputs[str(p)] = self._memo[memo]
        return dict(self._memo[memo])

    def idents(self, paths: Iterable[str | Path]) -> list[dict[str, Any]]:
        return [self.ident(p) for p in sorted(str(x) for x in paths)]

    def declared_paths(self, spec: Any) -> list[str]:
        """Every ``path``-like value of a spec's plain form (term tables, layers, polygons)."""
        found: list[str] = []

        def walk(x: Any, k: str = "") -> None:
            if isinstance(x, dict):
                for kk, v in x.items():
                    walk(v, kk)
            elif isinstance(x, list):
                for v in x:
                    walk(v, k)
            elif isinstance(x, str) and (k in ("path", "legend_csv", "reference_grid_raster")
                                         or k.endswith("_path")):
                found.append(x)

        walk(to_plain(spec))
        return sorted(set(found))

    # ------------------------------------------------------------------ outputs

    def save(self, table: CanonicalTable, path: Path, *, csv: bool = True) -> None:
        write_table(table, path)
        self._outputs.append(str(path))
        if csv:
            c = path.with_suffix(".csv")
            table.frame.to_csv(c, index=False)
            self._outputs.append(str(c))

    def save_csv(self, frame: pd.DataFrame, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(path, index=False)
        self._outputs.append(str(path))

    def save_text(self, text: str, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text.rstrip() + "\n", encoding="utf-8")
        self._outputs.append(str(path))

    def add_output(self, path: str | Path) -> None:
        self._outputs.append(str(path))

    # ------------------------------------------------------------------ execution

    def _gate(self, unit: Unit) -> UnitRecord | None:
        rec = lambda st, code, why: UnitRecord(  # noqa: E731
            unit.stage, unit.unit_id, unit.analysis, st, code, why, run_id=self.run_id,
            finished_utc=utc_now())
        if unit.analysis in self.cfg.run.skip:
            return rec("skipped", "run_skip", f"{unit.analysis} listed in run.skip")
        for dep in unit.requires:
            d = self.records.get(dep)
            if d is None:
                continue
            if d.status == "skipped":
                return rec("skipped", "dependency_skipped",
                           f"needs {dep[1]}, which was skipped ({d.reason_code})")
            if d.status in ("not_available", "failed"):
                return rec("not_available", "dependency_unavailable",
                           f"needs {dep[1]}, which is {d.status} ({d.reason_code}: {d.reason})")
        if unit.blocked is not None:
            b = unit.blocked()
            if b is not None:
                return rec("not_available", b[0], b[1])
        return None

    def unit_key(self, unit: Unit) -> str | None:
        parts = unit.key()
        if parts is None:
            return None
        code = (code_identity() if unit.stage == "report"
                else code_identity(PRESENTATION_MODULES))
        return canonical_hash({"code": code, "stage": unit.stage,
                               "unit": unit.unit_id, "parts": parts})

    def execute(self, units: Sequence[Unit]) -> list[UnitRecord]:
        """Run ``units`` in order; reuse valid outputs. A failing unit is recorded, then re-raised."""
        done = []
        for unit in units:
            done.append(self._execute(unit))
        return done

    def _store(self, r: UnitRecord, *, stamp: bool = True) -> UnitRecord:
        self.records[(r.stage, r.unit_id)] = r
        if stamp:
            self.cache.write(r)
        log_event(_log, "unit_finished", stage=r.stage, unit=r.unit_id, status=r.status,
                  reason_code=r.reason_code, elapsed_s=r.elapsed_s, n_outputs=len(r.outputs))
        return r

    def _execute(self, unit: Unit) -> UnitRecord:
        gated = self._gate(unit)
        if gated is not None:
            return self._store(gated)
        key = self.unit_key(unit)
        if key is not None:
            stamp = self.cache.valid(unit.stage, unit.unit_id, key)
            if stamp is not None:
                r = UnitRecord.from_dict(stamp)
                if r.status == "completed":
                    r.status = "cached"
                    r.reason_code = "cache_valid"
                    r.reason = f"outputs of run {r.run_id} reused; no input changed"
                return self._store(r, stamp=False)
        log_event(_log, "unit_started", stage=unit.stage, unit=unit.unit_id)
        self._outputs = []
        t0 = time.perf_counter()
        try:
            outcome = unit.run()
        except Exception as exc:
            self._store(UnitRecord(unit.stage, unit.unit_id, unit.analysis, "failed",
                                   type(exc).__name__, str(exc)[:2000], key, self.run_id,
                                   utc_now(), time.perf_counter() - t0, list(self._outputs)))
            raise
        return self._store(UnitRecord(
            unit.stage, unit.unit_id, unit.analysis, outcome.status, outcome.reason_code,
            outcome.reason, key, self.run_id, utc_now(), round(time.perf_counter() - t0, 3),
            list(dict.fromkeys(self._outputs)), outcome.details))

    def status_of(self, units: Sequence[Unit]) -> list[UnitRecord]:
        """Status of ``units`` without running them (completed, stale, not_run, ...)."""
        out = []
        for unit in units:
            if (unit.stage, unit.unit_id) in self.records:
                out.append(self.records[(unit.stage, unit.unit_id)])
                continue
            gated = self._gate(unit)
            if gated is not None:
                r = gated
            else:
                key = self.unit_key(unit)
                stamp = self.cache.read(unit.stage, unit.unit_id)
                if stamp is None:
                    r = UnitRecord(unit.stage, unit.unit_id, unit.analysis, "not_run",
                                   "never_run", "declared but not run yet", key)
                elif key is not None and self.cache.valid(unit.stage, unit.unit_id,
                                                          key) is None:
                    r = UnitRecord.from_dict(stamp)
                    r.status, r.reason_code = "stale", "inputs_changed"
                    r.reason = (f"outputs of run {r.run_id} exist, but an input, mask, grid "
                                "or configuration entry changed since (or an output was "
                                "modified); rerun to refresh")
                else:
                    r = UnitRecord.from_dict(stamp)
            self.records[(r.stage, r.unit_id)] = r
            out.append(r)
        return out

    def plan(self, stage: str) -> list[Unit]:
        return {"index": self.plan_index, "extract": self.plan_extract,
                "analyze": self.plan_analyze}[stage]()

    def undeclared(self) -> list[UnitRecord]:
        """A ``skipped`` row (``not_declared``) for every analysis with no declaration."""
        declared = {"footprint_aggregation": self.cfg.footprint_stacks,
                    "raster_extraction": self.cfg.raster_extractions,
                    "footprint_comparison": self.cfg.footprint_comparisons,
                    "cohorts": self.cfg.comparisons,
                    "periods": self.cfg.period_aggregations,
                    "metrics": self.cfg.metric_sets, "bootstrap": self.cfg.bootstrap,
                    "rankings": self.cfg.rankings,
                    "ranking_sensitivity": self.cfg.rankings,
                    "diagnostics": self.cfg.diagnostics,
                    "satellite_support": self.cfg.satellite_support,
                    "events": self.cfg.event_analyses, "anomalies": self.cfg.anomalies,
                    "water_balance": self.cfg.water_balances,
                    "ensembles": self.cfg.ensembles,
                    "representativeness": self.cfg.representativeness}
        return [_not_declared(a) for a, specs in declared.items() if not specs]

    # ------------------------------------------------------------------ loaders

    def _once(self, key: Any, make: Callable[[], Any]) -> Any:
        if key not in self._memo:
            self._memo[key] = make()
        return self._memo[key]

    def station_result(self, site: str) -> Any:
        from etcompare.adapters.station import adapt_station_daily

        t = next(t for t in self.cfg.station_tables if t.site_id == site)
        return self._once(("station", site), lambda: adapt_station_daily(
            t.path, t, self.cfg.stations[site]))  # type: ignore[arg-type]

    def stations(self, sites: Sequence[str] | None = None) -> list[CanonicalTable]:
        have = [t.site_id for t in self.cfg.station_tables]
        return [self.station_result(s).table for s in (sites or have) if s in have]

    def station_ident(self, sites: Sequence[str] | None = None) -> list[Any]:
        have = {t.site_id: t for t in self.cfg.station_tables}
        return [[self.ident(have[s].path), to_plain(have[s]), to_plain(self.cfg.stations[s])]
                for s in sorted(sites or have) if s in have]

    def extraction_path(self, name: str) -> Path:
        return self.out / f"{name}_extractions.parquet"

    def source(self, name: str) -> CanonicalTable:
        raster = {x.name for x in self.cfg.raster_extractions}
        if name in raster:
            return self._once(("source", name),
                              lambda: read_table(self.extraction_path(name)))

        from etcompare.adapters.extracted import adapt_extracted_table
        from etcompare.registry import SiteRegistry

        t = next(t for t in self.cfg.extracted_tables if t.name == name)
        return self._once(("source", name), lambda: adapt_extracted_table(
            t.path, t, models=self.cfg.model_registry(),
            sites=SiteRegistry(self.cfg.site_aliases), products=self.cfg.products,
            stations=self.cfg.stations).table)  # type: ignore[arg-type]

    def source_ident(self, name: str) -> Any:
        if name in {x.name for x in self.cfg.raster_extractions}:
            return self.ident(self.extraction_path(name), "sha256")
        t = next((t for t in self.cfg.extracted_tables if t.name == name), None)
        if t is None:
            raise ContractError(f"source {name!r} is neither a raster extraction nor an "
                                "extracted table", config_key="comparisons[].sources")
        return [self.ident(t.path), to_plain(t), to_plain(self.cfg.products),
                to_plain(self.cfg.stations), dict(self.cfg.site_aliases),
                dict(self.cfg.model_aliases)]

    def sources_missing(self, names: Sequence[str]) -> tuple[str, str] | None:
        raster = {x.name for x in self.cfg.raster_extractions}
        tables = {t.name: t for t in self.cfg.extracted_tables}
        for n in names:
            if n in raster and not self.extraction_path(n).is_file():
                return ("input_missing", f"raster extraction {n} has no output "
                        f"{self.extraction_path(n).name}; run `etcompare extract` first")
            if n in tables and not Path(tables[n].path or "").is_file():  # type: ignore[arg-type]
                return ("input_missing", f"extracted table {tables[n].path} not found")
            if n not in raster and n not in tables:
                raise ContractError(f"source {n!r} is not declared",
                                    config_key="comparisons[].sources")
        return None

    def stations_missing(self, sites: Sequence[str] | None = None) -> tuple[str, str] | None:
        for t in self.cfg.station_tables:
            if (sites is None or t.site_id in sites) and not Path(t.path or "").is_file():  # type: ignore[arg-type]
                return ("input_missing", f"station table {t.path} not found")
        return None

    # ================================================================== validate

    def validate(self) -> list[UnitRecord]:
        """Read every declared table input through its adapter; write validation tables.

        Raises on contract or data errors (the CLI exits nonzero). Missing raster or
        footprint directories are warnings: only the units that need them become
        ``not_available``.
        """
        vdir = self.out / "validation"
        self._outputs = []
        t0 = time.perf_counter()
        issues: list[pd.DataFrame] = []
        exclusions: list[pd.DataFrame] = []
        counts: list[dict[str, Any]] = []

        for p in (*[t.path for t in self.cfg.station_tables],
                  *[t.path for t in self.cfg.extracted_tables],
                  *[t.path for t in self.cfg.crop_height_tables],
                  *[t.path for t in self.cfg.event_tables]):
            self.ident(p)                       # recorded in the run manifest

        def keep(result: Any, what: str) -> None:
            issues.append(result.report.to_frame().assign(input=what))
            ex = result.exclusions.to_frame()
            exclusions.append(ex.assign(input=what))
            for (stage, code), n in result.exclusions.counts().items():
                counts.append({"stage": stage, "unit_id": "validate", "input": what,
                               "reason_code": code, "n": int(n)})

        for t in self.cfg.station_tables:
            keep(self.station_result(t.site_id), f"station_table:{t.site_id}")
        for t in self.cfg.extracted_tables:
            from etcompare.adapters.extracted import adapt_extracted_table
            from etcompare.registry import SiteRegistry

            r = adapt_extracted_table(
                t.path, t, models=self.cfg.model_registry(),  # type: ignore[arg-type]
                sites=SiteRegistry(self.cfg.site_aliases), products=self.cfg.products,
                stations=self.cfg.stations)
            self._memo[("source", t.name)] = r.table
            keep(r, f"extracted_table:{t.name}")
        notes = []
        for c in self.cfg.crop_height_tables:
            if not Path(c.path or "").is_file():  # type: ignore[arg-type]
                if c.on_missing == "error":
                    raise ContractError(f"crop-height table {c.path} not found (set "
                                        "on_missing: skip_growing_season to leave out only "
                                        "the growing-season periods)",
                                        config_key=f"crop_height_tables.{c.name}.path")
                notes.append({"severity": "warning", "code": "crop_height_missing",
                              "message": f"{c.path} not found; growing-season periods "
                                         "will be skipped", "input": f"crop_height:{c.name}"})
                continue
            from etcompare.adapters.crop_height import adapt_crop_height

            keep(adapt_crop_height(c.path, c), f"crop_height:{c.name}")  # type: ignore[arg-type]
        for e in self.cfg.event_tables:
            from etcompare.adapters.event_log import adapt_event_table

            keep(adapt_event_table(e.path, e, stations=self.cfg.stations),  # type: ignore[arg-type]
                 f"event_table:{e.name}")
        dirs = [(f"openet_rasters[{i}]", s.root) for i, s in enumerate(self.cfg.openet_rasters)]
        dirs += [(f"footprint_sets.{f.set_id}", f.root) for f in self.cfg.footprint_sets]
        dirs += [(f"footprint_stacks.{s.site_id}", s.stack_dir)
                 for s in self.cfg.footprint_stacks]
        for what, d in dirs:
            if not Path(d).is_dir():
                notes.append({"severity": "warning", "code": "directory_missing",
                              "message": f"{d} is not a directory; units that need it will "
                                         "be not_available", "input": what})
        if len(self.cfg.openet_rasters) > 1 and self.cfg.raster_extractions:
            raise ContractError("raster extraction supports exactly one openet_rasters "
                                "source", config_key="openet_rasters")
        frame = pd.concat([*issues, pd.DataFrame(notes)], ignore_index=True) \
            if issues or notes else pd.DataFrame(columns=["severity", "code", "message"])
        self.save_csv(frame, vdir / "validation_issues.csv")
        ex = pd.concat(exclusions, ignore_index=True) if exclusions else pd.DataFrame()
        self.save_csv(ex, vdir / "input_exclusions.csv")
        rec = UnitRecord("validate", "validate", "validate", "completed", None,
                         f"{len(self.cfg.station_tables)} station table(s), "
                         f"{len(self.cfg.extracted_tables)} extracted table(s), "
                         f"{len(self.cfg.crop_height_tables)} crop-height table(s), "
                         f"{len(self.cfg.event_tables)} event table(s) read; "
                         f"{len(notes)} warning(s)", None, self.run_id, utc_now(),
                         round(time.perf_counter() - t0, 3), list(self._outputs),
                         {"exclusions": counts})
        return [self._store(rec)]

    # ================================================================== index

    def discovery(self, i: int) -> Any:
        from etcompare.io.manifest import discover_openet_rasters
        from etcompare.registry import SiteRegistry

        src = self.cfg.openet_rasters[i]
        return self._once(("discovery", i), lambda: discover_openet_rasters(
            src.root, models=self.cfg.model_registry(),
            sites=SiteRegistry(self.cfg.site_aliases), products=self.cfg.products,
            recursive=src.recursive, processing_tags=src.processing_tags,
            on_conflict=src.on_conflict))  # type: ignore[arg-type]

    def footprint_discovery(self, set_id: str) -> Any:
        from etcompare.io.manifest import discover_footprints
        from etcompare.registry import SiteRegistry

        fs = self.cfg.footprint_set(set_id)
        return self._once(("fp_discovery", set_id), lambda: discover_footprints(
            fs, sites=SiteRegistry(self.cfg.site_aliases)))

    def _listing(self, root: Path, recursive: bool) -> list[str]:
        from etcompare.io.manifest import DEFAULT_EXTENSIONS, list_files

        return self._once(("listing", str(root), recursive), lambda: [
            str(p) for p in list_files(root, recursive=recursive,
                                       extensions=DEFAULT_EXTENSIONS)])

    def plan_index(self) -> list[Unit]:
        units = []
        for i, src in enumerate(self.cfg.openet_rasters):
            def key(i: int = i, src: Any = src) -> Any:
                if not Path(src.root).is_dir():
                    return None
                return [to_plain(src), to_plain(self.cfg.products),
                        dict(self.cfg.site_aliases), dict(self.cfg.model_aliases),
                        self.idents(self._listing(Path(src.root), src.recursive))]

            def run(i: int = i) -> Outcome:
                d = self.discovery(i)
                stem = self.out / "index" / f"openet_{i}"
                self.save(d.manifest, Path(f"{stem}_manifest.parquet"))
                if d.associations is not None:
                    self.save(d.associations, Path(f"{stem}_asset_sites.parquet"))
                self.save_csv(d.conflicts, Path(f"{stem}_conflicts.csv"))
                self.save_csv(d.exclusions.to_frame(), Path(f"{stem}_exclusions.csv"))
                self.save_text(d.report.summary(), Path(f"{stem}_report.txt"))
                return Outcome(details={"exclusions": _counts(d.exclusions, f"openet_{i}")})

            units.append(Unit("index", "index", f"index:openet_{i}", key, run,
                              blocked=_dir_blocked(src.root)))
        for fs in self.cfg.footprint_sets:
            def fkey(fs: Any = fs) -> Any:
                if not Path(fs.root).is_dir():
                    return None
                return [to_plain(fs), dict(self.cfg.site_aliases),
                        self.ident(fs.domain_capture_csv) if fs.domain_capture_csv else None,
                        self.idents(self._listing(Path(fs.root), fs.recursive))]

            def frun(fs: Any = fs) -> Outcome:
                d = self.footprint_discovery(fs.set_id)
                stem = self.out / "index" / f"footprints_{fs.set_id}"
                self.save(d.manifest, Path(f"{stem}_manifest.parquet"))
                self.save_csv(d.exclusions.to_frame(), Path(f"{stem}_exclusions.csv"))
                self.save_text(d.report.summary(), Path(f"{stem}_report.txt"))
                return Outcome(details={"exclusions": _counts(d.exclusions, fs.set_id)})

            units.append(Unit("index", "index", f"index:footprints_{fs.set_id}", fkey, frun,
                              blocked=_dir_blocked(fs.root)))
        return units

    # ================================================================== extract

    def plan_extract(self) -> list[Unit]:
        units = [*self._stack_units(), *self._extraction_units(),
                 *self._footprint_comparison_units()]
        return units

    def _stack_targets(self, site: str) -> dict[str, tuple[Any, Any]]:
        targets = {}
        for agg in self.cfg.footprint_aggregations:
            tag = f"etcompare_aggregation:{agg.method_id}"
            sets = [f for f in self.cfg.footprint_sets
                    if f.weighting_method == tag and f.set_id.startswith(f"{site}_")]
            if len(sets) > 1:
                raise ContractError(f"{site}: more than one footprint set with weighting_method "
                                    f"{tag}", config_key="footprint_sets")
            if sets:
                targets[agg.method_id] = (agg, sets[0])
        return targets

    def _stack_units(self) -> list[Unit]:
        units = []
        for st in self.cfg.footprint_stacks:
            files = lambda st=st: sorted(  # noqa: E731
                str(p) for p in Path(st.stack_dir).glob(f"{st.site_id}_*_timesteps.npz"))

            def blocked(st: Any = st, files: Callable[[], list[str]] = files
                        ) -> tuple[str, str] | None:
                if not Path(st.stack_dir).is_dir() or not files():
                    return ("input_missing", f"no per-timestep stacks "
                            f"{st.site_id}_*_timesteps.npz in {st.stack_dir}")
                if not self._stack_targets(st.site_id):
                    return ("no_target_set", f"no footprint set with weighting_method "
                            f"etcompare_aggregation:<method> for {st.site_id}")
                return None

            def key(st: Any = st, files: Callable[[], list[str]] = files) -> Any:
                t = self._stack_targets(st.site_id)
                cmp_ = [[to_plain(self.cfg.footprint_set(s)),
                         self.idents(self._listing(Path(self.cfg.footprint_set(s).root),
                                                   self.cfg.footprint_set(s).recursive))]
                        for s in st.compare_sets]
                return [to_plain(st), {m: [to_plain(a), to_plain(f)] for m, (a, f) in t.items()},
                        self.idents(files()), cmp_]

            def run(st: Any = st, files: Callable[[], list[str]] = files) -> Outcome:
                return self._run_stack(st, files())

            units.append(Unit("extract", "footprint_aggregation",
                              f"footprint_aggregation:{st.site_id}", key, run, blocked=blocked))
        return units

    def _run_stack(self, st: Any, files: list[str]) -> Outcome:
        import numpy as np

        from etcompare.adapters.fluxfootprints import load_timestep_stack
        from etcompare.footprint_aggregation import (
            aggregate_footprints,
            combine_results,
            common_window,
            tv_distance,
            write_footprint_set,
        )
        from etcompare.io.rasters import read_footprint

        targets = self._stack_targets(st.site_id)
        chunks: dict[str, list[Any]] = {m: [] for m in targets}
        for path in files:
            stack, weights = load_timestep_stack(path, site_id=st.site_id)
            for m, (agg, _) in targets.items():
                w = None if agg.weight_column is None else weights[agg.weight_column]
                chunks[m].append(aggregate_footprints(stack, agg, weights=w))
        results, text = {}, []
        for m, (_agg, declared) in targets.items():
            r = combine_results(chunks[m])
            results[m] = r
            fs = write_footprint_set(r, declared.root, set_id=declared.set_id,
                                     layer=declared.layers[0] if declared.layers else None)
            for f in ("value_semantics", "temporal_weighting", "weighting_method"):
                if getattr(fs, f) != getattr(declared, f):
                    raise ContractError(f"{declared.set_id}: written {f}={getattr(fs, f)!r} "
                                        f"but config declares {getattr(declared, f)!r}",
                                        config_key=f"footprint_sets.{declared.set_id}.{f}")
            summary = r.summary()
            self.save_csv(summary, Path(declared.root) / f"{declared.set_id}_periods.csv")
            for p in Path(declared.root).glob("*.tif"):
                self.add_output(p)
            text.append(f"{declared.set_id}: {int((summary.status == 'ok').sum())} of "
                        f"{len(summary)} days written; {summary.status.value_counts().to_dict()}")
        rows = []
        for set_id in st.compare_sets:
            man = self.footprint_discovery(set_id).manifest.frame
            man = man[man.period == "day"].set_index("date")
            for m, r in results.items():
                for p in r.periods:
                    if not p.ok or p.date not in man.index:
                        continue
                    other, grid, nodata = read_footprint(man.loc[p.date, "path"])
                    if nodata is not None and not np.isnan(nodata):
                        other = np.where(other == nodata, 0.0, other)
                    win = common_window(p.grid, grid)
                    if win is None:
                        raise EtcompareError(f"{set_id}: grid not aligned with the aggregate")
                    rows.append({"date": p.date, "aggregate": m, "existing_set": set_id,
                                 "tv_distance": tv_distance(p.contribution[win[0], win[1]],
                                                            other[win[2], win[3]]),
                                 "existing_has_negative": bool((other < 0).any())})
        if rows:
            self.save_csv(pd.DataFrame(rows),
                          self.out / f"{st.site_id}_aggregate_vs_existing_footprints.csv")
        self.save_text("\n".join(text), self.out / f"{st.site_id}_footprint_aggregation_report.txt")
        return Outcome()

    def _extraction_rows(self, spec: Any) -> tuple[list[str], list[str]]:
        """Raster and footprint files an extraction reads (site, window, models)."""
        man = self.discovery(0).manifest.frame
        assoc = self.discovery(0).associations
        if assoc is not None:
            ids = set(assoc.frame.loc[assoc.frame["site_id"] == spec.site_id, "asset_id"])
            man = man[man["asset_id"].isin(ids)]
        if spec.start:
            man = man[man["date"] >= pd.Timestamp(spec.start)]
        if spec.end:
            man = man[man["date"] <= pd.Timestamp(spec.end)]
        if spec.models:
            man = man[man["model"].isin(spec.models)]
        fps: list[str] = []
        if spec.footprint_set_id:
            f = self.footprint_discovery(spec.footprint_set_id).manifest.frame
            f = f[f["site_id"] == spec.site_id]
            if spec.start:
                f = f[f["date"] >= pd.Timestamp(spec.start)]
            if spec.end:
                f = f[f["date"] <= pd.Timestamp(spec.end)]
            fps = list(f["path"])
        return list(man["path"]), fps

    def _extraction_units(self) -> list[Unit]:
        units = []
        for spec in self.cfg.raster_extractions:
            def blocked(spec: Any = spec) -> tuple[str, str] | None:
                if not self.cfg.openet_rasters:
                    return ("input_missing", "no openet_rasters source declared")
                if not Path(self.cfg.openet_rasters[0].root).is_dir():
                    return ("input_missing",
                            f"raster root {self.cfg.openet_rasters[0].root} not found")
                if spec.footprint_set_id and not Path(
                        self.cfg.footprint_set(spec.footprint_set_id).root).is_dir():
                    return ("input_missing", f"footprint set {spec.footprint_set_id} root not "
                            "found")
                return None

            def key(spec: Any = spec) -> Any:
                rasters, fps = self._extraction_rows(spec)
                fs = (self.cfg.footprint_set(spec.footprint_set_id)
                      if spec.footprint_set_id else None)
                return [to_plain(spec), to_plain(self.cfg.stations[spec.site_id]),
                        to_plain(self.cfg.products), to_plain(self.cfg.openet_rasters[0]),
                        to_plain(fs), self.ident(fs.domain_capture_csv)
                        if fs and fs.domain_capture_csv else None,
                        self.idents(rasters), self.idents(fps)]

            def run(spec: Any = spec) -> Outcome:
                from etcompare.extraction import extract_rasters

                fs = (self.cfg.footprint_set(spec.footprint_set_id)
                      if spec.footprint_set_id else None)
                d = self.discovery(0)
                fd = self.footprint_discovery(spec.footprint_set_id) if fs else None
                t0 = time.time()
                result = extract_rasters(spec, station=self.cfg.stations[spec.site_id],
                                         model_manifest=d.manifest, products=self.cfg.products,
                                         footprint_manifest=fd.manifest if fd else None,
                                         footprint_set=fs, strict=spec.strict)
                self.save(result.table, self.extraction_path(spec.name), csv=False)
                self.save_csv(result.exclusions.to_frame(),
                              self.out / f"{spec.name}_exclusions.csv")
                reports = [d.report.summary(), *([fd.report.summary()] if fd else []),
                           result.report.summary(), f"elapsed {time.time() - t0:.0f}s"]
                self.save_text("\n\n".join(reports), self.out / f"{spec.name}_report.txt")
                a = result.table.attributes
                f = result.table.frame
                return Outcome(details={
                    "exclusions": _counts(result.exclusions, spec.name),
                    "raster_grids": [{"unit": spec.name,
                                      "model_grid_ids": sorted(f["model_grid_id"].dropna()
                                                               .unique().tolist()),
                                      "window_grids": a.get("window_grids"),
                                      "transfers": a.get("transfers")}],
                    "masks": [{"unit": spec.name, "methods": list(spec.methods),
                               "footprint_set_id": spec.footprint_set_id,
                               "negative_weight_tolerance": spec.negative_weight_tolerance,
                               "support_ids": sorted({str(s).split(":")[0] for s in
                                                      f["support_id"].dropna().unique()}),
                               "nodata": {m: self.cfg.products[m].nodata
                                          for m in sorted(f["model"].unique())
                                          if m in self.cfg.products}}]})

            units.append(Unit("extract", "raster_extraction", f"raster_extraction:{spec.name}",
                              key, run, blocked=blocked))
        return units

    def _footprint_comparison_units(self) -> list[Unit]:
        units = []
        for fc in self.cfg.footprint_comparisons:
            specs = [next(x for x in self.cfg.raster_extractions if x.name == n)
                     for n in fc.extractions]
            site = specs[0].site_id

            def blocked(fc: Any = fc, site: str = site) -> tuple[str, str] | None:
                return self.sources_missing(fc.extractions) or self.stations_missing([site])

            def key(fc: Any = fc, specs: list[Any] = specs, site: str = site) -> Any:
                return [to_plain(fc), [self.source_ident(n) for n in fc.extractions],
                        [to_plain(self.cfg.footprint_set(s.footprint_set_id)) for s in specs],
                        self.station_ident([site])]

            def run(fc: Any = fc, specs: list[Any] = specs, site: str = site) -> Outcome:
                from etcompare.footprint_sensitivity import compare_footprint_sets

                r = compare_footprint_sets(
                    [self.source(n) for n in fc.extractions],
                    [self.cfg.footprint_set(s.footprint_set_id) for s in specs],
                    comparison_id=fc.comparison_id, station_daily=self.stations([site])[0],
                    extraction_methods=fc.methods, min_retained_frac=fc.min_retained_frac,
                    common_models=fc.common_models)
                stem = self.out / fc.comparison_id
                if r.paired is not None:
                    self.save(r.paired, Path(f"{stem}_paired.parquet"), csv=False)
                self.save_csv(r.differences, Path(f"{stem}_differences.csv"))
                self.save_csv(r.availability, Path(f"{stem}_availability.csv"))
                self.save_csv(r.exclusions.to_frame(), Path(f"{stem}_exclusions.csv"))
                self.save_text(f"status {r.status} {r.reason}\n\n{r.report.summary()}",
                               Path(f"{stem}_report.txt"))
                details = {"exclusions": _counts(r.exclusions, fc.comparison_id)}
                if r.status != "ok":
                    return Outcome("not_available", "footprint_comparison_" + str(r.status),
                                   str(r.reason), details)
                return Outcome(details=details)

            units.append(Unit("extract", "footprint_comparison",
                              f"footprint_comparison:{fc.comparison_id}", key, run,
                              blocked=blocked))
        return units

    # ================================================================== analyze: helpers

    def comparison_sites(self, spec: ComparisonSpec) -> list[str]:
        have = [t.site_id for t in self.cfg.station_tables]
        return [s for s in (spec.sites or tuple(have)) if s in have]

    def cohort(self, cid: str) -> Any:
        from etcompare.cohorts import build_cohort

        spec = self.cfg.comparison(cid)
        return self._once(("cohort", cid), lambda: build_cohort(
            self.stations(self.comparison_sites(spec)), [self.source(s) for s in spec.sources],
            spec, footprint_sets=self.cfg.footprint_sets))  # type: ignore[arg-type]

    def cohort_key(self, cid: str) -> Any:
        spec = self.cfg.comparison(cid)
        sides = {s.footprint_set_id for s in spec.sides}
        return [to_plain(spec), [self.source_ident(s) for s in spec.sources],
                self.station_ident(self.comparison_sites(spec)),
                [to_plain(f) for f in self.cfg.footprint_sets if f.set_id in sides]]

    def _crop_note(self, agg: PeriodAggregationSpec) -> str | None:
        if not (agg.growing_season_source and "growing_season" in agg.periods):
            return None
        crop = self.cfg.crop_height_table(agg.growing_season_source)
        if Path(crop.path or "").is_file():  # type: ignore[arg-type]
            return None
        if crop.on_missing == "error":
            raise ContractError(f"crop-height table {crop.path} not found",
                                config_key=f"crop_height_tables.{crop.name}.path")
        return (f"{agg.aggregation_id}: growing_season periods skipped; crop-height file "
                f"{crop.path} not found (on_missing: skip_growing_season)")

    def resolved_aggregation(self, agg: PeriodAggregationSpec) -> PeriodAggregationSpec:
        from etcompare.adapters.crop_height import adapt_crop_height, growing_season_windows

        def make() -> PeriodAggregationSpec:
            if self._crop_note(agg) is not None:
                return replace(agg, periods=tuple(p for p in agg.periods
                                                  if p != "growing_season"),
                               growing_season_source=None)
            if agg.growing_season_source and "growing_season" in agg.periods:
                crop = self.cfg.crop_height_table(agg.growing_season_source)
                return agg.with_growing_seasons(growing_season_windows(
                    adapt_crop_height(crop.path, crop)))  # type: ignore[arg-type]
            return agg

        return self._once(("agg", agg.aggregation_id), make)

    def agg_key(self, agg: PeriodAggregationSpec) -> Any:
        crop = None
        if agg.growing_season_source:
            c = self.cfg.crop_height_table(agg.growing_season_source)
            crop = [to_plain(c), self.ident(c.path)]
        return [to_plain(agg), crop]

    def aggregations_for(self, cid: str) -> list[PeriodAggregationSpec]:
        if "periods" in self.cfg.run.skip:
            return []
        return [a for a in self.cfg.period_aggregations
                if a.comparisons is None or cid in a.comparisons]

    def totals(self, cid: str) -> list[Any]:
        from etcompare.aggregation import aggregate_cohort

        spec = self.cfg.comparison(cid)

        def make() -> list[Any]:
            obs = self.stations(self.comparison_sites(spec))
            return [aggregate_cohort(self.cohort(cid), self.resolved_aggregation(a),
                                     products=self.cfg.products, reference_et=obs)
                    for a in self.aggregations_for(cid)]

        return self._once(("totals", cid), make)

    def metric_sets_for(self, cid: str) -> list[MetricSpec]:
        return [m for m in self.cfg.metric_sets if m.comparisons is None or cid in m.comparisons]

    def metric_result(self, cid: str, ms: MetricSpec) -> Any:
        from etcompare.metrics import score_comparison

        return self._once(("metrics", cid, ms.metric_set_id), lambda: score_comparison(
            self.cohort(cid), ms, products=self.cfg.products, period_totals=self.totals(cid)))

    def metric_key(self, cid: str, ms: MetricSpec) -> Any:
        return [self.cohort_key(cid), to_plain(ms),
                [self.agg_key(a) for a in self.aggregations_for(cid)],
                to_plain(self.cfg.products)]

    def bootstraps_for(self, cid: str, ms: MetricSpec) -> list[BootstrapSpec]:
        return [b for b in self.cfg.bootstrap if b.metric_set == ms.metric_set_id
                and (b.comparisons is None or cid in b.comparisons)]

    def boot(self, cid: str, ms: MetricSpec, bs: BootstrapSpec) -> Any:
        from etcompare.bootstrap import bootstrap_comparison

        return self._once(("boot", cid, bs.bootstrap_id), lambda: bootstrap_comparison(
            self.cohort(cid), ms, bs, products=self.cfg.products,
            period_totals=self.totals(cid), metric_results=self.metric_result(cid, ms)))

    def rankings_for(self, cid: str, bs: BootstrapSpec) -> list[RankingSpec]:
        spec = self.cfg.comparison(cid)
        return [r for r in self.cfg.rankings if r.bootstrap == bs.bootstrap_id
                and not (r.comparisons is not None and cid not in r.comparisons)
                and not (r.comparisons is None and spec.ranking_scope == "none")]

    def ranking_results(self, cid: str, ms: MetricSpec, bs: BootstrapSpec,
                        rs: RankingSpec) -> list[Any]:
        from etcompare.bootstrap import bootstrap_comparison
        from etcompare.cohorts import build_cohort
        from etcompare.metrics import score_comparison
        from etcompare.ranking import rank_comparison, year_subsets

        def make() -> list[Any]:
            c = self.cohort(cid)
            results = [rank_comparison(c, self.metric_result(cid, ms), self.boot(cid, ms, bs),
                                       rs)]
            if rs.year_subsets:
                spec = self.cfg.comparison(cid)
                obs = self.stations(self.comparison_sites(spec))
                ext = [self.source(s) for s in spec.sources]
                day_ms = replace(ms, period_aggregations=(), period_products=(),
                                 period_types=None)
                day_bs = replace(bs, period_block_lengths=())
                for label, start, end in year_subsets(c):
                    sub = build_cohort(obs, ext, replace(spec, start=start, end=end),
                                       footprint_sets=self.cfg.footprint_sets)  # type: ignore[arg-type]
                    sres = score_comparison(sub, day_ms, products=self.cfg.products)
                    sboot = bootstrap_comparison(sub, day_ms, day_bs, products=self.cfg.products,
                                                 metric_results=sres)
                    results.append(rank_comparison(sub, sres, sboot, rs, subset=label))
            return results

        return self._once(("rank", cid, rs.ranking_id), make)

    # ================================================================== analyze: plan

    def plan_analyze(self) -> list[Unit]:
        units: list[Unit] = []
        for spec in self.cfg.comparisons:
            units += self._comparison_units(spec)
        units += self._sensitivity_units()
        units += self._event_units()
        units += self._water_balance_units()
        units += self._ensemble_units()
        units += self._representativeness_units()
        return units

    def _comparison_units(self, spec: ComparisonSpec) -> list[Unit]:
        cid = spec.comparison_id
        if not spec.sources:
            raise ContractError(f"comparison {cid} declares no sources",
                                config_key=f"comparisons.{cid}.sources")
        out = self.out / "cohorts"
        cohort_id = ("analyze", f"cohorts:{cid}")

        def blocked() -> tuple[str, str] | None:
            return self.sources_missing(spec.sources) or self.stations_missing(
                self.comparison_sites(spec))

        def run_cohort() -> Outcome:
            c = self.cohort(cid)
            self.save(c.paired, out / f"{cid}_paired.parquet", csv=False)
            self.save(c.membership, out / f"{cid}_membership.parquet", csv=False)
            summary = c.summary()
            self.save_csv(summary, out / f"{cid}_summary.csv")
            self.save_csv(c.exclusions.to_frame(), out / f"{cid}_exclusions.csv")
            m = c.membership.frame
            reasons = m[~m["included"].astype(bool)].groupby(["cohort_id", "reason_code"]).size()
            self.save_text(
                f"comparison {cid} ({spec.kind}, ranking scope {spec.ranking_scope})\n"
                f"status {c.status}; membership sha256 {c.fingerprint}\n\n"
                f"{c.report.summary()}\n\n{summary.to_string(index=False)}\n\n"
                f"exclusions by reason:\n{reasons.to_string()}", out / f"{cid}_report.txt")
            return Outcome(details={"exclusions": [
                {"stage": "cohort", "unit_id": f"cohorts:{cid}", "reason_code": str(code),
                 "n": int(n)} for code, n in m.loc[~m["included"].astype(bool),
                                                   "reason_code"].value_counts().items()]})

        units = [Unit("analyze", "cohorts", f"cohorts:{cid}", lambda: self.cohort_key(cid),
                      run_cohort, blocked=blocked)]

        for agg in self.cfg.period_aggregations:
            if agg.comparisons is not None and cid not in agg.comparisons:
                continue

            def run_periods(agg: PeriodAggregationSpec = agg) -> Outcome:
                idx = [a.aggregation_id for a in self.aggregations_for(cid)]
                t = self.totals(cid)[idx.index(agg.aggregation_id)]
                stem = out / f"{cid}__{agg.aggregation_id}"
                self.save(t.table, Path(f"{stem}_periods.parquet"))
                s = t.summary()
                self.save_csv(s, Path(f"{stem}_summary.csv"))
                if t.daily_estimates is not None:
                    self.save(t.daily_estimates, Path(f"{stem}_daily_estimates.parquet"))
                note = self._crop_note(agg)
                self.save_text(f"period totals {agg.aggregation_id}:\n{t.report.summary()}\n"
                               f"{s.to_string(index=False)}"
                               + (f"\n\nWARNING {note}" if note else ""),
                               Path(f"{stem}_report.txt"))
                return Outcome(details={"notes": [note]} if note else {})

            units.append(Unit("analyze", "periods", f"periods:{cid}__{agg.aggregation_id}",
                              lambda agg=agg: [self.cohort_key(cid), self.agg_key(agg),
                                               to_plain(self.cfg.products)],
                              run_periods, requires=(cohort_id,)))

        for ms in self.metric_sets_for(cid):
            mid = ("analyze", f"metrics:{cid}__{ms.metric_set_id}")

            def run_metrics(ms: MetricSpec = ms) -> Outcome:
                from etcompare.metrics import definitions_frame

                res = self.metric_result(cid, ms)
                stem = out / f"{cid}__{ms.metric_set_id}"
                self.save(res.table, Path(f"{stem}_metrics.parquet"))
                wide = res.wide()
                self.save_csv(wide, Path(f"{stem}_metrics_wide.csv"))
                if res.cumulative is not None:
                    self.save(res.cumulative, Path(f"{stem}_cumulative_error.parquet"))
                # Shared by every metric unit; written, but no unit's cache depends on it.
                definitions_frame().to_csv(out / "metric_definitions.csv", index=False)
                self.save_text(f"metrics {ms.metric_set_id}:\n{res.report.summary()}",
                               Path(f"{stem}_metrics_report.txt"))
                return Outcome()

            req = (cohort_id, *[("analyze", f"periods:{cid}__{a.aggregation_id}")
                                for a in self.aggregations_for(cid)])
            units.append(Unit("analyze", "metrics", mid[1],
                              lambda ms=ms: self.metric_key(cid, ms), run_metrics,
                              requires=req))

            for bs in self.bootstraps_for(cid, ms):
                bid = ("analyze", f"bootstrap:{cid}__{bs.bootstrap_id}")

                def run_boot(ms: MetricSpec = ms, bs: BootstrapSpec = bs) -> Outcome:
                    t0 = time.perf_counter()
                    boot = self.boot(cid, ms, bs)
                    stem = out / f"{cid}__{bs.bootstrap_id}"
                    self.save(boot.intervals, Path(f"{stem}_intervals.parquet"))
                    self.save(boot.autocorrelation, Path(f"{stem}_autocorrelation.parquet"))
                    self.save_text(
                        f"bootstrap {bs.bootstrap_id} ({time.perf_counter() - t0:.0f} s; "
                        "station-level sampling intervals, not rankings):\n"
                        f"{boot.report.summary()}\n{bootstrap_text(boot)}",
                        Path(f"{stem}_report.txt"))
                    return Outcome(details={"seeds": [{"unit": f"bootstrap:{cid}",
                                                       "bootstrap_id": bs.bootstrap_id,
                                                       "seed": bs.seed}]})

                units.append(Unit("analyze", "bootstrap", bid[1],
                                  lambda ms=ms, bs=bs: [self.metric_key(cid, ms), to_plain(bs)],
                                  run_boot, requires=(mid,)))

                for rs in self.rankings_for(cid, bs):
                    def run_rank(ms: MetricSpec = ms, bs: BootstrapSpec = bs,
                                 rs: RankingSpec = rs) -> Outcome:
                        results = self.ranking_results(cid, ms, bs, rs)
                        stem = out / f"{cid}__{rs.ranking_id}"
                        for name in ("rankings", "rank_distribution", "contrasts", "network"):
                            parts = [getattr(r, name) for r in results]
                            t = CanonicalTable.build(
                                pd.concat([x.frame for x in parts], ignore_index=True),
                                parts[0].schema, {**parts[0].attributes,
                                                  "subsets": [r.subset for r in results]})
                            self.save(t, Path(f"{stem}_{name}.parquet"))
                        self.save_text(
                            f"ranking {rs.ranking_id} (station-level description; not a "
                            "verdict on which model is best):\n"
                            + "\n".join(r.report.summary() for r in results)
                            + f"\n{ranking_text(results)}", Path(f"{stem}_report.txt"))
                        return Outcome(details={"seeds": [{"unit": f"rankings:{cid}",
                                                           "bootstrap_id": bs.bootstrap_id,
                                                           "seed": bs.seed}]})

                    units.append(Unit(
                        "analyze", "rankings", f"rankings:{cid}__{rs.ranking_id}",
                        lambda ms=ms, bs=bs, rs=rs: [self.metric_key(cid, ms), to_plain(bs),
                                                     to_plain(rs)],
                        run_rank, requires=(bid,)))

        for ds in self.cfg.diagnostics:
            if ds.comparisons is not None and cid not in ds.comparisons:
                continue
            use_boot = ds.bootstrap if "bootstrap" not in self.cfg.run.skip else None

            def run_diag(ds: Any = ds, use_boot: str | None = use_boot) -> Outcome:
                from etcompare.diagnostics import diagnose_comparison

                t0 = time.perf_counter()
                d = replace(ds, bootstrap=use_boot)
                dg = diagnose_comparison(
                    self.cohort(cid), d, self.cfg.metric_set(ds.metric_set),
                    station_daily=self.stations(self.comparison_sites(spec)),
                    extractions=[self.source(s) for s in spec.sources],
                    bootstrap=self.cfg.bootstrap_spec(use_boot) if use_boot else None)
                stem = out / f"{cid}__{ds.diagnostics_id}"
                for name, t in (("closure", dg.closure), ("provenance", dg.provenance),
                                ("conditional", dg.conditional),
                                ("regressions", dg.regressions),
                                ("availability", dg.availability)):
                    self.save(t, Path(f"{stem}_{name}.parquet"))
                note = "" if use_boot or not ds.bootstrap else (
                    "\nbin intervals skipped: bootstrap listed in run.skip")
                self.save_text(
                    f"diagnostics {ds.diagnostics_id} ({time.perf_counter() - t0:.0f} s; "
                    "conditional associations, not causes; corrected/raw differences are "
                    "reference sensitivity, not uncertainty bounds):\n"
                    f"{dg.report.summary()}\n{diagnostics_text(dg)}{note}",
                    Path(f"{stem}_report.txt"))
                return Outcome()

            units.append(Unit(
                "analyze", "diagnostics", f"diagnostics:{cid}__{ds.diagnostics_id}",
                lambda ds=ds, use_boot=use_boot: [
                    self.cohort_key(cid), to_plain(ds),
                    to_plain(self.cfg.metric_set(ds.metric_set)),
                    to_plain(self.cfg.bootstrap_spec(use_boot)) if use_boot else None],
                run_diag, requires=(cohort_id,)))

        for ss in self.cfg.satellite_support:
            if (ss.comparisons is not None and cid not in ss.comparisons) or (
                    ss.comparisons is None and not spec.freeze_models):
                continue

            def run_sat(ss: Any = ss) -> Outcome:
                from etcompare.io.tables import read_any
                from etcompare.satellite import assess_satellite_support, support_status

                sup = support_status(ss.sources, {s.source_id: read_any(s.path)
                                                  for s in ss.sources}) if ss.sources else None
                sat = assess_satellite_support(self.cohort(cid), ss,
                                               self.cfg.metric_set(ss.metric_set), support=sup)
                stem = out / f"{cid}__{ss.satellite_support_id}"
                for name, t in (("satellite_errors", sat.errors),
                                ("satellite_availability", sat.availability)):
                    self.save(t, Path(f"{stem}_{name}.parquet"))
                return Outcome()

            units.append(Unit(
                "analyze", "satellite_support",
                f"satellite_support:{cid}__{ss.satellite_support_id}",
                lambda ss=ss: [self.cohort_key(cid), to_plain(ss),
                               to_plain(self.cfg.metric_set(ss.metric_set)),
                               self.idents(self.declared_paths(ss))],
                run_sat, requires=(cohort_id,)))
        return units

    def _sensitivity_units(self) -> list[Unit]:
        units = []
        for rs in self.cfg.rankings:
            parts = []
            for spec in self.cfg.comparisons:
                cid = spec.comparison_id
                for ms in self.metric_sets_for(cid):
                    for bs in self.bootstraps_for(cid, ms):
                        if rs in self.rankings_for(cid, bs):
                            parts.append(cid)
            files = [self.out / "cohorts" / f"{cid}__{rs.ranking_id}_rankings.parquet"
                     for cid in parts]
            req = tuple(("analyze", f"rankings:{cid}__{rs.ranking_id}") for cid in parts)

            def blocked(files: list[Path] = files, rs: RankingSpec = rs
                        ) -> tuple[str, str] | None:
                if not files:
                    return ("no_ranked_comparison", f"ranking {rs.ranking_id} applies to no "
                            "comparison")
                missing = [f.name for f in files if not f.is_file()]
                return ("input_missing", f"missing {missing}") if missing else None

            def run(files: list[Path] = files, rs: RankingSpec = rs) -> Outcome:
                from etcompare.diagnostics import reference_rank_changes
                from etcompare.ranking import ranking_sensitivity

                sens = ranking_sensitivity([read_table(f) for f in files])
                out = self.out / "cohorts"
                self.save(sens, out / f"{rs.ranking_id}_sensitivity.parquet")
                if "diagnostics" not in self.cfg.run.skip:
                    for ds in self.cfg.diagnostics:
                        rc = reference_rank_changes(sens, ds.diagnostics_id)
                        self.save(rc, out / f"{ds.diagnostics_id}__{rs.ranking_id}"
                                            "_reference_rank_changes.parquet")
                return Outcome()

            units.append(Unit(
                "analyze", "ranking_sensitivity", f"ranking_sensitivity:{rs.ranking_id}",
                lambda files=files: [[self.ident(f, "sha256") for f in files],
                                     [d.diagnostics_id for d in self.cfg.diagnostics],
                                     "diagnostics" in self.cfg.run.skip],
                run, requires=req, blocked=blocked))
        return units

    def _event_units(self) -> list[Unit]:
        units: list[Unit] = []
        out = self.out / "events"
        for et in self.cfg.event_tables:
            def run_table(et: Any = et) -> Outcome:
                r = self.event_table(et.name)
                self.save(r.table, out / f"{et.name}_events.parquet")
                self.save_csv(r.exclusions.to_frame(), out / f"{et.name}_event_exclusions.csv")
                return Outcome(details={"exclusions": _counts(r.exclusions, et.name)})

            units.append(Unit("analyze", "events", f"events:table_{et.name}",
                              lambda et=et: self.event_table_key(et.name), run_table,
                              blocked=lambda et=et: None if Path(et.path or "").is_file()
                              else ("input_missing", f"event table {et.path} not found")))
        for an_kind, specs, id_field in (("events", self.cfg.event_analyses,
                                          "event_analysis_id"),
                                         ("anomalies", self.cfg.anomalies, "anomaly_id")):
            for s in specs:
                for cid in (s.comparisons or ()):
                    sid = getattr(s, id_field)
                    req: tuple[tuple[str, str], ...] = (("analyze", f"cohorts:{cid}"),)
                    if an_kind == "events":
                        req += (("analyze", f"events:table_{s.event_table}"),)

                    def run(s: Any = s, cid: str = cid, an_kind: str = an_kind,
                            sid: str = sid) -> Outcome:
                        return self._run_event(an_kind, s, cid, sid)

                    def key(s: Any = s, cid: str = cid, an_kind: str = an_kind) -> Any:
                        parts = [self.cohort_key(cid), to_plain(s)]
                        if an_kind == "events":
                            parts += [self.event_table_key(s.event_table),
                                      self.station_ident(self.comparison_sites(
                                          self.cfg.comparison(cid)))]
                        else:
                            parts.append(to_plain(self.cfg.metric_set(s.metric_set)))
                        return parts

                    units.append(Unit("analyze", an_kind, f"{an_kind}:{cid}__{sid}", key, run,
                                      requires=req))
        return units

    def event_table(self, name: str) -> Any:
        from etcompare.adapters.event_log import adapt_event_table

        et = self.cfg.event_table(name)
        return self._once(("events", name), lambda: adapt_event_table(
            et.path, et, stations=self.cfg.stations))  # type: ignore[arg-type]

    def event_table_key(self, name: str) -> Any:
        et = self.cfg.event_table(name)
        return [to_plain(et), self.ident(et.path), to_plain(self.cfg.stations)]

    def _run_event(self, kind: str, s: Any, cid: str, sid: str) -> Outcome:
        out = self.out / "events"
        spec = self.cfg.comparison(cid)
        c = self.cohort(cid)
        stem = out / f"{cid}__{sid}"
        if kind == "events":
            from etcompare.events import analyse_events

            r = analyse_events(c, self.event_table(s.event_table).table, s,
                               station_daily=self.stations(self.comparison_sites(spec)))
            self.save(r.responses, Path(f"{stem}_responses.parquet"))
            self.save_csv(r.overlaps.frame, Path(f"{stem}_overlaps.csv"))
            self.save_csv(r.conditions.frame, Path(f"{stem}_conditions.csv"))
            self.save_text(r.report.summary(), Path(f"{stem}_report.txt"))
        else:
            from etcompare.anomalies import compute_anomalies

            r = compute_anomalies(c, s, self.cfg.metric_set(s.metric_set))
            self.save(r.daily, Path(f"{stem}_anomaly_daily.parquet"))
            self.save(r.metrics, Path(f"{stem}_anomaly_metrics.parquet"))
            self.save_text(r.report.summary(), Path(f"{stem}_report.txt"))
        return Outcome()

    def _water_balance_units(self) -> list[Unit]:
        units = []
        for wb in self.cfg.water_balances:
            scen = [s for s in self.cfg.scenarios if s.water_balance == wb.water_balance_id]

            def key(wb: Any = wb, scen: list[Any] = scen) -> Any:
                agg = self.cfg.period_aggregation(wb.period_aggregation)
                return [to_plain(wb), self.agg_key(agg), [to_plain(s) for s in scen],
                        self.station_ident([wb.site_id]),
                        self.idents(p for p in self.declared_paths(wb)
                                    if Path(p).suffix.lower() in (".csv", ".parquet"))]

            def run(wb: Any = wb, scen: list[Any] = scen) -> Outcome:
                return self._run_water_balance(wb, scen)

            units.append(Unit("analyze", "water_balance", f"water_balance:{wb.water_balance_id}",
                              key, run,
                              blocked=lambda wb=wb: self.stations_missing([wb.site_id])))
        return units

    def _run_water_balance(self, wb: Any, scen: list[Any]) -> Outcome:
        from etcompare.scenarios import run_scenario
        from etcompare.water_balance import compute_water_balance

        out = self.out / "water_balance"
        agg = self.cfg.period_aggregation(wb.period_aggregation)
        periods = wb.periods or agg.periods
        if "growing_season" in periods:
            agg = self.resolved_aggregation(agg)
        station = self.stations([wb.site_id])
        kw = {"period_aggregation": agg, "station": self.cfg.stations[wb.site_id]}
        r = compute_water_balance(wb, station, **kw)
        stem = wb.water_balance_id
        self.save(r.terms, out / f"{stem}_terms.parquet")
        self.save(r.residuals, out / f"{stem}_residuals.parquet")
        res = r.residuals.frame
        main = res[res["residual_kind"] == "measured_modeled"]
        text = [f"water balance {stem}: {wb.equation_text} ({wb.sign_convention})",
                f"declaration: {json.dumps(to_plain(wb), default=str)}", "",
                r.report.summary(), "",
                f"residual status: {main['status'].value_counts().to_dict()}",
                f"unknown terms: {main['unknown_terms'].value_counts(dropna=False).to_dict()}"]
        seeds = []
        for sc in scen:
            s = run_scenario(sc, wb, station, **kw)
            self.save(s.results, out / f"{stem}__{sc.scenario_id}_scenarios.parquet")
            text += ["", f"scenario {sc.scenario_id} ({sc.category}):", s.report.summary()]
            if getattr(sc, "seed", None) is not None:
                seeds.append({"unit": f"water_balance:{stem}", "scenario_id": sc.scenario_id,
                              "seed": sc.seed})
        if not scen:
            text += ["", "scenarios: none declared"]
        self.save_text("\n".join(text), out / f"{stem}_report.txt")
        return Outcome(details={"seeds": seeds} if seeds else {})

    def _ensemble_units(self) -> list[Unit]:
        units = []
        for spec in self.cfg.ensembles:
            if not spec.sources:
                raise ContractError(f"ensemble {spec.ensemble_id} declares no sources",
                                    config_key=f"ensembles.{spec.ensemble_id}.sources")
            have = [t.site_id for t in self.cfg.station_tables]
            sites = [s for s in (spec.sites or tuple(have)) if s in have]

            def key(spec: Any = spec, sites: list[str] = sites) -> Any:
                return [to_plain(spec), [self.source_ident(s) for s in spec.sources],
                        self.station_ident(sites), to_plain(self.cfg.metric_set(spec.metric_set)),
                        to_plain(self.cfg.bootstrap_spec(spec.bootstrap))
                        if spec.bootstrap and "bootstrap" not in self.cfg.run.skip else None,
                        to_plain(self.cfg.footprint_sets), to_plain(self.cfg.products)]

            def run(spec: Any = spec, sites: list[str] = sites) -> Outcome:
                return self._run_ensemble(spec, sites)

            units.append(Unit(
                "analyze", "ensembles", f"ensembles:{spec.ensemble_id}", key, run,
                blocked=lambda spec=spec, sites=sites: self.sources_missing(spec.sources)
                or self.stations_missing(sites)))
        return units

    def _run_ensemble(self, spec: Any, sites: list[str]) -> Outcome:
        from etcompare.ensemble import run_ensemble

        out = self.out / "ensembles"
        boot = (self.cfg.bootstrap_spec(spec.bootstrap)
                if spec.bootstrap and "bootstrap" not in self.cfg.run.skip else None)
        r = run_ensemble(spec, self.stations(sites), [self.source(s) for s in spec.sources],
                         products=self.cfg.products,
                         metric_spec=self.cfg.metric_set(spec.metric_set),
                         bootstrap_spec=boot, footprint_sets=self.cfg.footprint_sets)
        stem = spec.ensemble_id
        for name, t in r.tables().items():
            self.save(t, out / f"{stem}_{name.removeprefix('ensemble_')}.parquet")
        for scope, comp in r.comparisons.items():
            pre = out / f"{stem}__{scope}"
            for name, t in (("paired", comp.cohort.paired), ("metrics", comp.metrics.table),
                            ("contrasts", comp.ranking.contrasts),
                            *((("intervals", comp.bootstrap.intervals),) if comp.bootstrap
                              else ())):
                self.save(t, Path(f"{pre}_{name}.parquet"))
        self.save_text(
            f"ensemble {stem} (post-extraction; not a pixel-level ensemble)\n"
            f"declaration: {json.dumps(to_plain(spec), default=str)}\n\n{r.report.summary()}\n\n"
            + r.summary.frame[["scope", "subset", "status", "n_sites", "n_folds",
                               "n_folds_fitted", "n_days_compared", "status_detail"]]
            .to_string(index=False), out / f"{stem}_report.txt")
        return Outcome(details={"seeds": [{"unit": f"ensembles:{stem}",
                                           "bootstrap_id": boot.bootstrap_id,
                                           "seed": boot.seed}] if boot else []})

    def _representativeness_units(self) -> list[Unit]:
        units = []
        for spec in self.cfg.representativeness:
            def blocked(spec: RepresentativenessSpec = spec) -> tuple[str, str] | None:
                import importlib.util

                for mod in ("rasterio", "pyproj", "shapely"):
                    if importlib.util.find_spec(mod) is None:
                        return ("optional_dependency_missing",
                                f"landscape representativeness needs the geo extra ({mod} is "
                                "not installed): pip install 'etcompare[geo]'")
                for set_id in spec.footprint_sets:
                    if not Path(self.cfg.footprint_set(set_id).root).is_dir():
                        return ("input_missing", f"footprint set {set_id} root not found")
                return None

            def key(spec: RepresentativenessSpec = spec) -> Any:
                from etcompare.provenance import package_versions

                fps = [self.idents(self._listing(Path(self.cfg.footprint_set(s).root),
                                                 self.cfg.footprint_set(s).recursive))
                       for s in spec.footprint_sets]
                return [to_plain(spec), to_plain(self.cfg.stations[spec.site_id]),
                        [to_plain(self.cfg.footprint_set(s)) for s in spec.footprint_sets],
                        fps, self.idents(self.declared_paths(spec)),
                        package_versions("fluxfootprints")]

            def run(spec: RepresentativenessSpec = spec) -> Outcome:
                from etcompare.config import to_plain as plain
                from etcompare.provenance import package_versions

                t0 = time.time()
                res, notes = landscape(self.cfg, spec)
                out = self.out / "representativeness"
                rid = spec.representativeness_id
                names = {"landscape_class_fractions": "class_fractions",
                         "landscape_index_summaries": "index_summaries",
                         "landscape_method_differences": "differences",
                         "representativeness_labels": "labels",
                         "representativeness_availability": "availability"}
                for k, t in res.tables().items():
                    self.save(t, out / f"{rid}_{names[k]}.parquet")
                from etcompare.representativeness import FOOTPRINT_METHOD

                self.save_text("\n".join([
                    f"representativeness {rid} ({spec.site_id})",
                    "Landscape description of each support; not an ET accuracy guarantee.",
                    f"settings: {plain(spec)}",
                    f"versions: {package_versions(*LANDSCAPE_VERSIONED)}",
                    *notes, "", *landscape_summary(res), "", res.report.summary(),
                    f"footprint method: {FOOTPRINT_METHOD}",
                    f"elapsed {time.time() - t0:.0f} s"]), out / f"{rid}_report.txt")
                return Outcome()

            units.append(Unit("analyze", "representativeness",
                              f"representativeness:{spec.representativeness_id}", key, run,
                              blocked=blocked))
        return units


# ====================================================================== helpers

def _dir_blocked(root: Any) -> Callable[[], tuple[str, str] | None]:
    def blocked() -> tuple[str, str] | None:
        return None if Path(root).is_dir() else ("input_missing", f"{root} is not a directory")
    return blocked


def _counts(log: ExclusionLog, unit: str) -> list[dict[str, Any]]:
    return [{"stage": str(stage), "unit_id": unit, "reason_code": str(code), "n": int(n)}
            for (stage, code), n in log.counts().items()] if len(log) else []


LANDSCAPE_VERSIONED = ("etcompare", "numpy", "pandas", "scipy", "rasterio", "pyproj", "shapely",
                       "geopandas", "fluxfootprints")


def landscape(cfg: Config, spec: RepresentativenessSpec) -> tuple[Any, list[str]]:
    """Read every input of one representativeness analysis and run ``assess_landscape``.

    Needs the ``geo`` extra (and, for the optional labels, fluxfootprints).
    """
    import numpy as np

    from etcompare.adapters.fluxfootprints_labels import (
        categorical_labels,
        fluxfootprints_status,
        upstream_parameters,
        version_label,
    )
    from etcompare.footprints import prepare_footprint
    from etcompare.io.manifest import discover_footprints
    from etcompare.io.rasters import read_footprint, read_header
    from etcompare.provenance import file_sha256
    from etcompare.registry import SiteRegistry
    from etcompare.representativeness import (
        FootprintDay,
        LabelSource,
        assess_landscape,
        nearest_pixel_support,
        pixel_window_support,
        polygon_support,
        read_layer_asset,
    )
    from etcompare.spatial import same_crs, transform_coords

    def tower(lon: float, lat: float, crs: str) -> tuple[float, float]:
        x, y = transform_coords(np.array([lon]), np.array([lat]), "EPSG:4326", crs)
        return float(x[0]), float(y[0])

    station = cfg.stations[spec.site_id]
    lon, lat = station.longitude, station.latitude
    if lon is None or lat is None:
        raise ContractError(f"station {spec.site_id}: coordinates needed",
                            config_key=f"stations.{spec.site_id}.latitude")
    notes: list[str] = []
    unavailable: list[dict[str, Any]] = []
    days: dict[str, list[Any]] = {}
    unusable: dict[str, dict[pd.Timestamp, str]] = {}
    extent = 0.0
    for set_id in spec.footprint_sets:
        fset = cfg.footprint_set(set_id)
        man = discover_footprints(fset, sites=SiteRegistry(cfg.site_aliases)).manifest.frame
        man = man[(man["site_id"] == spec.site_id) & (man["period"] == "day")]
        if spec.start:
            man = man[man["date"] >= pd.Timestamp(spec.start)]
        if spec.end:
            man = man[man["date"] <= pd.Timestamp(spec.end)]
        days[set_id], unusable[set_id] = [], {}
        for r in man.sort_values("date").itertuples():
            raw, grid, nod = read_footprint(r.path)
            fp = prepare_footprint(raw, grid, value_semantics=fset.value_semantics, nodata=nod,
                                   negative_tolerance=spec.negative_weight_tolerance)
            if fp.ok:
                days[set_id].append(FootprintDay(pd.Timestamp(r.date), fp, r.asset_id))
                tx, ty = tower(lon, lat, grid.crs)
                cx, cy = grid.corners()
                extent = max(extent, float(np.hypot(cx - tx, cy - ty).max()))
            else:
                unusable[set_id][pd.Timestamp(r.date)] = f"footprint_{fp.status}"
        notes.append(f"footprint set {set_id}: {len(days[set_id])} usable days, "
                     f"{len(unusable[set_id])} unusable ({fset.weighting_method}; "
                     f"{fset.value_semantics})")
    flux = spec.fluxfootprints_labels
    half = max(extent, max(flux["radii"]) if flux else 0.0) + 60.0
    layers: dict[str, list[Any]] = {}
    tower_xy: dict[str, tuple[float, float]] = {}
    for name, layer in spec.layers.items():
        got = []
        for i, a in enumerate(layer.assets):
            try:
                got.append(read_layer_asset(layer, i, x=lon, y=lat, xy_crs="EPSG:4326",
                                            half_width_m=half))
            except OSError as exc:
                unavailable.append({"analysis": "landscape_layer", "layer": name,
                                    "code": "layer_unreadable", "reason": f"{a.path}: {exc}"})
                got = []
                break
            notes.append(f"layer {name} asset {a.path.name}: sha256 {file_sha256(a.path)[:16]}, "
                         f"reference {a.period[0]}..{a.period[1]}, window "
                         f"{got[-1].grid.height}x{got[-1].grid.width} cells")
        if got:
            layers[name] = got
            tower_xy[name] = tower(lon, lat, got[0].grid.crs)
    supports = []
    if {"pixel_window", "nearest_tower_pixel"} & set(spec.methods):
        try:
            ref = read_header(spec.reference_grid_raster).grid
        except OSError as exc:
            for m in spec.method_ids():
                if not m.startswith("polygon_"):
                    unavailable.append({"analysis": "method_difference", "method": m,
                                        "code": "reference_grid_unreadable", "reason": str(exc)})
            ref = None
        if ref is not None:
            if spec.reference_grid_id and ref.grid_id != spec.reference_grid_id:
                raise ContractError(f"reference grid {ref.grid_id} != declared "
                                    f"{spec.reference_grid_id}",
                                    config_key="representativeness.reference_grid_id")
            tx, ty = tower(lon, lat, ref.crs)
            if "pixel_window" in spec.methods:
                supports += [pixel_window_support(ref, tx, ty, n) for n in spec.window_sizes]
            if "nearest_tower_pixel" in spec.methods:
                supports.append(nearest_pixel_support(ref, tx, ty))
            notes.append(f"reference grid {ref.grid_id} ({ref.crs}) from "
                         f"{spec.reference_grid_raster}")
    for p in spec.polygons:
        import geopandas as gpd
        import shapely

        gdf = gpd.read_file(p.path, layer=p.layer)
        if gdf.crs is None:
            notes.append(f"polygon {p.polygon_id}: file has no CRS; declared {p.crs} used")
        elif not same_crs(gdf.crs.to_wkt(), p.crs):
            raise ContractError(f"polygon {p.polygon_id}: file CRS differs from declared {p.crs}")
        sel = gdf[gdf[p.id_column].astype(str) == p.feature_id]
        if len(sel) != 1:
            raise ContractError(f"polygon {p.polygon_id}: {len(sel)} features with "
                                f"{p.id_column} = {p.feature_id}")
        supports.append(polygon_support(p.polygon_id, sel.geometry.iloc[0], p.crs))
        tx, ty = tower(lon, lat, p.crs)
        inside = bool(shapely.contains_xy(sel.geometry.iloc[0], tx, ty))
        notes.append(f"polygon {p.polygon_id}: {p.id_column}={p.feature_id}, area "
                     f"{sel.geometry.iloc[0].area:.0f} m2 (in {p.crs}), tower inside: {inside}, "
                     f"vintage {p.vintage}")
    label_source = None
    if flux:
        st = fluxfootprints_status()
        notes.append(f"fluxfootprints: {st}")
        if not st["available"]:
            for name in flux["layers"]:
                unavailable.append({"analysis": "fluxfootprints_labels", "layer": name,
                                    "code": "fluxfootprints_not_available",
                                    "reason": st["reason"]})
        else:
            radii, alpha = flux["radii"], flux["alpha"]

            def labeler(contrib: Any, classes: Any, asset: Any, layer: Any, txy: Any) -> Any:
                return categorical_labels(contrib, classes, asset.grid, layer, txy, radii=radii,
                                          alpha=alpha)

            label_source = LabelSource(labeler, version_label(st),
                                       {"layers": flux["layers"],
                                        **upstream_parameters(radii, alpha)}, dict(tower_xy))
    t = time.time()
    res = assess_landscape(spec, footprint_days=days, layers=layers, fixed_supports=supports,
                           footprint_unusable=unusable, station_land_cover=station.land_cover,
                           label_source=label_source, unavailable=unavailable)
    notes.append(f"assess_landscape: {time.time() - t:.0f} s")
    return res, notes


def landscape_summary(res: Any) -> list[str]:
    """Text summary of a landscape result (class shares, denominators, labels, gaps)."""
    out = []
    cf = res.class_fractions.frame
    if len(cf):
        cls = cf[cf["class_role"] == "class"]
        g = cls.groupby(["layer_id", "matching_policy", "extraction_method", "domain",
                         "class_code", "class_name"])["class_frac_of_valid"]
        tab = g.agg(["mean", "min", "max", "size"]).reset_index()
        tab = tab[tab["mean"] >= 0.005]
        out += ["Class share of valid weight (mean / min / max over days):",
                tab.to_string(index=False, float_format=lambda v: f"{v:.3f}")]
        den = cf.drop_duplicates(["layer_id", "matching_policy", "extraction_method", "domain",
                                  "date"])
        d = den.groupby(["layer_id", "matching_policy", "extraction_method", "domain"]).agg(
            n_days=("date", "size"), valid=("valid_frac_of_grid_total", "mean"),
            nodata=("nodata_frac_of_grid_total", "mean"),
            outside=("outside_layer_frac_of_grid_total", "mean"),
            domain_frac=("domain_frac_of_grid_total", "mean"),
            retro=("retrospective", "mean"), year_mismatch_max=("year_mismatch", "max"))
        out += ["Denominators (means over days):",
                d.reset_index().to_string(index=False, float_format=lambda v: f"{v:.4f}")]
    df = res.differences.frame
    if len(df):
        q = df[df["quantity_kind"] == "class_fraction"]
        g = q.groupby(["layer_id", "matching_policy", "domain", "compared_method", "quantity"])[
            "difference"].describe(percentiles=[0.05, 0.5, 0.95])
        g = g[(g["mean"].abs() >= 0.005) | (g["max"].abs() >= 0.05)]
        out += ["Footprint minus fixed support, class share of valid weight (frozen days):",
                g.reset_index().to_string(index=False, float_format=lambda v: f"{v:.3f}")]
    lab = res.labels.frame
    if len(lab):
        fl = lab[lab["label_source"] == "fluxfootprints"]
        if len(fl):
            t = fl.groupby(["layer_id", "matching_policy", "domain", "radius_m", "label"]).size()
            out += ["fluxfootprints labels (days per label; labels, not ET accuracy):",
                    t.unstack("label", fill_value=0).to_string()]
        st = lab[lab["label_source"] == "station_metadata"]
        out += [f"station label: {r.label} ({r.label_meaning})" for r in st.itertuples()]
    av = res.availability.frame
    na = av[av["status"] == "not_available"]
    if len(na):
        out += ["Not available:",
                na.groupby(["analysis", "layer_id", "extraction_method", "matching_policy",
                            "reason_code"]).size().rename("rows").reset_index()
                .to_string(index=False)]
    return out


def diagnostics_text(dg: Any) -> str:
    """Skipped analyses, closure magnitude (station record) and daily MAE per bin."""
    a = dg.availability.frame
    na = a[a["status"] == "not_available"][["analysis", "variable", "reference_variant",
                                            "extraction_method", "reason_code"]]
    cl = dg.closure.frame
    cl = cl[(cl["day_set"] == "station_record") & (cl["period_type"] == "all")][
        ["quantity", "n_days", "n_days_filled_excluded", "mean", "median", "q05", "q95"]]
    c = dg.conditional.frame
    c = c[(c["metric"] == "mae") & (c["bin_index"] >= 0)
          & (c["block_length"] == c["block_length"].min())]
    if not len(c):
        wide = pd.DataFrame()
    else:
        c = c.assign(cell=[f"{v:.2f}" if s == "ok" else s
                           for v, s in zip(c["value"], c["status"], strict=True)])
        wide = c.pivot_table(index=["variable", "bin_index", "bin_label"],
                             columns=["reference_variant", "extraction_method", "model"],
                             values="cell", aggfunc="first")
        n = c.drop_duplicates(["variable", "bin_index"]).set_index(
            ["variable", "bin_index", "bin_label"])["n_days_bin_cohort"]
        wide.insert(0, "n_days", n)
    with pd.option_context("display.width", 300, "display.max_columns", 40,
                           "display.max_rows", 500, "display.max_colwidth", 60):
        return (f"not available:\n{na.to_string(index=False)}\n\n"
                f"closure magnitude, corrected_from_raw station days:\n"
                f"{cl.to_string(index=False)}\n\n"
                f"daily MAE per bin (status instead of a value for sparse bins):\n"
                f"{wide.to_string()}")


def ranking_text(results: Sequence[Any]) -> str:
    """Daily ranks per block length: rank (top-rank frequency) [rank interval], leaders."""
    f = pd.concat([r.rankings.frame for r in results], ignore_index=True)
    d = f[(f["product"] == "daily_pairs") & f["metric"].isin(["mae", "rmse", "mbe", "kge_2009"])]
    if not len(d):
        return "no daily ranks"
    d = d.assign(cell=[
        (f"{r:g}" if pd.notna(r) else "-")
        + (f" ({t:.2f}) [{lo:g}-{hi:g}]" if s == "ok" else f" {s}")
        for r, t, lo, hi, s in zip(d["rank"], d["top_rank_frequency"], d["rank_lower"],
                                   d["rank_upper"], d["status"], strict=True)])
    keys = ["subset", "reference_variant", "extraction_method", "metric", "model", "value"]
    wide = d.pivot_table(index=keys, columns="block_length", values="cell",
                         aggfunc="first").reset_index()
    wide.columns = [f"b={c}" if isinstance(c, int | float) else c for c in wide.columns]
    lead = (d.drop_duplicates(["subset", "reference_variant", "extraction_method", "metric",
                               "block_length"])
            .pivot_table(index=["subset", "reference_variant", "extraction_method", "metric"],
                         columns="block_length", values="leader_status", aggfunc="first"))
    n = pd.concat([r.network.frame for r in results], ignore_index=True)
    with pd.option_context("display.width", 250, "display.max_columns", 30,
                           "display.max_rows", 500):
        return (f"daily ranks: rank (top-rank frequency, a bootstrap stability measure) "
                f"[rank interval]:\n{wide.to_string(index=False)}\n\nleader status:\n"
                f"{lead.to_string()}\n\nsummaries across sites: n_sites = "
                f"{sorted(int(x) for x in set(n['n_sites']))}, statuses "
                f"{sorted(set(n['status']))} (one site: station results only)")


def bootstrap_text(boot: Any) -> str:
    """Status counts, daily RMSE/MBE intervals side by side per block length, ACF summary."""
    f = boot.intervals.frame
    status = (f.groupby(["temporal_scale", "block_length", "status"]).size()
              .unstack(fill_value=0))
    d = f[(f["product"] == "daily_pairs") & (f["metric"].isin(["mbe", "rmse"]))
          & (f["interval_method"] == "percentile")].copy()
    d["interval"] = [f"{lo:.3f}..{hi:.3f}" if s == "ok" else s
                     for lo, hi, s in zip(d["lower"], d["upper"], d["status"], strict=True)]
    keys = ["cohort_id", "model", "reference_variant", "extraction_method",
            "contrast_dimension", "contrast_baseline", "metric", "estimate", "n_days"]
    wide = d.pivot_table(index=keys, columns="block_length", values="interval",
                         aggfunc="first").reset_index() if len(d) else pd.DataFrame()
    wide.columns = [f"b={c}" if isinstance(c, int | float) else c for c in wide.columns]
    a = boot.autocorrelation.frame
    acf_text = ""
    if len(a):
        lags = [k for k in (1, 7, 14, 28) if k in set(a["lag"])]
        acf = a[a["lag"].isin(lags)].pivot_table(
            index=["cohort_id", "model", "reference_variant", "extraction_method"],
            columns="lag", values="autocorrelation").round(2)
        below = a[a["autocorrelation"].abs() < a["white_noise_bound"]]
        first = below.groupby(["cohort_id", "model", "reference_variant",
                               "extraction_method"])["lag"].min().rename(
                                   "first_lag_within_bound")
        acf = acf.join(first)
        acf.columns = [f"lag{c}" if isinstance(c, int) else c for c in acf.columns]
        acf_text = acf.to_string()
    with pd.option_context("display.width", 250, "display.max_columns", 30):
        return (f"interval rows by status:\n{status.to_string()}\n\n"
                f"daily MBE / RMSE percentile intervals by block length:\n"
                f"{wide.to_string(index=False)}\n\n"
                f"residual autocorrelation of daily errors:\n{acf_text}")
