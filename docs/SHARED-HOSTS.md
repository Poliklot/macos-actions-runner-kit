# Shared Mac resource safety and headless maintenance

[Русский](#русский) · [Operations](../tool/runner/OPERATIONS.md)

A dedicated CI account isolates credentials and ownership, not RAM, CPU or disk.
An online runner is a connected listener, not proof that a build is running or can
pass. Keep the normal on-demand kit unless you explicitly need scheduled cache
maintenance. The optional recipes below never install or restart a runner service.

## Diagnose before deleting

Under the CI account, from the reviewed source folder:

```sh
python3 examples/scripts/ci-host.py report --minimum-free-gib 40
```

On macOS this reports free disk, physical RAM and swap without exposing process
arguments or environment secrets. Swap is disk used as an extension of RAM. A cool,
quiet Mac can still have substantial memory pressure. Swap usage is evidence of
pressure at some point, not proof that a particular test failed because of RAM.

Distinguish these independent failures:

| Evidence | Next step |
| --- | --- |
| Runner offline, no job assigned | Inspect the listener/supervisor, account and network |
| Free disk below the project's minimum | Inventory actual storage, preserve work, reclaim only unused rebuildable data |
| Docker build cache is already empty | Stop repeating Docker prune; inspect other users, SDKs, simulator data and swap |
| Dependency audit reports an advisory | Fix the locked dependency; retrying or deleting caches does not fix a vulnerability |
| Test child exits or is killed | Capture status, signal, timeout and bounded redacted output; reproduce the specific probe |

Two Docker VMs, a simulator, interactive builds, agents and CI share one physical
budget. Pause or hand over heavy work by agreement; never kill a developer's VM,
simulator, agents or apps to make CI green. Never delete swap files. Connecting a
mobile app to a staging API can remove the need for a local backend/database, but
does not eliminate local Flutter/Xcode builds or the simulator.

## A second gate failure is not necessarily the same incident

After restoring capacity, run the unchanged gate from the start. A strict audit
may reveal an independently vulnerable locked dependency. Check each package
manager's lockfile **and existing nested resolutions/overrides**: updating a direct
range while leaving a vulnerable test-tool override pinned is incomplete. Prefer
an available patched release in the existing major, regenerate the lock with its
package manager, retain immutable installs, and re-run production and development
audits plus tests/build. Do not use blanket audit suppression, `audit fix --force`,
or a cache purge as a security fix. Keep resource repair, diagnostic changes and
each dependency remediation in separate reviewable commits.

## Prepare a shared workload lease

GitHub workflow concurrency groups are repository-scoped. Identical group names in
two repositories do not serialize workloads on one Mac. The native `flock` lease in
`examples/scripts/ci-host.py` works across repositories and CI accounts on that host.
The installer below creates its protected root-owned lock; commands open it read-only.

All heavy CI jobs **and interactive builds** must cooperate with this same lease.
Wrap the whole install/test/build command, not individual subcommands that overlap:

```sh
HOST='/Library/Application Support/Local Actions/Host Tools/ci-host.py'
python3 "$HOST" run --wait-seconds 600 --minimum-free-gib 40 -- bash scripts/quality.sh
```

The CI account can explicitly opt into `run --clean-first` to clean and check disk
under the same lease before starting the command. A developer's normal account
may use `run`, but cannot use destructive cleanup. Cancellation forwards the signal
to the owned child group and keeps the lease until the direct child has exited.
Remaining same-group compilers are stopped before releasing the lease, even when
the leader has exited. Detached groups remain the workload’s responsibility.
The helper never dispatches GitHub jobs and cannot control unwrapped commands.
Migrate all participating workflows before relying on workload serialization.

## Install optional hourly maintenance without a desktop login

First complete ordinary kit setup for an existing standard CI account. Review the
two recipes and their tests. Use a trusted Python **3.12+**; the system
`/usr/bin/python3` may be older. From the reviewed extracted source folder, as the
administrator:

```sh
/opt/homebrew/bin/python3 --version
shasum -a 256 examples/scripts/ci-host.py
# Compare the digest with the source you reviewed. A locally computed hash alone
# does not authenticate downloaded source.
sudo /opt/homebrew/bin/python3 examples/scripts/install-ci-maintenance.py \
  --user ci --source-sha256 REVIEWED_SCRIPT_SHA256 \
  --minimum-free-gib 40 --target-free-gib 55
```

If a workflow stores Gradle outside `~/.gradle`, append its actual CI-owned path:
`--gradle-home /Users/ci/actions-runner/_work/_tool/project/gradle`.
Repeat installation for each dedicated CI account with its own paths. Do not copy
the administrator's cache, Docker configuration, signing state or credentials.

The installer creates a **system LaunchDaemon with explicit UserName**, not a
LaunchAgent requiring `gui/UID`. It pins the selected Python path, checks the script
digest, takes the same host lease and rejects symlinks. The script and log directory
are root-owned; the existing `0600` log belongs to the CI identity so launchd can
open stdio after selecting UserName. A root-only log can cause `EX_CONFIG` before
Python starts. The CI account cannot replace the log path in its protected parent.
Repeating the same installation is idempotent. A changed
installation requires explicit `--replace`; failure restores its previous files and
service. Initial failures may leave empty protected directories and the lease file.

```sh
sudo launchctl print system/org.local-actions.maintenance.ci
sudo tail -n 5 '/Library/Logs/Local Actions/Maintenance/ci.log'
sudo cat '/Library/Application Support/Local Actions/Host Tools/ci.receipt.json'
```

Verify `StartInterval` is 3600, `UserName` is the CI account, and the loaded service
and log actually exist. A plist merely present in `~/Library/LaunchAgents` is not
evidence of a working schedule on a headless CI account. The service does not use
KeepAlive, restart loops, sudo inside CI, or personal desktop login.

## Cleanup boundaries and acceptance

`clean` is a dry run by default; `--apply` is explicit consent to delete unused
rebuildable cache. It only targets CI-owned Gradle transforms/build-cache entries and
Xcode DerivedData. It **never** deletes Xcode Archives, SDKs, source, dependency
downloads, logs, signing journals, recovery backups, runner registration or Docker
volumes. Extra Gradle paths must remain inside the selected CI home.

At or above the target, only entries unused for 24 hours expire. Below the target,
oldest unused rebuildable entries may be reclaimed even if created today. A TTL
alone cannot bound space consumed by one large build. Open, foreign-owned, mounted,
symlinked or unverifiable cache data is preserved. Active build detection and the
shared workload lease are complementary safeguards, not a sandbox for hostile jobs.

Each invocation emits one JSON result; `reclaimed_gib` measures actual free-space
change, not a promise based on logical file sizes (APFS clones can share blocks).

| Result | Meaning |
| --- | --- |
| Exit 0, `ok` | Minimum and target met |
| Exit 0, `below-target` | Minimum met, target reserve not achieved; inspect before accepting sustained operation |
| Exit 1, `below-minimum` | Still unsafe to launch; no success claim or threshold reduction |
| Exit 75, `busy` or lease busy | Preserve running work; retry after the workload releases the host |

40/55 GiB are examples for a substantial workload, not universal sizing guarantees.
Keep the minimum consistent with project doctor/workflow checks. If caches cannot
maintain the reserve, move workloads or add capacity by explicit agreement. Do not
silently erase recovery data or reduce the minimum. In a quiet agreed window, run
the failing probes first, then one complete CI; record source SHA, disk before/after,
RAM/swap and actual test outcomes. Offline tests do not prove live workload acceptance.

To roll back the schedule (not the runner), as administrator:

```sh
sudo launchctl bootout system/org.local-actions.maintenance.ci
sudo rm /Library/LaunchDaemons/org.local-actions.maintenance.ci.plist
```

Keep the shared script/lease while any other account or workflow still uses them.
Existing third-party LaunchAgents are not disabled by this installer; inspect and
remove obsolete maintenance schedules separately, without touching runner services.

## Diagnose bounded Node probes without hiding their failures

Jest may render a rejected `execFile` as just `Command failed`, hiding the child's
exit/signal/timeout metadata. Reuse [run-probe.cjs](../examples/scripts/run-probe.cjs)
with its [TypeScript declaration](../examples/scripts/run-probe.d.cts) in project
test support code. Both files are source recipes, not an npm dependency or a daemon.
Keep the originating commit and SHA-256 when vendoring them into a private project.

```js
const { runProbe } = require('./run-probe.cjs');
const result = await runProbe(['test/queue.probe.js'], {
  name: 'queue-contract', timeout: 14000, env: process.env,
});
// Keep the project's existing assertions and timeout budget.
if (result.stdout.trim() !== 'verified') throw new Error('Unexpected probe result');
```

Failure reports preserve exit code, signal, killed flag, configured timeout and
elapsed time, with bounded stdout/stderr tails. They do not print full argv or the
original error cause. Values of secret-bearing environment entries, database URL
passwords and URL userinfo are redacted before truncation. The helper cannot infer
arbitrary application-specific personal data: probes must use disposable test data
and must not log real accounts/documents. A buffer-limit failure is not a timeout.

This recipe runs a Node child directly, without a shell. `execFile` timeout controls
that child, not arbitrary detached grandchildren. Projects spawning process trees
must own their process-group cleanup and disposable resources separately. Keep DB
guards and cleanup in the outer test runner. Reproduce the exact failed probes in
a quiet window before changing deadlines; a resource snapshot alone is not proof
of the reason for a past failure.

## Русский

CI-пользователь отделяет права и секреты, но не выделяет отдельные RAM, CPU или диск.
`online` означает подключённый listener, а не одновременно выполняющуюся сборку.
Подкачка занимает диск; холодный и бесшумный Mac тоже может испытывать дефицит RAM.
Разделяйте нехватку места, сетевую очередь, ошибки security audit и падения тестов.

Общий рецепт opt-in: `ci-host.py report` показывает ресурсы; `run -- команда`
сериализует тяжёлую работу через общий файловый lock. Одинаковый GitHub concurrency
group в разных repositories этого не обеспечивает. Все тяжёлые CI и ручные сборки
должны использовать один lock; не отключайте чужие VM/симулятор/приложения.

После обычного setup администратор устанавливает `install-ci-maintenance.py` с
выбранным Python 3.12+, проверенным SHA-256, именем стандартного CI-пользователя и
его порогами диска. Команды установки и проверки приведены выше. Создаётся системный
LaunchDaemon с UserName: GUI-login, пароль пользователя и sudo внутри CI не нужны.
Наличие plist в LaunchAgents само по себе не подтверждает работающий таймер.

Очистка по умолчанию диагностическая; удаление требует `--apply` или явного
`run --clean-first`. Удаляются только неиспользуемые восстановимые Gradle caches и
Xcode DerivedData. Исходники, SDK, Xcode Archives, подписи, recovery-копии и Docker
volumes не удаляются. При нехватке места могут удаляться и сегодняшние неиспользуемые
cache entries: одного TTL недостаточно для контроля объёма. Активная работа,
чужие файлы, symlinks и открытые caches сохраняются. Ни нехватка места, ни занятый
host не выдаются за успех. Недостаточный резерв требует уменьшения нагрузки или
согласованного увеличения ёмкости, а не снижения порога.

Приёмка выполняется в согласованное окно: проблемные probes → один полный CI →
замеры до/после. Прохождение offline tests не равно живой приёмке. Откат таймера
не должен останавливать runner или удалять данные другого пользователя.

Для Node probes используйте `run-probe.cjs` и `run-probe.d.cts`: ошибка показывает
exit code, signal, признак killed, timeout и длительность, а также ограниченные
хвосты stdout/stderr с редактированием секретов. Исходные argv и error cause не
печатаются. Сохраняйте существующие assertions, DB guards и лимиты; не увеличивайте
таймауты до воспроизведения. Для произвольных дочерних деревьев проект отдельно
отвечает за process-group cleanup. Пробники должны работать только с одноразовыми
тестовыми данными: универсальный фильтр не распознаёт все виды персональных данных.
