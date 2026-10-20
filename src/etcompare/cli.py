"""Command-line interface (guide step 17)::

    etcompare validate config.yaml   read every declared table through its adapter
    etcompare index    config.yaml   index rasters and footprints (manifests, conflicts)
    etcompare extract  config.yaml   footprint aggregation, raster extraction, footprint sets
    etcompare analyze  config.yaml   cohorts, periods, metrics, intervals, rankings, ...
    etcompare report   config.yaml   figures, Markdown/HTML report, skipped-analyses table
    etcompare run      config.yaml   all five; units whose cache is valid are reused
    etcompare demo     DIRECTORY     write the fixed-seed synthetic demo (--rasters: GeoTIFFs too)

Each subcommand is a thin layer over :func:`etcompare.runner.run_stages`. Everything an
analysis needs is declared in the configuration; the only options are logging and
``--force`` (rebuild instead of reusing cached outputs).

Exit codes: 0 success; 2 configuration, contract or data-validation error; 1 any other
error. Structured logs go to stderr (JSON lines by default) and to
``<output_dir>/runs/<run_id>/log.jsonl``.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence

from etcompare import __version__
from etcompare.config import WORKFLOW_STAGES
from etcompare.errors import (
    ContractError,
    DataValidationError,
    DuplicateAssetError,
    FilenameParseError,
    SchemaError,
)

#: Errors that mean the declaration or the inputs are wrong (exit code 2).
CONFIGURATION_ERRORS = (ContractError, DataValidationError, SchemaError, FilenameParseError,
                        DuplicateAssetError, FileNotFoundError)
EXIT_OK, EXIT_ERROR, EXIT_CONFIG = 0, 1, 2

_HELP = {
    "validate": "read every declared table input through its adapter",
    "index": "index OpenET rasters and footprint sets",
    "extract": "aggregate footprints, extract rasters, compare footprint sets",
    "analyze": "run every declared analysis (steps 7-16)",
    "report": "write figures, tables and the Markdown/HTML report",
    "run": "validate, index, extract, analyze and report; reuse valid cached outputs",
    "demo": "write the synthetic demonstration inputs and config.yaml (offline, no credentials)",
}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="etcompare", description="Compare OpenET model ET with eddy covariance station "
        "ET, as declared in a YAML configuration.")
    p.add_argument("--version", action="version", version=f"etcompare {__version__}")
    sub = p.add_subparsers(dest="command", required=True)
    for name in (*WORKFLOW_STAGES, "run"):
        s = sub.add_parser(name, help=_HELP[name], description=_HELP[name])
        s.add_argument("config", help="configuration file (YAML)")
        s.add_argument("--force", action="store_true",
                       help="rebuild every unit instead of reusing cached outputs")
        s.add_argument("--log-format", choices=("json", "text"), default="json",
                       help="stderr log format (the run log file is always JSON lines)")
        s.add_argument("--log-level", default="INFO",
                       choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    d = sub.add_parser("demo", help=_HELP["demo"], description=_HELP["demo"])
    d.add_argument("directory", help="where to write data/ and config.yaml")
    d.add_argument("--rasters", action="store_true",
                   help="also write synthetic GeoTIFFs, footprints and a land-cover map "
                        "(needs the geo extra), so index, extract and representativeness run")
    d.add_argument("--seed", type=int, default=None, help="generator seed (default: fixed)")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "demo":
        from etcompare.demo import DEMO_SEED, write_demo

        path = write_demo(args.directory, rasters=args.rasters,
                          seed=DEMO_SEED if args.seed is None else args.seed)
        print(f"synthetic demo written; run it with: etcompare run {path}")
        return EXIT_OK
    from etcompare.config import load_config
    from etcompare.log import configure_logging, get_logger, log_event
    from etcompare.runner import run_stages

    configure_logging(getattr(logging, args.log_level), json_lines=args.log_format == "json")
    log = get_logger("cli")
    stages = list(WORKFLOW_STAGES) if args.command == "run" else [args.command]
    try:
        cfg = load_config(args.config)
        manifest, records = run_stages(cfg, stages, command=" ".join(
            ["etcompare", *(argv if argv is not None else sys.argv[1:])]), force=args.force)
    except CONFIGURATION_ERRORS as exc:
        log_event(log, "configuration_error", level=logging.ERROR,
                  error_type=type(exc).__name__, error=str(exc))
        print(f"etcompare: configuration error: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    except Exception as exc:  # noqa: BLE001 - reported, then a nonzero exit
        log_event(log, "error", level=logging.ERROR, error_type=type(exc).__name__,
                  error=str(exc))
        print(f"etcompare: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_ERROR
    counts: dict[str, int] = {}
    for r in records:
        counts[r.status] = counts.get(r.status, 0) + 1
    print(f"run {manifest.run_id}: {manifest.status}; units "
          + ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
          + f"; manifest {cfg.output_dir}/runs/{manifest.run_id}/run_manifest.json")
    for r in records:
        if r.status in ("not_available", "failed") or (
                r.status == "skipped" and r.reason_code != "not_declared"):
            print(f"  {r.status:13} {r.unit_id}: {r.reason_code} - {r.reason}")
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
