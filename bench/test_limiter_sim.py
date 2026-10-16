"""Tests for limiter_sim.py: statistics helpers, model invariants, and small-trial mechanism checks."""
import random

import pytest

import limiter_sim as S


def test_t_distribution_against_tables():
    assert S.t_two_sided_p(2.262, 9) == pytest.approx(0.05, abs=5e-4)
    assert S.t_two_sided_p(1.96, 10**6) == pytest.approx(0.05, abs=5e-4)
    assert S.t_quantile(0.975, 7) == pytest.approx(2.365, abs=1e-3)


def test_sign_flip_exact():
    assert S.sign_flip_p([1.0] * 8) == pytest.approx(2 / 256)
    assert S.sign_flip_p([1.0, -1.0] * 4) == 1.0


def test_estimators_see_only_observed_fields():
    arms = S.arm_pairs()["PvsE"]
    blocks = S.session("stock", arms, ["A", "B", "B", "A"], random.Random(0), S.Noise())
    for _, obs in blocks:
        for o in obs:
            assert set(o) == set(S.OBSERVED)


def _f_after(history_watts, cap, leak):
    p = dict(S.SCENARIOS["stock"], pl1=cap, pl2=cap, leak=leak)
    pkg = S.Package(**p)
    pkg.advance(60.0, history_watts)
    return S.arm_pairs()["PvsE"]["A"].operating_point(pkg)[0]


def test_cap_makes_package_memoryless_without_leakage():
    assert _f_after(6.0, 25.0, 0.0) == pytest.approx(_f_after(60.0, 25.0, 0.0))


def test_leakage_gives_capped_package_a_memory():
    # The adversarial knob is live: with leakage, a hot history lowers capped frequency.
    assert _f_after(60.0, 25.0, 0.5) < _f_after(6.0, 25.0, 0.5)


def test_pl1_window_binds_only_when_ewma_is_above_it():
    arm = S.arm_pairs()["AA"]["A"]
    cold = S.Package(**S.SCENARIOS["host"])
    cold.pl1_override = (0.0, 10.0, 30.0)
    assert S.decode_step(cold, arm, random.Random(0), S.QUIET)["log"] == 0
    warm = S.Package(**S.SCENARIOS["host"])
    warm.ewma = 66.0
    warm.pl1_override = (0.0, 10.0, 30.0)
    assert S.decode_step(warm, arm, random.Random(0), S.QUIET)["log"] == 1 << S.BIT["PL1"]


def test_mechanism_small_trials():
    """D4 tracks the capped estimand; PL1-only ablation and D3 do not, on P vs E (stock)."""
    arms = S.arm_pairs()["PvsE"]
    truth_cap = S.true_effect("stock", arms, cap=25.0)
    d4 = S.summarize_trials(S.ab_trials("stock", "PvsE", "ABBA", 6, 3, cap=25.0,
                                        estimators=("paired",)), truth_cap)
    assert abs(d4["paired_ABBA"]["mean_est"] - truth_cap) < 0.01
    assert d4["manipulation_check_pass_rate"] == 1.0
    abl = S.summarize_trials(S.ab_trials("stock", "PvsE", "ABBA", 6, 3, cap=25.0, cap_pl1_only=True,
                                         estimators=("paired",)),
                             S.true_effect("stock", arms, cap=25.0, cap_pl1_only=True))
    assert abs(abl["paired_ABBA"]["mean_est"] - abl["truth"]) > 0.05
    d3 = S.summarize_trials(S.ab_trials("stock", "PvsE", "ABBA", 6, 3, estimators=("strat",)),
                            S.true_effect("stock", arms))
    assert d3["D3_stratified"]["mean_coverage"] < 0.7


def test_cap_at_stock_pl1_recovers_stock_sustained_effect():
    """Post-hoc: D4 at cap = stock PL1 tracks the stock sustained effect. Plain ABBA sessions do
    not: short blocks with idle gaps keep the PL1 EWMA below 35 W, so they measure turbo."""
    arms = S.arm_pairs()["PvsE"]
    stock_truth = S.true_effect("stock", arms)
    d4 = S.summarize_trials(S.ab_trials("stock", "PvsE", "ABBA", 6, 5, cap=35.0,
                                        estimators=("paired",)), stock_truth)
    d2 = S.summarize_trials(S.ab_trials("stock", "PvsE", "ABBA", 6, 5, estimators=("paired",)),
                            stock_truth)
    # Residual ~1.25 points: the interleaved session runs cooler than the arm alone, so
    # temperature-dependent leakage costs less. test_d4_residual_is_leakage removes it.
    assert abs(d4["paired_ABBA"]["mean_est"] - stock_truth) < 0.02
    assert abs(d2["paired_ABBA"]["mean_est"] - stock_truth) > 0.1
    assert d2["pl1_tagged_fraction"]["A"] == 0.0  # the ledger shows the session never hit PL1


def test_d4_residual_is_leakage(monkeypatch):
    monkeypatch.setitem(S.SCENARIOS, "stock", dict(S.SCENARIOS["stock"], leak=0.0))
    arms = S.arm_pairs()["PvsE"]
    truth = S.true_effect("stock", arms)
    d4 = S.summarize_trials(S.ab_trials("stock", "PvsE", "ABBA", 6, 5, cap=35.0,
                                        estimators=("paired",)), truth)
    assert abs(d4["paired_ABBA"]["mean_est"] - truth) < 0.002


def test_table_renders():
    rows = S.ab_trials("stock", "AA", "ABBA", 2, 1, cap=25.0, estimators=("paired",))
    out = {"E3_E4": {"stock/AA": {"D4_cap25": S.summarize_trials(rows, 0.0)}}}
    assert "D4_cap25" in S.table(out)
