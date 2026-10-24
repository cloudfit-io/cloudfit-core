# Contributing to cloudfit-core

Thank you for your interest in contributing. cloudfit-core is an early-stage open-source project and all contributions are welcome.

## Ways to contribute

- **Bug reports**: open an issue with a minimal reproducible example
- **Provider plugins**: build a `cloudfit-provider-*` package for a new cloud (Azure, Hetzner, Oracle Cloud, etc.)
- **Workload profiles**: add new bioinformatics tool profiles to the archetype registry
- **Benchmarks**: real-world performance data to improve `perf_score` accuracy
- **Documentation**: examples, tutorials, corrections

## Development setup

```bash
git clone https://github.com/cloudfit-io/cloudfit-core
cd cloudfit-core
pip install -e ".[dev]"
pytest
```

## Pull request guidelines

- Keep PRs focused: one feature or fix per PR
- Add tests for new scoring logic
- Run `pytest`, `ruff check cloudfit/`, and `mypy --strict cloudfit/` before submitting
- Add a line under `## [Unreleased]` in `CHANGELOG.md`; `make minor` / `make patch` roll it into the release and bump `pyproject.toml`, `__init__.py`, and `CITATION.cff`

## Provider plugin interface

If you want to build a provider plugin, implement:

```python
from cloudfit.providers.base import Provider
from cloudfit.models import MachineType

class MyCloudProvider(Provider):
    def fetch_instances(self, region: str) -> list[MachineType]: ...
    def get_pricing(self, instance_id: str, region: str) -> float: ...
```

Set each `MachineType.status` to `active`, `deprecated`, or `tombstoned`; the
scoring engine reads availability from that field. Also populate `perf_factor`
(via `cloudfit.perf_factor_for`, or add the family to `PERF_FACTORS` for GCP or
`AWS_PERF_FACTORS` for AWS; a new cloud gets its own table), `cpu_arch` (via
`cloudfit.cpu_arch_for`, or extend it for a new cloud's naming scheme), `gpu_type`
for GPU instances, and `spot_price_hr` / `cud_1yr_price_hr` where the cloud
publishes them. Publish as `cloudfit-provider-<name>` on PyPI.

## Code of conduct

Be respectful. This project follows the [Contributor Covenant](https://www.contributor-covenant.org/).
