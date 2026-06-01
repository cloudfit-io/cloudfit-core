# cloudfit-core

[![PyPI version](https://img.shields.io/pypi/v/cloudfit-core)](https://pypi.org/project/cloudfit-core/)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue)](https://pypi.org/project/cloudfit-core/)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-green)](LICENSE)
[![Tests](https://github.com/cloudfit-io/cloudfit-core/actions/workflows/ci.yml/badge.svg)](https://github.com/cloudfit-io/cloudfit-core/actions)

**Cloud-agnostic machine type scoring engine for computational workloads.**

`cloudfit-core` is the foundation of the [cloudfit](https://github.com/cloudfit-io) ecosystem: a pure Python library that, given a workload profile, scores and ranks available cloud instances across providers. No cloud credentials required. No API calls. Just a workload spec in, ranked recommendations out.

> **Try it without installing anything.** [One-click UI](https://chaitanyakasaraneni-cloudfit-ui.hf.space) for the form-driven try · [Swagger docs](https://chaitanyakasaraneni-cloudfit-api.hf.space/docs) for the API · [Landing page](https://cloudfit-io.github.io) for the visual tour.

---

## The problem

Teams hardcode instance types (`c2-standard-60`, `c7i.16xlarge`) in infrastructure-as-code. When providers deprecate them or release better generations, nothing updates: costs drift and performance degrades silently. There is no open-source tool that takes a workload description and returns the best available instance across AWS, GCP, and Azure with explainable scoring.

cloudfit-core is that scoring engine.

---

## Installation

```bash
pip install cloudfit-core
```

Requires Python 3.9+.

---

## Quick start

```python
from cloudfit import WorkloadProfile, MachineType, rank

# Define your workload
profile = WorkloadProfile(
    vcpu=60,
    ram_gb=224,
    workload="io-intensive",
    archetype="io",            # io | cpu | mem | gpu | burst
    optimize_for="balanced",   # cost | performance | availability | balanced
)

# Provide candidate instances (from a cloudfit-provider-* package or your own list)
candidates = [
    MachineType(id="c2-standard-60",       provider="gcp", vcpu=60, ram_gb=240, price_hr=3.13),
    MachineType(id="c3d-standard-60-lssd", provider="gcp", vcpu=60, ram_gb=240, price_hr=3.39),
    MachineType(id="t2d-standard-60",      provider="gcp", vcpu=60, ram_gb=240, price_hr=2.31),
    MachineType(id="c7i.24xlarge",         provider="aws", vcpu=96, ram_gb=192, price_hr=4.28),
]

# Score and rank
results = rank(profile, candidates)
for r in results:
    print(f"{r.instance.id:30s}  score={r.score:.2f}  ${r.instance.price_hr:.2f}/hr")
```

Output:
```
t2d-standard-60                 score=0.81  $2.31/hr
c2-standard-60                  score=0.81  $3.13/hr
c3d-standard-60-lssd            score=0.80  $3.39/hr
c7i.24xlarge                    score=0.00  $4.28/hr
```

`c7i.24xlarge` scores `0.00` and ranks last because its 192 GB RAM is below the
requested 224 GB: it's eliminated by the hard floor filter, not just ranked low
(see [How scoring works](#how-scoring-works)).

### Region-aware filtering (new in 0.2)

Set `region` on the workload profile to restrict candidates to a specific region. The hard floor disqualifies anything not available there, before scoring runs.

```python
profile = WorkloadProfile(
    vcpu=16,
    ram_gb=64,
    region="asia-southeast1",   # only instances tagged with this region pass the floor
    optimize_for="cost",
)
```

This pairs naturally with multi-region provider snapshots: `cloudfit-provider-gcp.fetch_instances_all_regions(...)` emits one `MachineType` entry per region the family is available in, and the region hard floor selects the right subset at scoring time. Useful when a pipeline runs in a specific region and you only want candidates that can actually launch there.

---

## How scoring works

Every recommendation runs through the same weighted scoring function:

```
score = w_cost × cost_score + w_perf × perf_score + w_avail × avail_score
```

The `optimize_for` mode sets the weights:

| Mode | w_cost | w_perf | w_avail | Best for |
|---|---|---|---|---|
| `cost` | 0.70 | 0.20 | 0.10 | Batch jobs, dev environments |
| `balanced` | 0.33 | 0.34 | 0.33 | Default: production workloads |
| `performance` | 0.10 | 0.80 | 0.10 | Latency-sensitive, GPU inference |
| `availability` | 0.10 | 0.20 | 0.70 | Long-running jobs, deprecation risk |

**Hard floor filters** run before scoring: instances that don't meet minimum RAM, vCPU, or GPU requirements are eliminated entirely, not just ranked low.

Advanced users can override weights directly:

```python
profile = WorkloadProfile(
    vcpu=60,
    ram_gb=224,
    # Both short and long key spellings are accepted:
    # short: {"cost": 0.5, "perf": 0.4, "avail": 0.1}
    # long:  {"cost": 0.5, "performance": 0.4, "availability": 0.1}
    weights={"cost": 0.5, "performance": 0.4, "availability": 0.1}
)
```

---

## Workload archetypes

cloudfit-core understands five resource archetypes, each reflecting a different dominant constraint:

| Archetype | Dominant constraint | Typical workloads |
|---|---|---|
| `io` | Disk throughput | Sequencing demultiplexing, short-read alignment |
| `cpu` | Thread parallelism | Variant calling, de novo assembly, quantification |
| `mem` | RAM capacity | Metagenomics classification, single-cell RNA-seq, Hi-C |
| `gpu` | GPU VRAM | Protein structure prediction, GPU variant calling, basecalling |
| `burst` | Fleet × small instances | Nextflow pipelines, Snakemake DAGs, WDL scatter-gather |

In this release the archetype is recorded on the workload profile for classification and downstream tooling; scoring weights are driven by `optimize_for`. Archetype-aware weighting and fleet-vs-single-instance recommendations (e.g. many small spot instances for `burst`) are planned for a future release.

---

## Dynamic disk sizing

For sequencing workloads, disk requirements scale with experiment parameters rather than being fixed. cloudfit-core computes disk from first principles:

```python
from cloudfit import compute_disk_tb, WorkloadProfile, DiskSpec

disk_tb = compute_disk_tb(
    sequencer="novaseq_6000",
    flowcell="s4",
    lanes=4,
    retain_input=False,        # if True, raw input files are kept post-run
    keep_undetermined=False,   # if True, unmatched reads written to disk (+8%)
    safety_margin=0.20,
)
# → 15.84 TB

# Use the result when building your workload profile
profile = WorkloadProfile(
    vcpu=60,
    ram_gb=224,
    workload="io-intensive",
    archetype="io",
    disk=DiskSpec(sizing="static", scratch_tb=disk_tb),
)
```

`compute_disk_tb` is a standalone helper: call it before constructing your `WorkloadProfile` and pass the result into `DiskSpec.scratch_tb`.

---

## Workload YAML schema

```yaml
workload:
  type: io-intensive
  archetype: io
  parallelism: lane        # lane | sample | interval | process | rule

  resources:
    vcpu: 60
    ram_gb: 224
    disk:
      sizing: dynamic      # "dynamic" computes from experiment params; "static" uses scratch_tb
      preferred: local_ssd_first
    gpu:
      required: false

  scheduling:
    spot: false
    restart_tolerant: false

  optimize_for: balanced   # cost | performance | availability | balanced
  providers:
    - gcp
    - aws
```

Load from file:

```python
from cloudfit import from_yaml

profile = from_yaml("my-workload.yaml")
results = rank(profile, candidates)
```

---

## Provider plugins

`cloudfit-core` is the scoring engine only: it scores whatever instances you give it. Provider plugins fetch live instance data from cloud APIs on a schedule and feed the registry:

```bash
pip install cloudfit-provider-gcp   # fetches GCP Compute Engine machine types
pip install cloudfit-provider-aws   # fetches AWS EC2 instance specs and pricing
```

Each provider implements a simple interface:

```python
from cloudfit.providers.base import Provider

class MyProvider(Provider):
    def fetch_instances(self, region: str) -> list[MachineType]: ...
    def get_pricing(self, instance_id: str, region: str) -> float: ...
    def get_availability(self, instance_id: str, region: str) -> float: ...
```

Want to add a provider? See [CONTRIBUTING.md](CONTRIBUTING.md).

---

## Terraform / OpenTofu integration

Once `cloudfit-api` is running, use the Terraform provider to resolve instance types at plan time:

```hcl
data "cloudfit_recommendation" "demux_worker" {
  vcpu         = 60
  ram_gb       = 224
  workload     = "sequencing-demux"
  optimize_for = "balanced"
}

resource "google_compute_instance" "worker" {
  machine_type = data.cloudfit_recommendation.demux_worker.machine_type
}
```

---

## Known limitations

cloudfit-core is at v0.3.0 and ships with documented gaps. Listed here in priority order, with planned mitigations. The math is open and auditable; these are not surprises, they are the next-release backlog.

| Limitation | Impact | Planned mitigation |
|---|---|---|
| **GCP-only provider.** No AWS, Azure, or other cloud catalogs yet. | Cannot rank AWS/Azure instances | `cloudfit-provider-aws` is the next planned provider (DescribeInstanceTypes + Pricing API) |
| **No commitments awareness.** CUDs, Savings Plans, and Reserved Instances are not factored. Recommendations are based on on-demand prices. | Inflated effective cost for customers with committed spend | Caller-provided `commitments` payload, computed `effective_price_hr` |
| **No quota / capacity awareness.** A recommendation may be technically valid but unlaunchable in a region with exhausted quota. | "Stuck in queue" failures | Optional `quota_snapshot` payload that hard-floors candidates exceeding remaining quota |
| **No GPU type discrimination.** Only `gpu_count` and `gpu_vram_gb` are scored. A100 vs H100 vs L4 vs T4 look the same if VRAM matches. | GPU recommendations may miss the right SKU for modern ML | GPU SKU as a scored dimension with TFLOPS and memory-bandwidth lookups |
| **No CPU generation factor.** A first-gen and third-gen instance with the same vCPU count score identically on perf. | Underweights modern instances that deliver more work per core | Add generation and architecture multipliers to the perf scorer |
| **Bundled snapshots are static.** `cloudfit-api` ships with an 875-instance, five-region JSON refreshed manually via `cloudfit-provider-gcp`. | Pricing drifts over time | Live registry refreshed hourly, versioned with provenance (`fetched_at`, `source_etag`) |
| **No empirical validation.** The scoring model is documented and auditable but has not been backtested against historical batch outcomes. | Recommendations are model predictions, not evidence-backed claims | Backtest harness ingesting Nextflow / Cromwell run history to compare cloudfit picks against actual run results |

A complete self-audit covering UX, operations, scoring methodology, and the v0.2 roadmap will be published alongside the next release. Issues and PRs that surface additional gaps are welcome: see [CONTRIBUTING.md](CONTRIBUTING.md).

---

## Citing cloudfit-core

If you use cloudfit-core in your research, please cite it:

```bibtex
@software{kasaraneni2026cloudfit,
  author    = {Kasaraneni, Chaitanya Krishna},
  title     = {cloudfit-core: Cloud-agnostic machine type scoring engine
               for computational workloads},
  year      = {2026},
  publisher = {GitHub},
  url       = {https://github.com/cloudfit-io/cloudfit-core},
  orcid     = {0000-0001-5792-1095}
}
```

GitHub also shows a **Cite this repository** button in the sidebar (powered by `CITATION.cff`).

---

## Related publications

- Kasaraneni, C.K. et al. (2025). *AI-Driven Drug Repurposing: A Graph Neural Network and Self-Supervised Learning Approach.* IEEE CIACON. [doi:10.1109/CIACON65473.2025.11189545](https://doi.org/10.1109/CIACON65473.2025.11189545)
- Kasaraneni, C.K. et al. (2025). *Multi-modality Medical Image Fusion Using Machine Learning/Deep Learning.* Springer. [doi:10.1007/978-3-031-98728-1_16](https://doi.org/10.1007/978-3-031-98728-1_16)

---

## Related projects

In the cloudfit ecosystem:

- [`cloudfit-provider-gcp`](https://github.com/cloudfit-io/cloudfit-provider-gcp): GCP Compute Engine machine-type fetcher (live PyPI)
- [`cloudfit-provider-aws`](https://github.com/cloudfit-io/cloudfit-provider-aws): AWS EC2 fetcher (planning phase, accepting feedback)
- [`cloudfit-api`](https://github.com/cloudfit-io/cloudfit-api): Stateless FastAPI service over cloudfit-core ([live demo](https://chaitanyakasaraneni-cloudfit-api.hf.space/docs))
- [`cloudfit-ui`](https://github.com/cloudfit-io/cloudfit-ui): One-click Gradio demo over cloudfit-core ([live demo](https://chaitanyakasaraneni-cloudfit-ui.hf.space))

Other open-source work:

- [`samplesheet-parser`](https://github.com/chaitanyakasaraneni/samplesheet-parser): Format-agnostic Illumina SampleSheet parser (BCLConvert V2 + IEM V1)
- [`clinops`](https://github.com/chaitanyakasaraneni/clinops): Clinical ML data quality library

---

## Repository structure

```
cloudfit-core/
├── README.md               # first thing every visitor reads
├── CITATION.cff            # GitHub "Cite this repository" button: ORCID linked
├── pyproject.toml          # packaging, dependencies, PyPI metadata
├── CONTRIBUTING.md         # provider plugin interface guide
├── LICENSE                 # Apache 2.0
├── .gitignore
│
├── cloudfit/
│   ├── __init__.py         # exports rank, recommend, key models
│   ├── models.py           # WorkloadProfile, MachineType, ScoredInstance (pydantic v2)
│   ├── scorer.py           # rank(), score_instance(), weight matrix
│   ├── filter.py           # hard_floor_check(): RAM, vCPU, GPU hard filters
│   ├── disk.py             # compute_disk_tb(): dynamic disk sizing formula
│   ├── yaml_loader.py      # from_yaml(): loads workload YAML schema
│   └── providers/
│       ├── __init__.py
│       └── base.py         # abstract Provider class: plugin contract
│
└── tests/
    ├── test_scorer.py      # rank, scores, weight modes, hard floors
    ├── test_disk.py        # disk formula, CBCL vs BCL factor, sequencer profiles
    └── test_yaml.py        # from_yaml() loads profiles correctly
```

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Issues and pull requests are welcome: especially provider plugins for new cloud platforms (Azure, Hetzner, Oracle Cloud).

## License

Apache 2.0: see [LICENSE](LICENSE).

---

<sub>Author: <a href="https://ckasaraneni.com">Chaitanya Krishna Kasaraneni</a> &nbsp;·&nbsp;
<a href="https://scholar.google.com/citations?user=Y2S8D2UAAAAJ">Google Scholar</a> &nbsp;·&nbsp;
<a href="https://orcid.org/0000-0001-5792-1095">ORCID 0000-0001-5792-1095</a></sub>
