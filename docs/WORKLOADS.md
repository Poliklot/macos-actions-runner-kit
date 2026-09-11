# Project requirements, not fixed stacks

The kit owns **runner lifecycle and prerequisite checks**. Your workflow owns tool
installation, dependencies, tests, builds and delivery. The host is still macOS.
No new profile or kit release is needed to add Python, Rust, Go, Terraform or another CLI.

## Recommended: one portable file per project

Keep a reviewed `ci-requirements.json` in your project. Copy/adapt an example:
[Python + Rust](../examples/requirements-python-rust.json) or
[Node + Docker + Terraform](../examples/requirements-backend.json).
These are examples, not a supported-stack matrix or automatic installers.

```bash
bash runner configure --repository OWNER/REPO --requirements /absolute/ci-requirements.json --ci-user ci_project --label local-project
```

`configure` snapshots the requirements into local ignored `tool/runner/config.json`.
Changing the original manifest later does **not** change an installed runner.
No identity, tokens or machine-specific personal paths belong in the shared manifest.
Use separate CI accounts/configs per repository and match workflow labels/account checks.
For custom local settings: `bash runner --config /absolute/config.json configure ...`.
The output gives the matching setup command; existing config files are never overwritten.

### Manifest contract

All fields are optional. `{}` selects only the base runner, not mobile defaults.

| Field | Meaning |
| --- | --- |
| `required_tools` | Up to 32 arbitrary unique command names; checks PATH presence only |
| `tool_versions` | Optional numeric version prefixes for selected `required_tools`: `3`, `3.12` or `3.12.8` |
| `path_prepend` | Up to 16 tool directories, searched after protected `~/bin` and before built-in tool paths |
| `minimum_free_gib` | Required free space, integer 20–1000; default 20 |
| `capabilities` | Optional built-in integration checks: `docker`, `node`, `ruby`, `android`, `ios`; default empty |
| `versions` | Constraints for selected integrations: Node major, Ruby major.minor, Xcode major.minor |

For `tool_versions`, `doctor` executes only `COMMAND --version`, under the CI account,
with a 15-second timeout. The first stdout line must contain one unambiguous stable dotted
numeric version. Matching is by components: `3.12` accepts `3.12.8`, not `3.120.1`.
Ranges, prereleases, custom arguments and custom regexes are intentionally unsupported.
For tools with another version interface (for example `go version`), use presence-only
`required_tools` and validate/pin the exact version in the workflow.
Do not add a custom version probe for a tool already owned by a selected built-in check.
Version probes run executables: review manifests and trust the installed tools, not just JSON.

Paths may start with `${HOME}/`, `/opt/homebrew/opt/` or `/usr/local/opt/`.
`${HOME}` means the **CI account**, never the developer's home. No relative paths, `..`,
arbitrary variables, shell syntax, personal `/Users/...` paths or custom environment exports.
Directories must exist; symlinks must resolve inside CI home or Homebrew; world-writable
search paths are refused. Homebrew Cellar symlinks are supported. The launcher still needs
Python 3.12+ in its standard locations; these paths configure jobs, not bootstrap Python.
No dependencies are copied/installed automatically except the explicitly selected Android SDK.

## Built-in integrations and optional shortcuts

- `docker`: CLI and reachable **local Unix-socket daemon** in the CI account's own context.
- `node`: Node.js + npm; optional `versions.node` major constraint.
- `ruby`: Ruby; requires `versions.ruby`.
- `android`: copied SDK/JDK/emulator readiness; does **not** implicitly require Ruby.
- `ios`: Xcode and actual iOS device destination; requires `versions.xcode`; no implicit Ruby.

These integrations have special environment/setup logic; they are not a list of allowed
languages. Combine any of them with arbitrary tools, or select no integrations at all.

CLI shorthand remains available: repeat `--capability` and `--require-tool`.
`--profile generic|node|backend|android|ios|mobile` merely expands a convenience preset;
`bash runner profiles` lists the expansion. Mobile presets include Ruby for compatibility.
`--node-version`, `--ruby-version` and `--xcode-version` constrain those shortcuts.
Use either a requirements file **or** workload flags, never an ambiguous merge of both.

## Setup, checks and updates

Base prerequisites: macOS, supported CPU, Python 3.12+, git/curl/jq/gh, free disk,
a dedicated standard CI account. `doctor --host` checks base/shared integrations only:
it skips project-specific tools/paths/version probes and only checks Docker CLI presence.
It does not contact the owner's daemon and is not a CI-account acceptance check.

`setup` installs the reviewed wrapper/config under root ownership, then runs CI-account
`doctor`. If CI-local tools are missing it may return incomplete **after installing the CLI**.
Prepare those tools under CI, then rerun `ci-runner doctor`. Registration/start remain blocked
until readiness passes. Doctor, runner and generated login-shell settings share one PATH renderer.

Legacy configs (`platforms`, `ruby_version`, `xcode_version`) and `--platforms` still work.
For compatibility, `configure` without any workload flags retains the old mobile default;
use a requirements file or `--profile generic` to choose the base explicitly.
Legacy settings normalize to schema v2 in memory; source files are never rewritten.
New v2 fields `tool_versions`/`path_prepend` default to empty if absent.
Unknown fields/schema versions fail closed. An older wrapper cannot read new fields.

To change requirements: stop all jobs/listeners, prepare a fresh local config from reviewed
source and rerun setup with the same repository/user/label. This regenerates login settings
and preserves matching registration. Do not swap only the installed JSON or upgrade a busy
runner. Keep the previous reviewed source/config to repeat setup for rollback while stopped.

## Docker and acceptance boundaries

The kit does not install/start/share Docker Desktop, OrbStack or another runtime.
CI must have its own working local context and daemon. Inherited Docker endpoint/config
variables are cleared. Remote SSH/TCP contexts are rejected before contacting a daemon.
Do not share a personal daemon or relax socket permissions: Docker exposes containers,
volumes and host-mounted files. Isolated runtime and per-job cleanup remain your responsibility.

Green doctor is not proof of project builds, PostgreSQL E2E, amd64 emulation, signing or
store delivery. Test the exact workflow separately. A dedicated user is not a VM sandbox.
Public/untrusted PR execution, Linux provisioning, shared multi-repository accounts,
organization-wide registration and arbitrary privileged install hooks are out of scope.
