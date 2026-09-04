"""Standalone configuration tests: real subprocesses, no accounts/network/sudo."""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tool/runner'))
import runner as kit


class ConfigureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.destination = Path(self.temp.name) / 'config.json'
        previous = kit.i18n.LANGUAGE
        self.addCleanup(setattr, kit.i18n, 'LANGUAGE', previous)

    def configure(self, *args):
        with patch.object(kit.os, 'getuid', return_value=501), redirect_stdout(io.StringIO()):
            return kit.main(['--config', str(self.destination), 'configure', *args])

    def test_creates_private_config_without_network_or_sudo(self):
        with patch.object(kit, 'run') as execute, patch.object(kit.urllib.request, 'urlopen') as request:
            self.assertEqual(self.configure('--repository', 'sample-org/flutter-app'), 0)
        execute.assert_not_called()
        request.assert_not_called()
        self.assertEqual(self.destination.stat().st_mode & 0o777, 0o600)
        value = kit.config(self.destination)
        self.assertEqual(value['repository'], 'sample-org/flutter-app')
        self.assertEqual(value['platforms'], ['android', 'ios'])

    def test_other_project_account_label_platform_and_versions(self):
        self.assertEqual(self.configure('--repository', 'second-org/second-app', '--ci-user', 'buildbot',
                                        '--label', 'second-local', '--platforms', 'ios',
                                        '--xcode-version', '26.2', '--ruby-version', '3.4'), 0)
        value = kit.config(self.destination)
        self.assertEqual((value['ci_user'], value['label'], value['platforms'], value['ruby_version']),
                         ('buildbot', 'second-local', ['ios'], '3.4'))

    def test_existing_config_is_never_overwritten(self):
        self.destination.write_text('preserve existing settings')
        self.assertEqual(self.configure('--repository', 'sample-org/flutter-app'), 1)
        self.assertEqual(self.destination.read_text(), 'preserve existing settings')

    def test_symlink_including_dangling_target_is_refused(self):
        target = self.destination.parent / 'untouched'
        self.destination.symlink_to(target)
        self.assertEqual(self.configure('--repository', 'sample-org/flutter-app'), 1)
        self.assertFalse(target.exists())

    def test_placeholder_is_not_used_as_a_real_repository(self):
        self.assertEqual(self.configure('--repository', 'OWNER/REPO'), 1)
        self.assertFalse(self.destination.exists())

    def test_bad_settings_do_not_leave_a_partial_config(self):
        self.assertEqual(self.configure('--repository', 'sample-org/flutter-app', '--ci-user', 'root'), 1)
        self.assertFalse(self.destination.exists())

    def test_missing_config_message_has_the_first_command(self):
        with self.assertRaisesRegex(ValueError, 'configure'):
            kit.config(self.destination)

    def test_configuration_bom_is_supported(self):
        data = kit.config(kit.ROOT / 'config.example.json')
        self.destination.write_text(json.dumps(data), encoding='utf-8-sig')
        self.assertEqual(kit.config(self.destination), data)

    def test_source_launcher_resolves_itself_from_another_directory(self):
        result = subprocess.run(['/bin/bash', str(ROOT / 'runner'), '--lang', 'en', '--help'],
                                cwd=self.temp.name, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('configure', result.stdout)
        self.assertIn('Options:', result.stdout)

    def test_configure_cli_no_implicit_personal_machine_mutations(self):
        if os.getuid() == 0:
            self.skipTest('configure deliberately refuses root')
        result = subprocess.run([sys.executable, str(kit.ROOT / 'runner.py'), '--lang', 'ru',
                                 '--config', str(self.destination), 'configure',
                                 '--repository', 'sample-org/flutter-app', '--platforms', 'android'],
                                cwd=self.temp.name, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Конфигурация записана', result.stdout)
        self.assertEqual(kit.config(self.destination)['platforms'], ['android'])
        self.assertEqual(list(self.destination.parent.iterdir()), [self.destination])
