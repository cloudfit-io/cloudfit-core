# Changelog

All notable changes to `cloudfit-core` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[0.3.0]: https://github.com/cloudfit-io/cloudfit-core/releases/tag/v0.3.0
[0.2.0]: https://github.com/cloudfit-io/cloudfit-core/releases/tag/v0.2.0
[0.1.3]: https://github.com/cloudfit-io/cloudfit-core/releases/tag/v0.1.3
[0.1.2]: https://github.com/cloudfit-io/cloudfit-core/releases/tag/v0.1.2
[0.1.1]: https://github.com/cloudfit-io/cloudfit-core/releases/tag/v0.1.1
[0.1.0]: https://github.com/cloudfit-io/cloudfit-core/releases/tag/v0.1.0
