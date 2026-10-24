"""Property-based tests: invariants of rank() that must hold for any input."""

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from cloudfit import (
    Archetype,
    CpuArch,
    GPUSpec,
    MachineType,
    OptimizeFor,
    PricingMode,
    WorkloadProfile,
    hard_floor_check,
    rank,
)

IDS = [
    "n2-standard-16", "c3d-standard-32", "t2a-standard-8", "e2-highmem-4", "a2-highgpu-1g",
    "m7i.4xlarge", "m8g.2xlarge", "c7gn.xlarge", "r6i.8xlarge", "g5.xlarge", "custom-box",
]


@st.composite
def machine_types(draw):
    price = draw(st.floats(min_value=0.0, max_value=100.0, allow_nan=False))
    gpus = draw(st.integers(min_value=0, max_value=8))
    return MachineType(
        id=draw(st.sampled_from(IDS)),
        provider=draw(st.sampled_from(["gcp", "aws"])),
        vcpu=draw(st.integers(min_value=1, max_value=256)),
        ram_gb=draw(st.floats(min_value=0.5, max_value=4096, allow_nan=False)),
        price_hr=price,
        spot_price_hr=draw(st.none() | st.floats(min_value=0.001, max_value=max(price, 0.001))),
        cud_1yr_price_hr=draw(st.none() | st.floats(min_value=0.001, max_value=max(price, 0.001))),
        perf_factor=draw(st.none() | st.floats(min_value=0.4, max_value=1.6)),
        local_ssd_tb=draw(st.floats(min_value=0.0, max_value=10.0, allow_nan=False)),
        gpu_count=gpus,
        gpu_vram_gb=draw(st.integers(min_value=8, max_value=192)) if gpus else None,
        gpu_type=draw(st.sampled_from([None, "nvidia-a100", "nvidia-t4", "nvidia-l40s"])) if gpus else None,
        region=draw(st.sampled_from(["us-central1", "us-east-1"])),
        status=draw(st.sampled_from(["active", "deprecated", "tombstoned"])),
    )


@st.composite
def profiles(draw):
    gpu_required = draw(st.booleans())
    return WorkloadProfile(
        vcpu=draw(st.integers(min_value=1, max_value=64)),
        ram_gb=draw(st.floats(min_value=1, max_value=512, allow_nan=False)),
        archetype=draw(st.sampled_from(list(Archetype))),
        optimize_for=draw(st.sampled_from(list(OptimizeFor))),
        pricing_mode=draw(st.none() | st.sampled_from(list(PricingMode))),
        cpu_arch=draw(st.none() | st.sampled_from(list(CpuArch))),
        region=draw(st.none() | st.sampled_from(["us-central1", "us-east-1"])),
        gpu=GPUSpec(
            required=gpu_required,
            vram_gb=draw(st.none() | st.integers(min_value=8, max_value=80)) if gpu_required else None,
        ),
    )


CASES = settings(max_examples=300, deadline=None)


@CASES
@given(profile=profiles(), candidates=st.lists(machine_types(), max_size=12))
def test_scores_are_bounded_and_explained(profile, candidates):
    for r in rank(profile, candidates):
        for value in (r.score, r.cost_score, r.perf_score, r.avail_score):
            assert 0.0 <= value <= 1.0
        if r.disqualified:
            assert r.score == 0.0 and r.disqualify_reason and r.contributions == {}
        else:
            assert sum(r.contributions.values()) == pytest.approx(r.score, abs=2e-3)
            assert r.reason


@CASES
@given(profile=profiles(), candidates=st.lists(machine_types(), max_size=12))
def test_rank_orders_qualified_by_score_then_disqualified(profile, candidates):
    results = rank(profile, candidates)
    assert sorted(id(r.instance) for r in results) == sorted(id(m) for m in candidates)
    flags = [r.disqualified for r in results]
    assert flags == sorted(flags)                      # qualified (False) first
    scores = [r.score for r in results if not r.disqualified]
    assert scores == sorted(scores, reverse=True)


@CASES
@given(profile=profiles(), candidates=st.lists(machine_types(), max_size=12))
def test_disqualification_matches_the_hard_floor(profile, candidates):
    for r in rank(profile, candidates):
        assert r.disqualified == (hard_floor_check(r.instance, profile) is not None)


@CASES
@given(profile=profiles(), base=machine_types(), cheaper_by=st.floats(min_value=0.01, max_value=0.9))
def test_a_cheaper_twin_never_scores_lower(profile, base, cheaper_by):
    """Two otherwise identical instances: the cheaper one never loses on cost or score."""
    base = base.model_copy(update={
        "price_hr": max(base.price_hr, 0.02), "spot_price_hr": None, "cud_1yr_price_hr": None,
    })
    cheaper = base.model_copy(update={"price_hr": base.price_hr * (1 - cheaper_by)})
    by_obj = {id(r.instance): r for r in rank(profile, [base, cheaper])}
    a, b = by_obj[id(base)], by_obj[id(cheaper)]
    if not a.disqualified:
        assert b.cost_score >= a.cost_score
        assert b.score >= a.score
