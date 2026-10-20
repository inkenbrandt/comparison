"""Closure, coverage and environmental diagnostics (guide step 12).

Expected values are computed independently in the tests: bin samples are
selected with numpy masks from the cohort's paired rows and scored with
explicit numpy formulas; closure magnitudes come from the synthetic ET and
closure factors; sector boundaries are written out by hand.
"""

import numpy as np
import pandas as pd
import pytest

from etcompare.cohorts import build_cohort
from etcompare.config import (
    BootstrapSpec,
    ComparisonSpec,
    DiagnosticsSpec,
    DiagnosticVariableSpec,
    ExtractionSide,
    FootprintSetSpec,
    MetricSpec,
    NearZeroTolerance,
    config_from_dict,
)
from etcompare.diagnostics import (
    assign_bins,
    diagnose_comparison,
    reference_rank_changes,
    resolve_bins,
    sector_bounds,
    training_quantile_edges,
    wind_sector,
)
from etcompare.errors import ContractError
from etcompare.io.tables import read_table, write_table
from etcompare.schemas import RANKING_SENSITIVITY, CanonicalTable

TOL = NearZeroTolerance(mean=1e-9, std=1e-12)
FP = ExtractionSide("footprint_conservative", "SET")
PIX = ExtractionSide("pixel_window_3x3", "none")
SETS = [FootprintSetSpec("SET", "x", weighting_method="nldas",
                         value_semantics="normalized_cell_weight",
                         temporal_weighting="reference_et_weighted")]
MODELS = ("aaa", "bbb", "ccc")
DATES = pd.date_range("2025-01-01", periods=240, freq="D")
SITE = "US-AAA"


def ext(model, dates, vals, side=FP, retained=1.0):
    n = len(dates)
    return pd.DataFrame({
        "site_id": SITE, "date": dates, "period": "day", "model": model,
        "extraction_method": side.extraction_method, "footprint_set_id": side.footprint_set_id,
        "footprint_method": "m", "et_mm": np.broadcast_to(np.asarray(vals, float), (n,)),
        "fp_weight_sum": 1.0,
        "fp_weight_retained_frac": (np.broadcast_to(np.asarray(retained, float), (n,))
                                    if side.footprint_set_id != "none" else np.nan),
        "fp_weight_retained_denominator": None, "support_id": None, "day_alignment": "declared",
        "model_asset_id": None, "footprint_asset_id": None, "source_path": None,
    })


def station(dates=DATES, raw=None, cf=None, filled=None, fill_value=3.0, **src):
    n = len(dates)
    rng = np.random.default_rng(1)
    raw = rng.uniform(1, 6, n) if raw is None else np.asarray(raw, float)
    cf = np.full(n, 1.25) if cf is None else np.asarray(cf, float)
    filled = np.zeros(n, bool) if filled is None else np.asarray(filled, bool)
    corr = np.where(filled, fill_value, raw * cf)
    frame = pd.DataFrame({
        "site_id": SITE, "date": dates, "et_raw_mm": np.where(filled, np.nan, raw),
        "et_corrected_mm": corr, "gap_filled": filled, "closure_factor": cf,
        "raw_available": ~filled,
        "reference_provenance": np.where(filled, "corrected_filled", "corrected_from_raw"),
    })
    for k, v in src.items():
        frame[f"src_{k}"] = v
    return frame


def met(n=None, seed=2):
    n = len(DATES) if n is None else n
    rng = np.random.default_rng(seed)
    return {"vpd": rng.uniform(0.1, 3.5, n), "ws": rng.uniform(0.5, 4.0, n),
            "h": rng.normal(15, 30, n), "rn": rng.uniform(0, 220, n),
            "ebr_corr": rng.uniform(0.6, 0.98, n)}


def var(name, column=None, **kw):
    base = dict(name=name, source="station", column=column or f"src_{name}", units="kPa",
                meaning=f"daily {name}", status="provisional")
    base.update(kw)
    return DiagnosticVariableSpec(**base)


VARS = {
    "vpd": var("vpd", edges=[0, 1, 2, 4]),
    "ws": var("ws", units="m s-1", edges=[0, 1.5, 3, 5]),
    "h": var("h", units="W m-2", edges=[-np.inf, 0, 25, np.inf]),
    "rn": var("rn", units="W m-2"),
    "ebr_corr": var("ebr_corr", units="1", edges=[0, 0.8, 1.0], measured_days_only=True),
}


def dspec(variables=VARS, **kw):
    base = dict(diagnostics_id="dg", metric_set="mset", min_bin_days=10, variables=variables,
                metrics=("mbe", "mae", "rmse"), gap_filled_fraction_edges=(0, 0.5, 1),
                closure_metadata=("ebr_corr",), sensible_heat_variable="h",
                net_radiation_variable="rn")
    base.update(kw)
    return DiagnosticsSpec(**base)


def mspec():
    return MetricSpec("mset", metrics=("mbe", "mae", "rmse", "pearson_r"), daily_tolerance=TOL)


def cspec(kind="common_date", variants=("et_corrected_inclusive",), sides=(FP,), **kw):
    return ComparisonSpec("cmp", kind, MODELS, variants, sides, **kw)


def model_frames(obs, sides=(FP,), seed=3, scale=(1.0, 1.1, 0.8), retained=None):
    rng = np.random.default_rng(seed)
    truth = obs["et_corrected_mm"].to_numpy()
    out = []
    for i, m in enumerate(MODELS):
        vals = truth * scale[i] + rng.normal(0, 0.4, len(truth))
        for s in sides:
            out.append(ext(m, obs["date"], vals, side=s,
                           retained=1.0 if retained is None else retained[i]))
    return out


def run(obs, frames=None, spec=None, d=None, bootstrap=None):
    c = build_cohort(obs, frames if frames is not None else model_frames(obs),
                     spec or cspec(), footprint_sets=SETS)
    return c, diagnose_comparison(c, d or dspec(), mspec(), station_daily=obs,
                                  extractions=frames if frames is not None else model_frames(obs),
                                  bootstrap=bootstrap)


def sel(frame, **match):
    for k, v in match.items():
        frame = frame[frame[k] == v]
    return frame.reset_index(drop=True)


def not_available(r):
    """{(analysis, variable): reason_code} of the not_available rows (one per variable)."""
    a = r.availability.frame
    a = a[(a["status"] == "not_available") & (a["cohort_id"] == "all")]
    return dict(zip(zip(a["analysis"], a["variable"], strict=True), a["reason_code"], strict=True))


def numpy_metrics(o, p):
    e = p - o
    return {"mbe": e.mean(), "mae": np.abs(e).mean(), "rmse": np.sqrt((e ** 2).mean())}


# --------------------------------------------------------------------------- gates

def test_unknown_meteorology_skips_only_the_affected_diagnostic():
    obs = station(**met())
    variables = dict(VARS)
    variables["vpd"] = var("vpd", column="src_vpd_missing", edges=[0, 1, 2, 4])
    variables["gaps"] = var("gaps", units="count", status="unverified", edges=[0, 10, 48],
                            note="counts and denominators unresolved (contract 10)")
    variables["wd"] = var("wd", units="degree", kind="direction", n_sectors=8,
                          column="src_wd_absent")
    _, r = run(obs, d=dspec(variables=variables))
    a = r.availability.frame
    na = not_available(r)
    assert na[("conditional_error", "vpd")] == "column_missing"
    assert na[("conditional_error", "gaps")] == "variable_unverified"
    assert na[("conditional_error", "wd")] == "column_missing"
    assert na[("satellite_support", "acquisition_flags")] == "no_acquisition_metadata"
    assert na[("wind_direction_sector", "none")] == "variable_not_declared"
    c = r.conditional.frame
    assert not c["variable"].isin(["vpd", "gaps", "wd"]).any()
    ran = set(c["variable"])
    assert {"ws", "h", "ebr_corr", "season", "month", "gap_filled_fraction",
            "reference_provenance"} <= ran
    assert (c[c["variable"] == "ws"]["status"] == "ok").any()
    assert len(r.closure.frame) and len(r.regressions.frame)
    # Every other diagnostic is available.
    av = a[a["status"] == "available"]
    assert {"ws", "h", "season"} <= set(av.loc[av["analysis"] == "conditional_error", "variable"])


def test_non_numeric_and_empty_columns_skip_only_their_diagnostic():
    m = met()
    obs = station(**m, text=np.array(["x"] * len(DATES)), empty=np.full(len(DATES), np.nan))
    variables = dict(VARS, text=var("text", edges=[0, 1]), empty=var("empty", edges=[0, 1]))
    _, r = run(obs, d=dspec(variables=variables))
    na = not_available(r)
    assert na[("conditional_error", "text")] == "non_numeric"
    assert na[("conditional_error", "empty")] == "no_values"
    assert (r.conditional.frame["variable"] == "vpd").any()


def test_sparse_bins_have_a_status_not_a_value():
    m = met()
    m["vpd"] = np.where(np.arange(len(DATES)) < 5, 3.0, 0.5)  # 5 days in [2, 4]; none in [1, 2)
    obs = station(**m)
    _, r = run(obs)
    c = sel(r.conditional.frame, variable="vpd", metric="mae")
    by_bin = c.groupby("bin_index").first()
    assert by_bin.loc[2, "status"] == "sparse_bin" and pd.isna(by_bin.loc[2, "value"])
    assert by_bin.loc[2, "n_days"] == 5 and "min_bin_days 10" in by_bin.loc[2, "status_detail"]
    assert by_bin.loc[1, "status"] == "empty_bin" and by_bin.loc[1, "n_days"] == 0
    assert by_bin.loc[0, "status"] == "ok" and by_bin.loc[0, "n_days"] == len(DATES) - 5
    assert (c["min_bin_days"] == 10).all()
    assert c.loc[c["status"] != "ok", "value"].isna().all()


def test_wind_directions_wrap_at_0_and_360():
    d = [359.0, 1.0, 0.0, 360.0, 22.4999, 22.5, 337.5, 337.4999, 180.0, 90.0, np.nan]
    assert wind_sector(d, 8).tolist() == [0, 0, 0, 0, 0, 1, 0, 7, 4, 2, -1]
    assert wind_sector([359.0, 1.0, 45.0, 315.0], 4).tolist() == [0, 0, 1, 0]  # 45 opens E
    assert sector_bounds(8)[0] == (337.5, 22.5) and sector_bounds(8)[1] == (22.5, 67.5)
    assert sector_bounds(4)[3] == (225.0, 315.0)
    for bad in ([-1.0], [360.5]):
        with pytest.raises(ValueError, match=r"\[0, 360\]"):
            wind_sector(bad, 8)
    with pytest.raises(ValueError):
        wind_sector([1.0], 7)
    # Through the diagnostics: 359 and 1 degree days share the north bin.
    n = len(DATES)
    wd = np.where(np.arange(n) % 2 == 0, 359.0, 1.0)
    wd[:20] = 180.0
    obs = station(**met(), wd=wd)
    variables = dict(VARS, wd=var("wd", units="degree", kind="direction", n_sectors=8))
    _, r = run(obs, d=dspec(variables=variables, wind_direction_variable="wd"))
    c = sel(r.conditional.frame, variable="wd", metric="mae", model="aaa")
    counts = c.set_index("bin_index")["n_days"]
    assert counts[0] == n - 20 and counts[4] == 20 and counts.drop([0, 4]).eq(0).all()
    assert c.set_index("bin_index").loc[0, "bin_label"] == "sector 0 [337.5, 22.5)"
    a = r.availability.frame
    assert not ((a["analysis"] == "wind_direction_sector") & (a["status"] == "not_available")
                ).any()
    # Directions outside [0, 360] skip only the wind diagnostic.
    obs2 = station(**met(), wd=np.full(n, 400.0))
    _, r2 = run(obs2, d=dspec(variables=variables, wind_direction_variable="wd"))
    na = not_available(r2)
    assert na[("conditional_error", "wd")] == "out_of_range"
    assert (r2.conditional.frame["variable"] == "vpd").any()


def test_bin_edges_come_from_config_not_from_errors():
    obs = station(**met())
    _, r1 = run(obs, frames=model_frames(obs, seed=3))
    _, r2 = run(obs, frames=model_frames(obs, seed=99, scale=(2.0, 0.3, 1.7)))
    cols = ["variable", "bin_index", "bin_label", "bin_lower", "bin_upper", "n_days"]
    b1 = r1.conditional.frame[cols].drop_duplicates().reset_index(drop=True)
    b2 = r2.conditional.frame[cols].drop_duplicates().reset_index(drop=True)
    pd.testing.assert_frame_equal(b1, b2)
    assert r1.bins["vpd"].edges == (0.0, 1.0, 2.0, 4.0)
    assert not np.allclose(sel(r1.conditional.frame, variable="vpd", metric="mae")["value"],
                           sel(r2.conditional.frame, variable="vpd", metric="mae")["value"],
                           equal_nan=True)
    # Training quantiles: the variable's own values on the declared period only.
    q = var("vpd", quantiles=[0, 0.5, 1], training_start="2025-01-01",
            training_end="2025-03-31")
    train = obs.loc[obs["date"] <= "2025-03-31", "src_vpd"].to_numpy()
    expected = tuple(np.quantile(train, [0, 0.5, 1]))
    assert training_quantile_edges(q, obs) == pytest.approx(expected)
    qspec = dspec(variables={"vpd": q}, closure_metadata=(), sensible_heat_variable=None,
                  net_radiation_variable=None)
    bins = resolve_bins(qspec, obs)
    assert bins["vpd"].source == "training_quantiles"
    assert bins["vpd"].edges == pytest.approx(expected)
    shifted = obs.assign(et_corrected_mm=obs["et_corrected_mm"] * 3)
    assert resolve_bins(qspec, shifted)["vpd"].edges == bins["vpd"].edges
    with pytest.raises(ContractError, match="not strictly increasing"):
        training_quantile_edges(q, obs.assign(src_vpd=1.0))


def test_assign_bins_edges():
    e = [0.0, 1.0, 2.0]
    assert assign_bins([0, 0.999, 1.0, 2.0, 2.0001, -0.1, np.nan], e).tolist() == [
        0, 0, 1, 1, -1, -1, -2]


def test_closure_magnitude_reproduces_known_factor_and_excludes_filled_days():
    n = len(DATES)
    rng = np.random.default_rng(5)
    raw = rng.uniform(1, 6, n)
    cf = np.where(DATES.month <= 2, 1.2, 1.5)
    filled = np.zeros(n, bool)
    filled[10:25] = True  # January, with absurd fill values and factors that must not count
    cf_meta = np.where(filled, 9.0, cf)
    obs = station(raw=raw, cf=cf_meta, filled=filled, fill_value=50.0, **met())
    _, r = run(obs)
    cm = sel(r.closure.frame, day_set="station_record")
    measured = ~filled
    row = sel(cm, period_type="all", quantity="correction_mm").iloc[0]
    corr = raw * cf
    np.testing.assert_allclose(row["mean"], (corr - raw)[measured].mean(), rtol=1e-12)
    np.testing.assert_allclose(row["max"], (corr - raw)[measured].max(), rtol=1e-12)
    assert row["n_days"] == measured.sum() and row["n_days_filled_excluded"] == 15
    ratio = sel(cm, period_type="all", quantity="correction_ratio").iloc[0]
    np.testing.assert_allclose([ratio["min"], ratio["max"]], [1.2, 1.5], rtol=1e-12)
    for q in ("correction_ratio", "closure_factor"):
        jan = sel(cm, period_type="month", period_label="2025-01", quantity=q).iloc[0]
        np.testing.assert_allclose([jan["mean"], jan["std"]], [1.2, 0.0], atol=1e-12)
        assert jan["n_days"] == 31 - 15 and jan["n_days_filled_excluded"] == 15
    mar = sel(cm, period_type="month", period_label="2025-03", quantity="correction_mm").iloc[0]
    m3 = (DATES.month == 3)
    np.testing.assert_allclose(mar["median"], np.median((0.5 * raw)[m3]), rtol=1e-12)
    djf = sel(cm, period_type="season", period_label="2025-DJF", quantity="closure_factor")
    assert djf["n_days"].iloc[0] == 59 - 15
    meta = sel(cm, period_type="all", quantity="ebr_corr").iloc[0]
    np.testing.assert_allclose(meta["mean"], obs["src_ebr_corr"][measured].mean(), rtol=1e-12)
    assert "reference_sensitivity_not_uncertainty" in row["flags"]
    # Cohort day set: identical here because every station day is a cohort day.
    coh = sel(r.closure.frame, day_set="cohort", period_type="all", quantity="correction_mm")
    assert coh["n_days"].iloc[0] == row["n_days"]


def test_provenance_subsets_use_identical_days_across_models():
    n = len(DATES)
    filled = (np.arange(n) % 5 == 0)
    obs = station(filled=filled, **met())
    c, r = run(obs)
    p = c.paired.frame
    f = sel(r.conditional.frame, variable="reference_provenance", metric="rmse")
    for k, n_exp in ((0, n), (1, int((~filled).sum())), (2, int(filled.sum()))):
        rows = sel(f, bin_index=k)
        assert len(rows) == len(MODELS)
        assert (rows["n_days"] == n_exp).all() and rows["bin_days_shared"].all()
    for m in MODELS:
        pm = p[p["model"] == m]
        for k, mask in ((1, pm["reference_provenance"] == "corrected_from_raw"),
                        (2, pm["reference_provenance"] == "corrected_filled")):
            exp = numpy_metrics(pm.loc[mask, "observed_mm"].to_numpy(),
                                pm.loc[mask, "modeled_mm"].to_numpy())["rmse"]
            got = sel(f, bin_index=k, model=m)["value"].iloc[0]
            np.testing.assert_allclose(got, exp, rtol=1e-12)
    filled_rows = sel(f, bin_index=2)
    assert filled_rows["flags"].str.contains("not_independent_validation").all()
    assert filled_rows["flags"].str.contains("shared_gridmet_drivers").all()
    assert sel(f, bin_index=0)["flags"].str.contains("includes_gap_filled_days").all()
    pp = sel(r.provenance.frame, period_type="all").iloc[0]
    assert pp["n_days_corrected_filled"] == filled.sum()
    np.testing.assert_allclose(pp["gap_filled_fraction"], filled.mean())
    # gap_filled_fraction conditioning uses the per-month share
    jan = sel(r.provenance.frame, period_type="month", period_label="2025-01").iloc[0]
    np.testing.assert_allclose(jan["gap_filled_fraction"], filled[:31].mean())


def test_conditional_metrics_equal_numpy_on_bin_samples():
    m = met()
    obs = station(**m)
    c, r = run(obs)
    p = c.paired.frame.merge(obs[["date", "src_vpd", "src_h"]], on="date")
    f = r.conditional.frame
    for name, edges in (("vpd", [0, 1, 2, 4]), ("h", [-np.inf, 0, 25, np.inf])):
        col = f"src_{name}"
        for k in range(len(edges) - 1):
            last = k == len(edges) - 2
            lo, hi = edges[k], edges[k + 1]
            inside = (p[col] >= lo) & ((p[col] <= hi) if last else (p[col] < hi))
            for mod in MODELS:
                s = p[inside & (p["model"] == mod)]
                exp = numpy_metrics(s["observed_mm"].to_numpy(), s["modeled_mm"].to_numpy())
                for metric, v in exp.items():
                    got = sel(f, variable=name, bin_index=k, model=mod, metric=metric)
                    assert got["n_days"].iloc[0] == len(s)
                    np.testing.assert_allclose(got["value"].iloc[0], v, rtol=1e-12)
    # Every day is accounted for: bins plus remainders sum to the series.
    for (name, _mod), g in sel(f, metric="mae").groupby(["variable", "model"]):
        if name == "reference_provenance":
            continue
        assert g["n_days"].sum() == g["n_days_series"].iloc[0], name
    h = sel(f, variable="h", bin_index=0, metric="mae")
    assert h["flags"].str.contains("negative_daily_mean_h").all()
    assert h["flags"].str.contains("daily_mean_h_indicator").all()
    assert r.attributes["interpretation"].endswith("not a causal explanation")


def test_measured_days_only_variable_excludes_filled_days():
    filled = np.zeros(len(DATES), bool)
    filled[:30] = True
    obs = station(filled=filled, **met())
    _, r = run(obs)
    f = sel(r.conditional.frame, variable="ebr_corr", metric="mae", model="aaa")
    ex = sel(f, bin_index=-3).iloc[0]
    assert ex["status"] == "excluded_provenance" and ex["n_days"] == 30
    assert f.loc[f["bin_index"] >= 0, "n_days_reference_filled"].eq(0).all()


def test_extraction_variable_can_differ_across_models():
    obs = station(**met())
    n = len(DATES)
    retained = [np.ones(n), np.where(np.arange(n) < 100, 0.5, 1.0), np.ones(n)]
    frames = model_frames(obs, sides=(FP, PIX), retained=retained)
    variables = dict(VARS, retained=var("retained", source="extraction",
                                        column="fp_weight_retained_frac", units="1",
                                        edges=[0, 0.8, 1.0]))
    spec = cspec(kind="paired_method", variants=("et_corrected_measured",), sides=(FP, PIX),
                 common_models=True)
    _, r = run(obs, frames=frames, spec=spec, d=dspec(variables=variables))
    f = sel(r.conditional.frame, variable="retained", metric="mae",
            extraction_method="footprint_conservative")
    low = sel(f, bin_index=0).set_index("model")
    assert low.loc["bbb", "n_days"] == 100 and low.loc["aaa", "n_days"] == 0
    assert not low["bin_days_shared"].any() and low.loc["bbb", "n_days_bin_cohort"] == 100
    assert low["flags"].str.contains("bin_days_differ_across_models").all()
    na = r.availability.frame
    side = na[(na["variable"] == "retained") & (na["status"] == "not_available")]
    assert set(side["extraction_method"]) == {"pixel_window_3x3"}
    assert (side["reason_code"] == "no_values").all()


def test_net_radiation_coupling_reports_raw_alongside_corrected():
    m = met()
    obs = station(**m)
    rng = np.random.default_rng(8)
    raw = obs["et_raw_mm"].to_numpy()
    frames = [ext(mod, DATES, raw + 0.01 * m["rn"] + rng.normal(0, 0.05, len(DATES)))
              for mod in MODELS]
    spec = cspec(kind="paired_reference", variants=("et_raw", "et_corrected_measured"),
                 common_models=True)
    _, r = run(obs, frames=frames, spec=spec)
    g = r.regressions.frame
    raw_rows = sel(g, reference_variant="et_raw")
    assert (raw_rows["coupling"] == "uncorrected_reference").all()
    for mod in MODELS:
        e = frames[MODELS.index(mod)]["et_mm"].to_numpy() - raw
        slope = np.polyfit(m["rn"], e, 1)[0]
        np.testing.assert_allclose(sel(raw_rows, model=mod)["slope"].iloc[0], slope, rtol=1e-9)
    cor = sel(g, reference_variant="et_corrected_measured")
    assert (cor["coupling"] == "energy_balance_corrected_reference").all()
    assert cor["flags"].str.contains("mathematical_coupling").all()
    # A corrected-only cohort says the uncorrected version is not available.
    _, r2 = run(obs, frames=frames)
    a = r2.availability.frame
    row = a[(a["analysis"] == "net_radiation_coupling") & (a["status"] == "not_available")]
    assert row["reason_code"].tolist() == ["cohort_lacks_variant"]
    assert (r2.regressions.frame["reference_variant"] == "et_corrected_inclusive").all()


def test_bin_intervals_reuse_the_block_scheme_at_every_length():
    obs = station(**met())
    bs = BootstrapSpec("bs", "mset", block_lengths=(7, 14), seed=11, n_replicates=200,
                       confidence_level=0.9, min_blocks=5, segment_boundary="none")
    d = dspec(bootstrap="bs")
    _, r1 = run(obs, d=d, bootstrap=bs)
    _, r2 = run(obs, d=d, bootstrap=bs)
    pd.testing.assert_frame_equal(r1.conditional.frame, r2.conditional.frame)
    f = r1.conditional.frame
    assert set(f["block_length"]) == {7, 14}
    whole = sel(f, variable="reference_provenance", bin_index=0, metric="mbe", model="aaa")
    assert set(whole["block_length"]) == {7, 14} and (whole["interval_status"] == "ok").all()
    assert (whole["lower"] <= whole["value"]).all() and (whole["value"] <= whole["upper"]).all()
    assert whole["n_blocks"].tolist() == [240 // 7, 240 // 14]
    # A scattered bin (alternate days) has runs of one day: no full blocks.
    m = met()
    m["vpd"] = np.where(np.arange(len(DATES)) % 2 == 0, 0.5, 1.5)
    _, r3 = run(station(**m), d=d, bootstrap=bs)
    sc = sel(r3.conditional.frame, variable="vpd", bin_index=0, metric="mae", model="aaa")
    assert (sc["interval_status"] == "insufficient_support").all() and sc["lower"].isna().all()
    assert (sc["status"] == "ok").all() and sc["value"].notna().all()
    sparse = sel(r3.conditional.frame, variable="vpd", bin_index=2, metric="mae")
    assert (sparse["interval_status"] == "no_estimate").all()
    with pytest.raises(ContractError, match="supply exactly that bootstrap"):
        run(obs, d=d)


def test_tables_round_trip_through_parquet(tmp_path):
    filled = np.zeros(len(DATES), bool)
    filled[::7] = True
    obs = station(filled=filled, **met())
    _, r = run(obs)
    for name, t in r.tables().items():
        path = write_table(t, tmp_path / f"{name}.parquet")
        back = read_table(path)
        assert back.schema is t.schema
        pd.testing.assert_frame_equal(back.frame, t.frame)
    # Keys separate cohorts, variants, sides, variables and bins.
    f = r.conditional.frame
    assert not f.duplicated(list(r.conditional.schema.primary_key)).any()


def test_results_are_immutable_copies():
    obs = station(**met())
    _, r = run(obs)
    f = r.conditional.frame
    f.loc[:, "value"] = -1.0
    assert not (r.conditional.frame["value"] == -1).all()
    with pytest.raises(TypeError):
        r.attributes["x"] = 1  # type: ignore[index]


def test_reference_rank_changes_label_effects_from_sensitivity_rows():
    def ctx(cmp, variant):
        return f"{cmp}|all|US-AAA|daily:none:none:daily_pairs|{variant}|fp@SET"

    rows = []
    for a, b, same in ((ctx("cl", "et_raw"), ctx("cl", "et_corrected_measured"), True),
                       (ctx("ms", "et_corrected_measured"), ctx("in", "et_corrected_inclusive"),
                        False),
                       (ctx("x", "et_raw"), ctx("y", "et_corrected_measured"), False)):
        rows.append({"ranking_id": "rk", "dimension": "reference_variant", "context_a": a,
                     "context_b": b, "metric": "mae", "model": "aaa", "rank_a": 1.0,
                     "rank_b": 3.0, "rank_change": 2.0, "n_models": 3, "same_cohort": same,
                     "comparison_a": a.split("|")[0], "comparison_b": b.split("|")[0],
                     "n_days_a": 10, "n_days_b": 12, "n_periods_a": None, "n_periods_b": None})
    rows.append({**rows[0], "dimension": "extraction_method", "context_b": "other"})
    sens = CanonicalTable.build(pd.DataFrame(rows), RANKING_SENSITIVITY, {})
    out = reference_rank_changes(sens, "dg").frame
    assert len(out) == 3
    assert out.set_index("context_a")["effect"].to_dict() == {
        ctx("cl", "et_raw"): "closure",
        ctx("ms", "et_corrected_measured"): "gap_filling_and_cohort_days",
        ctx("x", "et_raw"): "closure_and_cohort_days"}
    assert (out["rank_change"] == 2.0).all()
    with pytest.raises(ContractError):
        reference_rank_changes(CanonicalTable.build(out, reference_rank_changes(
            sens, "dg").schema, {}), "dg")


# --------------------------------------------------------------------------- config

def _cfg_dict(diag=None):
    return {
        "products": {m: {"units": "mm/day", "day_definition": "unknown",
                         "temporal_support": "interpolated_daily"} for m in MODELS},
        "comparisons": [{"comparison_id": "cmp", "kind": "common_date", "models": list(MODELS),
                         "reference_variants": ["et_corrected_inclusive"],
                         "sides": [{"extraction_method": "fp", "footprint_set_id": "none"}]}],
        "metric_sets": [{"metric_set_id": "mset", "metrics": ["mbe", "mae", "rmse"],
                         "daily_tolerance": {"mean": 0.01, "std": 0.001}}],
        "bootstrap": [{"bootstrap_id": "bs", "metric_set": "mset", "block_lengths": [7],
                       "seed": 1, "n_replicates": 10, "confidence_level": 0.9,
                       "min_blocks": 2, "segment_boundary": "none"}],
        "diagnostics": [diag or GOOD],
    }


def _cfg(diag):
    return config_from_dict(_cfg_dict(diag))


GOOD = {"diagnostics_id": "dg", "metric_set": "mset", "min_bin_days": 20,
        "bootstrap": "bs", "comparisons": ["cmp"],
        "variables": {"vpd": {"source": "station", "column": "src_vpd", "units": "kPa",
                              "meaning": "vapour pressure deficit", "status": "provisional",
                              "edges": [0, 1, 2, float("inf")]}}}


def test_example_style_config_loads():
    cfg = _cfg(GOOD)
    d = cfg.diagnostics_spec("dg")
    assert d.variables["vpd"].edges == (0.0, 1.0, 2.0, float("inf"))
    assert d.calendar == ("season", "month")


def _with_var(**kw):
    v = dict(GOOD["variables"]["vpd"])
    v.update(kw)
    v = {k: x for k, x in v.items() if x is not None}
    return {**GOOD, "variables": {"vpd": v}}


@pytest.mark.parametrize("diag, match", [
    ({**GOOD, "min_bin_days": None}, "min_bin_days not declared"),
    ({**GOOD, "min_bin_days": 0}, "min_bin_days must be an integer"),
    ({**GOOD, "metric_set": "nope"}, "metric_set 'nope' not declared"),
    ({**GOOD, "metrics": ["nse"]}, "not one of"),
    ({**GOOD, "metrics": ["pearson_r"]}, "not computed by metric set"),
    ({**GOOD, "bootstrap": "nope"}, "bootstrap 'nope' not declared"),
    ({**GOOD, "comparisons": ["nope"]}, "comparisons \\['nope'\\] not declared"),
    ({**GOOD, "calendar": ["week"]}, "not one of"),
    ({**GOOD, "unknown_key": 1}, "unknown configuration key"),
    (_with_var(units=None), "units not declared"),
    (_with_var(meaning=None), "meaning not declared"),
    (_with_var(column=None), "column not declared"),
    (_with_var(status="guessed"), "not one of"),
    (_with_var(source="gridded"), "not one of"),
    (_with_var(status="unverified"), "needs a note"),
    (_with_var(edges=[0]), "at least two"),
    (_with_var(edges=[0, 2, 1]), "strictly increasing"),
    (_with_var(edges=[0, float("inf"), 3]), "strictly increasing"),
    (_with_var(edges=[0, 1], quantiles=[0, 1]), "either fixed edges or training quantiles"),
    (_with_var(edges=None, quantiles=[0, 0.5, 1]), "declared training period"),
    (_with_var(edges=None, quantiles=[0, 1.5], training_start="2025-01-01",
               training_end="2025-02-01"), "must lie in"),
    (_with_var(edges=None, kind="direction", units="degree", n_sectors=7), "divide 360"),
    (_with_var(edges=None, kind="direction", units="deg", n_sectors=8), "units 'degree'"),
    (_with_var(kind="direction", units="degree"), "binned by n_sectors"),
    (_with_var(n_sectors=8), "applies only to kind direction"),
    (_with_var(training_start="2025-01-01"), "apply only to quantiles"),
    (_with_var(colour="red"), "unknown configuration key"),
    ({**GOOD, "closure_metadata": ["ebr"]}, "closure_metadata 'ebr' is not a declared"),
    ({**GOOD, "net_radiation_variable": "rn"}, "net_radiation_variable 'rn' is not a declared"),
    ({**GOOD, "wind_direction_variable": "vpd"}, "must be of kind direction"),
    ({**_with_var(edges=[1, 2]), "sensible_heat_variable": "vpd"}, "include 0"),
    ({**GOOD, "gap_filled_fraction_edges": [0, 2]}, "must lie in"),
    ({**GOOD, "variables": {"season": GOOD["variables"]["vpd"]}}, "reserved"),
    ({**GOOD, "diagnostics_id": "Bad-ID"}, "snake_case"),
])
def test_config_rejections(diag, match):
    with pytest.raises(ContractError, match=match):
        _cfg(diag)


def test_config_rejects_duplicates_and_mismatched_bootstrap():
    with pytest.raises(ContractError, match="duplicate diagnostics_id"):
        config_from_dict({**_cfg_dict(), "diagnostics": [GOOD, GOOD]})
    other = {"bootstrap_id": "bs2", "metric_set": "mset2", "block_lengths": [7], "seed": 1,
             "n_replicates": 10, "confidence_level": 0.9, "min_blocks": 2,
             "segment_boundary": "none"}
    d = _cfg_dict()
    d["metric_sets"].append({"metric_set_id": "mset2", "daily_tolerance":
                             {"mean": 0.01, "std": 0.001}})
    d["bootstrap"].append(other)
    d["diagnostics"] = [{**GOOD, "bootstrap": "bs2"}]
    with pytest.raises(ContractError, match="resamples metric set mset2"):
        config_from_dict(d)
