"""Tests for pricing modes and cost-efficiency (scoring-depth Layer 2)."""

from cloudfit import (
    MachineType,
    OptimizeFor,
    PricingMode,
    WorkloadProfile,
    rank,
    score_instance,
)


# --- effective pricing mode resolution ---

def test_burst_defaults_to_spot():
    p = WorkloadProfile(vcpu=8, ram_gb=32, archetype="burst")
    assert p.effective_pricing_mode == PricingMode.spot


def test_non_burst_defaults_to_on_demand():
    for arch in ("cpu", "mem", "io", "gpu"):
        p = WorkloadProfile(vcpu=8, ram_gb=32, archetype=arch)
        assert p.effective_pricing_mode == PricingMode.on_demand


def test_explicit_pricing_mode_overrides_default():
    p = WorkloadProfile(vcpu=8, ram_gb=32, archetype="burst",
                        pricing_mode=PricingMode.on_demand)
    assert p.effective_pricing_mode == PricingMode.on_demand


# --- price selection per mode ---

def test_spot_price_used_when_mode_spot():
    profile = WorkloadProfile(vcpu=8, ram_gb=32, archetype="cpu",
                              pricing_mode=PricingMode.spot, optimize_for=OptimizeFor.cost)
    spot = MachineType(id="n2-standard-8", provider="gcp", vcpu=8, ram_gb=32,
                       price_hr=2.0, spot_price_hr=0.6)
    on_dem = MachineType(id="n2-standard-8b", provider="gcp", vcpu=8, ram_gb=32,
                         price_hr=1.0)  # no spot -> falls back to 1.0 on-demand
    by_id = {r.instance.id: r for r in rank(profile, [spot, on_dem])}
    # spot 0.6 < on-demand 1.0 -> spot instance wins on cost
    assert by_id["n2-standard-8"].cost_score == 1.0
    assert by_id["n2-standard-8b"].cost_score == 0.0


def test_spot_falls_back_to_on_demand_when_absent():
    profile = WorkloadProfile(vcpu=8, ram_gb=32, pricing_mode=PricingMode.spot)
    inst = MachineType(id="n2-standard-8", provider="gcp", vcpu=8, ram_gb=32, price_hr=1.5)
    # No spot price: cost basis is the on-demand price; lone candidate scores 1.0.
    assert score_instance(inst, profile, price_range=(1.0, 2.0)).cost_score < 1.0


def test_cud_price_used_when_mode_cud():
    profile = WorkloadProfile(vcpu=8, ram_gb=32, pricing_mode=PricingMode.cud_1yr,
                              optimize_for=OptimizeFor.cost)
    cud = MachineType(id="a", provider="gcp", vcpu=8, ram_gb=32,
                      price_hr=2.0, cud_1yr_price_hr=1.2)
    flat = MachineType(id="b", provider="gcp", vcpu=8, ram_gb=32, price_hr=1.5)
    by_id = {r.instance.id: r for r in rank(profile, [cud, flat])}
    assert by_id["a"].cost_score == 1.0   # 1.2 committed < 1.5 on-demand
    assert by_id["b"].cost_score == 0.0


def test_spot_reason_flags_interruptible():
    profile = WorkloadProfile(vcpu=8, ram_gb=32, pricing_mode=PricingMode.spot)
    inst = MachineType(id="n2-standard-8", provider="gcp", vcpu=8, ram_gb=32,
                       price_hr=2.0, spot_price_hr=0.6)
    assert "spot" in score_instance(inst, profile, price_range=(0.5, 1.0)).reason


# --- cost efficiency (price per unit of work) ---

def test_faster_family_is_more_cost_efficient_at_equal_price():
    """A faster family wins on cost at the same hourly price: cheaper per unit of work."""
    profile = WorkloadProfile(vcpu=32, ram_gb=64, archetype="cpu",
                              optimize_for=OptimizeFor.cost)
    fast = MachineType(id="c3-standard-32", provider="gcp", vcpu=32, ram_gb=64,
                       price_hr=2.0, perf_factor=1.2)
    slow = MachineType(id="n2-standard-32", provider="gcp", vcpu=32, ram_gb=64,
                       price_hr=2.0, perf_factor=1.0)
    by_id = {r.instance.id: r for r in rank(profile, [fast, slow])}
    assert by_id["c3-standard-32"].cost_score > by_id["n2-standard-32"].cost_score


def test_oversize_is_not_rewarded_on_cost():
    """A bigger same-speed box must not look cheaper: cost basis ignores excess cores."""
    profile = WorkloadProfile(vcpu=16, ram_gb=64, archetype="cpu",
                              optimize_for=OptimizeFor.cost)
    exact = MachineType(id="exact", provider="gcp", vcpu=16, ram_gb=64, price_hr=0.78)
    big = MachineType(id="big", provider="gcp", vcpu=32, ram_gb=128, price_hr=1.55)
    by_id = {r.instance.id: r for r in rank(profile, [exact, big])}
    # cheaper absolute price (same perf_factor) wins; the larger box does not.
    assert by_id["exact"].cost_score > by_id["big"].cost_score


def test_pricing_default_is_a_noop_vs_raw_price():
    """With no perf_factor and default mode, cost ranking matches raw-price behavior."""
    profile = WorkloadProfile(vcpu=16, ram_gb=64, optimize_for=OptimizeFor.cost)
    candidates = [
        MachineType(id="cheap", provider="gcp", vcpu=16, ram_gb=64, price_hr=2.31),
        MachineType(id="dear", provider="gcp", vcpu=16, ram_gb=64, price_hr=3.39),
    ]
    by_id = {r.instance.id: r for r in rank(profile, candidates)}
    assert by_id["cheap"].cost_score == 1.0
    assert by_id["dear"].cost_score == 0.0
