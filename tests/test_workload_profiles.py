"""Workload composition and migration, without sudo, credentials or real Docker jobs."""
import copy
from contextlib import ExitStack, redirect_stdout
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tool/runner'))
import configuration as config
import runner as kit
import workloads


def profile(name='generic', **kwargs):
    return config.for_profile(kit.config(kit.ROOT / 'config.example.json'), name, **kwargs)


class SchemaTests(unittest.TestCase):
    def test_presets_are_independent_copies(self):
        for name, expected in config.PROFILES.items():
            value = profile(name)
            self.assertEqual(value['capabilities'], list(expected))
            self.assertEqual(value['schema_version'], 2)
            value['capabilities'].append('not-valid')
            self.assertEqual(profile(name)['capabilities'], list(expected))

    def test_backend_has_no_mobile_requirements(self):
        value = profile('backend', node='24')
        self.assertEqual(value['capabilities'], ['docker', 'node'])
        self.assertEqual(value['versions'], {'node': '24'})
        self.assertNotIn('platforms', value)

    def test_composable_capabilities_and_extra_commands(self):
        value = profile('node', extra=['docker', 'node'], tools=['terraform', 'go', 'python3.12'])
        self.assertEqual(value['capabilities'], ['docker', 'node'])
        self.assertEqual(value['required_tools'], ['terraform', 'go', 'python3.12'])

    def test_legacy_normalizes_in_memory_without_mutating_source(self):
        original = kit.config(kit.ROOT / 'config.example.json')
        before = copy.deepcopy(original)
        value = config.normalized(original)
        self.assertEqual(value['capabilities'], ['android', 'ios', 'ruby'])
        self.assertEqual(value['versions'], {'ruby': '3.3', 'xcode': '26.3'})
        self.assertEqual(original, before)
        value['runner_sha256']['arm64'] = 'b' * 64
        self.assertEqual(original, before)

    def test_legacy_android_does_not_keep_unused_xcode_version(self):
        legacy = dict(kit.config(kit.ROOT / 'config.example.json'), platforms=['android'])
        self.assertEqual(config.normalized(legacy)['versions'], {'ruby': '3.3'})

    def test_v2_normalization_is_idempotent_and_a_copy(self):
        value = profile('backend')
        normalized = config.normalized(value)
        self.assertEqual(normalized, value)
        self.assertEqual(config.normalized(normalized), value)
        self.assertIsNot(normalized, value)

    def test_invalid_schema_and_unknown_fields_fail_closed(self):
        for change in ({'schema_version': 1}, {'schema_version': 3}, {'schema_version': True},
                       {'schema_version': '2'}, {'token': 'fixture'}, {'install': 'curl | bash'},
                       {'platforms': []}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                config.validate(dict(profile(), **change))

    def test_invalid_capabilities_fail_closed(self):
        for caps in (None, 'node', ['linux'], ['node', 'node'], [{}], [1]):
            with self.subTest(caps=caps), self.assertRaises(ValueError):
                config.validate(dict(profile(), capabilities=caps))

    def test_versions_must_match_selected_capabilities(self):
        for versions in ({'node': '24'}, {'ruby': '3.3'}, {'xcode': '26.3'}, {'python': '3.12'}, []):
            with self.subTest(versions=versions), self.assertRaises(ValueError):
                config.validate(dict(profile(), versions=versions))
        for name in ('mobile', 'ios', 'android'):
            with self.assertRaises(ValueError):
                config.validate(dict(profile(name), versions={}))

    def test_invalid_version_values_rejected_before_execution(self):
        for value in ('24.1', 'v24', '0', '--help', '24;id', None, 24, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                config.validate(dict(profile('node'), versions={'node': value}))

    def test_extra_commands_are_names_not_scripts_paths_or_options(self):
        for tools in (['../tool'], ['/bin/sh'], ['a b'], ['$(id)'], ['--version'], ['a\nb'],
                      ['.'], ['..'], ['foo', 'foo'], [None], 'go', ['x' * 65],
                      [f'tool{i}' for i in range(33)]):
            with self.subTest(tools=tools), self.assertRaises(ValueError):
                config.validate(dict(profile(), required_tools=tools))


class ProfileCLITests(unittest.TestCase):
    def test_profiles_does_not_need_config_or_touch_host(self):
        with patch.object(kit, 'config') as read, patch.object(kit, 'run') as run, redirect_stdout(io.StringIO()) as report:
            self.assertEqual(kit.main(['profiles']), 0)
        read.assert_not_called()
        run.assert_not_called()
        for name in config.PROFILES:
            self.assertIn(name + ':', report.getvalue())

    def test_real_generic_and_backend_configure_subprocesses(self):
        if os.getuid() == 0:
            self.skipTest('configure intentionally refuses root')
        for args, expected in ((['--profile', 'generic', '--require-tool', 'terraform'], []),
                               (['--profile', 'backend', '--node-version', '24'], ['docker', 'node']),
                               (['--capability', 'docker'], ['docker'])):
            with self.subTest(args=args), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'config.json'
                result = subprocess.run([sys.executable, str(kit.ROOT / 'runner.py'), '--config', str(path),
                                         'configure', '--repository', 'sample-org/private-app', *args],
                                        text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(kit.config(path)['capabilities'], expected)
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertEqual(list(path.parent.iterdir()), [path])

    def test_custom_config_setup_hint_preserves_and_quotes_path(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config with spaces.json"
            with patch.object(kit.os, 'getuid', return_value=501), redirect_stdout(io.StringIO()) as output:
                self.assertEqual(kit.main(['--lang', 'en', '--config', str(path), 'configure',
                                          '--repository', 'sample-org/private-app', '--profile', 'generic']), 0)
            import shlex
            self.assertIn('--config ' + shlex.quote(str(path.resolve())) + ' setup', output.getvalue())

    def test_conflicting_or_invalid_profile_flags_leave_no_file(self):
        for args in (['--platforms', 'ios', '--profile', 'generic'],
                     ['--profile', 'generic', '--node-version', '24'],
                     ['--profile', 'generic', '--require-tool', '/bin/sh'],
                     ['--profile', 'generic', '--ruby-version', '3.3'],
                     ['--profile', 'node', '--xcode-version', '26.3']):
            with self.subTest(args=args), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'config.json'
                with patch.object(kit.os, 'getuid', return_value=501):
                    self.assertEqual(kit.main(['--config', str(path), 'configure', '--repository',
                                              'sample-org/private-app', *args]), 1)
                self.assertFalse(path.exists())


class EnvironmentTests(unittest.TestCase):
    def test_generic_does_not_inherit_mobile_or_docker_overrides(self):
        dirty = {'ANDROID_HOME': '/private/sdk', 'ANDROID_SDK_ROOT': '/private/sdk',
                 'DEVELOPER_DIR': '/private/xcode', 'DOCKER_CONFIG': '/private/docker',
                 'DOCKER_HOST': 'ssh://production', 'DOCKER_CONTEXT': 'production',
                 'DOCKER_CERT_PATH': '/private/cert'}
        with patch.dict(os.environ, dirty):
            env = kit.environment(profile(), Path('/Users/ci'))
        for name in dirty:
            self.assertNotIn(name, env)
        self.assertNotIn('ruby@', env['PATH'])
        self.assertNotIn('Android', env['PATH'])

    def test_backend_uses_ci_account_docker_config_and_optional_node_path(self):
        env = kit.environment(profile('backend', node='24'), Path('/Users/ci_backend'))
        self.assertEqual(env['DOCKER_CONFIG'], '/Users/ci_backend/.docker')
        self.assertIn('/opt/homebrew/opt/node@24/bin', env['PATH'])
        self.assertNotIn('ANDROID_HOME', env)

    def test_v1_and_normalized_mobile_environment_match(self):
        legacy = kit.config(kit.ROOT / 'config.example.json')
        self.assertEqual(kit.environment(legacy, Path('/Users/ci')),
                         kit.environment(config.normalized(legacy), Path('/Users/ci')))

    def test_shell_renderer_quotes_data_and_does_not_dump_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home '$(false)"
            cfg = profile('backend')
            with patch.dict(os.environ, {'SECRET_FIXTURE': 'must-not-be-printed'}):
                script = kit.shell_environment(cfg, home)
            self.assertNotIn('must-not-be-printed', script)
            self.assertNotIn('SECRET_FIXTURE', script)
            result = subprocess.run(['/bin/bash', '-c', script + '\nprintf "%s" "$DOCKER_CONFIG"'],
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, str(home / '.docker'))

    def test_shell_renderer_unsets_stale_profile_variables(self):
        script = kit.shell_environment(profile(), Path('/Users/ci'))
        for name in ('ANDROID_HOME', 'ANDROID_SDK_ROOT', 'DEVELOPER_DIR', 'DOCKER_CONFIG', *kit.DOCKER_OVERRIDES):
            self.assertIn('unset ' + name, script)


class ProbeTests(unittest.TestCase):
    def test_node_requires_valid_version_and_requested_major(self):
        for version, major, expected in (('v24.1.0', '24', True), ('v22.1.0', '24', False),
                                         ('v22.1.0', None, True), ('garbage', None, False)):
            output = Mock(return_value=(True, version))
            self.assertEqual(workloads.node_ready(output, {}, major), expected)
            output.assert_called_once_with(['node', '--version'], {})

    def test_local_docker_is_probed_without_starting_containers(self):
        output = Mock(side_effect=[(True, '{"Host":"unix:///Users/ci/.docker/run/docker.sock"}'), (True, '29.4.0')])
        self.assertTrue(workloads.docker_ready(output, {}))
        self.assertEqual(output.call_args.args[0], ['docker', '--host', 'unix:///Users/ci/.docker/run/docker.sock',
                                                   'info', '--format', '{{.ServerVersion}}'])

    def test_remote_malformed_or_unsafe_context_never_contacts_daemon(self):
        for context in ('{"Host":"ssh://production"}', '{"Host":"tcp://localhost:2375"}',
                        '{"Host":"unix:///tmp/../private/docker.sock"}', '{"Host":null}',
                        '{}', '[]', 'null', 'not json', '{"Host":"unix://relative"}'):
            with self.subTest(context=context):
                output = Mock(return_value=(True, context))
                self.assertFalse(workloads.docker_ready(output, {}))
                self.assertEqual(output.call_count, 1)

    def test_missing_cli_or_offline_daemon_fails(self):
        self.assertFalse(workloads.docker_ready(Mock(return_value=(False, '')), {}))
        output = Mock(side_effect=[(True, '{"Host":"unix:///tmp/docker.sock"}'), (False, '')])
        self.assertFalse(workloads.docker_ready(output, {}))

    def test_command_probes_are_bounded(self):
        with patch.object(kit, 'run', side_effect=subprocess.TimeoutExpired('docker', 15)) as run:
            self.assertEqual(kit.output(['docker', 'info']), (False, ''))
        self.assertEqual(run.call_args.kwargs['timeout'], 15)


class DoctorTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(kit.platform, 'system', return_value='Darwin'))
        self.stack.enter_context(patch.object(kit.platform, 'machine', return_value='arm64'))
        self.stack.enter_context(patch.object(kit.shutil, 'disk_usage', return_value=SimpleNamespace(free=30 * 1024**3)))
        self.which = self.stack.enter_context(patch.object(kit.shutil, 'which', return_value='/fixture/bin/tool'))
        self.stack.enter_context(patch.object(kit, 'current_ci', return_value=True))

    def test_generic_skips_mobile_docker_and_signing_checks(self):
        with patch.object(kit, 'output') as output, patch.object(kit, 'default_keychain') as keychain, \
                patch.object(kit, 'ios_platform_available') as ios, patch.object(kit.Path, 'glob') as glob:
            self.assertTrue(kit.doctor(profile(), print_report=False))
        output.assert_not_called()
        keychain.assert_not_called()
        ios.assert_not_called()
        glob.assert_not_called()

    def test_additional_tool_only_checks_presence(self):
        with patch.object(kit, 'output') as output:
            self.assertTrue(kit.doctor(profile(tools=['terraform', 'go']), print_report=False))
        self.assertIn('terraform', [call.args[0] for call in self.which.call_args_list])
        self.assertIn('go', [call.args[0] for call in self.which.call_args_list])
        output.assert_not_called()

    def test_missing_custom_tool_blocks_readiness(self):
        self.which.side_effect = lambda name, **_: None if name == 'terraform' else '/fixture/tool'
        self.assertFalse(kit.doctor(profile(tools=['terraform']), print_report=False))

    def test_backend_checks_node_npm_and_local_docker_without_mobile(self):
        def output(args, env):
            if args == ['node', '--version']:
                return True, 'v24.1.0'
            if args == ['npm', '--version']:
                return True, '11.0.0'
            if args[:3] == ['docker', 'context', 'inspect']:
                return True, '{"Host":"unix:///fixture/docker.sock"}'
            if args[:3] == ['docker', '--host', 'unix:///fixture/docker.sock']:
                return True, '29.4.0'
            self.fail('Unexpected command: ' + repr(args))
        with patch.object(kit, 'output', side_effect=output):
            self.assertTrue(kit.doctor(profile('backend', node='24'), print_report=False))

    def test_host_preflight_does_not_contact_owner_docker_daemon(self):
        def output(args, env):
            self.assertIn(args[0], ('node', 'npm'))
            return True, 'v24.1.0' if args[0] == 'node' else '11.0.0'
        with patch.object(kit, 'output', side_effect=output):
            self.assertTrue(kit.doctor(profile('backend'), host=True, print_report=False))
        self.assertIn('docker', [call.args[0] for call in self.which.call_args_list])

    def test_failed_backend_gate_blocks_listener(self):
        with patch.object(kit, 'doctor', return_value=False), patch.object(kit.subprocess, 'Popen') as start:
            self.assertEqual(kit.start(profile('backend')), 1)
        start.assert_not_called()


class ProvisioningTests(unittest.TestCase):
    def test_setup_uses_normalized_private_snapshot_and_cleans_it_after_failure(self):
        snapshots = []
        original = kit.ROOT / 'config.example.json'
        original_bytes = original.read_bytes()
        def provision(args, **kwargs):
            snapshot = Path(args[4])
            snapshots.append(snapshot)
            self.assertEqual(json.loads(snapshot.read_text()), config.normalized(kit.config(original)))
            self.assertEqual(snapshot.stat().st_mode & 0o777, 0o600)
            self.assertEqual(kwargs['cwd'], '/')
            raise subprocess.SubprocessError('fixture failure')
        with patch.object(kit.os, 'getuid', return_value=501), patch.object(kit, 'current_ci', return_value=False), \
                patch.object(kit.os, 'getgroups', return_value=[80]), \
                patch.object(kit.grp, 'getgrgid', return_value=SimpleNamespace(gr_name='admin')), \
                patch.object(kit, 'doctor', return_value=True), patch.object(kit, 'run', side_effect=provision):
            with self.assertRaises(subprocess.SubprocessError):
                kit.setup(kit.config(original), original, None)
        self.assertFalse(snapshots[0].exists())
        self.assertEqual(original.read_bytes(), original_bytes)

    def test_backend_setup_does_not_resolve_or_copy_android_sdk(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            cfg = profile('backend')
            path.write_text(json.dumps(cfg))
            with patch.object(kit.os, 'getuid', return_value=501), patch.object(kit, 'current_ci', return_value=False), \
                    patch.object(kit.os, 'getgroups', return_value=[80]), \
                    patch.object(kit.grp, 'getgrgid', return_value=SimpleNamespace(gr_name='admin')), \
                    patch.object(kit, 'doctor', return_value=True), \
                    patch.object(kit, 'run', return_value=SimpleNamespace(returncode=0)) as run:
                self.assertEqual(kit.setup(cfg, path, None), 0)
                self.assertEqual(run.call_args.args[0][5], '/')
                run.reset_mock()
                with self.assertRaises(ValueError):
                    kit.setup(cfg, path, '/private/sdk')
                run.assert_not_called()

    def test_provisioner_installs_every_import_and_uses_one_environment_renderer(self):
        source = (kit.ROOT / 'provision.sh').read_text()
        for name in ('configuration.py', 'workloads.py'):
            self.assertEqual(source.count(name), 2)
        self.assertIn('"$HOME/bin/ci-runner" shell-env >', source)
        self.assertNotIn('CI_RUBY=', source)
        self.assertIn('if [[ "$CI_CAPABILITIES" == *ios* ]]; then', source)
        self.assertNotIn('chmod 666', source)

    def test_shell_capability_validation_accepts_all_subsets(self):
        import itertools
        source = (kit.ROOT / 'provision.sh').read_text()
        pattern = re.search(r"^capability_pattern='([^']+)'", source, re.M)[1]
        for size in range(len(config.CAPABILITIES) + 1):
            for caps in itertools.combinations(config.CAPABILITIES, size):
                env = dict(os.environ, FIXTURE_CAPS=json.dumps(list(caps), separators=(',', ':')), FIXTURE_PATTERN=pattern)
                result = subprocess.run(['/bin/bash', '-c', '[[ "$FIXTURE_CAPS" =~ $FIXTURE_PATTERN ]]'], env=env)
                self.assertEqual(result.returncode, 0, caps)


if __name__ == '__main__':
    unittest.main()
