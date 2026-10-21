#!/usr/bin/env python3
"""Opt-in shared-host lease and CI-owned rebuildable cache maintenance.

No Docker prune, SDK/Archives deletion, process killing or runner reconfiguration.
All heavy jobs AND interactive builds must cooperate with the same host lease.
"""
import argparse
from contextlib import contextmanager
import fcntl
import grp
import json
import math
import os
from pathlib import Path
import platform
import pwd
import re
import shutil
import signal
import stat
import subprocess
import sys
import time

HOST_ROOT = Path('/Library/Application Support/Local Actions/Host Tools')
HOST_LOCK = HOST_ROOT / 'workload.lock'
GIB = 1024 ** 3


def no_symlinks(path):
    path = Path(path).absolute()
    for component in (path, *path.parents):
        if component.is_symlink():
            raise ValueError('Symlinked paths are not allowed')
    return path


@contextmanager
def lease(path, wait_seconds=0):
    path = no_symlinks(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid not in (0, os.getuid())
                or info.st_mode & 0o022):
            raise ValueError('Host lease must be a protected regular file')
        deadline = time.monotonic() + wait_seconds
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise BlockingIOError('Host lease is busy; no workload or cleanup started')
                time.sleep(min(0.25, max(0, deadline - time.monotonic())))
        yield
    finally:
        os.close(fd)


def active_builds():
    # Do not print arbitrary process arguments: they can contain credentials.
    output = subprocess.check_output(
        ['/bin/ps', '-axo', 'pid=,ppid=,comm='], text=True, timeout=10)
    rows = [line.split(None, 2) for line in output.splitlines()]
    parents = {int(p): int(pp) for p, pp, *_ in rows if p.isdigit() and pp.isdigit()}
    ancestors = {os.getpid()}
    parent = os.getppid()
    while parent > 0 and parent not in ancestors:
        ancestors.add(parent)
        parent = parents.get(parent, 0)
    found = []
    for row in rows:
        if len(row) != 3:
            continue
        pid, _, command = row
        if int(pid) in ancestors:
            continue
        name = Path(command).name
        busy = name in {'Runner.Worker', 'xcodebuild', 'swift-frontend', 'gradle', 'gradlew'}
        if name in {'java', 'dart', 'dartvm', 'docker', 'docker-buildx', 'node'}:
            try:
                args = subprocess.check_output(
                    ['/bin/ps', '-p', pid, '-o', 'args='], text=True, timeout=5).strip()
            except subprocess.CalledProcessError:
                # A short-lived tool may exit between the process snapshot and
                # this query. Ignore only a confirmed vanished PID; permission
                # or inspection failures for a live process still fail closed.
                try:
                    os.kill(int(pid), 0)
                except ProcessLookupError:
                    continue
                raise
            busy = bool(re.search(
                r'GradleMain|GradleWorkerMain|flutter_tools.*\b(build|test|run)\b|'
                r'\bdocker(?:-buildx)?\s+(?:build|buildx\s+build)\b|'
                r'[/\s]jest(?:[/\s.]|$)|[/\s]tsc(?:[/\s.]|$)|'
                r'vite(?:\.js)?\s+build|npm-cli\.js\s+(?:ci|install|run\s+(?:build|test))', args))
        if busy:
            found.append({'pid': int(pid), 'tool': name})
    return found


def report(home, minimum):
    value = {'free_gib': round(shutil.disk_usage(home).free / GIB, 2),
             'minimum_free_gib': minimum}
    if platform.system() == 'Darwin':
        for key, arguments in (
            ('ram_bytes', ['/usr/sbin/sysctl', '-n', 'hw.memsize']),
            ('swap', ['/usr/sbin/sysctl', '-n', 'vm.swapusage']),
        ):
            value[key] = subprocess.check_output(arguments, text=True, timeout=10).strip()
    return value


def require_cache_account(home):
    user = pwd.getpwuid(os.getuid())
    if (os.getuid() == 0 or Path(user.pw_dir) != home
            or home.parent != Path('/Users') or
            'admin' in {grp.getgrgid(g).gr_name for g in os.getgroups()}):
        raise ValueError('Cache deletion requires the dedicated non-admin CI account')


def cache_roots(home, gradle_homes):
    home = no_symlinks(home).resolve(strict=True)
    roots = [home / 'Library/Developer/Xcode/DerivedData']
    # Never include Xcode Archives, signing state, SDKs, dependency downloads,
    # runner registration, source checkouts, Docker volumes or recovery backups.
    for gradle in [home / '.gradle', *gradle_homes]:
        gradle = no_symlinks(gradle)
        if not gradle.is_relative_to(home):
            raise ValueError('Gradle home must stay inside the CI account home')
        caches = gradle / 'caches'
        if caches.is_dir():
            roots.extend(caches.glob('build-cache-*'))
            roots.extend(caches.glob('transforms-*'))
            roots.extend(p / 'transforms' for p in caches.iterdir()
                         if p.is_dir() and re.fullmatch(r'\d+(?:\.\d+)*', p.name))
    return sorted({no_symlinks(p) for p in roots if p.exists()})


def candidate_info(path, uid):
    """Fail closed on foreign ownership, links or mounts; count physical blocks."""
    initial = path.lstat()
    if initial.st_uid != uid or not (stat.S_ISREG(initial.st_mode) or stat.S_ISDIR(initial.st_mode)):
        raise ValueError('Unsafe cache entry')
    newest, size = initial.st_mtime, 0
    for current, directories, files in os.walk(path, followlinks=False):
        for p in [Path(current), *(Path(current) / n for n in files)]:
            info = p.lstat()
            if (info.st_uid != uid or info.st_dev != initial.st_dev
                    or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))):
                raise ValueError('Unsafe cache contents')
            newest = max(newest, info.st_mtime)
            size += info.st_blocks * 512
        for name in directories:
            no_symlinks(Path(current) / name)
    if path.is_file():
        if initial.st_uid != uid or not stat.S_ISREG(initial.st_mode):
            raise ValueError('Unsafe cache file')
        size = initial.st_blocks * 512
    return newest, size, (initial.st_dev, initial.st_ino)


def clean(home, gradle_homes, *, apply, minimum, target, workload=False):
    before = report(home, minimum)
    result = dict(before, status='ok', removed=[], skipped=[])
    builds = active_builds()
    # Under a workload lease, other cooperating GitHub workers may merely be
    # waiting for this lease. Their actual compilers are still detected below.
    if workload:
        builds = [b for b in builds if b['tool'] != 'Runner.Worker']
    if builds:
        return dict(result, status='busy', active_builds=builds)
    candidates = []
    for root in cache_roots(home, gradle_homes):
        # A compiler daemon with an open cache is also a reason not to delete it.
        opened = subprocess.run(['/usr/sbin/lsof', '-t', '+D', str(root)],
                                capture_output=True, text=True, timeout=30)
        if opened.returncode not in (0, 1) or opened.stdout.strip():
            result['skipped'].append({'cache': str(root), 'reason': 'open-or-unverifiable'})
            continue
        for entry in root.iterdir():
            try:
                newest, size, identity = candidate_info(entry, os.getuid())
                candidates.append((newest, str(entry), size, identity))
            except (ValueError, OSError):
                result['skipped'].append({'cache': str(entry), 'reason': 'unsafe'})
    cutoff = time.time() - 24 * 60 * 60
    for newest, name, size, identity in sorted(candidates):
        # Under pressure, TTL alone is insufficient: today's unused build cache
        # can exhaust the disk after a single run. Oldest unused entries go first.
        if newest >= cutoff and shutil.disk_usage(home).free / GIB >= target:
            continue
        entry = no_symlinks(name)
        info = entry.lstat()
        if (info.st_dev, info.st_ino) != identity:
            raise ValueError('Cache changed during maintenance')
        if apply:
            builds = active_builds()
            if workload:
                builds = [b for b in builds if b['tool'] != 'Runner.Worker']
            if builds:
                return dict(result, status='busy', free_gib=report(home, minimum)['free_gib'])
            candidate_info(entry, os.getuid())
            if entry.is_dir():
                if not shutil.rmtree.avoids_symlink_attacks:
                    raise RuntimeError('This Python cannot safely remove directory trees')
                shutil.rmtree(entry)
            else:
                entry.unlink()
        result['removed'].append({'cache': name, 'allocated_bytes': size, 'applied': apply})
    result.update(report(home, minimum))
    result['reclaimed_gib'] = round(result['free_gib'] - before['free_gib'], 2)
    if result['free_gib'] < minimum:
        result['status'] = 'below-minimum'
    elif result['free_gib'] < target:
        result['status'] = 'below-target'
    return result


def run_command(command):
    """Keep the lease until the child process group is reaped on cancellation."""
    child = subprocess.Popen(command, start_new_session=True)
    previous = {}
    cancelled = None
    cancel_deadline = None

    def stop(signum, _frame):
        nonlocal cancelled, cancel_deadline
        if cancelled is None:
            cancelled = signum
            cancel_deadline = time.monotonic() + 10
        try:
            os.killpg(child.pid, signum)
        except ProcessLookupError:
            pass

    try:
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous[signum] = signal.signal(signum, stop)
        while True:
            try:
                status = child.wait(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                if cancel_deadline is not None and time.monotonic() >= cancel_deadline:
                    os.killpg(child.pid, signal.SIGKILL)
                    status = child.wait()
                    break
        return 128 + cancelled if cancelled else status if status >= 0 else 128 - status
    finally:
        if child.poll() is None:
            stop(signal.SIGTERM, None)
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('report', 'clean', 'run'))
    parser.add_argument('--lock', type=Path, default=HOST_LOCK)
    parser.add_argument('--minimum-free-gib', type=float, default=40)
    parser.add_argument('--target-free-gib', type=float, default=55)
    parser.add_argument('--gradle-home', type=Path, action='append', default=[])
    parser.add_argument('--apply', action='store_true', help='Explicitly delete unused rebuildable cache')
    parser.add_argument('--clean-first', action='store_true', help='Explicitly clean under the job lease')
    parser.add_argument('--wait-seconds', type=float, default=0)
    # parse_known_args keeps flags before -- in options rather than REMAINDER.
    arguments = list(sys.argv[1:] if argv is None else argv)
    separator = arguments.index('--') if '--' in arguments else len(arguments)
    args = parser.parse_args(arguments[:separator])
    command = arguments[separator + 1:]
    if (not 0 < args.minimum_free_gib <= args.target_free_gib
            or not 0 <= args.wait_seconds <= 3600 or
            any(not math.isfinite(v) for v in
                (args.minimum_free_gib, args.target_free_gib, args.wait_seconds))):
        parser.error('Require finite 0 < minimum <= target and 0 <= wait <= 3600')
    if bool(command) != (args.action == 'run'):
        parser.error('Only run requires a command after --')
    if (args.apply and args.action != 'clean') or (args.clean_first and args.action != 'run'):
        parser.error('--apply is for clean; --clean-first is for run')
    home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    if args.action == 'report':
        print(json.dumps(report(home, args.minimum_free_gib)))
        return 0
    if args.apply or args.clean_first:
        require_cache_account(home)
    with lease(args.lock, args.wait_seconds):
        if args.action == 'clean' or args.clean_first:
            result = clean(home, args.gradle_home, apply=args.apply or args.clean_first,
                           minimum=args.minimum_free_gib, target=args.target_free_gib,
                           workload=args.action == 'run')
            print(json.dumps(result), flush=True)
            if result['status'] == 'busy':
                return 75
            if result['status'] == 'below-minimum':
                return 1
        if args.action == 'run':
            if report(home, args.minimum_free_gib)['free_gib'] < args.minimum_free_gib:
                raise RuntimeError('Insufficient free disk; no workload started')
            return run_command(command)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except BlockingIOError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(75)
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f'Host tools: {error}', file=sys.stderr)
        raise SystemExit(1)
