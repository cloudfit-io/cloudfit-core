"""Dynamic disk sizing for sequencing workloads.

Computes total scratch disk required for a sequencing demultiplexing run
from experiment parameters rather than using a fixed estimate.

Disk components
---------------
    input_gb        Raw sequencer output (compressed or uncompressed format)
    output_gb       Demultiplexed output files (FASTQ)
    tmp_gb          Working directory during processing (~35% of input)
    retained_input_gb  Raw input kept post-run (if retain_input=True)

Compressed vs uncompressed input
---------------------------------
    Some sequencers write a compressed input format that is ~4x smaller
    than the uncompressed equivalent before demultiplexing. Output file
    size is the same regardless of input format.

Safety margin
-------------
    Applied to the total before returning. Default 20%.
"""

from __future__ import annotations
from dataclasses import dataclass


@dataclass
class _FlowcellProfile:
    label: str
    gb_per_lane: float
    max_lanes: int
    compressed: bool    # True if sequencer writes compressed input format


_SEQUENCER_PROFILES: dict[str, list[_FlowcellProfile]] = {
    "miseq": [
        _FlowcellProfile("nano",     gb_per_lane=0.8,    max_lanes=1, compressed=False),
        _FlowcellProfile("micro",    gb_per_lane=1.5,    max_lanes=1, compressed=False),
        _FlowcellProfile("standard", gb_per_lane=15.0,   max_lanes=1, compressed=False),
    ],
    "nextseq": [
        _FlowcellProfile("mid",      gb_per_lane=40.0,   max_lanes=4, compressed=False),
        _FlowcellProfile("high",     gb_per_lane=120.0,  max_lanes=4, compressed=False),
    ],
    "novaseq_6000": [
        _FlowcellProfile("sp",       gb_per_lane=200.0,  max_lanes=2, compressed=False),
        _FlowcellProfile("s1",       gb_per_lane=400.0,  max_lanes=2, compressed=False),
        _FlowcellProfile("s2",       gb_per_lane=500.0,  max_lanes=4, compressed=False),
        _FlowcellProfile("s4",       gb_per_lane=1200.0, max_lanes=4, compressed=False),
    ],
    "novaseq_x": [
        _FlowcellProfile("1.5b",     gb_per_lane=380.0,  max_lanes=2, compressed=True),
        _FlowcellProfile("10b",      gb_per_lane=600.0,  max_lanes=8, compressed=True),
        _FlowcellProfile("25b",      gb_per_lane=1500.0, max_lanes=8, compressed=True),
    ],
    "hiseq": [
        _FlowcellProfile("rapid",    gb_per_lane=150.0,  max_lanes=2, compressed=False),
        _FlowcellProfile("high",     gb_per_lane=400.0,  max_lanes=8, compressed=False),
    ],
}

# Compressed input format is ~4x smaller than uncompressed equivalent
_COMPRESSION_FACTOR = 0.25

# Output files are ~1.4x the (uncompressed-equivalent) input size
_OUTPUT_MULTIPLIER = 1.40

# Reads that don't match any sample add ~8% to output
_UNDETERMINED_FACTOR = 1.08

# Temp/working directory is ~35% of input during processing
_TMP_FRACTION = 0.35


@dataclass
class DiskBreakdown:
    """Detailed breakdown of disk components in GB."""
    input_gb: float
    output_gb: float
    tmp_gb: float
    retained_input_gb: float
    subtotal_gb: float
    safety_margin: float
    total_gb: float

    @property
    def total_tb(self) -> float:
        return self.total_gb / 1000


def compute_disk_tb(
    sequencer: str,
    flowcell: str,
    lanes: int,
    *,
    retain_input: bool = False,
    keep_undetermined: bool = False,
    safety_margin: float = 0.20,
) -> float:
    """Compute total scratch disk required in TB.

    Args:
        sequencer:          Sequencer model key (e.g. "novaseq_6000", "miseq")
        flowcell:           Flowcell type key (e.g. "s4", "nano", "high")
        lanes:              Number of lanes used in this run
        retain_input:       If True, raw input files are kept post-run
        keep_undetermined:  If True, unmatched reads are written to disk (+8%)
        safety_margin:      Fractional headroom added to total (default 0.20)

    Returns:
        Total disk required in TB (with safety margin applied).

    Raises:
        ValueError: If sequencer or flowcell key is not recognised.
    """
    breakdown = compute_disk_breakdown(
        sequencer=sequencer,
        flowcell=flowcell,
        lanes=lanes,
        retain_input=retain_input,
        keep_undetermined=keep_undetermined,
        safety_margin=safety_margin,
    )
    return round(breakdown.total_tb, 4)


def compute_disk_breakdown(
    sequencer: str,
    flowcell: str,
    lanes: int,
    *,
    retain_input: bool = False,
    keep_undetermined: bool = False,
    safety_margin: float = 0.20,
) -> DiskBreakdown:
    """Compute a full disk breakdown with all components in GB."""
    if lanes < 1:
        raise ValueError(f"lanes must be >= 1, got {lanes}")

    seq_key = sequencer.lower().replace("-", "_").replace(" ", "_")
    fc_key  = flowcell.lower().replace("-", "_").replace(" ", "_")

    if seq_key not in _SEQUENCER_PROFILES:
        raise ValueError(
            f"Unknown sequencer {sequencer!r}. "
            f"Known: {list(_SEQUENCER_PROFILES)}"
        )

    profiles = _SEQUENCER_PROFILES[seq_key]
    fc_profile = next((p for p in profiles if p.label == fc_key), None)
    if fc_profile is None:
        known = [p.label for p in profiles]
        raise ValueError(
            f"Unknown flowcell {flowcell!r} for {sequencer!r}. "
            f"Known flowcells: {known}"
        )

    lanes = min(lanes, fc_profile.max_lanes)

    # Raw uncompressed sequencer output. FASTQ output size depends on this,
    # not on the on-disk input format: compression shrinks the stored input
    # only, while demultiplexed output is always full size.
    raw_input_gb = fc_profile.gb_per_lane * lanes

    # Apply compression factor to the stored input only.
    compression = _COMPRESSION_FACTOR if fc_profile.compressed else 1.0
    input_gb = raw_input_gb * compression

    undetermined_factor = _UNDETERMINED_FACTOR if keep_undetermined else 1.0
    output_gb = raw_input_gb * _OUTPUT_MULTIPLIER * undetermined_factor

    tmp_gb            = input_gb * _TMP_FRACTION
    retained_input_gb = input_gb if retain_input else 0.0

    subtotal_gb = input_gb + output_gb + tmp_gb + retained_input_gb
    total_gb    = subtotal_gb * (1 + safety_margin)

    return DiskBreakdown(
        input_gb=round(input_gb, 1),
        output_gb=round(output_gb, 1),
        tmp_gb=round(tmp_gb, 1),
        retained_input_gb=round(retained_input_gb, 1),
        subtotal_gb=round(subtotal_gb, 1),
        safety_margin=safety_margin,
        total_gb=round(total_gb, 1),
    )


def list_sequencers() -> dict[str, list[str]]:
    """Return all known sequencer/flowcell combinations."""
    return {
        seq: [fc.label for fc in profiles]
        for seq, profiles in _SEQUENCER_PROFILES.items()
    }
