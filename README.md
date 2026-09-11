# macos-actions-runner-kit

[Русский](README.ru.md) · [Workflow integration](docs/INTEGRATION.md) · [Security](SECURITY.md)

Run GitHub Actions on your Mac **when you need it**, under a dedicated standard account.
Keep GitHub's logs and job interface. Stop the foreground runner with **Ctrl+C**.
No background service, personal access token manager or automatic workflow dispatch.

> [!IMPORTANT]
> This helper is intended for **trusted private repositories**. A separate account is
> **not a VM or sandbox**. Do not run public/unreviewed PRs on a personal Mac.

> [!NOTE]
> Early-stage open source under [MIT](LICENSE). Independent setup on a second Mac is
> deferred; Intel and a full signed release are not verified for this standalone kit.
> See [verification](docs/VERIFICATION.md) and [release readiness](docs/RELEASE-CHECKLIST.md).

## Any project stack

Use a **project requirements file**: arbitrary CLI tools, version constraints and CI-local
search paths. Enable Docker/SDK integration checks independently. Presets are optional
shortcuts, not an allowed-stack list; builds and dependency installation stay in workflows.
[Contract, examples and migration](docs/WORKLOADS.md).

The quickstart below uses the mobile shortcut; substitute your requirements file instead.

## 1. Download and open the folder

On this repository's GitHub page: **Code → Download ZIP**, then extract it.
Open Terminal under your usual administrator account:

```bash
cd "$HOME/Downloads/macos-actions-runner-kit-main"
ls runner tool/runner/config.example.json
```

If you extracted elsewhere, use that actual folder. Stop if a command fails.

## 2. Choose your private repository

Replace `OWNER/REPO` with the repository **whose jobs you want to run**, not this helper's public repository:

```bash
bash runner configure --repository OWNER/REPO --profile mobile
```

This creates an ignored, non-secret `tool/runner/config.json`. Nothing contacts GitHub or requests sudo.
For one platform, use `--profile android` or `--profile ios`; use `--profile generic` without mobile SDKs.

Alternative: use your reviewed project requirements file instead of `--profile mobile`:

```bash
bash runner configure --repository OWNER/REPO --requirements /absolute/ci-requirements.json
```

## 3. Prepare the account

```bash
bash runner setup
```

Enter your Mac password at sudo. If the `ci` account is new, choose a separate password for it.
The installer prepares the account and `ci-runner`; it copies an Android SDK only when selected.

> [!NOTE]
> Start with a developer Mac: Python 3.12+, `git`, `curl`, `jq`, `gh`;
> Ruby 3.3 for mobile profiles; Xcode 26.3 for iOS; full Android SDK and JDK for Android.
> Keep **20 GiB free**, plus space for an SDK copy when Android is selected. The installer reports missing tools;
> it does not install Xcode, accept Apple licences or make your account an administrator.
> For iOS, also install **Platform Support** in Xcode → Settings → Components.
> An installed Simulator or reported SDK version is not enough: `doctor` resolves a
> real generic iOS build destination using a disposable project without signing.

## 4. Connect — once

```bash
sudo -iu ci
```

**Wait for the `ci@…` prompt**, then run this separately:

```bash
ci-runner register
```

Open the displayed GitHub URL. Paste **only the registration token after `--token`**
into the hidden prompt. No token in a command, chat or config file. Ask the repository
administrator for a registration token if you cannot access its runner settings.
No separate macOS desktop login or personal Apple ID is needed.

## 5. Run when needed

Under `ci` (in a new terminal, run `sudo -iu ci` first):

```bash
ci-runner start
```

Wait for **Listening for Jobs**. Start your prepared GitHub workflow manually with
`runner=self-hosted`. [The repository maintainer must integrate a workflow first](docs/INTEGRATION.md).
Keep the terminal/lid open and the Mac on power. After **all workflow jobs** finish, press **Ctrl+C**.
Enter `exit` to return to your account. Registration is kept for next time.

> [!TIP]
> Check prerequisites: `ci-runner doctor`. Russian: `ci-runner --lang ru doctor`.
> To update, stop the runner and repeat setup from reviewed new source. See [operations](tool/runner/OPERATIONS.md).

This kit prepares a runner, **not a universal signed Flutter release pipeline**.
Project build commands, signing, credential cleanup and delivery remain the workflow owner's responsibility.

For backend, select `--profile backend` and follow the [Node workflow example](examples/manual-node-ci.yml).
Legacy mobile configs/commands remain supported. New profiles do not enable automatic PR jobs
or install a Docker daemon. Update an installed kit only after all jobs have stopped.
