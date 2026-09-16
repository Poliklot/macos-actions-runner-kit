# Portable macOS workflow recipes

These files are **reviewable examples**, not code automatically installed into workload
repositories. Copy the needed recipe into a trusted private project, keep it in normal code
review, and call it from that project's workflow. The runner kit does not modify project CI/CD.

## Reject developer-machine paths before dependency installation

Absolute `/Users/name/...` references can make a lockfile work on one developer Mac and fail
on a clean runner. Copy `examples/scripts/check-macos-portability.py` into the project and list
the exact dependency inputs that must stay portable:

```bash
python3 tool/ci/check-macos-portability.py \
  ios/Podfile ios/Podfile.lock pubspec.lock
```

The checker is read-only. It rejects developer-home paths, missing selected inputs, escaping or
broken symlinks, and selected files above 16 MiB. It scans only paths explicitly named by the
workflow: add other lock/project/vendor metadata deliberately. Run it before `pod install`,
Flutter dependency resolution, or a build. It does not prove that a referenced relative path is
committed; pair it with the package manager's frozen/deployment mode and a clean-checkout build.

Do not “fix” a private absolute CocoaPods path by disabling deployment mode or copying an
untracked developer directory. Use a portable source supported by the project (for example a
pinned CocoaPods release, reviewed repository dependency, or committed vendor tree) and regenerate
the lockfile through the project's normal review process.

## Install actionlint without assuming GNU checksum tools

macOS includes `shasum -a 256`; many Linux-oriented installers assume `sha256sum` instead.
`examples/scripts/install-actionlint-macos.sh` supports both Apple Silicon and Intel and verifies
the pinned upstream archive before extracting only the executable. Copy and run it without sudo:

```bash
ACTIONLINT_DESTINATION="$RUNNER_TEMP/actionlint-bin" \
  bash tool/ci/install-actionlint-macos.sh
"$RUNNER_TEMP/actionlint-bin/actionlint" -color
```

The example currently pins actionlint 1.7.12 and architecture-specific SHA-256 values published
with its official release. Updating it means reviewing one release and changing the version plus
both checksums together. A preinstalled Homebrew `actionlint` selected as a runner requirement is
also valid; choose one ownership model rather than silently downloading `latest` on every run.

## Boundaries

Recipes do not grant trust to a workflow or dependency. Keep immutable action references, locked
dependencies, explicit timeouts, minimum-scoped secrets and project-specific cleanup. Never route
untrusted pull requests to a persistent personal Mac.
