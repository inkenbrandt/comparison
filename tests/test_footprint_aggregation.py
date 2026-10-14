"""Temporal footprint aggregation (guide step 6) on synthetic stacks.

Expected values are computed directly with numpy, independently of the
aggregation code.
"""

import numpy as np
import pandas as pd
import pytest

from etcompare.config import TemporalAggregationSpec
from etcompare.errors import ContractError
from etcompare.footprint_aggregation import TimestepFootprints, aggregate_footprints
from etcompare.spatial import Grid

GRID = Grid("EPSG:32612", (10.0, 0.0, 600000.0, 0.0, -10.0, 4100000.0), 4, 3)


def spec(**kw):
    base = dict(method_id="agg", temporal_weighting="time_mean", include_duration=False,
                basis="original_contribution", negative_weight_policy="exclude_timestep",
                gap_policy="exclude_timestep", night_policy="include")
    base.update(kw)
    if base["temporal_weighting"] != "time_mean":
        base.setdefault("weight_column", "s")
    return TemporalAggregationSpec(**base)


def stack(n=6, *, start="2024-06-05 06:00", step="1h", durations=None, seed=0,
          semantics="cell_contribution", label="start", daytime=None, capture=None):
    rng = np.random.default_rng(seed)
    v = rng.random((n, 3, 4))
    cap = np.linspace(0.8, 0.98, n) if capture is None else np.asarray(capture)
    v = v / v.sum(axis=(1, 2))[:, None, None] * cap[:, None, None]
    times = pd.date_range(start, periods=n, freq=step)
    d = np.full(n, pd.Timedelta(step).total_seconds()) if durations is None else durations
    return TimestepFootprints("US-XXX", v, GRID, times, d, semantics, label, daytime=daytime)


def only(result):
    assert result.available
    ok = [p for p in result.periods if p.ok]
    assert len(ok) == 1, [p.status for p in result.periods]
    return ok[0]


def test_equal_weights_reproduce_duration_weighted_mean():
    d = np.array([1800.0, 3600.0, 1800.0, 7200.0, 3600.0, 1800.0])
    st = stack(durations=d)
    p = only(aggregate_footprints(st, spec(include_duration=True)))
    expected = (st.values * d[:, None, None]).sum(axis=0) / d.sum()
    np.testing.assert_allclose(p.contribution, expected, rtol=1e-14)
    assert p.temporal_coverage_frac == pytest.approx(d.sum() / 86400)
    # Without durations the mean is unweighted.
    q = only(aggregate_footprints(st, spec(include_duration=False)))
    np.testing.assert_allclose(q.contribution, st.values.mean(axis=0), rtol=1e-14)


def test_common_factor_leaves_aggregate_unchanged():
    st = stack()
    s = np.array([0.1, 0.4, 0.9, 1.3, 0.7, 0.2])
    ws = spec(temporal_weighting="modeled_et_weighted", include_duration=True)
    a = only(aggregate_footprints(st, ws, weights=s))
    b = only(aggregate_footprints(st, ws, weights=s * 7.3))
    np.testing.assert_allclose(a.contribution, b.contribution, rtol=1e-14)
    expected = (st.values * s[:, None, None]).sum(axis=0) / s.sum()  # equal durations
    np.testing.assert_allclose(a.contribution, expected, rtol=1e-14)
    # Weights given as a Series are matched by stamp, not by position.
    ser = pd.Series(s, index=st.times)[::-1]
    c = only(aggregate_footprints(st, ws, weights=ser))
    np.testing.assert_allclose(c.contribution, a.contribution, rtol=1e-14)


def test_missing_inputs_are_not_available():
    r = aggregate_footprints(None, spec())
    assert r.status == "not_available" and "cannot be re-weighted" in r.reason
    assert not r.periods and "not_available" in r.report.codes()
    r = aggregate_footprints(stack(), spec(temporal_weighting="measured_et_weighted"))
    assert r.status == "not_available" and "weight series" in r.reason
    with pytest.raises(ContractError, match="takes no temporal weights"):
        aggregate_footprints(stack(), spec(), weights=np.ones(6))


def test_renormalized_basis_differs_and_loses_capture():
    st = stack(capture=[0.5, 0.5, 0.5, 1.0, 1.0, 1.0])
    orig = only(aggregate_footprints(st, spec()))
    ren = only(aggregate_footprints(st, spec(basis="renormalized_per_timestep")))
    per = st.values / st.values.sum(axis=(1, 2))[:, None, None]
    np.testing.assert_allclose(ren.contribution, per.mean(axis=0), rtol=1e-14)
    assert ren.contribution.sum() == pytest.approx(1.0)
    assert ren.value_semantics == "normalized_cell_weight" and ren.domain_capture_frac is None
    assert orig.value_semantics == "cell_contribution"
    assert orig.domain_capture_frac == pytest.approx(0.75)
    # Same shape after normalization would mean the two agree; here they do not.
    assert not np.allclose(orig.contribution / orig.contribution.sum(), ren.contribution)


def test_domain_capture_is_weighted_mean_of_timestep_capture():
    cap = np.array([0.6, 0.7, 0.8, 0.9, 1.0, 0.95])
    s = np.array([0.0, 1.0, 2.0, 3.0, 1.0, 0.5])
    p = only(aggregate_footprints(stack(capture=cap), spec(
        temporal_weighting="reference_et_weighted"), weights=s))
    assert p.domain_capture_frac == pytest.approx((s * cap).sum() / s.sum(), rel=1e-14)


def test_normalized_input_cannot_use_original_basis():
    with pytest.raises(ContractError, match="no original contribution"):
        aggregate_footprints(stack(semantics="normalized_cell_weight"), spec())
    p = only(aggregate_footprints(stack(semantics="normalized_cell_weight"),
                                  spec(basis="renormalized_per_timestep")))
    assert p.value_semantics == "normalized_cell_weight"


def test_negative_temporal_weight_policies():
    st = stack()
    s = np.array([1.0, -0.5, 2.0, 1.0, 1.0, 1.0])
    kw = dict(temporal_weighting="measured_et_weighted")
    ex = only(aggregate_footprints(st, spec(**kw), weights=s))
    keep = s >= 0
    np.testing.assert_allclose(ex.contribution, (st.values[keep] * s[keep, None, None]).sum(0)
                               / s[keep].sum(), rtol=1e-14)
    assert ex.excluded == {"negative_temporal_weight": 1} and ex.n_eligible == 5
    r = aggregate_footprints(st, spec(negative_weight_policy="reject_period", **kw), weights=s)
    assert r.periods[0].status == "rejected_negative_temporal_weight"
    assert "rejected_negative_temporal_weight" in set(r.exclusions.to_frame().reason_code)
    k = aggregate_footprints(st, spec(negative_weight_policy="keep", **kw), weights=s)
    p = only(k)
    np.testing.assert_allclose(p.contribution, (st.values * s[:, None, None]).sum(0) / s.sum(),
                               rtol=1e-14)
    assert "negative_temporal_weight_kept" in set(k.exclusions.to_frame().reason_code)
    # Large negative weights can make aggregate cells negative; they are counted.
    big = np.array([1.0, -5.0, 2.0, 1.0, 1.0, 1.0])
    big_st = stack(seed=3)
    q = aggregate_footprints(big_st, spec(negative_weight_policy="keep", **kw), weights=big)
    w = (big_st.values * big[:, None, None]).sum(0) / big.sum()
    assert q.periods[0].n_negative_cells == int((w < 0).sum()) > 0


def test_gap_policies_and_unusable_timesteps():
    st = stack()
    v = st.values.copy()
    v[2] = np.nan  # a timestep with no footprint
    st = TimestepFootprints("US-XXX", v, GRID, st.times, st.durations_s, "cell_contribution",
                            "start")
    s = np.array([1.0, np.nan, 1.0, 1.0, 1.0, 1.0])
    kw = dict(temporal_weighting="modeled_et_weighted")
    p = only(aggregate_footprints(st, spec(**kw), weights=s))
    assert p.excluded == {"weight_missing": 1, "no_footprint_mass": 1} and p.n_eligible == 4
    np.testing.assert_allclose(p.contribution, v[[0, 3, 4, 5]].mean(0), rtol=1e-14)
    r = aggregate_footprints(st, spec(gap_policy="reject_period", **kw), weights=s)
    assert r.periods[0].status == "rejected_gaps"


def test_night_policy_needs_flag_and_excludes():
    day = np.array([False, True, True, True, True, False])
    st = stack(daytime=day)
    p = only(aggregate_footprints(st, spec(night_policy="exclude")))
    np.testing.assert_allclose(p.contribution, st.values[day].mean(0), rtol=1e-14)
    assert p.excluded == {"night": 2}
    with pytest.raises(ContractError, match="daytime flag"):
        aggregate_footprints(stack(), spec(night_policy="exclude"))


def test_interval_label_end_assigns_midnight_to_previous_day():
    t = pd.DatetimeIndex(["2024-06-05 23:00", "2024-06-06 00:00", "2024-06-06 01:00"])
    v = np.ones((3, 3, 4)) / 12
    d = np.full(3, 3600.0)
    end = aggregate_footprints(TimestepFootprints("US-XXX", v, GRID, t, d, "cell_contribution",
                                                  "end"), spec())
    assert [(str(p.date.date()), p.n_timesteps) for p in end.periods] == [
        ("2024-06-05", 2), ("2024-06-06", 1)]
    start = aggregate_footprints(TimestepFootprints("US-XXX", v, GRID, t, d,
                                                    "cell_contribution", "start"), spec())
    assert [p.n_timesteps for p in start.periods] == [1, 2]


def test_min_temporal_coverage_and_monthly_denominator():
    st = stack(n=6)  # 6 h of 24
    r = aggregate_footprints(st, spec(min_temporal_coverage=0.5))
    assert r.periods[0].status == "insufficient_temporal_coverage"
    assert r.periods[0].temporal_coverage_frac == pytest.approx(0.25)
    m = only(aggregate_footprints(st, spec(), period="month"))
    assert m.date == pd.Timestamp("2024-06-01")
    assert m.temporal_coverage_frac == pytest.approx(6 * 3600 / (30 * 86400))


def test_reproduces_upstream_summarize_periods_formulas():
    """fluxfootprints summarize_periods (424ec20): mean over timesteps with mass > 0, and
    sum(f * ET) / sum(ET) with ET unclipped, no duration factor, no renormalization."""
    st = stack(n=8, seed=5)
    v = st.values.copy()
    v[3] = 0.0  # a timestep with no mass is masked upstream
    st = TimestepFootprints("US-XXX", v, GRID, st.times, st.durations_s, "cell_contribution",
                            "start")
    et = np.array([0.05, 0.2, -0.01, 0.3, 0.4, 0.25, 0.1, -0.02])
    valid = v.sum(axis=(1, 2)) > 0
    upstream_mean = v[valid].mean(axis=0)
    e = np.where(valid, et, np.nan)
    upstream_etw = np.nansum(v * e[:, None, None], axis=0) / np.nansum(e)
    mean = only(aggregate_footprints(st, spec()))
    etw = only(aggregate_footprints(st, spec(temporal_weighting="measured_et_weighted",
                                             negative_weight_policy="keep"), weights=et))
    np.testing.assert_allclose(mean.contribution, upstream_mean, rtol=1e-14)
    np.testing.assert_allclose(etw.contribution, upstream_etw, rtol=1e-13)


def test_spec_requires_every_choice():
    with pytest.raises(TypeError):
        TemporalAggregationSpec(method_id="x", temporal_weighting="time_mean")  # type: ignore[call-arg]
    with pytest.raises(ContractError, match="include_duration"):
        spec(include_duration=None)
    with pytest.raises(ContractError, match="temporal_weighting"):
        spec(temporal_weighting="unknown")
    with pytest.raises(ContractError, match="basis"):
        spec(basis="whatever")


def test_write_footprint_set_round_trip(tmp_path):
    pytest.importorskip("rasterio")
    from etcompare.footprint_aggregation import write_footprint_set
    from etcompare.io.manifest import discover_footprints
    from etcompare.io.rasters import read_footprint

    st = stack(n=30, step="1h", start="2024-06-05 00:00")
    r = aggregate_footprints(st, spec(method_id="agg_mean"))
    fs = write_footprint_set(r, tmp_path / "agg", set_id="SYN_AGG")
    assert fs.value_semantics == "cell_contribution" and fs.temporal_weighting == "time_mean"
    man = discover_footprints(fs).manifest.frame
    assert list(man["layer_token"].unique()) == ["aggmean"] and len(man) == 2
    by_date = {p.date: p for p in r.periods}
    for row in man.itertuples():
        values, grid, nodata = read_footprint(row.path)
        assert grid == GRID and nodata is None
        np.testing.assert_array_equal(values, by_date[row.date].contribution)
        assert row.domain_capture_frac == pytest.approx(by_date[row.date].domain_capture_frac)


def test_combine_results_and_weight_column_rule():
    from etcompare.footprint_aggregation import combine_results

    a = aggregate_footprints(stack(start="2024-06-05 06:00"), spec())
    b = aggregate_footprints(stack(start="2024-06-07 06:00"), spec())
    c = combine_results([b, a])
    assert [p.date for p in c.periods] == [pd.Timestamp("2024-06-05"), pd.Timestamp("2024-06-07")]
    with pytest.raises(ContractError, match="share a period"):
        combine_results([a, a])
    with pytest.raises(ContractError, match="weight_column"):
        TemporalAggregationSpec("x", "measured_et_weighted", True, "original_contribution",
                                "keep", "exclude_timestep", "include")


def test_load_timestep_stack(tmp_path):
    from etcompare.adapters.fluxfootprints import load_timestep_stack

    st = stack()
    path = tmp_path / "US-XXX_202406_timesteps.npz"
    np.savez_compressed(path, f=st.values.astype("float32"),
                        time=st.times.values.astype("datetime64[s]"), duration_s=st.durations_s,
                        transform=np.array(GRID.transform), crs=np.array(GRID.crs),
                        nldas_eto_mm_h=np.arange(6.0))
    loaded, weights = load_timestep_stack(path, site_id="US-XXX",
                                          value_semantics="cell_contribution")
    assert loaded.grid == GRID and list(weights.columns) == ["nldas_eto_mm_h"]
    assert (weights.index == st.times).all()
    np.testing.assert_allclose(loaded.values, st.values, rtol=1e-6)
