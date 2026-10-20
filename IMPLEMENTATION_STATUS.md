# Implementation status

Tracks progress against `openet_ec_library_ai_implementation_guide.md`. Newest entries first.

| Step | Status |
|---|---|
| 1 — Inspect inputs, freeze analysis contract | **Done** 2026-10-13 |
| 2 — Package skeleton | **Done** 2026-10-13 |
| 3 — Canonical tables and result objects | **Done** 2026-10-13 |
| 4 — File discovery and upstream adapters | **Done** 2026-10-13 (exported-file adapters; see limitations) |
| 5 — Spatial extraction | **Done** 2026-10-13 (footprint set B; polygon area-weighted mode not implemented: no field polygons) |
| 6 — Footprint aggregation sensitivity | **Done** 2026-10-14 (NLDAS-ETo primary and measured-LE sensitivity footprints built from one per-timestep stack) |
| 7 — Comparison cohorts | **Done** 2026-10-14 |
| 8 — Temporal aggregation and completeness | **Done** 2026-10-15; imputed estimated totals and crop-height growing season added the same day. The US-UTJ real-data run was completed on 2026-10-15 (see "Steps 5–6 outputs regenerated" below) |
| 9 — Performance metrics | **Done** 2026-10-15; US-UTJ growing-season metrics run 2026-10-16 (D-046) |
| 10 — Paired block-bootstrap intervals | **Done** 2026-10-16 (station level; defaults Provisional, no block length adopted) |
| 11 — Ranking and paired contrasts | **Done** 2026-10-16 (station level; defaults Provisional; network summaries have n_sites = 1 for US-UTJ) |
| 12 — Closure, coverage and environmental diagnostics | **Done** 2026-10-16 (station level; defaults Provisional; several diagnostics `not_available` for US-UTJ by design) |
| 13 — Landscape representativeness and satellite support | **Done** 2026-10-17 (station level; defaults Provisional; no matching policy adopted; satellite support `not_available` for US-UTJ, D-011) |
| 14 — Anomalies and management events | **Done** 2026-10-17 (station level; defaults Provisional, chosen by the assistant at the user's request; uncontrolled before/after only; no held-out anomaly skill possible for US-UTJ) |
| 15 — Water-balance checks and scenario uncertainty | **Done** 2026-10-17 (station level; US-UTJ declaration Provisional; **the US-UTJ residual is unknown**: five terms have no input; no scenario declared) |
| 16 — Held-out ensemble experiments | **Done** 2026-10-18 (station level; post-extraction ensembles; US-UTJ choices made by the user; **the US-UTJ fitted ensemble is declined**: one calendar-year fold, one station) |
| 17 — Plots, reports, configuration and CLI | **Done** 2026-10-19 (workflow units with exact cache invalidation, run manifests, reports from saved tables, synthetic demo; US-UTJ and the seven Utah stations rerun through the CLI) |
| 18 — Resilient processing and first release | **Done** 2026-10-20 (version 0.1.0, MIT, unpublished; retries, keep-awake, stall detection, checkpoints, systematic-failure rule, atomic outputs; full raster demo; Sphinx docs, notebooks, CI; US-UTJ and Utah tables bit-identical) |

## Step 18 — Resilient processing and a usable first release (2026-10-20)

**User decisions (2026-10-20; D-079, D-080):**
- No concurrency.
- Keep-awake on.
- Retries as proposed.
- No staging; the user works from a local drive.
- Monthly checkpoints.
- Strict off, with a 1 % systematic threshold.
- An open licence: MIT, chosen by the assistant.
- Version 0.1.0; the name stays `etcompare`; Keep a Changelog.
- Plain Markdown docs, plus Sphinx for Read the Docs.
- GitHub Actions.
- A pinned demo requirements file.
- Three committed notebooks, outputs stripped.
- The full raster demo extends the tables demo.
- Verification on US-UTJ and the seven Utah stations.

**Run it**
```bash
python -m etcompare demo demo --rasters && python -m etcompare run demo/config.yaml   # ~4.5 min offline
uv run --no-sync python -m pytest            # 867 passed, 3 skipped (opt-in Drive tests), ~16 min
uv run --no-sync ruff check src tests tools/run_*.py tools/make_data_dictionary.py && uv run --no-sync mypy
uv run --no-sync python -m sphinx -b html -W --keep-going docs docs/_build/html
python tools/make_data_dictionary.py         # regenerate docs/data_dictionary.md
```

### Survey (before building; D-079)
- **Raster path.** One open per raster, bounded windows, no dense stacks. Memory grows only with output rows and the index.
- **Failure modes found:**
  - a non-strict extraction with every raster unreadable completed with zero rows;
  - an unreadable raster left no row in the extraction table;
  - CSV, text and figure writes were not atomic;
  - an interrupted extraction kept no progress;
  - nothing detected a stall.
- **Benchmark** (serial, instrumented):
  - J: clipped US-UTJ: 22 s, 86 % I/O (file opens about 5 ms each), peak +24 MB working set.
  - M: unclipped US-UTJ, 4 methods: 88–94 s, 95 % I/O (opens about 23 ms each).
- **The 9-hour pause (D-078) was idle sleep.** The Windows System log shows Kernel-Power 42, *System Idle*, one second after the last progress record; the AC sleep timeout is 900 s.

### API
- **`etcompare.config.ProcessingSpec`** (`processing:`): `keep_awake`, `io_attempts`, `io_backoff_s`, `stall_warning_s`, `stall_timeout_s`, `checkpoint`, `max_unreadable_fraction`.
- **`etcompare.resilience`:** `RetryPolicy`, `call_with_retries`, `keep_awake`, `StallWatchdog` (with `beat`/`rest`), `StallTimeoutError`, `Checkpoint`.
- **`etcompare.io.atomic`:** `atomic_path`, `write_text_atomic`, `write_csv_atomic`, `write_json_atomic`.
- **`etcompare.errors`:** `InputMissingError` (a `FileNotFoundError`), `SystematicFailureError` (a `DataValidationError`).
- **`extract_rasters(..., retry=, max_unreadable_fraction=, checkpoint=)`**: new codes `raster_missing` and `footprint_file_missing`; unreadable model-days keep null rows.
- **`etcompare.demo.write_demo(..., rasters=True)`** and **`etcompare demo DIR [--rasters] [--seed]`**.
- **`etcompare.schemas.data_dictionary()`** and **`etcompare.reporting.skipped_file_rows`**.

### Delivered
- **Resilience.** Retries logged as `io_retry`/`io_failed`.
  - Keep-awake.
  - Stall and suspend detection, armed only while rasters are read.
  - Monthly checkpoints under `.etcompare/checkpoints/<unit>/<key>/`, with a SHA-256-sealed record; pruned when the key changes, cleared after success.
  - The systematic-failure rule: exit 2, manifest `failed`, exclusions written, the table not written.
  - Atomic writes everywhere.
  - The manifest is written as `running` at the start.
- **Release files.**
  - `LICENSE` (MIT), `CHANGELOG.md`, version 0.1.0.
  - `pyproject` metadata and a `docs` dependency group.
  - Sphinx docs: index, getting started, configuration, method guide, limitations, one API page per module, generated data dictionary, references, decisions, input contract, changelog.
  - `.readthedocs.yaml`, `.github/workflows/ci.yml`, `examples/demo/requirements-demo.txt`, `notebooks/01–03`.
- **Configuration.**
  - Both example configs declare `processing:` (Provisional, D-079 in `decisions:`).
  - `examples/us_utj.yaml` reads the `unclipped/` Drive folder (D-054 resolved), so its four extractions now run.
- **Fixes.**
  - Invalid YAML gives exit code 2.
  - `--log-level` filters stderr.
  - `write_footprint` removes its `.part` file on a failed write.

### Checks
- **`tests/test_resilience.py`** (18):
  - atomic writes keep the old file and leave no temp file, and match the plain writers byte for byte;
  - retries are finite and logged, and are never applied to contract errors or vanished files;
  - the processing section is validated;
  - keep-awake;
  - stall warning, suspend detection, stall timeout, and disarming after extraction;
  - checkpoint key and integrity;
  - a corrupt raster is skipped, counted and kept null;
  - a missing raster gets its own code;
  - every raster of one model, every footprint, or the fraction above the threshold is systematic;
  - an interrupted extraction resumes to an identical frame, attributes and report, without rereading checkpointed months.
- **`tests/test_release.py`** (9), the final gate on the raster demo:
  - offline end-to-end run; every unit completed or not declared; a rerun reuses everything;
  - paired rows and exclusions inspectable;
  - MAE, RMSE and MBE reproduced from the paired rows for every model, site and comparison (over 300 rows);
  - a corrupt raster is skipped, counted in the manifest, retried in the log, and listed as `files_skipped` in the report;
  - a systematic failure gives exit 2, a failed manifest and no table;
  - an interrupted CLI extraction resumes to identical outputs;
  - the tables demo is unchanged; invalid YAML exits 2; the data dictionary is current; the limitations list the gaps.
- **`tests/test_notebooks.py`** (7): outputs stripped, no private paths, the Drive mount only as a commented cell, every code cell runs.
- **Full suite:** 867 passed, 3 skipped (16 min). ruff and mypy are clean, and Sphinx builds with `-W`.
- **Not run locally:** Application Control blocks DLLs freshly unpacked outside the project venv (pyarrow 14, matplotlib `_path`). So the lowest-direct stack on Python 3.11 and the pinned demo environment could not be run here. Both are CI jobs.

### Real-file verification (local, git-ignored; D-014)
The pre-change baselines were run from a worktree at the step-17 commit. The final code (0.1.0.dev0, before the version bump) was run with `--force`. Every Parquet table was compared: frames, dtypes and the full `etcompare` metadata, attributes included.

| Configuration | Units | Wall time | Extractions (s) | Tables compared | Differences |
|---|---|---|---|---|---|
| `examples/us_utj.yaml` (M: unclipped Drive, footprints on M:) | 82 completed, 1 skipped (`run_skip`) | 11 min 32 s | set B `etw` 83, set B `mean` 81, NLDAS 73, measured LE 70 | 197 | **0** |
| `examples/utah_stations.yaml` (J:) | 20 completed, 18 skipped (`run_skip`, `not_declared`) | 10 min 23 s | UTD 71, UTE 24, UTG 14, UTJ 25, UTM production 88, UTM NLDAS 86, UTV 35, UTW 140 | 58 | **0** |

- **Run health.** Neither run had a read failure: no `io_retry`, `io_failed`, `io_stall` or `system_suspended` event, and no unreadable or missing file in any exclusions file.
- **Keep-awake** was held in both runs. 64 and 220 monthly checkpoints were written and all were cleared afterwards.
- **US-UTJ** extracts again for the first time since D-054. Its extracted values equal the stored tables except `source_path` (D-079 benchmark).
- **Comparison series.** An intermediate comparison after the raster-path changes was also identical (US-UTJ 197 tables, Utah 58).

### Open
- **Publishing needs authorization:** PyPI, a Read the Docs project, pushing the branch (which runs CI).
- **Licence holder:** Paul Inkenbrandt (user, 2026-10-20).
- **Carried over:** every decision in `decisions:` of the example configs, and the gaps in `docs/limitations.md`.

## Step 17 — Plots, reports, configuration and CLI (2026-10-19)

**User decisions (2026-10-19; D-074 to D-077):** the assistant's recommendations were accepted:
- `argparse` with the console script `etcompare`;
- analyses run when declared, with `run.skip`;
- Markdown plus self-contained HTML, and PNG plus SVG figures;
- a per-unit cache with a `stat` raster identity (SHA-256 opt-in);
- versioned run manifests;
- local real-station reports;
- a minimal synthetic demo now;
- cumulative gaps as a break, a shaded span and a count;
- a `decisions:` section;
- verdicts carried verbatim.

**Run it**
```bash
python -m etcompare run examples/us_utj.yaml            # or: etcompare run ...
python -m etcompare report examples/us_utj.yaml         # report only (reuses everything)
python -c "from etcompare.demo import write_demo; write_demo('demo')"
python -m etcompare run demo/config.yaml                # synthetic, offline
uv run python -m pytest                                 # 834 passed, 3 skipped (opt-in Drive raster tests)
uv run ruff check src tests tools/run_*.py && uv run mypy   # clean
```

### API
- **`etcompare.cli`:** `validate`, `index`, `extract`, `analyze`, `report`, `run`, plus `--force`, `--log-format` and `--log-level`. Exit codes: 0, 2 (configuration or data error), 1.
- **`etcompare.runner.run_stages(cfg, stages, command=, force=, select=)`** returns `(RunManifest, records)`. `tool_main` serves the `tools/run_*.py` wrappers.
- **`etcompare.workflow.Workflow`:**
  - planning: `plan(stage)` returns `Unit`s;
  - running: `execute`, `status_of` (completed, stale, not_run, ...), `validate()` and `undeclared()`;
  - helpers: `landscape`, plus the text summaries moved from the tools.
- **`etcompare.runrecord`:** `RunManifest` (JSON, version 1, round trip), `UnitRecord`, `Cache`, `status_table`.
- **`etcompare.provenance`:** `file_identity` (`sha256` or `stat`), `canonical_hash`, `code_identity(exclude=)`, `git_state`, `detect_upstream`.
- **`etcompare.plots`** (`plot` extra): `scorecard`, `scatter_1to1`, `cumulative`, `contrast_intervals`, `conditional_errors`, `coverage`, `ranking_stability`, `ensemble_folds`, `ensemble_improvement`, `ensemble_disagreement`; `caption_meta`, `check_meta`, `save_figure`; `NothingToDraw` and `CaptionMismatch`.
- **`etcompare.reporting`:** `build_report`, `report_unit`, `check_language`, `table_status_rows`, `derived_decisions`.
- **`etcompare.demo.write_demo`.**
- **New schemas:** `analysis_status` v1 and `skipped_analyses` v1.
- **New configuration:**
  - sections `run`, `report`, `cache`, `upstream`, `decisions`, `footprint_stacks` and `footprint_comparisons`;
  - fields `raster_extractions[].strict` and `crop_height_tables[].on_missing`.

### Delivered
- **Tool logic moved into the library.** The eight `tools/run_*.py` scripts are thin wrappers over the workflow units and write the same files, now cached and with a manifest. The former flags are configuration fields (D-074).
- **Cache.** One stamp per unit. The key covers the code (without the presentation modules), the configuration entries read and the input identities (upstream outputs by content; rasters by size + mtime unless `sha256`). Invalidation is exact, and the workflow tests check this for station tables, configuration entries, masks, rasters, raster windows and modified outputs.
- **Run manifest** per invocation, versioned, never overwritten, plus `analysis_status` and `log.jsonl` (D-075).
- **Reports and figures from saved tables only** (D-076):
  - every guide figure, plus the ensemble figures;
  - captions read from the rows and checked against the declaration;
  - a `not_available` entry instead of an empty figure;
  - the skipped-analyses table, open decisions and guarded wording;
  - synthetic labels on every page and caption.
- **Example configs:**
  - `footprint_stacks`, plus `footprint_comparisons` for the two step-6 comparisons (D-035, D-038);
  - `run.skip: [footprint_aggregation]`, so the verified footprints are never rewritten;
  - declared upstream versions;
  - every open or provisional decision with its D-number.
- **Synthetic demo** (D-077).

### Checks
- **`tests/test_workflow.py`** (15): exact invalidation (station, configuration entry, mask, raster in and outside the window, SHA-256 identity, modified output); reuse; `--force`; `run.skip` and dependency skips; missing input gives `not_available`; undeclared analyses; a failed unit recorded then raised; the crop-height policy; validation outputs.
- **`tests/test_runrecord.py`** (13): manifest round trip, versioning and refusal of unknown keys; identities; canonical hash; code identity; git outside a checkout; stamps; the CLI help, exit code 2 on configuration errors, and the manifest and JSON log of `validate`.
- **`tests/test_reporting.py`** (18):
  - the synthetic end-to-end gate: every status recorded; a rerun reuses everything, the report included; seeds, exclusions and versions in the manifest; synthetic labels on every page and caption; no real station ID; report sample counts equal the tables'; the skipped-analyses table; open decisions;
  - figures: drawn rows equal input rows; captions; no cumulative line spans a gap; statuses in place of intervals; sparse bins marked; caption mismatches raise; folds labelled held out, exploratory or declined; disagreement is not an interval;
  - guarded words.
- **`tests/test_config_workflow.py`** (11): the new sections and their refusals, and the example configs.
- **`test_water_balance`:** the isolation test now lists the orchestration and presentation modules and checks that every selecting module still never names the water balance.
- **Regression:** the pre-step-17 runners and the workflow produce identical US-UTJ tables (177 Parquet files, frames and attributes).
- **Full suite:** 834 passed, 3 skipped (6 min 16 s). ruff (src, tests, tools/run_*.py) and mypy are clean.

### Real stations (local, git-ignored; D-078)
See D-078.
**US-UTJ** (`python -m etcompare run examples/us_utj.yaml`, 5 min 42 s; 77 units completed):
- **Index and all four raster extractions are `not_available`** (`input_missing`): `openet_rasters.root` is stale (D-054). The analyses read the existing extraction outputs by content.
- **Footprint aggregation is `skipped`** (`run_skip`), as declared.
- **Both footprint-set comparisons, now declared in `footprint_comparisons`, reproduce D-035 and D-038:**
  - set B `mean` − `etw`: −0.046 to −0.001 mm d⁻¹, maximum 0.48;
  - measured LE − NLDAS: +0.002 to +0.015, maximum 1.12.
- **Report:** 151 figures, HTML 12 MB.
  - The open decisions list the 11 declared entries plus the ones read from the tables: no block length adopted (7, 14 and 28 days), 20,034 contrast rows `not_assessed`, contrasts exploratory, `single_site` summaries, leave-one-site-out `not_available` and leave-one-year-out `declined`, five unknown water-balance terms.
  - Two figures are `not_available` with their reason: retained-fraction bins on the 3×3-window side (`empty_bin`), and no held-out ensemble contrast (nothing fitted).

**Seven Utah stations** (`python -m etcompare run examples/utah_stations.yaml`; this checkout had no Utah outputs):
- **Index:** J: was indexed in 18 s.
- **Extractions:** all eight completed. Seven took 17–138 s each. `utm_nldas_clip` took 32,382 s of wall time, but 32,305 s of it is a single pause between two consecutive progress records. **The machine was asleep** (established 2026-10-20 from the Windows System log): the last progress record (US-UTM day 950) is at 21:11:24.7 MDT; Kernel-Power event 42 "The system is entering sleep", reason *System Idle*, follows at 21:11:25.1; the system resumed at 06:09:40 (wake source: the USB host controller), and the next record came 10 s later. The AC idle-sleep timeout is 900 s and nothing kept the system awake during a run. J: is a USB SSD (Samsung T7, exFAT), so no spin-down is involved; no disk event was logged and no read failed (the exclusions hold only `footprint_missing` and `no_valid_cells`). Whether the USB drive was briefly disconnected during sleep cannot be established. Step 18 adds `run.keep_awake`.
- **The ensemble reproduces D-073:**
  - 1,839 eligible station-days (1,264 in April–October);
  - leave-one-site-out held out on 6 of 7 folds (1,798 days compared);
  - leave-one-year-out declined;
  - `tests/integration/test_utah_ensemble.py` (8) passes on these outputs.
- **Report:** ensemble section only (no comparisons are declared in that configuration).

### Open
- Step 18: concurrency, retries, staging, resumable extraction, the release artifacts and a full raster demo.
- Carried over: every decision listed in `decisions:` of the two example configs.

## Step 16, part 4 — US-UTE files swapped, US-UTM rebuilt (2026-10-19)

**User decisions (D-073):** swap the wrong-region US-UTE files; rebuild US-UTM, which the UGS parameter table lacks, using production's constant heights.

### Delivered
- **J: US-UTE swap:** 171 wrong-region files moved to `J:/FLUX/wrong_region_quarantine/US-UTE/` and their C: US-UTE counterparts copied in, checked by SHA-256. Two 2023 wrong-region files without a counterpart were quarantined. The log is `US-UTE_swap_log.csv` there.
- **US-UTM:** stack and NLDAS aggregation with zm 3.45 m and z0 0.121 m held constant (`US-UTM_nldas_eto_20261019`; extraction `utm_nldas_clip`). The production set stays declared.

### Checks
- `tests/integration/test_utah_ensemble.py` (8): 1,839 days; no US-UTE `no_overlap`; the US-UTM rebuild within 0.03 mm d⁻¹ of production.
- **Full suite:** 775 passed, 3 skipped. ruff and mypy are clean. The oldest stack (ensemble, both ensemble integration, discovery, extraction, import-gate, schema and config tests) gives 186 passed and 1 skipped: extraction needs the geo extra.

### Results (D-073)
- **1,839 eligible station-days** (US-UTE 218).
- **Leave-one-year-out:** declined.
- **Leave-one-site-out:** 6 of 7 folds fitted.
  - Fitted RMSE is below the mean at 5 of 6 stations (equal-station mean −0.166 mm d⁻¹; April–October −0.218) and below the official ensemble at 6 of 6 (−0.226).
  - **Supported at 14-day blocks:** US-UTE and US-UTV.
- **US-UTM:** the rebuild changes its extracted ET by at most 0.026 mm d⁻¹, and no result changes.

### Open
- US-UTM's heights are constant; a crop-height model for it would make it match the other stations.
- Carried over: block length (D-048), equivalence margins (D-049), a network interval, and the corn station (below the 180-day minimum).

## Step 16, part 3 — J: rasters, minimum held-out size, extraction fix (2026-10-19)

**User decisions (D-072):** use `J:/FLUX` (de-duplicated rasters), `min_test_days_per_fold: 180`, keep the default ffp_xr height filter for US-UTG, and rebuild US-UTM (blocked: the UGS database timed out twice).

### Delivered
- **`min_test_days_per_fold`** (mandatory with designs): a held-out fold with fewer eligible days is declined, with the count as the reason.
- **Fix in `etcompare.extraction`:** a raster on another grid than the previous one was declared `no_overlap` from the cached grid without its header being read. It hid 1,740 US-UTE model-days after a wrong-region file. No earlier run was affected (zero `no_overlap` rows in every earlier exclusion file).
- **`examples/utah_stations.yaml`** points the rasters and station tables at J:. J:'s database and footprints are byte-identical to C:.

### Checks
- `tests/test_extraction.py` (+1 regression test).
- `tests/test_ensemble.py` (+2: the declined fold and its reason; the mandatory field).
- `tests/integration/test_utah_ensemble.py` (7): the new counts and statuses, and the wrong-region `no_overlap` rows (geesebal and ptjpl only).
- J: extractions equal the C: ones at six stations (zero difference).
- **Full suite:** 774 passed, 3 skipped. ruff and mypy are clean. Oldest stack: 384 passed.

### Results (D-072)
- **1,801 eligible station-days.** Leave-one-year-out is declined.
- **Leave-one-site-out:** 6 of 7 folds fitted; US-UTG (41 days) is declined, and US-UTE (180) now trains and is tested. SIMS is weighted 0.55–0.71.
- **Held-out RMSE** is lower than the mean at 5 of 6 stations (equal-station mean −0.168 mm d⁻¹; April–October −0.224) and lower than the official ensemble at 6 of 6.
- **The corn station** (worse in D-071) is no longer evaluated, so these figures say nothing about corn.
- **Supported at 14-day blocks:** US-UTV only.

### Open
- **The 171 wrong-region US-UTE files on J:** keep the US-UTE file instead? That would restore at most 39 in-record model-days.
- **US-UTM's rebuild,** when the database is reachable.
- Carried over: block length (D-048), equivalence margins (D-049), and a network interval.

## Step 16, part 2 — Seven-station ensemble (2026-10-19)

**Run it**
```bash
# footprint parameters (read-only DB query, as for US-UTJ) and per-timestep stacks
../footprints/.venv/Scripts/python.exe tools/upstream/fetch_footprint_parameters.py US-UTD outputs/footprint_inputs
../footprints/.venv/Scripts/python.exe tools/upstream/run_timestep_footprints.py --site US-UTD --lat 38.1038 \
    --lon -109.5952 --metflux .../US-UTD_metflux_qc.parquet --params outputs/footprint_inputs/US-UTD_daily_footprint_parameters.parquet \
    --nldas .../US-UTD_nldas.csv --out outputs/footprint_timesteps/US-UTD --start 2023-05-24 --end "2025-10-22 23:59"
uv run python tools/run_footprint_aggregation.py examples/utah_stations.yaml US-UTD outputs/footprint_timesteps/US-UTD
uv run python tools/run_raster_extraction.py examples/utah_stations.yaml utd_nldas_clip     # one per station
uv run python tools/run_ensembles.py examples/utah_stations.yaml                            # ~1 min
uv run python -m pytest                           # 770 passed, 3 skipped (opt-in Drive raster tests)
# ruff (same file list plus tools/run_ensembles.py) and mypy clean; oldest stack (UV_LINK_MODE=copy,
# --resolution lowest-direct, Python 3.11) on the ensemble, discovery, filename, ranking, bootstrap,
# metrics, cohort, aggregation, import-gate, schema and config tests plus both ensemble
# integration tests: 381 passed
```

### Delivered (D-069)
- **[examples/utah_stations.yaml](examples/utah_stations.yaml)** declares seven stations (US-UTD, UTE, UTG, UTJ, UTM, UTV and UTW) with the user's choices of 2026-10-19:
  - footprints rebuilt as NLDAS-ETo weighted from per-timestep stacks (US-UTM uses its production top-level set);
  - the clipped rasters for every station;
  - byte-identical duplicates collapsed;
  - UTC−7, standard time, interval start and mm/day;
  - calendar-year folds, with April–October reported separately.
- **Library additions:**
  - declared raster processing tags (`buffer400m_clip`);
  - `on_conflict: collapse_identical` (identical copies collapse; differing copies are held out);
  - `ensembles[].site_footprint_sets` (per-station footprint sets under one side label, provenance kept);
  - `ensembles[].subsets` (month subsets scored apart, training unchanged; `subset` key in `ensemble_summary` and `ensemble_improvement`).
- **Tools:**
  - `run_timestep_footprints.py --veg-type` (cross-check only);
  - `run_footprint_aggregation.py` picks target sets by station prefix;
  - `run_ensembles.py` reports several stations and subsets.

### Checks
- **[tests/test_discovery.py](tests/test_discovery.py)** (+2): declared and undeclared tags; collapse of identical copies with logging, and differing copies held out.
- **[tests/test_ensemble.py](tests/test_ensemble.py)** (+10):
  - month subsets: identical training, months 4–10 only, metrics equal to `compute_metrics`;
  - 5 subset refusals;
  - the per-station relabelling and its provenance; another set's rows never enter;
  - target-dependent mapped sets refused; the mapping must cover the sites; config label rules.
- **[tests/integration/test_utah_ensemble.py](tests/integration/test_utah_ensemble.py)** (6; skips without the files):
  - the independent eligible-day count (1,798) and member means;
  - leave-one-site-out held out, and US-UTE and US-UTG never trained;
  - leave-one-year-out with no supported claim;
  - fitted predictions equal to member values times the weights;
  - April–October days only;
  - the clipped US-UTJ extraction equals the unclipped one (≤ 1.3e-10).

### Results (D-070, D-071; local outputs under `outputs/utah/`, git-ignored)
- **Footprint days:** US-UTD 831, UTE 457, UTV 726, UTW 1,543, and **UTG only 41**. Production also found mostly no valid footprint at the corn site; the cause is unresolved.
- **Eligible station-days:** 1,798 in all (1,225 in April–October). US-UTE loses 41 days to the 186 held-out conflicting geesebal/ptjpl pairs.
- **Leave-one-year-out:** no held-out claim. 2023 and 2025 are declined. 2024 is fitted but has 5 held-out days, so it is `not_assessed`.
- **Leave-one-site-out:** all 7 station folds fitted. Weights favour SIMS (0.42–0.59) and SSEBop (0.22–0.30).
  - Held-out RMSE is lower than the member mean at 6 of 7 stations (equal-station mean −0.058 mm d⁻¹; April–October −0.103), and lower than the official ensemble at 6 of 7 (−0.110).
  - The corn station US-UTG is worse (+0.53 on 41 days).
  - RMSE improvements are `supported` at 14-day blocks only at US-UTJ and US-UTV.
  - These are exploratory, station-level results with no network interval.

### Open (user decisions)
- A selection for the 186 differing US-UTE duplicate pairs (they are held out now).
- Whether leave-one-year-out needs a minimum held-out size: the 2024 fold has 5 days.
- The US-UTG footprint problem: its corn heights and why most half-hours give no footprint.
- Whether US-UTM should also be rebuilt from its stack, for one footprint method at every station.
- Carried over: block length and support (D-048), equivalence margins (D-049), and a hierarchical network interval (not implemented).

## Step 16 — Held-out ensemble experiments (2026-10-18)

**Run it**
```bash
uv run python -m pytest                           # 752 passed, 3 skipped (opt-in Drive raster tests)
uv run ruff check src tests tools/run_cohorts.py tools/run_representativeness.py tools/run_events.py \
    tools/run_water_balance.py tools/run_ensembles.py && uv run mypy     # clean
uv run python tools/run_ensembles.py examples/us_utj.yaml   # ~4 s; outputs/ensembles/
UV_LINK_MODE=copy uv run --isolated --resolution lowest-direct --python 3.11 --with pytest \
    python -W error::FutureWarning -m pytest tests/test_ensemble.py \
    tests/integration/test_us_utj_ensemble.py tests/test_ranking.py tests/test_bootstrap.py \
    tests/test_metrics.py tests/test_cohorts.py tests/test_aggregation.py tests/test_anomalies.py \
    tests/test_import_gate.py tests/test_schemas.py tests/test_config.py tests/test_imputation.py \
    tests/test_water_balance.py tests/test_scenarios.py
    # oldest stack (Python 3.11.16, numpy 1.26.0, pandas 2.2.0, pyarrow 14.0.0, scipy 1.11.1): 448 passed
```

### API
- **`etcompare.ensemble.run_ensemble(spec, observations, extractions, *, products, metric_spec, bootstrap_spec=None, footprint_sets=None)`** returns an `EnsembleResult` with:
  - `days`, `predictions`, `folds`, `weights`, `inner_scores`, `training_membership`, `summary` and `improvement` (all v1), plus `tables()` and `report`;
  - `comparisons`: per scope, an `EnsembleComparison` holding the frozen product `cohort`, `metrics` (step 9), `bootstrap` (step 10) and `ranking` (step 11 contrasts).
- **Building blocks** (public, tested): `simplex_least_squares` (exact, by member support), `shrink`, `combine`, `fold_groups` and `fold_period`.
- **Config:** `EnsembleSpec` (`ensembles:`) and the accessor `Config.ensemble()`. New constants: `ENSEMBLE_DESIGNS`, `MISSING_MEMBER_POLICIES`, `ENSEMBLE_COMBINATIONS`, `ENSEMBLE_METHODS`, `ENSEMBLE_REGULARISATIONS`, `ENSEMBLE_SELECTION_METRICS`, `ENSEMBLE_PREPROCESSING`, `INSUFFICIENT_DATA_POLICIES` and `MAX_ENSEMBLE_MEMBERS`.
- **[tools/run_ensembles.py](tools/run_ensembles.py)** (new) writes:
  - `outputs/ensembles/<id>_{days,predictions,folds,weights,inner_scores,training_membership,summary,improvement}.{parquet,csv}`;
  - `<id>__<scope>_{paired,metrics,intervals,contrasts}.*` for each compared scope;
  - `<id>_report.txt` (inputs with SHA-256, declaration, status, folds, metrics, contrasts).

### Delivered
The rules are in the module docstring and D-065:
- Three products on the same frozen days: the official ensemble, the mean and median of the declared members, and an optional fitted ensemble. All are scored, resampled and contrasted through `build_cohort`, `score_comparison`, `bootstrap_comparison` and `rank_comparison`.
- **Post-extraction, never pixel-level**, labelled on every row. The official ensemble is never a member. Target-dependent footprints (measured LE) are refused.
- The missing-member policy is declared and reported per day and per fold. Member spread is labelled disagreement.
- **Fitting:**
  - leave-one-year-out (calendar or water year, shared across stations) and leave-one-site-out;
  - training rows from other folds only, with the gap-filled rule, the time buffer and the training-fold size rule;
  - simplex weights shrunk toward equal weights, with α chosen by inner grouped folds on training data;
  - per-fold membership, weights, α, inner scores, preprocessing and member availability are saved.
- **Statuses:** `held_out`, `declined`, `exploratory` (in-sample only) and `not_available`. A held-out improvement is `supported` only by an interval in the fitted ensemble's favour.

### Checks
- **[tests/test_ensemble.py](tests/test_ensemble.py)** (61 tests):
  - **Gates:**
    - a member good only in the training years gets no false held-out improvement, with a positive control;
    - a perturbed held-out fold leaves its own weights, α, inner scores and membership unchanged;
    - the official ensemble is refused as a member;
    - the equal-weight limit reproduces the mean exactly, with zero contrasts that are not supported;
    - both missing-member policies are applied and reported;
    - too few folds give `declined` or `exploratory`, never fitted weights;
    - the same seed reproduces the intervals.
  - Also: the solver (an exact mixture; optimality against 5,000 simplex points and the vertices), the buffer dates, the fold-size rule, gap-filled exclusion, leave-one-site-out on three synthetic stations, frozen product days, disagreement values, labels, target-dependent footprints, the signature and imports, round trips, and 33 config refusals.
- **[tests/integration/test_us_utj_ensemble.py](tests/integration/test_us_utj_ensemble.py)** (5 tests; skips without the local files):
  - the declined and not-available statuses;
  - the 188 days, the mean, median, MAE and RMSE recomputed with pandas from the raw files;
  - the 115 ineligible 2024 days;
  - the water-year and two-member alternatives, also declined;
  - refusals of the official ensemble and the measured-LE footprints.
- `test_schemas` round-trips the eight new schemas.

### US-UTJ result (D-066; local outputs, git-ignored)
- **The fitted ensemble is declined.** With calendar-year folds the six members coexist only in 2025 (188 measured-reference days, 2025-01-01 to 2025-10-08), so no fold has a training year. Water-year folds (WY2025 180 days, WY2026 8) and an eemetric + ssebop subset (2024 and 2025) would be declined too. Leave-one-site-out is `not_available` (one station). No weights exist and no held-out improvement is reported.
- **Baseline** (nothing fitted; an evaluation on 188 frozen days, `et_corrected_measured`):

  | Product | MAE | RMSE | MBE (mm d⁻¹) |
  |---|---|---|---|
  | official | 0.699 | 1.140 | −0.314 |
  | mean | 0.683 | 1.082 | −0.255 |
  | median | 0.725 | 1.107 | −0.201 |

  No contrast excludes zero at 7 or 14 days, and 28-day blocks lack support. The intervals are probably too narrow (D-048).
- Member disagreement ranges 0.19–7.83 mm d⁻¹ (median 1.87); this is disagreement, not an interval.
- Station level only: n_sites = 1, not network evidence.

### Other stations surveyed (2026-10-18, D-068)
- The user gave the paths, and calendar years were confirmed as the project standard (the April–October growing season; D-066). The directories were read only.
- Daily exports exist for 11 stations, footprints for 8 and clipped rasters for 9. There are no pre-extracted tables, so a step-5 extraction is needed.
- **Pre-extraction estimate:**
  - Leave-one-year-out stays declined: six-member days exist only in 2023 and 2025.
  - Leave-one-site-out becomes possible in 2025: six stations have ≥ 180 six-member measured days.
- Blockers before extraction: the footprint set per station, the US-UTE conflicting duplicates (186 pairs), collapsing byte-identical duplicates, station day definitions, and US-UTB / US-CdM / US-UTL coverage.

### Open (user decisions; see D-067, D-068)
- The choices listed at the end of D-068 (they come before any extraction of the other stations).
- Whether to keep the declined designs declared, or to declare another design before any fitting.
- Carried over: block length and support (D-048), equivalence margins (D-049).

**Next:** step 17 (done 2026-10-19, above).

## Step 15 — Water-balance checks and scenario uncertainty (2026-10-17)

**Run it**
```bash
uv run python -m pytest                           # 678 passed, 3 skipped (opt-in Drive raster tests)
uv run ruff check src tests tools/run_cohorts.py tools/run_representativeness.py tools/run_events.py \
    tools/run_water_balance.py && uv run mypy     # clean
uv run python tools/run_water_balance.py examples/us_utj.yaml   # ~1 s; outputs/water_balance/
UV_LINK_MODE=copy uv run --isolated --resolution lowest-direct --python 3.11 --with pytest \
    python -W error::FutureWarning -m pytest tests/test_water_balance.py tests/test_scenarios.py \
    tests/integration/test_us_utj_water_balance.py tests/test_events.py tests/test_event_analysis.py \
    tests/test_anomalies.py tests/test_import_gate.py tests/test_schemas.py tests/test_config.py \
    tests/test_imputation.py tests/test_aggregation.py tests/test_cohorts.py tests/test_metrics.py \
    tests/test_bootstrap.py
    # oldest stack: 410 passed, 1 skipped (the polygon-area check needs geopandas)
```

### API
- **`etcompare.water_balance.compute_water_balance(spec, station_daily, *, period_aggregation, station)`** returns a `WaterBalanceResult` with `terms` (`water_balance_terms` v1), `residuals` (`water_balance_residuals` v1), `report` and `tables()`. It has no cohort or model parameter.
  - Building blocks: `prepare_water_balance` / `evaluate_water_balance`, `term_series`, `term_total` and `depth_to_volume_m3`.
  - Data classes: `TermSeries`, `BalancePeriod`, `PreparedWaterBalance` and `TermTotal`.
- **`etcompare.scenarios.run_scenario(scenario, water_balance, station_daily, *, period_aggregation, station)`** returns a `ScenarioResult` with `results` (`scenario_results` v1), `draws()` (measurement only) and `report`. Also public: `draw_perturbations`, `marginal`, `scenario_sha256` and `UNCERTAINTY_LABELS`.
- **Config:**
  - `WaterBalanceSpec` (`water_balances:`) with `WaterBalanceBoundarySpec` and `WaterBalanceTermSpec`;
  - `ScenarioSpec` (`scenarios:`) with `PerturbationSpec` and `ScenarioAlternativeSpec`;
  - accessors `Config.water_balance()` and `scenario()`;
  - constants `WATER_BALANCE_TERMS`, `WATER_BALANCE_SIGNS`, `WATER_BALANCE_EQUATIONS`, `SIGN_CONVENTIONS`, `TERM_STATUSES`, `TERM_SOURCES`, `TERM_QUANTITIES`, `SPATIAL_SUPPORTS`, `BOUNDARY_KINDS`, `UNCERTAINTY_CATEGORIES`, `SCENARIO_CATEGORIES`, `DISTRIBUTION_FAMILIES`, `PERTURBATION_MODES`, `TEMPORAL_DEPENDENCES`, `TERM_DEPENDENCES` and `STATE_TIMES`.
- **[tools/run_water_balance.py](tools/run_water_balance.py)** (new) writes `outputs/water_balance/<id>_{terms,residuals}.{parquet,csv}`, `<id>__<scenario>_scenarios.*` for declared scenarios, and `<id>_report.txt` (station SHA-256, declaration, validation, totals, gap-filled counts).

### Delivered
The rules are in the module docstrings and D-062 / D-063:
- **Every term is declared.** Unknown terms are never zero: they give a null residual whose reason lists every unknown term.
- Residuals from measured/modeled terms and scenario residuals are separate rows.
- Supports other than the boundary are flagged `support_mismatch`, never equated. A footprint boundary and regional irrigation are refused.
- The periods are the step-8 calendar. A flux needs every day, and a storage change needs both states; otherwise only a labelled partial sum is reported.
- ET gap-filled days are counted and flagged (D-005), and unknown day definitions are flagged.
- Volumes need a known area and a matching support.
- Scenarios have one of three categories (`sampling` is the bootstrap and is refused), each labelled, with seed, draws and declaration. Footprint uncertainty comes only from supplied realizations.

### Checks
- **[tests/test_water_balance.py](tests/test_water_balance.py)** (47 tests). Expected values are analytic, from binary-exact daily depths.
  - **Gates:** a closed balance is exactly 0 mm / 0 m³; an unknown inflow stays unknown with every unknown term listed; a support mismatch is flagged and gives no volume; volumes are refused without an area.
  - Also: incomplete months (partial sum 30 × 0.75 mm, total null), storage states, gap-filled ET (5 days; the measured variant then 24/29 and incomplete), day alignment, scenario residuals, 366-day year and water-year denominators, Parquet, isolation (signature; only `scenarios` imports the module) and 28 declaration refusals.
- **[tests/test_scenarios.py](tests/test_scenarios.py)** (35 tests).
  - **Gates:** the same seed reproduces (another differs); results are labelled apart from `intervals` and `sampling` is refused.
  - Analytic draws: `P_total·(x − 1)`, February = January × 29/31, `−Σ x_d`, the storage offset, copula ρ = 0.8 ± 0.03.
  - Unknown bases stay unknown, method-choice ranges, footprint realizations and their refusals, and 20 declaration refusals plus copula checks.
- **[tests/integration/test_us_utj_water_balance.py](tests/integration/test_us_utj_water_balance.py)** (6 tests; skips without the local files):
  - every residual is unknown with the five terms listed;
  - supports are flagged and no volume is given;
  - `p_1_1_1` is unused;
  - July 2025 and WY2025 totals and gap-filled counts equal pandas sums of the CSV;
  - partial periods are never totals;
  - the declared area equals the geopandas polygon area.

### US-UTJ result (D-064; local outputs, git-ignored)
- **The water balance is unknown.** Irrigation, capillary rise, runoff, deep percolation and storage change have no input, so all 24 residuals are null with that reason. No scenario is declared, and no volume is given (both known terms mismatch the field's support).
- **Complete term totals:**
  - every month from 2024-07 to 2025-09, and WY2025: ET (corrected-inclusive) 888.9 mm, 127 of 365 days gap-filled; gridMET precipitation 131.9 mm;
  - May 2025 is 31 of 31 days gap-filled.

  These are term totals only. Their difference is not a residual, and nothing is assigned to ET error.
- **Not used:** `p_1_1_1` (D-059), the half-hourly logger files, and any download, service or credential.

### Open (user decisions; see the questions in D-064)
- Irrigation depths, timing or diversion records for the field.
- A soil-moisture series: which sensor, depth and units, and whether the logger files may be read.
- Whether and how `p_1_1_1` may be used.
- The ET reference variant of the balance (`et_corrected_inclusive` is Provisional).
- The gridMET precipitation units and day definition.
- The boundary area and polygon vintage.
- Any runoff, deep-percolation or capillary-rise input or scenario.
- Any measurement, method-choice or footprint scenario.

**Next (at the time):** step 16. Superseded by the Step 16 entry above.

## Step 14, part 2 — Event responses and anomalies (2026-10-17)

**Run it**
```bash
uv run python -m pytest                           # 587 passed, 3 skipped (opt-in Drive raster tests)
uv run ruff check src tests tools/run_cohorts.py tools/run_representativeness.py tools/run_events.py && uv run mypy   # clean
uv run python tools/run_events.py examples/us_utj.yaml   # ~4 s; outputs/events/
UV_LINK_MODE=copy uv run --isolated --resolution lowest-direct --python 3.11 --with pytest \
    python -W error::FutureWarning -m pytest tests/test_events.py tests/test_event_analysis.py \
    tests/test_anomalies.py tests/test_import_gate.py tests/test_schemas.py tests/test_config.py \
    tests/test_imputation.py tests/test_aggregation.py tests/test_cohorts.py tests/test_metrics.py
    # oldest stack: 274 passed
```

### API
- **`etcompare.events.analyse_events(cohort, events, spec, *, station_daily=None)`** returns an `EventAnalysisResult` with `responses` (`event_responses` v1), `overlaps` (`event_overlaps` v1), `conditions` (`event_conditions` v1), `report` and `tables()`. `recovery(values, end, baseline, spec, next_management)` is public and tested.
- **`etcompare.anomalies.compute_anomalies(cohort, spec, metric_spec)`** returns an `AnomalyResult` with `daily` (`anomaly_daily` v1), `metrics` (`anomaly_metrics` v1) and `report`. Building blocks: `day_of_year_365`, `fold_labels`, `climatology` and `moving_mean`.
- **Config:**
  - `EventAnalysisSpec` (`event_analyses:`) with `WindowVariableSpec`;
  - `AnomalySpec` (`anomalies:`);
  - `ManagementRecordSpec` (`event_tables[].management_records`);
  - accessors `Config.event_analysis()` and `anomaly_spec()`;
  - constants `APPROXIMATE_POLICIES`, `WINDOW_AGGREGATIONS`, `ANOMALY_METHODS`, `FOLD_UNITS` and `LEAP_DAY_RULES`.
- **`station_events`** v1 gains `management_record` and `management_record_source`.
- **[tools/run_events.py](tools/run_events.py)** reads the event tables, builds only the cohorts these analyses list, and writes `outputs/events/`.

### Delivered
The rules are in the module docstrings and D-061:
- Events come only from the adapter's table; anything else is refused.
- Calendar windows hold cohort days only, with explicit completeness. A cumulative response needs every post day, and recovery is identifiable only without gaps.
- Overlapping events of every category, weather conditions and gap-filled days are kept visible.
- Every row is labelled an uncontrolled before/after design and not causal evidence.
- Climatologies are held out by fold, with explicit leap-day and sparse-season rules. Exploratory detrending is in sample and labelled so.
- Raw metrics are reported on the same days next to the anomaly metrics.

### Checks
- **[tests/test_event_analysis.py](tests/test_event_analysis.py)** (21 tests):
  - **Gate:** a synthetic irrigation cutoff gives exactly −3 mm/day, −60 % and −21 mm;
  - incomplete baselines and post windows;
  - overlaps of management, instrument and confounder events, with their windows (instrument and confounder events are never analysed);
  - recovery day and every recovery status;
  - gap-filled flags, weather conditions and normalisation, and a missing weather column (skipped alone);
  - the approximate policy, and shared days in frozen cohorts;
  - forged or foreign event tables are refused, plus 11 declaration refusals.
- **[tests/test_anomalies.py](tests/test_anomalies.py)** (18 tests):
  - leap-day and water-year folds; the circular window and sparse rule;
  - **Gate:** a perturbed held-out year leaves its own climatology unchanged;
  - a constant bias vanishes from anomalies while raw MBE keeps it;
  - metrics equal `compute_metrics`;
  - two years give `insufficient_training_years`;
  - the exploratory moving mean is analytic (+3 departure → 3 − 3/31) and keeps gaps;
  - sparse samples and gap-filled flags; declaration refusals.
- **[tests/test_events.py](tests/test_events.py)** (+9): management-record confirmations, and the `event_analyses` / `anomalies` config sections.
- **Independent real-data check:** the 2025-07-05 and 2025-09-07 responses (means, change, ET/ETr change, cumulative response, recovery day) were recomputed from `US-UTJ_daily.csv` by hand and match exactly.

### US-UTJ results (D-061; local outputs, git-ignored)
- After every cutting with measured reference days, the station drops 1.8–3.8 mm/day in the following week. Every model drops less (modeled − observed change +0.15 to +2.70 mm/day), and several recover 16–18 days sooner after 2025-07-05.
- **2025-05-28 cannot be assessed:** all 14 window days have gap-filled reference values (`gridMET_ETr × ETrF_filtered`), so the observed curve is the gap-fill, not the cut.
- 2024-06-11 has no complete window. The 2024 cuttings are covered only by the eemetric/ssebop pair and the coverage cohorts.
- **No held-out anomaly skill exists:** the record holds two or three partial water years. The exploratory anomalies are in sample only.
- Uncontrolled before/after, one station: descriptive, not causal, and not a verdict on any model.

### Open (user decisions; all current values Provisional)
- The event-window, completeness and recovery defaults (7/1–7/0.7/0.9/3/28), and `baseline_min_mm` 0.5.
- Whether cutting windows that are mostly gap-filled should be excluded rather than flagged.
- The anomaly defaults (water-year folds, ±15 days, ≥ 20 values from ≥ 2 years; 31-day exploratory window).
- A control site or period, if a BACI design is wanted.
- Whether `station_log` is the right class for the `eddy`/`met`/`both` sources.

## Step 14, part 1 — Station event tables (2026-10-17)

**Run it**
```bash
uv run python -m pytest                           # 530 passed, 3 skipped (+4 local US-UTJ event checks)
uv run ruff check src tests tools/run_cohorts.py tools/run_representativeness.py && uv run mypy   # clean
UV_LINK_MODE=copy uv run --isolated --resolution lowest-direct --python 3.11 --with pytest \
    python -W error::FutureWarning -m pytest tests/test_events.py tests/test_import_gate.py \
    tests/test_schemas.py tests/test_config.py tests/test_imputation.py   # oldest stack: 136 passed
```

### API
- **`etcompare.adapters.event_log.adapt_event_table(source, spec, *, stations)`** returns an `AdapterResult` with a `station_events` table (v1, key `site_id` + `event_id`), a validation report, and exclusions (stage `event_adapter`). `approximate_flags(notes, patterns)` applies the declared text rule.
- **Config:** `EventTableSpec` (the `event_tables:` section) and `EventTypeSpec`; `Config.event_table(name)`. New constants: `EVENT_CATEGORIES`, `MANAGEMENT_KINDS`, `EVIDENCE_CLASSES`, `EVENT_DAY_RULES`, `EVENT_COLUMNS` and `EVENT_OPTIONAL_COLUMNS`. Schema constant `EVENT_ROLES`.

### Delivered
- Events come only from the log. The adapter takes no model, residual or cohort input (tested via its signature).
- Refusals, exclusions, the day rule, the evidence classes and the approximate rule are as in D-060. Every event keeps `observer`, `event_source`, `event_notes` and all `src_` columns.
- `examples/us_utj.yaml` declares `utj_station_events`.

### Checks
- **[tests/test_events.py](tests/test_events.py)** (46 tests, synthetic):
  - the logged context is kept;
  - only management events get `management_event`, and cuttings are never irrigation;
  - the station day is the logged calendar date (23:59 and 00:01 are not moved);
  - unmapped stations and out-of-record events are excluded with reasons, and edge-crossing events carry a warning;
  - the approximate rule: "date approximate" and "approximate date" match; "depth approximate", "time estimated" and "dated approximately" do not;
  - every refusal (duplicate IDs across stations, null ID, undeclared type, unmapped source, bad or blank timestamp, missing column or file, tz-aware stamps, timezone mismatch) and 17 declaration refusals;
  - CSV/DataFrame equivalence and a Parquet round trip.
- **[tests/integration/test_us_utj_events.py](tests/integration/test_us_utj_events.py)** (local log): the sha256; 17 events; the six cutting dates with their evidence classes; only 2024-09-08 approximate; the IRGASON move as an overlap; the exclusion counts; and the cuttings equal the crop-height resets (one-day drops to the 5 cm base).

### US-UTJ (D-060)
- 6 cuttings: 2024-06-11, 2024-07-19 and 2025-07-05 come from Planet imagery (remote-sensing inference), and 2024-09-08, 2025-05-28 and 2025-09-07 from the camera (photo observation). 2024-09-08 is approximate.
- 11 instrument overlaps inside the record. 17 US-UTJ entries outside the record and 479 rows of other stations are excluded with reasons.
- No irrigation events.

### Open (user decisions)
- ~~Which cuttings the farm records confirm.~~ All six (user, 2026-10-17; part 2).
- ~~The event-analysis settings and the anomaly method.~~ Provisional defaults in part 2.
- Whether `station_log` is the right class for the `eddy`/`met`/`both` sources.

## Step 13 — Landscape representativeness and satellite support (2026-10-17)

**Run it**
```bash
uv sync --extra geo && uv pip install -e ../footprints   # fluxfootprints: optional, not an extra (D-056)
uv run python -m pytest                           # 483 passed, 3 skipped (opt-in Drive raster tests)
uv run ruff check src tests tools/run_cohorts.py tools/run_representativeness.py && uv run mypy   # clean
uv run python tools/run_representativeness.py examples/us_utj.yaml   # landscape (~70 s; reads M: once)
uv run python tools/run_cohorts.py examples/us_utj.yaml              # + satellite support (~4 min)
UV_LINK_MODE=copy uv run --isolated --resolution lowest-direct --python 3.11 --with pytest \
    python -W error::FutureWarning -m pytest tests/test_representativeness.py tests/test_satellite.py \
    tests/test_spatial.py tests/test_import_gate.py tests/test_diagnostics.py tests/test_ranking.py \
    tests/test_bootstrap.py tests/test_metrics.py tests/test_aggregation.py tests/test_cohorts.py \
    tests/test_schemas.py tests/test_config.py tests/test_imputation.py
    # oldest stack (numpy 1.26.0, pandas 2.2.0, pyarrow 14.0.0, scipy 1.11.1, no geo):
    # 387 passed, 7 skipped (shapely, pyproj and fluxfootprints tests)
```

### API
- **`etcompare.representativeness.assess_landscape(spec, *, footprint_days, layers, fixed_supports=(), footprint_unusable=None, station_land_cover=None, label_source=None, unavailable=())`** returns an immutable `RepresentativenessResult` with:
  - `class_fractions` (`landscape_class_fractions` v1) and `index_summaries` (`landscape_index_summaries` v1);
  - `differences` (`landscape_method_differences` v1);
  - `labels` (`representativeness_labels` v1) and `availability` (`representativeness_availability` v1);
  - `report` and `tables()`.
- **Building blocks** (public, tested): `contour_mask` (footprint only), `class_composition`, `index_summary`, `match_asset`, `pixel_window_support`, `nearest_pixel_support`, `polygon_support`, `polygon_cell_fractions`, `read_layer_asset` (geo) and `read_legend_csv`. Data classes: `LayerAsset`, `FootprintDay`, `FixedSupport` and `LabelSource`.
- **`etcompare.satellite`:** `support_status(sources, tables)`, `acquisition_gaps(days, clear_days)` and `assess_satellite_support(cohort, spec, metric_spec, *, support)` → `SatelliteSupportResult` (`satellite_support_errors` v1 + availability).
- **`etcompare.adapters.fluxfootprints_labels`:** `fluxfootprints_status()` (version, commit, location), `categorical_labels`, `to_xy` and `upstream_parameters`. All imports are deferred.
- **Config:**
  - `RepresentativenessSpec` (`representativeness:` section) with `LandscapeLayerSpec`, `LayerAssetSpec`, `MatchingPolicySpec` and `PolygonLayerSpec`.
  - `SatelliteSupportSpec` (`satellite_support:` section) with `SupportSourceSpec`.
  - New constants: `LAYER_KINDS`, `MATCHING_POLICIES`, `LANDSCAPE_METHODS`, `SUPPORT_KINDS` and `ACQUISITION_GAP_VARIABLES`. New accessors `Config.representativeness_spec()` and `satellite_support_spec()`.
- **`prepare_footprint`** gains the status `invalid_values` (a normalized weight above 1, or a total that is not finite; D-054).

### Delivered
- **[src/etcompare/representativeness.py](src/etcompare/representativeness.py)**; the rules are in the module docstring and D-055:
  - **Conservative transfer.** Every support's weights move onto the layer's native grid by exact area overlap (D-028). Classes and index values never move, so no categorical map is interpolated and no 30 m class is shown as 10 m information.
  - **Separate denominators:** valid / grid total, nodata / grid total, outside / grid total, and class / valid (summing to 1). There are explicit nodata rows, and nothing is multiplied with domain capture.
  - **Weighted index:** mean, population weighted SD (the formula is on every row) and Kish `n_eff`.
  - **Contours** come from the footprint alone, as separate `contour_<level>` rows next to `full_domain`.
  - **Matching:** `nearest`, `previous_only`, `within_n_days`. Every row records the days from the layer, the relation, both years, the year mismatch and `retrospective`. Days without an admissible asset get a per-day `not_available` row and are never filled.
  - **Fixed supports** (3×3 window and nearest pixel on the model grid, polygons) on the footprint's frozen days with the same asset, and footprint − support differences. `ET_mean` is never a fixed support (D-007).
  - **Labels:** the station IGBP label and the fluxfootprints labels sit in their own table, kept apart from every number.
- **[src/etcompare/satellite.py](src/etcompare/satellite.py)** (D-057):
  - Authoritative flags or counts only. Missing rows are `unknown`.
  - Clear and interpolated subsets are identical across models; mixed and unknown days are counted. Metrics come via `compute_metrics`.
  - Timing bins come from config; the variables that use later acquisitions are labelled retrospective. Without sources: `not_available`.
- **[src/etcompare/adapters/fluxfootprints_labels.py](src/etcompare/adapters/fluxfootprints_labels.py)** (D-056): calls `categorical_representativeness` on the native map grid and records the version, commit and thresholds.
- **Schemas:** the six tables above, all v1. Keys include analysis, site, date, day set, extraction method, footprint set, layer, matching policy, domain and class (labels also source and radius; satellite errors also cohort, series, variable, subset and metric), so none can collide.
- **[tools/run_representativeness.py](tools/run_representativeness.py)** (new) writes `outputs/representativeness/<id>_{class_fractions,index_summaries,differences,labels,availability}.{parquet,csv}` and `<id>_report.txt` (inputs with SHA-256, settings, summary).
- **[tools/run_cohorts.py](tools/run_cohorts.py)** also writes `<id>__<sat>_satellite_{errors,availability}.{parquet,csv}` for frozen cohorts.
- **Example config:**
  - `utj_landscape` on Annual NLCD 2023–2025, CDL 2024 and the tower field. Legend file: `examples/legends/cdl_2024_legend.csv`.
  - `utj_satellite_support` with no sources.
  - All settings are **Provisional** (D-055, D-057).
- **Docs:** D-054 to D-058; contract §1, §8 and §9 rows.

### Gate checks, in [tests/test_representativeness.py](tests/test_representativeness.py) (40) and [tests/test_satellite.py](tests/test_satellite.py) (20)
Expected values are independent: rectangle areas by hand, the 0.75/0.25 moments written out, contours listed from binary-exact weights, calendar day counts, and numpy metrics on masks built from the synthetic flags.
- **Mixed land cover** (30 m map; uniform 10 m footprint shifted by 25 m, so cells straddle the map edges at 30, 60 and 90 m):
  - Class shares equal the area ratios 3150/6175 and 3025/6175.
  - The nodata area is 125/6300 of the grid total and is reported separately.
  - Valid weight is 6175/6300, and the shares sum to 1.
  - A cell straddling a class boundary splits 0.125/0.125; a footprint half outside the map gives outside fraction 0.5.
  - A class missing from the legend raises.
- **Index:** a uniform index has SD 0. Weights 0.75/0.25 on 0.2/0.6 give mean 0.3, SD √0.1875·0.4 and n_eff 1.6, both directly and through the transfer.
- **Contours:** {0.5, 0.25} at level 0.75. At level 0.8 both tied 0.125 cells are included (mass 1.0). Contour shares are 2/3 and 1/3 against 0.5 and 0.5 for the full domain, both kept. Under a different map the contour mass and membership are unchanged.
- **Matching:**
  - `nearest` picks the same-year map (0 days, retrospective). `previous_only` picks 2023 (157 days, not retrospective).
  - For 2025-03-01, `nearest` gives 60 days (past), and `within_30_days` gives nothing.
  - An 8-day tie goes to the past scene; 7 days ahead gives a future scene (retrospective) under `nearest` and the past one under `previous_only`. A same-day scene is not retrospective.
  - Days without an asset are per-day `not_available` rows with no data rows, and retrospective rows are flagged.
- **Fixed supports:** the 3×3 window gives 6/9 and 3/9. Footprint − window and footprint − pixel differences are as computed by hand (a class absent on one side has share 0). Partial windows are reported, and polygon area shares are exact (0.5/0.5).
- **Labels never enter numbers:** labels `high` and `low` give identical class, index and difference tables, every label row carries `label_not_accuracy_guarantee`, and the numeric tables have no label column.
- **fluxfootprints against the real API:** `p_footprint` = 0.8, `p_target` equals an independent disc count, and the label equals upstream `classify_categorical`. The `(x, y)` orientation is tested separately.
- **No acquisition metadata:** a cohort with daily values in every model and no source gives no error rows and `not_available` (`no_acquisition_metadata`). Flags on 60 of 120 days leave 60 days `unknown`, never interpolated.
- **Clear and interpolated subsets:** with per-model flags (three days where one model differs, counted as mixed), every model has identical subset days, and MBE/MAE/RMSE equal numpy and `compute_metrics`. Per-model counts are recorded, and a 5-day subset is `sparse_subset` with no value.
- **Timing:** days since, nearest and enclosing gap are checked by hand. Count sources convert with `min_clear_count`, bins count 15/45/60 days, the enclosing gap is retrospective, and 7 trailing days are `variable_missing`.
- **Also tested:** unfrozen cohorts are refused, and missing model support gives `support_missing_for_models`. Non-boolean and null flags are refused.
- **Parquet:** all six tables round-trip; `test_schemas` covers every new schema too.
- **Config:** 21 representativeness and 11 satellite rejection cases, among them a missing legend, vintage or acquisition date, an unknown or undeclared policy, contour levels 0 and 1, an undeclared nodata, scene cloud cover, missing authority or meaning, a label threshold key and a non-frozen comparison.
- **Import:** the gate now also blocks `fluxfootprints`, `xarray` and `pyogrio`. etcompare imports without the geo extra; this is also exercised by the oldest stack.

### US-UTJ run (D-054, D-058; local outputs)
- **Inputs** were confirmed by the user (D-054):
  - Annual NLCD, which replaced a folder that turned out to hold the LCNext stratum map;
  - CDL 2024 at 30 m;
  - `Bluff.gpkg`, with the CRS and tower field declared by the user;
  - no NDVI and no acquisition metadata.
- **Corrupted footprints restored:** three daily files per etcompare-built set (the D-043 dates) were still corrupt in this checkout, and two passed `prepare_footprint`. They were restored from the other clone, and a guard was added.
- **Independent checks:** point sampling (8×8 per footprint cell) agrees to ≤ 2.7e-4, the window lattice gives 0.2852/0.5984, the polygon Monte Carlo agrees to ≤ 0.001, and fluxfootprints `p_footprint` equals etcompare to 1e-6.
- **Footprint, full domain:** NLCD crops 0.394 and woody wetlands 0.570; CDL alfalfa 0.696 and shrubland 0.282. The field polygon is 0.900 crops (NLCD) and 0.878 alfalfa (CDL). The footprint is never the field.
- **NLCD and CDL disagree** on the riparian strip east of the tower. The tower's OpenET pixel is woody wetlands in NLCD and alfalfa in CDL. This is not resolved.
- **Matching:** NLCD `nearest` (same year, retrospective) and `previous_only` (previous year) agree. CDL `previous_only` leaves the 191 days of 2024 `not_available`.
- **fluxfootprints:** 20,459 `low` and 1 `medium`. Labels only.
- **Satellite support:** `not_available` for all four frozen ranking cohorts, with zero rows (D-011, D-057).
- **Restated:**
  - IGBP CRO is a site label, not a fraction.
  - No acquisition flags exist (D-011).
  - No intervals are attached in step 13. Elsewhere, 28-day blocks mostly lack support and the 7- and 14-day intervals are probably too narrow (D-048).
  - Gap-filled reference days share gridMET drivers (D-005).

### Assumptions and limitations
- **One station, descriptive only.** Landscape shares describe what each support sees. They are not ET errors, and they are not accuracy guarantees.
- **Map quality.** The two 30 m maps disagree near the tower, and the 30 m CDL is resampled by NASS from 10 m. No map is treated as truth.
- **Polygon vintage** is unknown, and the field boundary may predate or postdate the record.
- **Same-year annual maps** are labelled retrospective because they are built from the whole year.
- **fluxfootprints** is installed from a local checkout, not as an extra. Its chi-square uses pseudo-counts (upstream note).
- **Not implemented:** scene-level cloud-cover diagnostics, which need authorization to query and are never pixel flags.

### Open (user decisions)
- **Matching policy** (`nearest` vs `previous_only`; none adopted) and the **contour level** (0.8, Provisional).
- **Which map, if either, describes the riparian strip.** A 10 m CDL or a site survey would help.
- **The vintage of `Bluff.gpkg`.**
- **Whether to declare a vegetation-index series** (with acquisition dates), and whether any acquisition metadata exists.
- **Whether to keep fluxfootprints installed** outside the project metadata.
- **`openet_rasters.root`** in the example config is stale after the Drive reorganisation (`unclipped/`).
- **Carried over:** block length and support (D-048), equivalence margins (D-049), quality threshold (D-008), measured-LE coverage minimum (D-038), metric tolerances (D-044), imputation driver (D-042), diagnostic defaults (D-051), and whether to keep `utj_rank_closure` and `utj_rank_methods`.

**Next:** step 14. Anomalies and management events: held-out seasonal anomalies (climatologies fitted on training years only) and event windows from management records. The crop-height model's cutting dates (D-042) are candidate events; their source must be declared.

## Step 12 — Closure, coverage and environmental diagnostics (2026-10-16)

**Run it**
```bash
uv run python -m pytest                           # 417 passed, 3 skipped (opt-in Drive raster tests)
uv run ruff check src tests tools/run_cohorts.py && uv run mypy   # clean
uv run python tools/run_cohorts.py examples/us_utj.yaml           # + diagnostics (~3 min 45 s)
UV_LINK_MODE=copy uv run --isolated --resolution lowest-direct --python 3.11 --with pytest \
    python -W error::FutureWarning -m pytest tests/test_diagnostics.py tests/test_ranking.py \
    tests/test_bootstrap.py tests/test_metrics.py tests/test_aggregation.py tests/test_cohorts.py \
    tests/test_schemas.py tests/test_config.py tests/test_imputation.py   # oldest stack: 310 passed
```

### API
- **`etcompare.diagnostics.diagnose_comparison(cohort, spec, metric_spec, *, station_daily, extractions=(), bootstrap=None)`** returns an immutable `DiagnosticsResult` with:
  - `closure` (`closure_magnitude` v1) and `provenance` (`reference_provenance_periods` v1);
  - `conditional` (`conditional_errors` v1) and `regressions` (`error_regressions` v1);
  - `availability` (`diagnostic_availability` v1);
  - `bins`, `report`, `tables()` and `summary()`.
- **`reference_rank_changes(sensitivity, diagnostics_id)`** turns a `ranking_sensitivity` table into `reference_rank_changes` v1 without re-ranking.
- **Building blocks** (public, tested): `resolve_bins` (it sees no errors), `training_quantile_edges`, `assign_bins`, `wind_sector`, `sector_bounds`, `season_index`, `season_labels`, `month_labels` and `BinDefinition`.
- **Config:**
  - New `DiagnosticsSpec` and `DiagnosticVariableSpec`, in a `diagnostics:` YAML section.
  - `DiagnosticsSpec` fields: `diagnostics_id`, `metric_set`, `min_bin_days` (mandatory), `metrics`, `bootstrap`, `calendar`, `gap_filled_fraction_edges`, `provenance_subsets`, `closure`, `closure_metadata`, `sensible_heat_variable`, `net_radiation_variable`, `wind_direction_variable`, `variables` and `comparisons`.
  - `DiagnosticVariableSpec` fields: `source`, `column`, `units`, `meaning`, `status` (all mandatory); bins as `edges`, `quantiles` + `training_start`/`training_end`, or `n_sectors`; plus `kind`, `measured_days_only` and `note`.
  - New constants: `DIAGNOSTIC_SOURCES`, `VARIABLE_STATUSES`, `VARIABLE_KINDS`, `CALENDAR_CONDITIONS` and `DIAGNOSTIC_METRICS`. `Config.diagnostics_spec()` is new.

### Delivered
- **[src/etcompare/diagnostics.py](src/etcompare/diagnostics.py)**; the rules are in the module docstring and D-051:
  - **Correction magnitude:** existing `ET_corr − ET`, `ET_corr/ET`, `ebc_cf` and declared closure metadata on `corrected_from_raw` days only, by all / month / season, for the station record and the cohort's days. Gap-filled days are excluded and counted. Nothing is recomputed, and every row is labelled reference sensitivity.
  - **Provenance subsets** (all accepted, `corrected_from_raw`, `corrected_filled`) inside one frozen cohort, with gridMET flags (D-005). Gap-filled fraction per period, and as a conditioning variable.
  - **Conditional errors per declared bin and model** via `compute_metrics`:
    - bins are fixed before errors exist;
    - remainder rows (outside edges, missing value, excluded provenance) account for every day;
    - sparse and empty bins carry a status and no value;
    - `bin_days_shared` and `n_days_bin_cohort` are explicit.
  - **Optional bin intervals** reuse the step-10 blocks, runs and streams at every block length.
  - **Process diagnostics:** a labelled negative daily-mean H bin; sector logic for wind direction; net-radiation regressions, corrected (flagged `mathematical_coupling`) and raw.
  - **Explicit `not_available` rows** for undeclared, unverified, missing, non-numeric, all-null or out-of-range variables, daytime H, wind direction and satellite support.
- **Schemas:** the six tables above, all v1, keyed so that cohorts, variants, sides, variables, bins, metrics and block lengths cannot collide.
- **Example config:** `utj_diagnostics` on the six ranking cohorts plus `utj_closure_all_models`, all **Provisional** (D-051):
  - `min_bin_days` 20; MBE, MAE and RMSE; the `utj_block_bootstrap` intervals.
  - Fixed round edges for gridMET ETo, VPD, wind speed, daily H and closure ratio, and for the retained and domain-capture fractions.
  - `h_subday_gaps`, `le_subday_gaps` and `rh` are declared `unverified`; wind direction is not declared.
- **[tools/run_cohorts.py](tools/run_cohorts.py)** writes `<id>__utj_diagnostics_{closure,provenance,conditional,regressions,availability}.{parquet,csv}` and `utj_diagnostics__utj_model_ranking_reference_rank_changes.{parquet,csv}`. Each report lists the skipped analyses, the closure magnitude and daily MAE per bin. With `--no-bootstrap` the diagnostics run without intervals.
- **Docs:** D-051 (rules and defaults), D-052 (US-UTJ run); a new contract §9 row and a §4.4 note on the station-variable declarations.

### Gate checks, in [tests/test_diagnostics.py](tests/test_diagnostics.py) (53 tests)
Expected values are independent: numpy masks and formulas on the cohort's paired rows, closure magnitudes from the synthetic ET and factors, and hand-written sector boundaries.
- **Unknown meteorology:** a missing column, an unverified variable and an absent direction column give `not_available` (`column_missing` / `variable_unverified`). Wind speed, H, closure ratio, season, month, gap fraction, provenance, closure and regressions all still run. Non-numeric and all-null columns give `non_numeric` / `no_values`, and out-of-range directions give `out_of_range`.
- **Sparse bins:** a 5-day bin is `sparse_bin`, with a null value and the counts in `status_detail`, and a 0-day bin is `empty_bin`.
- **Wind wrap:** 359°, 1°, 0° and 360° are north. With 8 sectors, 22.4999° is north, 22.5° opens NE, 337.5° is north and 337.4999° is NW. With 4 sectors, 45° opens E. Sector bounds are checked, values outside [0, 360] raise, and NaN gives −1. Through the diagnostics, alternating 359°/1° days all fall in sector 0.
- **Bins from config, not errors:** two runs with very different modeled values give identical bin edges, labels and day counts, but different metrics. Training-quantile edges equal `numpy.quantile` of the training-period values and are unchanged when ET changes; tied quantiles are refused.
- **Closure:** with known factors (1.2 in Jan–Feb, 1.5 later) and 15 January days filled with absurd values and factors, the correction mean, max and March median match numpy. The ratio and `ebc_cf` give mean 1.2 with standard deviation 0 in January, over 16 days with 15 excluded. DJF has 44 days, closure metadata matches, and the cohort day set agrees.
- **Provenance subsets:** for every model, the all / measured / filled subsets have 240 / 192 / 48 days, are `bin_days_shared`, and their RMSE matches numpy. Filled rows carry `not_independent_validation`; the monthly gap fraction matches.
- **Conditional metrics:** MBE, MAE and RMSE for every VPD and H bin and model equal numpy on the bin sample. The bins plus remainders sum to the series, and the negative-H bin carries its labels.
- **Also tested:**
  - `measured_days_only` puts filled days in the excluded remainder.
  - An extraction variable that differs by model gives unshared bins, flagged, and a `not_available` row for the side without values.
  - Raw-reference regression slopes equal `numpy.polyfit`; corrected rows are flagged coupled; a corrected-only cohort gives `cohort_lacks_variant`.
  - **Bin intervals:** the same seed reproduces them; 7- and 14-day rows exist with `n_blocks` = ⌊240/b⌋; a bin of alternate days is `insufficient_support`; a sparse bin is `no_estimate`; a mismatched bootstrap raises.
  - **Rank changes:** effect labels (closure, gap filling + days, closure + days); other dimensions are dropped; the wrong table is refused.
  - **Parquet:** all five result tables round-trip, and the primary keys are unique (every new schema is also covered in `test_schemas`). Results are immutable copies.
  - **Config:** 34 rejection cases plus duplicate IDs, a mismatched bootstrap metric set, and the example declarations.

### US-UTJ run (D-052; local outputs)
- **Independent check:** 3,222 non-sparse bin metrics (VPD, ETo, H, wind) recomputed with numpy from the written paired files and the raw CSV match to 8.9e-16. The closure mean and count match the CSV, and the provenance subsets have identical days across models in every cohort.
- **Not available:** domain capture (no values for the NLDAS set), subdaily gaps (unverified), daytime negative-H frequency, wind direction and satellite support. The retained fraction is unavailable on the window side, and the raw net-radiation version is unavailable in corrected-only cohorts.
- **Correction:** `ET_corr − ET` median 0.20 mm d⁻¹ (5–95 % 0.03–2.28) and ratio median 1.26 (1.15–1.53) on 303 measured days, with 170 filled days excluded. The ratio is highest in summer 2024.
- **Rank changes:** closure (`et_raw` vs `et_corrected_measured`, same 188 days) moves 6 of 7 models in MAE and RMSE, by up to 5 positions. Adding the gap-filled days (measured vs inclusive) moves 5 in MAE and 2 in RMSE, by up to 3.
- **Gap-filled days:** 93 of the 281 inclusive days. The monthly fraction is 0 to 1.0, with all of May 2025 filled. MAE is higher on filled than on measured days for 6 of 7 models. Season is confounded with fill status, and the filled days share gridMET drivers (D-005), so this is not independent validation.
- **Bins and intervals:** several extreme bins are sparse. Scattered-day bins have no interval at any block length. **28-day blocks give no bin interval at all**, and the 7- and 14-day intervals are probably too narrow (D-048).
- **Net radiation:** the corrected-minus-raw slope is about −0.0076 mm d⁻¹ per W m⁻² for every model. That is the coupling through the correction, not a model property.

### Units confirmed (user, 2026-10-16; D-053)
- The user confirmed the station units, with `US-UTJ.ini` as supporting metadata. gridMET ETo, VPD (kPa), wind speed (m s⁻¹), daily H and net radiation (W m⁻²) and RH (%) are now `verified`.
- The `.ini` lists VPD in hPa, but it describes the half-hourly input; the daily export is kPa.
- `ebr` and `ebr_5day_clim` stay `provisional`, because the closure window is unresolved. RH has no bins, since VPD is the humidity condition.
- The diagnostics were rerun. Only labels and flags changed (`variable_provisional`); values, bins and edges are unchanged.
- Wind direction exists only half-hourly (`WD`). A daily sector diagnostic needs an upstream daily aggregation of direction.

### Assumptions and limitations
- **Station level, one station.** Conditional summaries are associations in about 1.3 years at one site. They are not causal and do not transfer to other sites.
- **Units.** Station units are user-confirmed (D-053). The closure method and window behind `ebr` and `ebr_5day_clim` remain unresolved.
- **Bins with scattered days** cannot support block intervals. Blocks are built from runs of consecutive bin days, as step 10 requires.
- **Model-specific bin days.** Extraction-based variables can give bins whose days differ by model. These are flagged and are not like-for-like model comparisons.
- **The negative-H bin is daily-mean only.** It is neither a daytime frequency nor proof of advection.
- **The coupling remains.** The corrected-reference net-radiation regression stays coupled; the raw version exists only for cohorts with `et_raw`.

### Open (user decisions)
- **Station variables:** units were confirmed (D-053). Still open:
  - wind-speed measurement height;
  - the closure method and window (`ebr`, `ebr_5day_clim`);
  - the subdaily gap definitions;
  - whether to export a daily wind direction (or daytime sector) so that the sector diagnostic can run.
- **Diagnostic defaults:** `min_bin_days` (20) and the bin edges are Provisional.
- **Carried over:**
  - the block length and support decision (D-048);
  - equivalence margins and formal tests (D-049);
  - the quality threshold (D-008), the measured-LE coverage minimum (D-038), the metric tolerances (D-044) and the imputation driver (D-042);
  - whether to keep `utj_rank_closure` and `utj_rank_methods`.

**Next (at the time):** step 13. Superseded by the Step 13 entry above.

## Step 11 — Ranking and paired contrasts (2026-10-16)

**Run it**
```bash
uv run python -m pytest                           # 358 passed, 3 skipped (opt-in Drive raster tests)
uv run ruff check src tests tools/run_cohorts.py && uv run mypy   # clean
uv run python tools/run_cohorts.py examples/us_utj.yaml           # + rankings (~2 min 45 s)
UV_LINK_MODE=copy uv run --isolated --resolution lowest-direct --python 3.11 --with pytest \
    python -W error::FutureWarning -m pytest tests/test_ranking.py tests/test_bootstrap.py \
    tests/test_metrics.py tests/test_aggregation.py tests/test_cohorts.py tests/test_schemas.py \
    tests/test_config.py tests/test_imputation.py # oldest stack: 251 passed
```
`UV_LINK_MODE=copy` is needed here because OneDrive refuses uv's hard links in the isolated environment.

### API
- **`etcompare.ranking.rank_comparison(cohort, metric_results, bootstrap, spec, *, subset="all")`** returns an immutable `RankingResult` with:
  - `rankings` (`model_rankings` v1), `rank_distribution` (v1), `contrasts` (`model_contrasts` v1) and `network` (`network_summaries` v1);
  - `report` and `summary()`.

  `bootstrap=None` gives point ranks only (`not_resampled`).
- **`network_summaries(metrics, spec)`** works on one comparison's `metrics` table. **`ranking_sensitivity(results)`** returns `ranking_sensitivity` v1.
- **Building blocks** (public, tested): `check_rankable`, `oriented`, `rank_positions`, `adjust_p_values`, `bootstrap_p_value` and `year_subsets`.
- **Config:**
  - New `RankingSpec` in a `rankings:` YAML section, with fields `ranking_id`, `metric_set`, `bootstrap`, `metrics` (metric → direction), `equivalence_margins`, `period_equivalence_margins`, `network_summaries`, `formal_tests`, `multiplicity`, `year_subsets` and `comparisons`.
  - New constants: `RANKING_DIRECTIONS` (the one place where directions are declared), `DIRECTIONS`, `NETWORK_SUMMARY_TYPES` and `MULTIPLICITY_POLICIES`.

### Delivered
- **[src/etcompare/ranking.py](src/etcompare/ranking.py)**; the rules are in the module docstring and D-049:
  - Only `all_models` and `pairwise` cohorts and only score metrics are ranked, inside one frozen context. Mixed fingerprints, variants, sides, scales, products, units or support raise.
  - **Ties:** mid-ranks, with tied positions shared equally.
  - **Rank stability per block length, side by side:** mean rank, a rank interval, top-rank frequency (labelled a bootstrap stability measure) and the position distribution. All come from `BootstrapResult.replicates()`, replicate by replicate; nothing is redrawn and no metric is re-implemented.
  - **Statuses** carry through: `insufficient_support`, `insufficient_valid_replicates`, `estimate_undefined` and `not_resampled`.
  - **`leader_status`:** `unique_leader` only when the best model's contrast intervals exclude zero against every other model. Otherwise `no_unique_leader`, with `leading_models` listed.
  - **Contrasts** are `metric(model_a) − metric(model_b)`, with the earlier-declared model as the baseline. Each row has a sign meaning, `interval_position` and `favoured_model`. Closest-to-zero metrics get `value` and `absolute_value` rows.
  - **Equivalence** (`not_assessed` without a margin) and optional formal tests under Holm or Bonferroni; everything else is `exploratory`.
  - **Network summaries:** equal-site mean (primary), observation and sqrt-observation weighting, and pooled RMSE (labelled as not the mean station RMSE). Each carries `n_sites`; one site gives `single_site` and `not_network_evidence`.
  - **Sensitivity** across station, year, temporal scale, reference variant, extraction side and cohort.
- **Schemas:** `model_rankings`, `rank_distribution`, `model_contrasts`, `network_summaries` and `ranking_sensitivity`, all v1. Ranking, contrast and distribution tables are keyed by `ranking_id`, comparison, cohort, subset, metric set, bootstrap, scope, scale, period type, aggregation, product, variant, side, metric, model(s), basis, interval method, block unit and block length, so cohorts, block lengths and products cannot collide. They carry `n_sites`, `n_days`, `n_periods`, `n_segments`, `n_blocks`, statuses, the cohort fingerprint and flags.
- **Example config:**
  - Ranking `utj_model_ranking` with MAE, RMSE, `nrmse_mean`, r, r², NSE, KGE 2009, MBE and percent bias.
  - No equivalence margins; all four network summaries; `formal_tests: false`; `year_subsets: true`.
  - Six cohorts, including two new all-model sensitivity cohorts: `utj_rank_closure` (`et_raw` + `et_corrected_measured`) and `utj_rank_methods` (footprint + 3×3 window). All of this is **Provisional** (D-049).
- **[tools/run_cohorts.py](tools/run_cohorts.py)** writes `<id>__utj_model_ranking_{rankings,rank_distribution,contrasts,network}.{parquet,csv}` (all subsets, every block length) and `utj_model_ranking_sensitivity.{parquet,csv}`. The reports show daily ranks per block length, leader status and network `n_sites`.
- **Docs:** D-049 (rules and defaults), D-050 (US-UTJ run); a new contract §9 row.

### Gate checks, in [tests/test_ranking.py](tests/test_ranking.py) (45 tests)
Expected values are independent: `scipy.stats.rankdata` and `numpy.quantile` on the step-10 replicate arrays, numpy metrics of concatenated or duplicated records, and Holm/Bonferroni by hand.
- **Duplicated station record:**
  - Duplicating site A's record (every day twice) leaves its station metric unchanged (checked with `compute_metrics`) and doubles `n_days`.
  - The equal-site mean is unchanged, while the observation-weighted mean changes, from (120a + 80b)/200 to (240a + 80b)/320.
  - A copy added as a separate *site* is a new site (n_sites = 3), and the equal-site mean becomes (2a + b)/3.
  - Pooled RMSE equals the RMSE of the concatenated errors and differs from the mean station RMSE.
- **Known small advantage:** errors of k·e with k = 1, 1.02, 3.
  - MAE(bbb) − MAE(aaa) = 0.02·mean|e|, with an interval entirely above 0, favouring aaa. NSE is negative and |MBE| positive, also favouring aaa.
  - Ranks are 1/2/3 for MAE, RMSE, NSE and |MBE|; top-rank frequency is 1, with a unique leader.
- **No pooling:** mixed fingerprints, reference variants, extraction methods and period products raise in `check_rankable`. Metric results of another cohort are refused, and `model_available` and `paired_method` cohorts are refused.
- **Diagnostic metrics:** `bias_corrected_rmse`, `mape`, `ols_slope` and `kge_2009_alpha` are refused by the config and by `check_rankable`.
- **Identical models:**
  - The replicate arrays are identical and the contrasts are exactly 0 in estimate and bounds.
  - Both models get mid-rank 1.5 with `n_tied` = 2, top-rank frequency 0.5 each and positions {1: 0.5, 2: 0.5}.
  - The result is `no_unique_leader`, and an MAE margin gives `equivalent`.
- **Overlapping intervals:** three equal-noise models give `no_unique_leader` at both block lengths, top-rank frequencies sum to 1, and equivalence is `not_assessed` even though the intervals cross zero.
- **Rank statistics match the replicates:** mean rank, top-rank frequency, rank bounds, the position distribution (each position sums to 1) and contrast bounds.
- **Equivalence:** without a margin the result is `not_assessed` (or `not_applicable` for signed closest-to-zero rows). Margins below, above and inside the interval give `practically_different`, `equivalent` and `inconclusive`.
- **Pairwise contrasts** equal the step-10 pairwise intervals exactly.
- **Statuses:** a constant model gives `estimate_undefined` for r and leaves the defined pairs `ok`. 50-day blocks give `insufficient_support`, with point ranks kept and statistics null. With no bootstrap the status is `not_resampled`.
- **Formal tests:** Holm and Bonferroni are checked against hand values; tested rows are `formal`, the rest `exploratory`; `family_size` is checked. Without `formal_tests` every row is `exploratory`, with no p-values.
- **Single site:** status `single_site`, the `not_network_evidence` flag and a report warning. Mixed comparisons refuse network summaries, and an undefined site metric leaves the summary undefined.
- **Sensitivity:** reference-variant (same cohort), temporal-scale (daily vs monthly) and year dimensions are found; station and extraction are reported absent; `rank_change` = `rank_b` − `rank_a`.
- **Parquet:** all five tables round-trip (also every new schema in `test_schemas`). Results are immutable.
- **Config:** 25 rejection cases plus duplicate IDs.

### US-UTJ run (D-050; local outputs)
- **Independent check:** ranks recomputed with `scipy.stats.rankdata` from the written paired files match every daily ranking. Values and contrast estimates match the metric tables to 1.1e-14.
- **No block length is chosen.** 28-day blocks lack support everywhere (set A and pairwise-2024 also lack 14-day support). The 7- and 14-day intervals are probably too narrow (D-048).
- **No all-model cohort has a unique leader** for any metric at 7 or 14 days. In the pairwise cohort, eemetric's |MBE|, |percent bias| and KGE are separated; MAE, RMSE and NSE are not.
- **Reference variant (raw vs corrected) moves rankings most:** 6 of 7 models move in MAE and RMSE, by up to 5 positions. Footprint vs 3×3 window moves them less.
- **Network summaries:** n_sites = 1, `single_site`; not network evidence.
- **Gap-filled days:** the 93 gap-filled reference days of the inclusive cohort share gridMET drivers (D-005).

### Assumptions and limitations
- **Station level only.** Network summaries carry no interval: hierarchical site × calendar-block resampling is not implemented (a documented limitation). With one station, every network summary is a station result.
- **Ties are exact.** There is no tie tolerance: models whose metrics differ only by round-off are not tied.
- **Leader status and equivalence** use the first declared interval method at each block length. Equivalence on a 95 % interval is a TOST at 2.5 % per side.
- **Year subsets** are daily only (no period products) and are rebuilt by the runner from the same inputs.
- **Sensitivity uses point ranks only.** Cross-cohort pairs (`same_cohort = false`) differ in days as well.

### Open (user decisions)
- **Equivalence margins:** none declared for US-UTJ. Any margin must be fixed before ranking and per metric.
- **Formal tests and multiplicity policy:** not requested; every contrast is exploratory.
- **Block length and support** (D-048): still open. Rankings report every length side by side.
- **The two new sensitivity cohorts** (`utj_rank_closure`, `utj_rank_methods`): keep, change or drop.
- Carried over: the quality threshold (D-008), the measured-LE coverage minimum (D-038), the metric tolerances (D-044), the imputation driver (D-042) and the Provisional bootstrap defaults (D-047).

**Next (at the time):** step 12. Superseded by the Step 12 entry above.

## Step 10 — Paired block-bootstrap intervals (2026-10-16)

**Run it**
```bash
uv run python -m pytest                           # 308 passed, 3 skipped (opt-in Drive raster tests)
uv run ruff check src tests tools/run_cohorts.py && uv run mypy   # clean
uv run python tools/run_cohorts.py examples/us_utj.yaml           # cohorts, totals, metrics, intervals (~80 s)
uv run --isolated --resolution lowest-direct --python 3.11 --with pytest \
    python -W error::FutureWarning -m pytest tests/test_bootstrap.py tests/test_metrics.py \
    tests/test_aggregation.py tests/test_cohorts.py tests/test_schemas.py tests/test_config.py \
    tests/test_imputation.py                      # oldest stack: 201 passed
```
Use `python -m pytest` in this checkout: the `.venv/Scripts/pytest.exe` launcher has a baked-in path to the OneDrive clone's interpreter, so the plain `pytest` command runs the wrong code.

### API
- **`etcompare.bootstrap.bootstrap_comparison(cohort, metric_spec, bootstrap_spec, *, products, period_totals=(), metric_results=None)`** returns an immutable `BootstrapResult` with:
  - `intervals` (the `intervals` schema, now v2);
  - `autocorrelation` (new `residual_autocorrelation` v1);
  - `replicates()`: an index frame plus a (series × replicate) array, drawn on shared indices, kept for step 11;
  - `report` and `summary()`.
- **Building blocks** (public, tested on their own):
  - `segment_bounds` and `season_year`;
  - `block_support` (disjoint full-length blocks);
  - `draw_blocks` / `block_indices`;
  - `daily_samples` / `period_samples` (paired samples with one common mask);
  - `sample_rng` (per-sample streams from the declared seed);
  - `residual_autocorrelation`.
- **Config:**
  - `BootstrapSpec` in a new `bootstrap:` YAML section.
  - Mandatory fields: `block_lengths`, `seed`, `n_replicates`, `confidence_level`, `min_blocks` and `segment_boundary` (`none`, `year` or `water_year`).
  - Optional fields: `interval_methods` (`percentile`, the default, or `basic`), `period_block_lengths`, `water_year_start_month`, `min_valid_fraction` (0.9), `acf_max_lag` (default: the longest block), `contrasts` and `comparisons`.
  - The loader checks that the metric set and comparisons exist, and that period block lengths are given only when the metric set scores period products.
  - New constants: `INTERVAL_METHODS` and `SEGMENT_BOUNDARIES`.

### Delivered
- **[src/etcompare/bootstrap.py](src/etcompare/bootstrap.py)** implements segment-truncated moving blocks. The estimand, edge handling and limitations are in the module docstring and D-047.
  - **Resampling unit:** the cohort day. Whole paired rows are resampled, with identical indices for every model, side and variant of a cohort and site.
  - **Blocks** stay inside segments: they split at missing days, at sites and at the season-year boundary, never span a gap and never wrap.
  - **Metrics** are recomputed with `compute_metrics` in every replicate. Point estimates come from `score_comparison`.
  - **Period metrics** resample whole periods. They are never rebuilt from daily blocks.
  - **Contrasts** are `metric(target) − metric(baseline)` for paired sides, paired reference variants and pairwise models. Model contrasts of the all-model cohorts are left to step 11.
  - **Statuses:** `ok`, `insufficient_support` (with counts in `status_detail`), `estimate_undefined` and `insufficient_valid_replicates`.
  - **Flags:** `short_segments=<k>`, `undefined_replicates=<k>` and `estimate_outside_interval`, plus the metric flags.
  - **Residual autocorrelation** at exact calendar lags, with a white-noise bound.
- **Schemas:** `intervals` v1 → v2. It is keyed by cohort, bootstrap, metric set, scope, sample, series, contrast, metric, interval method, block unit and block length. Columns: estimate, bounds, units, level, seed, requested and valid replicates, `n_segments`, `n_blocks`, `n_days`, `n_periods`, status, status detail, role, ranking scope and flags. Nothing wrote v1. `residual_autocorrelation` v1 is new.
- **Example config:** `utj_block_bootstrap` with block lengths 7/14/28, period block length 1, seed 20261016, 2,000 replicates, 95 % percentile intervals, `min_blocks` 10, `water_year` segments and lags to 28. All are **Provisional** (D-047).
- **[tools/run_cohorts.py](tools/run_cohorts.py)** writes `<id>__utj_block_bootstrap_{intervals,autocorrelation}.{parquet,csv}` at every declared block length. The report shows statuses, MBE/RMSE intervals side by side per block length, and an autocorrelation summary. `--no-bootstrap` skips this step.
- **Docs:** D-046 (step 9 growing-season run), D-047 (rules and defaults), D-048 (US-UTJ run); a new contract §9 row.

### Gate checks, in [tests/test_bootstrap.py](tests/test_bootstrap.py) (46 tests)
Expected values are independent: block geometry from the dates, Monte Carlo with `scipy.signal.lfilter`, explicit numpy loops, and `numpy.quantile` on the returned replicates.
- **Identical predictions:** two models with identical predictions in a pairwise cohort give a contrast of exactly 0 in every replicate, with zero bounds for all metrics. Identical sides give a zero method contrast.
- **Seed:** the same seed gives identical tables and replicates; another seed gives different bounds.
- **AR(1) errors** (φ = 0.8, n = 1000):
  - The Monte Carlo 95 % width of the mean error (4,000 series) matches the AR(1) theory to 10 %.
  - iid-day resampling (b = 1) gives an MBE interval narrower than half that width (theory: one third).
  - 28-day blocks give 0.7–1.3× the true width.
  - The lag-1 autocorrelation diagnostic recovers φ (±0.06).
- **Too few blocks:** 30 days at b = 7 with `min_blocks` 10 give `insufficient_support`, with null bounds, 0 valid replicates and the counts in `status_detail`; the estimate is still reported. Five 6-day segments give 0 blocks of 7 days.
- **Gaps are never bridged:** for 200 draws at b = 1, 3, 7 and 28 over a gappy record, every block consists of consecutive calendar days in one water year, and the lengths sum to n. The documented `min(j + 1, b)` edge weighting is reproduced. b = 1 is iid.
- **Paired removal:** a day missing from one side is absent from both sides' samples. A 500 mm outlier on the other side never enters a replicate, and the contrast is 0 in every replicate.
- **Parquet:** intervals and autocorrelation tables round-trip identically, with their attributes.
- **Also tested:**
  - `compute_metrics` is called exactly replicates × series × block lengths times, and estimates equal `score_comparison`.
  - Percentile and basic bounds match `numpy.quantile`.
  - Undefined estimates and invalid replicates.
  - An empty cohort.
  - Contrast signs: window − footprint MAE > 0 when the footprint error is smaller; percentage-point units; corrected − raw.
  - Period resampling: 13 complete months split by a missing month into 5 + 8, giving 6 blocks at b = 2.
  - Exact-lag autocorrelation across gaps.
- **Config:** 23 rejection cases, plus duplicate IDs and a metric-set mismatch.
- **Segments** split at gaps and at year and water-year boundaries, and month periods continue across consecutive months.

### US-UTJ run (D-048; local outputs)
- **All 7 comparisons** produced intervals. Every estimate equals its step-9 metric, and only `ok` rows carry bounds.
- **Independent check:** segment and block counts recomputed from the paired files with pandas match every daily row.
- **Support at `min_blocks` = 10:**
  - 7-day intervals exist for every cohort.
  - 14-day intervals exist for every cohort except set A (9 blocks).
  - 28-day intervals exist only for the three long-record models of the coverage cohort; elsewhere there are 2–9 blocks.
  - Period intervals exist only for monthly products with at least 10 periods (mostly partial sums).
- **Residual autocorrelation is strong and long:**
  - lag 1: 0.43–0.93;
  - lag 14: up to 0.54;
  - 13 of 58 series never fall inside the white-noise bound by lag 28.
- **Block-length sensitivity:** intervals widen by a median factor of 1.09–1.27 from 7- to 14-day blocks. The 7- and 14-day intervals are therefore probably too narrow for many series.
- **No block length is chosen.**

### Assumptions and limitations
- **Station level only.** A fixed network differs from a site population, which would need hierarchical site × block resampling with common calendar blocks. Network summaries and rankings are step 11.
- **Stationarity.** The moving-block bootstrap assumes stationary, short-memory dependence. US-UTJ errors vary by season (D-048), which violates this.
- **Edge effect.** The first b − 1 days of each segment are under-sampled.
- **Shared drivers.** Gap-filled and imputed days are resampled like measured days; their shared drivers are not modelled (D-005, D-042).
- **Partial-sum period intervals** inherit the descriptive status of partial sums (months with unequal matched days).
- **Inputs copied:** this checkout lacked the step 5–6 extraction outputs. They were copied byte-identically from the other local clone (`OneDrive/Documents/github/comparison`), which is one commit behind, and cohort sizes reproduce D-040.

### Open (user decisions)
- **Block length and support:** whether to accept that 28-day blocks are unsupported at `min_blocks` = 10, to lower `min_blocks`, or to address seasonal non-stationarity (for example seasonal strata, or a block length fixed from the autocorrelation diagnostic before scoring). Never by interval width.
- **Period block length** (1, i.e. months treated as independent) and the other Provisional defaults (D-047).
- Carried over: the quality threshold (D-008), the measured-LE coverage minimum (D-038), the metric tolerances (D-044) and the imputation driver (D-042).

**Next (at the time):** step 11. Superseded by the Step 11 entry above.

## Step 9 — Transparent performance metrics (2026-10-15)

**Run it**
```bash
uv run pytest                                     # 259 passed, 5 skipped (see "Not run here")
uv run ruff check src tests && uv run mypy        # clean
uv run python tools/run_cohorts.py examples/us_utj.yaml --skip-missing-crop-height
```

### API
- **`etcompare.metrics.compute_metrics(observed, modeled, *, tolerance, metrics=DEFAULT_METRICS)`** works on one already-masked sample.
  - It returns `{metric_id: MetricResult(value, reason)}`.
  - Non-finite values raise; they are never dropped.
- **`score_comparison(cohort, metric_spec, *, products, period_totals=())`** returns an immutable `MetricResults`:
  - `table` (`metrics` v2), `cumulative` (`cumulative_error` v1), `report`, and `wide()` (one column per metric, for reading).
- **`cumulative_error(cohort)`** returns running sums over cohort days with missing-period flags.
- **Supporting definitions:** `METRIC_DEFINITIONS`, `definitions_frame()` and `output_metrics()` (which expands `kge_2009` into its components).
- **Config:**
  - New `MetricSpec` and `NearZeroTolerance`, in a new `metric_sets:` YAML section.
  - Fields: `metrics`, a mandatory `daily_tolerance`, `period_tolerance`, `period_aggregations`, `period_products`, `period_types`, `comparisons`, `daily` and `cumulative_error`.
  - Constants `DEFAULT_METRICS`, `OPTIONAL_METRICS` and `METRIC_IDS`; `TOTAL_PRODUCTS` moved to `config` and is re-exported by `schemas`.
  - The loader checks that referenced comparisons and aggregations exist, and that `estimated_total` is requested only where an estimator is declared.

### Delivered
- **[src/etcompare/metrics.py](src/etcompare/metrics.py)** computes:
  - by default: MBE, MAE, RMSE, bias-corrected RMSE, percent bias, NRMSE (`nrmse_mean`, a fraction), Pearson r and `pearson_r_squared`, and the OLS slope and intercept of P on O;
  - when listed: `nrmse_mean_percent`, NSE, the original KGE (`kge_2009` plus `_r`, `_alpha` and `_beta` components) and MAPE.
  - Exact formulas, units and roles are in the module docstring, `metric_definitions.csv` and D-044.
- **Roles** keep diagnostics out of rankings: bias-corrected RMSE and MAPE are `diagnostic`, OLS coefficients are `descriptive`, and KGE parts are `component`.
- **Undefined metrics** are null with a reason: `empty_cohort`, `no_available_periods`, `singleton_sample`, `constant_observed`, `constant_modeled`, `near_zero_observed_mean` or `near_zero_observation`.
- **Samples:**
  - Daily pairs and each period product (`partial_sum`, `complete_total`, `estimated_total`) are separate rows, labelled by `temporal_scale`, `period_type`, `aggregation_id` and `product`.
  - The common mask is verified across models, sides and variants.
  - Period totals must carry the cohort's fingerprint.
  - Mixed temporal support inside a ranking cohort raises an error.
- **Schemas:** `metrics` v1 → v2 adds the keys `metric_set_id`, `period_type`, `aggregation_id`, `product` and `footprint_set_id`, plus `valid`, `metric_role`, `ranking_scope`, `n_days_reference_filled`, `n_days_imputed`, `temporal_support` and `flags`. A new `cumulative_error` v1 schema was added. Nothing had written v1.
- **Example config:** the `utj_metrics` set uses the default metrics plus `nrmse_mean_percent`, NSE and KGE 2009. It scores daily pairs and all three products of `utj_periods`. Tolerances are daily `{mean 0.01, std 0.001}` mm d⁻¹ and period `{mean 0.1, std 0.01}` mm (Provisional, D-044).
- **[tools/run_cohorts.py](tools/run_cohorts.py)** also writes:
  - `<id>__utj_metrics_metrics.{parquet,csv}`, `_metrics_wide.csv` and `_cumulative_error.{parquet,csv}`;
  - `metric_definitions.csv`.
  - A new flag, `--skip-missing-crop-height`, leaves out only the growing-season periods when the crop file is absent, and the reports say so. Without the flag the run still stops, as before.
- **Docs:** D-044 (rules) and D-045 (US-UTJ run); a contract §9 row for the mandatory tolerances.

### Checks
- **[tests/test_metrics.py](tests/test_metrics.py)**, 40 tests; expected values come from the guide and from numpy/scipy computed in the tests only:
  - **Guide fixture:** O=[1,2,3], P=[2,3,4] gives MBE = MAE = RMSE = 1, bias-corrected RMSE = 0, slope = 1, intercept = 1, r² = 1 and percent bias = 50. It is also cross-checked with `scipy.stats.linregress`.
  - **Random samples (3 seeds):** every metric matches numpy/scipy (`pearsonr`, `linregress`, `polyfit`, explicit NSE and KGE) to 1e-10 relative, and RMSE² = MBE² + bias-corrected RMSE².
  - **Undefined cases:**
    - Constant observed and modeled series; with a constant P, the slope is 0 and the intercept equals P.
    - An empty sample and a singleton.
    - A near-zero observed total (percent bias, NRMSE and KGE null; MAPE `near_zero_observation`).
    - Both tolerances change the outcome when changed.
    - NaN raises.
  - **Labels:** a biased, perfectly correlated model has r² = 1 and negative NSE, and no metric is called `r2`. Bias-corrected RMSE and MAPE are diagnostics; MAPE, NSE and KGE are not defaults; KGE components cannot be requested alone.
  - **Cohorts:**
    - Two models in a common-date cohort are scored on the same 57 frozen days.
    - An empty cohort gives null rows (`empty_cohort`).
    - Paired methods give separate rows, with a day dropped on both sides.
    - Mixed or undeclared temporal support raises, and an unequal mask raises.
    - Flags carry through, and the tables round-trip through Parquet.
  - **Periods:**
    - Partial, complete and estimated monthly products are scored separately and match numpy on the `period_totals` rows (`n_periods`, `n_days`, `n_days_imputed`, completeness).
    - A product with no available period gives `no_available_periods`.
    - Totals from another cohort are refused by fingerprint.
    - `period_types` filtering works, and an unsupplied aggregation is reported.
  - **Cumulative error:** gaps are flagged, segments increment and nothing is filled.
  - **Config:** 13 rejection cases.
- **Oldest dependency stack** (Python 3.11, lowest-direct, `FutureWarning` as error): metrics, aggregation, cohorts, schemas and config tests pass (138).
- **US-UTJ run (D-045):**
  - Daily cohort sizes reproduce D-040, and complete and estimated month counts reproduce D-043.
  - An independent numpy/scipy recomputation from the written paired and period-total files deviates at most 3.6e-15.
  - No metric is null because of a tolerance.

### Not run here (resolved 2026-10-16)
- **Crop-height file:** it was absent on 2026-10-15, so the growing-season periods were skipped. The user restored `US-UTJ_20240101_20251230.csv` at the repository root (SHA-256 matches D-042).
  - Both crop-height tests now run and pass: `test_us_utj_crop_height.py` and `test_us_utj.py::test_set_a_period_totals`. The suite gives 261 passed and 3 skipped before step 10; the remaining skips are the opt-in Drive raster tests.
  - `tools/run_cohorts.py examples/us_utj.yaml` was rerun **without** `--skip-missing-crop-height`.
  - **Growing-season period:** GS2024 = 2024-03-15..12-06 and GS2025 = 2025-03-15..12-06 (267 days each). Both extend past the record, so only matched-day partial sums exist. There is no complete or estimated season total in any cohort (D-046).
  - **GS2025 partial sums, inclusive ranking** (208 of 267 days matched, 86 gap-filled): observed 805.9 mm; modeled 571.3 to 961.3 mm; percent bias −29.1 % to +19.3 %. One-period samples give `singleton_sample` for the spread-based metrics.
  - **Independent checks:** sums and metrics recomputed with numpy from the paired files and the crop windows match exactly. The daily metrics reproduce D-045.

### Assumptions and limitations
- **Tolerances are provisional.** They were set before scoring and decide only when a denominator counts as zero; the user should confirm or replace them.
- **Station-level only.** Network summaries, weighting and contrasts are step 11. No intervals are given (step 10). The metric values are point estimates, not rankings.
- **Partial-sum metrics are descriptive.** They mix periods with unequal matched days and are labelled with `n_days`/`n_periods`.
- **One-period products are singletons.** Yearly partial sums and a single complete month give `singleton_sample` for spread-based metrics.
- **`utj_coverage` is scored per model on unequal days.** Its rows carry `ranking_scope=none` and must not be ranked.
- **Every US-UTJ row is flagged `day_alignment=unverified`** (OpenET day boundary undeclared).
- **The 93 gap-filled reference days** of the inclusive cohort share gridMET drivers (D-005) and are counted in `n_days_reference_filled`.

**Next (at the time):** step 10. Superseded by the Step 10 entry above.

## Steps 5–6 outputs regenerated; all cohorts and period totals (2026-10-15)

**Run:** the step 5–6 commands (footprint-parameter fetch, per-timestep footprints, aggregation, four raster extractions, two sensitivity comparisons), then `tools/run_cohorts.py examples/us_utj.yaml`. The rebuild took about 1 h, mostly the first set-B `etw` extraction (38 min over the Drive); re-extractions run in about 1 min once Drive files are cached.

- **Environment:** the footprints environment lacked its declared `pyarrow`. Version 26.0.0 was installed with `--link-mode=copy` because OneDrive refuses uv's hard links. The footprints repository's code was not changed.
- **Corrupted footprint files:** the first rebuild left 3 corrupted footprint GeoTIFFs in each etcompare-built set, with correct in-memory arrays. `write_footprint` now verifies every write by reading the file back (new test). The rewrite is clean, scanned 474 + 391 files. Details in D-043.
- **Reproduction:** every figure in D-035 to D-040 is reproduced exactly. The D-037 timestep count was a typo and has been corrected to 10,795.
- **Period totals:** all 7 comparisons produce period totals plus daily estimates (`outputs/cohorts/*__utj_periods_*`). Which months are complete or estimated is in D-043.
  - The 7-model inclusive ranking has complete totals for 2025-01 to 07 and 09, and estimated totals for 2025-01 to 09.
  - Measured-reference cohorts have no estimates for April–August 2025, because the raw station gaps are longer than 14 days.
- **Tests:** 220 passed, 3 skipped (opt-in Drive and raster-indexing tests); ruff and mypy are clean.

## Step 8 addendum — imputed estimated totals, crop-height growing season (2026-10-15)

**User decisions:** estimated totals are wanted; missing values are imputed into a new column; the growing season comes from the crop-height model (`US-UTJ_20240101_20251230.csv`, added by the user).

**Run it**
```bash
uv run pytest                                     # 213 passed, 9 skipped (US-UTJ daily/Parquet absent, opt-in Drive)
uv run ruff check src tests && uv run mypy        # clean
uv run python tools/run_cohorts.py examples/us_utj.yaml   # also writes *_daily_estimates.{parquet,csv}
```

### Delivered
- **[src/etcompare/adapters/crop_height.py](src/etcompare/adapters/crop_height.py):** `adapt_crop_height` produces the new `crop_height` schema, with height in cm, `season_start`/`season_end`, `in_season`, and source columns as `src_*`. It refuses inconsistent seasons. `growing_season_windows` gives `GS<year>` windows.
- **Imputation** in [aggregation.py](src/etcompare/aggregation.py):
  - Methods `linear_interpolation` and `reference_et_fraction`. Imputation is paired over cohort anchors, limited to gaps bracketed by anchors and no longer than `estimation_max_gap_days`, and never extrapolates.
  - `PeriodTotals.daily_estimates` (new `daily_estimates` schema) keeps the original values and puts imputed values only in `*_estimated_mm`, with `value_source` and `gap_length_days`.
  - `estimated_total` rows sum it and carry `n_days_imputed`. A total is withheld as `imputation_incomplete` when any day stays unfilled.
- **Config:**
  - `crop_height_tables:` section.
  - `PeriodAggregationSpec.growing_season_source`, `estimation_max_gap_days`, and `with_growing_seasons()`.
  - `StationTableSpec.reference_et_column`, whose unit must be declared. The station adapter now emits `reference_et_mm`.
- **Example config:** growing season from `utj_zone_e_crop_height`; `reference_et_fraction` on gridMET ETr; max gap 14 days and min completeness 0.5 (adopted defaults, user 2026-10-15; D-042).
- **Runner:** reads the crop table, passes reference ET, and writes the daily estimates.
- **`.gitignore`:** now ignores the crop-height CSV, which is private data (D-014).
- **Docs:** D-042; two new rows in contract §9.

### Checks
- **Synthetic tests** ([tests/test_imputation.py](tests/test_imputation.py)):
  - Linear imputation is exact on linear data; imputed values are in the new columns and the original columns stay null.
  - Paired imputation ignores a real station value outside the cohort, and frozen models share imputed days.
  - Gap limit: a 4-day gap is refused at max 3 and filled at max 4. Nothing is extrapolated before the first anchor.
  - Anchors in the neighbouring month bracket an edge gap.
  - Minimum completeness is checked before imputation.
  - `reference_et_fraction` reproduces `0.5·ETr` and `0.8·ETr` exactly. An anchor with ETr = 0 is not used; a day without ETr stays unfilled.
  - Daily estimates round-trip through Parquet.
  - Spec validation, the station `reference_et_column` unit requirement, and crop-height season windows (the end-to-end GS2025 total is 256 days) are covered, along with the three rejection cases and the config section.
- **Real crop file** ([tests/integration/test_us_utj_crop_height.py](tests/integration/test_us_utj_crop_height.py), runs here):
  - SHA-256 is pinned; 730 rows.
  - Windows are GS2024 and GS2025, both 03-15..12-06.
  - 534 in-season days; height outside the season is always 5 cm.
- **Runner smoke test** (synthetic station and model data plus the real crop file, scratch only):
  - Two missing SIMS days are imputed in June for every model.
  - A 30-day station hole is refused (`imputation_incomplete`), and August is below minimum completeness.
  - GS2025 has 267 expected days.

### US-UTJ real data (2026-10-15; set-A pre-extracted cohort, local outputs)
The user added `US-UTJ_daily.csv` and `station_oet_US-UTJ.parquet`; both SHA-256 values match PROJECT_CONTEXT. The integration tests now run: 7 passed and 3 skipped (opt-in Drive and raster tests).
- **Cohort:** `tools/run_cohorts.py examples/us_utj.yaml utj_set_a_common` gives 260 common days, 88 of them gap-filled (unchanged).
- **Imputation:** within the 2025 record (Jan 1 – Oct 9) the cohort has 260 real days, and all 20 missing days fall in gaps of 1–3 days, so every one was imputed. The 14-day limit is not binding for this cohort. Jan 1–2 stay unfilled because no cohort day precedes them (the 2025-only models start Jan 3), and nothing is extrapolated.
- **Estimated monthly totals exist for Feb–Sep 2025.** January is `imputation_incomplete` (Jan 1–2). October has 8 of 31 days, below the 0.5 minimum. 2024 has no matched days.
- **No complete or estimated total for GS2025, 2025, or WY2025:** the record ends on 2025-10-09, before the season end (12-06) and the year end. These remain partial sums only, e.g. GS2025 has 196 of 267 days matched.
- **Raster-extraction cohorts:** run after regenerating the step 5–6 outputs; see the entry below.

### Open (user decisions)
- **Imputation driver:** gridMET ETr was chosen to match the station's own fill. Switch to `gridMET_ETo` or `linear_interpolation` if preferred.
- **Cutting cycles:** these could become a period type (cutting dates are visible as height resets); not implemented.

## Step 8 — Temporal aggregation and completeness (2026-10-15)

**Run it**
```bash
uv sync --extra geo && uv run pytest              # 194 passed, 9 skipped (US-UTJ files absent, opt-in Drive)
uv run ruff check src tests && uv run mypy        # clean
uv run python tools/run_cohorts.py examples/us_utj.yaml   # cohorts + period totals into outputs/cohorts/
```

### Delivered
- **[src/etcompare/aggregation.py](src/etcompare/aggregation.py):** `aggregate_cohort(cohort, spec, products=)` returns an immutable `PeriodTotals`. It holds a `period_totals` table, a validation report, `summary()` (periods, expected and matched days, complete periods), and attributes with the cohort fingerprint. `period_calendar` builds the calendar.
- **Three products, one row each** (rules in D-041):
  - `partial_sum`: matched days only, never scaled.
  - `complete_total`: only when every calendar day of the period is a cohort day and support is `interpolated_daily`.
  - `estimated_total`: opt-in `mean_daily_scaling`, which needs a declared minimum completeness.
  - Null products carry a `validity_reason`: `incomplete_period`, `no_matched_days`, `below_min_completeness` or `temporal_support_<value>`.
- **Periods:** `day`, `month`, `year`, `water_year` (start month configurable, labelled by end year) and `growing_season`. Growing seasons come from site/year windows or a month-day rule, can cross the year boundary, and have no default.
- **Counts per row:** expected, matched, matched gap-filled, model-available (before cohort rules), reference measured and filled, plus completeness. Daily flags carry over, and `period_outside_inputs` marks periods that extend beyond the inputs.
- **Cohort:** `Cohort.model_availability()` / `reference_availability()` record per-day availability before cohort rules. Aggregation needs only the cohort.
- **Config:** `PeriodAggregationSpec` (with `GrowingSeasonRule` and `GrowingSeasonWindow`) in a new `period_aggregations:` YAML section. Constants: `PERIOD_TYPES`, `ESTIMATION_METHODS`, `ET_TOTAL_UNIT = "mm"`. The loader checks referenced comparisons and sites.
- **Schema:** new `period_totals` v1, with totals in `mm` (not `mm d-1`). Named `observed_total_mm`/`modeled_total_mm` so they cannot be confused with daily columns.
- **Example config:** `utj_periods` covers month, calendar year and water year for all comparisons. No growing season and no estimator are declared.
- **[tools/run_cohorts.py](tools/run_cohorts.py):** now also writes `<id>__<agg>_periods.{parquet,csv}` and `_summary.csv`.
- **Docs:** D-041. Contract §9 now states the temporal-support rule for period totals.

### Gate checks (guide step 8), in [tests/test_aggregation.py](tests/test_aggregation.py)
- **The gate:** a 31-day month with 30 valid days at 2 mm/day gives a partial sum of 60 mm, and the complete total is null (`incomplete_period`). A fully covered month gives 62 mm.
- **A day missing from both station and model** still counts as expected (31 expected, 30 matched), so the month is not complete.
- **Sums, not means:** random daily values sum to 1e-12. The unit is `mm`.
- **Calendar:**
  - Leap days: Feb 2024 has 29 days, 2024 has 366, WY2024 has 366.
  - Water-year labels (09-30 → WY2024, 10-01 → WY2025); a January start equals the calendar year.
  - A cross-year rule gives GS2023-2024 = 152 days and GS2024-2025 = 151.
  - Days outside a site's growing-season window are excluded; a window for another site produces nothing.
- **References:** an `et_corrected_inclusive` month with 3 gap-filled days is complete (62 mm, 3 filled counted). In the paired-reference cohort the same month is incomplete, with 28 measured days on both variants.
- **Freezing:** in a common-date cohort, one model's missing day makes the month incomplete for every model, while availability is reported before freezing (31 vs 30). Paired methods aggregate over identical days.
- **Temporal support:** `overpass_only` and `unknown` withhold complete and estimated totals and keep partial sums. An undeclared product raises; mixed support warns.
- **Estimation:** scales both sides by 31/28 at ≥ 0.9 completeness, is null below its threshold, equals the complete total when the period is complete, and never appears unless declared.
- **Export and immutability:** Parquet round trip with the fingerprint, flag propagation, `summary()`, copy-on-access, the zero-matched-day denominator, the identity at daily scale, and spec/config validation errors.
- **Pandas versions:** passes on the oldest dependency stack (Python 3.11, lowest direct) with `FutureWarning` as an error.
- **Runner smoke test** on synthetic US-UTJ-shaped inputs (scratch only): Dec–Mar gives 4 months. One missing SIMS day leaves 3 complete months for all 7 models; WY2025 has 120 of 365 days.

### Not run here (resolved later on 2026-10-15: the inputs were supplied, the integration tests ran and the full runner was run; see the entries above)
- **US-UTJ real-data run:** `US-UTJ_daily.csv`, `station_oet_US-UTJ.parquet` and the earlier `outputs/` are not in this git checkout, so the integration tests skip. A new integration test, `test_set_a_period_totals`, is ready. It checks that months, years and water years each partition the 260 common days (88 gap-filled), that yearly sums equal daily sums, and that neither calendar year is complete. Run it with `ETCOMPARE_DATA_DIR=<folder with both files> uv run pytest tests/integration`. Then run `tools/run_cohorts.py` where the step-5/6 extraction outputs exist.

### Open (user decisions)
- **US-UTJ growing season:** not declared. It needs site/year metadata (e.g. alfalfa green-up and cutting dates) or an explicit rule.
- **Estimated totals:** no estimator is adopted. If one is wanted, the method and its minimum completeness must be declared before scoring.
- Carried over: the quality threshold (D-008) and the measured-LE coverage minimum (D-038).

### Limitations
- **Monthly-raster extraction with monthly footprints** is not implemented. It would be a distinct extraction method; period totals here are sums of daily extractions only.
- **`mean_daily_scaling` is the only estimator.** It assumes the missing days resemble the matched days, which gap patterns (cloudy, wet, or winter days) may violate. It is documented as an estimate.
- **Availability counts are limited to the comparison.** They cover its sites and declared `start`/`end`, so days outside the window count as expected but unavailable, and those rows are flagged `period_outside_inputs`.

**Next:** step 9, transparent performance metrics on cohort paired rows (daily) and on `period_totals` (by product, never mixing partial and complete).

## Step 7 — Comparison cohorts (2026-10-14)

**Run it**
```bash
uv run pytest                                     # 176 passed, 3 skipped (opt-in)
uv run ruff check src tests && uv run mypy        # clean
uv run python tools/run_cohorts.py examples/us_utj.yaml   # all 7 declared cohorts, ≈5 s
```

### Delivered
- **[src/etcompare/cohorts.py](src/etcompare/cohorts.py):** `build_cohort(observations, extractions, spec, footprint_sets=)` returns an immutable `Cohort`. It holds:
  - `paired` rows (`paired` schema) for every kept unit × side × reference variant, with no missing values;
  - `membership`: one row per candidate site/date/model, with `included`, `reason_code` and `detail`;
  - exclusions, a validation report, `summary()` (days per cohort and model, with gap-filled reference days counted apart), `units()`, and a `fingerprint` (SHA-256 of the included units).
- **Config:** `ComparisonSpec` and `ExtractionSide`, in a new `comparisons:` YAML section. Kinds: `common_date`, `pairwise_model`, `model_available`, `paired_method` and `paired_reference`, with an optional `common_models`, `min_retained_frac`, `sites` and `start`/`end`. The loader checks that models, footprint sets, sources and sites are declared. Rules are in D-039.
- **Schema:** new `cohort_membership` v1. `REFERENCE_VARIANTS` moved to `config` (re-exported by `schemas`).
- **Example config:** 7 US-UTJ comparisons: two all-model rankings, closure sensitivity, coverage, footprint vs 3×3 window, one pairwise cohort, and a set-A regression cohort.
- **[tools/run_cohorts.py](tools/run_cohorts.py):** writes paired rows, membership, summary, exclusions and a report into `outputs/cohorts/`. It is a stand-in until the step-17 CLI.

### Gate checks (guide step 7), in [tests/test_cohorts.py](tests/test_cohorts.py)
- **Difficult days:** a model missing them gains no advantage. Per-model cohorts give MAE 0.66 vs 0.10. The common-date cohort scores both models on the same 8 days (MAE 0.10 each), and the other model's difficult days are recorded as `not_all_models`. (MAE is computed in the test; metrics are step 9.)
- **Paired methods:** a date missing from one method is removed from both sides of that model. With `common_models` it is removed from every model.
- **Export:** every candidate has a decision; excluded units have a reason code and detail. Membership and paired tables round-trip through Parquet, and the fingerprint travels in the attributes.
- **Also tested:**
  - `paired_reference` uses measured days only.
  - A pairwise cohort has more days than the all-model cohort and a different `ranking_scope`.
  - `model_available` gives per-model cohorts.
  - The retained-weight threshold applies uniformly and never to `none` sides; an unknown fraction is a failure.
  - Duplicated extraction or station keys raise before joins.
  - Flags: unverified day alignment, and target dependence (`yes` / `unknown` / absent).
  - Immutability (copies, frozen dataclass, read-only attributes) and fingerprint stability.
  - Period filtering and the empty-cohort status.
  - Spec validation errors.
  - Equivalence with `compare_footprint_sets` paired rows.
- **Pandas versions:** tested on the oldest dependency stack with `FutureWarning` as an error.
- **Integration:** the set-A pre-extracted cohort reproduces 260 common dates with 88 gap-filled (PROJECT_CONTEXT §5).

### US-UTJ cohorts (local outputs, git-ignored; details in D-040)
- **All-model rankings:** 281 days with `et_corrected_inclusive` (93 of them gap-filled); 188 with `et_corrected_measured`. These are all in 2025.
- **Closure sensitivity:** 188 days.
- **Footprint vs 3×3 window:** 303 measured days for the long-record models, 188 for the 2025-only models.
- **Coverage:** 472, 282 and 281 days.
- No target-dependence warnings: both primary sets are reference-ET weighted.

### Open (user decisions before scoring)
- **Quality threshold:** no `min_retained_frac` is adopted (D-008). SIMS retained weight falls to 0.19 on the cropland mask. Any threshold must be declared before step 9 and applies to every model.
- **Measured-LE coverage minimum:** still undecided (D-038).

### Limitations
- **Daily rows only:** monthly cohorts belong to step 8 (aggregation from daily cohorts).
- **No acquisition-based eligibility:** none is available (D-011).
- **Separate code paths:** `compare_footprint_sets` still has its own frozen-key code. It is shown equivalent by test but not refactored onto `build_cohort`.

**Next:** step 8, temporal aggregation and completeness built on cohorts.

## Step 6 addendum — NLDAS-ETo primary, measured-LE sensitivity (2026-10-14)

**User decision:** rely on NLDAS weights and also provide measured-LE weights. The user also chose the inputs: database heights (read-only), the production 08–20 h window, and per-timestep renormalization.

**Run it**
```bash
F=../footprints/.venv/Scripts/python.exe        # footprints repo environment (upstream)
Q="M:/Shared drives/UGS_Flux/Data_Processing"
$F tools/upstream/fetch_footprint_parameters.py US-UTJ outputs/footprint_inputs      # read-only SELECTs
$F tools/upstream/run_timestep_footprints.py --site US-UTJ --lat 37.281746 --lon -109.53489 \
   --metflux "$Q/final_database_tables/database_exports_20260825/US-UTJ_metflux_qc.parquet" \
   --params outputs/footprint_inputs/US-UTJ_daily_footprint_parameters.parquet \
   --nldas "$Q/final_processing_workflow/footprints/nldas/nldas_results/US-UTJ_nldas.csv" \
   --out outputs/footprint_timesteps/US-UTJ                                          # ≈1 min
uv run python tools/run_footprint_aggregation.py examples/us_utj.yaml US-UTJ \
   outputs/footprint_timesteps/US-UTJ --compare-sets US-UTJ_set_A_20261005 US-UTJ_set_B_etw_20261012
uv run python tools/run_raster_extraction.py examples/us_utj.yaml us_utj_nldas_eto       # ≈3.5 min
uv run python tools/run_raster_extraction.py examples/us_utj.yaml us_utj_measured_le     # ≈3 min
uv run python tools/run_footprint_sensitivity.py examples/us_utj.yaml us_utj_nldas_eto \
   us_utj_measured_le --out us_utj_nldas_vs_measured_le
```

### Delivered
- **[tools/upstream/](tools/upstream/):** these scripts run with the footprints repo environment, outside the etcompare core.
  - `fetch_footprint_parameters.py`: read-only queries of the heights and the tower location.
  - `run_timestep_footprints.py`: saves every half-hour footprint from fluxfootprints `ffp_xr`, using the production inputs, as monthly `.npz` stacks with full provenance (input SHA-256 and fluxfootprints file hashes).
- **[src/etcompare/adapters/fluxfootprints.py](src/etcompare/adapters/fluxfootprints.py):** `load_timestep_stack`.
- **Aggregation:** `combine_results` in [footprint_aggregation.py](src/etcompare/footprint_aggregation.py).
- **Config:**
  - New `footprint_aggregations` YAML section, so aggregation specs are now declared in config.
  - `TemporalAggregationSpec.weight_column`.
  - New footprint sets `US-UTJ_nldas_eto_20261014` (primary) and `US-UTJ_measured_le_20261014`.
  - New raster extractions `us_utj_nldas_eto` (all methods) and `us_utj_measured_le`.
  - Station coordinates now come from the database (37.281746, −109.53489).
- **[tools/run_footprint_aggregation.py](tools/run_footprint_aggregation.py):** writes the declared sets, checks the written semantics against the config, and measures distance to existing sets.
- **Fix:** `compare_footprint_sets` now checks keys on the compared rows only, so footprint-free pixel rows repeated across runs no longer collide.

### Findings
- **The existing `etw` sets are NLDAS-grass-ETo weighted with negative ETo kept** (D-036). The reproduction matches set A to a median 0.2 % (maximum 0.7 %) relative L1. Negative weights reappear on all 43 days, and on set B's 3 round-off days. This resolves D-009 and D-015 q2.
- **"NLDAS ET"** in fluxfootprints means FAO-56 grass ETo computed from NLDAS-2 forcing, not land-model ET.
- **Station time** is fixed UTC−7: the SW_IN centroid falls at 12.1–12.6 h. NLDAS radiation sits a steady ≈0.5 h later; its hour-labelling convention is unconfirmed.
- **Coverage:** NLDAS 474 days; measured LE 391 days (83 days have no LE).
- **Comparisons** (D-038):
  - NLDAS − set B `etw`: ≤ 0.005 mm/day.
  - Measured LE − NLDAS: per-model mean +0.002 to +0.015 mm/day, 95th-percentile |difference| ≤ 0.15. The largest differences fall on days with few LE half-hours.

### Checks
- 151 tests pass (3 opt-in skipped), including synthetic tests of `combine_results`, the stack loader and the `weight_column` rules.
- ruff and mypy are clean.
- The opt-in Drive raster tests pass.

### Open
- **Minimum temporal coverage for the LE set:** none is declared. The user needs to choose one before scoring (D-038).
- **NLDAS hour-label convention:** a ≈0.5 h ambiguity.
- **Other stations:** the per-timestep stacks (455 MB) are local outputs; they are not generated for other stations.

## Step 6 — 2026-10-14

**Run the checks**
```bash
uv sync --extra geo && uv run pytest                    # 149 passed, 3 skipped (opt-in Drive tests)
uv run ruff check src tests && uv run mypy              # clean
uv run python tools/run_raster_extraction.py examples/us_utj.yaml us_utj_set_b_mean   # ≈4 min
uv run python tools/run_footprint_sensitivity.py examples/us_utj.yaml \
    us_utj_set_b_etw us_utj_set_b_mean --out us_utj_set_b_etw_vs_mean
```

### Delivered
- **[src/etcompare/footprint_aggregation.py](src/etcompare/footprint_aggregation.py)** builds period footprints from per-timestep footprints with `W_i = Σ a_t f_it / Σ a_t` (D-033).
  - Every choice is declared in `TemporalAggregationSpec` (in `config`): the weighting, `include_duration`, the original-contribution or per-timestep-renormalized basis, and the negative-weight, gap and night policies, plus an optional minimum coverage.
  - Each period reports eligible counts, exclusions by reason, `temporal_coverage_frac` (denominator: period duration) and, on the original basis, domain capture.
  - Missing stacks or weights return `not_available`.
  - `write_footprint_set` writes the aggregates as a regular footprint set (GeoTIFFs, a capture CSV and a JSON provenance file), so step-5 discovery and extraction consume them unchanged.
- **[src/etcompare/footprint_sensitivity.py](src/etcompare/footprint_sensitivity.py)**: `compare_footprint_sets` builds the frozen-key comparison (D-034).
  - Per-variant cohorts plus a model-only cohort.
  - An optional uniform `min_retained_frac` and `common_models`.
  - Outputs: `paired` rows, set-minus-baseline differences, pre-freeze availability, and an exclusion for every dropped key.
  - Measured- or unknown-weighted sets give a warning and the row flag `footprint_target_dependence=`.
- **[src/etcompare/references.py](src/etcompare/references.py)**: `reference_values` applies the D-004 variant rules. Step 7 will reuse it.
- **Config:** `FootprintSetSpec.temporal_weighting` is a controlled field with derived `target_dependence` (D-032). The example declares set B `mean` = `time_mean`, the `etw` sets = `unknown`, and a second raster extraction `us_utj_set_b_mean`.
- **Schema:** `paired` v2 adds the key `footprint_set_id`; nothing used v1.
- **I/O:** `io.rasters.write_footprint` writes float64 GeoTIFFs atomically.
- **Tools:** [tools/run_footprint_sensitivity.py](tools/run_footprint_sensitivity.py) is a stand-in until the step-17 CLI.

### Gate checks (guide step 6)
In [tests/test_footprint_aggregation.py](tests/test_footprint_aggregation.py) and [tests/test_footprint_sensitivity.py](tests/test_footprint_sensitivity.py); expected values come from direct numpy:
- **Duration weighting:** equal temporal weights with unequal durations reproduce `Σ d_t f_t / Σ d_t` to 1e-14. Without durations the result is the plain mean.
- **Scale invariance:** multiplying all weights by 7.3 leaves W unchanged (1e-14). Series weights are matched by stamp, not by position.
- **Missing inputs:** no stack, or no weights for a weighted method, gives `not_available` with a reason, and nothing is built.
- **Basis:** the renormalized basis differs from the original basis and drops capture. Original-basis capture equals `Σ a cap / Σ a`. Normalized per-timestep input with the original basis is rejected.
- **Policies:**
  - Negative weights are excluded, rejected, or kept with negative aggregate cells counted.
  - Gap exclusion or rejection works as declared.
  - Night exclusion needs a flag.
  - End-labelled midnight stamps go to the previous day.
  - Coverage thresholds apply, with monthly denominators.
- **Upstream reproduction:** fluxfootprints `summarize_periods` `mean` and `etw` formulas are reproduced to 1e-13.
- **Round trip:** the aggregates are written, rediscovered and read back bit-identical, with capture attached.
- **Frozen dates:**
  - A date missing in one set is removed from both.
  - Cohorts per variant keep each observation paired; filled days appear only in the inclusive variant.
  - The retained threshold drops a day on both sides.
  - The `common_models` rule applies.
  - Target-dependence flags are set.
  - A set with no data is skipped as `not_available`.

### US-UTJ results (set B, local outputs, git-ignored)
- **`mean` extraction:**
  - 474 footprint days (18 with no weight, 0 negative); 2,549 rows per footprint method; 0 errors.
  - Retained weight is 1.000 except SIMS (minimum 0.192) and DisALEXI (minimum 0.997).
  - Domain capture 0.873–1.042.
- **Comparison with `etw`:**
  - 4,706 of 5,098 keys are frozen. Every key is an `etw` key; the 40 days on which `etw` was rejected for negative weights are dropped from `mean` too.
  - Cohorts: 434 days with no reference restriction, 276 for `et_raw` and `et_corrected_measured`, and 432 for `et_corrected_inclusive`.
  - `mean` − `etw`: mean differences −0.046 to −0.001 mm/day; 95th-percentile |difference| ≤ 0.24; maximum 0.48 (D-035).
  - Descriptive only, and flagged as possibly target-dependent (D-032).

### Limitations
- **Per-timestep aggregation is untested on real data:** no per-timestep footprints or weight series exist for US-UTJ. Re-weighting the daily rasters is refused by design.
- **`etw` weighting is unknown:** which ET variable it used (measured LE or NLDAS) is unrecorded, so its target dependence is `unknown`.
- **`temporal_coverage_frac` is still null in footprint manifests:** the domain CSV's `valid_timesteps` (7–26 per day) has no declared timestep length or expected count.
- **No YAML block for aggregation specs yet:** they are Python objects until the step-17 CLI.
- **No station-agreement metrics:** these are step 9. `paired` rows are ready for steps 7–10.

**Next:** step 7 (explicit comparison cohorts). Generalize the frozen-key logic into `build_cohort` and an immutable cohort object.

## Step 5 — 2026-10-13

**D-016 decided by the user: footprint set B.** Its `etw` layer is the primary footprint; its `mean` layer is declared for step 6.

**Run the checks**
```bash
uv sync --extra geo && uv run pytest                    # 128 passed, 1 skipped
ETCOMPARE_RASTER_TESTS=1 uv run pytest tests/integration # real Drive rasters (≈20 s)
uv run python tools/run_raster_extraction.py examples/us_utj.yaml us_utj_set_b_etw
```

### Delivered
- [src/etcompare/spatial.py](src/etcompare/spatial.py) — the transfer core:
  - `Grid`/`Window` types and conservative contribution transfer onto the native model grid (D-028). The exact path for aligned grids in the same CRS needs only numpy and scipy; other grid pairs use a shapely polygon-overlap path.
  - Weighted extraction, which tracks the retained, in-extent and valid-given-extent fractions with named denominators, and support IDs.
  - Pixel-block means.
- [src/etcompare/footprints.py](src/etcompare/footprints.py) prepares footprints:
  - NaN or nodata cells get zero weight.
  - `density_per_m2` values are converted to contributions using cell area (projected metre CRS required).
  - Negative weights are rejected beyond a round-off tolerance (D-027); zero-total footprints are rejected.
- [src/etcompare/io/rasters.py](src/etcompare/io/rasters.py) reads rasters in bounded windows with declared scaling. A header nodata that differs from the declaration is an error (D-026).
- [src/etcompare/extraction.py](src/etcompare/extraction.py) runs the per-station workflow:
  - Methods: `footprint_conservative`, `footprint_conservative_common_support`, `pixel_window_<n>x<n>` and `nearest_tower_pixel` (D-029).
  - Each footprint is read once per day and each raster once. Transfer matrices are cached per grid pair.
  - Unreadable files become counted exclusions (or raise in strict mode). Contract violations always raise.
- **Config:**
  - `ProductSpec.raster_scaling`/`scale_factor`/`add_offset`/`nodata`.
  - `FootprintSetSpec.domain_capture_*`.
  - `RasterExtractionSpec`, including a declared `start`/`end` period.
  - Top-level `x-` anchor keys are allowed.
- **Schema:** optional extraction columns `fp_frac_in_model_extent`, `fp_domain_capture_frac`, `n_cells_valid`, `model_grid_id`, plus footprint-manifest `domain_capture_source`. The set-B domain-capture CSV is attached by layer (D-030).
- **Tools:** [tools/profile_footprints.py](tools/profile_footprints.py) profiles footprint sets. [tools/run_raster_extraction.py](tools/run_raster_extraction.py) is a stand-in runner until the step-17 CLI.

### Gate checks (guide step 5)
- **Synthetic** ([tests/test_spatial.py](tests/test_spatial.py), [tests/test_extraction.py](tests/test_extraction.py)):
  - Uniform ET is unchanged under any weights.
  - ET `[2, 6]` with weights `[0.75, 0.25]` gives 3. Masking the second cell gives 2 with retained 0.75.
  - Transfer conserves total contribution.
  - Analytic cases pass: non-overlap, partial and shifted cells, a rotated grid.
  - The polygon path equals the exact path to 1e-12. UTM → EPSG:4326 agrees with point sampling.
  - Density conversion, differing model masks and the common support behave as specified.
  - Rejected for: negative weights, zero total, missing CRS, undeclared scaling, mismatched nodata.
  - Zero ET is valid, and nodata is never zero-filled.
  - The end-to-end GeoTIFF workflow matches independent 3×3 block sums to 1e-12.
- **Real data:** the set-A conservative method differs from the producer's bilinear `ET_fp` by ≤ 0.023 mm/day on 28 model-days (D-031).

### Full US-UTJ run (set B `etw`, 2024-06-05 to 2025-10-09, 34 min over the Drive mount)
- **Outputs:** `outputs/us_utj_set_b_etw_{extractions.parquet, exclusions.csv, report.txt, run.log}`. They are local and git-ignored.
- **Rows and errors:** 9,912 rows, 0 null values, 0 errors or warnings. The transfer is polygon overlap: all 15,625 footprint cells lie inside the model window, and row sums equal 1 to 12 decimals.
- **Footprint days:** 434 = 492 − 18 with no weight − 40 with negative weights. A further 3 round-off days were zeroed and reported.
- **Per-model footprint coverage:** 434 days for eemetric, ensemble and ssebop; 263 for disalexi, geesebal and ptjpl; 262 for SIMS.
- **Pixel methods:** 2,603 model-days.
- **Retained weight:** 1.000 for every model except SIMS (mean 0.782, minimum 0.206; cropland mask).
- **Domain capture** (from the CSV): minimum 0.859.
- **Compared with the Parquet (set A, bilinear; descriptive only):**
  - All 2,336 Parquet model-days are present.
  - Median per-model difference ≤ 0.014 mm/day; 95th percentile ≤ 0.043; maximum 0.105.
  - The 3 round-off dates exist only in set B.

### Limitations
- **Polygon area-weighted mode:** not implemented. No field polygons exist (contract §8), and it was left out rather than shipped untested. It is a planned step-5 extension.
- **Weighting method:** set B's is still unverified. Set B was not written by the current footprint code, since its `etw` files have negative weights and nodata 0 (D-009, D-027).
- **Domain-capture mapping:** code-inferred (D-030).
- **No retries or staging:** there are no I/O retries and no local staging of Drive files yet (step 18). A run reads about 2,600 rasters in about 34 min.
- **Common support varies by year:** it covers the models present each day (3 in 2024, 7 in 2025), and the `commonN:` support ID records N. Cross-model rankings must use step-7 cohorts.
- **Step 6 inputs:** the set-B `mean` layer is declared but not yet extracted.

**Next (at the time):** step 6. Superseded by the Step 6 entry above.

## Steps 2–4 — 2026-10-13

**Run the checks**
```bash
uv sync && uv run pytest                     # 100 passed, 1 skipped (Drive opt-in)
uv run ruff check src tests && uv run mypy   # clean
uv run --isolated --resolution lowest-direct --python 3.11 pytest   # oldest supported deps
```

### Step 2 — package skeleton
- `pyproject.toml` (hatchling build, `src` layout, `py.typed`), with `geo`/`plot` extras and a `dev` group. Dependency floors and the reasons for them are in D-018. `.gitignore` excludes private data (D-014). `README.md`.
- Infrastructure modules:
  - `errors` (`ContractError` and others)
  - `log` (structured events, JSON-lines formatter; the library never configures the root logger)
  - `validation` (`ValidationReport`)
  - `_optional` (deferred optional imports with an install hint)
  - `provenance` (sha256, package versions)
  - `registry` (models, site aliases)
- `config`: dataclasses for every mandatory declaration in contract §9, a strict YAML loader that rejects unknown keys, and error messages that name the config key. Example config: [examples/us_utj.yaml](examples/us_utj.yaml).
- **Gate verified:**
  - A subprocess import of every submodule, with sockets blocked and geo/plot packages blocked, succeeds and leaks no optional module.
  - The built wheel imports in an isolated environment.
  - Tests pass on the newest stack (pandas 3.0.6) and the oldest allowed one (pandas 2.2.0, numpy 1.26.0, pyarrow 14.0.0, Python 3.11).

### Step 3 — canonical tables
- [src/etcompare/schemas.py](src/etcompare/schemas.py) has 10 schemas: stations, station_daily, model_manifest, asset_sites, footprint_manifest, extractions, paired, metrics, intervals, exclusions.
  - Each schema declares column kinds, units, nullability, allowed values and a primary key. Counts are named (`n_sites`/`n_days`/`n_periods`); no table has a bare `n`.
  - `conform()` validates and coerces strictly. It rejects timezone-aware dates, times of day and type mismatches.
  - `check_primary_key()` runs before joins.
- [src/etcompare/io/tables.py](src/etcompare/io/tables.py) writes Parquet atomically. Metadata records the schema name and version, units, key and date semantics, plus provenance attributes. Dates are stored as `date32` (D-019).
- [src/etcompare/results.py](src/etcompare/results.py) provides `ExclusionLog` (stage, input key, reason code, severity) and `AdapterResult`.
- **Gate verified:**
  - Duplicate and null keys raise `DuplicateKeyError`.
  - Every schema round-trips through Parquet, as do the real US-UTJ tables, with identical frames. This covers nulls in every kind, identifiers with leading zeros, negative ET, extra columns, units and attributes.

### Step 4 — discovery and adapters
- [src/etcompare/adapters/filenames.py](src/etcompare/adapters/filenames.py) parses OpenET and footprint filenames.
  - OpenET names are anchored on `_<model>_<var>_YYYY_MM_DD_<hex>.tif` using the model registry. The prefix is parsed as an unordered set of site tokens, so explicit aliases containing `_` (e.g. `US_UTM`) work.
  - Rejected: invalid dates, unregistered models, wrong variable/suffix/extension, repeated sites. Each rejection has a specific message.
- [src/etcompare/io/manifest.py](src/etcompare/io/manifest.py) handles discovery and manifests.
  - Indexing uses `pathlib`; rasters are never opened (`scaling_source=not_read`). Each multi-site asset is one manifest row plus one `asset_sites` row per site.
  - Supports explicit manifests (CSV/Parquet) with an existence check. Footprint-set discovery is non-recursive by default (D-025).
  - Duplicate and conflicting assets raise an error, or are held out until `select_assets` records an explicit choice (D-021).
- [src/etcompare/adapters/station.py](src/etcompare/adapters/station.py): station daily table → `station_daily`.
  - Strict date, number and flag parsing. Reference provenance follows contract §4.3.
  - Inconsistent rows produce a warning plus an exclusion record. Reported as diagnostics: the closure relation, calendar gaps and negative values.
  - Source columns are kept as `src_*` (D-020).
- [src/etcompare/adapters/extracted.py](src/etcompare/adapters/extracted.py): pre-extracted table → long `extractions`.
  - Method labels are declared, and values are used as delivered.
  - Checks: model, site and date validity; key uniqueness; filename-versus-row consistency for both asset columns; the unity tolerance (warning); the retained-fraction range.
  - Sets `day_alignment` (D-023).
- **Gate verified (synthetic):** multi-site and single-site files, model and site aliases, malformed dates, duplicate assets in two folders, two assets for one site/model/day (error → report → explicit selection), non-recursive discovery, and the layer filter.

### Real-data verification (local only)
- [tests/integration/test_us_utj.py](tests/integration/test_us_utj.py) runs through `examples/us_utj.yaml`. It is skipped automatically when the files are absent.
- **Station:** reproduces sha256 and 492 rows. Provenance is 303 / 170 / 19 / 0, and `ET_corr = ET·ebc_cf` holds on 303/303. There are no calendar gaps, and all 44 source columns are preserved.
- **Extractions:** 4,672 long rows. Per-model coverage is 431×3 / 261×3 / 260. All 180 rows with retained weight < 0.90 are SIMS, the maximum |unity − 1| is ≤ 1.01e-8, and filenames are consistent in every row.
- **Across the two tables:** 61 station dates have no extraction; 260 dates are common to all models, of which 172 are `corrected_from_raw` and 88 `corrected_filled`.
- **Drive indexing (read-only listing, not a test):**
  - `OpenET_Daily_Tiffs/US-UTJ_US-CdM`: 4,747 rasters in 12 s, no parse failures or conflicts. Ranges: long-record models 2023-01-01–2025-12-31 (1,096 each); 2025-only models 365, SIMS 364.
  - Footprint set A: 492 daily + 17 monthly `etw` files, with set B excluded.
  - Whole `OpenET_Daily_Tiffs` root: 29,502 assets accepted. 1,617 conflicting site/model/day keys were held out in other site folders (D-021). 41,002 files were skipped as unparseable: `clipped/`, `archive_openet/`, and `castledale` (a region name, not a site ID).

### Limitations
- **flux-data-qaqc and fluxfootprints:** the adapters read *exported files* only. Direct object adapters, version-tested against a recorded upstream commit (guide step 4), are not implemented.
- **Raster headers** (CRS, scale, nodata) are not read. `io.rasters` and bounded raster access belong to step 5, so those manifest columns are null.
- **Footprint manifest** `domain_capture_frac` and `temporal_coverage_frac` are always null. The set-B domain-coverage CSV is not attached (contract §7).
- **No CLI yet** (step 17). Configuration is used through `load_config`.
- **Site tokens** must match the AmeriFlux pattern or an explicit alias, so region-named folders such as `castledale` need a separate mapping that has not been designed.
- **License and authors** are not set in `pyproject.toml`; this is the user's decision. The repository is not a git repo, so no commit was made.

**Next (at the time):** step 5. Superseded by the Step 5 entry above.

## Step 1 revision — 2026-10-13 (b)

The user supplied the timezone (UTC−7), `OpenET_Comparison.ipynb` (Parquet producer), the OpenET download notebook, and the footprint location `M:/My Drive/footprints`.

**Checks run**
- Recomputed `ET_fp`, `ET_mean` and `valid_weight_fraction` for ensemble 2024-06-06, geesebal 2025-09-12 and sims 2025-09-12 with the producer method on the set-A footprints and Drive rasters. All matched within 1e-13.
- Drive copy of the Parquet is byte-identical to the local file.
- Profiled all 492 set-A daily footprints. The 61 unextracted dates are 18 all-nodata and 43 negative-weight files, rejected by the producer.
- Compared footprint sets A (121×121, 2026-10-05) and B (`weighted/`, 125×125, 2026-10-12); the domain-coverage CSV belongs to set B.

**Resolved:** OpenET units (mm/day by request), reference ET (gridMET), clip polygon, extraction semantics, cause of the 61 missing dates, site-order variation in filenames.
**Still open:** the decisions.md D-015 questions and the footprint-set choice (D-016).

## Step 1 — 2026-10-13

**Delivered**
- [docs/input_contract.md](docs/input_contract.md): inventory, station and day-definition contract, station/extraction/raster/footprint semantics, mandatory-versus-optional fields and their validation errors, unresolved items with leads.
- [docs/decisions.md](docs/decisions.md): D-001 to D-015.
- [tools/profile_inputs.py](tools/profile_inputs.py): read-only profiler that regenerates the table facts.

**Checks run**
- SHA-256 of both example files matches PROJECT_CONTEXT.md; all counts there are reproduced (492/303/473/170/19, 2336 rows, 431/261/260 coverage, 260 common dates with 172 raw and 88 filled, 61 dates without extraction, 180 low-support rows).
- Verified relations: `ET_corr = ET·ebc_cf` (303/303), `ET_corr = ET_fill_val` on gap days (170/170), `ET_fill = gridMET_ETr·ETrF_filtered` (472/472), `ETrF = ET_corr/gridMET_ETr` (303/303), `ebc_cf = 1/ebr_corr` (458/458).
- Sampled 13 OpenET rasters on the Drive mount: one shared EPSG:4326 grid, float32, scale 1, NaN outside the export region plus −9999 nodata inside it.
- Sampled 3 US-CdM footprint rasters: EPSG:32612, 10 m, 121×121, weights summing to 1.

**New findings beyond PROJECT_CONTEXT.md**
- All 180 rows with `valid_weight_fraction < 0.90` are SIMS (cropland-only nodata mask).
- `daily_etw` footprints are NLDAS-ET-weighted in the current workflow code. *Superseded by revision (b): the footprints actually used came from an earlier version, so the weighting is unverified (D-009).*
- Gap-filled reference values are built from gridMET ETr (D-005).
- 2025-only models lack 2024 downloads; the 61 missing extraction days come from the footprint side (D-012).
- No local code writes the Parquet's exact schema; the producer is unconfirmed.

**Limitations**
- US-UTJ footprint rasters, audit files, OpenET version metadata and the Parquet producer were not available.
- The validation errors in contract §9 are specified, not implemented; they become code in steps 2–3.
- Real-data integration is limited to profiling; no performance results exist.

**Open questions:** decisions.md D-015.
