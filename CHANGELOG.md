# Changelog

## 0.2.0-alpha.1 — 2026-09-11

- Made portable project requirements the primary interface: arbitrary CLI tools,
  optional numeric version constraints, CI-local search paths and disk requirements.
- Separated optional SDK/Docker integrations from project languages. Named profiles
  remain convenience shortcuts, not a closed list of supported stacks.
- Added fixed, bounded CI-only version probes; rejected ambiguous flags, unsafe paths,
  unknown fields and conflicting version contracts. No executable config/install hooks.
- Kept legacy mobile config/CLI support; setup installs a validated v2 snapshot
  without rewriting source settings or replacing repository registration.
- Unified doctor, runner and login-shell environments; non-mobile profiles do not
  require SDKs/Ruby/signing checks. Added bounded Node/npm and local Docker probes.
- Added a private manual Node CI example and migration guidance. No installed-runner
  update, real backend deployment, Linux support or second-account acceptance is implied.

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
