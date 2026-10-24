"""Golden ranking test: the top picks for fixed workloads over a frozen catalog.

Any change to scoring (weights, speed factors, cost or perf curves, floors) shows
up here as a diff in the expected file, so ranking changes are reviewed on
purpose rather than slipping through. After an intended change, regenerate:

    CLOUDFIT_UPDATE_GOLDEN=1 pytest tests/test_golden_ranking.py

and review the diff of tests/fixtures/golden_rankings.json with the change.
"""

import json
import os
from pathlib import Path

import pytest

from cloudfit import MachineType, WorkloadProfile, rank

FIXTURES = Path(__file__).parent / "fixtures"
CANDIDATES = [
    MachineType(**row) for row in json.loads((FIXTURES / "golden_candidates.json").read_text())
]
EXPECTED_FILE = FIXTURES / "golden_rankings.json"

PROFILES = {
    "cpu-balanced": dict(vcpu=16, ram_gb=64, archetype="cpu", optimize_for="balanced"),
    "mem-cost": dict(vcpu=8, ram_gb=64, archetype="mem", optimize_for="cost"),
    "io-performance-scratch": dict(vcpu=32, ram_gb=128, archetype="io",
                                   optimize_for="performance", disk={"scratch_tb": 1.0}),
    "gpu-a100-class": dict(vcpu=12, ram_gb=85, archetype="gpu", optimize_for="performance",
                           gpu={"required": True, "vram_gb": 40}),
    "burst-spot-default": dict(vcpu=4, ram_gb=16, archetype="burst", optimize_for="cost"),
    "x86-only-committed": dict(vcpu=32, ram_gb=128, archetype="cpu", optimize_for="cost",
                               cpu_arch="x86_64", pricing_mode="cud_1yr"),
}


def _top(profile_kwargs: dict, n: int = 5) -> list[list]:
    results = [r for r in rank(WorkloadProfile(**profile_kwargs), CANDIDATES) if not r.disqualified]
    return [[r.instance.id, r.instance.region, round(r.score, 4)] for r in results[:n]]


ACTUAL = {name: _top(kwargs) for name, kwargs in PROFILES.items()}

if os.environ.get("CLOUDFIT_UPDATE_GOLDEN"):
    EXPECTED_FILE.write_text(json.dumps(ACTUAL, indent=1) + "\n")


@pytest.mark.parametrize("name", list(PROFILES))
def test_top_picks_match_golden(name):
    expected = json.loads(EXPECTED_FILE.read_text())
    assert ACTUAL[name] == expected[name], (
        f"Ranking for {name!r} changed. If intended, regenerate with "
        "CLOUDFIT_UPDATE_GOLDEN=1 and review the diff."
    )
