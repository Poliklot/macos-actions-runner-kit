# First public release checklist

Early public source release under MIT. This is not an independently audited or stable release.
The maintainer approved publication and deferred the second-Mac check on 2026-09-04.

## Publication decisions

- [x] Maintainer authorized public redistribution of the generic helper and tests.
- [x] Repository: `Poliklot/macos-actions-runner-kit`; license: MIT, copyright 2026 Poliklot.
- [x] Approved `LICENSE` included in the source manifest and vendoring instructions.
- [x] Complete source package reviewed for private data and scanned with Gitleaks.
- [x] Public repository creation and exact Git add/commit/push operations authorized.
- [x] GitHub private vulnerability reporting enabled and verified through the API; SECURITY.md links the channel.

## Automated evidence

- [x] `bash scripts/check.sh` and static checks pass locally on Apple Silicon, including a clean source archive.
- [ ] Hosted CI passes without repository secrets or any self-hosted runner.

## Deferred acceptance — required before broader readiness claims

- [ ] A second developer follows the short README on a separate Mac without undocumented commands.
- [ ] Fresh setup, interrupted SDK-copy recovery, repeated setup and existing registration are verified on a disposable account.
- [ ] Real private-repository smoke job, clean stop and another successful start are verified.
- [ ] Apple Silicon verified independently; Intel checked before claiming verified Intel support.
- [ ] Any advertised Android/iOS signing and delivery claims have their own evidence; a green doctor is insufficient.
- [ ] Test decommissioning/revocation instructions without deleting unrelated user data.

## Scope

The initial package is a runner bootstrap, not a Flutter release framework. Runtime has been exercised
in one private integration, but the extracted/configurable standalone package needs its own acceptance.
Do not turn prior integration results into an “audited”, “production-ready” or multi-machine claim.

The deferred checks do not block this explicitly early source publication. Keep the README's
limitations visible until independently verified. Generate archives from the explicit source manifest;
never archive a configured checkout or CI home wholesale.
