"""Tests for the cpu_arch hard floor and cpu_arch_for lookups."""

import pytest
from pydantic import ValidationError

from cloudfit import CpuArch, MachineType, WorkloadProfile, cpu_arch_for, from_dict, rank


@pytest.mark.parametrize("machine_id, arch", [
    ("m8g.xlarge", CpuArch.arm64),       # Graviton4
    ("c7gn.large", CpuArch.arm64),       # Graviton3, network-optimized
    ("x2gd.metal", CpuArch.arm64),
    ("im4gn.large", CpuArch.arm64),
    ("is4gen.medium", CpuArch.arm64),
    ("t4g.micro", CpuArch.arm64),
    ("g5g.xlarge", CpuArch.arm64),       # Graviton2 host with a T4G GPU
    ("a1.large", CpuArch.arm64),         # Graviton1
    ("mac2-m2pro.metal", CpuArch.arm64),
    ("m7i.2xlarge", CpuArch.x86_64),
    ("g4dn.xlarge", CpuArch.x86_64),     # "g" is the GPU class letter, not Graviton
    ("g6e.12xlarge", CpuArch.x86_64),
    ("m7i-flex.large", CpuArch.x86_64),
    ("u7in-16tb.224xlarge", CpuArch.x86_64),
    ("u-6tb1.112xlarge", CpuArch.x86_64),  # high memory, no generation digit
    ("mac1.metal", CpuArch.x86_64),
    ("t2a-standard-4", CpuArch.arm64),   # GCP Ampere Altra
    ("c4a-standard-8", CpuArch.arm64),   # GCP Axion
    ("c3d-standard-16", CpuArch.x86_64),
    ("n2-standard-32", CpuArch.x86_64),
])
def test_cpu_arch_for(machine_id, arch):
    assert cpu_arch_for(machine_id) == arch


def test_cpu_arch_for_unrecognized_id_is_none():
    assert cpu_arch_for("small") is None


def _mt(id, **over):
    return MachineType(id=id, provider="aws", vcpu=16, ram_gb=64, price_hr=1.0, **over)


def test_x86_floor_disqualifies_graviton_with_a_reason():
    profile = WorkloadProfile(vcpu=16, ram_gb=64, cpu_arch="x86_64")
    by_id = {r.instance.id: r for r in rank(profile, [_mt("m8g.4xlarge"), _mt("m7i.4xlarge")])}
    assert by_id["m8g.4xlarge"].disqualified
    assert by_id["m8g.4xlarge"].disqualify_reason == "CPU architecture arm64 != required x86_64"
    assert not by_id["m7i.4xlarge"].disqualified


def test_arm_floor_disqualifies_x86():
    profile = WorkloadProfile(vcpu=16, ram_gb=64, cpu_arch="arm64")
    results = rank(profile, [_mt("m8g.4xlarge"), _mt("m7i.4xlarge")])
    assert [r.instance.id for r in results if not r.disqualified] == ["m8g.4xlarge"]


def test_no_floor_allows_both():
    profile = WorkloadProfile(vcpu=16, ram_gb=64)
    assert not any(r.disqualified for r in rank(profile, [_mt("m8g.4xlarge"), _mt("m7i.4xlarge")]))


def test_explicit_instance_cpu_arch_overrides_the_id():
    profile = WorkloadProfile(vcpu=16, ram_gb=64, cpu_arch="x86_64")
    relabeled = _mt("m8g.4xlarge", cpu_arch="x86_64")
    assert not rank(profile, [relabeled])[0].disqualified


def test_unknown_architecture_passes_the_floor():
    profile = WorkloadProfile(vcpu=16, ram_gb=64, cpu_arch="x86_64")
    assert not rank(profile, [_mt("small")])[0].disqualified


@pytest.mark.parametrize("alias, arch", [
    ("amd64", CpuArch.x86_64), ("X86_64", CpuArch.x86_64), ("aarch64", CpuArch.arm64), ("arm64", CpuArch.arm64),
])
def test_aliases_are_accepted(alias, arch):
    assert WorkloadProfile(vcpu=4, ram_gb=16, cpu_arch=alias).cpu_arch == arch
    assert _mt("x", cpu_arch=alias).cpu_arch == arch


def test_unknown_architecture_value_is_rejected():
    with pytest.raises(ValidationError):
        WorkloadProfile(vcpu=4, ram_gb=16, cpu_arch="riscv")


def test_yaml_profile_reads_cpu_arch():
    profile = from_dict({"workload": {"resources": {"vcpu": 32, "ram_gb": 64}, "cpu_arch": "amd64"}})
    assert profile.cpu_arch == CpuArch.x86_64
