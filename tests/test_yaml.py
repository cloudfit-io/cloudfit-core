"""Tests for cloudfit.yaml_loader — YAML schema loading."""

import pytest
import tempfile
from pathlib import Path
from cloudfit import from_yaml, from_dict
from cloudfit.models import OptimizeFor, Archetype


MINIMAL_YAML = """
workload:
  resources:
    vcpu: 32
    ram_gb: 128
"""

FULL_YAML = """
workload:
  type: io-intensive
  archetype: io
  parallelism: lane

  resources:
    vcpu: 60
    ram_gb: 224
    ram_floor_gb: 200
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
  providers:
    - gcp
    - aws
"""

GPU_YAML = """
workload:
  type: gpu-inference
  archetype: gpu
  resources:
    vcpu: 8
    ram_gb: 64
    gpu:
      required: true
      vram_gb: 40
  optimize_for: performance
"""

FLAT_YAML = """
vcpu: 16
ram_gb: 64
"""


def _write_tmp(content: str) -> Path:
    f = tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False
    )
    f.write(content)
    f.close()
    return Path(f.name)


class TestFromYaml:
    def test_minimal_yaml_loads(self):
        p = _write_tmp(MINIMAL_YAML)
        profile = from_yaml(p)
        assert profile.vcpu == 32
        assert profile.ram_gb == 128.0

    def test_full_yaml_loads_all_fields(self):
        p = _write_tmp(FULL_YAML)
        profile = from_yaml(p)
        assert profile.vcpu == 60
        assert profile.ram_gb == 224.0
        assert profile.ram_floor_gb == 200.0
        assert profile.archetype == Archetype.io
        assert profile.optimize_for == OptimizeFor.balanced
        assert profile.disk.sizing == "dynamic"
        assert profile.disk.preferred == "local_ssd_first"
        assert profile.scheduling.spot is False
        assert "gcp" in profile.providers
        assert "aws" in profile.providers

    def test_gpu_yaml_loads(self):
        p = _write_tmp(GPU_YAML)
        profile = from_yaml(p)
        assert profile.gpu.required is True
        assert profile.gpu.vram_gb == 40
        assert profile.archetype == Archetype.gpu
        assert profile.optimize_for == OptimizeFor.performance

    def test_flat_yaml_loads(self):
        p = _write_tmp(FLAT_YAML)
        profile = from_yaml(p)
        assert profile.vcpu == 16
        assert profile.ram_gb == 64.0

    def test_missing_file_raises(self):
        with pytest.raises(FileNotFoundError):
            from_yaml("/nonexistent/path/workload.yaml")

    def test_default_optimize_for_is_balanced(self):
        p = _write_tmp(MINIMAL_YAML)
        profile = from_yaml(p)
        assert profile.optimize_for == OptimizeFor.balanced

    def test_default_providers(self):
        p = _write_tmp(MINIMAL_YAML)
        profile = from_yaml(p)
        assert "gcp" in profile.providers
        assert "aws" in profile.providers


class TestFromDict:
    def test_nested_dict_loads(self):
        data = {"workload": {"resources": {"vcpu": 8, "ram_gb": 32}}}
        profile = from_dict(data)
        assert profile.vcpu == 8
        assert profile.ram_gb == 32.0

    def test_flat_dict_loads(self):
        profile = from_dict({"vcpu": 4, "ram_gb": 16})
        assert profile.vcpu == 4

    def test_missing_vcpu_raises(self):
        with pytest.raises(ValueError, match="vcpu"):
            from_dict({"workload": {"resources": {"ram_gb": 64}}})

    def test_missing_ram_raises(self):
        with pytest.raises(ValueError, match="ram_gb"):
            from_dict({"workload": {"resources": {"vcpu": 4}}})

    def test_invalid_archetype_raises(self):
        with pytest.raises(Exception):
            from_dict({
                "workload": {
                    "archetype": "invalid_arch",
                    "resources": {"vcpu": 4, "ram_gb": 16}
                }
            })
