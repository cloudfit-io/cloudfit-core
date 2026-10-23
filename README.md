# cloudfit-core

[![PyPI version](https://img.shields.io/pypi/v/cloudfit-core)](https://pypi.org/project/cloudfit-core/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue)](https://pypi.org/project/cloudfit-core/)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-green)](LICENSE)
[![Tests](https://github.com/cloudfit-io/cloudfit-core/actions/workflows/ci.yml/badge.svg)](https://github.com/cloudfit-io/cloudfit-core/actions)

**Cloud-agnostic machine type scoring engine for computational workloads.**

`cloudfit-core` is the foundation of the [cloudfit](https://github.com/cloudfit-io) ecosystem: a pure Python library that, given a workload profile, scores and ranks available cloud instances across providers. No cloud credentials required. No API calls. Just a workload spec in, ranked recommendations out.

> **Try it without installing anything.** [One-click UI](https://chaitanyakasaraneni-cloudfit-ui.hf.space) for the form-driven try · [Swagger docs](https://chaitanyakasaraneni-cloudfit-api.hf.space/docs) for the API · [Landing page](https://cloudfit-io.github.io) for the visual tour.

---

## The problem

Teams hardcode instance types (`c2-standard-60`, `c7i.16xlarge`) in infrastructure-as-code and pipeline configs. When a provider deprecates them or ships a better generation, nothing updates: cost drifts and performance degrades silently.

The free, on-by-default right-sizing tools (GCP Recommender, AWS Compute Optimizer) read live telemetry from instances that are **already running**. They cannot size the workloads that need a decision *before* they run: batch jobs, ephemeral pipelines, and pre-launch services that have no telemetry yet. That gap is the niche cloudfit targets.

cloudfit-core takes a declared workload profile (vCPU, RAM, GPU) and returns ranked, explainable instance recommendations, with no running instance and no telemetry required.

> **Scope today:** the scoring engine is provider-agnostic (it ranks whatever candidates you give it), but the only live provider catalog shipped is GCP, via [`cloudfit-provider-gcp`](https://github.com/cloudfit-io/cloudfit-provider-gcp). AWS is planned, not yet released.

---

## Installation

```bash
pip install cloudfit-core
```

Requires Python 3.10+.

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
t2d-standard-60                 score=1.00  $2.31/hr
c2-standard-60                  score=0.75  $3.13/hr
c3d-standard-60-lssd            score=0.67  $3.39/hr
c7i.24xlarge                    score=0.00  $4.28/hr
```

`c7i.24xlarge` scores `0.00` and ranks last because its 192 GB RAM is below the
requested 224 GB: it's eliminated by the hard floor filter, not just ranked low
(see [How scoring works](#how-scoring-works)).

Every result also explains itself:

```python
top = results[0]
print(top.reason)          # Led by performance fit.
print(top.contributions)   # {'cost': 0.33, 'perf': 0.3371, 'avail': 0.33}
```

`contributions` is exactly what each term (`weight × sub-score`) added to `score`, and `reason` is a one-line summary that also notes a non-baseline core speed and a spot or committed-use price. A disqualified instance carries its `disqualify_reason` (e.g. `RAM 192 GB < required 224 GB`).

### Region-aware filtering

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

**Hard floor filters** run before scoring: region (when set), RAM, vCPU, GPU presence, GPU VRAM, and lifecycle status (tombstoned instances are always out). Instances that fail are eliminated entirely, not just ranked low, and carry a plain-language `disqualify_reason`.

**Cost is cost per unit of work, normalized across the candidates**, not against a fixed scale. Each candidate's cost basis is its price in the active [pricing mode](#pricing-modes) divided by its [`perf_factor`](#effective-capacity), so a faster family is cheaper per unit of work even at a higher hourly price. The cheapest qualifying basis scores 1.0 on cost and the most expensive scores 0.0, so a real gap produces a real score gap. An instance with no price (`price_hr <= 0`, e.g. a pricing lookup that failed) scores 0.0 on cost; a missing price is never treated as free.

**Performance is fit to the request**: an exact fit scores highest and oversize decays, per component, weighted by the [archetype](#workload-archetypes). **Availability** is 1.0 for active machine types and 0.4 for deprecated ones.

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

### Headroom

`headroom` asks for spare capacity above your declared `vcpu` and `ram_gb`, as a fraction. It is the compute sibling of the disk `safety_margin`. The default is `0.0` (no headroom), so existing behavior is unchanged.

```python
profile = WorkloadProfile(
    vcpu=60,
    ram_gb=224,
    headroom=0.15,                 # aim for 15% spare capacity
    headroom_mode="hard",          # "hard" (default) or "soft"
)
```

Two modes control how strictly the buffer is applied. With `headroom=h`, the target is `declared × (1 + h)`.

| Mode | Hard floor | Perf scoring | Use when |
|---|---|---|---|
| `hard` (default) | Raised to the target: instances without the buffer are **disqualified** | Peak fit recenters on the target | You need the slack guaranteed |
| `soft` | Unchanged: nothing is disqualified | Peak fit recenters on the target, so instances below it lose fit credit but can still rank on cost or availability | You prefer the buffer but will accept a tight fit |

When both `headroom` and `ram_floor_gb` are set, the RAM floor is the larger of the two (`max(ram_floor_gb, ram_gb × (1 + headroom))`).

### Effective capacity

Machine families differ in per-core speed, so 32 vCPUs of c3 do more work than 32 vCPUs of t2d. `MachineType.perf_factor` captures this relative to an n2 baseline of 1.0, and the scorer uses it twice: vCPU fit is measured on effective capacity (`vcpu × perf_factor`), and cost is divided by it (cost per unit of work). Providers populate it from the documented `PERF_FACTORS` table (19 GCP families) via `perf_factor_for(machine_id)`; set it yourself to override. With `perf_factor` unset, scoring is unchanged.

```python
from cloudfit import MachineType, WorkloadProfile, perf_factor_for, rank

candidates = [
    MachineType(id="c3-standard-32", provider="gcp", vcpu=32, ram_gb=128, price_hr=1.69,
                spot_price_hr=0.62, perf_factor=perf_factor_for("c3-standard-32")),  # 1.2
    MachineType(id="n2-standard-32", provider="gcp", vcpu=32, ram_gb=128, price_hr=1.55,
                spot_price_hr=0.93, perf_factor=perf_factor_for("n2-standard-32")),  # 1.0
]
profile = WorkloadProfile(vcpu=32, ram_gb=128, archetype="cpu", optimize_for="cost")
print(rank(profile, candidates)[0].reason)
# Led by cost; c3 cores ~20% faster than baseline.
```

c3 costs more per hour here but wins on cost per unit of work.

### Pricing modes

`pricing_mode` chooses which price the cost score uses: `on_demand`, `spot`, or `cud_1yr` (1-year committed use). Left unset, a workload that declares `scheduling.spot=True` is scored on spot, as is the `burst` archetype (scatter-gather work is restart-tolerant); everything else defaults to on-demand. An explicit `pricing_mode` always wins. When a candidate has no positive price for the chosen mode (`spot_price_hr` / `cud_1yr_price_hr`), its on-demand `price_hr` is used instead, so a partial price set never makes an instance look free.

```python
profile = WorkloadProfile(vcpu=32, ram_gb=128, archetype="cpu", optimize_for="cost",
                          pricing_mode="spot")
print(rank(profile, candidates)[0].reason)
# Led by cost; c3 cores ~20% faster than baseline; priced on spot (interruptible).
```

### GPU throughput

For the `gpu` archetype, when an instance declares `gpu_type`, the GPU term scores accelerator throughput (`gpu_count × GPU_CLASS[type]`, relative to an H100) instead of VRAM fit, so an A100 outranks a T4 at equal VRAM. The `GPU_CLASS` table covers H100, A100, V100, L4, T4, P100, and P4; `gpu_class_for(gpu_type)` resolves accelerator names. VRAM stays a hard floor either way, and without `gpu_type` the GPU term falls back to VRAM fit.

---

## Workload archetypes

> **What `archetype` does today:** it sets the per-component weighting of the
> performance score, so the ranking emphasizes the dimension that dominates the
> workload. `cpu` weights vCPU fit, `mem` weights RAM fit, `io` weights local
> SSD against the declared `scratch_tb`, and `gpu` weights GPU throughput
> (or VRAM fit when the instance has no `gpu_type`). The
> weights are heuristics, not yet calibrated against validation data, and the
> hard floors and `optimize_for` weights still apply on top. Fleet-vs-single
> recommendations for `burst` remain on the roadmap; `burst` currently weights
> vCPU and RAM equally and defaults to spot pricing.

cloudfit-core recognizes five resource archetypes. The "dominant constraint"
column drives the perf weighting the engine applies for that archetype:

| Archetype | Dominant constraint | Perf weighting | Typical workloads |
|---|---|---|---|
| `io` | Disk throughput | vCPU 0.3 / RAM 0.3 / local SSD 0.4 | Sequencing demultiplexing, short-read alignment |
| `cpu` | Thread parallelism | vCPU 0.7 / RAM 0.3 | Variant calling, de novo assembly, quantification |
| `mem` | RAM capacity | vCPU 0.2 / RAM 0.8 | Metagenomics classification, single-cell RNA-seq, Hi-C |
| `gpu` | GPU throughput | vCPU 0.1 / RAM 0.1 / GPU 0.8 | Protein structure prediction, GPU variant calling, basecalling |
| `burst` | Fleet of small instances | vCPU 0.5 / RAM 0.5 | Nextflow pipelines, Snakemake DAGs, WDL scatter-gather |

---

## Dynamic disk sizing

For sequencing workloads, disk requirements scale with experiment parameters rather than being fixed. cloudfit-core estimates disk from experiment parameters:

> **These are planning estimates, not measurements.** The per-lane sizes and the output/tmp/compression multipliers in [`disk.py`](cloudfit/disk.py) are approximate heuristics, not validated against a corpus of real runs. Treat the result as a starting point for provisioning (the default 20% safety margin exists for this reason), and verify against your own pipeline before relying on it.

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
    spot: false            # true scores on spot prices unless pricing_mode is set
    restart_tolerant: false

  optimize_for: balanced   # cost | performance | availability | balanced
  pricing_mode: on_demand  # on_demand | spot | cud_1yr (optional)
  region: us-central1      # optional region hard floor
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
pip install cloudfit-provider-gcp   # GCP Compute Engine machine types and pricing
# cloudfit-provider-aws (AWS EC2) is planned, not yet released
```

Each provider implements a simple interface:

```python
from cloudfit.providers.base import Provider

class MyProvider(Provider):
    def fetch_instances(self, region: str) -> list[MachineType]: ...
    def get_pricing(self, instance_id: str, region: str) -> float: ...
```

Availability is carried by each `MachineType.status` (`active` / `deprecated` /
`tombstoned`), so the provider sets it when building instances; the engine does
not call a separate availability method. Providers should also populate
`perf_factor` (via `perf_factor_for`), `gpu_type` for GPU instances, and
`spot_price_hr` / `cud_1yr_price_hr` where the cloud publishes them.

Want to add a provider? See [CONTRIBUTING.md](CONTRIBUTING.md).

---

## Terraform / OpenTofu integration

> **Planned, not yet released.** No Terraform provider is published yet; this sketch shows the intended interface. Today you can call `cloudfit-api`'s `/recommend` from a Terraform `http` data source.

Once a Terraform provider ships, resolving instance types at plan time will look like:

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

cloudfit-core ships with documented gaps. Listed here in priority order, with planned mitigations. The math is open and auditable; these are not surprises, they are the next-release backlog.

| Limitation | Impact | Planned mitigation |
|---|---|---|
| **GCP-only provider.** No AWS, Azure, or other cloud catalogs yet. | Cannot rank AWS/Azure instances | `cloudfit-provider-aws` is the next planned provider (DescribeInstanceTypes + Pricing API) |
| **Partial commitments awareness.** Spot and 1-year committed-use prices are scored when a provider supplies them (`spot_price_hr`, `cud_1yr_price_hr`); multi-year CUDs, Savings Plans, Reserved Instances, and account-level committed-spend discounts are not yet factored. | Effective cost can still be overstated for customers with broader committed spend | Caller-provided `commitments` payload that discounts candidates against existing commitments |
| **No quota / capacity awareness.** A recommendation may be technically valid but unlaunchable in a region with exhausted quota. | "Stuck in queue" failures | Optional `quota_snapshot` payload that hard-floors candidates exceeding remaining quota |
| **Heuristic speed and GPU tables.** `PERF_FACTORS` and `GPU_CLASS` are documented, dated estimates (GCP families and common accelerators), not benchmarks of your workload. Unknown families and accelerators fall back to the baseline / VRAM fit. | Relative speed can be off for a given tool | Workload-specific factors calibrated from run history (see backtesting below) |
| **Snapshots are refreshed weekly, not live.** `cloudfit-api` and `cloudfit-ui` bundle an 875-instance, five-region JSON whose on-demand, spot, and committed-use prices are refreshed weekly from the GCP billing catalog via a scheduled pull request. GPU prices are not reconstructed and stay approximate. | Prices can lag by up to a week; GPU cost is approximate | Live registry refreshed hourly, versioned with provenance (`fetched_at`, `source_etag`) |
| **No empirical validation.** The scoring model is documented and auditable but has not been backtested against historical batch outcomes. | Recommendations are model predictions, not evidence-backed claims | Backtest harness ingesting Nextflow / Cromwell run history to compare cloudfit picks against actual run results |

Issues and PRs that surface additional gaps are welcome: see [CONTRIBUTING.md](CONTRIBUTING.md).

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
│   ├── __init__.py         # exports rank, score_instance, key models
│   ├── models.py           # WorkloadProfile, MachineType, ScoredInstance (pydantic v2)
│   ├── scorer.py           # rank(), weights, PERF_FACTORS, GPU_CLASS, pricing modes
│   ├── filter.py           # hard_floor_check(): region, RAM, vCPU, GPU, status
│   ├── disk.py             # compute_disk_tb(): dynamic disk sizing formula
│   ├── yaml_loader.py      # from_yaml(): loads workload YAML schema
│   └── providers/
│       ├── __init__.py
│       └── base.py         # abstract Provider class: plugin contract
│
└── tests/
    ├── test_scorer.py        # rank, scores, weight modes, hard floors
    ├── test_perf_factor.py   # effective capacity and PERF_FACTORS lookups
    ├── test_cost_realism.py  # pricing modes, cost per unit of work
    ├── test_gpu_class.py     # GPU throughput and GPU_CLASS lookups
    ├── test_disk.py          # disk formula, CBCL vs BCL factor, sequencer profiles
    └── test_yaml.py          # from_yaml() loads profiles correctly
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
