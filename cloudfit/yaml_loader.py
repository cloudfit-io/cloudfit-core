"""YAML workload schema loader for cloudfit-core.

Loads a workload profile from a YAML file or dict and returns
a validated WorkloadProfile instance.

Schema example
--------------
    workload:
      type: io-intensive
      archetype: io
      parallelism: lane

      resources:
        vcpu: 60
        ram_gb: 224
        headroom: 0.15          # optional spare capacity above vcpu/ram_gb
        disk:
          sizing: dynamic
          scratch_tb: 18
          preferred: local_ssd_first
          safety_margin: 0.20
        gpu:
          required: false

      scheduling:
        spot: false
        restart_tolerant: false

      optimize_for: balanced
      headroom_mode: hard      # hard (default) or soft
      providers:
        - gcp
        - aws
"""

from __future__ import annotations
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as exc:
    raise ImportError(
        "PyYAML is required for from_yaml(). "
        "Install it with: pip install pyyaml"
    ) from exc

from .models import (
    WorkloadProfile,
    DiskSpec,
    GPUSpec,
    SchedulingSpec,
    OptimizeFor,
    Archetype,
    HeadroomMode,
)


def from_yaml(path: str | Path) -> WorkloadProfile:
    """Load a WorkloadProfile from a YAML file.

    Args:
        path: Path to the YAML file.

    Returns:
        A validated WorkloadProfile instance.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If required fields are missing or invalid.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Workload file not found: {path}")

    with path.open() as f:
        raw = yaml.safe_load(f)

    return from_dict(raw)


def from_dict(data: dict[str, Any]) -> WorkloadProfile:
    """Parse a WorkloadProfile from a plain dict (e.g. parsed JSON or YAML).

    Accepts both flat and nested schemas:

    Nested (preferred):
        workload:
          resources:
            vcpu: 60
            ram_gb: 224

    Flat (also accepted):
        vcpu: 60
        ram_gb: 224

    Args:
        data: Raw dict from YAML/JSON.

    Returns:
        A validated WorkloadProfile.
    """
    if not isinstance(data, dict):
        raise ValueError(
            f"Expected a YAML mapping (dict), got {type(data).__name__}. "
            "Check that the file is not empty and is valid YAML."
        )

    # Support both top-level "workload:" key and flat dict
    w = data.get("workload", data)
    resources = w.get("resources", w)

    # Disk spec
    disk_raw = resources.get("disk", {})
    disk = DiskSpec(
        sizing=disk_raw.get("sizing", "static"),
        scratch_tb=disk_raw.get("scratch_tb"),
        preferred=disk_raw.get("preferred", "network_ssd"),
        safety_margin=disk_raw.get("safety_margin", 0.20),
    )

    # GPU spec
    gpu_raw = resources.get("gpu", {})
    gpu = GPUSpec(
        required=gpu_raw.get("required", False),
        vram_gb=gpu_raw.get("vram_gb"),
    )

    # Scheduling spec
    sched_raw = w.get("scheduling", {})
    scheduling = SchedulingSpec(
        spot=sched_raw.get("spot", False),
        restart_tolerant=sched_raw.get("restart_tolerant", False),
    )

    vcpu = resources.get("vcpu")
    ram_gb = resources.get("ram_gb")

    if vcpu is None:
        raise ValueError("workload.resources.vcpu is required")
    if ram_gb is None:
        raise ValueError("workload.resources.ram_gb is required")

    return WorkloadProfile(
        vcpu=int(vcpu),
        ram_gb=float(ram_gb),
        ram_floor_gb=resources.get("ram_floor_gb"),
        headroom=float(resources.get("headroom", 0.0)),
        headroom_mode=HeadroomMode(w.get("headroom_mode", "hard")),
        workload=w.get("type", "generic"),
        archetype=Archetype(w.get("archetype", "cpu")),
        tool=w.get("tool"),
        parallelism=w.get("parallelism", "sample"),
        disk=disk,
        gpu=gpu,
        scheduling=scheduling,
        optimize_for=OptimizeFor(w.get("optimize_for", "balanced")),
        providers=w.get("providers", ["gcp", "aws"]),
        weights=w.get("weights"),
    )
