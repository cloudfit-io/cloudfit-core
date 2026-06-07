"""Tests for the cloudfit-core scoring engine."""

import pytest
from cloudfit import rank, WorkloadProfile, MachineType, OptimizeFor, HeadroomMode


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


# --- fit-based perf_score (0.3.0) ---

def test_balanced_mode_prefers_exact_fit_over_oversize():
    """The headline 0.3.0 fix: 'balanced' picks the exact match, not the 2x oversize."""
    profile = WorkloadProfile(vcpu=16, ram_gb=64, optimize_for=OptimizeFor.balanced)
    candidates = [
        MachineType(id="exact", provider="gcp", vcpu=16, ram_gb=64,  price_hr=0.78),
        MachineType(id="2x",    provider="gcp", vcpu=32, ram_gb=128, price_hr=1.55),
        MachineType(id="4x",    provider="gcp", vcpu=64, ram_gb=256, price_hr=3.10),
    ]
    results = rank(profile, candidates)
    qualified = [r for r in results if not r.disqualified]
    assert qualified[0].instance.id == "exact"
    assert qualified[1].instance.id == "2x"
    assert qualified[2].instance.id == "4x"


def test_perf_score_max_within_ideal_band():
    """Instances at 1.0x to 1.5x of requested resources score perf == 1.0."""
    profile = WorkloadProfile(vcpu=16, ram_gb=64, optimize_for=OptimizeFor.performance)
    candidates = [
        MachineType(id="exact",     provider="gcp", vcpu=16, ram_gb=64, price_hr=0.78),
        MachineType(id="headroom",  provider="gcp", vcpu=24, ram_gb=96, price_hr=1.10),
    ]
    results = rank(profile, candidates)
    # Both fit inside the ideal band so perf_score == 1.0 for both;
    # cost breaks the tie in favor of the cheaper exact match.
    assert results[0].perf_score == 1.0
    assert results[1].perf_score == 1.0


def test_perf_score_zero_for_heavy_oversize():
    """At >=3.5x the requested size, perf_score is fully penalized (0.0)."""
    profile = WorkloadProfile(vcpu=8, ram_gb=16, optimize_for=OptimizeFor.performance)
    heavy = MachineType(id="huge", provider="gcp", vcpu=64, ram_gb=256, price_hr=3.10)  # 8x / 16x
    results = rank(profile, [heavy])
    assert results[0].perf_score == 0.0


# --- candidate-relative cost normalization (0.3.x) ---

def test_cost_score_spreads_across_candidate_range():
    """Cheapest qualifying candidate scores 1.0 on cost, most expensive 0.0.

    Regression for the fixed-$35 normalizer, which compressed a 35% price gap
    into a ~0.02 cost-score difference. Cost must now span the full range.
    """
    profile = WorkloadProfile(vcpu=16, ram_gb=64, optimize_for=OptimizeFor.cost)
    candidates = [
        MachineType(id="cheap",  provider="gcp", vcpu=16, ram_gb=64, price_hr=2.31),
        MachineType(id="mid",    provider="gcp", vcpu=16, ram_gb=64, price_hr=2.85),
        MachineType(id="dear",   provider="gcp", vcpu=16, ram_gb=64, price_hr=3.39),
    ]
    by_id = {r.instance.id: r for r in rank(profile, candidates)}
    assert by_id["cheap"].cost_score == 1.0
    assert by_id["dear"].cost_score == 0.0
    assert 0.0 < by_id["mid"].cost_score < 1.0


def test_unpriced_instance_is_not_treated_as_free():
    """An instance with price_hr <= 0 (pricing lookup failed) must not win on cost.

    Regression for the old normalizer, where price 0.0 yielded the maximum cost
    score (1.0) and could rank an unpriced instance first.
    """
    profile = WorkloadProfile(vcpu=16, ram_gb=64, optimize_for=OptimizeFor.cost)
    candidates = [
        MachineType(id="priced",   provider="gcp", vcpu=16, ram_gb=64, price_hr=2.50),
        MachineType(id="unpriced", provider="gcp", vcpu=16, ram_gb=64, price_hr=0.0),
    ]
    results = rank(profile, candidates)
    by_id = {r.instance.id: r for r in results}
    assert by_id["unpriced"].cost_score == 0.0
    assert results[0].instance.id == "priced"


# --- user-provided headroom (0.5.0) ---

def test_headroom_default_is_a_noop():
    """With no headroom, floors and perf are identical to the pre-headroom engine."""
    plain = WorkloadProfile(vcpu=16, ram_gb=64, optimize_for=OptimizeFor.balanced)
    hr0 = WorkloadProfile(vcpu=16, ram_gb=64, optimize_for=OptimizeFor.balanced, headroom=0.0)
    assert [r.score for r in rank(plain, CANDIDATES)] == [r.score for r in rank(hr0, CANDIDATES)]


def test_headroom_hard_disqualifies_exact_fit():
    """Hard mode raises the floor: an instance meeting only the declared spec is disqualified."""
    profile = WorkloadProfile(vcpu=16, ram_gb=64, headroom=0.5, headroom_mode=HeadroomMode.hard)
    candidates = [
        MachineType(id="exact",    provider="gcp", vcpu=16, ram_gb=64, price_hr=0.78),
        MachineType(id="buffered", provider="gcp", vcpu=24, ram_gb=96, price_hr=1.10),
    ]
    by_id = {r.instance.id: r for r in rank(profile, candidates)}
    assert by_id["exact"].disqualified
    assert "96" in by_id["exact"].disqualify_reason  # floor raised to the buffered target
    assert not by_id["buffered"].disqualified
    assert by_id["buffered"].perf_score == 1.0  # buffered target is the new exact fit


def test_headroom_soft_keeps_but_penalizes_exact_fit():
    """Soft mode never disqualifies; sub-target instances lose perf-fit credit instead."""
    profile = WorkloadProfile(
        vcpu=16, ram_gb=64, headroom=0.5,
        headroom_mode=HeadroomMode.soft, optimize_for=OptimizeFor.performance,
    )
    candidates = [
        MachineType(id="exact",    provider="gcp", vcpu=16, ram_gb=64, price_hr=0.78),
        MachineType(id="buffered", provider="gcp", vcpu=24, ram_gb=96, price_hr=1.10),
    ]
    results = rank(profile, candidates)
    by_id = {r.instance.id: r for r in results}
    assert not by_id["exact"].disqualified          # kept
    assert by_id["exact"].perf_score == 0.0         # below the buffered target
    assert by_id["buffered"].perf_score == 1.0
    assert results[0].instance.id == "buffered"     # preference wins under performance mode


def test_headroom_hard_floor_takes_max_with_ram_floor_gb():
    """When both are set, the RAM floor is max(ram_floor_gb, headroom target)."""
    # headroom target 64 * 1.5 = 96 exceeds the explicit ram_floor_gb of 70
    profile = WorkloadProfile(
        vcpu=8, ram_gb=64, ram_floor_gb=70, headroom=0.5, headroom_mode=HeadroomMode.hard,
    )
    assert profile.effective_ram_floor_gb == 96.0
    under = MachineType(id="under", provider="gcp", vcpu=16, ram_gb=80, price_hr=1.0)
    ok = MachineType(id="ok",       provider="gcp", vcpu=16, ram_gb=96, price_hr=1.2)
    by_id = {r.instance.id: r for r in rank(profile, [under, ok])}
    assert by_id["under"].disqualified
    assert not by_id["ok"].disqualified


def test_headroom_zero_preserves_sub_nominal_ram_floor():
    """Regression: with headroom=0, an explicit ram_floor_gb below ram_gb is honored."""
    profile = WorkloadProfile(vcpu=8, ram_gb=224, ram_floor_gb=200)
    assert profile.effective_ram_floor_gb == 200.0
    inst = MachineType(id="lean", provider="gcp", vcpu=16, ram_gb=210, price_hr=1.0)
    assert not rank(profile, [inst])[0].disqualified


def test_readme_headline_example_matches_docs():
    """Pin the README quick-start numbers to CI so docs cannot silently drift.

    If this fails, either the scoring changed (update the README output block)
    or the README was edited by hand to numbers the engine does not produce.
    """
    profile = WorkloadProfile(vcpu=60, ram_gb=224, workload="io-intensive",
                              archetype="io", optimize_for=OptimizeFor.balanced)
    candidates = [
        MachineType(id="c2-standard-60",       provider="gcp", vcpu=60, ram_gb=240, price_hr=3.13),
        MachineType(id="c3d-standard-60-lssd", provider="gcp", vcpu=60, ram_gb=240, price_hr=3.39),
        MachineType(id="t2d-standard-60",      provider="gcp", vcpu=60, ram_gb=240, price_hr=2.31),
        MachineType(id="c7i.24xlarge",         provider="aws", vcpu=96, ram_gb=192, price_hr=4.28),
    ]
    shown = [(r.instance.id, round(r.score, 2)) for r in rank(profile, candidates)]
    assert shown == [
        ("t2d-standard-60", 1.00),
        ("c2-standard-60", 0.75),
        ("c3d-standard-60-lssd", 0.67),
        ("c7i.24xlarge", 0.00),
    ]
