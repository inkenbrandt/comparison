"""Run workflow stages and write the run manifest (guide step 17).

:func:`run_stages` is what every CLI subcommand calls: ``validate``, ``index``,
``extract``, ``analyze`` and ``report`` run their stage, and ``run`` chains all five,
reusing every unit whose cache is still valid. Each call is one run with its own
manifest (:class:`~etcompare.runrecord.RunManifest`) and JSON-lines log under
``<output_dir>/runs/<run_id>/``.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

from etcompare import __version__
from etcompare.config import WORKFLOW_STAGES, Config, to_plain
from etcompare.io.atomic import write_csv_atomic
from etcompare.log import add_log_file, get_logger, log_event
from etcompare.provenance import (
    canonical_hash,
    code_identity,
    detect_upstream,
    file_sha256,
    git_state,
    package_versions,
)
from etcompare.resilience import StallWatchdog, keep_awake
from etcompare.runrecord import RunManifest, UnitRecord, new_run_id, status_table, utc_now

_log = get_logger("runner")

#: Packages whose versions every manifest records.
VERSIONED = ("etcompare", "numpy", "pandas", "pyarrow", "scipy", "pyyaml", "matplotlib",
             "rasterio", "pyproj", "shapely", "geopandas", "fluxfootprints")


def config_hash(cfg: Config) -> str:
    """SHA-256 of the whole declaration (paths resolved; the file location left out)."""
    plain = to_plain(cfg)
    plain.pop("source_path", None)
    return canonical_hash(plain)


def _manifest(cfg: Config, run_id: str, command: str, stages: Sequence[str]) -> RunManifest:
    src = cfg.source_path
    return RunManifest(
        run_id=run_id, command=command, stages=list(stages), created_utc=utc_now(),
        config_path=str(src) if src else None,
        config_sha256=file_sha256(src) if src and Path(src).is_file() else None,
        config_hash=config_hash(cfg), code_identity=code_identity(),
        etcompare_version=__version__, git=git_state(),
        package_versions=package_versions(*VERSIONED),
        upstream_declared=[to_plain(u) for u in cfg.upstream],
        upstream_detected=detect_upstream(), cache=to_plain(cfg.cache))


def _collect(manifest: RunManifest, records: Sequence[UnitRecord], inputs: dict[str, Any]
             ) -> None:
    for r in records:
        d = r.details or {}
        for row in d.get("exclusions", []):
            manifest.exclusion_counts.append({"stage": r.stage, **row})
        manifest.raster_grids += d.get("raster_grids", [])
        manifest.masks += d.get("masks", [])
        manifest.seeds += d.get("seeds", [])
        manifest.outputs += list(r.outputs)
        manifest.analyses.append({**r.row(), "details_notes": d.get("notes")})
    manifest.inputs = [inputs[k] for k in sorted(inputs)]


def run_stages(cfg: Config, stages: Sequence[str], *, command: str, force: bool = False,
               select: Callable[[Any], bool] | None = None
               ) -> tuple[RunManifest, list[UnitRecord]]:
    """Run ``stages`` in order; return the manifest and the unit records of this run.

    ``select`` keeps only the planned units for which it returns true (the
    ``tools/run_*.py`` scripts run one analysis this way).

    A configuration or data error raises before or during a stage; the manifest is
    still written with ``status: failed`` and the error, then the error propagates.
    """
    from etcompare.workflow import Workflow

    bad = [s for s in stages if s not in WORKFLOW_STAGES]
    if bad:
        raise ValueError(f"unknown stage(s) {bad}; expected {list(WORKFLOW_STAGES)}")
    run_id = new_run_id(config_hash(cfg))
    wf = Workflow(cfg, run_id=run_id, force=force)
    manifest = _manifest(cfg, run_id, command, stages)
    run_dir = wf.out / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    handler = add_log_file(str(run_dir / "log.jsonl"))
    log_event(_log, "run_started", run_id=run_id, stages=list(stages), command=command,
              config=manifest.config_path, config_hash=manifest.config_hash)
    # Written now with status "running": a run killed outright leaves this record.
    manifest.write(wf.out)
    proc = cfg.processing

    def stalled(msg: str) -> None:
        manifest.status, manifest.error = "failed", f"StallTimeoutError: {msg}"
        manifest.finished_utc = utc_now()
        manifest.write(wf.out)

    try:
        with keep_awake(proc.keep_awake), StallWatchdog(
                proc.stall_warning_s, proc.stall_timeout_s, on_timeout=stalled):
            for stage in stages:
                log_event(_log, "stage_started", stage=stage)
                if stage == "validate":
                    wf.validate()
                elif stage == "report":
                    from etcompare.reporting import report_unit

                    wf.execute([report_unit(wf)])
                else:
                    wf.execute([u for u in wf.plan(stage) if select is None or select(u)])
        manifest.status = "completed"
    except BaseException as exc:
        manifest.status = "failed"
        manifest.error = f"{type(exc).__name__}: {exc}"[:4000]
        log_event(_log, "run_failed", level=logging.ERROR, error=manifest.error)
        raise
    finally:
        records = list(wf.records.values())
        if select is None and any(s in stages for s in ("index", "extract", "analyze")):
            records += [r for r in wf.undeclared() if r.stage in stages]
        _collect(manifest, records, wf.inputs)
        manifest.finished_utc = utc_now()
        from etcompare.io.tables import write_table

        write_table(status_table(records), run_dir / "analysis_status.parquet")
        write_csv_atomic(status_table(records).frame, run_dir / "analysis_status.csv")
        manifest.write(wf.out)
        log_event(_log, "run_finished", run_id=run_id, status=manifest.status,
                  n_units=len(records))
        logging.getLogger("etcompare").removeHandler(handler)
        handler.close()
    return manifest, records


def tool_main(stage: str, analyses: Iterable[str], description: str | None = None,
              argv: Sequence[str] | None = None) -> int:
    """Entry point of the ``tools/run_*.py`` scripts: one stage, some analyses, optional IDs.

    ``ids`` keep only units whose ID names one of them (e.g. a comparison, an ensemble or a
    raster extraction). Everything else is declared in the configuration. Exit codes as
    :func:`etcompare.cli.main`.
    """
    from etcompare.cli import CONFIGURATION_ERRORS, EXIT_CONFIG, EXIT_ERROR, EXIT_OK
    from etcompare.config import load_config
    from etcompare.log import configure_logging

    ap = argparse.ArgumentParser(description=description,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("config")
    ap.add_argument("ids", nargs="*", help="unit IDs to run (default: every declared one)")
    ap.add_argument("--force", action="store_true", help="rebuild instead of reusing cache")
    args = ap.parse_args(argv)
    configure_logging(logging.INFO, json_lines=False)
    wanted = set(analyses)

    def select(unit: Any) -> bool:
        if unit.analysis not in wanted:
            return False
        parts = set(re.split(r":|__", unit.unit_id))
        return not args.ids or bool(parts & set(args.ids))

    try:
        cfg = load_config(args.config)
        manifest, records = run_stages(cfg, [stage], command=" ".join(sys.argv),
                                       force=args.force, select=select)
    except CONFIGURATION_ERRORS as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    except Exception as exc:  # noqa: BLE001 - reported, then a nonzero exit
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_ERROR
    if not records:
        print(f"no {sorted(wanted)} unit matches {args.ids or 'the configuration'}",
              file=sys.stderr)
        return EXIT_CONFIG
    for r in records:
        print(f"{r.status:13} {r.unit_id}" + (f": {r.reason_code} - {r.reason}"
                                             if r.status not in ("completed",) else ""))
    print(f"run {manifest.run_id} {manifest.status}; manifest "
          f"{cfg.output_dir}/runs/{manifest.run_id}/run_manifest.json")
    return EXIT_OK
