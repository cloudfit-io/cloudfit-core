# Changelog

All notable changes to `cloudfit-core` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[0.2.0]: https://github.com/cloudfit-io/cloudfit-core/releases/tag/v0.2.0
[0.1.3]: https://github.com/cloudfit-io/cloudfit-core/releases/tag/v0.1.3
[0.1.2]: https://github.com/cloudfit-io/cloudfit-core/releases/tag/v0.1.2
[0.1.1]: https://github.com/cloudfit-io/cloudfit-core/releases/tag/v0.1.1
[0.1.0]: https://github.com/cloudfit-io/cloudfit-core/releases/tag/v0.1.0
