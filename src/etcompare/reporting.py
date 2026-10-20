"""Reports built from saved tables (guide step 17).

:func:`build_report` reads only files the analyses wrote under ``output_dir`` and the
workflow unit statuses (:mod:`etcompare.runrecord`). It writes to ``<output_dir>/report/``:

- ``report.md`` (figures linked) and ``report.html`` (self-contained, figures embedded);
- ``figures/<section>/<name>.{png,svg}`` and ``<name>_rows.csv``, the exact rows drawn;
- ``analysis_status.{parquet,csv}``: every declared unit with its status and reason;
- ``skipped_analyses.{parquet,csv}``: everything that produced no result, from the
  workflow (skipped, not available, not run, stale, failed; figures with nothing to
  draw) and from the analyses' own status columns (``not_available`` variables,
  ``insufficient_support`` intervals, ``declined`` folds, ``unknown`` residuals, ...).

**Wording.** The report carries each verdict over as written (``no_unique_leader``,
``includes_zero``, ``not_supported``, ``exploratory``, ``single_site``). It never uses
"network", "validated" or "significant" unless a verdict column licenses it
(:func:`check_language`; ``network_evidence`` true, a formal ``reject_zero`` decision;
nothing licenses "validated"). Member spread is disagreement. With
``report.synthetic`` every page and caption is labelled synthetic.

**Open decisions** listed in the report come from the declared ``decisions:`` and from
the tables' status columns (block lengths reported side by side with none adopted,
equivalence ``not_assessed``, exploratory contrasts, one-site summaries, provisional
variables, declined ensembles, unknown water-balance terms).
"""

from __future__ import annotations

import base64
import html
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from etcompare.config import Config, to_plain
from etcompare.errors import EtcompareError
from etcompare.io.tables import read_table, write_table
from etcompare.plots import (
    SYNTHETIC_LABEL,
    CaptionMismatch,
    FigureResult,
    NothingToDraw,
    check_meta,
    conditional_errors,
    contrast_intervals,
    coverage,
    cumulative,
    ensemble_disagreement,
    ensemble_folds,
    ensemble_improvement,
    ranking_stability,
    save_figure,
    scatter_1to1,
    scorecard,
)
from etcompare.runrecord import UnitRecord, status_table
from etcompare.schemas import SKIPPED_ANALYSES, CanonicalTable

#: Words a report may use only when a verdict column licenses them.
GUARDED_WORDS: dict[str, str] = {
    "network": r"\bnetworks?\b",
    "validated": r"\bvalidated\b",
    "significant": r"\bsignifican(?:t|tly|ce)\b",
}


class LanguageError(EtcompareError):
    """The report text uses a guarded word that no verdict column licenses."""


def check_language(text: str, licenses: Iterable[str] = ()) -> None:
    """Raise :class:`LanguageError` for a guarded word not in ``licenses``."""
    allowed = set(licenses)
    for word, pattern in GUARDED_WORDS.items():
        if word in allowed:
            continue
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if m:
            start = max(0, m.start() - 60)
            raise LanguageError(f"report uses {word!r} without a verdict that licenses it: "
                                f"...{text[start:m.end() + 60]}...")


# ---------------------------------------------------------------------- document model

@dataclass
class Block:
    kind: str  # heading | para | table | figure | list | banner
    text: str = ""
    level: int = 2
    frame: pd.DataFrame | None = None
    figure: dict[str, Path] = field(default_factory=dict)
    items: list[str] = field(default_factory=list)
    alt: str = ""


def _md_cell(v: Any) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)) or v is pd.NA:
        return ""
    if isinstance(v, float):
        return f"{v:.4g}"
    return str(v).replace("|", "\\|").replace("\n", " ")


def md_table(frame: pd.DataFrame, max_rows: int = 200) -> str:
    f = frame.head(max_rows)
    lines = ["| " + " | ".join(str(c) for c in f.columns) + " |",
             "|" + "|".join("---" for _ in f.columns) + "|"]
    lines += ["| " + " | ".join(_md_cell(v) for v in row) + " |"
              for row in f.itertuples(index=False)]
    if len(frame) > max_rows:
        lines.append(f"\n*{len(frame) - max_rows} more rows in the CSV.*")
    return "\n".join(lines)


def _html_table(frame: pd.DataFrame, max_rows: int = 200) -> str:
    f = frame.head(max_rows)
    head = "".join(f"<th>{html.escape(str(c))}</th>" for c in f.columns)
    body = "".join("<tr>" + "".join(f"<td>{html.escape(_md_cell(v))}</td>" for v in row)
                   + "</tr>" for row in f.itertuples(index=False))
    more = (f"<p class='muted'>{len(frame) - max_rows} more rows in the CSV.</p>"
            if len(frame) > max_rows else "")
    return f"<div class='tw'><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>{more}"


def render_markdown(blocks: list[Block], base: Path) -> str:
    out = []
    for b in blocks:
        if b.kind == "heading":
            out.append("#" * b.level + " " + b.text)
        elif b.kind in ("para", "banner"):
            out.append(f"**{b.text}**" if b.kind == "banner" else b.text)
        elif b.kind == "list":
            out.append("\n".join(f"- {i}" for i in b.items) if b.items else "- none")
        elif b.kind == "table" and b.frame is not None:
            out.append((f"*{b.text}*\n\n" if b.text else "") + md_table(b.frame))
        elif b.kind == "figure":
            img = b.figure.get("png") or b.figure.get("svg")
            rel = img.relative_to(base).as_posix() if img else ""
            out.append(f"![{b.alt}]({rel})\n\n*{b.text}*")
    return "\n\n".join(out) + "\n"


_CSS = """
:root{--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#8a8985;--line:#e4e3df;
--banner:#fff3d6;--banner-ink:#5b3d00}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--surface:#1a1a19;
--ink:#f0efec;--ink2:#c3c2b7;--muted:#8f8e86;--line:#3a3936;--banner:#3d2f0a;
--banner-ink:#ffe2a3}}
:root[data-theme="dark"]{--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--muted:#8f8e86;
--line:#3a3936;--banner:#3d2f0a;--banner-ink:#ffe2a3}
body{background:var(--surface);color:var(--ink);font:15px/1.5 system-ui,sans-serif;
margin:0 auto;max-width:1100px;padding:16px}
h1,h2,h3,h4{line-height:1.25}h2{border-bottom:1px solid var(--line);padding-bottom:4px}
.tw{overflow-x:auto}table{border-collapse:collapse;font-size:12px;margin:8px 0}
th,td{border-bottom:1px solid var(--line);padding:3px 8px;text-align:left;
font-variant-numeric:tabular-nums}th{color:var(--ink2)}
figure{margin:16px 0}figure img,figure svg{max-width:100%;height:auto;background:#fcfcfb;
border-radius:4px}figcaption{color:var(--ink2);font-size:13px}
.banner{background:var(--banner);color:var(--banner-ink);padding:8px 12px;border-radius:6px;
font-weight:600}.muted{color:var(--muted)}
"""


def render_html(blocks: list[Block], title: str) -> str:
    parts = []
    for b in blocks:
        if b.kind == "heading":
            parts.append(f"<h{b.level}>{html.escape(b.text)}</h{b.level}>")
        elif b.kind == "banner":
            parts.append(f"<p class='banner'>{html.escape(b.text)}</p>")
        elif b.kind == "para":
            parts.append(f"<p>{html.escape(b.text)}</p>")
        elif b.kind == "list":
            items = "".join(f"<li>{html.escape(i)}</li>" for i in b.items) or "<li>none</li>"
            parts.append(f"<ul>{items}</ul>")
        elif b.kind == "table" and b.frame is not None:
            cap = f"<p class='muted'><em>{html.escape(b.text)}</em></p>" if b.text else ""
            parts.append(cap + _html_table(b.frame))
        elif b.kind == "figure":
            png = b.figure.get("png")
            if png is not None:
                data = base64.b64encode(png.read_bytes()).decode("ascii")
                img = (f"<img alt='{html.escape(b.alt)}' "
                       f"src='data:image/png;base64,{data}'>")
            else:
                svg = b.figure["svg"].read_text(encoding="utf-8")
                img = svg[svg.find("<svg"):]
            parts.append(f"<figure>{img}<figcaption>{html.escape(b.text)}</figcaption></figure>")
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{html.escape(title)}</title><style>{_CSS}</style></head><body>"
            + "\n".join(parts) + "</body></html>\n")


# ---------------------------------------------------------------------- table reading

def _read(path: Path) -> CanonicalTable | None:
    return read_table(path) if path.is_file() else None


def _glob_frames(root: Path, pattern: str) -> list[tuple[Path, pd.DataFrame]]:
    return [(p, read_table(p).frame) for p in sorted(root.glob(pattern))]


#: Status columns read into the skipped-analyses table:
#: (glob under output_dir, analysis, status column, values that count as a result,
#:  detail columns, reason-code column, reason column).
STATUS_SOURCES: tuple[tuple[str, str, str, tuple[str, ...], tuple[str, ...], str | None,
                            str | None], ...] = (
    ("cohorts/*_availability.parquet", "diagnostics", "status", ("available",),
     ("comparison_id", "analysis", "variable", "reference_variant", "extraction_method"),
     "reason_code", "reason"),
    ("cohorts/*_satellite_availability.parquet", "satellite_support", "status",
     ("available",), ("comparison_id", "analysis", "variable"), "reason_code", "reason"),
    ("representativeness/*_availability.parquet", "representativeness", "status",
     ("available",), ("analysis_id", "analysis", "layer_id", "extraction_method"),
     "reason_code", "reason"),
    ("cohorts/*_intervals.parquet", "bootstrap", "status", ("ok",),
     ("comparison_id", "temporal_scale", "product", "block_length"), None, "status_detail"),
    ("cohorts/*_rankings.parquet", "rankings", "status", ("ok",),
     ("comparison_id", "product", "block_length"), None, "status_detail"),
    ("cohorts/*_network.parquet", "rankings", "status", ("ok",),
     ("comparison_id", "summary_type"), None, "status_detail"),
    ("cohorts/*_conditional.parquet", "diagnostics", "status", ("ok",),
     ("comparison_id", "variable"), None, "status_detail"),
    ("cohorts/*_metrics.parquet", "metrics", "validity_reason", (),
     ("comparison_id", "product", "period_type"), "validity_reason", None),
    ("events/*_anomaly_metrics.parquet", "anomalies", "status", ("ok",),
     ("comparison_id", "anomaly_id", "series_kind"), None, "status_detail"),
    ("events/*_responses.parquet", "events", "status", ("ok",),
     ("comparison_id", "event_analysis_id"), None, "status_detail"),
    ("water_balance/*_residuals.parquet", "water_balance", "status", ("computed",),
     ("water_balance_id", "residual_kind", "unknown_terms"), None, "reason"),
    ("ensembles/*_summary.parquet", "ensembles", "status", ("evaluated", "held_out"),
     ("ensemble_id", "scope", "subset"), None, "status_detail"),
    ("ensembles/*_folds.parquet", "ensembles", "status", ("fitted",),
     ("ensemble_id", "design", "fold"), None, "status_detail"),
    ("ensembles/*_improvement.parquet", "ensembles", "held_out_improvement",
     ("supported", "not_supported"), ("ensemble_id", "scope", "block_length"), None, "status"),
)


def table_status_rows(out: Path) -> pd.DataFrame:
    """Rows of every analysis table whose status column says it gave no result."""
    rows = []
    for pattern, analysis, col, ok, detail, code_col, reason_col in STATUS_SOURCES:
        for path, f in _glob_frames(out, pattern):
            if col not in f.columns:
                continue
            bad = f[f[col].notna() & ~f[col].isin(ok)] if ok else f[f[col].notna()]
            if bad.empty:
                continue
            keys = [c for c in detail if c in bad.columns]
            cols = [*keys, col] + [c for c in (code_col, reason_col) if c and c not in keys
                                   and c != col]
            g = bad[cols].astype("string").fillna("").groupby(cols, sort=True).size()
            for idx, n in g.items():
                vals = dict(zip(cols, idx if isinstance(idx, tuple) else (idx,), strict=True))
                rows.append({
                    "source": "table", "stage": "analyze", "unit_id": None,
                    "analysis": analysis,
                    "detail": "; ".join(f"{k}={vals[k]}" for k in keys if vals[k]),
                    "status": vals[col], "reason_code": vals.get(code_col) if code_col else None,
                    "reason": vals.get(reason_col) if reason_col else None,
                    "table": path.relative_to(out).as_posix(), "n_rows": int(n)})
    return pd.DataFrame(rows)


def derived_decisions(out: Path) -> list[str]:
    """Open choices that the tables' status columns show the outputs depend on."""
    items = []
    lengths: dict[str, set[int]] = {}
    for _, f in _glob_frames(out, "cohorts/*_intervals.parquet"):
        for b, g in f.groupby("bootstrap_id"):
            lengths.setdefault(str(b), set()).update(
                int(x) for x in g.loc[g["block_unit"] == "site_day", "block_length"].dropna())
    for b, ls in sorted(lengths.items()):
        if len(ls) > 1:
            items.append(f"Block length ({b}): intervals are reported side by side at "
                         f"{sorted(ls)} days; none is adopted.")
    contrasts = [f for _, f in _glob_frames(out, "cohorts/*_contrasts.parquet")]
    if contrasts:
        c = pd.concat(contrasts, ignore_index=True)
        if (c["equivalence"] == "not_assessed").any():
            items.append("Equivalence margins: none declared, so equivalence is not_assessed "
                         f"({int((c['equivalence'] == 'not_assessed').sum())} contrast rows).")
        if (c["inference"] == "exploratory").any():
            items.append("Formal tests: not requested, so every model contrast is "
                         "exploratory.")
    net = [f for _, f in _glob_frames(out, "cohorts/*_network.parquet")]
    if net and (pd.concat(net)["status"] == "single_site").any():
        items.append("Summaries across sites have n_sites = 1 (status single_site): they "
                     "restate one station.")
    cond = [f for _, f in _glob_frames(out, "cohorts/*_conditional.parquet")]
    if cond:
        c = pd.concat(cond)
        prov = sorted(set(c.loc[c["variable_status"] == "provisional", "variable"]))
        if prov:
            items.append(f"Conditioning variables with provisional meaning or units: {prov}.")
    for _, f in _glob_frames(out, "ensembles/*_summary.parquet"):
        for r in f[f["status"].isin(["declined", "exploratory", "not_available"])].itertuples():
            items.append(f"Ensemble {r.ensemble_id} {r.scope}: {r.status} "
                         f"({r.status_detail}).")
    for _, f in _glob_frames(out, "water_balance/*_residuals.parquet"):
        u = sorted({t for v in f["unknown_terms"].dropna() for t in str(v).split(";") if t})
        if u:
            items.append(f"Water balance {f['water_balance_id'].iloc[0]}: terms without input "
                         f"(unknown, never zero): {u}.")
    return items


def licenses(out: Path) -> set[str]:
    """Guarded words that a verdict column licenses."""
    ok = set()
    for _, f in _glob_frames(out, "cohorts/*_network.parquet"):
        if f["network_evidence"].fillna(False).astype(bool).any():
            ok.add("network")
    for _, f in _glob_frames(out, "cohorts/*_contrasts.parquet"):
        if (f["test_decision"] == "reject_zero").any():
            ok.add("significant")
    return ok


# ---------------------------------------------------------------------- report

class _Builder:
    def __init__(self, cfg: Config, out: Path) -> None:
        self.cfg = cfg
        self.out = out
        self.dir = out / "report"
        self.synthetic = cfg.report.synthetic
        self.blocks: list[Block] = []
        self.skips: list[dict[str, Any]] = []
        self.n_figures = 0

    def add(self, kind: str, text: str = "", **kw: Any) -> None:
        self.blocks.append(Block(kind, text, **kw))

    def figure(self, section: str, name: str, make: Callable[[], FigureResult],
               check: Callable[[FigureResult], None] | None = None) -> None:
        try:
            fr = make()
        except NothingToDraw as exc:
            self.skips.append({"source": "workflow", "stage": "report",
                               "unit_id": f"report:{section}/{name}", "analysis": "report",
                               "detail": f"figure {section}/{name}", "status": "not_available",
                               "reason_code": exc.reason_code, "reason": str(exc)})
            self.add("para", f"Figure {name} not drawn: {exc.reason_code} ({exc}).")
            return
        if check is not None:
            check(fr)
        stem = self.dir / "figures" / _safe(section) / _safe(name)
        paths = save_figure(fr, stem, self.cfg.report.figure_formats, dpi=self.cfg.report.dpi)
        fr.rows.to_csv(stem.parent / f"{stem.name}_rows.csv", index=False)
        if fr.figure is not None:
            fr.figure.clear()
        self.n_figures += 1
        self.add("figure", fr.caption, figure={p.suffix[1:]: p for p in paths}, alt=fr.title)

    def table(self, fr_make: Callable[[], FigureResult], section: str, name: str) -> None:
        try:
            fr = fr_make()
        except NothingToDraw as exc:
            self.add("para", f"Table {name} not available: {exc.reason_code} ({exc}).")
            return
        stem = self.dir / "figures" / _safe(section) / _safe(name)
        stem.parent.mkdir(parents=True, exist_ok=True)
        fr.rows.to_csv(stem.parent / f"{stem.name}_rows.csv", index=False)
        self.add("table", fr.caption, frame=fr.table)


def _git_line(git: dict[str, Any]) -> str:
    if not git.get("available"):
        return "Git revision: not available (not a git checkout)"
    dirty = git.get("dirty_paths") or []
    return (f"Git revision {git['revision']}"
            + (f", dirty ({len(dirty)} modified file(s))" if git.get("dirty") else ", clean"))


def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", s)


def _series(frame: pd.DataFrame) -> list[tuple[str, str, str]]:
    cols = ["reference_variant", "extraction_method", "footprint_set_id"]
    return [tuple(r) for r in frame[cols].drop_duplicates().astype(str).itertuples(index=False)]


def _pick(frame: pd.DataFrame, s: tuple[str, str, str]) -> pd.DataFrame:
    return frame[(frame["reference_variant"] == s[0]) & (frame["extraction_method"] == s[1])
                 & (frame["footprint_set_id"] == s[2])]


def _comparison_section(b: _Builder, spec: Any) -> None:
    cid = spec.comparison_id
    co = b.out / "cohorts"
    syn = b.synthetic
    check = lambda fr: check_meta(fr.meta, spec)  # noqa: E731
    b.add("heading", f"Comparison {cid}", level=2)
    paired = _read(co / f"{cid}_paired.parquet")
    if paired is None:
        b.add("para", "No cohort output: the cohort unit has not produced its tables "
                      "(see the analysis status).")
        return
    p = paired.frame
    b.add("para", f"Kind {spec.kind} (ranking scope {spec.ranking_scope}); models "
                  f"{sorted(p['model'].unique())}; reference variants "
                  f"{sorted(p['reference_variant'].unique())}; {p['site_id'].nunique()} "
                  f"site(s); {p[['site_id', 'date']].drop_duplicates().shape[0]} paired "
                  "site-days.")
    mem = _read(co / f"{cid}_membership.parquet")
    if mem is not None:
        b.figure(cid, "coverage", lambda: coverage(mem, paired, synthetic=syn), check)
    for ms in [m for m in b.cfg.metric_sets if m.comparisons is None or cid in m.comparisons]:
        mt = _read(co / f"{cid}__{ms.metric_set_id}_metrics.parquet")
        if mt is None:
            continue
        f = mt.frame
        for s in _series(f):
            tag = "__".join(s)
            sub = _pick(f, s)
            b.add("heading", f"{s[0]}, {s[1]} ({s[2]})", level=3)
            for site in sorted(sub["scope"].unique()):
                b.figure(cid, f"scorecard_{tag}_{site}",
                         lambda sub=sub, site=site: scorecard(sub[sub["scope"] == site],
                                                              synthetic=syn), check)
            d = sub[sub["product"] == "daily_pairs"]
            if len(d):
                w = d.pivot_table(index=["scope", "model"], columns="metric", values="value",
                                  aggfunc="first").round(3).reset_index()
                n = d.groupby(["scope", "model"])["n_days"].max().rename("n_days").reset_index()
                b.add("table", f"Daily metrics per site ({', '.join(sorted(d['units'].unique()))}"
                               f"); cohort {', '.join(sorted(d['cohort_id'].unique()))}",
                      frame=w.merge(n, on=["scope", "model"]).rename(columns={"scope": "site"}))
            ps = _pick(p, s)
            b.figure(cid, f"scatter_{tag}", lambda ps=ps: scatter_1to1(ps, synthetic=syn),
                     check)
            ce = _read(co / f"{cid}__{ms.metric_set_id}_cumulative_error.parquet")
            if ce is not None:
                cs = _pick(ce.frame, s)
                for site in sorted(cs["site_id"].unique()):
                    b.figure(cid, f"cumulative_{tag}_{site}",
                             lambda cs=cs, site=site: cumulative(cs[cs["site_id"] == site],
                                                                 synthetic=syn), check)
        for bs in [x for x in b.cfg.bootstrap if x.metric_set == ms.metric_set_id
                   and (x.comparisons is None or cid in x.comparisons)]:
            iv = _read(co / f"{cid}__{bs.bootstrap_id}_intervals.parquet")
            if iv is None:
                continue
            dims = set(iv.frame["contrast_dimension"])
            if "extraction_side" in dims:
                b.figure(cid, f"footprint_benefit_{bs.bootstrap_id}",
                         lambda iv=iv: contrast_intervals(iv, dimension="extraction_side",
                                                          synthetic=syn), check)
            if "reference_variant" in dims:
                b.figure(cid, f"raw_corrected_{bs.bootstrap_id}",
                         lambda iv=iv: contrast_intervals(iv, dimension="reference_variant",
                                                          synthetic=syn), check)
            for rs in [r for r in b.cfg.rankings if r.bootstrap == bs.bootstrap_id]:
                rk = _read(co / f"{cid}__{rs.ranking_id}_rankings.parquet")
                if rk is None:
                    continue
                b.table(lambda rk=rk: ranking_stability(rk, synthetic=syn), cid,
                        f"ranking_stability_{rs.ranking_id}")
                ct = _read(co / f"{cid}__{rs.ranking_id}_contrasts.parquet")
                if ct is not None:
                    c = ct.frame
                    c = c[(c["product"] == "daily_pairs") & c["ranking_basis"].astype(bool)
                          & c["metric"].isin(["mae", "rmse"]) & (c["subset"] == "all")]
                    if len(c):
                        t = (c.groupby(["scope", "reference_variant", "extraction_method",
                                        "metric",
                                        "block_length", "interval_position", "equivalence",
                                        "inference"]).size().rename("n_pairs").reset_index())
                        b.add("table", "Model contrasts (daily MAE, RMSE): pairs by interval "
                                       "position, as written by the contrasts table", frame=t)
    for ds in [d for d in b.cfg.diagnostics if d.comparisons is None or cid in d.comparisons]:
        cond = _read(co / f"{cid}__{ds.diagnostics_id}_conditional.parquet")
        if cond is not None:
            f = cond.frame
            for s in _series(f):
                sub = _pick(f, s)
                for site in sorted(sub["site_id"].unique()):
                    ss = sub[sub["site_id"] == site]
                    for v in sorted(ss["variable"].unique()):
                        b.figure(cid, f"conditional_{v}_{'__'.join(s)}_{site}",
                                 lambda ss=ss, v=v: conditional_errors(ss, variable=v,
                                                                       synthetic=syn), check)
        cl = _read(co / f"{cid}__{ds.diagnostics_id}_closure.parquet")
        if cl is not None:
            c = cl.frame
            c = c[(c["period_type"] == "all")][["day_set", "quantity", "quantity_units", "n_days",
                                                "n_days_filled_excluded", "mean", "median",
                                                "q05", "q95", "status"]]
            if len(c):
                b.add("table", "Correction magnitude from existing columns, corrected_from_raw "
                               "days only (reference sensitivity, not uncertainty)", frame=c)


def _ensemble_section(b: _Builder, spec: Any) -> None:
    eid = spec.ensemble_id
    e = b.out / "ensembles"
    syn = b.synthetic
    b.add("heading", f"Ensemble {eid}", level=2)
    summary = _read(e / f"{eid}_summary.parquet")
    if summary is None:
        b.add("para", "No ensemble output (see the analysis status).")
        return
    s = summary.frame

    def check(fr: FigureResult) -> None:
        if set(fr.meta.reference_variants) - {spec.reference_variant}:
            raise CaptionMismatch(f"ensemble {eid}: rows carry reference variants "
                                  f"{fr.meta.reference_variants}, declared "
                                  f"{spec.reference_variant}")

    b.add("para", "Post-extraction ensembles (not pixel-level). The official ensemble is a "
                  "comparison product, never a member.")
    b.add("table", "Status per scope, as written by the ensemble summary", frame=s[[
        "scope", "subset", "status", "n_sites", "n_folds", "n_folds_fitted", "n_days_compared",
        "status_detail"]])
    folds = _read(e / f"{eid}_folds.parquet")
    if folds is not None:
        b.figure(f"ensemble_{eid}", "folds", lambda: ensemble_folds(folds, s, synthetic=syn),
                 check)
    imp = _read(e / f"{eid}_improvement.parquet")
    if imp is not None:
        b.figure(f"ensemble_{eid}", "improvement",
                 lambda: ensemble_improvement(imp, s, synthetic=syn), check)
    days = _read(e / f"{eid}_days.parquet")
    if days is not None:
        b.figure(f"ensemble_{eid}", "disagreement",
                 lambda: ensemble_disagreement(days, s, synthetic=syn), check)


def _brief_sections(b: _Builder) -> None:
    out = b.out
    ev = _glob_frames(out, "events/*_responses.parquet")
    if ev:
        b.add("heading", "Management events (uncontrolled before/after; never causal)", level=2)
        f = pd.concat([x for _, x in ev], ignore_index=True)
        b.add("table", "Event responses by status, as written", frame=f.groupby(
            ["comparison_id", "event_analysis_id", "series", "status"]).size()
            .rename("rows").reset_index())
    an = _glob_frames(out, "events/*_anomaly_metrics.parquet")
    if an:
        b.add("heading", "Anomalies", level=2)
        f = pd.concat([x for _, x in an], ignore_index=True)
        b.add("table", "Anomaly metric rows by method, inference and status, as written",
              frame=f.groupby(["comparison_id", "anomaly_id", "method", "inference", "status"])
              .size().rename("rows").reset_index())
    wb = _glob_frames(out, "water_balance/*_residuals.parquet")
    if wb:
        b.add("heading", "Water-balance plausibility checks", level=2)
        f = pd.concat([x for _, x in wb], ignore_index=True)
        b.add("para", "Residuals are plausibility diagnostics, never ET error. An unknown term "
                      "is never zero and leaves the residual unknown.")
        b.add("table", "Residuals by status", frame=f.groupby(
            ["water_balance_id", "residual_kind", "status", "unknown_terms"], dropna=False)
            .size().rename("periods").reset_index())
    rp = _glob_frames(out, "representativeness/*_class_fractions.parquet")
    if rp:
        b.add("heading", "Landscape representativeness", level=2)
        f = pd.concat([x for _, x in rp], ignore_index=True)
        f = f[(f["class_role"] == "class") & (f["domain"] == "full_domain")]
        t = (f.groupby(["representativeness_id", "layer_id", "matching_policy",
                        "extraction_method", "class_name"])["class_frac_of_valid"]
             .agg(["mean", "min", "max", "size"]).round(3).reset_index())
        b.add("table", "Class share of valid weight, full domain (mean, min, max over days; a "
                       "landscape description, not an ET accuracy guarantee)",
              frame=t[t["mean"] >= 0.005])


def build_report(cfg: Config, records: list[UnitRecord], *, run_id: str,
                 manifest_info: dict[str, Any] | None = None) -> list[Path]:
    """Write the report, figures and status tables; return the written files."""
    if cfg.output_dir is None:
        raise EtcompareError("output_dir not declared")
    out = Path(cfg.output_dir)
    b = _Builder(cfg, out)
    b.dir.mkdir(parents=True, exist_ok=True)
    info = manifest_info or {}
    title = cfg.report.title + (f" ({SYNTHETIC_LABEL})" if b.synthetic else "")
    b.add("heading", title, level=1)
    if b.synthetic:
        b.add("banner", f"{SYNTHETIC_LABEL}: every value, station and model on this page is "
                        "generated by a fixed-seed script. Nothing here describes an observed "
                        "station or a real product.")
    else:
        b.add("banner", "Station-level results. A summary across sites with n_sites = 1 "
                        "restates one station; nothing here generalises beyond the stations "
                        "listed.")
    b.add("list", items=[f"Run {run_id}; configuration {cfg.source_path}",
                         f"Configuration hash {info.get('config_hash', '')[:16]}; code "
                         f"{info.get('code_identity', '')[:16]}",
                         _git_line(info.get("git") or {}),
                         f"Stations: {sorted(cfg.stations)}"])

    status = status_table(records)
    write_table(status, b.dir / "analysis_status.parquet")
    status.frame.to_csv(b.dir / "analysis_status.csv", index=False)
    f = status.frame
    b.add("heading", "Analyses that ran", level=2)
    ran = f[f["status"].isin(["completed", "cached"])]
    b.add("table", "Workflow units with results (completed now, or reused because no input "
                   "changed) and the runs that produced them",
          frame=ran.groupby(["stage", "analysis"]).agg(
              units=("unit_id", "size"),
              runs=("run_id", lambda s: ", ".join(sorted(set(s.dropna()))))).reset_index())

    head_n = len(b.blocks)
    comps = cfg.report.comparisons or tuple(c.comparison_id for c in cfg.comparisons)
    for cid in comps:
        _comparison_section(b, cfg.comparison(cid))
    for e in cfg.ensembles:
        _ensemble_section(b, e)
    _brief_sections(b)

    workflow_skips = f[~f["status"].isin(["completed", "cached"])]
    rows = [{"source": "workflow", "stage": r.stage, "unit_id": r.unit_id,
             "analysis": r.analysis, "detail": None, "status": r.status,
             "reason_code": r.reason_code, "reason": r.reason, "table": None, "n_rows": None}
            for r in workflow_skips.itertuples()]
    rows += [{**s, "table": None, "n_rows": None} for s in b.skips]
    table_rows = table_status_rows(out)
    skipped = pd.concat([pd.DataFrame(rows), table_rows], ignore_index=True) \
        if rows or len(table_rows) else SKIPPED_ANALYSES.empty()
    skipped["n_rows"] = pd.to_numeric(skipped["n_rows"], errors="raise").astype("Int64")
    skipped_t = CanonicalTable.build(skipped, SKIPPED_ANALYSES, {"run_id": run_id})
    write_table(skipped_t, b.dir / "skipped_analyses.parquet")
    skipped_t.frame.to_csv(b.dir / "skipped_analyses.csv", index=False)

    tail: list[Block] = []
    tail.append(Block("heading", "Skipped, not available or not run", level=2))
    tail.append(Block("para", "Workflow units without a result, with their reason. Every row "
                              "of the full table, including the analyses' own status rows, is "
                              "in report/skipped_analyses.csv."))
    w = skipped_t.frame[skipped_t.frame["source"] == "workflow"]
    tail.append(Block("table", "Workflow units", frame=w[["stage", "unit_id", "status",
                                                          "reason_code", "reason"]]))
    t = skipped_t.frame[skipped_t.frame["source"] == "table"]
    if len(t):
        tail.append(Block("table", "Analysis rows without a value, by status (counts)",
                          frame=t.groupby(["analysis", "status", "reason_code"], dropna=False)
                          ["n_rows"].sum().reset_index()))
    tail.append(Block("heading", "Open decisions these outputs depend on", level=2))
    dec = []
    for d in cfg.decisions:
        dec.append(f"{d.ref} ({d.status}): {d.summary} [applies to {', '.join(d.applies_to)}]")
    tail.append(Block("list", items=dec + derived_decisions(out)))
    blocks = b.blocks[:head_n] + tail + b.blocks[head_n:]
    if b.synthetic:
        blocks.append(Block("banner", f"{SYNTHETIC_LABEL}: end of report."))

    md = render_markdown(blocks, b.dir)
    check_language(md, licenses(out))
    paths = []
    if "markdown" in cfg.report.formats:
        p = b.dir / "report.md"
        p.write_text(md, encoding="utf-8")
        paths.append(p)
    if "html" in cfg.report.formats:
        p = b.dir / "report.html"
        p.write_text(render_html(blocks, title), encoding="utf-8")
        paths.append(p)
    paths += [b.dir / "analysis_status.parquet", b.dir / "analysis_status.csv",
              b.dir / "skipped_analyses.parquet", b.dir / "skipped_analyses.csv"]
    paths += sorted((b.dir / "figures").rglob("*.*")) if (b.dir / "figures").is_dir() else []
    return paths


def all_records(wf: Any) -> list[UnitRecord]:
    """Status of every declared unit: this run's records, else the last stamp (or not_run)."""
    validate = wf.cache.read("validate", "validate")
    recs = []
    if ("validate", "validate") in wf.records:
        recs.append(wf.records[("validate", "validate")])
    elif validate is not None:
        recs.append(UnitRecord.from_dict(validate))
    else:
        recs.append(UnitRecord("validate", "validate", "validate", "not_run", "never_run",
                               "etcompare validate has not been run"))
    for stage in ("index", "extract", "analyze"):
        recs += wf.status_of(wf.plan(stage))
    recs += wf.undeclared()
    return recs


def report_unit(wf: Any) -> Any:
    """The workflow unit that writes the report (cached on every input it reads)."""
    from etcompare.provenance import file_identity
    from etcompare.runner import config_hash
    from etcompare.workflow import Outcome, Unit

    holder: dict[str, Any] = {}

    def records() -> list[UnitRecord]:
        if "records" not in holder:
            holder["records"] = [r for r in all_records(wf) if r.stage != "report"]
        return holder["records"]

    def key() -> Any:
        tables = sorted(p for d in ("cohorts", "ensembles", "events", "water_balance",
                                    "representativeness")
                        for p in (wf.out / d).glob("*.parquet"))
        return [to_plain(wf.cfg.report), [to_plain(c) for c in wf.cfg.decisions],
                # cached and completed outputs are the same outputs
                [[r.stage, r.unit_id, "completed" if r.status == "cached" else r.status,
                  None if r.status == "cached" else r.reason_code, r.cache_key]
                 for r in records()],
                [file_identity(p, "stat") for p in tables]]

    def run() -> Outcome:
        from etcompare.provenance import code_identity, git_state

        info = {"config_hash": config_hash(wf.cfg), "code_identity": code_identity(),
                "git": git_state()}
        for p in build_report(wf.cfg, records(), run_id=wf.run_id, manifest_info=info):
            wf.add_output(p)
        return Outcome()

    return Unit("report", "report", "report", key, run)
