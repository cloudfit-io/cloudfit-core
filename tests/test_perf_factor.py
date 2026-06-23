"""Tests for effective-capacity perf scoring (perf_factor) and explainability."""

import pytest

from cloudfit import (
    MachineType,
    OptimizeFor,
    PERF_FACTORS,
    WorkloadProfile,
    perf_factor_for,
    rank,
    score_instance,
)


# --- perf_factor_for lookup ---

def test_perf_factor_for_known_family():
    assert perf_factor_for("c3-standard-44") == PERF_FACTORS["c3"]
    assert perf_factor_for("t2d-standard-60") == PERF_FACTORS["t2d"]


def test_perf_factor_for_baseline_is_one():
    assert perf_factor_for("n2-standard-32") == 1.0


def test_perf_factor_for_unknown_family_defaults_to_one():
    assert perf_factor_for("totally-made-up") == 1.0
    assert perf_factor_for("singletoken") == 1.0


# --- effective capacity ---

def test_perf_factor_unset_is_a_noop():
    """An unset perf_factor scores exactly like perf_factor == 1.0 (count-based)."""
    profile = WorkloadProfile(vcpu=32, ram_gb=64, archetype="cpu",
                              optimize_for=OptimizeFor.performance)
    base = MachineType(id="c3-standard-32", provider="gcp", vcpu=32, ram_gb=64, price_hr=2.0)
    one = base.model_copy(update={"perf_factor": 1.0})
    assert score_instance(base, profile).perf_score == score_instance(one, profile).perf_score


def test_faster_family_outranks_slower_at_equal_cores():
    """Layer 1 goal: a fast family is not equated with a slow one at the same vCPU count."""
    profile = WorkloadProfile(vcpu=32, ram_gb=64, archetype="cpu",
                              optimize_for=OptimizeFor.performance)
    common = dict(provider="gcp", vcpu=32, ram_gb=64, price_hr=2.0)
    fast = MachineType(id="c3-standard-32", perf_factor=PERF_FACTORS["c3"], **common)
    slow = MachineType(id="t2d-standard-32", perf_factor=PERF_FACTORS["t2d"], **common)
    by_id = {r.instance.id: r for r in rank(profile, [fast, slow])}
    assert by_id["c3-standard-32"].perf_score > by_id["t2d-standard-32"].perf_score
    assert rank(profile, [fast, slow])[0].instance.id == "c3-standard-32"


def test_slow_family_under_delivers_at_exact_core_count():
    """A sub-baseline family with exactly the requested cores delivers < target effective vCPU."""
    profile = WorkloadProfile(vcpu=32, ram_gb=64, archetype="cpu",
                              optimize_for=OptimizeFor.performance)
    slow = MachineType(id="e2-standard-32", provider="gcp", vcpu=32, ram_gb=64,
                       price_hr=1.5, perf_factor=PERF_FACTORS["e2"])  # 0.80 -> eff 25.6 < 32
    # vCPU dominates the cpu archetype (weight 0.7); under-delivery drags perf below RAM-only credit.
    assert score_instance(slow, profile).perf_score < 0.5


def test_explicit_perf_factor_changes_perf_score():
    profile = WorkloadProfile(vcpu=32, ram_gb=64, archetype="cpu",
                              optimize_for=OptimizeFor.performance)
    common = dict(id="x-standard-32", provider="gcp", vcpu=32, ram_gb=64, price_hr=2.0)
    plain = MachineType(**common)
    boosted = MachineType(perf_factor=1.2, **common)
    assert score_instance(boosted, profile).perf_score != score_instance(plain, profile).perf_score


# --- explainability ---

def test_contributions_sum_to_score():
    profile = WorkloadProfile(vcpu=16, ram_gb=64, optimize_for=OptimizeFor.balanced)
    inst = MachineType(id="n2-standard-16", provider="gcp", vcpu=16, ram_gb=64, price_hr=0.78)
    s = score_instance(inst, profile, price_range=(0.5, 1.5))
    assert sum(s.contributions.values()) == pytest.approx(s.score, abs=1e-3)
    assert set(s.contributions) == {"cost", "perf", "avail"}


def test_reason_is_present_for_qualified():
    profile = WorkloadProfile(vcpu=16, ram_gb=64, optimize_for=OptimizeFor.performance)
    inst = MachineType(id="c3-standard-16", provider="gcp", vcpu=16, ram_gb=64,
                       price_hr=0.78, perf_factor=PERF_FACTORS["c3"])
    s = score_instance(inst, profile)
    assert s.reason
    assert "faster" in s.reason  # c3 perf_factor > 1.0


def test_reason_flags_slower_family():
    profile = WorkloadProfile(vcpu=16, ram_gb=64, optimize_for=OptimizeFor.cost)
    inst = MachineType(id="e2-standard-16", provider="gcp", vcpu=16, ram_gb=64,
                       price_hr=0.5, perf_factor=PERF_FACTORS["e2"])
    s = score_instance(inst, profile, price_range=(0.5, 1.5))
    assert "slower" in s.reason


def test_disqualified_has_reason_and_empty_contributions():
    profile = WorkloadProfile(vcpu=64, ram_gb=256)
    under = MachineType(id="small", provider="gcp", vcpu=2, ram_gb=8, price_hr=0.1)
    s = score_instance(under, profile)
    assert s.disqualified
    assert s.reason == s.disqualify_reason
    assert s.contributions == {}
