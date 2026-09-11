# Integrating a private workload

The **helper's source can be public; the personal Mac's workload repository should be private**.
Do not register a personal Mac with the public helper repository. Its own CI uses only disposable GitHub-hosted runners.

## Smallest first test

1. Install/configure the kit for your private repository.
2. Copy `examples/manual-smoke.yml` to that repository's `.github/workflows/manual-mac-smoke.yml`.
3. Match `local-macos` to your configured label and review the allowed `main`/`develop` branches.
4. Publish the workflow to the default branch through your normal review process.
5. Start `ci-runner start` under the CI account. In Actions, choose **Manual Mac smoke → Run workflow → self-hosted**.
6. Verify the job names your Mac and standard CI account. Stop only after all jobs finish.

This executes a checkout and environment smoke check, **not an app build or signed release**.
The cloud option remains explicit/default; an offline local runner queues instead of falling back to a paid machine.

## Node/backend and other stacks

Select a [workload profile](WORKLOADS.md) before setup. For Node, adapt
[manual-node-ci.yml](../examples/manual-node-ci.yml): account, label, Node version,
locked installation and test/build commands must match the private project. For a backend,
provision Docker separately under the CI account and verify disposable PostgreSQL tests,
image architecture and cleanup. The kit supplies no application-specific deployment.

For Python/Go/Rust/infra, use `generic` plus `--require-tool` or selected capabilities;
start from the manual smoke example and add the project's reviewed commands. Exact
version installation is the workflow's responsibility, not a root-run config hook.
One CI account/registration per repository remains mandatory, even on the same Mac.

## Existing workflows (including Flutter)

- Add an explicit `runner` choice and route **every** required job, including gates and notifications.
- Allow local jobs only on trusted manual refs; do not route `pull_request` / `pull_request_target` to this Mac.
- Put the trust condition on the job **before it is scheduled**; a check after checkout is too late for routing safety.
- Keep pinned Flutter/toolchain, locked dependencies, static analysis, tests, native compile and launch gates.
- Treat the Mac as persistent: never copy hosted-machine `sudo rm -rf` cleanup into local jobs.
- Isolate dependency caches. Cache downloads, not an unauthenticated “checks passed” file.
- Use scoped environment secrets only when required, no persistent personal PAT or Apple ID.
- Design signing rollback/cleanup for cancellation and failure; account for no default keychain.
- Test Android/iOS signing and external delivery separately. The kit supplies none of these project-specific contracts.

Only people/code you trust should have access to a runner-enabled repository. A label is routing,
not authorization; someone able to change workflows may bypass your example's safeguards.
Standard users can still reach the network and read files their permissions allow.

## Vendoring the helper

Copy the complete `tool/runner` source directory, excluding local config/language/cache files.
Include this helper's `LICENSE` with the vendored source, preserving the copyright and permission notice.
The embedded entry point is `bash tool/runner/runner`, not the standalone root `bash runner`.
Developers can run `configure --repository OWNER/REPO --requirements /absolute/ci-requirements.json`
with the project's reviewed portable manifest. Keep machine identity in local config, not the manifest. Never include a token/password in it.
Document the actual clone URL, branch, directory, CI user and workflow fields for your team.

Apply upstream source updates through a reviewed diff, then run tests and update installed kits
with setup. Do not introduce a mutable `curl | sudo bash` installation shortcut.
