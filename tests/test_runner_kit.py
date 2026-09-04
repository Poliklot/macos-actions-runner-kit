"""Portable runner-kit tests: no root, credentials, accounts or GitHub side effects."""
from __future__ import annotations

import copy
from contextlib import redirect_stdout
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

KIT = Path(__file__).resolve().parents[1] / "tool" / "runner"
sys.path.insert(0, str(KIT))
spec = importlib.util.spec_from_file_location("runner_kit", KIT / "runner.py")
kit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kit)


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.cfg = kit.config(KIT / "config.example.json")

    def validate(self, changes):
        value = copy.deepcopy(self.cfg)
        value.update(changes)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.example.json"
            path.write_text(json.dumps(value))
            return kit.config(path)

    def test_other_project_is_only_configuration(self):
        result = self.validate({"repository": "example/mobile-app", "label": "mobile-local",
                                "ci_user": "buildbot", "platforms": ["ios"], "xcode_version": "26.2"})
        self.assertEqual(result["repository"], "example/mobile-app")

    def test_rejects_shell_injection_and_path_traversal(self):
        for field in ("ci_user", "label", "repository", "xcode_version", "ruby_version", "runner_version"):
            for value in ("../escape", "$(touch /tmp/pwn)", "a\nb", "--replace", "", None):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    self.validate({field: value})

    def test_rejects_privileged_or_reserved_accounts(self):
        for value in ("root", "admin", "daemon", "nobody", "guest"):
            with self.assertRaises(ValueError):
                self.validate({"ci_user": value})

    def test_no_secrets_or_unknown_configuration_fields(self):
        with self.assertRaises(ValueError):
            self.validate({"token": "not-a-real-token"})

    def test_minimum_disk_cannot_be_disabled(self):
        for value in (0, 19, 1001, True, "20"):
            with self.assertRaises(ValueError):
                self.validate({"minimum_free_gib": value})

    def test_platforms_are_explicit(self):
        for value in (["linux"], [], "ios", ["ios", "android"], ["android", "android"]):
            with self.assertRaises(ValueError):
                self.validate({"platforms": value})

    def test_both_architectures_must_have_hashes(self):
        for value in ({"arm64": "a" * 64}, {"arm64": "latest", "x64": "b" * 64}):
            with self.assertRaises(ValueError):
                self.validate({"runner_sha256": value})

    def test_config_matches_private_manual_workflow_example(self):
        root = KIT.parents[1]
        workflow = (root / "examples/manual-smoke.yml").read_text()
        self.assertIn(self.cfg["label"], workflow)
        self.assertIn("github.event.repository.private == true", workflow)
        self.assertIn("github.event_name == 'workflow_dispatch'", workflow)
        self.assertNotIn("pull_request:", workflow)

    def test_environment_does_not_modify_process_environment(self):
        before = dict(os.environ)
        env = kit.environment(self.cfg, Path("/Users/buildbot"))
        self.assertEqual(dict(os.environ), before)
        self.assertEqual(env["ANDROID_HOME"], "/Users/buildbot/Library/Android/sdk")
        self.assertIn("ruby@3.3/bin", env["PATH"])


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.target = self.home / "actions-runner"
        self.cfg = kit.config(KIT / "config.example.json")

    def archive(self, bad=None):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w:gz") as tar:
            for name in ("config.sh", "run.sh", "bin/Runner.Listener"):
                info = tarfile.TarInfo(name)
                info.mode = 0o755
                info.size = 2
                tar.addfile(info, io.BytesIO(b"ok"))
            if bad:
                tar.addfile(bad, io.BytesIO(b""))
        return stream.getvalue()

    def download(self, payload):
        self.cfg["runner_sha256"]["arm64"] = hashlib.sha256(payload).hexdigest()
        with patch.object(kit.urllib.request, "urlopen", return_value=io.BytesIO(payload)) as fetch:
            kit.download_runner(self.cfg, self.target, "arm64")
        return fetch

    @unittest.skipUnless(hasattr(tarfile, "data_filter"), "runner CLI requires Python 3.12+")
    def test_verified_archive_installs_without_registration(self):
        fetch = self.download(self.archive())
        self.assertIn("github.com/actions/runner/releases/download/v2.337.0/", fetch.call_args.args[0])
        self.assertTrue((self.target / "run.sh").is_file())
        self.assertEqual(self.target.stat().st_mode & 0o777, 0o700)
        self.assertFalse((self.target / ".runner").exists())
        self.assertEqual(len(list(self.home.iterdir())), 1)

    def test_wrong_checksum_cannot_extract_or_execute(self):
        with patch.object(kit.urllib.request, "urlopen", return_value=io.BytesIO(self.archive())):
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                kit.download_runner(self.cfg, self.target, "arm64")
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.home.iterdir()), [])

    @unittest.skipUnless(hasattr(tarfile, "data_filter"), "runner CLI requires Python 3.12+")
    def test_archive_cannot_escape_destination(self):
        for name in ("../escape", "../../escape"):
            with self.subTest(name=name), self.assertRaises(tarfile.FilterError):
                self.download(self.archive(tarfile.TarInfo(name)))
            self.assertFalse(self.target.exists())

    @unittest.skipUnless(hasattr(tarfile, "data_filter"), "runner CLI requires Python 3.12+")
    def test_archive_cannot_link_outside_destination(self):
        info = tarfile.TarInfo("evil")
        info.type = tarfile.SYMTYPE
        info.linkname = "/tmp/outside-ci"
        with self.assertRaises(tarfile.FilterError):
            self.download(self.archive(info))
        self.assertFalse(self.target.exists())

    def test_existing_directory_is_never_replaced(self):
        self.target.mkdir()
        (self.target / "keep").write_text("personal")
        with self.assertRaises(ValueError):
            kit.download_runner(self.cfg, self.target, "arm64")
        self.assertEqual((self.target / "keep").read_text(), "personal")

    def test_symlinked_runner_directory_is_rejected(self):
        self.target.symlink_to(self.home / "another-location")
        with self.assertRaises(ValueError):
            kit.runner_directory(self.home)

    def test_foreign_registration_is_not_reused(self):
        self.target.mkdir()
        (self.target / ".runner").write_text(json.dumps({"gitHubUrl": "https://github.com/other/repo"}))
        with self.assertRaises(ValueError):
            kit.registration_matches(self.target, self.cfg)


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        previous_language = kit.i18n.LANGUAGE
        self.addCleanup(setattr, kit.i18n, 'LANGUAGE', previous_language)
        kit.i18n.LANGUAGE = 'ru'
        self.cfg = kit.config(KIT / "config.example.json")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.target = self.home / "actions-runner"
        self.target.mkdir()
        (self.target / ".kit-download.json").write_text("{}")
        probe = patch.object(kit, "ios_platform_available", return_value=True)
        self.platform_probe = probe.start()
        self.addCleanup(probe.stop)

    def test_admin_cannot_register_or_start(self):
        with patch.object(kit, "current_ci", return_value=False), patch.object(kit, "run") as run:
            for function in (kit.register, kit.start):
                with self.assertRaises(ValueError):
                    function(self.cfg)
            run.assert_not_called()

    def test_failed_doctor_blocks_start(self):
        with patch.object(kit, "current_ci", return_value=True), patch.object(kit, "doctor", return_value=False), \
                patch.object(kit.subprocess, "Popen") as popen:
            self.assertEqual(kit.start(self.cfg), 1)
            popen.assert_not_called()

    def test_failed_host_doctor_prevents_sudo(self):
        with patch.object(kit.os, "getuid", return_value=501), \
                patch.object(kit, "current_ci", return_value=False), \
                patch.object(kit.os, "getgroups", return_value=[80]), \
                patch.object(kit.grp, "getgrgid", return_value=SimpleNamespace(gr_name="admin")), \
                patch.object(kit, "doctor", return_value=False), patch.object(kit, "run") as run:
            self.assertEqual(kit.setup(self.cfg, KIT / "config.example.json", None), 1)
            run.assert_not_called()

    def test_setup_starts_outside_private_checkout_without_changing_caller_cwd(self):
        caller_cwd = Path.cwd()
        source_sdk = self.home / "private source SDK"
        def provision(args, **kwargs):
            self.assertEqual(kwargs["cwd"], "/")
            self.assertFalse(kwargs["capture"])
            self.assertEqual(args[:2], ["sudo", "/bin/bash"])
            self.assertTrue(all(Path(path).is_absolute() for path in args[2:6]))
            self.assertEqual(args[4], str((KIT / "config.example.json").resolve()))
            self.assertEqual(args[5], str(source_sdk.resolve()))
            # Real shell startup, no sudo/accounts/permissions changes. A stale
            # inherited PWD must not override the accessible physical directory.
            result = subprocess.run(["/bin/bash", "-c", "pwd -P"], cwd=kwargs["cwd"],
                                    env=dict(os.environ, PWD=str(caller_cwd)),
                                    text=True, capture_output=True)
            self.assertEqual(result.stdout.strip(), "/")
            self.assertEqual(result.stderr, "")
            return result
        with patch.object(kit.os, "getuid", return_value=501), \
                patch.object(kit, "current_ci", return_value=False), \
                patch.object(kit.os, "getgroups", return_value=[80]), \
                patch.object(kit.grp, "getgrgid", return_value=SimpleNamespace(gr_name="admin")), \
                patch.object(kit, "doctor", return_value=True), \
                patch.object(kit, "run", side_effect=provision), redirect_stdout(io.StringIO()):
            self.assertEqual(kit.setup(self.cfg, KIT / "config.example.json", str(source_sdk)), 0)
        self.assertEqual(Path.cwd(), caller_cwd)

    def test_doctor_lists_missing_requirements_together(self):
        report = io.StringIO()
        with patch.object(kit.Path, "home", return_value=self.home), \
                patch.object(kit.shutil, "which", return_value=None), \
                patch.object(kit.shutil, "disk_usage", return_value=SimpleNamespace(free=19 * 1024**3)), \
                patch.object(kit, "output", return_value=(False, "")), redirect_stdout(report):
            self.assertFalse(kit.doctor(self.cfg, host=True))
        for missing in ("Команда python3", "Команда gh", "Ruby", "Xcode", "Android SDK", "Место"):
            self.assertIn(missing, report.getvalue())

    def test_missing_ci_keychain_does_not_require_desktop_login(self):
        cfg = dict(self.cfg, platforms=["ios"])
        report = io.StringIO()
        def output(args, env):
            if args[0] == "ruby":
                return True, "3.3.12"
            if args[0] == "xcodebuild" and args[1] == "-version":
                return True, "Xcode 26.3\nBuild version 17C529"
            return True, ""
        with patch.object(kit.Path, "home", return_value=self.home), \
                patch.object(kit, "current_ci", return_value=True), \
                patch.object(kit, "default_keychain", return_value=[]), \
                patch.object(kit.platform, "system", return_value="Darwin"), \
                patch.object(kit.platform, "machine", return_value="arm64"), \
                patch.object(kit.shutil, "which", return_value="/usr/bin/tool"), \
                patch.object(kit.shutil, "disk_usage", return_value=SimpleNamespace(free=30 * 1024**3)), \
                patch.object(kit, "output", side_effect=output), redirect_stdout(report):
            self.assertTrue(kit.doctor(cfg))
        self.assertIn("workflow сам управляет временной связкой", report.getvalue())
        self.assertTrue(report.getvalue().endswith("Проверка окружения пройдена.\n"))
        self.assertNotIn("Первый раз войди на рабочий стол", report.getvalue())

    def test_sdk_metadata_without_build_destination_does_not_pass_doctor(self):
        cfg = dict(self.cfg, platforms=["ios"])
        report = io.StringIO()
        self.platform_probe.return_value = False
        def output(args, env):
            if args[0] == "ruby":
                return True, "3.3.12"
            if args[0] == "xcodebuild" and args[1] == "-version":
                return True, "Xcode 26.3\nBuild version 17C529"
            return True, "26.2"
        with patch.object(kit.Path, "home", return_value=self.home), \
                patch.object(kit, "current_ci", return_value=True), \
                patch.object(kit, "default_keychain", return_value=[]), \
                patch.object(kit.platform, "system", return_value="Darwin"), \
                patch.object(kit.platform, "machine", return_value="arm64"), \
                patch.object(kit.shutil, "which", return_value="/usr/bin/tool"), \
                patch.object(kit.shutil, "disk_usage", return_value=SimpleNamespace(free=30 * 1024**3)), \
                patch.object(kit, "output", side_effect=output), redirect_stdout(report):
            self.assertFalse(kit.doctor(cfg))
        self.assertIn("установи поддержку iOS", report.getvalue())
        self.assertNotIn("Проверка окружения пройдена.", report.getvalue())

    def test_unavailable_keychain_api_blocks_doctor(self):
        report = io.StringIO()
        with patch.object(kit.Path, "home", return_value=self.home), \
                patch.object(kit, "current_ci", return_value=True), \
                patch.object(kit, "default_keychain", side_effect=OSError("OSStatus -25308")), \
                patch.object(kit, "output", return_value=(False, "")), redirect_stdout(report):
            self.assertFalse(kit.doctor(dict(self.cfg, platforms=["ios"])))
        self.assertIn("OSStatus -25308", report.getvalue())

    def test_non_root_provisioning_cannot_mutate_accounts(self):
        if os.getuid() == 0 or sys.platform != "darwin":
            self.skipTest("macOS non-root guard")
        result = subprocess.run(["/bin/bash", str(KIT / "provision.sh"), str(KIT),
                                 str(KIT / "config.example.json"), str(self.home)], text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("требует macOS и sudo", result.stderr)

    def test_registration_uses_hidden_input_and_official_unattended_mode(self):
        with patch.object(kit, "current_ci", return_value=True), patch.object(kit, "doctor", return_value=True), \
                patch.object(kit.Path, "home", return_value=self.home), \
                patch.object(kit, "registration_matches", side_effect=[False, True]), \
                patch.object(kit, "read_registration_token", return_value="fixture-not-a-real-token"), \
                patch.object(kit, "run", return_value=subprocess.CompletedProcess([], 0)) as run:
            self.assertEqual(kit.register(self.cfg), 0)
            args = run.call_args.args[0]
            self.assertEqual(args[:2], ["/bin/bash", "./config.sh"])
            self.assertIn("local-macos", args)
            self.assertIn("--unattended", args)
            for forbidden in ("--token", "--replace", "--ephemeral", "fixture-not-a-real-token"):
                self.assertNotIn(forbidden, args)
            self.assertNotIn("ACTIONS_RUNNER_INPUT_TOKEN", run.call_args.kwargs["env"])

    def test_bom_registration_is_reused_without_token_or_network(self):
        record = self.target / ".runner"
        record.write_text(json.dumps({"gitHubUrl": "https://github.com/" + self.cfg["repository"]}),
                          encoding="utf-8-sig")
        before = record.read_bytes()
        with patch.object(kit, "current_ci", return_value=True), patch.object(kit, "doctor", return_value=True), \
                patch.object(kit.Path, "home", return_value=self.home), \
                patch.object(kit, "read_registration_token") as prompt, patch.object(kit, "run") as run:
            self.assertEqual(kit.register(self.cfg), 0)
            prompt.assert_not_called()
            run.assert_not_called()
        self.assertEqual(record.read_bytes(), before)

    def test_bom_foreign_and_malformed_registration_still_fail_closed(self):
        for data in ('{"gitHubUrl":"https://github.com/another/repo"}', 'not-json', '[]'):
            (self.target / ".runner").write_text(data, encoding="utf-8-sig")
            with self.assertRaises(ValueError):
                kit.registration_matches(self.target, self.cfg)

    def test_second_listener_is_not_started(self):
        with patch.object(kit, "current_ci", return_value=True), patch.object(kit, "doctor", return_value=True), \
                patch.object(kit.Path, "home", return_value=self.home), \
                patch.object(kit, "registration_matches", return_value=True), \
                patch.object(kit, "output", return_value=(True, "12345")), \
                patch.object(kit.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(ValueError, "Runner.Listener"):
                kit.start(self.cfg)
            popen.assert_not_called()

    def test_start_runs_foreground_official_runner_and_waits(self):
        with patch.object(kit, "current_ci", return_value=True), patch.object(kit, "doctor", return_value=True), \
                patch.object(kit.Path, "home", return_value=self.home), \
                patch.object(kit, "registration_matches", return_value=True), \
                patch.object(kit, "output", return_value=(False, "")), \
                patch.object(kit.subprocess, "Popen") as popen:
            popen.return_value.wait.side_effect = [KeyboardInterrupt(), 0]
            self.assertEqual(kit.start(self.cfg), 0)
            self.assertEqual(popen.call_args.args[0], ["/usr/bin/caffeinate", "-i", "/bin/bash", "./run.sh"])
            self.assertNotIn("start_new_session", popen.call_args.kwargs)
            self.assertEqual(popen.return_value.wait.call_count, 2)

    @unittest.skipUnless(sys.platform == "darwin", "real macOS caffeinate/SIGINT lifecycle")
    def test_real_ctrl_c_waits_for_child_cleanup_without_github(self):
        # Isolated process group and a fake run.sh: no account, token or job.
        (self.target / "run.sh").write_text(
            "#!/bin/bash\ntrap 'sleep 1; echo stopped > stopped; exit 0' INT\n"
            "echo TEST_RUNNER_READY\nwhile :; do sleep 0.1; done\n")
        code = f"""import importlib.util, pathlib, sys
sys.path.insert(0, {str(KIT)!r})
s = importlib.util.spec_from_file_location('kit', {str(KIT / 'runner.py')!r})
k = importlib.util.module_from_spec(s); s.loader.exec_module(k)
k.require_ci = lambda c: None
k.doctor = lambda c: True
k.registration_matches = lambda *a: True
k.output = lambda *a: (False, '')
k.Path.home = lambda: pathlib.Path({str(self.home)!r})
raise SystemExit(k.start(k.config(pathlib.Path({str(KIT / 'config.example.json')!r}))))
"""
        child = subprocess.Popen([sys.executable, "-u", "-c", code], stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, start_new_session=True)
        try:
            log = b""
            deadline = time.monotonic() + 10
            while b"TEST_RUNNER_READY" not in log:
                self.assertLess(time.monotonic(), deadline, log.decode())
                if select.select([child.stdout], [], [], 0.1)[0]:
                    chunk = os.read(child.stdout.fileno(), 8192)
                    self.assertTrue(chunk, log.decode())
                    log += chunk
            os.killpg(child.pid, signal.SIGINT)
            child.wait(timeout=10)
            self.assertTrue((self.target / "stopped").is_file(), "CLI exited before child cleanup")
            self.assertEqual(child.returncode, 0)
        finally:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait()
            child.stdout.close()

    def test_provisioning_has_no_admin_grants_or_background_service(self):
        script = (KIT / "provision.sh").read_text()
        for forbidden in ("dseditgroup", "sudoers", "chmod -R 777", "svc.sh", "launchctl", "secureTokenOn"):
            self.assertNotIn(forbidden, script)
        self.assertIn("createhomedir -c -l -u", script)
        self.assertIn("-shell /bin/zsh -password -", script)
        self.assertNotIn('chown -R', script)
        self.assertIn('/usr/bin/sudo -H -u "$ci_user" /usr/bin/env COPYFILE_DISABLE=1 /usr/bin/tar', script)
        self.assertIn('previous_repository', script)
        self.assertIn('required_kib + source_kib', script)
        self.assertIn("source SDK", (KIT / "OPERATIONS.md").read_text())


if __name__ == "__main__":
    unittest.main()
