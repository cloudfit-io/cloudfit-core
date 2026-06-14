"""cloudfit-core: Cloud-agnostic machine type scoring engine."""

from .models import (
    WorkloadProfile,
    MachineType,
    ScoredInstance,
    OptimizeFor,
    Archetype,
    HeadroomMode,
    DiskSpec,
    GPUSpec,
    SchedulingSpec,
)
from .scorer import rank, score_instance, score
from .filter import hard_floor_check, apply_floors
from .disk import compute_disk_tb, compute_disk_breakdown, list_sequencers
from .yaml_loader import from_yaml, from_dict

__version__ = "0.6.1"
__author__ = "Chaitanya Krishna Kasaraneni"
__license__ = "Apache-2.0"

__all__ = [
    # models
    "WorkloadProfile", "MachineType", "ScoredInstance",
    "OptimizeFor", "Archetype", "HeadroomMode", "DiskSpec", "GPUSpec", "SchedulingSpec",
    # scorer — both names are public API
    "rank", "score_instance", "score",
    # filter
    "hard_floor_check", "apply_floors",
    # disk
    "compute_disk_tb", "compute_disk_breakdown", "list_sequencers",
    # yaml
    "from_yaml", "from_dict",
]
