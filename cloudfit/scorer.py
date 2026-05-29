"""Weighted scoring engine for cloudfit-core."""

from __future__ import annotations
from .models import WorkloadProfile, MachineType, ScoredInstance, OptimizeFor

WEIGHT_MATRIX: dict[str, dict[str, float]] = {
    OptimizeFor.cost:         {"cost": 0.70, "perf": 0.20, "avail": 0.10},
    OptimizeFor.balanced:     {"cost": 0.33, "perf": 0.34, "avail": 0.33},
    OptimizeFor.performance:  {"cost": 0.10, "perf": 0.80, "avail": 0.10},
    OptimizeFor.availability: {"cost": 0.10, "perf": 0.20, "avail": 0.70},
}

# Max price reference for normalizing cost score (~$35/hr covers most instances)
_MAX_PRICE_HR = 35.0

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


def _cost_score(instance: MachineType) -> float:
    """Lower price → higher score. Normalized 0–1."""
    return max(0.0, 1.0 - (instance.price_hr / _MAX_PRICE_HR))


def _perf_score(instance: MachineType, profile: WorkloadProfile) -> float:
    """Higher vCPU + RAM density relative to request → higher score."""
    vcpu_ratio = min(instance.vcpu / max(profile.vcpu, 1), 2.0) / 2.0
    ram_ratio  = min(instance.ram_gb / max(profile.ram_gb, 1), 2.0) / 2.0
    return vcpu_ratio * 0.5 + ram_ratio * 0.5


def _avail_score(instance: MachineType) -> float:
    """Active > deprecated > tombstoned."""
    return {"active": 1.0, "deprecated": 0.4, "tombstoned": 0.0}.get(
        instance.status, 0.5
    )


def _hard_floor_check(instance: MachineType, profile: WorkloadProfile) -> str | None:
    """Return a disqualify reason string if this instance fails any hard floor."""
    if profile.region is not None and instance.region != profile.region:
        return f"Instance not available in region {profile.region!r} (this entry: {instance.region!r})"
    floor_ram = profile.ram_floor_gb if profile.ram_floor_gb is not None else profile.ram_gb
    if instance.ram_gb < floor_ram:
        return f"RAM {instance.ram_gb} GB < required {floor_ram} GB"
    if instance.vcpu < profile.vcpu:
        return f"vCPU {instance.vcpu} < required {profile.vcpu}"
    if profile.gpu.required:
        if instance.gpu_count == 0:
            return "GPU required but not available"
        if (
            profile.gpu.vram_gb is not None
            and instance.gpu_vram_gb is not None
            and instance.gpu_vram_gb < profile.gpu.vram_gb
        ):
            return (
                f"GPU VRAM {instance.gpu_vram_gb} GB "
                f"< required {profile.gpu.vram_gb} GB"
            )
    if instance.status == "tombstoned":
        return f"Instance {instance.id!r} is tombstoned, no longer available"
    return None


def score_instance(instance: MachineType, profile: WorkloadProfile) -> ScoredInstance:
    """Score a single instance against a workload profile.

    Returns a ScoredInstance with composite score and sub-scores.
    Disqualified instances have score=0.0 and disqualified=True.
    """
    reason = _hard_floor_check(instance, profile)
    if reason:
        return ScoredInstance(
            instance=instance, score=0.0,
            cost_score=0.0, perf_score=0.0, avail_score=0.0,
            disqualified=True, disqualify_reason=reason,
        )

    raw_weights = profile.weights or WEIGHT_MATRIX[profile.optimize_for]
    weights = _normalize_weights(raw_weights)

    c = _cost_score(instance)
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


# Alias — both names are public API
score = score_instance


def rank(
    profile: WorkloadProfile,
    candidates: list[MachineType],
) -> list[ScoredInstance]:
    """Score and rank all candidates.

    Qualified instances are returned first, sorted by score descending.
    Disqualified instances appear at the end (score=0.0).
    """
    scored = [score_instance(m, profile) for m in candidates]
    qualified    = sorted(
        [s for s in scored if not s.disqualified],
        key=lambda s: s.score,
        reverse=True,
    )
    disqualified = [s for s in scored if s.disqualified]
    return qualified + disqualified
