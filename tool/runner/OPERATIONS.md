# Operations / Эксплуатация

## Requirements and configuration

macOS on Apple Silicon or Intel; Python 3.12+, Ruby matching `ruby_version`, git/curl/jq/gh.
For iOS: matching Xcode, first-launch setup and an iPhoneOS SDK.
For Android: a full **source SDK** owned by the administrator, including platform-tools,
build-tools, emulator and command-line tools, plus a working JDK.
No automatic Homebrew/Xcode/SDK installation or licence acceptance is performed.

`configure` creates local, ignored `config.json` without root/network access and never
overwrites an existing file or symlink. Review non-secret settings before setup:

| Setting | Meaning |
|---|---|
| repository | Private workload repository, not this helper's public source |
| label | Must match every local workflow job; default `local-macos` |
| ci_user | Dedicated non-admin account, default `ci` |
| platforms | Android, iOS or both; sets prerequisite checks only |
| xcode_version / ruby_version | Must agree with your project's workflow |
| minimum_free_gib | At least 20 GiB after any SDK copy |
| runner_version / runner_sha256 | Bootstrap version plus both official archive checksums |

Other versions: `configure --xcode-version 26.3 --ruby-version 3.3`.
Another project on the same Mac: use a different `--ci-user`, for example `ci_second`,
and its own reviewed config. One account belongs to one repository/runner.
For an existing config, review edits locally; repeat setup only with the runner stopped.
Changing an installed repository/label is refused rather than repurposing its credentials.

## Installation and update

`setup` runs only from the administrator's normal shell, not from root or `ci`.
It validates prerequisites before sudo; the privileged child starts in `/` so it does
not inherit an inaccessible private checkout. SDK extraction runs **as the CI user**;
root only reads the selected SDK components. Personal `.android` credentials are not copied.
Interrupted copies retain a marker; repeat setup, do not clear the marker manually.

The installer does not grant admin, passwordless sudo, Full Disk Access or FileVault rights,
and does not change permissions on Homebrew/Xcode or the owner's home directory.
It reuses an existing matching account/SDK, refuses unknown ownership/symlinks and a running listener.
Account creation and SDK copying are not a single transaction; a failed setup may leave partial state.

Installed paths:
- root-owned CLI/config: `/Library/Application Support/Local Actions/<ci_user>`;
- launcher: `/Users/<ci_user>/bin/ci-runner`;
- official runner and persistent credentials: `/Users/<ci_user>/actions-runner`;
- SDK: `/Users/<ci_user>/Library/Android/sdk`;
- CI shell environment: `~/.config/local-ci/env.sh`, sourced by `.zprofile`.

To update: wait for jobs to finish → Ctrl+C → `exit` → obtain/review new source → repeat setup
with the existing config. Registration is preserved. Do not modify a runner while a job is active.

## Registration, language and logs

Registration uses a short-lived repository registration token, not a PAT.
Only hidden terminal input is accepted; the token is passed to the official child via
`ACTIONS_RUNNER_INPUT_TOKEN`, never in argv or a kit file. This does not hide it from root
or another process under the same account. The official runner stores its own persistent credentials.
The default runner group and a unique name are chosen automatically; other runners are never replaced.
UTF-8 registration files with and without BOM are accepted. A foreign registration is refused.

English is the standalone default. `setup --lang ru` persists Russian for the installed CLI.
`CI_RUNNER_LANG` overrides the saved choice; `--lang ru|en` overrides both.
Python and Bash use one catalog. External runner/macOS/tool logs retain their original language.

## Starting and stopping

`start` keeps the official runner in the foreground under `caffeinate -i` and prevents
concurrent kit starts. Terminal Ctrl+C reaches the official runner; the wrapper waits for it.
No background service, automatic dispatch or cloud fallback is installed.
**Wait for all jobs** before stopping. Ctrl+C during a job cancels it. `caffeinate -i`
does not promise operation with the lid closed or after reboot/power loss.

Stopping is not unregistering: credentials and caches remain for the next session.
For decommissioning, remove the runner in GitHub settings first, review/revoke workload
credentials and only then remove the dedicated account/data using macOS administration tools.
No automated destructive uninstall is provided.

## Signing and recovery

This kit does not install certificates, profiles or a signed release workflow.
No default keychain can be normal for a new headless CI user **only when the project's
workflow explicitly creates, uses and cleans up a temporary keychain**. The kit does not do that for you.
`doctor` makes a read-only Keychain API query; it does not prove codesign, notarization or delivery.
It recognizes `~/Library/Caches/*-signing-state` journals as an incomplete project cleanup
convention, not universal recovery detection. Restore using the project's own procedure; never
delete recovery evidence merely to make doctor green. A generic successful smoke test is not a release test.

## Official dependencies

Initial runner 2.337.0 archives are checked against the bundled SHA-256 values. The official
runner's automatic update mechanism remains enabled; bootstrap pinning is not a permanent runtime pin.
When updating, verify both architectures against the [official release](https://github.com/actions/runner/releases/tag/v2.337.0).
No runner/SDK binaries are bundled in this source distribution.

[Registration guide](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/add-runners) ·
[Official environment inputs](https://github.com/actions/runner/blob/v2.337.0/src/Runner.Listener/CommandSettings.cs) ·
[Self-hosted security limits](https://docs.github.com/en/actions/security-for-github-actions/security-guides/security-hardening-for-github-actions#hardening-for-self-hosted-runners)
