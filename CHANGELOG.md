# Changelog

## 0.1.0-alpha.1 — 2026-09-04

- Doctor resolves an actual generic iOS build destination rather than trusting SDK
  version metadata alone; missing Platform Support now fails before runner startup.
- Standalone configurable macOS foreground runner helper, extracted without workload configuration.
- Dedicated standard-account provisioning and SDK ownership checks.
- RU/EN CLI and secure hidden registration-token input; BOM-compatible registration reuse.
- Non-overwriting local `configure` command and a root entry point independent of the caller's current directory.
- Offline regression suite, manual private-workload smoke template and explicit safety boundaries.
- MIT license and private vulnerability reporting for the public Poliklot repository.
- Independent-machine acceptance is deferred; no stable or full signed-release claim.
