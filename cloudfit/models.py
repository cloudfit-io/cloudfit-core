"""Core data models for cloudfit-core."""

from __future__ import annotations
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class OptimizeFor(str, Enum):
    cost = "cost"
    balanced = "balanced"
    performance = "performance"
    availability = "availability"


class Archetype(str, Enum):
    io = "io"           # disk-saturating (BCLConvert, BWA)
    cpu = "cpu"         # thread-parallel (GATK, Trinity)
    mem = "mem"         # large index / reference (Kraken2, Cell Ranger)
    gpu = "gpu"         # GPU inference (AlphaFold, Parabricks)
    burst = "burst"     # scatter-gather fleet (Nextflow, Snakemake)


class HeadroomMode(str, Enum):
    soft = "soft"   # preference: bias perf scoring toward buffered instances only
    hard = "hard"   # guarantee: also raise the hard floor so sub-buffer instances are disqualified


class GPUSpec(BaseModel):
    required: bool = False
    vram_gb: Optional[int] = None


class DiskSpec(BaseModel):
    sizing: str = "static"
    scratch_tb: Optional[float] = None
    preferred: str = "network_ssd"
    safety_margin: float = 0.20


class SchedulingSpec(BaseModel):
    spot: bool = False
    restart_tolerant: bool = False


class WorkloadProfile(BaseModel):
    """Describes the resource requirements of a computational workload."""
    vcpu: int = Field(gt=0)
    ram_gb: float = Field(gt=0)
    ram_floor_gb: Optional[float] = None
    headroom: float = Field(
        default=0.0,
        ge=0.0,
        description="Fractional spare capacity above declared vcpu/ram_gb (e.g. 0.15 = 15%).",
    )
    headroom_mode: HeadroomMode = Field(
        default=HeadroomMode.hard,
        description="hard: raise the floor and recenter perf; soft: recenter perf only.",
    )
    workload: str = "generic"
    archetype: Archetype = Archetype.cpu
    tool: Optional[str] = None
    parallelism: str = "sample"
    disk: DiskSpec = Field(default_factory=DiskSpec)
    gpu: GPUSpec = Field(default_factory=GPUSpec)
    scheduling: SchedulingSpec = Field(default_factory=SchedulingSpec)
    optimize_for: OptimizeFor = OptimizeFor.balanced
    providers: list[str] = Field(default_factory=lambda: ["gcp", "aws"])
    region: Optional[str] = Field(
        default=None,
        description="If set, only instances available in this region pass the hard floor.",
    )
    weights: Optional[dict[str, float]] = None

    @property
    def perf_target_vcpu(self) -> float:
        """vCPU the workload aims for, including headroom. Drives perf scoring."""
        return self.vcpu * (1.0 + self.headroom)

    @property
    def perf_target_ram_gb(self) -> float:
        """RAM the workload aims for, including headroom. Drives perf scoring."""
        return self.ram_gb * (1.0 + self.headroom)

    @property
    def effective_vcpu_floor(self) -> float:
        """Minimum vCPU to pass the hard floor. Raised by headroom only in hard mode."""
        if self.headroom_mode == HeadroomMode.hard:
            return self.perf_target_vcpu
        return float(self.vcpu)

    @property
    def effective_ram_floor_gb(self) -> float:
        """Minimum RAM to pass the hard floor.

        Base floor is ram_floor_gb when set, else ram_gb. In hard mode with
        headroom > 0, the floor is raised to max(base, ram_gb * (1 + headroom)),
        so an explicit sub-nominal ram_floor_gb is preserved when no headroom is asked.
        """
        base = self.ram_floor_gb if self.ram_floor_gb is not None else self.ram_gb
        if self.headroom_mode == HeadroomMode.hard and self.headroom > 0:
            return max(base, self.perf_target_ram_gb)
        return base


class MachineType(BaseModel):
    """A cloud provider machine type with specs and pricing."""
    id: str
    provider: str
    vcpu: int
    ram_gb: float
    price_hr: float
    local_ssd_tb: float = 0.0
    gpu_count: int = 0
    gpu_vram_gb: Optional[int] = None
    region: str = "us-central1"
    status: str = "active"
    generation: Optional[str] = None


class ScoredInstance(BaseModel):
    """A MachineType with its composite score and sub-scores."""
    instance: MachineType
    score: float
    cost_score: float
    perf_score: float
    avail_score: float
    disqualified: bool = False
    disqualify_reason: Optional[str] = None

    def __lt__(self, other: "ScoredInstance") -> bool:
        return self.score < other.score
