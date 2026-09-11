"""Offline localization, hidden-input and registration regression tests."""
import ast
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tool' / 'runner'))
import i18n
import runner as kit


class LocalizationTests(unittest.TestCase):
    def setUp(self):
        previous = i18n.LANGUAGE
        self.addCleanup(setattr, i18n, 'LANGUAGE', previous)
        i18n.LANGUAGE = 'ru'

    def test_catalog_languages_and_placeholders_match(self):
        for key, values in i18n.MESSAGES.items():
            self.assertEqual(set(values), {'ru', 'en'}, key)
            self.assertEqual(values['ru'].count('%s'), values['en'].count('%s'), key)
            for language, value in values.items():
                self.assertTrue(value, key)
                self.assertNotIn('%', value.replace('%s', ''), key)
                if language == 'en':
                    self.assertIsNone(re.search('[а-яА-ЯёЁ]', value), key)

    def test_all_python_and_shell_message_ids_exist(self):
        for name in ('runner.py', 'i18n.py', 'configuration.py'):
            tree = ast.parse((kit.ROOT / name).read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'tr':
                    self.assertIn(node.args[0].value, i18n.MESSAGES)
        source = (kit.ROOT / 'provision.sh').read_text()
        for key in re.findall(r'\b(?:msg|fail) ([a-z_]+)', source):
            self.assertIn(key, i18n.MESSAGES)
        for name in ('i18n.py', 'messages.json', 'messages.sh'):
            self.assertIn(name, source)

    def test_language_flag_works_on_both_sides_of_command(self):
        with patch.dict(os.environ, {'CI_RUNNER_LANG': 'ru'}):
            self.assertEqual(i18n.select(['--lang', 'en', 'doctor']), ['doctor'])
            self.assertEqual(i18n.LANGUAGE, 'en')
            self.assertEqual(i18n.select(['doctor', '--lang=ru']), ['doctor'])
            self.assertEqual(i18n.LANGUAGE, 'ru')

    def test_saved_language_and_environment_override(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(i18n, 'ROOT', Path(directory)):
            (Path(directory) / 'language').write_text('en\n')
            with patch.dict(os.environ, {}, clear=True):
                i18n.select(['doctor'])
                self.assertEqual(i18n.LANGUAGE, 'en')
            with patch.dict(os.environ, {'CI_RUNNER_LANG': 'ru'}):
                i18n.select(['doctor'])
                self.assertEqual(i18n.LANGUAGE, 'ru')

    def test_invalid_language_cannot_select_arbitrary_catalog(self):
        for args in (['--lang'], ['--lang=../../secrets'], ['--lang', 'de']):
            with self.assertRaises(ValueError):
                i18n.select(args)

    def test_cli_help_in_both_languages(self):
        for language, expected, excluded in [('ru', 'Параметры:', 'Options:'), ('en', 'Options:', 'Параметры:')]:
            output = io.StringIO()
            with redirect_stdout(output), self.assertRaises(SystemExit) as exit_code:
                kit.main(['--lang', language, '--help'])
            self.assertEqual(exit_code.exception.code, 0)
            self.assertIn(expected, output.getvalue())
            self.assertNotIn(excluded, output.getvalue())
            self.assertIn('--lang', output.getvalue())

    def test_argument_error_does_not_echo_secret(self):
        output = io.StringIO()
        with redirect_stderr(output), self.assertRaises(SystemExit) as exit_code:
            kit.main(['--lang=en', 'register', '--token', 'fixture-secret-not-real'])
        self.assertEqual(exit_code.exception.code, 2)
        self.assertIn('Invalid arguments', output.getvalue())
        self.assertNotIn('fixture-secret-not-real', output.getvalue())

    @unittest.skipUnless(sys.platform == 'darwin', 'macOS plutil catalog consumer')
    def test_bash_messages_use_same_catalog_and_data_is_not_executed(self):
        for language in ('ru', 'en'):
            env = dict(os.environ, CI_RUNNER_LANG=language, kit=str(kit.ROOT))
            result = subprocess.run(['/bin/bash', '-c', 'source "$kit/messages.sh"; msg provision_symlink \'$(exit 99)\''],
                                    env=env, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('$(exit 99)', result.stdout)
            i18n.LANGUAGE = language
            self.assertEqual(result.stdout.strip(), i18n.tr('provision_symlink', '$(exit 99)'))


class RegistrationInputTests(unittest.TestCase):
    def test_redirected_stdin_is_rejected_without_reading(self):
        with patch.object(kit.sys.stdin, 'isatty', return_value=False), patch.object(kit.getpass, 'getpass') as prompt:
            with self.assertRaises(ValueError):
                kit.read_registration_token()
            prompt.assert_not_called()

    def test_getpass_never_falls_back_to_echo(self):
        with patch.object(kit.sys.stdin, 'isatty', return_value=True), \
                patch.object(kit.getpass, 'getpass', side_effect=kit.getpass.GetPassWarning):
            with self.assertRaises(ValueError):
                kit.read_registration_token()

    def test_pasted_commands_pat_and_invisible_characters_rejected(self):
        for token in ('', './config.sh --token fixture', '{{fixture-not-real}}', 'fixture-not-real\u00a0',
                      'ghp_' + 'x' * 30, 'github_pat_' + 'x' * 40, '\x1b[200~fixture-not-real', 'x' * 257):
            with self.subTest(token=token), patch.object(kit.sys.stdin, 'isatty', return_value=True), \
                    patch.object(kit.getpass, 'getpass', return_value=token):
                with self.assertRaises(ValueError):
                    kit.read_registration_token()

    def test_token_is_read_once_and_hidden(self):
        with patch.object(kit.sys.stdin, 'isatty', return_value=True), \
                patch.object(kit.getpass, 'getpass', return_value='fixture-not-real') as prompt:
            self.assertEqual(kit.read_registration_token(), 'fixture-not-real')
            prompt.assert_called_once()

    def test_inherited_runner_overrides_are_removed(self):
        cfg = kit.config(kit.ROOT / 'config.example.json')
        with patch.dict(os.environ, {'ACTIONS_RUNNER_INPUT_REPLACE': 'true', 'actions_runner_input_token': 'stale'}):
            env = kit.environment(cfg, Path('/Users/ci'))
            self.assertFalse(any(k.upper().startswith('ACTIONS_RUNNER_INPUT_') for k in env))

    def test_failure_masks_token_and_clears_child_environment(self):
        token = 'fixture-not-a-real-secret'
        output, errors = io.StringIO(), io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            target = home / 'actions-runner'
            target.mkdir()
            (target / '.kit-download.json').write_text('{}')
            child_env = None
            def failed(args, **kwargs):
                nonlocal child_env
                child_env = kwargs['env']
                self.assertEqual(child_env['ACTIONS_RUNNER_INPUT_TOKEN'], token)
                self.assertNotIn(token, args)
                self.assertIn('--unattended', args)
                return subprocess.CompletedProcess(args, 1, 'HTTP 404 ' + token, 'error ' + token)
            with patch.object(kit, 'require_ci'), patch.object(kit, 'doctor', return_value=True), \
                    patch.object(kit.Path, 'home', return_value=home), \
                    patch.object(kit, 'read_registration_token', return_value=token), \
                    patch.object(kit, 'run', side_effect=failed), redirect_stdout(output), redirect_stderr(errors):
                self.assertEqual(kit.register(kit.config(kit.ROOT / 'config.example.json')), 1)
            self.assertNotIn('ACTIONS_RUNNER_INPUT_TOKEN', child_env)
            self.assertNotIn(token, output.getvalue() + errors.getvalue())
            self.assertIn('404 ***', errors.getvalue())
            self.assertEqual(list(target.iterdir()), [target / '.kit-download.json'])

    def test_real_subprocess_registers_bom_fixture_then_reuses_it(self):
        # Fake config.sh, real subprocess and environment transport; no GitHub access.
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            target = home / 'actions-runner'
            target.mkdir()
            (target / '.kit-download.json').write_text('{}')
            fixture = target / 'fixture.py'
            fixture.write_text('import json,os,pathlib,sys\n'
                'assert os.environ["ACTIONS_RUNNER_INPUT_TOKEN"] == "fixture-not-real"\n'
                'assert "--unattended" in sys.argv and "--token" not in sys.argv\n'
                'url = sys.argv[sys.argv.index("--url") + 1]\n'
                'pathlib.Path(".runner").write_text(json.dumps({"gitHubUrl": url}), encoding="utf-8-sig")\n')
            import shlex
            (target / 'config.sh').write_text('exec ' + shlex.quote(sys.executable) + ' fixture.py "$@"\n')
            with patch.object(kit, 'require_ci'), patch.object(kit, 'doctor', return_value=True), \
                    patch.object(kit.Path, 'home', return_value=home), \
                    patch.object(kit, 'read_registration_token', return_value='fixture-not-real') as prompt, \
                    redirect_stdout(io.StringIO()):
                cfg = kit.config(kit.ROOT / 'config.example.json')
                self.assertEqual(kit.register(cfg), 0)
                self.assertEqual(kit.register(cfg), 0)
                prompt.assert_called_once()
            self.assertTrue((target / '.runner').read_bytes().startswith(b'\xef\xbb\xbf'))
            self.assertNotIn(b'fixture-not-real', (target / '.runner').read_bytes())


if __name__ == '__main__':
    unittest.main()
