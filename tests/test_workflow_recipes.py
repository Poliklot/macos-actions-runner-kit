"""Reusable workflow recipes remain portable, bounded and secret-free."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PORTABILITY = ROOT / "examples/scripts/check-macos-portability.py"
ACTIONLINT = ROOT / "examples/scripts/install-actionlint-macos.sh"
SIGNING = ROOT / "examples/scripts/apple-signing-keychain.py"


class PortabilityRecipeTests(unittest.TestCase):
    def run_check(self, root, *paths):
        return subprocess.run([sys.executable, str(PORTABILITY), "--root", str(root), *paths],
                              text=True, capture_output=True)

    def test_relative_lockfile_passes_and_developer_home_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lock = root / "ios/Podfile.lock"
            lock.parent.mkdir()
            lock.write_text("PODS:\n  - LocalSDK (1.2.3):\n    :path: vendor/LocalSDK\n")
            self.assertEqual(self.run_check(root, "ios/Podfile.lock").returncode, 0)
            lock.write_text("PODS:\n  :path: /Users/developer/project/ios/vendor/LocalSDK\n")
            failed = self.run_check(root, "ios/Podfile.lock")
            self.assertEqual(failed.returncode, 1)
            self.assertIn("developer-home path", failed.stderr)
            self.assertIn("Podfile.lock:2", failed.stderr)

    def test_escaping_broken_and_missing_paths_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir()
            outside = Path(directory) / "outside"
            outside.write_text("fixture")
            (root / "escape").symlink_to(outside)
            (root / "broken").symlink_to(root / "absent")
            for name, expected in (("escape", "escapes repository"),
                                   ("broken", "broken symlink"),
                                   ("missing", "missing selected path")):
                result = self.run_check(root, name)
                self.assertEqual(result.returncode, 1)
                self.assertIn(expected, result.stderr)


class ActionlintRecipeTests(unittest.TestCase):
    def test_installer_is_valid_bash_and_has_portable_checksum_fallback(self):
        result = subprocess.run(["/bin/bash", "-n", str(ACTIONLINT)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        source = ACTIONLINT.read_text()
        self.assertIn("shasum -a 256", source)
        self.assertIn("sha256sum", source)
        self.assertNotIn("sudo", source)
        self.assertNotIn("curl |", source)
        self.assertRegex(source, r"arm64_sha256=[0-9a-f]{64}")
        self.assertRegex(source, r"amd64_sha256=[0-9a-f]{64}")


class AppleSigningRecipeTests(unittest.TestCase):
    def test_requires_explicit_command_and_never_modifies_system_keychain(self):
        result = subprocess.run([sys.executable, str(SIGNING)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 2)
        source = SIGNING.read_text()
        self.assertIn('Path.home() / "Library/Caches"', source)
        self.assertIn('APPLE_WWDR_SHA256', source)
        self.assertNotIn('/Library/Keychains/System.keychain', source)
        self.assertNotIn('sudo', source)

    def test_invalid_state_name_fails_before_security_or_secret_access(self):
        import os
        env = dict(os.environ, SIGNING_STATE_NAME='../escape-signing-state')
        result = subprocess.run([sys.executable, str(SIGNING), 'cleanup'], env=env,
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn('SIGNING_STATE_NAME', result.stderr)


if __name__ == "__main__":
    unittest.main()
