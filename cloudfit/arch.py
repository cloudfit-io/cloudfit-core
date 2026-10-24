"""CPU architecture lookup for machine ids (x86_64 vs arm64).

Many tools ship for one architecture only (BCLConvert is x86_64-only, for
example), so an Arm instance can be the cheapest and fastest pick yet unable to
run the job. ``cpu_arch_for`` identifies the architecture from a machine id so
the ``cpu_arch`` hard floor works even when a provider or caller did not set
``MachineType.cpu_arch``.
"""

from __future__ import annotations

import re

from .models import CpuArch

# GCP families built on Arm CPUs (Ampere Altra, Google Axion, NVIDIA Grace).
_GCP_ARM_FAMILIES = frozenset({"t2a", "c4a", "n4a", "a4x"})

# AWS instance families: class letters, generation digits, attribute letters.
# Graviton families carry a "g" among the attribute letters after the
# generation (m6g, c7gn, x2gd, im4gn, is4gen, g5g, t4g); the class letter "g"
# before the digits (g4dn, g6e) is the GPU class, not Graviton.
_AWS_FAMILY = re.compile(r"^([a-z]+)(\d+)([a-z]*)$")
_AWS_GRAVITON1 = "a1"


def cpu_arch_for(machine_id: str) -> CpuArch | None:
    """Return the CPU architecture implied by a GCP or AWS machine id.

    AWS instance types contain a ``.`` (``"m8g.xlarge"``); everything else with
    a ``-`` is read as a GCP machine type (``"t2a-standard-4"``). Returns None
    for an id that matches neither naming scheme, so an unrecognized custom id
    is never guessed.
    """
    machine_id = machine_id.lower()
    if "." in machine_id:
        if machine_id.startswith("mac"):
            # mac1 is Intel; mac2 and the M-series Macs are Apple silicon.
            return CpuArch.x86_64 if machine_id.startswith("mac1.") else CpuArch.arm64
        family = machine_id.split(".")[0].split("-")[0]
        if family == _AWS_GRAVITON1:
            return CpuArch.arm64
        match = _AWS_FAMILY.match(family)
        if match is None:
            return None
        return CpuArch.arm64 if "g" in match.group(3) else CpuArch.x86_64
    if "-" in machine_id:
        family = machine_id.split("-")[0]
        return CpuArch.arm64 if family in _GCP_ARM_FAMILIES else CpuArch.x86_64
    return None
