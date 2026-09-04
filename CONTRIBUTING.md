# Contributing

Run `bash scripts/check.sh` with Python 3.12+. Tests must not create users, invoke privileged
setup, register real runners or dispatch workflows. macOS-specific read-only/framework and
isolated dummy-process checks are skipped on other hosts. No runtime Python packages are required.

- Keep both message languages and `%s` placeholders synchronized.
- Add regressions for every validation, permission, lifecycle or registration change.
- Preserve no-service/no-admin/no-secret-arguments defaults.
- Keep examples manual, private-repository-only and pinned to reviewed action SHAs.
- Do not add personal paths, project credentials, accounts or copied runtime directories.
- Review changes to the privileged installer separately and test on a disposable Mac/account before release.
- Update both quickstarts when commands change; do not hide required `cd` or account switches.

Proposed branches: `feat/`, `fix/`, `docs/`, `test/`, `ci/`, `chore/` plus a short work description.
Never bypass tests simply because the change is “only an installer”.
By submitting a contribution, you agree that it is provided under this project's [MIT license](LICENSE).
Only contribute code and documentation you have the right to share under those terms.
