"""Weighted scoring engine for cloudfit-core."""

from __future__ import annotations
from .models import (
    WorkloadProfile,
    MachineType,
    ScoredInstance,
    OptimizeFor,
    Archetype,
    PricingMode,
)
from .filter import hard_floor_check

WEIGHT_MATRIX: dict[str, dict[str, float]] = {
    OptimizeFor.cost:         {"cost": 0.70, "perf": 0.20, "avail": 0.10},
    OptimizeFor.balanced:     {"cost": 0.33, "perf": 0.34, "avail": 0.33},
    OptimizeFor.performance:  {"cost": 0.10, "perf": 0.80, "avail": 0.10},
    OptimizeFor.availability: {"cost": 0.10, "perf": 0.20, "avail": 0.70},
}

# Fallback price reference, used only when score_instance() is called standalone
# without a candidate price range (e.g. scoring a single instance). rank()
# always supplies a candidate-relative range, which is the preferred path.
_MAX_PRICE_HR = 35.0

# Performance scoring (fit-based): peak at an exact fit to the (headroom-adjusted)
# target, then linear decay to 0 at well-oversized (3.5x). There is no flat
# "ideal band": the headroom parameter already recenters the target on the
# buffered size, so an instance sized exactly to the target scores highest and
# every step of oversize costs score. This lets perf discriminate between
# candidates instead of flat-lining at 1.0 across a 1.0x-1.5x range.
_PERF_DECAY_END_RATIO = 3.5

# Per-archetype perf weighting. Each archetype emphasizes the dimensions that
# dominate its workload. Weights sum to 1.0 so perf_score stays in 0-1.
# Components: vcpu, ram, ssd (local SSD vs scratch_tb), gpu.
_ARCHETYPE_PERF_WEIGHTS: dict[Archetype, dict[str, float]] = {
    Archetype.io:    {"vcpu": 0.3, "ram": 0.3, "ssd": 0.4},
    Archetype.cpu:   {"vcpu": 0.7, "ram": 0.3},
    Archetype.mem:   {"vcpu": 0.2, "ram": 0.8},
    Archetype.gpu:   {"vcpu": 0.1, "ram": 0.1, "gpu": 0.8},
    Archetype.burst: {"vcpu": 0.5, "ram": 0.5},
}

# Normalize long-form weight keys to short internal keys.
# Users may pass "performance" or "availability" (as documented in README);
# the engine uses "perf" and "avail" internally.
_KEY_ALIASES: dict[str, str] = {
    "performance": "perf",
    "availability": "avail",
}

# Per-family relative per-vCPU performance, normalized to a baseline of n2 = 1.00.
#
# This is the "effective capacity" table (scoring-depth design, Layer 1): a vCPU
# is not a fixed unit of work, so a faster family delivers more per core. When a
# MachineType carries a perf_factor (providers populate it via perf_factor_for),
# perf scoring fits effective capacity (vcpu * perf_factor) to the request, so a
# slower family must bring more physical cores to score the same.
#
# Source basis: vendor-published SPECrate2017_int per-vCPU where available, else
# community CoreMark/Geekbench per-vCPU medians, normalized to n2. Read 2026-06-23.
# These are INDICATIVE heuristics pending real run-telemetry calibration, and are
# overridable: set MachineType.perf_factor explicitly to bypass this table.
# Refresh policy: revisit when a new family generation lands.
_BASELINE_FAMILY = "n2"
PERF_FACTORS: dict[str, float] = {
    "e2":   0.80,   # shared-core / variable, oldest general-purpose
    "n1":   0.85,   # Skylake/Broadwell
    "t2a":  0.85,   # Ampere Altra (Arm)
    "m1":   0.85,   # memory-optimized, first gen
    "t2d":  0.90,   # AMD Milan, scale-out tuned
    "m2":   0.90,   # memory-optimized, second gen
    "n2d":  0.95,   # AMD Rome/Milan
    "n2":   1.00,   # Cascade/Ice Lake — BASELINE
    "a2":   1.00,   # A100 host; CPU is Cascade Lake
    "m3":   1.05,   # memory-optimized, Ice Lake
    "c2d":  1.08,   # AMD Milan, compute-optimized
    "c2":   1.10,   # Cascade Lake, higher all-core clock
    "n4":   1.10,   # Emerald Rapids, Titanium
    "g2":   1.10,   # L4 host; Cascade Lake CPU
    "c3d":  1.18,   # AMD Genoa
    "c3":   1.20,   # Intel Sapphire Rapids
    "a3":   1.20,   # H100 host; Sapphire Rapids CPU
    "c4":   1.25,   # Intel Emerald Rapids
    "h3":   1.25,   # Sapphire Rapids, HPC
}


def perf_factor_for(machine_id: str) -> float:
    """Look up the relative per-vCPU performance factor for a machine id.

    The family token is the part before the first ``-`` (e.g. ``"c3"`` from
    ``"c3-standard-44"``). Unknown families return the baseline ``1.0`` so an
    unrecognized id is scored on raw counts, exactly as before this table existed.

    Providers call this to populate ``MachineType.perf_factor``; the scorer itself
    only reads the field, so an instance with ``perf_factor=None`` is unaffected.
    """
    family = machine_id.split("-")[0].lower()
    return PERF_FACTORS.get(family, 1.0)


# Relative ML throughput per accelerator, normalized to H100 = 1.00 (scoring-depth
# design, Layer 3). VRAM alone cannot tell an A100 from a T4 with equal memory, so
# the gpu archetype scores effective GPU capacity (gpu_count * class) when an
# instance declares gpu_type. VRAM remains a hard floor in filter.py.
#
# Source basis: vendor-published peak dense FP16/BF16 tensor TFLOPS, normalized to
# H100. Read 2026-06-23. Indicative, overridable, refreshed as new SKUs land.
GPU_CLASS: dict[str, float] = {
    "h100": 1.00,   # Hopper, ~990 TFLOPS BF16 dense
    "a100": 0.32,   # Ampere, ~312 TFLOPS (40GB and 80GB share compute)
    "v100": 0.13,   # Volta, ~125 TFLOPS FP16
    "l4":   0.12,   # Ada, ~121 TFLOPS BF16 dense
    "t4":   0.07,   # Turing, ~65 TFLOPS FP16
    "p100": 0.02,   # Pascal, ~19 TFLOPS FP16
    "p4":   0.01,   # Pascal, ~5 TFLOPS FP16
}
_GPU_CLASS_REFERENCE = max(GPU_CLASS.values())   # full credit at one top-class GPU


def gpu_class_for(gpu_type: str) -> float | None:
    """Relative throughput class for an accelerator name, or None if unrecognized.

    Matches the model token within the provider's gpu_type string (e.g. "a100"
    in "nvidia-a100-80gb"). Returns None for an unknown accelerator so the scorer
    falls back to VRAM-fit scoring, exactly as before this table existed.
    """
    token = gpu_type.lower()
    for name, value in GPU_CLASS.items():
        if name in token:
            return value
    return None


def _normalize_weights(weights: dict[str, float]) -> dict[str, float]:
    """Accept both short (perf/avail) and long (performance/availability) keys."""
    return {_KEY_ALIASES.get(k, k): v for k, v in weights.items()}


def _effective_price(instance: MachineType, mode: PricingMode) -> float:
    """Price/hr for the requested pricing mode, falling back to on-demand.

    Spot and committed-use prices are optional; when a mode-specific price is
    absent (or non-positive) the on-demand price_hr is used, so a partial price
    set never makes an instance look free.
    """
    if mode == PricingMode.spot and instance.spot_price_hr and instance.spot_price_hr > 0:
        return instance.spot_price_hr
    if mode == PricingMode.cud_1yr and instance.cud_1yr_price_hr and instance.cud_1yr_price_hr > 0:
        return instance.cud_1yr_price_hr
    return instance.price_hr


def _cost_basis(instance: MachineType, mode: PricingMode) -> float:
    """Cost per unit of work: effective price / perf_factor (Layer 2).

    Dividing by perf_factor (not by physical vCPU) models total cost to finish a
    fixed job, since runtime scales with effective speed: a faster family is
    cheaper per unit of work even at the same hourly price, while a larger-but-
    same-speed box is not rewarded for cores beyond what the request needs (that
    is the perf fit's job). With perf_factor unset this reduces to the raw price,
    so cost normalization is unchanged from before this layer. Returns 0.0 for an
    unpriced instance (sentinel: handled as "not free" by _cost_score).
    """
    price = _effective_price(instance, mode)
    if price <= 0:
        return 0.0
    return price / (instance.perf_factor or 1.0)


def _cost_basis_range(
    candidates: list[MachineType], mode: PricingMode
) -> tuple[float, float] | None:
    """Min and max cost basis across priced candidates, used to normalize cost.

    Unpriced instances are excluded: a missing price is not a free instance and
    must not anchor the bottom of the range. Returns None when no candidate
    carries a usable price.
    """
    bases = [b for c in candidates if (b := _cost_basis(c, mode)) > 0]
    if not bases:
        return None
    return (min(bases), max(bases))


def _cost_score(
    instance: MachineType,
    basis_range: tuple[float, float] | None,
    mode: PricingMode,
) -> float:
    """Cost score in 0-1. Lowest cost basis scores 1.0, highest 0.0.

    Candidate-relative normalization spreads the qualified set across the full
    range, so a real cost gap moves the score. An unpriced instance scores 0.0: a
    missing price is never treated as free. When no range is available (standalone
    scoring), fall back to a fixed reference.
    """
    basis = _cost_basis(instance, mode)
    if basis <= 0:
        return 0.0
    if basis_range is None:
        return max(0.0, 1.0 - (basis / _MAX_PRICE_HR))
    floor, ceiling = basis_range
    if ceiling <= floor:
        return 1.0
    return max(0.0, min(1.0, (ceiling - basis) / (ceiling - floor)))


def _fit(have: float, want: float) -> float:
    """Score how well `have` fits `want`. Peak at an exact fit (1.0x), decays above.

    Under 1.0x returns 0 (under-spec); the hard floor disqualifies these before
    they reach the scorer in normal flow, but we return 0 defensively. From an
    exact fit the score decays linearly to 0 at _PERF_DECAY_END_RATIO, so a
    tighter fit always outscores a more oversized one.
    """
    if want <= 0:
        return 1.0
    ratio = have / want
    if ratio < 1.0:
        return 0.0
    return max(0.0, 1.0 - (ratio - 1.0) / (_PERF_DECAY_END_RATIO - 1.0))


def _perf_score(instance: MachineType, profile: WorkloadProfile) -> float:
    """Reward instances that fit the request. Exact match wins; oversize is penalized.

    Fit is measured against the headroom-adjusted target (declared * (1 + headroom)),
    so when headroom is set the peak band recenters on the buffered size. With the
    default headroom=0 the target equals the declared request.

    The per-component weighting is archetype-specific (see _ARCHETYPE_PERF_WEIGHTS):
    cpu workloads weight vCPU, mem workloads weight RAM, io workloads weight local
    SSD against the declared scratch_tb, and gpu workloads weight GPU VRAM. A
    component with no declared requirement (e.g. no scratch_tb) fits perfectly and
    does not distort the score.

    vCPU fit is measured on effective capacity (vcpu * perf_factor) when the
    instance declares a perf_factor, so a faster family is not equated with a
    slower one at the same core count. With perf_factor unset the effective
    capacity equals the physical vCPU count and scoring is unchanged.

    The gpu component scores effective GPU throughput (gpu_count * GPU_CLASS) when
    the instance declares a recognized gpu_type, so a faster accelerator outscores
    a slower one at equal VRAM (VRAM stays a hard floor). With gpu_type unset it
    falls back to VRAM fit, unchanged from before Layer 3.
    """
    arch = Archetype(profile.archetype)
    weights = _ARCHETYPE_PERF_WEIGHTS[arch]
    effective_vcpu = instance.vcpu * (instance.perf_factor or 1.0)
    fits = {
        "vcpu": _fit(effective_vcpu, profile.perf_target_vcpu),
        "ram": _fit(instance.ram_gb, profile.perf_target_ram_gb),
        "ssd": _fit(instance.local_ssd_tb, profile.disk.scratch_tb or 0.0),
        "gpu": _gpu_score(instance, profile),
    }
    return sum(weight * fits[component] for component, weight in weights.items())


def _gpu_score(instance: MachineType, profile: WorkloadProfile) -> float:
    """GPU perf term: effective throughput when gpu_type is known, else VRAM fit.

    With a recognized gpu_type, score effective GPU capacity (gpu_count * class)
    normalized to one top-class accelerator, so an A100 beats a T4 and two GPUs
    beat one. Without it, fall back to the original VRAM fit against the request.
    """
    gpu_class = gpu_class_for(instance.gpu_type) if instance.gpu_type else None
    if gpu_class is not None:
        effective_gpu = instance.gpu_count * gpu_class
        return min(1.0, effective_gpu / _GPU_CLASS_REFERENCE)
    return _fit(float(instance.gpu_vram_gb or 0), float(profile.gpu.vram_gb or 0))


def _avail_score(instance: MachineType) -> float:
    """Active > deprecated > tombstoned."""
    return {"active": 1.0, "deprecated": 0.4, "tombstoned": 0.0}.get(
        instance.status, 0.5
    )


_COMPONENT_LABELS: dict[str, str] = {
    "cost": "cost",
    "perf": "performance fit",
    "avail": "availability",
}


def _reason(
    instance: MachineType, contributions: dict[str, float], mode: PricingMode
) -> str:
    """One human-readable line explaining what drove this instance's score.

    Names the component that contributed the most to the composite, flags a
    non-baseline perf_factor so a speed advantage (or penalty) is visible rather
    than buried in the perf sub-score, and notes when the cost reflects an
    interruptible spot price.
    """
    driver = max(contributions, key=lambda k: contributions[k])
    line = f"Led by {_COMPONENT_LABELS[driver]}"

    factor = instance.perf_factor
    if factor is not None and abs(factor - 1.0) >= 0.01:
        pct = round(abs(factor - 1.0) * 100)
        direction = "faster" if factor > 1.0 else "slower"
        family = instance.id.split("-")[0].lower()
        line += f"; {family} cores ~{pct}% {direction} than baseline"

    if mode == PricingMode.spot and instance.spot_price_hr and instance.spot_price_hr > 0:
        line += "; priced on spot (interruptible)"
    elif mode == PricingMode.cud_1yr and instance.cud_1yr_price_hr and instance.cud_1yr_price_hr > 0:
        line += "; priced on 1-year committed use"

    return line + "."


def score_instance(
    instance: MachineType,
    profile: WorkloadProfile,
    *,
    price_range: tuple[float, float] | None = None,
) -> ScoredInstance:
    """Score a single instance against a workload profile.

    Returns a ScoredInstance with composite score and sub-scores.
    Disqualified instances have score=0.0 and disqualified=True.

    `price_range` is the (min, max) cost basis of the candidate set, used to
    normalize the cost score across the actual options. rank() supplies it
    automatically; callers scoring a lone instance can omit it and a fixed
    reference is used instead. The cost basis is the price for the profile's
    effective pricing mode divided by perf_factor (see _cost_basis).
    """
    reason = hard_floor_check(instance, profile)
    if reason:
        return ScoredInstance(
            instance=instance, score=0.0,
            cost_score=0.0, perf_score=0.0, avail_score=0.0,
            disqualified=True, disqualify_reason=reason,
            reason=reason,
        )

    mode = profile.effective_pricing_mode
    raw_weights = profile.weights or WEIGHT_MATRIX[profile.optimize_for]
    weights = _normalize_weights(raw_weights)

    c = _cost_score(instance, price_range, mode)
    p = _perf_score(instance, profile)
    a = _avail_score(instance)

    w_cost = weights.get("cost", 0.33)
    w_perf = weights.get("perf", 0.34)
    w_avail = weights.get("avail", 0.33)

    # Each component's actual contribution to the composite (weight * sub-score).
    # These sum to the composite, so the breakdown is exact, not illustrative.
    contributions = {
        "cost": round(w_cost * c, 4),
        "perf": round(w_perf * p, 4),
        "avail": round(w_avail * a, 4),
    }
    composite = w_cost * c + w_perf * p + w_avail * a

    return ScoredInstance(
        instance=instance,
        score=round(composite, 4),
        cost_score=round(c, 4),
        perf_score=round(p, 4),
        avail_score=round(a, 4),
        contributions=contributions,
        reason=_reason(instance, contributions, mode),
    )


# Alias, both names are public API
score = score_instance


def rank(
    profile: WorkloadProfile,
    candidates: list[MachineType],
) -> list[ScoredInstance]:
    """Score and rank all candidates.

    Qualified instances are returned first, sorted by score descending.
    Disqualified instances appear at the end (score=0.0).

    Cost is normalized across the qualified, priced candidates so the cheapest
    qualifying option scores 1.0 and the most expensive 0.0. The cost basis uses
    the profile's effective pricing mode (spot/on-demand/committed-use).
    """
    mode = profile.effective_pricing_mode
    qualified_priced = [
        m for m in candidates if hard_floor_check(m, profile) is None
    ]
    price_range = _cost_basis_range(qualified_priced, mode)

    scored = [score_instance(m, profile, price_range=price_range) for m in candidates]
    qualified = sorted(
        [s for s in scored if not s.disqualified],
        key=lambda s: s.score,
        reverse=True,
    )
    disqualified = [s for s in scored if s.disqualified]
    return qualified + disqualified
