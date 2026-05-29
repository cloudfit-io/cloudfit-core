"""Hard floor filters for cloudfit-core.

Hard floors run before scoring and eliminate instances that cannot
satisfy the workload requirements, regardless of their composite score.
"""

from __future__ import annotations
from .models import WorkloadProfile, MachineType


def hard_floor_check(instance: MachineType, profile: WorkloadProfile) -> str | None:
    """Return a disqualify reason string if the instance fails any hard floor.

    Returns None if the instance passes all floors and is eligible for scoring.

    Floors checked (in order):
        1. Region — if profile.region is set, instance.region must match
        2. RAM — instance.ram_gb >= profile.ram_floor_gb (or ram_gb if no floor set)
        3. vCPU — instance.vcpu >= profile.vcpu
        4. GPU presence — if profile.gpu.required, instance must have gpu_count > 0
        5. GPU VRAM — if profile.gpu.vram_gb set, instance.gpu_vram_gb must meet it
        6. Status — tombstoned instances are always disqualified
    """
    # 1. Region floor (only enforced when profile.region is explicitly set)
    if profile.region is not None and instance.region != profile.region:
        return f"Instance not available in region {profile.region!r} (this entry: {instance.region!r})"

    # 2. RAM floor
    floor_ram = profile.ram_floor_gb if profile.ram_floor_gb is not None else profile.ram_gb
    if instance.ram_gb < floor_ram:
        return (
            f"RAM {instance.ram_gb:.0f} GB < required {floor_ram:.0f} GB"
        )

    # 3. vCPU floor
    if instance.vcpu < profile.vcpu:
        return f"vCPU {instance.vcpu} < required {profile.vcpu}"

    # 4. GPU presence
    if profile.gpu.required and instance.gpu_count == 0:
        return "GPU required but instance has no GPU"

    # 5. GPU VRAM
    if (
        profile.gpu.required
        and profile.gpu.vram_gb is not None
        and instance.gpu_vram_gb is not None
        and instance.gpu_vram_gb < profile.gpu.vram_gb
    ):
        return (
            f"GPU VRAM {instance.gpu_vram_gb} GB < required {profile.gpu.vram_gb} GB"
        )

    # 6. Tombstoned instances are never recommended
    if instance.status == "tombstoned":
        return f"Instance {instance.id!r} is tombstoned, no longer available from {instance.provider}"

    return None


def apply_floors(
    instances: list[MachineType],
    profile: WorkloadProfile,
) -> tuple[list[MachineType], list[tuple[MachineType, str]]]:
    """Partition instances into (qualified, disqualified) lists.

    Returns:
        qualified:    instances that passed all hard floors
        disqualified: list of (instance, reason) tuples that failed
    """
    qualified: list[MachineType] = []
    disqualified: list[tuple[MachineType, str]] = []

    for inst in instances:
        reason = hard_floor_check(inst, profile)
        if reason is None:
            qualified.append(inst)
        else:
            disqualified.append((inst, reason))

    return qualified, disqualified
