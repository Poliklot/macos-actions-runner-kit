# Interactive runner experience

The installed CLI uses a checksum-pinned, bundled [Questionary](https://questionary.readthedocs.io/)
interface. It needs no `pip install` or network access at runtime. If the verified UI bundle is
missing or damaged, the operational CLI falls back to plain text/numeric input rather than importing
unverified code.

## Configure

In a terminal, `bash runner configure` opens a guided flow for repository, workload profile, label
and dedicated macOS account. All existing flags remain available for reproducible/non-interactive
configuration. Redirected input never triggers a prompt and requires `--repository` explicitly.

## Check and start

`ci-runner start` first renders one readable readiness screen. If checks fail in an interactive
terminal, the user chooses:

1. apply every currently available safe automatic repair;
2. show the complete ordered manual plan and exit;
3. rerun checks after fixing something in another terminal;
4. cancel startup.

After an automatic action the complete doctor runs again. The listener starts only after every
required check passes. Ctrl+C/Cancel returns code 130. JSON commands never contain colors/prompts.

Automation is an allowlist, not a shell-hook mechanism. The initial automatic action can start the
CI account's installed Colima runtime and select its local Docker context. It never uses sudo,
installs a package, contacts a rejected remote Docker endpoint or deletes data. Xcode installation,
Apple licences, private signing material, project source changes and external quotas remain manual.

For explicit behavior:

```bash
ci-runner start --repair ask   # default in a terminal
ci-runner start --repair auto  # apply allowlisted repairs; no questions
ci-runner start --repair never # diagnosis only
```

Non-interactive `ask` behaves like `never`, so automation cannot hang waiting for input. `auto`
still applies only allowlisted CI-owned actions and fails with the remaining plan when a manual or
administrator step is required.

## Other commands

`doctor`, `plan`, `env`, `profiles`, registration and lifecycle messages share the same semantic
visual language:

- `✓` ready/success;
- `!` attention or operator action;
- `✗` blocking failure;
- `◆` a new section.

Colors and arrow-key selection are used only on a real terminal. Plain redirected output preserves
the symbols and all content. `doctor --json`, `plan --json` and `env --json` remain stable automation
interfaces independent of the visual renderer.
