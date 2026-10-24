"""cloudfit-core: Cloud-agnostic machine type scoring engine."""

from .models import (
    WorkloadProfile,
    MachineType,
    ScoredInstance,
    OptimizeFor,
    Archetype,
    HeadroomMode,
    PricingMode,
    CpuArch,
    DiskSpec,
    GPUSpec,
    SchedulingSpec,
)
from .scorer import (
    rank,
    score_instance,
    score,
    perf_factor_for,
    PERF_FACTORS,
    AWS_PERF_FACTORS,
    gpu_class_for,
    GPU_CLASS,
)
from .filter import hard_floor_check, apply_floors
from .arch import cpu_arch_for
from .disk import compute_disk_tb, compute_disk_breakdown, list_sequencers
from .yaml_loader import from_yaml, from_dict

__version__ = "0.11.2"
__author__ = "Chaitanya Krishna Kasaraneni"
__license__ = "Apache-2.0"

__all__ = [
    # models
    "WorkloadProfile", "MachineType", "ScoredInstance",
    "OptimizeFor", "Archetype", "HeadroomMode", "PricingMode", "CpuArch",
    "DiskSpec", "GPUSpec", "SchedulingSpec",
    # scorer — both names are public API
    "rank", "score_instance", "score",
    "perf_factor_for", "PERF_FACTORS", "AWS_PERF_FACTORS", "gpu_class_for", "GPU_CLASS",
    # filter
    "hard_floor_check", "apply_floors", "cpu_arch_for",
    # disk
    "compute_disk_tb", "compute_disk_breakdown", "list_sequencers",
    # yaml
    "from_yaml", "from_dict",
]
