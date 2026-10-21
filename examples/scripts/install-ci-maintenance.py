#!/usr/bin/env python3
"""Explicit administrator opt-in: headless maintenance, never a runner service."""
import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import platform
import plistlib
import pwd
import re
import stat
import subprocess
import sys
import tempfile

ROOT = Path('/Library/Application Support/Local Actions/Host Tools')
LOGS = Path('/Library/Logs/Local Actions/Maintenance')
LAUNCH_DAEMONS = Path('/Library/LaunchDaemons')


def service(user, python, *, minimum, target, gradle_homes):
    if not re.fullmatch(r'[a-z_][a-z0-9_-]{0,30}', user):
        raise ValueError('Invalid CI account name')
    if not (math.isfinite(minimum) and math.isfinite(target) and 0 < minimum <= target):
        raise ValueError('Require finite 0 < minimum <= target')
    account = pwd.getpwnam(user)
    home = Path(account.pw_dir)
    if home != Path('/Users') / user or account.pw_uid == 0:
        raise ValueError('Expected an existing standard CI account with its own home')
    label = f'org.local-actions.maintenance.{user}'
    args = [str(python), str(ROOT / 'ci-host.py'), 'clean', '--apply',
            '--minimum-free-gib', str(minimum), '--target-free-gib', str(target)]
    for gradle in gradle_homes:
        path = Path(gradle)
        if not path.is_absolute() or not path.is_relative_to(home) or '..' in path.parts:
            raise ValueError('Gradle home must stay inside the selected CI home')
        args.extend(['--gradle-home', str(path)])
    return {'Label': label, 'UserName': user, 'GroupName': 'staff',
            'ProgramArguments': args, 'WorkingDirectory': str(home),
            'RunAtLoad': True, 'StartInterval': 3600, 'ProcessType': 'Background',
            'Umask': 0o077,
            'StandardOutPath': str(LOGS / f'{user}.log'),
            'StandardErrorPath': str(LOGS / f'{user}.log'),
            'EnvironmentVariables': {'HOME': str(home), 'USER': user, 'LOGNAME': user,
                                    'PATH': '/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin'}}


def protected(path, *, directory=False, owners=(0,)):
    for component in (path, *path.parents):
        if component.is_symlink():
            raise ValueError('Refusing symlinked installation path')
    if path.exists():
        info = path.stat()
        kind = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
        if not kind or info.st_uid not in owners or info.st_mode & 0o022:
            raise ValueError('Installation paths must be root-owned and protected')


def atomic_write(path, data, mode):
    protected(path)
    fd, name = tempfile.mkstemp(prefix='.install-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
            os.fchmod(output.fileno(), mode)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def load_host_tools(source):
    spec = importlib.util.spec_from_file_location('reviewed_host_tools', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def install(args):
    if platform.system() != 'Darwin' or os.getuid() != 0:
        raise ValueError('Run this opt-in installer with sudo on macOS')
    source = Path(__file__).with_name('ci-host.py')
    if source.is_symlink() or not source.is_file():
        raise ValueError('Use the reviewed source folder, not a symlink')
    data = source.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != args.source_sha256:
        raise ValueError('Source SHA-256 does not match the reviewed script')
    python = Path(sys.executable).resolve(strict=True)
    if sys.version_info < (3, 12):
        raise ValueError('Python 3.12+ is required')
    definition = service(args.user, python, minimum=args.minimum_free_gib,
                         target=args.target_free_gib, gradle_homes=args.gradle_home)
    if 'admin' in subprocess.check_output(['/usr/bin/id', '-Gn', args.user], text=True).split():
        raise ValueError('The maintenance account must not be an administrator')
    protected(LAUNCH_DAEMONS, directory=True)
    for path in [ROOT.parent, ROOT, LOGS.parent, LOGS]:
        protected(path, directory=True)
        path.mkdir(mode=0o755, parents=True, exist_ok=True)
    lock = ROOT / 'workload.lock'
    protected(lock)
    if not lock.exists():
        atomic_write(lock, b'', 0o444)
    module = load_host_tools(source)
    target = ROOT / 'ci-host.py'
    plist = LAUNCH_DAEMONS / (definition['Label'] + '.plist')
    log = LOGS / f'{args.user}.log'
    account = pwd.getpwnam(args.user)
    protected(log, owners=(0, account.pw_uid))
    log_changed = (not log.exists() or log.stat().st_uid != account.pw_uid
                   or stat.S_IMODE(log.stat().st_mode) != 0o600)
    if not log.exists():
        atomic_write(log, b'', 0o600)
    # launchd opens stdio with the target identity. Protect the directory from
    # replacement/symlink attacks, but let that identity write its existing log.
    os.chown(log, account.pw_uid, account.pw_gid)
    os.chmod(log, 0o600)
    payload = plistlib.dumps(definition, sort_keys=True)
    for p, expected in ((target, data), (plist, payload)):
        protected(p)
        if p.exists() and p.read_bytes() != expected and not args.replace:
            raise ValueError('Existing installation differs; review it and explicitly use --replace')
    with module.lease(lock):
        receipt = ROOT / f'{args.user}.receipt.json'
        protected(receipt)
        originals = {p: (p.read_bytes(), stat.S_IMODE(p.stat().st_mode)) if p.exists() else None
                     for p in (target, plist, receipt)}
        changed = not plist.exists() or plist.read_bytes() != payload or log_changed
        loaded = subprocess.run(['/bin/launchctl', 'print', f'system/{definition["Label"]}'],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
        try:
            if loaded and changed:
                subprocess.run(['/bin/launchctl', 'bootout', f'system/{definition["Label"]}'], check=True)
            atomic_write(target, data, 0o644)
            atomic_write(plist, payload, 0o644)
            atomic_write(receipt,
                         (json.dumps({'source_sha256': digest, 'service': definition['Label'],
                                      'python': str(python)}, indent=2) + '\n').encode(), 0o644)
            if not loaded or changed:
                subprocess.run(['/bin/launchctl', 'bootstrap', 'system', str(plist)], check=True)
        except BaseException:
            # Restore only this maintenance service, never a runner/VM/account.
            if not loaded or changed:
                subprocess.run(['/bin/launchctl', 'bootout', f'system/{definition["Label"]}'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for path, previous in originals.items():
                if previous is None:
                    if path.exists():
                        path.unlink()
                else:
                    atomic_write(path, *previous)
            if loaded and changed:
                subprocess.run(['/bin/launchctl', 'bootstrap', 'system', str(plist)], check=True)
            raise
    print(json.dumps({'service': definition['Label'], 'source_sha256': digest,
                      'status': 'installed', 'runner_changed': False}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--user', required=True)
    parser.add_argument('--source-sha256', required=True)
    parser.add_argument('--minimum-free-gib', type=float, default=40)
    parser.add_argument('--target-free-gib', type=float, default=55)
    parser.add_argument('--gradle-home', action='append', default=[])
    parser.add_argument('--replace', action='store_true')
    install(parser.parse_args())


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, BlockingIOError, subprocess.SubprocessError) as error:
        print(f'Installation stopped: {error}', file=sys.stderr)
        raise SystemExit(1)
