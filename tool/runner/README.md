# Embedded runner kit / Встраиваемый комплект

Copy this whole directory into your private project's `tool/runner`.
Do not copy `config.json`, `language`, `__pycache__`, runner binaries or credentials from another computer.

From the **project root**, after replacing `OWNER/REPO` with your real private repository:

```bash
bash tool/runner/runner configure --repository OWNER/REPO
bash tool/runner/runner setup
```

Русский интерфейс: добавь `--lang ru` к обеим командам. Только Android: `configure --platforms android`.

Switch account in a separate command:

```bash
sudo -iu ci
```

Wait for `ci@…`, then:

```bash
ci-runner register
ci-runner start
```

Paste the token into the hidden prompt, never into a shell command. Run your prepared workflow
with matching labels `[self-hosted, macOS, local-macos]`. Keep the Mac awake/on power;
after all jobs finish, press Ctrl+C. The kit never dispatches a workflow itself.

Only trusted private repositories; a standard account is not a VM sandbox.
Project signing/cleanup is not installed by this kit. See [operations](OPERATIONS.md).

Choose workload requirements with `--profile generic|node|backend|android|ios|mobile`.
See [profiles](../../docs/WORKLOADS.md); existing mobile commands remain compatible.
