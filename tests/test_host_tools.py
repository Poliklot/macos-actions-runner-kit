"""Offline host safety regressions: no real accounts, services, caches or builds."""
from contextlib import nullcontext
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'examples/scripts' / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


host = load('ci_host', 'ci-host.py')
installer = load('install_host', 'install-ci-maintenance.py')


class LeaseTests(unittest.TestCase):
    def test_exclusive_across_processes_and_released_after_error(self):
        with tempfile.TemporaryDirectory() as directory:
            lock = Path(directory).resolve() / 'lock'
            lock.touch(mode=0o600)
            with host.lease(lock):
                child = subprocess.run([sys.executable, '-c',
                    'import fcntl,sys; f=open(sys.argv[1]); '
                    'fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)', str(lock)], capture_output=True)
                self.assertNotEqual(child.returncode, 0)
            with self.assertRaises(RuntimeError):
                with host.lease(lock):
                    raise RuntimeError('fixture')
            with host.lease(lock):
                pass

    def test_symlink_and_writable_shared_lock_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            lock = Path(directory).resolve() / 'lock'
            lock.touch(mode=0o600)
            alias = Path(directory).resolve() / 'alias'
            alias.symlink_to(lock)
            with self.assertRaises(ValueError), host.lease(alias):
                pass
            lock.chmod(0o666)
            with self.assertRaises(ValueError), host.lease(lock):
                pass

    def test_signal_does_not_release_lease_with_surviving_child(self):
        with tempfile.TemporaryDirectory() as directory:
            lock = Path(directory).resolve() / 'lock'
            lock.touch(mode=0o600)
            marker = Path(directory).resolve() / 'started'
            code = ('import time,pathlib,signal; '
                    'signal.signal(signal.SIGTERM, lambda *a: exit(0)); '
                    f'pathlib.Path({str(marker)!r}).write_text("ready"); time.sleep(60)')
            wrapper = (f'import sys;sys.path.insert(0,{str(ROOT / "examples/scripts")!r});'
                       'import importlib.util;'
                       f's=importlib.util.spec_from_file_location("h",{str(ROOT / "examples/scripts/ci-host.py")!r});'
                       'h=importlib.util.module_from_spec(s);s.loader.exec_module(h);'
                       f'\nwith h.lease({str(lock)!r}):\n sys.exit(h.run_command([{sys.executable!r},"-c",{code!r}]))')
            process = subprocess.Popen([sys.executable, '-c', wrapper])
            try:
                for _ in range(100):
                    if marker.exists():
                        break
                    time.sleep(0.05)
                self.assertTrue(marker.exists())
                process.send_signal(signal.SIGTERM)
                self.assertEqual(process.wait(timeout=15), 143)
                with host.lease(lock):
                    pass
            finally:
                if process.poll() is None:
                    process.kill(); process.wait()

    def test_orphaned_same_group_compiler_is_stopped_before_lease_release(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory).resolve()
            lock = directory / 'lock'
            lock.touch(mode=0o600)
            marker = directory / 'compiler'
            compiler = ('import os,signal,time,pathlib; '
                        'signal.signal(signal.SIGTERM, signal.SIG_IGN); '
                        f'pathlib.Path({str(marker)!r}).write_text(str(os.getpid())); time.sleep(60)')
            leader = ('import subprocess,sys,signal,time; '
                      'signal.signal(signal.SIGTERM, lambda *a: exit(0)); '
                      f'subprocess.Popen([sys.executable,"-c",{compiler!r}]); time.sleep(60)')
            wrapper = ('import importlib.util,sys; '
                       f's=importlib.util.spec_from_file_location("h",{str(ROOT / "examples/scripts/ci-host.py")!r}); '
                       'h=importlib.util.module_from_spec(s);s.loader.exec_module(h);'
                       f'\nwith h.lease({str(lock)!r}):\n sys.exit(h.run_command([{sys.executable!r},"-c",{leader!r}]))')
            process = subprocess.Popen([sys.executable, '-c', wrapper])
            compiler_pid = None
            try:
                for _ in range(100):
                    if marker.exists():
                        compiler_pid = int(marker.read_text()); break
                    time.sleep(0.05)
                self.assertIsNotNone(compiler_pid)
                process.send_signal(signal.SIGTERM)
                self.assertEqual(process.wait(timeout=15), 143)
                for _ in range(100):
                    state = subprocess.run(['ps', '-p', str(compiler_pid), '-o', 'stat='],
                                           capture_output=True, text=True)
                    if state.returncode or state.stdout.strip().startswith('Z'):
                        break
                    time.sleep(0.05)
                else:
                    self.fail('Compiler survived the workload lease')
                with host.lease(lock):
                    pass
            finally:
                if process.poll() is None:
                    process.kill(); process.wait()
                if compiler_pid:
                    try:
                        os.kill(compiler_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass


class ProcessTests(unittest.TestCase):
    def test_vanished_process_does_not_block_maintenance_but_live_failure_does(self):
        outputs = ['999999 1 /usr/bin/node\n', '']
        with patch.object(host.subprocess, 'check_output', side_effect=outputs), \
                patch.object(host.os, 'kill', side_effect=ProcessLookupError):
            self.assertEqual(host.active_builds(), [])
        with patch.object(host.subprocess, 'check_output', side_effect=outputs), \
                patch.object(host.os, 'kill', return_value=None):
            with self.assertRaises(RuntimeError):
                host.active_builds()

    def test_process_arguments_are_queried_once_and_never_reported(self):
        rows = '999991 1 /usr/bin/node\n999992 1 /usr/bin/java\n999993 1 /usr/bin/dartvm\n'
        args = '999991 node /fixture/jest --token=private-fixture\n999992 java GradleWorkerMain\n999993 dartvm flutter_tools.snapshot run\n'
        with patch.object(host.subprocess, 'check_output', side_effect=[rows, args]) as query:
            builds = host.active_builds()
        self.assertEqual(query.call_count, 2)
        self.assertEqual([b['tool'] for b in builds], ['node', 'java', 'dartvm'])
        self.assertNotIn('private-fixture', repr(builds))


class CacheTests(unittest.TestCase):
    def make_cache(self, home):
        gradle = home / 'gradle'
        root = gradle / 'caches/8.14/transforms'
        for name in ('older', 'newer'):
            entry = root / name
            entry.mkdir(parents=True)
            (entry / 'rebuildable.bin').write_bytes(b'fixture')
        os.utime(root / 'older/rebuildable.bin', (1, 1))
        os.utime(root / 'older', (1, 1))
        return gradle, root

    def clean(self, home, gradle, **args):
        with patch.object(host, 'active_builds', return_value=[]), patch.object(
                host.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, '', '')):
            return host.clean(home, [gradle], minimum=40, target=55, **args)

    def test_dry_run_does_not_delete_and_pressure_can_reclaim_today_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            gradle, root = self.make_cache(home)
            # No dependency on this machine's actual free disk or destructive sudo.
            usage = type('Usage', (), {'free': 30 * host.GIB})()
            with patch.object(host.shutil, 'disk_usage', return_value=usage):
                result = self.clean(home, gradle, apply=False)
                self.assertEqual(result['status'], 'below-minimum')
                self.assertTrue((root / 'newer').exists())
                result = self.clean(home, gradle, apply=True)
                self.assertFalse((root / 'older').exists())
                self.assertFalse((root / 'newer').exists())
                self.assertEqual(result['status'], 'below-minimum')

    def test_above_target_only_expired_entries_are_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            gradle, root = self.make_cache(home)
            with patch.object(host.shutil, 'disk_usage', return_value=type('Usage', (), {'free': 60 * host.GIB})()):
                self.clean(home, gradle, apply=True)
            self.assertFalse((root / 'older').exists())
            self.assertTrue((root / 'newer').exists())

    def test_active_build_means_no_cache_discovery_or_deletion(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(host, 'active_builds',
                return_value=[{'pid': 123, 'tool': 'xcodebuild'}]), patch.object(host, 'cache_roots') as roots:
            result = host.clean(Path(directory).resolve(), [], apply=True, minimum=40, target=55)
            self.assertEqual(result['status'], 'busy')
            roots.assert_not_called()

    def test_open_cache_and_unknown_lsof_results_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            gradle, root = self.make_cache(home)
            for status, output in ((0, '123\n'), (2, '')):
                with patch.object(host, 'active_builds', return_value=[]), patch.object(
                        host.subprocess, 'run', return_value=subprocess.CompletedProcess([], status, output, '')):
                    result = host.clean(home, [gradle], apply=True, minimum=40, target=55)
                    self.assertTrue(result['skipped'])
                    self.assertTrue((root / 'older').exists())

    def test_source_signing_backups_sdk_and_archives_are_outside_allowlist(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            for relative in ('source', 'Library/Developer/Xcode/Archives', 'Library/Backups',
                             'Library/Keychains', 'Library/Android/sdk', 'actions-runner'):
                p = home / relative
                p.mkdir(parents=True, exist_ok=True)
                (p / 'keep').write_text('fixture')
            self.assertEqual(host.cache_roots(home, []), [])
            with self.assertRaises(ValueError):
                host.cache_roots(home, [home.parent])

    def test_symlinked_cache_contents_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            entry = Path(directory).resolve() / 'entry'
            entry.mkdir()
            (entry / 'escape').symlink_to(Path(directory).resolve() / 'outside')
            with self.assertRaises(ValueError):
                host.candidate_info(entry, os.getuid())


class SchedulerTests(unittest.TestCase):
    def test_system_service_has_explicit_identity_python_and_protected_logs(self):
        account = type('Account', (), {'pw_dir': '/Users/ci_sample', 'pw_uid': 502})()
        with patch.object(installer.pwd, 'getpwnam', return_value=account):
            value = installer.service('ci_sample', Path('/opt/homebrew/bin/python3'),
                                      minimum=40, target=55, gradle_homes=['/Users/ci_sample/cache/gradle'])
        self.assertEqual(value['UserName'], 'ci_sample')
        self.assertEqual(value['StartInterval'], 3600)
        self.assertNotIn('KeepAlive', value)
        self.assertNotIn('/Users/', value['StandardOutPath'])
        self.assertEqual(value['ProgramArguments'][0], '/opt/homebrew/bin/python3')
        self.assertIn('--apply', value['ProgramArguments'])

    def test_invalid_budgets_and_accounts_refused_before_install(self):
        for user, minimum, target in (('../escape', 40, 55), ('ci', 55, 40), ('ci', float('nan'), 55)):
            with self.assertRaises(ValueError):
                installer.service(user, Path('/python'), minimum=minimum, target=target, gradle_homes=[])

    def test_idempotent_install_and_failed_update_restore_previous_service(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            root, logs, daemons = base / 'tools', base / 'logs', base / 'daemons'
            daemons.mkdir()
            args = SimpleNamespace(user='ci_sample', source_sha256=hashlib.sha256(
                (ROOT / 'examples/scripts/ci-host.py').read_bytes()).hexdigest(),
                minimum_free_gib=40, target_free_gib=55, gradle_home=[], replace=False)
            # Fake root installation, real unprivileged fixture files.
            account = SimpleNamespace(pw_dir='/Users/ci_sample', pw_uid=os.getuid(), pw_gid=os.getgid())
            state = {'loaded': False, 'bootstraps': 0, 'fail': False}

            def launch(command, **_kwargs):
                if 'print' in command:
                    return subprocess.CompletedProcess(command, 0 if state['loaded'] else 1)
                if 'bootout' in command:
                    state['loaded'] = False
                if 'bootstrap' in command:
                    state['bootstraps'] += 1
                    if state['fail']:
                        state['fail'] = False
                        raise subprocess.CalledProcessError(5, command)
                    state['loaded'] = True
                return subprocess.CompletedProcess(command, 0)

            with patch.object(installer, 'ROOT', root), patch.object(installer, 'LOGS', logs), \
                    patch.object(installer, 'LAUNCH_DAEMONS', daemons), \
                    patch.object(installer, 'protected'), \
                    patch.object(installer.platform, 'system', return_value='Darwin'), \
                    patch.object(installer.os, 'getuid', return_value=0), \
                    patch.object(installer.os, 'chown') as chown, \
                    patch.object(installer.pwd, 'getpwnam', return_value=account), \
                    patch.object(installer.subprocess, 'check_output', return_value='staff\n'), \
                    patch.object(installer.subprocess, 'run', side_effect=launch), \
                    patch.object(installer, 'load_host_tools', return_value=SimpleNamespace(lease=lambda _: nullcontext())):
                installer.install(args)
                installer.install(args)
                chown.assert_called_with(logs / 'ci_sample.log', account.pw_uid, account.pw_gid)
                self.assertEqual(state['bootstraps'], 1)
                paths = [root / 'ci-host.py', root / 'ci_sample.receipt.json',
                         daemons / 'org.local-actions.maintenance.ci_sample.plist']
                original = {p: p.read_bytes() for p in paths}
                args.target_free_gib, args.replace = 60, True
                state['fail'] = True
                with self.assertRaises(subprocess.CalledProcessError):
                    installer.install(args)
                self.assertTrue(state['loaded'])
                self.assertEqual(original, {p: p.read_bytes() for p in paths})


if __name__ == '__main__':
    unittest.main()
