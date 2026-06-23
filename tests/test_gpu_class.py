"""Tests for GPU SKU discrimination (scoring-depth Layer 3)."""

from cloudfit import (
    GPU_CLASS,
    GPUSpec,
    MachineType,
    OptimizeFor,
    WorkloadProfile,
    gpu_class_for,
    rank,
    score_instance,
)

GPU_PROFILE = WorkloadProfile(
    vcpu=12, ram_gb=85, archetype="gpu",
    gpu=GPUSpec(required=True, vram_gb=40),
    optimize_for=OptimizeFor.performance,
)


# --- gpu_class_for lookup ---

def test_gpu_class_for_matches_token_in_name():
    assert gpu_class_for("nvidia-a100-80gb") == GPU_CLASS["a100"]
    assert gpu_class_for("nvidia-tesla-t4") == GPU_CLASS["t4"]
    assert gpu_class_for("nvidia-h100-80gb") == GPU_CLASS["h100"]


def test_gpu_class_for_unknown_returns_none():
    assert gpu_class_for("nvidia-future-9000") is None


def test_gpu_class_orders_a100_above_t4():
    assert GPU_CLASS["a100"] > GPU_CLASS["t4"]


# --- effective GPU throughput scoring ---

def test_a100_outscores_t4_at_equal_vram():
    """The Layer 3 goal: faster accelerator wins even when VRAM (the floor) is equal."""
    common = dict(provider="gcp", vcpu=12, ram_gb=85, gpu_count=1, gpu_vram_gb=40)
    a100 = MachineType(id="a2-highgpu-1g", gpu_type="nvidia-tesla-a100", price_hr=3.67, **common)
    t4 = MachineType(id="n1-gpu-t4", gpu_type="nvidia-tesla-t4", price_hr=3.67, **common)
    by_id = {r.instance.id: r for r in rank(GPU_PROFILE, [a100, t4])}
    assert by_id["a2-highgpu-1g"].perf_score > by_id["n1-gpu-t4"].perf_score
    assert rank(GPU_PROFILE, [a100, t4])[0].instance.id == "a2-highgpu-1g"


def test_more_gpus_score_higher():
    common = dict(provider="gcp", vcpu=12, ram_gb=85, gpu_vram_gb=40,
                  gpu_type="nvidia-tesla-a100", price_hr=3.67)
    one = MachineType(id="one", gpu_count=1, **common)
    two = MachineType(id="two", gpu_count=2, **common)
    by_id = {r.instance.id: r for r in rank(GPU_PROFILE, [one, two])}
    assert by_id["two"].perf_score > by_id["one"].perf_score


def test_top_class_gpu_gets_full_credit():
    h100 = MachineType(id="a3-highgpu-1g", provider="gcp", vcpu=12, ram_gb=85,
                       gpu_count=1, gpu_vram_gb=80, gpu_type="nvidia-h100-80gb", price_hr=11.0)
    # gpu weight is 0.8; vcpu/ram fit at exact request -> perf approaches 1.0.
    assert score_instance(h100, GPU_PROFILE).perf_score > 0.95


def test_gpu_type_unset_is_a_noop():
    """Without gpu_type, the gpu term falls back to VRAM fit (pre-Layer-3 behavior)."""
    common = dict(provider="gcp", vcpu=12, ram_gb=85, gpu_count=1, price_hr=3.67)
    big_vram = MachineType(id="big", gpu_vram_gb=80, **common)
    exact_vram = MachineType(id="exact", gpu_vram_gb=40, **common)
    by_id = {r.instance.id: r for r in rank(GPU_PROFILE, [big_vram, exact_vram])}
    # VRAM fit peaks at the requested 40 and decays for oversize, unchanged by Layer 3.
    assert by_id["exact"].perf_score > by_id["big"].perf_score
