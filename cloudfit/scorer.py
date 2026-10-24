"""Weighted scoring engine for cloudfit-core."""

from __future__ import annotations
import re
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

# AWS EC2 families on the same n2 = 1.00 scale. Kept separate from PERF_FACTORS
# because AWS and GCP reuse family names for different hardware (AWS c3/c4/m3 are
# 2013-2015 Intel parts; GCP c3/c4 are current generation).
#
# Keys are the base family; attribute suffixes (d = local NVMe, n = network,
# e = extra memory/storage, b = EBS, z = high frequency) and anything after a
# dash (-flex, u-6tb1, u7i-12tb, p6-b200) resolve to it via _aws_family, so m6idn
# and m6i share a factor. Graviton and AMD 7th-gen-and-later
# families run one physical core per vCPU (no SMT), hence their higher per-vCPU
# factors. Burstable t-series factors are conservative: sustained use beyond the
# CPU-credit baseline is throttled or billed extra.
#
# Source basis: same as PERF_FACTORS (vendor SPECrate2017_int per vCPU where
# published, else CoreMark/Geekbench per-vCPU medians), normalized to GCP n2.
# Read 2026-10-23. Indicative and overridable, like PERF_FACTORS.
AWS_PERF_FACTORS: dict[str, float] = {
    # burstable (see note above)
    "t2":    0.60,   # Haswell/Broadwell, CPU credits
    "t3a":   0.65,   # AMD Naples/Rome, CPU credits
    "t3":    0.70,   # Skylake/Cascade Lake, CPU credits
    "t4g":   0.70,   # Graviton2, CPU credits
    "t8i":   0.70,   # Granite Rapids, CPU credits
    # previous generations (the oldest are approximate)
    "t1":    0.40,   # 2008-era, CPU credits
    "m1":    0.50,   # 2006-era first generation
    "c1":    0.50,   # 2008-era first generation
    "m2":    0.55,   # Nehalem
    "cc2":   0.65,   # Sandy Bridge, cluster compute
    "cr1":   0.65,   # Sandy Bridge, memory-optimized
    "hs1":   0.60,   # Sandy Bridge, dense storage
    "g2":    0.65,   # GRID K520 host, Sandy Bridge
    "i2":    0.70,   # Ivy Bridge, storage-optimized
    "m3":    0.70,   # Ivy Bridge
    "r3":    0.70,   # Ivy Bridge
    "c3":    0.75,   # Ivy Bridge, higher clock
    "x1":    0.75,   # Haswell, memory-optimized
    "d2":    0.75,   # Haswell, dense storage
    "m4":    0.80,   # Broadwell/Haswell
    "r4":    0.80,   # Broadwell
    "i3":    0.80,   # Broadwell, storage-optimized
    "c4":    0.85,   # Haswell, higher clock
    "p2":    0.80,   # K80 host, Broadwell
    "g3":    0.80,   # M60 host, Broadwell
    "g3s":   0.80,   # M60 host, Broadwell
    "h1":    0.80,   # Broadwell, dense storage
    "p3":    0.85,   # V100 host, Broadwell
    # Intel
    "m5":    0.95,   # Skylake/Cascade Lake
    "u":     0.95,   # high memory (u-6tb1 etc.), Skylake/Cascade Lake
    "r5":    0.95,   # Skylake/Cascade Lake
    "d3":    0.95,   # Cascade Lake, dense storage
    "g4":    0.95,   # T4 host (g4dn), Cascade Lake
    "c5":    1.00,   # Skylake/Cascade Lake, 3.4-3.6 GHz
    "p4":    1.00,   # A100 host (p4d), Cascade Lake
    "m6i":   1.00,   # Ice Lake
    "r6i":   1.00,   # Ice Lake
    "i4i":   1.00,   # Ice Lake, storage-optimized
    "x2i":   1.00,   # Ice Lake, memory-optimized
    "c6i":   1.05,   # Ice Lake, 3.5 GHz
    "z1":    1.10,   # Skylake, 4.0 GHz sustained
    "m5zn":  1.15,   # Cascade Lake, 4.5 GHz
    "m7i":   1.20,   # Sapphire Rapids
    "c7i":   1.20,   # Sapphire Rapids
    "i7i":   1.25,   # Emerald Rapids, storage-optimized
    "r7i":   1.20,   # Sapphire Rapids
    "u7i":   1.20,   # high memory, Sapphire Rapids
    "p6":    1.25,   # B200 / B300 host (p6-b200), Emerald Rapids
    "m8i":   1.30,   # Granite Rapids
    "c8i":   1.30,   # Granite Rapids
    "r8i":   1.30,   # Granite Rapids
    "x8i":   1.30,   # Granite Rapids, memory-optimized
    # AMD
    "m5a":   0.80,   # Naples/Rome
    "r5a":   0.80,   # Naples/Rome
    "c5a":   0.85,   # Rome, 3.3 GHz
    "g4a":   0.85,   # Radeon V520 host (g4ad), Rome
    "m6a":   0.95,   # Milan
    "c6a":   0.95,   # Milan
    "r6a":   0.95,   # Milan
    "g5":    0.95,   # A10G host, Milan
    "g6":    0.95,   # L4 / L40S host (g6, g6e), Milan
    "gr6":   0.95,   # L4 host with extra memory, Milan
    "p5":    0.95,   # H100 / H200 host, Milan
    "hpc6a": 1.10,   # Milan, no SMT
    "m7a":   1.35,   # Genoa, no SMT
    "c7a":   1.35,   # Genoa, no SMT
    "r7a":   1.35,   # Genoa, no SMT
    "hpc7a": 1.35,   # Genoa, no SMT
    "m8a":   1.50,   # Turin, no SMT
    "c8a":   1.50,   # Turin, no SMT
    "r8a":   1.50,   # Turin, no SMT
    # Graviton (Arm, no SMT)
    "a1":    0.70,   # Graviton1
    "m6g":   0.90,   # Graviton2
    "c6g":   0.90,   # Graviton2
    "r6g":   0.90,   # Graviton2
    "x2g":   0.90,   # Graviton2, memory-optimized
    "g5g":   0.90,   # T4G host, Graviton2
    "i4g":   0.90,   # Graviton2, storage-optimized
    "im4g":  0.90,   # Graviton2, storage-optimized
    "is4g":  0.90,   # Graviton2, storage-optimized
    "m7g":   1.10,   # Graviton3
    "c7g":   1.10,   # Graviton3
    "r7g":   1.10,   # Graviton3
    "hpc7g": 1.10,   # Graviton3E
    "m8g":   1.35,   # Graviton4
    "c8g":   1.35,   # Graviton4
    "r8g":   1.35,   # Graviton4
    "x8g":   1.35,   # Graviton4, memory-optimized
    "i8g":   1.35,   # Graviton4, storage-optimized
}
_AWS_ATTRIBUTE_SUFFIXES = "bdenz"
_AWS_FAMILY_PATTERN = re.compile(r"^([a-z]+)(\d+)([a-z]*)$")


def _aws_family(instance_type: str) -> str:
    """Base family for an EC2 instance type, e.g. ``"m6i"`` from ``"m6idn.xlarge"``.

    Drops anything after a dash (``-flex``, ``-12tb``) and strips trailing
    attribute letters until the family is in
    AWS_PERF_FACTORS (exact entries such as ``m5zn`` match first). Processor
    letters (g, i, a) are never stripped.
    """
    family = instance_type.split(".")[0].lower().split("-")[0]
    while (
        family not in AWS_PERF_FACTORS
        and len(family) > 1
        and family[-1] in _AWS_ATTRIBUTE_SUFFIXES
    ):
        family = family[:-1]
    return family


def perf_factor_for(machine_id: str) -> float:
    """Look up the relative per-vCPU performance factor for a machine id.

    GCP ids use the token before the first ``-`` (``"c3"`` from
    ``"c3-standard-44"``) in PERF_FACTORS. AWS instance types, which always
    contain a ``.`` (``"m7i.2xlarge"``), resolve their base family in
    AWS_PERF_FACTORS, falling back to the newest listed earlier generation of
    the same line. Unknown families return the baseline ``1.0`` so an
    unrecognized id is scored on raw counts, exactly as before these tables existed.

    Providers call this to populate ``MachineType.perf_factor``; the scorer itself
    only reads the field, so an instance with ``perf_factor=None`` is unaffected.
    """
    if "." in machine_id:
        return _aws_perf_factor(machine_id)
    family = machine_id.split("-")[0].lower()
    return PERF_FACTORS.get(family, 1.0)


def _aws_perf_factor(instance_type: str) -> float:
    """AWS_PERF_FACTORS lookup with a previous-generation fallback.

    A family newer than the table (e.g. ``m9g`` when the table stops at
    ``m8g``) takes the factor of the newest listed generation of the same
    class and processor line, so a new generation is rated at least as fast as
    the one before it instead of dropping to the 1.0 baseline.
    """
    family = _aws_family(instance_type)
    if family in AWS_PERF_FACTORS:
        return AWS_PERF_FACTORS[family]
    match = _AWS_FAMILY_PATTERN.match(family)
    if match:
        kind, generation, line = match.group(1), int(match.group(2)), match.group(3)
        for older in range(generation - 1, 0, -1):
            candidate = _aws_family(f"{kind}{older}{line}")
            if candidate in AWS_PERF_FACTORS:
                return AWS_PERF_FACTORS[candidate]
    return 1.0


# Relative ML throughput per accelerator, normalized to H100 = 1.00 (scoring-depth
# design, Layer 3). VRAM alone cannot tell an A100 from a T4 with equal memory, so
# the gpu archetype scores effective GPU capacity (gpu_count * class) when an
# instance declares gpu_type. VRAM remains a hard floor in filter.py.
#
# Source basis: vendor-published peak dense FP16/BF16 tensor TFLOPS, normalized to
# H100. Read 2026-06-23; AWS accelerators (h200, l40s, a10g, v520, m60, k80) added
# 2026-10-23. Indicative, overridable, refreshed as new SKUs land.
#
# Lookup takes the first key contained in the gpu_type string, so a key that is a
# substring of another (l4 in l40s) must come after it. Accelerators faster than
# the H100 reference (e.g. B200) are not listed yet: adding one would raise the
# full-credit reference and shift every existing GPU score.
GPU_CLASS: dict[str, float] = {
    "h100": 1.00,   # Hopper, ~990 TFLOPS BF16 dense
    "h200": 1.00,   # Hopper, same compute as H100 with more HBM
    "l40s": 0.37,   # Ada, ~362 TFLOPS BF16 dense (before l4: "l4" is in "l40s")
    "a100": 0.32,   # Ampere, ~312 TFLOPS (40GB and 80GB share compute)
    "v100": 0.13,   # Volta, ~125 TFLOPS FP16
    "l4":   0.12,   # Ada, ~121 TFLOPS BF16 dense
    "a10g": 0.07,   # Ampere, AWS A10 variant at lower clocks, ~70 TFLOPS FP16 dense
    "t4":   0.07,   # Turing, ~65 TFLOPS FP16 (also matches the Arm-host T4G)
    "p100": 0.02,   # Pascal, ~19 TFLOPS FP16
    "v520": 0.02,   # AMD Radeon Pro V520, ~15 TFLOPS FP16, no tensor cores
    "p4":   0.01,   # Pascal, ~5 TFLOPS FP16
    "m60":  0.01,   # Maxwell, ~10 TFLOPS FP32, no FP16 tensor cores
    "k80":  0.01,   # Kepler, ~9 TFLOPS FP32, no FP16 tensor cores
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
    """Cost score in 0-1: the cheapest qualified cost basis divided by this one.

    The cheapest qualified option scores 1.0 and one costing twice as much per unit
    of work scores 0.5, however expensive the priciest candidate is. (A linear
    min-max scale let one very large qualifying instance, e.g. a $250/hr GPU box
    that also meets a 16 vCPU request, squeeze every sensibly sized option to
    ~0.999, so cost stopped ranking anything.) An unpriced instance scores 0.0: a
    missing price is never treated as free. When no range is available (standalone
    scoring), fall back to a fixed reference.
    """
    basis = _cost_basis(instance, mode)
    if basis <= 0:
        return 0.0
    if basis_range is None:
        return max(0.0, 1.0 - (basis / _MAX_PRICE_HR))
    floor = basis_range[0]
    return min(1.0, floor / basis)


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
        # AWS types name the family before the "." (c8g.8xlarge); GCP before the "-".
        family = instance.id.split(".")[0] if "." in instance.id else instance.id.split("-")[0]
        line += f"; {family.lower()} cores ~{pct}% {direction} than baseline"

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

    Cost is scored relative to the cheapest qualified, priced candidate, which
    scores 1.0; the rest score cheapest / own cost basis. The cost basis uses the
    profile's effective pricing mode (spot/on-demand/committed-use).
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
