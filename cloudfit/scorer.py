"""Weighted scoring engine for cloudfit-core."""

from __future__ import annotations
from .models import WorkloadProfile, MachineType, ScoredInstance, OptimizeFor, Archetype
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
# Components: vcpu, ram, ssd (local SSD vs scratch_tb), gpu_vram.
_ARCHETYPE_PERF_WEIGHTS: dict[Archetype, dict[str, float]] = {
    Archetype.io:    {"vcpu": 0.3, "ram": 0.3, "ssd": 0.4},
    Archetype.cpu:   {"vcpu": 0.7, "ram": 0.3},
    Archetype.mem:   {"vcpu": 0.2, "ram": 0.8},
    Archetype.gpu:   {"vcpu": 0.1, "ram": 0.1, "gpu_vram": 0.8},
    Archetype.burst: {"vcpu": 0.5, "ram": 0.5},
}

# Normalize long-form weight keys to short internal keys.
# Users may pass "performance" or "availability" (as documented in README);
# the engine uses "perf" and "avail" internally.
_KEY_ALIASES: dict[str, str] = {
    "performance": "perf",
    "availability": "avail",
}


def _normalize_weights(weights: dict[str, float]) -> dict[str, float]:
    """Accept both short (perf/avail) and long (performance/availability) keys."""
    return {_KEY_ALIASES.get(k, k): v for k, v in weights.items()}


def _price_range(candidates: list[MachineType]) -> tuple[float, float] | None:
    """Min and max price across priced candidates, used to normalize cost.

    Unpriced instances (price_hr <= 0) are excluded: a missing price is not a
    free instance and must not anchor the bottom of the range. Returns None when
    no candidate carries a usable price.
    """
    prices = [c.price_hr for c in candidates if c.price_hr > 0]
    if not prices:
        return None
    return (min(prices), max(prices))


def _cost_score(instance: MachineType, price_range: tuple[float, float] | None) -> float:
    """Cost score in 0-1. Cheapest candidate scores 1.0, most expensive 0.0.

    Candidate-relative normalization spreads the qualified set across the full
    range, so a real price gap moves the score. An unpriced instance
    (price_hr <= 0) scores 0.0: a missing price is never treated as free. When
    no range is available (standalone scoring), fall back to a fixed reference.
    """
    if instance.price_hr <= 0:
        return 0.0
    if price_range is None:
        return max(0.0, 1.0 - (instance.price_hr / _MAX_PRICE_HR))
    floor, ceiling = price_range
    if ceiling <= floor:
        return 1.0
    return max(0.0, min(1.0, (ceiling - instance.price_hr) / (ceiling - floor)))


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
    """
    arch = Archetype(profile.archetype)
    weights = _ARCHETYPE_PERF_WEIGHTS[arch]
    fits = {
        "vcpu": _fit(instance.vcpu, profile.perf_target_vcpu),
        "ram": _fit(instance.ram_gb, profile.perf_target_ram_gb),
        "ssd": _fit(instance.local_ssd_tb, profile.disk.scratch_tb or 0.0),
        "gpu_vram": _fit(
            float(instance.gpu_vram_gb or 0),
            float(profile.gpu.vram_gb or 0),
        ),
    }
    return sum(weight * fits[component] for component, weight in weights.items())


def _avail_score(instance: MachineType) -> float:
    """Active > deprecated > tombstoned."""
    return {"active": 1.0, "deprecated": 0.4, "tombstoned": 0.0}.get(
        instance.status, 0.5
    )


def score_instance(
    instance: MachineType,
    profile: WorkloadProfile,
    *,
    price_range: tuple[float, float] | None = None,
) -> ScoredInstance:
    """Score a single instance against a workload profile.

    Returns a ScoredInstance with composite score and sub-scores.
    Disqualified instances have score=0.0 and disqualified=True.

    `price_range` is the (min, max) price of the candidate set, used to
    normalize the cost score across the actual options. rank() supplies it
    automatically; callers scoring a lone instance can omit it and a fixed
    reference is used instead.
    """
    reason = hard_floor_check(instance, profile)
    if reason:
        return ScoredInstance(
            instance=instance, score=0.0,
            cost_score=0.0, perf_score=0.0, avail_score=0.0,
            disqualified=True, disqualify_reason=reason,
        )

    raw_weights = profile.weights or WEIGHT_MATRIX[profile.optimize_for]
    weights = _normalize_weights(raw_weights)

    c = _cost_score(instance, price_range)
    p = _perf_score(instance, profile)
    a = _avail_score(instance)

    composite = (
        weights.get("cost", 0.33) * c
        + weights.get("perf", 0.34) * p
        + weights.get("avail", 0.33) * a
    )

    return ScoredInstance(
        instance=instance,
        score=round(composite, 4),
        cost_score=round(c, 4),
        perf_score=round(p, 4),
        avail_score=round(a, 4),
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
    qualifying option scores 1.0 and the most expensive 0.0.
    """
    qualified_priced = [
        m for m in candidates if hard_floor_check(m, profile) is None
    ]
    price_range = _price_range(qualified_priced)

    scored = [score_instance(m, profile, price_range=price_range) for m in candidates]
    qualified = sorted(
        [s for s in scored if not s.disqualified],
        key=lambda s: s.score,
        reverse=True,
    )
    disqualified = [s for s in scored if s.disqualified]
    return qualified + disqualified
