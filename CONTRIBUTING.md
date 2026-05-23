# Contributing to cloudfit-core

Thank you for your interest in contributing. cloudfit-core is an early-stage open-source project and all contributions are welcome.

## Ways to contribute

- **Bug reports** — open an issue with a minimal reproducible example
- **Provider plugins** — build a `cloudfit-provider-*` package for a new cloud (Azure, Hetzner, Oracle Cloud, etc.)
- **Workload profiles** — add new bioinformatics tool profiles to the archetype registry
- **Benchmarks** — real-world performance data to improve `perf_score` accuracy
- **Documentation** — examples, tutorials, corrections

## Development setup

```bash
git clone https://github.com/cloudfit-io/cloudfit-core
cd cloudfit-core
pip install -e ".[dev]"
pytest
```

## Pull request guidelines

- Keep PRs focused — one feature or fix per PR
- Add tests for new scoring logic
- Run `pytest` and `ruff check .` before submitting
- Update `CITATION.cff` version if releasing

## Provider plugin interface

If you want to build a provider plugin, implement:

```python
from cloudfit.providers.base import Provider
from cloudfit.models import MachineType, Pricing

class MyCloudProvider(Provider):
    def fetch_instances(self, region: str) -> list[MachineType]: ...
    def get_pricing(self, instance_id: str, region: str) -> Pricing: ...
    def get_availability(self, instance_id: str, region: str) -> float: ...
```

Publish as `cloudfit-provider-<name>` on PyPI.

## Code of conduct

Be respectful. This project follows the [Contributor Covenant](https://www.contributor-covenant.org/).
