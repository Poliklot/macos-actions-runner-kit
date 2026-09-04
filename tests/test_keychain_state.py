"""Framework query tests; the only real framework test is strictly read-only."""
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tool" / "runner"))
import keychain_state as state


class DefaultKeychainTests(unittest.TestCase):
    def setUp(self):
        self.security = SimpleNamespace(SecKeychainCopyDomainDefault=Mock(return_value=0),
                                        SecKeychainGetPath=Mock(return_value=0))
        self.core = SimpleNamespace(CFRelease=Mock())
        self.loader = patch.object(state.ctypes, "CDLL", side_effect=[self.security, self.core])

    def test_no_default_status_is_not_an_error(self):
        self.security.SecKeychainCopyDomainDefault.return_value = -25307
        with self.loader:
            self.assertEqual(state.default_keychain(), [])
        self.security.SecKeychainGetPath.assert_not_called()
        self.core.CFRelease.assert_not_called()

    def test_other_osstatus_is_not_silently_ignored(self):
        self.security.SecKeychainCopyDomainDefault.return_value = -25308
        with self.loader, self.assertRaisesRegex(OSError, "OSStatus -25308"):
            state.default_keychain()

    def test_success_with_null_reference_is_absent(self):
        with self.loader:
            self.assertEqual(state.default_keychain(), [])

    def reference(self, domain, ref):
        self.assertEqual(domain, 0)
        ref._obj.value = 123
        return 0

    def test_path_with_spaces_and_unicode_releases_reference(self):
        path = "/Users/ci/Library/Keychains/сборка test.keychain-db"
        def write_path(ref, length, buffer):
            buffer.value = path.encode()
            length._obj.value = len(path.encode())
            return 0
        self.security.SecKeychainCopyDomainDefault.side_effect = self.reference
        self.security.SecKeychainGetPath.side_effect = write_path
        with self.loader:
            self.assertEqual(state.default_keychain(), [path])
        self.core.CFRelease.assert_called_once()

    def test_path_error_still_releases_reference(self):
        self.security.SecKeychainCopyDomainDefault.side_effect = self.reference
        self.security.SecKeychainGetPath.return_value = -50
        with self.loader, self.assertRaisesRegex(OSError, "SecKeychainGetPath"):
            state.default_keychain()
        self.core.CFRelease.assert_called_once()

    @unittest.skipUnless(sys.platform == "darwin", "read-only macOS framework comparison")
    def test_real_read_only_query_matches_security_cli(self):
        paths = state.default_keychain()
        result = subprocess.run(["/usr/bin/security", "default-keychain", "-d", "user"],
                                text=True, capture_output=True, check=False)
        if paths:
            self.assertEqual(result.returncode, 0)
            self.assertEqual(json.loads(result.stdout.strip()), paths[0])
        else:
            self.assertTrue(result.returncode != 0 or result.stdout.strip() in ("", "<NULL>"))


if __name__ == "__main__":
    unittest.main()
