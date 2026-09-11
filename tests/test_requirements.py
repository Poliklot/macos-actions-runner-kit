"""Portable project requirements: offline tests, no user/tool installation."""
import copy
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tool/runner'))
import configuration
import runner
import workloads


def manifest(data):
    return configuration.with_requirements(runner.config(runner.ROOT / 'config.example.json'), data)


class ManifestTests(unittest.TestCase):
    def test_arbitrary_stack_without_profile_or_capability(self):
        cfg = manifest({'required_tools': ['python3', 'rustc', 'cargo', 'go', 'terraform'],
                        'tool_versions': {'python3': '3.12', 'rustc': '1.90'},
                        'path_prepend': ['${HOME}/.cargo/bin', '/opt/homebrew/opt/python@3.12/libexec/bin'],
                        'minimum_free_gib': 50})
        self.assertEqual(cfg['capabilities'], [])
        self.assertEqual(cfg['versions'], {})
        self.assertEqual(cfg['minimum_free_gib'], 50)
        self.assertEqual(configuration.normalized(cfg), cfg)

    def test_manifest_and_base_never_mutated(self):
        data = {'capabilities': ['docker'], 'required_tools': ['python3']}
        before = copy.deepcopy(data)
        cfg = manifest(data)
        cfg['required_tools'].append('go')
        self.assertEqual(data, before)

    def test_empty_manifest_is_explicit_base_not_mobile(self):
        self.assertEqual(manifest({})['capabilities'], [])

    def test_identity_secrets_hooks_unknown_fields_rejected(self):
        for value in ([], None, {'repository': 'other/repo'}, {'ci_user': 'root'},
                      {'env': {'TOKEN': 'fixture'}}, {'install': 'echo nope'}, {'checks': ['sh']}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                manifest(value)

    def test_version_constraints_are_explicit_numeric_prefixes(self):
        for version in (None, 3, True, '>=3.12', '3.*', '3.12;id', '', '3.12-beta', '1.2.3.4.5'):
            with self.subTest(version=version), self.assertRaises(ValueError):
                manifest({'required_tools': ['python3'], 'tool_versions': {'python3': version}})
        with self.assertRaises(ValueError):
            manifest({'tool_versions': {'python3': '3.12'}})

    def test_builtin_and_custom_version_contracts_do_not_conflict(self):
        with self.assertRaises(ValueError):
            manifest({'capabilities': ['node'], 'required_tools': ['node'], 'tool_versions': {'node': '22'}})
        self.assertEqual(manifest({'capabilities': ['docker'], 'required_tools': ['python3'],
                                   'tool_versions': {'python3': '3.12'}})['capabilities'], ['docker'])

    def test_unsafe_paths_fail_at_config_time(self):
        for path in ('', '/tmp/bin', '/Users/personal/bin', '${HOME}', '${HOME}/../personal/bin',
                     '${HOME}//bin', '${HOME}/./bin', '${HOME}/bin:', '${HOME}/$(id)',
                     '${HOME}/bin\n', '~/.cargo/bin', '/opt/homebrew/opt/../../tmp', '${TOKEN}/bin'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                manifest({'path_prepend': [path]})
        for paths in (None, '${HOME}/bin', ['${HOME}/bin'] * 2, [1], [f'${{HOME}}/bin{i}' for i in range(17)]):
            with self.subTest(paths=paths), self.assertRaises(ValueError):
                manifest({'path_prepend': paths})

    def test_cli_snapshot_is_portable_and_immutable(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'requirements.json'
            dest = Path(directory) / 'config.json'
            source.write_text(json.dumps({'required_tools': ['rustc'], 'tool_versions': {'rustc': '1.90'}}))
            args = ['--config', str(dest), 'configure', '--repository', 'sample-org/private-app',
                    '--requirements', str(source)]
            with patch.object(runner.os, 'getuid', return_value=501), redirect_stdout(io.StringIO()):
                self.assertEqual(runner.main(args), 0)
                snapshot = dest.read_bytes()
                source.write_text('{}')
                self.assertEqual(runner.config(dest)['tool_versions'], {'rustc': '1.90'})
                self.assertEqual(runner.main(args), 1)
                self.assertEqual(dest.read_bytes(), snapshot)
            self.assertEqual(dest.stat().st_mode & 0o777, 0o600)

    def test_cli_conflicts_and_bad_json_never_write_partial_config(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'requirements.json'
            dest = Path(directory) / 'config.json'
            source.write_text('{}')
            args = ['--config', str(dest), 'configure', '--repository', 'sample-org/private-app',
                    '--requirements', str(source)]
            for extra in (['--profile', 'generic'], ['--platforms', 'all'], ['--capability', 'docker'],
                          ['--node-version', '24'], ['--require-tool', 'go'], ['--ruby-version', '3.3']):
                with patch.object(runner.os, 'getuid', return_value=501), redirect_stdout(io.StringIO()):
                    self.assertEqual(runner.main(args + extra), 1)
                self.assertFalse(dest.exists())
            source.write_text('{')
            with patch.object(runner.os, 'getuid', return_value=501):
                self.assertEqual(runner.main(args), 1)
            self.assertFalse(dest.exists())


class ProbeTests(unittest.TestCase):
    def test_fixed_version_probe_no_shell_or_custom_arguments(self):
        for output, expected in (('Python 3.12.8', True), ('Python 3.120.1', False),
                                 ('Python 3.13.0', False), ('Python 3.12.8-rc1', False),
                                 ('Python 3.12.8+dev', False), ('3.12.8 and 4.1.0', False),
                                 ('warning\nPython 3.12.8', False), ('', False)):
            probe = Mock(return_value=(True, output))
            self.assertEqual(workloads.tool_ready(probe, {}, 'python3', '3.12'), expected, output)
            probe.assert_called_once_with(['python3', '--version'], {})
        self.assertFalse(workloads.tool_ready(Mock(return_value=(False, 'Python 3.12.8')), {}, 'python3', '3.12'))

    def test_home_expansion_is_not_owner_environment_interpolation(self):
        cfg = manifest({'path_prepend': ['${HOME}/.cargo/bin']})
        with patch.dict(os.environ, {'HOME': '/Users/personal', 'SECRET': 'fixture'}):
            env = runner.environment(cfg, Path('/Users/ci_rust'))
        self.assertTrue(env['PATH'].startswith('/Users/ci_rust/bin:/Users/ci_rust/.cargo/bin:'))
        self.assertNotIn('/Users/personal', env['PATH'])

    def test_paths_must_exist_and_not_escape_via_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / 'ci'
            home.mkdir()
            tool = home / 'tools'
            tool.mkdir()
            self.assertTrue(workloads.path_ready(tool, home))
            self.assertFalse(workloads.path_ready(home / 'missing', home))
            external = Path(directory) / 'owner'
            external.mkdir()
            (home / 'escape').symlink_to(external, target_is_directory=True)
            self.assertFalse(workloads.path_ready(home / 'escape', home))
            tool.chmod(0o777)
            self.assertFalse(workloads.path_ready(tool, home))

    def test_invalid_path_stops_doctor_before_any_tool_execution(self):
        cfg = manifest({'path_prepend': ['${HOME}/missing'], 'required_tools': ['python3'],
                        'tool_versions': {'python3': '3.12'}})
        with patch.object(workloads, 'path_ready', return_value=False), patch.object(runner, 'output') as output:
            with self.assertRaises(ValueError):
                runner.doctor(cfg, print_report=False)
        output.assert_not_called()

    def test_custom_probes_not_executed_by_owner_or_root(self):
        cfg = manifest({'required_tools': ['rustc'], 'tool_versions': {'rustc': '1.90'}})
        with patch.object(runner, 'current_ci', return_value=False), patch.object(runner, 'output') as output:
            self.assertFalse(runner.doctor(cfg, print_report=False))
            runner.doctor(cfg, host=True, print_report=False)
        output.assert_not_called()

    def test_doctor_checks_ci_tool_version(self):
        cfg = manifest({'required_tools': ['rustc'], 'tool_versions': {'rustc': '1.90'}})
        with patch.object(runner, 'current_ci', return_value=True), patch.object(runner, 'output',
                                                                               return_value=(True, 'rustc 1.90.1')) as output:
            runner.doctor(cfg, print_report=False)
        output.assert_called_once()
        self.assertEqual(output.call_args.args[0], ['rustc', '--version'])


if __name__ == '__main__':
    unittest.main()
