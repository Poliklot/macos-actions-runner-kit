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

## Temporary Apple signing keychain with an explicit WWDR chain

A signing identity alone is not enough: `codesign` also needs the Apple Worldwide Developer
Relations intermediate certificate chain. Do not depend on whatever a developer happened to
install in the System keychain. Copy `examples/scripts/apple-signing-keychain.py` and keep the
required public WWDR certificate in a reviewed project location with a pinned SHA-256. Download
that public certificate only from Apple's [certificate authority page](https://www.apple.com/certificateauthority/).
The private `.p12` remains a scoped workflow secret and must never be committed.

Prepare the keychain before signing:

```bash
SIGNING_STATE_NAME=mobile-signing-state \
SIGNING_CERTIFICATE_PATH="$RUNNER_TEMP/distribution.p12" \
SIGNING_CERTIFICATE_PASSWORD="$APPLE_CERTIFICATE_PASSWORD" \
APPLE_WWDR_CERTIFICATE_PATH="$GITHUB_WORKSPACE/tool/ci/AppleWWDRCAG3.cer" \
APPLE_WWDR_SHA256="reviewed-lowercase-sha256" \
python3 tool/ci/apple-signing-keychain.py prepare
```

Then verify the intended identity and perform the project's normal signed build. Add a separate
`if: always()` cleanup step; it needs no signing secrets:

```bash
SIGNING_STATE_NAME=mobile-signing-state \
python3 tool/ci/apple-signing-keychain.py cleanup-if-present
```

The helper creates only a temporary **user** keychain, imports the pinned public WWDR certificate
and private identity, makes it the job default, checks for a valid code-signing identity, and
records the previous user-keychain list/default in a mode-0600 recovery journal under
`~/Library/Caches/*-signing-state`. Successful cleanup restores that state and deletes the temporary
keychain. It never changes the System keychain, installs a provisioning profile, chooses a signing
identity, notarizes, or delivers an app. If power loss leaves the journal behind, `ci-runner doctor`
blocks startup; inspect it and run `cleanup` rather than deleting the evidence.

`security` necessarily receives the temporary generated keychain password and imported `.p12`
password while it runs. Keep the CI account dedicated, do not enable shell tracing, delete the
workflow-created `.p12`/profile files in the project's own always-cleanup step, and scope/revoke
the upstream secret independently.
