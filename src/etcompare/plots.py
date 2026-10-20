"""Figures built only from saved analysis tables (guide step 17).

Every function here takes canonical tables (or their frames) as written by the
analyses and returns a :class:`FigureResult`: the figure, **the exact rows it drew**
and a caption. Nothing is recomputed: values, intervals, statuses and counts are read
from the rows. When there is nothing to draw (no rows, or only unavailable ones) the
function raises :class:`NothingToDraw` with a reason; it never returns an empty figure.

**Captions** name the units, reference variant, extraction method (with footprint set),
temporal scale, cohort and sample size (``n_days``, ``n_sites``), all taken from the rows
(:func:`caption_meta`). :func:`check_meta` compares them with a comparison's declaration
and raises :class:`CaptionMismatch` when they disagree.

**Wording.** Statuses are printed as written by the analysis (``insufficient_support``,
``sparse_bin``, ``declined``, ...). Member spread is labelled disagreement, never an
interval. Synthetic figures carry "SYNTHETIC DATA" in the figure and the caption.

Plotting needs the ``plot`` extra (matplotlib >= 3.8); the module imports it only when a
figure is drawn. Figures use the object-oriented API (no pyplot state).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from etcompare._optional import require
from etcompare.errors import EtcompareError

#: Categorical slots in fixed order (reference palette, light mode).
PALETTE = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6",
           "#e34948")
MARKERS = ("o", "s", "^", "D", "v", "P", "X", "*")
INK, INK2, MUTED, GRID, SURFACE, SHADE = ("#0b0b0b", "#52514e", "#8a8985", "#e4e3df",
                                          "#fcfcfb", "#e9e8e4")
SYNTHETIC_LABEL = "SYNTHETIC DATA"
ET_UNITS = "mm d-1"


class NothingToDraw(EtcompareError):
    """No row can be drawn; ``reason_code`` and the message say why."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


class CaptionMismatch(EtcompareError):
    """A caption field read from the rows disagrees with the declaration."""


@dataclass(frozen=True)
class CaptionMeta:
    """Caption fields, all read from the drawn rows."""

    units: str
    reference_variants: tuple[str, ...]
    extraction: tuple[str, ...]
    scale: str
    cohort_ids: tuple[str, ...]
    comparison_ids: tuple[str, ...]
    n_days: str
    n_sites: int

    def text(self) -> str:
        return (f"Units: {self.units}. Reference: {', '.join(self.reference_variants) or 'none'}. "
                f"Extraction: {', '.join(self.extraction) or 'none'}. Scale: {self.scale}. "
                f"Cohort: {', '.join(self.cohort_ids)}. n_days = {self.n_days}; "
                f"n_sites = {self.n_sites}.")


@dataclass
class FigureResult:
    """A drawn figure (``None`` for a table result), its rows and caption."""

    kind: str
    title: str
    figure: Any
    rows: pd.DataFrame
    caption: str
    meta: CaptionMeta
    table: pd.DataFrame | None = None
    notes: list[str] = field(default_factory=list)


def _frame(x: Any) -> pd.DataFrame:
    return x.frame if hasattr(x, "frame") else x


def _uniq(rows: pd.DataFrame, col: str) -> tuple[str, ...]:
    if col not in rows.columns:
        return ()
    return tuple(sorted(str(v) for v in rows[col].dropna().unique()))


def _span(values: Sequence[int]) -> str:
    v = sorted({int(x) for x in values})
    if not v:
        return "0"
    return str(v[0]) if len(v) == 1 else f"{v[0]}–{v[-1]}"


def caption_meta(rows: pd.DataFrame, *, units: str, scale: str,
                 n_days: str | None = None) -> CaptionMeta:
    """Caption fields from ``rows``: never from the configuration."""
    ext = ()
    if "extraction_method" in rows.columns:
        fs = rows["footprint_set_id"] if "footprint_set_id" in rows.columns else "none"
        ext = tuple(sorted({f"{m}@{f}" for m, f in zip(
            rows["extraction_method"].astype(str),
            (fs.astype(str) if isinstance(fs, pd.Series) else [fs] * len(rows)), strict=True)}))
    if "site_id" in rows.columns:
        n_sites = int(rows["site_id"].nunique())
    elif "n_sites" in rows.columns and rows["n_sites"].notna().any():
        n_sites = int(rows["n_sites"].max())
    elif "scope" in rows.columns:
        n_sites = int(rows["scope"].nunique())
    else:
        n_sites = 0
    if n_days is None:
        if {"site_id", "date"} <= set(rows.columns):
            n_days = str(int(rows[["site_id", "date"]].drop_duplicates().shape[0]))
        elif "n_days" in rows.columns:
            n_days = _span(rows["n_days"].dropna())
        else:
            n_days = "not recorded"
    return CaptionMeta(units, _uniq(rows, "reference_variant"), ext, scale,
                       _uniq(rows, "cohort_id"), _uniq(rows, "comparison_id"), n_days, n_sites)


def check_meta(meta: CaptionMeta, spec: Any) -> None:
    """Raise :class:`CaptionMismatch` when ``meta`` disagrees with a ``ComparisonSpec``."""
    if meta.comparison_ids and set(meta.comparison_ids) != {spec.comparison_id}:
        raise CaptionMismatch(f"rows belong to {meta.comparison_ids}, not "
                              f"{spec.comparison_id}")
    extra = set(meta.reference_variants) - set(spec.reference_variants)
    if extra:
        raise CaptionMismatch(f"{spec.comparison_id}: rows carry reference variant(s) "
                              f"{sorted(extra)} that the comparison does not declare")
    sides = {s.label for s in spec.sides}
    extra = set(meta.extraction) - sides
    if extra:
        raise CaptionMismatch(f"{spec.comparison_id}: rows carry extraction side(s) "
                              f"{sorted(extra)}; declared {sorted(sides)}")


def compose_caption(title: str, meta: CaptionMeta, notes: Sequence[str] = (), *,
                    synthetic: bool = False) -> str:
    head = f"{SYNTHETIC_LABEL}. " if synthetic else ""
    tail = " ".join(n.rstrip(".") + "." for n in notes if n)
    return f"{head}{title}. {meta.text()}" + (f" {tail}" if tail else "")


# ---------------------------------------------------------------------- drawing

def _figure(width: float, height: float, *, synthetic: bool) -> Any:
    mfig = require("matplotlib.figure", "plotting")
    fig = mfig.Figure(figsize=(width, height), facecolor=SURFACE, layout="constrained")
    if synthetic:
        fig.suptitle(SYNTHETIC_LABEL, color=INK2, fontsize=9, fontweight="bold",
                     x=0.99, ha="right")
    return fig


def _style(ax: Any) -> None:
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(MUTED)
    ax.tick_params(colors=INK2, labelsize=8)
    ax.grid(True, color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    ax.xaxis.label.set_color(INK2)
    ax.yaxis.label.set_color(INK2)
    ax.title.set_color(INK)


def _one_series(rows: pd.DataFrame, what: str) -> None:
    cols = [c for c in ("reference_variant", "extraction_method", "footprint_set_id")
            if c in rows.columns]
    if cols and len(rows[cols].drop_duplicates()) > 1:
        raise ValueError(f"{what}: draw one reference variant and extraction side at a time; "
                         f"got {rows[cols].drop_duplicates().to_dict('records')}")


def _one_site(rows: pd.DataFrame, what: str, col: str) -> None:
    if col in rows.columns and rows[col].nunique() > 1:
        raise ValueError(f"{what}: one site per figure; got {sorted(rows[col].unique())}")


def _colors(n: int, what: str) -> tuple[str, ...]:
    if n > len(PALETTE):
        raise NothingToDraw("too_many_series", f"{what}: {n} series exceed the {len(PALETTE)} "
                            "categorical colours; facet the figure instead")
    return PALETTE[:n]


def save_figure(result: FigureResult, stem: str | Path, formats: Sequence[str] = ("png", "svg"),
                *, dpi: int = 120) -> list[Path]:
    """Write ``result.figure`` as ``<stem>.<format>`` for each format; return the paths."""
    if result.figure is None:
        return []
    stem = Path(stem)
    stem.parent.mkdir(parents=True, exist_ok=True)
    out = []
    for f in formats:
        p = stem.with_suffix(f".{f}")
        result.figure.savefig(p, format=f, dpi=dpi, facecolor=SURFACE,
                              metadata={"Date": None} if f == "svg" else None)
        out.append(p)
    return out


# ---------------------------------------------------------------------- figures

def scorecard(metrics: Any, *, metric_ids: Sequence[str] = ("mae", "rmse", "mbe", "pearson_r"),
              product: str = "daily_pairs", period_type: str = "none",
              synthetic: bool = False) -> FigureResult:
    """Point estimates per model and metric for one reference variant and side.

    Undefined metrics are printed with their ``validity_reason``.
    """
    f = _frame(metrics)
    rows = f[(f["product"] == product) & (f["period_type"] == period_type)
             & f["metric"].isin(metric_ids)].copy()
    if rows.empty:
        raise NothingToDraw("no_rows", f"no {product} metrics among {list(metric_ids)}")
    _one_series(rows, "scorecard")
    _one_site(rows, "scorecard", "scope")
    shown = [m for m in metric_ids if m in set(rows["metric"])]
    models = sorted(rows["model"].unique())
    fig = _figure(2.4 * len(shown) + 1.2, 0.32 * len(models) + 1.3, synthetic=synthetic)
    axes = fig.subplots(1, len(shown), sharey=True, squeeze=False)[0]
    for ax, m in zip(axes, shown, strict=True):
        _style(ax)
        r = rows[rows["metric"] == m].set_index("model").reindex(models)
        y = np.arange(len(models))
        ok = r["valid"].fillna(False).astype(bool).to_numpy()
        ax.scatter(r["value"].to_numpy()[ok], y[ok], s=36, color=PALETTE[0], zorder=3,
                   edgecolor=SURFACE, linewidth=1.5)
        for yy, (_, row) in zip(y, r.iterrows(), strict=True):
            if not ok[yy] and pd.notna(row.get("validity_reason")):
                ax.text(0.02, yy, str(row["validity_reason"]), transform=ax.get_yaxis_transform(),
                        va="center", fontsize=7, color=INK2)
            elif ok[yy]:
                ax.annotate(f"{row['value']:.2f}", (row["value"], yy), xytext=(5, 0),
                            textcoords="offset points", va="center", fontsize=7, color=INK2)
        if m in ("mbe", "percent_bias", "ols_intercept"):
            ax.axvline(0, color=MUTED, linewidth=0.8)
        unit = _uniq(r.reset_index(), "units")
        ax.set_title(m, fontsize=9)
        ax.set_xlabel(unit[0] if unit else "")
        ax.set_yticks(y, models)
    meta = caption_meta(rows, units=", ".join(_uniq(rows, "units")),
                        scale=f"{product}" + (f" ({period_type})" if period_type != "none"
                                              else " (daily)"))
    title = f"Scorecard, {rows['comparison_id'].iloc[0]}, {rows['scope'].iloc[0]}"
    cap = compose_caption(title, meta, ["Point estimates on the cohort's frozen days; "
                                        "intervals are in the contrast figures and tables"],
                          synthetic=synthetic)
    return FigureResult("scorecard", title, fig, rows, cap, meta)


def scatter_1to1(paired: Any, *, synthetic: bool = False, ncols: int = 4) -> FigureResult:
    """Modeled against observed daily ET per model, with the 1:1 line."""
    rows = _frame(paired).copy()
    if rows.empty:
        raise NothingToDraw("no_rows", "no paired rows")
    _one_series(rows, "scatter")
    models = sorted(rows["model"].unique())
    n = len(models)
    nc = min(ncols, n)
    nr = int(np.ceil(n / nc))
    fig = _figure(2.4 * nc + 0.4, 2.4 * nr + 0.5, synthetic=synthetic)
    axes = fig.subplots(nr, nc, squeeze=False).ravel()
    lo = float(min(rows["observed_mm"].min(), rows["modeled_mm"].min(), 0.0))
    hi = float(max(rows["observed_mm"].max(), rows["modeled_mm"].max()))
    for ax, m in zip(axes, models, strict=False):
        _style(ax)
        r = rows[rows["model"] == m]
        ax.plot([lo, hi], [lo, hi], color=MUTED, linewidth=1, linestyle="--", zorder=2)
        ax.scatter(r["observed_mm"], r["modeled_mm"], s=8, color=PALETTE[0], alpha=0.6,
                   linewidth=0, zorder=3)
        ax.set_xlim(lo, hi)
        ax.set_ylim(lo, hi)
        ax.set_aspect("equal")
        ax.set_title(f"{m} (n_days = {r[['site_id', 'date']].drop_duplicates().shape[0]})",
                     fontsize=8)
        ax.set_xlabel(f"observed ({ET_UNITS})", fontsize=8)
        ax.set_ylabel(f"modeled ({ET_UNITS})", fontsize=8)
    for ax in axes[n:]:
        ax.set_visible(False)
    meta = caption_meta(rows, units=ET_UNITS, scale="daily")
    title = f"Modeled vs observed ET with the 1:1 line, {rows['comparison_id'].iloc[0]}"
    return FigureResult("scatter", title, fig, rows,
                        compose_caption(title, meta, synthetic=synthetic), meta)


def _gap_spans(dates: pd.Series, missing: pd.Series) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    spans = []
    d = pd.to_datetime(dates).reset_index(drop=True)
    miss = missing.reset_index(drop=True).fillna(0).astype(int)
    for i in range(1, len(d)):
        if miss[i] > 0:
            spans.append((d[i - 1] + pd.Timedelta(days=1), d[i] - pd.Timedelta(days=1)))
    return spans


def cumulative(cumulative_error: Any, *, synthetic: bool = False) -> FigureResult:
    """Cumulative observed and modeled ET (top) and cumulative error (bottom).

    Curves are drawn per ``segment`` and never joined across a missing period; missing
    spans are shaded and counted in the caption.
    """
    rows = _frame(cumulative_error).sort_values(["model", "site_id", "date"]).copy()
    if rows.empty:
        raise NothingToDraw("no_rows", "no cumulative-error rows")
    _one_series(rows, "cumulative")
    if rows["site_id"].nunique() > 1:
        raise ValueError("cumulative: one site per figure")
    models = sorted(rows["model"].unique())
    colors = _colors(len(models), "cumulative")
    fig = _figure(8.0, 5.6, synthetic=synthetic)
    ax1, ax2 = fig.subplots(2, 1, sharex=True)
    for ax in (ax1, ax2):
        _style(ax)
    same_days = rows.groupby("model")["date"].apply(lambda s: tuple(s)).nunique() == 1
    spans: set[tuple[pd.Timestamp, pd.Timestamp]] = set()
    n_missing: dict[str, int] = {}
    for m, c in zip(models, colors, strict=True):
        r = rows[rows["model"] == m]
        spans |= set(_gap_spans(r["date"], r["missing_days_before"]))
        n_missing[m] = int(r["missing_days_before"].fillna(0).sum())
        for k, (_, seg) in enumerate(r.groupby("segment")):
            lab = m if k == 0 else None
            ax1.plot(seg["date"], seg["cumulative_modeled_mm"], color=c, linewidth=1.6, label=lab)
            ax2.plot(seg["date"], seg["cumulative_error_mm"], color=c, linewidth=1.6, label=lab)
            if not same_days:
                ax1.plot(seg["date"], seg["cumulative_observed_mm"], color=c, linewidth=1,
                         linestyle=":")
    if same_days:
        r = rows[rows["model"] == models[0]]
        for k, (_, seg) in enumerate(r.groupby("segment")):
            ax1.plot(seg["date"], seg["cumulative_observed_mm"], color=INK, linewidth=2,
                     linestyle="--", label="observed (station)" if k == 0 else None)
    for a, b in sorted(spans):
        for ax in (ax1, ax2):
            ax.axvspan(a, b + pd.Timedelta(days=1), color=SHADE, zorder=0, linewidth=0)
    ax2.axhline(0, color=MUTED, linewidth=0.8)
    ax1.set_ylabel("cumulative ET (mm)")
    ax2.set_ylabel("cumulative error P − O (mm)")
    ax1.legend(fontsize=7, frameon=False, ncols=4, loc="upper left")
    meta = caption_meta(rows, units="mm (running sums of mm d-1)", scale="daily, cumulated")
    gaps = (f"{len(spans)} missing period(s) shaded; curves are not joined across them "
            f"(missing days per model: {_span(n_missing.values())})" if spans
            else "no missing day inside the record")
    obs = ("observed is the same cumulative series for every model (frozen days)" if same_days
           else "days differ between models; each model's observed sum is dotted")
    title = f"Cumulative ET and cumulative error, {rows['comparison_id'].iloc[0]}"
    return FigureResult("cumulative", title, fig, rows,
                        compose_caption(title, meta, [gaps, obs], synthetic=synthetic), meta,
                        notes=[gaps])


def contrast_intervals(intervals: Any, *, dimension: str,
                       metric_ids: Sequence[str] = ("mae", "rmse", "mbe"),
                       synthetic: bool = False) -> FigureResult:
    """Paired contrasts with intervals at every block length, side by side.

    ``dimension`` is ``extraction_side`` (footprint benefit) or ``reference_variant``
    (raw/corrected sensitivity). Rows without bounds show their status.
    """
    f = _frame(intervals)
    rows = f[(f["contrast_dimension"] == dimension) & (f["product"] == "daily_pairs")
             & f["metric"].isin(metric_ids)].copy()
    if rows.empty:
        raise NothingToDraw("no_rows", f"no daily {dimension} contrasts")
    method = sorted(rows["interval_method"].unique())[0]
    rows = rows[rows["interval_method"] == method]
    shown = [m for m in metric_ids if m in set(rows["metric"])]
    blocks = sorted(rows["block_length"].dropna().unique())
    colors = _colors(len(blocks), "contrast_intervals")
    tcol = "extraction_method" if dimension == "extraction_side" else "reference_variant"
    multi = rows["scope"].nunique() > 1
    label = lambda r: (f"{r['scope']} | " if multi else "") + f"{r['model']} | {r[tcol]}"  # noqa: E731
    targets = sorted({label(r) for _, r in rows.iterrows()})
    fig = _figure(2.8 * len(shown) + 1.6, 0.36 * len(targets) * max(1, len(blocks)) / 2 + 1.6,
                  synthetic=synthetic)
    axes = fig.subplots(1, len(shown), sharey=True, squeeze=False)[0]
    for ax, met in zip(axes, shown, strict=True):
        _style(ax)
        ax.axvline(0, color=MUTED, linewidth=0.8)
        r = rows[rows["metric"] == met]
        for j, (b, c) in enumerate(zip(blocks, colors, strict=True)):
            off = (j - (len(blocks) - 1) / 2) * 0.22
            for _, row in r[r["block_length"] == b].iterrows():
                y = targets.index(label(row)) + off
                if row["status"] == "ok":
                    ax.plot([row["lower"], row["upper"]], [y, y], color=c, linewidth=2)
                    ax.scatter([row["estimate"]], [y], color=c, marker=MARKERS[j], s=24,
                               zorder=3, edgecolor=SURFACE, linewidth=1)
                else:
                    ax.scatter([row["estimate"]], [y], facecolor="none", edgecolor=c,
                               marker=MARKERS[j], s=24, zorder=3)
                    ax.annotate(str(row["status"]), (row["estimate"], y), xytext=(5, 0),
                                textcoords="offset points", fontsize=6, color=INK2,
                                va="center")
            ax.plot([], [], color=c, marker=MARKERS[j], label=f"{int(b)}-day blocks")
        ax.set_yticks(range(len(targets)), targets)
        unit = _uniq(r, "units")
        ax.set_xlabel(f"Δ{met} ({unit[0] if unit else ''})")
        ax.set_title(met, fontsize=9)
    axes[0].legend(fontsize=7, frameon=False, loc="best")
    base = ", ".join(_uniq(rows, "contrast_baseline"))
    level = ", ".join(f"{float(x):.0%}" for x in rows["confidence_level"].dropna().unique())
    kind = ("Footprint benefit" if dimension == "extraction_side"
            else "Raw/corrected reference sensitivity")
    notes = [f"Each estimate is metric(target) − metric(baseline {base}), as written in the "
             "intervals table",
             f"{level} {method} intervals at block lengths {[int(b) for b in blocks]} shown "
             "side by side; no block length is adopted",
             "Rows without bounds show their status (hollow markers)"]
    if dimension == "reference_variant":
        notes.append("Corrected/raw differences are reference sensitivity, not uncertainty "
                     "bounds")
    meta = caption_meta(rows, units=", ".join(_uniq(rows, "units")), scale="daily")
    title = f"{kind}, {rows['comparison_id'].iloc[0]}"
    return FigureResult(f"contrast_{dimension}", title, fig, rows,
                        compose_caption(title, meta, notes, synthetic=synthetic), meta)


def conditional_errors(conditional: Any, *, variable: str, metric: str = "mae",
                       synthetic: bool = False) -> FigureResult:
    """A metric per declared bin and model; sparse and empty bins show their status."""
    f = _frame(conditional)
    rows = f[(f["variable"] == variable) & (f["metric"] == metric) & (f["bin_index"] >= 0)]
    if rows.empty:
        raise NothingToDraw("no_rows", f"no {metric} bins for {variable}")
    _one_series(rows, "conditional_errors")
    _one_site(rows, "conditional_errors", "site_id")
    rows = (rows.sort_values("block_length", na_position="first")
            .drop_duplicates(["model", "bin_index"]).sort_values(["model", "bin_index"]))
    if not (rows["status"] == "ok").any():
        raise NothingToDraw("no_bin_with_value",
                            f"{variable}: every bin is {sorted(set(rows['status']))}")
    models = sorted(rows["model"].unique())
    colors = _colors(len(models), "conditional_errors")
    bins = rows.drop_duplicates("bin_index").sort_values("bin_index")
    fig = _figure(max(5.0, 0.9 * len(bins) + 2.5), 3.6, synthetic=synthetic)
    ax = fig.subplots()
    _style(ax)
    for j, (m, c) in enumerate(zip(models, colors, strict=True)):
        r = rows[rows["model"] == m].set_index("bin_index").reindex(bins["bin_index"])
        v = r["value"].where(r["status"] == "ok")
        ax.plot(range(len(bins)), v.to_numpy(float), color=c, marker=MARKERS[j], markersize=4,
                linewidth=1.4, label=m)
    for i, (_, b) in enumerate(bins.iterrows()):
        st = rows[rows["bin_index"] == b["bin_index"]]["status"]
        if (st != "ok").all():
            ax.annotate(f"{st.iloc[0]}\n(n_days {int(b['n_days_bin_cohort'])})", (i, 0),
                        xycoords=("data", "axes fraction"), xytext=(0, 4),
                        textcoords="offset points", ha="center", fontsize=6, color=INK2)
    ax.set_xticks(range(len(bins)), [f"{lbl}\nn={int(n)}" for lbl, n in zip(
        bins["bin_label"], bins["n_days_bin_cohort"], strict=True)], fontsize=7)
    unit = _uniq(rows, "units")
    vunit = _uniq(rows, "variable_units")
    ax.set_ylabel(f"{metric} ({unit[0] if unit else ''})")
    ax.set_xlabel(f"{variable} ({vunit[0] if vunit else ''})")
    ax.legend(fontsize=7, frameon=False, ncols=2)
    status = _uniq(rows, "variable_status")
    notes = [f"Bins declared before any error was computed; bins below min_bin_days "
             f"({_span(rows['min_bin_days'].dropna())}) show their status instead of a value",
             f"variable status: {', '.join(status)}",
             "Associations within the record, not causes"]
    if rows["flags"].fillna("").str.contains("bin_days_differ_across_models").any():
        notes.append("bin days differ across models (bin_days_differ_across_models)")
    meta = caption_meta(rows, units=", ".join(unit), scale="daily",
                        n_days=_span(rows["n_days_series"].dropna()))
    title = f"{metric} by {variable}, {rows['comparison_id'].iloc[0]}, {rows['site_id'].iloc[0]}"
    return FigureResult("conditional", title, fig, rows,
                        compose_caption(title, meta, notes, synthetic=synthetic), meta)


def coverage(membership: Any, paired: Any = None, *, synthetic: bool = False) -> FigureResult:
    """Every candidate site-day per model, coloured by inclusion or exclusion reason.

    The membership table has no variant or side columns; when the same cohort's
    ``paired`` table is given, the caption takes them from it (cohort IDs must match).
    """
    rows = _frame(membership).copy()
    if rows.empty:
        raise NothingToDraw("no_rows", "no membership rows")
    rows["state"] = np.where(rows["included"].astype(bool), "included",
                             rows["reason_code"].fillna("excluded").astype(str))
    states = ["included", *sorted(set(rows["state"]) - {"included"})]
    colors = _colors(len(states), "coverage")
    models = sorted(rows["model"].unique())
    sites = sorted(rows["site_id"].unique())
    lanes = [(s, m) for s in sites for m in models]
    fig = _figure(8.5, 0.3 * len(lanes) + 1.6, synthetic=synthetic)
    ax = fig.subplots()
    _style(ax)
    ax.grid(False)
    for st, c, mk in zip(states, colors, MARKERS, strict=False):
        r = rows[rows["state"] == st]
        y = [lanes.index((s, m)) for s, m in zip(r["site_id"], r["model"], strict=True)]
        ax.scatter(r["date"], y, s=6, marker="s" if st == "included" else mk, color=c,
                   linewidth=0, label=f"{st} ({len(r)})")
    ax.set_yticks(range(len(lanes)), [f"{s} {m}" if len(sites) > 1 else m for s, m in lanes],
                  fontsize=7)
    ax.legend(fontsize=7, frameon=False, loc="upper left", bbox_to_anchor=(1.0, 1.0))
    included = rows[rows["state"] == "included"]
    n_days = str(int(included[["site_id", "date"]].drop_duplicates().shape[0]))
    meta = caption_meta(included if len(included) else rows, units="site-days",
                        scale="daily", n_days=n_days)
    if paired is not None:
        p = _frame(paired)
        if set(p["cohort_id"]) - set(rows["cohort_id"]):
            raise CaptionMismatch("coverage: paired rows belong to another cohort")
        pm = caption_meta(p, units="site-days", scale="daily")
        meta = CaptionMeta(meta.units, pm.reference_variants, pm.extraction, meta.scale,
                           meta.cohort_ids, meta.comparison_ids, n_days, meta.n_sites)
    title = f"Coverage and exclusion reasons, {rows['comparison_id'].iloc[0]}"
    cap = compose_caption(title, meta, ["n_days counts included site-days; every candidate "
                                        "day is shown with its reason code"],
                          synthetic=synthetic)
    return FigureResult("coverage", title, fig, rows.drop(columns="state"), cap, meta)


def ranking_stability(rankings: Any, *, metric_ids: Sequence[str] = ("mae", "rmse"),
                      subset: str = "all", synthetic: bool = False) -> FigureResult:
    """Table of point rank, mean rank, rank interval and top-rank frequency per block length."""
    f = _frame(rankings)
    rows = f[(f["product"] == "daily_pairs") & f["metric"].isin(metric_ids)
             & (f["subset"] == subset)].copy()
    if rows.empty:
        raise NothingToDraw("no_rows", f"no daily rankings for {list(metric_ids)}")
    rows = rows.sort_values(["scope", "reference_variant", "extraction_method", "metric",
                             "block_length", "rank"])
    table = pd.DataFrame({
        "site": rows["scope"], "reference": rows["reference_variant"], "extraction": rows["extraction_method"],
        "metric": rows["metric"], "block": rows["block_length"].astype("Int64"),
        "model": rows["model"], "value": rows["value"].round(3), "rank": rows["rank"],
        "mean rank": rows["mean_rank"].round(2),
        "rank interval": [f"{lo:g}–{hi:g}" if s == "ok" else s for lo, hi, s in zip(
            rows["rank_lower"], rows["rank_upper"], rows["status"], strict=True)],
        "top-rank freq.": rows["top_rank_frequency"].round(2),
        "leader status": rows["leader_status"], "n_days": rows["n_days"]})
    meta = caption_meta(rows, units=", ".join(_uniq(rows, "units")), scale="daily")
    title = f"Ranking stability, {rows['comparison_id'].iloc[0]}"
    notes = ["Top-rank frequency is a bootstrap stability measure, not the probability that a "
             "model is best", "Leader status as written by the ranking table"]
    return FigureResult("ranking_stability", title, None, rows,
                        compose_caption(title, meta, notes, synthetic=synthetic), meta,
                        table=table)


def _ensemble_meta(rows: pd.DataFrame, summary: pd.DataFrame, n_days: str) -> CaptionMeta:
    s = summary.copy()
    return CaptionMeta(ET_UNITS, _uniq(s, "reference_variant"),
                       tuple(sorted(f"{m}@{f}" for m, f in zip(
                           s["extraction_method"], s["footprint_set_id"], strict=True))),
                       "daily", _uniq(s, "comparison_id"), (), n_days,
                       int(s["n_sites"].max()) if len(s) else 0)


def ensemble_folds(folds: Any, summary: Any, *, synthetic: bool = False) -> FigureResult:
    """Every fold with its status: fitted (held out), exploratory_in_sample or declined."""
    rows = _frame(folds).copy()
    if rows.empty:
        raise NothingToDraw("no_rows", "no ensemble folds (no fitted design declared)")
    status_color = {"fitted": PALETTE[0], "exploratory_in_sample": PALETTE[3],
                    "declined": MUTED}
    status_label = {"fitted": "held out (fitted on other folds)",
                    "exploratory_in_sample": "exploratory (in sample, no held-out claim)",
                    "declined": "declined (not fitted)"}
    designs = sorted(rows["design"].unique())
    fig = _figure(max(5.0, 1.1 * rows.groupby("design").size().max() + 2.0),
                  1.0 * len(designs) + 1.4, synthetic=synthetic)
    ax = fig.subplots()
    _style(ax)
    ax.grid(False)
    for i, d in enumerate(designs):
        r = rows[rows["design"] == d].sort_values("fold")
        for j, (_, row) in enumerate(r.iterrows()):
            c = status_color.get(row["status"], MUTED)
            ax.scatter([j], [i], s=420, marker="s", color=c, edgecolor=SURFACE, linewidth=2)
            label = "held_out" if row["status"] == "fitted" else row["status"]
            ax.annotate(f"{row['fold']}\n{label}\nn_test {int(row['n_test_days'])}", (j, i),
                        xytext=(0, -24), textcoords="offset points", ha="center", va="top",
                        fontsize=6, color=INK2)
    ax.set_yticks(range(len(designs)), designs)
    ax.set_xticks([])
    ax.set_ylim(len(designs) - 0.4, -0.9)
    for st, c in status_color.items():
        ax.scatter([], [], marker="s", color=c, label=status_label[st])
    ax.legend(fontsize=7, frameon=False, loc="upper left", bbox_to_anchor=(1.0, 1.0))
    meta = _ensemble_meta(rows, _frame(summary), _span(rows["n_test_days"].dropna()))
    title = f"Ensemble folds, {rows['ensemble_id'].iloc[0]}"
    notes = ["fitted folds have held-out predictions; exploratory_in_sample folds make no "
             "held-out claim; declined folds have no weights", "n_days is held-out days per fold"]
    return FigureResult("ensemble_folds", title, fig, rows,
                        compose_caption(title, meta, notes, synthetic=synthetic), meta)


def ensemble_improvement(improvement: Any, summary: Any, *, metric: str = "rmse",
                         synthetic: bool = False) -> FigureResult:
    """Held-out contrasts metric(fitted) − metric(product) per station and block length."""
    f = _frame(improvement)
    rows = f[f["metric"] == metric].copy()
    if rows.empty:
        raise NothingToDraw("no_rows", f"no held-out {metric} contrasts (nothing was fitted)")
    rows = rows[rows["interval_method"] == sorted(rows["interval_method"].unique())[0]]
    blocks = sorted(rows["block_length"].dropna().unique())
    colors = _colors(len(blocks), "ensemble_improvement")
    keys = sorted({f"{s} | {b} | {sub}" for s, b, sub in zip(
        rows["site_id"], rows["baseline_product"], rows["subset"], strict=True)})
    fig = _figure(7.0, 0.3 * len(keys) * max(1, len(blocks)) / 2 + 1.6, synthetic=synthetic)
    ax = fig.subplots()
    _style(ax)
    ax.axvline(0, color=MUTED, linewidth=0.8)
    for j, (b, c) in enumerate(zip(blocks, colors, strict=True)):
        off = (j - (len(blocks) - 1) / 2) * 0.22
        for _, row in rows[rows["block_length"] == b].iterrows():
            y = keys.index(f"{row['site_id']} | {row['baseline_product']} | {row['subset']}") + off
            if pd.notna(row["lower"]):
                ax.plot([row["lower"], row["upper"]], [y, y], color=c, linewidth=2)
            ax.scatter([row["estimate"]], [y], color=c, marker=MARKERS[j], s=22, zorder=3)
            right = max(float(row["estimate"]), float(row["upper"])
                        if pd.notna(row["upper"]) else float(row["estimate"]))
            ax.annotate(str(row["held_out_improvement"]), (right, y), xytext=(6, 0),
                        textcoords="offset points", fontsize=6, color=INK2, va="center")
        ax.plot([], [], color=c, marker=MARKERS[j], label=f"{int(b)}-day blocks")
    ax.set_yticks(range(len(keys)), keys, fontsize=7)
    unit = _uniq(rows, "units")
    ax.set_xlabel(f"{metric}(fitted) − {metric}(product) ({unit[0] if unit else ''})")
    ax.legend(fontsize=7, frameon=False)
    meta = _ensemble_meta(rows, _frame(summary), _span(rows["n_days"].dropna()))
    title = f"Held-out ensemble contrasts, {rows['ensemble_id'].iloc[0]}"
    notes = ["Negative favours the fitted ensemble; the label is the held_out_improvement "
             "column (supported only when the interval excludes zero in its favour)",
             "Every contrast is exploratory; no block length is adopted"]
    return FigureResult("ensemble_improvement", title, fig, rows,
                        compose_caption(title, meta, notes, synthetic=synthetic), meta)


def ensemble_disagreement(days: Any, summary: Any, *, synthetic: bool = False) -> FigureResult:
    """Member range per day and station: disagreement among members, not an interval."""
    f = _frame(days)
    rows = f[f["member_range_mm"].notna()].sort_values(["site_id", "date"]).copy()
    if rows.empty:
        raise NothingToDraw("no_rows", "no day with member values")
    sites = sorted(rows["site_id"].unique())
    colors = _colors(len(sites), "ensemble_disagreement")
    fig = _figure(8.0, 3.4, synthetic=synthetic)
    ax = fig.subplots()
    _style(ax)
    for s, c in zip(sites, colors, strict=True):
        r = rows[rows["site_id"] == s]
        seg = (pd.to_datetime(r["date"]).diff().dt.days.fillna(1) > 1).cumsum()
        for k, (_, g) in enumerate(r.groupby(seg)):
            ax.plot(g["date"], g["member_range_mm"], color=c, linewidth=1,
                    label=s if k == 0 else None)
    ax.set_ylabel(f"member range, max − min ({ET_UNITS})")
    ax.legend(fontsize=7, frameon=False, ncols=4)
    meta = _ensemble_meta(rows, _frame(summary),
                          str(int(rows[["site_id", "date"]].drop_duplicates().shape[0])))
    title = f"Member disagreement, {rows['ensemble_id'].iloc[0]}"
    notes = ["The range across members is disagreement, not a confidence or prediction "
             "interval", "Lines are not joined across days without member values"]
    return FigureResult("ensemble_disagreement", title, fig, rows,
                        compose_caption(title, meta, notes, synthetic=synthetic), meta)
