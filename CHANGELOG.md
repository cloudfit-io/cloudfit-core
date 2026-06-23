# Changelog

All notable changes to `cloudfit-core` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Effective-capacity perf scoring (scoring-depth Layer 1).** `MachineType` gains an optional `perf_factor` (relative per-vCPU performance vs a baseline of `n2` = 1.0). When set, `_perf_score` fits effective capacity (`vcpu * perf_factor`) to the request, so a faster family is no longer equated with a slower one at the same core count. Core ships a documented, overridable `PERF_FACTORS` table and a `perf_factor_for(machine_id)` helper for providers and callers to populate the field. The factors are indicative heuristics (SPECrate/CoreMark per-vCPU basis, read 2026-06-23) pending real run-telemetry calibration.
- **Cost realism: pricing modes and cost-efficiency (scoring-depth Layer 2).** `MachineType` gains optional `spot_price_hr` and `cud_1yr_price_hr` (both fall back to `price_hr` when unset). `WorkloadProfile` gains `pricing_mode` (`on_demand`/`spot`/`cud_1yr`) with an archetype-aware default exposed as `effective_pricing_mode`: the `burst` archetype defaults to spot (restart-tolerant), everything else to on-demand. Cost is now scored on a cost basis of `price(mode) / perf_factor` (cost per unit of work) rather than raw hourly price, so a faster family is cheaper per unit of work. A `PricingMode` enum is exported.
- **GPU SKU discrimination (scoring-depth Layer 3).** `MachineType` gains optional `gpu_type`. For the `gpu` archetype the GPU term now scores effective throughput (`gpu_count * GPU_CLASS[type]`, capped at one top-class accelerator) so an A100 outscores a T4 at equal VRAM and two GPUs beat one; VRAM remains a hard floor. Core ships a documented, overridable `GPU_CLASS` table (peak dense FP16/BF16 tensor TFLOPS basis, normalized to H100, read 2026-06-23) and a `gpu_class_for(gpu_type)` helper.
- **Score explainability (scoring-depth Layer 4).** `ScoredInstance` gains `contributions` (the exact `weight * sub-score` each component added to the composite; they sum to `score`) and `reason` (a one-line human explanation naming the dominant driver, flagging a non-baseline `perf_factor`, and noting spot/committed-use pricing). Disqualified results carry the floor reason in `reason` and empty `contributions`.

### Notes
- **Default behavior is unchanged.** Unset `perf_factor`, `gpu_type`, `spot_price_hr`, `cud_1yr_price_hr`, and an unset `pricing_mode` on a non-burst workload all reproduce the prior scores: effective capacity equals physical vCPU, the GPU term reduces to VRAM fit, and the cost basis reduces to raw price. The README golden example is pinned and unchanged. Realism activates once a provider populates the new fields (cloudfit-provider-gcp will do this in its next minor).
- **Cost denominator (deliberate deviation from the design doc).** The doc proposed `price / effective_capacity`; dividing by physical vCPU would reward over-provisioning (a bigger box looks cheap per core) and fight the perf fit. Cost is instead divided by `perf_factor` only, modelling total cost to finish a fixed job (runtime scales with effective speed). This surfaces the Layer 1 speed advantage in cost without rewarding raw oversize.
- **Semantic to be aware of (nominal-vCPU interpretation).** The requested `vcpu` is treated as baseline-equivalent work. In `performance` mode an instance sized to exactly the request scores highest on perf; a faster family at the same physical core count counts as oversized in effective terms, so its speed advantage surfaces through cost-efficiency rather than a higher perf sub-score. A faster family still outranks a slower one at the same physical cores.

## [0.7.0] - 2026-06-14

### Changed
- **Performance scoring now discriminates between fits instead of plateauing.** `_fit` previously returned a flat `1.0` for any instance between 1.0x and 1.5x of the (headroom-adjusted) target, so well-matched candidates tied at the top and the composite score read ~1.0 for nearly everything. Perf now peaks at an exact fit (1.0x) and decays linearly to 0 at 3.5x, so a tighter fit outscores a more oversized one and the rankings spread out. The `headroom` parameter already recenters the target on the buffered size, so the removed plateau was double-counting headroom. This shifts `rank()` and `score()` perf sub-scores (and therefore composite scores) for over-provisioned candidates; exact-fit and disqualified results are unchanged.

## [0.6.1] - 2026-06-14
### Added
- `py.typed` marker so downstream packages (cloudfit-provider-gcp, cloudfit-api) can type-check against cloudfit-core under `mypy --strict`.

### Changed
- **Minimum Python is now 3.10** (`requires-python = ">=3.10"`). Python 3.9 is dropped from the supported set and the CI matrix. The models use PEP 604 union syntax (`Archetype | str`) in pydantic fields, which is not reliably evaluable on 3.9; 3.10+ removes that risk and aligns cloudfit-core with cloudfit-provider-gcp.

## [0.6.0] - 2026-06-11

### Fixed
- **`compute_disk_tb` no longer returns 0.0 for small runs.** The TB result is rounded to 4 decimals instead of 2, so sub-5 GB runs (MiSeq Nano, micro) report a real non-zero figure. `compute_disk_breakdown` now raises `ValueError` for `lanes < 1`.
- **FASTQ output sizing is now independent of input compression.** Compressed-format flowcells (NovaSeq X) shrink the stored input only; demultiplexed output is derived from the raw uncompressed size, fixing the understated disk estimate for compressed runs.
- **Custom weights are validated.** `WorkloadProfile` now rejects partial, negative, and non-normalized weight dicts (must sum to 1.0 within 1e-6) instead of silently producing scores outside `[0, 1]`. Long-form keys (`performance`/`availability`) are accepted and normalized.
- **Unknown fields are rejected.** All five models use `extra="forbid"`, so a mistyped field raises `ValidationError` rather than being silently dropped. The YAML loader still tolerates unknown keys, so schema typos do not break file loading.
- **`from_dict` / `from_yaml` raise `ValueError`** (not `AttributeError`) for empty, scalar, or list YAML input.

### Changed
- **Archetype now drives perf weighting.** `_perf_score` weights vCPU, RAM, local SSD (vs `scratch_tb`), and GPU VRAM per archetype (`io`/`cpu`/`mem`/`gpu`/`burst`). A component with no declared requirement fits perfectly and does not distort the score, so prior examples with no `scratch_tb` rank identically. This is a behavior change for workloads whose archetype-dominant dimension differs across candidates.
- `archetype` and `optimize_for` accept `str` as well as their enum types, matching the documented string-based call style under static type checking.

### Added
- Tests for disk small-run sizing, weight validation, `extra="forbid"`, malformed-YAML handling, and archetype-dependent ranking.

### Removed
- `Provider.get_availability` from the abstract contract: the engine reads availability from `MachineType.status`. Providers convey availability by setting that field.
- Unused `samples` parameter from `compute_disk_tb` / `compute_disk_breakdown`.

## [0.5.0] - 2026-06-07

### Added
- **User-provided compute headroom.** `WorkloadProfile.headroom` (fraction, default `0.0`) requests spare capacity above the declared `vcpu`/`ram_gb`, the compute sibling of the disk `safety_margin`. `headroom_mode` selects how it is applied: `hard` (default) raises the hard floor so instances without the buffer are disqualified, and `soft` recenters perf scoring only without disqualifying anything. Both modes recenter the perf fit peak on the buffered target (`declared * (1 + headroom)`).
- `WorkloadProfile` helper properties: `perf_target_vcpu`, `perf_target_ram_gb`, `effective_vcpu_floor`, `effective_ram_floor_gb`.
- `HeadroomMode` enum is now exported from the package root.
- `from_dict` / `from_yaml` read `headroom` (under `resources`) and `headroom_mode` (under `workload`).
- Tests covering hard/soft modes, the `max(ram_floor_gb, headroom target)` tie-break, and the headroom=0 no-op guarantee.

### Notes
- Backward compatible: with the default `headroom=0.0`, floors and scores are identical to 0.4.0. When both `headroom` and `ram_floor_gb` are set, the RAM floor is `max(ram_floor_gb, ram_gb * (1 + headroom))`.

## [0.4.0] - 2026-06-02

### Changed
- **Cost scoring is now candidate-relative.** `_cost_score` normalizes against the min/max price of the qualifying candidates (cheapest = 1.0, most expensive = 0.0) instead of a
fixed `$35/hr` reference. The old normalizer compressed realistic prices into the top ~3% of the scale, so a 35% price gap moved the score by ~0.02. Note: composite scores are now a
within-query ranking signal and are not comparable across separate `rank()` calls; use price for cross-query comparison.
- `scorer.py` now imports the canonical `hard_floor_check` from `filter.py` instead of carrying its own private copy.
- README: corrected the quick-start example output (now pinned by a test), rewrote "The problem" around the no-telemetry niche and GCP-only scope, made the `archetype` "does not
change ranking" disclaimer prominent, and added a validation caveat to the disk-sizing section.

### Added
- `score_instance(..., price_range=...)` keyword-only argument; `rank()` supplies it automatically.
- Tests: `test_cost_score_spreads_across_candidate_range`, `test_unpriced_instance_is_not_treated_as_free`, `test_readme_headline_example_matches_docs`.

### Fixed
- Unpriced instances (`price_hr <= 0`) no longer receive the maximum cost score; they score 0.0 and are excluded from cost normalization.

### Removed
- Duplicate `_hard_floor_check` in `scorer.py`.

## [0.3.0] - 2026-05-31

### Changed
- **Performance scorer is now fit-based.** Replaces the pre-0.3 behavior, which capped at 2x the requested size and structurally rewarded oversize (an exact match scored 0.5; a 2x-oversize instance scored 1.0). The new `_perf_score` peaks at exact match plus a healthy headroom band (1.0x-1.5x of requested vCPU/RAM), then decays linearly to 0 at 3.5x. This matches common cloud-sizing practice and removes the "balanced mode picks the 2x machine" behavior. Behavior change is intentional and resolves the "performance scorer headroom heuristic" item in the README's Known Limitations.

### Added
- `_PERF_IDEAL_RATIO_MAX` and `_PERF_DECAY_END_RATIO` constants in `scorer.py`, exposed as tuning knobs for future calibration.
- Three tests covering the new fit behavior: balanced mode prefers exact fit over 2x, perf_score saturates inside the 1.0x-1.5x band, and perf_score is 0 at heavy oversize.

### Removed
- The "Performance scorer caps at 2× the requested size" line from the README's Known Limitations table.

## [0.2.0] - 2026-05-28

### Added
- `WorkloadProfile.region: Optional[str]` field. When set, the hard floor disqualifies candidates not available in the matching region.
- Region check as the first hard floor in `filter.hard_floor_check()` and `scorer._hard_floor_check()`. Disqualified instances surface the region mismatch in `disqualify_reason`.
- Tests covering region floor behavior (`test_region_hard_floor_disqualifies_other_regions`, `test_no_region_allows_all_regions`).

### Changed
- README quick start expanded with a "Region-aware filtering" subsection.

## [0.1.3] - 2026-05-27

### Changed
- Packaging and CI workflow maintenance.

## [0.1.2] - 2026-05-27

### Changed
- Build configuration maintenance.

## [0.1.1] - 2026-05-27

### Added
- "Known limitations" section in the README cataloging the v0.2 backlog: performance scorer headroom heuristic, GCP-only provider coverage, no commitments awareness, no quota awareness, no GPU type discrimination, no CPU generation factor, static bundled snapshots, no empirical validation.

## [0.1.0] - 2026-05-22

### Added
- Initial release of the cloudfit-core scoring engine.
- `rank()` and `score_instance()` for ranking candidate machine types against a workload profile.
- `WorkloadProfile`, `MachineType`, `ScoredInstance`, `OptimizeFor`, `Archetype`, `DiskSpec`, `GPUSpec`, `SchedulingSpec` pydantic models.
- Hard-floor filtering on RAM, vCPU, GPU presence, GPU VRAM, and machine status (active / deprecated / tombstoned).
- Weighted scoring across cost, performance, and availability with four optimization modes: `cost`, `balanced`, `performance`, `availability`.
- Custom `weights` override for advanced users, accepting both short (`perf`, `avail`) and long (`performance`, `availability`) key spellings.
- Dynamic disk sizing for I/O-heavy workloads via `compute_disk_tb()` and `compute_disk_breakdown()`.
- YAML loader (`from_yaml`, `from_dict`) for declarative workload profiles.
- Provider plugin interface (`cloudfit.providers.base.Provider`) for community-built provider packages.
- Apache 2.0 license. CITATION.cff for academic citation.

[0.6.0]: https://github.com/cloudfit-io/cloudfit-core/releases/tag/v0.6.0
[0.5.0]: https://github.com/cloudfit-io/cloudfit-core/releases/tag/v0.5.0
[0.4.0]: https://github.com/cloudfit-io/cloudfit-core/releases/tag/v0.4.0
[0.3.0]: https://github.com/cloudfit-io/cloudfit-core/releases/tag/v0.3.0
[0.2.0]: https://github.com/cloudfit-io/cloudfit-core/releases/tag/v0.2.0
[0.1.3]: https://github.com/cloudfit-io/cloudfit-core/releases/tag/v0.1.3
[0.1.2]: https://github.com/cloudfit-io/cloudfit-core/releases/tag/v0.1.2
[0.1.1]: https://github.com/cloudfit-io/cloudfit-core/releases/tag/v0.1.1
[0.1.0]: https://github.com/cloudfit-io/cloudfit-core/releases/tag/v0.1.0
