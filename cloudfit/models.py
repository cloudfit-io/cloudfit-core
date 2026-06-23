"""Core data models for cloudfit-core."""

from __future__ import annotations
from enum import Enum
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Weight key aliases and the canonical key set used by the scorer.
_WEIGHT_ALIASES: dict[str, str] = {"performance": "perf", "availability": "avail"}
_WEIGHT_KEYS: tuple[str, ...] = ("cost", "perf", "avail")


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


class PricingMode(str, Enum):
    on_demand = "on_demand"   # standard on-demand price (default for stateful work)
    spot = "spot"             # preemptible/spot price (default for the burst archetype)
    cud_1yr = "cud_1yr"       # 1-year committed-use discount price


class GPUSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    required: bool = False
    vram_gb: Optional[int] = None


class DiskSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sizing: str = "static"
    scratch_tb: Optional[float] = None
    preferred: str = "network_ssd"
    safety_margin: float = 0.20


class SchedulingSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spot: bool = False
    restart_tolerant: bool = False


class WorkloadProfile(BaseModel):
    """Describes the resource requirements of a computational workload."""
    model_config = ConfigDict(extra="forbid")

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
    archetype: Archetype | str = Archetype.cpu
    tool: Optional[str] = None
    parallelism: str = "sample"
    disk: DiskSpec = Field(default_factory=DiskSpec)
    gpu: GPUSpec = Field(default_factory=GPUSpec)
    scheduling: SchedulingSpec = Field(default_factory=SchedulingSpec)
    optimize_for: OptimizeFor | str = OptimizeFor.balanced
    pricing_mode: Optional[PricingMode] = Field(
        default=None,
        description=(
            "Which price to score cost on. Unset resolves to spot for the burst "
            "archetype (restart-tolerant) and on_demand otherwise; see "
            "effective_pricing_mode."
        ),
    )
    providers: list[str] = Field(default_factory=lambda: ["gcp", "aws"])
    region: Optional[str] = Field(
        default=None,
        description="If set, only instances available in this region pass the hard floor.",
    )
    weights: Optional[dict[str, float]] = None

    @field_validator("archetype", mode="before")
    @classmethod
    def _coerce_archetype(cls, v: object) -> Archetype:
        """Coerce a string to Archetype, raising on an unknown value."""
        return v if isinstance(v, Archetype) else Archetype(v)

    @field_validator("optimize_for", mode="before")
    @classmethod
    def _coerce_optimize_for(cls, v: object) -> OptimizeFor:
        """Coerce a string to OptimizeFor, raising on an unknown value."""
        return v if isinstance(v, OptimizeFor) else OptimizeFor(v)

    @property
    def effective_pricing_mode(self) -> PricingMode:
        """Resolved pricing mode: explicit override, else archetype-aware default.

        The burst archetype is scatter-gather and restart-tolerant, so it defaults
        to spot; every other archetype defaults to on-demand. An explicit
        pricing_mode always wins.
        """
        if self.pricing_mode is not None:
            return self.pricing_mode
        if Archetype(self.archetype) == Archetype.burst:
            return PricingMode.spot
        return PricingMode.on_demand

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

    @model_validator(mode="after")
    def _validate_weights(self) -> "WorkloadProfile":
        """Normalize and validate custom weights.

        Accepts long-form keys (performance/availability), requires all three
        keys (cost, perf, avail) or none, rejects negative values, and requires
        the weights to sum to 1.0 within a small tolerance. Stores the result
        back with the canonical short keys so the scorer can consume it directly.
        """
        if self.weights is None:
            return self

        normalized = {_WEIGHT_ALIASES.get(k, k): v for k, v in self.weights.items()}

        unknown = set(normalized) - set(_WEIGHT_KEYS)
        if unknown:
            raise ValueError(
                f"Unknown weight keys: {sorted(unknown)}. "
                f"Allowed: {list(_WEIGHT_KEYS)} (or performance/availability)."
            )
        if set(normalized) != set(_WEIGHT_KEYS):
            raise ValueError(
                f"weights must include all of {list(_WEIGHT_KEYS)} or none; "
                f"got {sorted(normalized)}."
            )
        for key, value in normalized.items():
            if value < 0:
                raise ValueError(f"weight {key!r} must be non-negative, got {value}.")

        total = sum(normalized.values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"weights must sum to 1.0 (within 1e-6), got {total}.")

        object.__setattr__(self, "weights", normalized)
        return self


class MachineType(BaseModel):
    """A cloud provider machine type with specs and pricing."""
    model_config = ConfigDict(extra="forbid")

    id: str
    provider: str
    vcpu: int
    ram_gb: float
    price_hr: float
    spot_price_hr: Optional[float] = Field(
        default=None,
        gt=0.0,
        description="Preemptible/spot price per hour. Falls back to price_hr when unset.",
    )
    cud_1yr_price_hr: Optional[float] = Field(
        default=None,
        gt=0.0,
        description="1-year committed-use price per hour. Falls back to price_hr when unset.",
    )
    local_ssd_tb: float = 0.0
    gpu_count: int = 0
    gpu_vram_gb: Optional[int] = None
    gpu_type: Optional[str] = Field(
        default=None,
        description=(
            "Accelerator model (e.g. 'nvidia-tesla-a100'). When set, the gpu archetype "
            "scores effective GPU throughput via GPU_CLASS so an A100 outscores a T4 at "
            "equal VRAM. Unset preserves VRAM-fit scoring."
        ),
    )
    region: str = "us-central1"
    status: str = "active"
    generation: Optional[str] = None
    perf_factor: Optional[float] = Field(
        default=None,
        gt=0.0,
        description=(
            "Relative per-vCPU performance vs the baseline family (1.0 = baseline). "
            "When set, perf scoring uses effective capacity (vcpu * perf_factor) so a "
            "faster family is not equated with a slower one at the same core count. "
            "Unset (None) preserves count-based scoring. Providers populate this from "
            "PERF_FACTORS; see cloudfit.scorer.perf_factor_for."
        ),
    )


class ScoredInstance(BaseModel):
    """A MachineType with its composite score and sub-scores."""
    instance: MachineType
    score: float
    cost_score: float
    perf_score: float
    avail_score: float
    disqualified: bool = False
    disqualify_reason: Optional[str] = None
    contributions: dict[str, float] = Field(default_factory=dict)
    reason: str = ""

    def __lt__(self, other: "ScoredInstance") -> bool:
        return self.score < other.score
