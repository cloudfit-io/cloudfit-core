"""Tests for the cloudfit-core scoring engine."""

import pytest
from cloudfit import rank, WorkloadProfile, MachineType, OptimizeFor


CANDIDATES = [
    MachineType(id="c2-standard-60",       provider="gcp", vcpu=60, ram_gb=240, price_hr=3.13),
    MachineType(id="c3d-standard-60-lssd", provider="gcp", vcpu=60, ram_gb=240, price_hr=3.39),
    MachineType(id="t2d-standard-60",      provider="gcp", vcpu=60, ram_gb=240, price_hr=2.31),
    MachineType(id="c7i.24xlarge",         provider="aws", vcpu=96, ram_gb=192, price_hr=4.28),
]

BCLCONVERT = WorkloadProfile(
    vcpu=60, ram_gb=224,
    workload="bclconvert",
    archetype="io",
    optimize_for="balanced",
)


def test_rank_returns_all_candidates():
    results = rank(BCLCONVERT, CANDIDATES)
    assert len(results) == len(CANDIDATES)


def test_rank_scores_in_descending_order():
    results = rank(BCLCONVERT, CANDIDATES)
    qualified = [r for r in results if not r.disqualified]
    scores = [r.score for r in qualified]
    assert scores == sorted(scores, reverse=True)


def test_hard_floor_disqualifies_underpowered():
    profile = WorkloadProfile(vcpu=60, ram_gb=224, ram_floor_gb=224)
    under = MachineType(id="small", provider="gcp", vcpu=4, ram_gb=16, price_hr=0.10)
    results = rank(profile, [under])
    assert results[0].disqualified
    assert "RAM" in results[0].disqualify_reason


def test_cost_mode_prefers_cheapest_qualifying():
    profile = WorkloadProfile(vcpu=60, ram_gb=224, optimize_for=OptimizeFor.cost)
    results = rank(profile, CANDIDATES)
    qualified = [r for r in results if not r.disqualified]
    assert qualified[0].instance.id == "t2d-standard-60"  # cheapest at $2.31/hr


def test_gpu_hard_floor():
    profile = WorkloadProfile(vcpu=8, ram_gb=64, gpu={"required": True, "vram_gb": 40})
    no_gpu = MachineType(id="cpu-only", provider="gcp", vcpu=32, ram_gb=128, price_hr=1.50)
    results = rank(profile, [no_gpu])
    assert results[0].disqualified


def test_score_between_zero_and_one():
    results = rank(BCLCONVERT, CANDIDATES)
    for r in results:
        assert 0.0 <= r.score <= 1.0


def test_region_hard_floor_disqualifies_other_regions():
    """When profile.region is set, instances in other regions are disqualified."""
    profile = WorkloadProfile(vcpu=16, ram_gb=64, region="us-central1")
    candidates = [
        MachineType(id="n2-standard-16", provider="gcp", vcpu=16, ram_gb=64, price_hr=0.78, region="us-central1"),
        MachineType(id="n2-standard-16", provider="gcp", vcpu=16, ram_gb=64, price_hr=0.84, region="europe-west4"),
        MachineType(id="n2-standard-16", provider="gcp", vcpu=16, ram_gb=64, price_hr=0.92, region="asia-southeast1"),
    ]
    results = rank(profile, candidates)
    qualified = [r for r in results if not r.disqualified]
    disqualified = [r for r in results if r.disqualified]
    assert len(qualified) == 1
    assert qualified[0].instance.region == "us-central1"
    assert len(disqualified) == 2
    for r in disqualified:
        assert "region" in r.disqualify_reason.lower()


def test_no_region_allows_all_regions():
    """When profile.region is None, instances from all regions pass the floor."""
    profile = WorkloadProfile(vcpu=16, ram_gb=64)  # no region set
    candidates = [
        MachineType(id="n2-standard-16", provider="gcp", vcpu=16, ram_gb=64, price_hr=0.78, region="us-central1"),
        MachineType(id="n2-standard-16", provider="gcp", vcpu=16, ram_gb=64, price_hr=0.84, region="europe-west4"),
    ]
    results = rank(profile, candidates)
    qualified = [r for r in results if not r.disqualified]
    assert len(qualified) == 2
