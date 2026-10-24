"""Tests for effective-capacity perf scoring (perf_factor) and explainability."""

import pytest

from cloudfit import (
    AWS_PERF_FACTORS,
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


# --- AWS instance types ---

def test_perf_factor_for_aws_base_family():
    assert perf_factor_for("m7i.2xlarge") == AWS_PERF_FACTORS["m7i"]
    assert perf_factor_for("c7g.16xlarge") == AWS_PERF_FACTORS["c7g"]
    assert perf_factor_for("t3.micro") == AWS_PERF_FACTORS["t3"]


@pytest.mark.parametrize("instance_type, family", [
    ("m6idn.xlarge", "m6i"),    # d + n stripped, processor letter kept
    ("m5dn.large", "m5"),
    ("c6gn.medium", "c6g"),
    ("r5b.4xlarge", "r5"),
    ("x2iedn.metal", "x2i"),
    ("i3en.6xlarge", "i3"),
    ("g4dn.xlarge", "g4"),
    ("p4de.24xlarge", "p4"),
    ("g6e.12xlarge", "g6"),
    ("m7i-flex.large", "m7i"),
    ("u-6tb1.112xlarge", "u"),
    ("u7in-16tb.224xlarge", "u7i"),
    ("p6-b200.48xlarge", "p6"),
])
def test_perf_factor_for_aws_attribute_suffixes(instance_type, family):
    assert perf_factor_for(instance_type) == AWS_PERF_FACTORS[family]


def test_perf_factor_for_aws_exact_entry_beats_suffix_stripping():
    """m5zn is a 4.5 GHz part, not an m5 with attributes."""
    assert perf_factor_for("m5zn.6xlarge") == AWS_PERF_FACTORS["m5zn"]
    assert AWS_PERF_FACTORS["m5zn"] > AWS_PERF_FACTORS["m5"]


def test_perf_factor_for_aws_and_gcp_names_do_not_collide():
    """AWS c3 is 2013 Ivy Bridge; GCP c3 is Sapphire Rapids."""
    assert perf_factor_for("c3.large") == AWS_PERF_FACTORS["c3"]
    assert perf_factor_for("c3-standard-4") == PERF_FACTORS["c3"]
    assert perf_factor_for("c3.large") < perf_factor_for("c3-standard-4")


@pytest.mark.parametrize("instance_type, family", [
    ("m9g.xlarge", "m8g"),      # Graviton5 rated at least as fast as Graviton4
    ("c9gd.large", "c8g"),
    ("i7ie.large", "i7i"),
    ("m8azn.large", "m8a"),
    ("g7e.2xlarge", "g6"),
])
def test_perf_factor_for_aws_newer_generation_falls_back(instance_type, family):
    assert perf_factor_for(instance_type) == AWS_PERF_FACTORS[family]


def test_perf_factor_for_aws_unknown_family_defaults_to_one():
    assert perf_factor_for("inf2.xlarge") == 1.0
    assert perf_factor_for("mac2.metal") == 1.0


def test_aws_newer_generation_outranks_older_at_equal_cores_and_price():
    profile = WorkloadProfile(vcpu=32, ram_gb=128, archetype="cpu",
                              optimize_for=OptimizeFor.balanced)
    common = dict(provider="aws", vcpu=32, ram_gb=128, price_hr=1.6)
    old = MachineType(id="m5.8xlarge", perf_factor=perf_factor_for("m5.8xlarge"), **common)
    new = MachineType(id="m7i.8xlarge", perf_factor=perf_factor_for("m7i.8xlarge"), **common)
    assert rank(profile, [old, new])[0].instance.id == "m7i.8xlarge"


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
