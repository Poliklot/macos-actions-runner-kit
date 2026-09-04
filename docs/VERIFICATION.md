# Verification — 2026-09-04

Local environment: macOS / Apple Silicon, Python 3.14.0. This document records tests,
not independent security certification or a verified end-to-end installation on another Mac.

| Check | Result |
|---|---|
| Offline unit/regression suite | 70 tests passed |
| Shell/Python syntax | Passed |
| ShellCheck: launchers, installer, shared messages and check script | Passed |
| actionlint: helper CI and private manual example | Passed |
| Gitleaks: complete source tree | No findings |
| Fresh source archive, without Git metadata or project config | Same 70 tests passed |
| Deterministic packaging / traversal, symlink and runtime-file rejection | Covered by tests |
| Standalone configure in a temporary directory, EN/RU | Passed without sudo/network/account creation |
| Real installed standalone setup / signed release / second Mac / Intel | Not performed |
| GitHub-hosted macOS 15 / Python 3.14.7 | 70 tests passed; source packaging passed |
| GitHub-hosted Ubuntu 24.04 / Python 3.12.14 | 66 passed, 4 macOS-only checks skipped; source packaging passed |

Hosted evidence: [Check the helper, run 33874038227](https://github.com/Poliklot/macos-actions-runner-kit/actions/runs/33874038227),
commit `611049ff0a4b28ad9f4865bf004592e6e666d616`. Both jobs succeeded on GitHub-hosted runners.
The public repository has no registered personal runner; workflow token permissions are read-only.

Mac-only tests include a read-only Keychain comparison and a real foreground SIGINT lifecycle
with a dummy runner. Download/registration/provisioning scenarios otherwise use fixtures and
mocked privileged/network boundaries; no real account or GitHub runner is created by the test suite.

The package was extracted from source files only. Original workload configuration, build pipelines,
signing helpers, runtime credentials, account data, private repository names and Git history are not included.

Reproduce: `bash scripts/check.sh`, then `python3 scripts/package.py` with Python 3.12+.
The source tarball contains only `source-manifest.json` entries, including the MIT license.
The maintainer approved MIT publication under Poliklot and deferred independent-machine acceptance
on 2026-09-04. This does not imply a verified second Mac or a stable release.
