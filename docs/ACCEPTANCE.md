# Runner kit acceptance matrix

Passing unit tests is necessary but not the same as proving that a persistent Mac can build,
sign and deliver a real project. Use the levels below in order and retain non-secret evidence for
each supported machine/profile. Never copy tokens, signing material, private logs or client data
into this public repository.

## Level 0 — portable source gate

Run from a clean checkout:

```bash
bash scripts/acceptance.sh
```

This runs the complete offline suite, builds the allowlisted archive, extracts it without Git or
runtime configuration, reruns the suite, rebuilds the archive and requires byte-for-byte equality.
It prints OS/architecture and the final archive SHA-256. It performs no sudo, account creation,
registration, Docker start, signing or delivery.

## Level 1 — administrator host preflight

From the reviewed source and final project config, as the Mac administrator:

```bash
bash runner --config /absolute/path/config.json plan --host
bash runner --config /absolute/path/config.json doctor --host --verbose
```

Resolve every plan step without weakening account/socket permissions. Record macOS build, CPU,
config schema/capabilities (no repository secrets), exact Xcode/JDK/runtime versions and result.

## Level 2 — dedicated CI-account acceptance

After setup, switch accounts and use the installed root-owned wrapper:

```bash
sudo -iu CI_USER
ci-runner env
ci-runner plan
ci-runner doctor --verbose
```

For Docker profiles, prove the context belongs to this account, then run one disposable container
and project-specific cleanup checks. For iOS, doctor must select the exact bundle and real iOS
destination. For Android, prove managed Java/SDK/emulator behavior. Stop here if a recovery journal
exists. A green doctor does not validate project dependencies, signing or delivery.

## Level 3 — trusted private workflow

Register only to the intended private repository and run the manual smoke workflow on a reviewed
branch. Then run the real project gate from a clean checkout: locked dependency install, lint,
tests, build, architecture checks and failure/cancellation cleanup. Confirm the listener stops
cleanly and another start/run succeeds without re-registration. Repeat setup while stopped and
confirm registration is preserved.

## Level 4 — project release recovery

Where the project signs or delivers mobile binaries, separately prove:

- exact signing identity/profile/application identifiers;
- pinned WWDR chain in the temporary job keychain and successful restoration after cleanup;
- APK/IPA signature verification before checkpointing;
- local checkpoint recovery after simulated Actions artifact-upload failure;
- durable pre-delivery checkpoint and exact SHA-256 at every destination;
- failure after the first destination does not force rebuild or publish a different binary;
- explicit retained-artifact cleanup after confirmed delivery.

Use synthetic/test destinations first. Do not infer production readiness from a DEV delivery.

## Level 5 — independent Mac and support claims

A second operator follows only published documentation on a different Mac. Test a fresh disposable
CI account, interrupted setup recovery, repeated setup, registration reuse, stop/start and a real
private workflow. Apple Silicon and Intel require separate evidence before both are advertised as
verified. Record failures as documentation/product bugs; undocumented rescue commands fail this level.

## Evidence table template

| Date | Revision/archive SHA | Mac/CPU | Profile | Levels | Result/evidence link | Gaps |
|---|---|---|---|---|---|---|
| YYYY-MM-DD | commit + SHA-256 | macOS build / arm64 or x86_64 | backend/mobile/etc. | 0–N | private run/report | explicit pending items |

Current public status remains early/prerelease: Level 0 has local coverage and a historical hosted
baseline, while the final revision's hosted gate and a fresh independent second-Mac Level 5 run are
still pending. Do not describe the kit as audited, universally production-ready or independently
verified until the release checklist and evidence say so.
