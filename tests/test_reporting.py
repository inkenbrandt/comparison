"""Step 17: figures from saved tables, captions, wording and the synthetic end-to-end run.

Gate: the synthetic run's report has internally consistent sample counts and makes no
claim about a real station.
"""

import re
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("matplotlib")

from etcompare.config import ExtractionSide, load_config  # noqa: E402
from etcompare.demo import write_demo  # noqa: E402
from etcompare.io.tables import read_table  # noqa: E402
from etcompare.plots import (  # noqa: E402
    SYNTHETIC_LABEL,
    CaptionMismatch,
    NothingToDraw,
    check_meta,
    conditional_errors,
    contrast_intervals,
    coverage,
    cumulative,
    ensemble_disagreement,
    ensemble_folds,
    ranking_stability,
    scatter_1to1,
    scorecard,
)
from etcompare.reporting import LanguageError, check_language, md_table  # noqa: E402
from etcompare.runner import run_stages  # noqa: E402


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    """The synthetic demo, run end to end once (fewer replicates, PNG only)."""
    root = tmp_path_factory.mktemp("demo")
    cfg = write_demo(root)
    text = cfg.read_text(encoding="utf-8")
    text = text.replace("n_replicates: 199", "n_replicates: 59")
    text = text.replace("  synthetic: true", "  synthetic: true\n  figure_formats: [png]\n"
                        "  comparisons: [syn_rank_measured, syn_closure, syn_fp_vs_region, "
                        "syn_coverage]")
    cfg.write_text(text, encoding="utf-8")
    manifest, records = run_stages(load_config(cfg), ["validate", "index", "extract",
                                                      "analyze", "report"], command="test")
    return {"root": root, "cfg": cfg, "out": root / "outputs", "manifest": manifest,
            "records": {r.unit_id: r for r in records}}


def table(demo, rel):
    return read_table(demo["out"] / rel)


# --------------------------------------------------------------------------- end to end

def test_demo_runs_and_records_every_status(demo):
    r = demo["records"]
    assert demo["manifest"].status == "completed"
    assert not [u for u, x in r.items() if x.status == "failed"]
    assert r["index:footprints_synthetic_fp"].status == "not_available"
    assert r["representativeness"].reason_code == "not_declared"
    assert r["raster_extraction"].reason_code == "not_declared"
    assert r["report"].status == "completed"
    s = table(demo, "ensembles/syn_ensemble_summary.parquet").frame.set_index("scope")
    assert s.loc["leave_one_site_out", "status"] == "held_out"
    assert s.loc["leave_one_year_out", "status"] == "declined"


def test_rerun_reuses_everything(demo):
    _, recs = run_stages(load_config(demo["cfg"]), ["validate", "index", "extract",
                                                    "analyze", "report"], command="test")
    st = {r.unit_id: r.status for r in recs if r.reason_code != "not_declared"}
    assert {s for u, s in st.items() if u != "validate"} <= {"cached", "not_available"}
    assert st["report"] == "cached"


def test_manifest_records_seeds_exclusions_and_versions(demo):
    m = demo["manifest"]
    assert {s["seed"] for s in m.seeds} == {20261019}
    assert any(e["stage"] == "cohort" and e["n"] > 0 for e in m.exclusion_counts)
    assert m.package_versions["matplotlib"]
    assert any(a["unit_id"] == "report" for a in m.analyses)
    assert len(m.config_hash) == 64 and "available" in m.git


def test_report_is_synthetic_on_every_page_and_caption(demo):
    md = (demo["out"] / "report" / "report.md").read_text(encoding="utf-8")
    html = (demo["out"] / "report" / "report.html").read_text(encoding="utf-8")
    assert md.startswith(f"# etcompare synthetic demonstration ({SYNTHETIC_LABEL})")
    assert md.rstrip().endswith(f"**{SYNTHETIC_LABEL}: end of report.**")
    captions = re.findall(r"^\*(.+)\*$", md, flags=re.MULTILINE)
    figs = [c for c in captions if "n_days" in c and "Units:" in c]
    assert figs and all(c.startswith(SYNTHETIC_LABEL) for c in figs)
    assert html.count("class='banner'") == 2
    for text in (md, html):
        assert not re.search(r"\bUS-[A-Z0-9]{3}\b", text)               # no real station
    assert "<img" in html and "src='http" not in html and "src=\"http" not in html


def test_report_sample_counts_match_the_tables(demo):
    md = (demo["out"] / "report" / "report.md").read_text(encoding="utf-8")
    p = table(demo, "cohorts/syn_rank_measured_paired.parquet").frame
    n = p[["site_id", "date"]].drop_duplicates()
    assert f"{len(n)} paired site-days" in md
    m = table(demo, "cohorts/syn_rank_measured__syn_metrics_metrics.parquet").frame
    d = m[(m["product"] == "daily_pairs") & (m["metric"] == "mae")]
    per = p.groupby(["site_id", "model"]).size()
    for r in d.itertuples():
        assert r.n_days == per[(r.scope, r.model)]
        assert re.search(rf"\| {r.scope} \| {r.model} \|[^\n]*\| {r.n_days} \|", md)
    rows = pd.read_csv(next((demo["out"] / "report" / "figures" / "syn_rank_measured")
                            .glob("scatter_*_rows.csv")))
    assert len(rows) == len(p)


def test_skipped_analyses_table(demo):
    s = table(demo, "report/skipped_analyses.parquet").frame
    wf = s[s["source"] == "workflow"]
    assert {"not_declared", "input_missing"} <= set(wf["reason_code"])
    t = s[s["source"] == "table"]
    assert "no_acquisition_metadata" in set(t["reason_code"])          # satellite support
    assert "declined" in set(t["status"])                              # leave-one-year-out
    assert (t.loc[t["analysis"] == "water_balance", "status"] == "unknown").any()


def test_open_decisions_are_listed(demo):
    md = (demo["out"] / "report" / "report.md").read_text(encoding="utf-8")
    sec = md.split("## Open decisions these outputs depend on")[1].split("\n## ")[0]
    assert "DEMO-1 (provisional)" in sec and "DEMO-2 (open)" in sec
    assert "none is adopted" in sec and "not_assessed" in sec
    assert "exploratory" in sec and "unknown, never zero" in sec


# --------------------------------------------------------------------------- figures

def test_scorecard_rows_and_caption(demo):
    m = table(demo, "cohorts/syn_rank_measured__syn_metrics_metrics.parquet").frame
    sub = m[m["scope"] == "XX-SY1"]
    fr = scorecard(sub, synthetic=True)
    exp = sub[(sub["product"] == "daily_pairs") & sub["metric"].isin(
        ["mae", "rmse", "mbe", "pearson_r"])]
    pd.testing.assert_frame_equal(fr.rows.reset_index(drop=True), exp.reset_index(drop=True))
    for field in ("Units:", "Reference: et_corrected_measured",
                  "Extraction: upstream_footprint_weighted@synthetic_fp", "Scale:",
                  "Cohort: syn_rank_measured:common", "n_sites = 1"):
        assert field in fr.caption
    assert f"n_days = {int(exp['n_days'].iloc[0])}" in fr.caption
    assert fr.caption.startswith(SYNTHETIC_LABEL)
    with pytest.raises(ValueError, match="one site"):
        scorecard(m)
    with pytest.raises(NothingToDraw):
        scorecard(m.iloc[0:0])


def test_scatter_draws_every_pair_and_the_one_to_one_line(demo):
    p = table(demo, "cohorts/syn_rank_measured_paired.parquet").frame
    fr = scatter_1to1(p)
    assert len(fr.rows) == len(p)
    ax = fr.figure.axes[0]
    line = ax.lines[0]
    np.testing.assert_array_equal(line.get_xdata(), line.get_ydata())
    pts = sum(len(c.get_offsets()) for a in fr.figure.axes for c in a.collections)
    assert pts == len(p)


def test_cumulative_never_joins_across_a_gap(demo):
    ce = table(demo, "cohorts/syn_closure__syn_metrics_cumulative_error.parquet").frame
    ce = ce[(ce["site_id"] == "XX-SY1") & (ce["reference_variant"] == "et_raw")]
    fr = cumulative(ce)
    bottom = fr.figure.axes[1]
    n_seg = ce.groupby("model")["segment"].nunique()
    assert (n_seg > 1).all()
    assert len(bottom.lines) == n_seg.sum() + 1                    # one per segment + zero
    for line in bottom.lines[:-1]:                                 # last is the zero line
        x = pd.to_datetime(pd.Series(line.get_xdata()))
        if len(x) > 1:
            assert (x.diff().dropna() <= pd.Timedelta(days=1)).all()
    gaps = ce[ce["missing_days_before"].fillna(0) > 0]
    n_spans = len(gaps.drop_duplicates("date"))
    assert f"{n_spans} missing period(s) shaded" in fr.caption
    assert "not joined" in fr.caption


def test_contrast_intervals_show_status_without_bounds(demo):
    iv = table(demo, "cohorts/syn_fp_vs_region__syn_bootstrap_intervals.parquet").frame
    iv = iv.copy()
    sel = (iv["contrast_dimension"] == "extraction_side") & (iv["metric"] == "mae")
    i = iv.index[sel][0]
    iv.loc[i, ["lower", "upper"]] = np.nan
    iv.loc[i, "status"] = "insufficient_support"
    fr = contrast_intervals(iv, dimension="extraction_side")
    texts = [t.get_text() for a in fr.figure.axes for t in a.texts]
    assert "insufficient_support" in texts
    assert "no block length is adopted" in fr.caption
    assert set(fr.rows["contrast_dimension"]) == {"extraction_side"}
    with pytest.raises(NothingToDraw):
        contrast_intervals(iv, dimension="reference_variant")


def test_conditional_errors_mark_sparse_bins(demo):
    c = table(demo, "cohorts/syn_closure__syn_diagnostics_conditional.parquet").frame
    c = c[(c["site_id"] == "XX-SY1") & (c["reference_variant"] == "et_raw")].copy()
    v = c[c["variable"] == "vpd"]
    first = v["bin_index"].max()
    c.loc[(c["variable"] == "vpd") & (c["bin_index"] == first), ["status", "value"]] = (
        "sparse_bin", np.nan)
    fr = conditional_errors(c, variable="vpd")
    texts = " ".join(t.get_text() for t in fr.figure.axes[0].texts)
    assert "sparse_bin" in texts
    assert "not causes" in fr.caption


def test_coverage_caption_takes_variants_from_the_paired_table(demo):
    mem = table(demo, "cohorts/syn_coverage_membership.parquet")
    p = table(demo, "cohorts/syn_coverage_paired.parquet")
    fr = coverage(mem, p)
    assert "Reference: et_corrected_inclusive" in fr.caption
    assert len(fr.rows) == len(mem.frame)
    other = table(demo, "cohorts/syn_closure_paired.parquet")
    with pytest.raises(CaptionMismatch):
        coverage(mem, other)


def test_caption_disagreeing_with_the_declaration_raises(demo):
    cfg = load_config(demo["cfg"])
    p = table(demo, "cohorts/syn_closure_paired.parquet").frame
    fr = scatter_1to1(p[p["reference_variant"] == "et_raw"])
    spec = cfg.comparison("syn_closure")
    check_meta(fr.meta, spec)
    with pytest.raises(CaptionMismatch, match="reference variant"):
        check_meta(fr.meta, replace(spec, reference_variants=("et_corrected_measured",
                                                              "et_corrected_inclusive")))
    with pytest.raises(CaptionMismatch, match="extraction side"):
        check_meta(fr.meta, replace(spec, sides=(ExtractionSide("source_region_mean",
                                                                "synthetic_fp"),)))
    with pytest.raises(CaptionMismatch, match="rows belong to"):
        check_meta(fr.meta, cfg.comparison("syn_rank_measured"))


def test_ranking_stability_table(demo):
    rk = table(demo, "cohorts/syn_rank_measured__syn_ranking_rankings.parquet").frame
    fr = ranking_stability(rk)
    assert fr.figure is None and len(fr.table) == len(fr.rows)
    assert set(fr.table["leader status"]) <= {"unique_leader", "no_unique_leader",
                                              "not_assessed"}
    assert "not the probability that a model is best" in fr.caption


def test_ensemble_figures(demo):
    s = table(demo, "ensembles/syn_ensemble_summary.parquet").frame
    folds = table(demo, "ensembles/syn_ensemble_folds.parquet")
    fr = ensemble_folds(folds, s)
    labels = [t.get_text() for t in fr.figure.axes[0].get_legend().get_texts()]
    assert any(x.startswith("held out") for x in labels)
    assert any(x.startswith("exploratory") for x in labels)
    assert any(x.startswith("declined") for x in labels)
    days = table(demo, "ensembles/syn_ensemble_days.parquet")
    fr = ensemble_disagreement(days, s)
    assert "disagreement, not a confidence or prediction interval" in fr.caption
    assert "interval" not in fr.figure.axes[0].get_ylabel()


# --------------------------------------------------------------------------- wording

def test_guarded_words_need_a_licence():
    with pytest.raises(LanguageError, match="network"):
        check_language("network summaries of one station")
    check_language("network summaries", {"network"})
    with pytest.raises(LanguageError, match="validated"):
        check_language("the model is validated", {"network", "significant"})
    with pytest.raises(LanguageError, match="significant"):
        check_language("a significantly lower error")
    check_language("not_network_evidence; single_site; validation issues")


def test_md_table_escapes_pipes():
    t = md_table(pd.DataFrame({"a": ["x|y"], "b": [1.234567]}))
    assert "x\\|y" in t and "1.235" in t
