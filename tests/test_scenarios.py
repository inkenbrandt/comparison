"""Guide step 15: declared scenario uncertainty, kept apart from bootstrap intervals."""

from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from etcompare.config import (
    PerturbationSpec,
    ScenarioAlternativeSpec,
    ScenarioSpec,
    WaterBalanceTermSpec,
    config_from_dict,
)
from etcompare.errors import ContractError
from etcompare.scenarios import (
    UNCERTAINTY_LABELS,
    draw_perturbations,
    marginal,
    run_scenario,
    scenario_sha256,
)
from etcompare.schemas import INTERVALS, SCENARIO_RESULTS
from test_water_balance import (  # tests/ is on sys.path (pytest rootdir insertion)
    AGG,
    BASE,
    DS,
    ET,
    STATION,
    P,
    balance,
    make_closed_terms,
    spec,
    station_table,
    table_term,
    unknown,
    write,
)


@pytest.fixture
def closed_terms(tmp_path):
    return make_closed_terms(tmp_path)


DEP = {"across_terms": "independent", "justification": "synthetic: terms measured separately"}


def pert(term="precipitation", family="normal", how="multiplicative",
         temporal="systematic", **params):
    params = params or {"mean": 1.0, "sd": 0.1}
    return PerturbationSpec(term, {"family": family, **params}, how, temporal,
                            "synthetic gauge error")


def measurement(perturbations, *, seed=7, n=400, dependence=DEP, quantiles=(0.05, 0.5, 0.95),
                sid="syn_measurement"):
    return ScenarioSpec(sid, "syn_balance", "measurement", "synthetic test", seed=seed,
                        n_draws=n, quantiles=quantiles, perturbations=tuple(perturbations),
                        dependence=dependence)


def run(sc, wb, station=None):
    return run_scenario(sc, wb, station or station_table(), period_aggregation=AGG,
                        station=STATION)


def stat(result, label, statistic):
    f = result.results.frame
    return f[(f["period_label"] == label) & (f["statistic"] == statistic)].iloc[0]


# --------------------------------------------------------------------------- gates

def test_same_seed_reproduces_and_other_seed_differs(closed_terms):
    wb = spec(closed_terms)
    sc = measurement([pert(), pert("et", temporal="independent_daily", mean=1.0, sd=0.05)])
    a, b = run(sc, wb), run(sc, wb)
    pd.testing.assert_frame_equal(a.results.frame, b.results.frame)
    np.testing.assert_array_equal(a.draws()[1], b.draws()[1])
    c = run(replace(sc, seed=8), wb)
    assert not np.allclose(a.draws()[1], c.draws()[1])
    f = a.results.frame
    assert set(f["seed"]) == {7} and set(f["n_draws"]) == {400}
    assert set(f["scenario_sha256"]) == {scenario_sha256(sc)}
    assert a.results.attributes["scenario"]["perturbations"][0]["justification"]


def test_scenario_results_are_labelled_apart_from_bootstrap_intervals(closed_terms):
    r = run(measurement([pert()]), spec(closed_terms))
    t = r.results
    assert t.schema is SCENARIO_RESULTS and t.schema.name != INTERVALS.name
    assert "uncertainty_category" not in INTERVALS.names
    assert set(t.frame["uncertainty_category"]) == {"measurement"}
    assert set(t.frame["uncertainty_label"]) == {UNCERTAINTY_LABELS["measurement"]}
    assert t.frame["flags"].str.contains("not_bootstrap_interval").all()
    assert t.attributes["not_bootstrap_interval"] is True
    assert "never merged" in t.attributes["sampling_uncertainty"]
    assert "not a bootstrap performance interval" in t.frame["interpretation"].iloc[0]
    with pytest.raises(ContractError, match="step-10 block bootstrap"):
        ScenarioSpec("s", "syn_balance", "sampling", "j")


def test_systematic_multiplicative_precipitation_is_analytic(closed_terms):
    wb = spec(closed_terms)
    sc = measurement([pert(mean=1.0, sd=0.1)], n=4000)
    r = run(sc, wb)
    x = draw_perturbations(sc, 91)["precipitation"]
    index, draws = r.draws()
    i = index.index[(index["period_label"] == "2024-01")][0]
    # The closed balance is 0, so the residual is P_total * (x - 1).
    np.testing.assert_allclose(draws[i], P * 31 * (x - 1.0), atol=1e-12)
    assert stat(r, "2024-01", "base")["value"] == 0.0
    assert stat(r, "2024-01", "mean")["value"] == pytest.approx(np.mean(P * 31 * (x - 1)))
    assert stat(r, "2024-01", "sd")["value"] == pytest.approx(P * 31 * 0.1, rel=0.05)
    assert stat(r, "2024-01", "q0.5")["value"] == pytest.approx(
        np.quantile(P * 31 * (x - 1), 0.5))
    # One systematic draw serves every period: February scales with its 29 days.
    j = index.index[(index["period_label"] == "2024-02")][0]
    np.testing.assert_allclose(draws[j], draws[i] * 29 / 31, atol=1e-12)


def test_independent_daily_additive_et_is_analytic(closed_terms):
    wb = spec(closed_terms, periods=("month",))     # the draw axis is then the 91 days
    sc = measurement([pert("et", "uniform", "additive", "independent_daily",
                           low=-0.5, high=0.5)], n=50)
    r = run(sc, wb)
    x = draw_perturbations(sc, 91)["et"]           # (draws, days of the period axis)
    index, draws = r.draws()
    i = index.index[(index["period_label"] == "2024-02")][0]
    # ET is subtracted: residual = -sum of the February draws (axis days 31..59).
    np.testing.assert_allclose(draws[i], -x[:, 31:60].sum(axis=1), atol=1e-12)
    assert x.shape == (50, 91) and (x >= -0.5).all() and (x <= 0.5).all()


def test_storage_additive_is_per_period(closed_terms):
    sc = measurement([pert("storage_change", "normal", "additive", mean=0.0, sd=2.0)], n=20)
    r = run(sc, spec(closed_terms))
    x = draw_perturbations(sc, 91)["storage_change"]
    index, draws = r.draws()
    i = index.index[(index["period_label"] == "2024-03")][0]
    np.testing.assert_allclose(draws[i], -x, atol=1e-12)    # dS + x, subtracted


def test_gaussian_copula_correlation():
    rho = 0.8
    sc = measurement([pert(), pert("et", "lognormal", mu=0.0, sigma=0.05)], n=6000,
                     dependence={"across_terms": "gaussian_copula", "justification": "j",
                                 "correlations": [{"terms": ["precipitation", "et"],
                                                   "rho": rho}]})
    d = draw_perturbations(sc, 10)
    z_p = (d["precipitation"] - 1.0) / 0.1
    z_e = np.log(d["et"]) / 0.05
    assert np.corrcoef(z_p, z_e)[0, 1] == pytest.approx(rho, abs=0.03)
    ind = measurement([pert(), pert("et", "lognormal", mu=0.0, sigma=0.05)], n=6000)
    d2 = draw_perturbations(ind, 10)
    assert abs(np.corrcoef(d2["precipitation"], d2["et"])[0, 1]) < 0.05


def test_marginals():
    z = np.array([-1.0, 0.0, 1.0])
    assert marginal(pert(mean=2.0, sd=0.5), z).tolist() == [1.5, 2.0, 2.5]
    u = marginal(pert(family="uniform", low=0.0, high=2.0), np.array([0.0]))
    assert u[0] == pytest.approx(1.0)
    tri = marginal(pert(family="triangular", low=0.0, mode=1.0, high=2.0), np.array([0.0]))
    assert tri[0] == pytest.approx(1.0)


def test_unknown_base_residual_stays_unknown(closed_terms):
    wb = spec({**closed_terms, "irrigation": unknown("no records")})
    r = run(measurement([pert()]), wb)
    f = r.results.frame
    assert set(f["status"]) == {"unknown"} and f["value"].isna().all()
    assert f["status_detail"].str.contains("irrigation").all()
    assert np.isnan(r.draws()[1]).all()
    with pytest.raises(ContractError, match="unknown"):
        run(measurement([pert("irrigation")]), wb)


def test_scenario_term_can_carry_a_declared_distribution(closed_terms):
    s = WaterBalanceTermSpec(status="scenario", source="scenario", daily_mm=1.0,
                             justification="synthetic", spatial_support="boundary")
    wb = spec({**closed_terms, "irrigation": s})
    r = run(measurement([pert("irrigation", temporal="independent_daily")], n=10), wb)
    assert set(r.results.frame["residual_kind"]) == {"scenario"}
    assert stat(r, "2024-01", "base")["value"] == 0.0


def test_method_choice_alternatives(closed_terms):
    filled = pd.date_range("2024-02-10", "2024-02-14")
    wb = spec(closed_terms)
    alt = ScenarioAlternativeSpec("measured_et", "closure kept, gap-filled days removed",
                                  {"et": replace(closed_terms["et"],
                                                 reference_variant="et_corrected_measured")})
    raw = ScenarioAlternativeSpec("raw_et", "no closure correction",
                                  {"et": replace(closed_terms["et"], reference_variant="et_raw")})
    sc = ScenarioSpec("syn_methods", "syn_balance", "method_choice", "ET reference choice",
                      alternatives=(alt, raw))
    r = run(sc, wb, station_table(filled=filled))
    jan_raw = stat(r, "2024-01", "alternative=raw_et")
    assert jan_raw["value"] == pytest.approx(ET * 31 - ET / 1.25 * 31)   # less ET out
    assert stat(r, "2024-01", "alternative=declared")["value"] == 0.0
    assert stat(r, "2024-01", "max")["value"] == jan_raw["value"]
    assert stat(r, "2024-01", "min")["value"] == 0.0
    feb = stat(r, "2024-02", "alternative=measured_et")
    assert feb["status"] == "unknown" and "incomplete" in feb["status_detail"]
    assert stat(r, "2024-02", "min")["status"] == "unknown"
    f = r.results.frame
    assert f["flags"].str.contains("not_a_probability_distribution").all()
    assert f["seed"].isna().all() and set(f["n_draws"]) == {3}
    assert r.draws() is None
    assert set(f["uncertainty_category"]) == {"method_choice"}


def test_footprint_realizations_need_supplied_tables(closed_terms, tmp_path):
    days = pd.date_range("2024-01-01", "2024-03-31")
    reals = [ScenarioAlternativeSpec(
        f"fp_{k}", "regenerated footprints (synthetic)",
        {"et": table_term(write(tmp_path, f"et_{k}", ET + d), support="boundary")})
        for k, d in enumerate((-0.25, 0.25))]
    sc = ScenarioSpec("syn_fp", "syn_balance", "footprint_parameter", "footprint z0",
                      alternatives=tuple(reals), realization_source="synthetic rerun")
    r = run(sc, spec(closed_terms))
    assert stat(r, "2024-01", "realization=fp_0")["value"] == pytest.approx(0.25 * 31)
    assert stat(r, "2024-01", "median")["value"] == 0.0
    assert stat(r, "2024-01", "max")["value"] == pytest.approx(0.25 * 31)
    assert r.results.frame["flags"].str.contains("supplied_realizations").all()
    assert len(days) == 91
    # Noise on pixels or a distribution is not a footprint realization.
    with pytest.raises(ContractError, match="never noise"):
        ScenarioSpec("x", "syn_balance", "footprint_parameter", "j", realization_source="r",
                     alternatives=(ScenarioAlternativeSpec(
                         "a", "j", {"et": closed_terms["et"]}), reals[0]))
    with pytest.raises(ContractError, match="alternatives, not distributions"):
        ScenarioSpec("x", "syn_balance", "footprint_parameter", "j", realization_source="r",
                     alternatives=tuple(reals), perturbations=(pert("et"),))
    with pytest.raises(ContractError, match="at least 2"):
        ScenarioSpec("x", "syn_balance", "footprint_parameter", "j", realization_source="r",
                     alternatives=reals[:1])


def test_storage_state_daily_perturbation_refused(closed_terms):
    sc = measurement([pert("storage_change", temporal="independent_daily")])
    with pytest.raises(ContractError, match="systematically"):
        run(sc, spec(closed_terms))
    assert DS == 1.5


# --------------------------------------------------------------------------- declarations

def scenario_body(**over):
    body = {"scenario_id": "sc", "water_balance": "wb", "category": "measurement",
            "justification": "j", "seed": 1, "n_draws": 10, "quantiles": [0.05, 0.95],
            "perturbations": [{"term": "precipitation",
                               "distribution": {"family": "normal", "mean": 1.0, "sd": 0.1},
                               "mode": "multiplicative", "temporal_dependence": "systematic",
                               "justification": "gauge catch"}],
            "dependence": dict(DEP)}
    body.update(over)
    return body


def load(**over):
    return config_from_dict({**BASE, "water_balances": [balance()],
                             "scenarios": [scenario_body(**over)]}, base_dir=Path("."))


def test_config_loads_a_scenario():
    s = load().scenario("sc")
    assert s.perturbations[0].distribution["sd"] == 0.1
    assert s.quantiles == (0.05, 0.95)


GOOD = scenario_body()["perturbations"][0]


@pytest.mark.parametrize("over, match", [
    ({"water_balance": "nope"}, "not declared"),
    ({"category": "sampling"}, "step-10"),
    ({"category": "spread"}, "category"),
    ({"seed": None}, "seed not declared"),
    ({"n_draws": None}, "n_draws not declared"),
    ({"quantiles": []}, "quantiles"),
    ({"quantiles": [0.5, 1.0]}, "quantiles"),
    ({"perturbations": []}, "never a default"),
    ({"perturbations": [{**GOOD, "justification": None}]}, "justification"),
    ({"perturbations": [{**GOOD, "distribution": {"family": "normal", "mean": 1.0}}]},
     "sd not declared"),
    ({"perturbations": [{**GOOD, "distribution": {"family": "normal", "mean": 1, "sd": 0}}]},
     "invalid normal"),
    ({"perturbations": [{**GOOD, "distribution": {"family": "empirical"}}]}, "family"),
    ({"perturbations": [{**GOOD, "term": "irrigation"}]}, "unknown in wb"),
    ({"perturbations": [GOOD, GOOD]}, "perturbed twice"),
    ({"dependence": None}, "dependence not declared"),
    ({"dependence": {"across_terms": "independent"}}, "justification"),
    ({"dependence": {"across_terms": "gaussian_copula", "justification": "j"}},
     "needs declared correlations"),
    ({"alternatives": [{"name": "a", "justification": "j", "terms": {"et": {
        "status": "unknown", "source": "none", "reason": "r"}}}]}, "not alternatives"),
    ({"category": "method_choice", "seed": None, "n_draws": None, "quantiles": [],
      "perturbations": [], "dependence": None}, "at least 1"),
    ({"category": "method_choice", "n_draws": None, "quantiles": [], "perturbations": [],
      "dependence": None}, "seed applies to measurement"),
])
def test_scenario_config_refusals(over, match):
    with pytest.raises(ContractError, match=match):
        load(**over)


def test_copula_checks():
    def two(rho, temporal="systematic"):
        p2 = {**GOOD, "term": "et", "temporal_dependence": temporal}
        return ScenarioSpec("sc", "wb", "measurement", "j", seed=1, n_draws=5,
                            quantiles=(0.5,), perturbations=(GOOD, p2),
                            dependence={"across_terms": "gaussian_copula", "justification": "j",
                                        "correlations": [{"terms": ["precipitation", "et"],
                                                          "rho": rho}]})
    assert two(0.5).correlation("et", "precipitation") == 0.5
    with pytest.raises(ContractError, match=r"\(-1, 1\)"):
        two(1.0)
    with pytest.raises(ContractError, match="same temporal_dependence"):
        two(0.5, "independent_daily")


def test_non_positive_definite_correlations_refused():
    ps = [{**GOOD, "term": t} for t in ("precipitation", "et", "runoff")]
    corr = [{"terms": ["precipitation", "et"], "rho": 0.9},
            {"terms": ["precipitation", "runoff"], "rho": 0.9},
            {"terms": ["et", "runoff"], "rho": -0.9}]
    with pytest.raises(ContractError, match="positive definite"):
        ScenarioSpec("sc", "wb", "measurement", "j", seed=1, n_draws=5, quantiles=(0.5,),
                     perturbations=tuple(ps), dependence={"across_terms": "gaussian_copula",
                                                          "justification": "j",
                                                          "correlations": corr})
