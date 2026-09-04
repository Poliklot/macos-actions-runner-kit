"""SDK metadata must not substitute for an actual Xcode destination check."""
from pathlib import Path
import plistlib
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tool" / "runner"))
import ios_platform_probe as probe


class IOSPlatformProbeTests(unittest.TestCase):
    def test_real_destination_is_requested_without_signing_or_dependency_manager(self):
        roots = []
        def execute(args, **kwargs):
            root = kwargs["cwd"]
            roots.append(root)
            self.assertEqual(args[0], "xcodebuild")
            self.assertIn("generic/platform=iOS", args)
            self.assertIn("-showBuildSettings", args)
            self.assertIn("CODE_SIGNING_ALLOWED=NO", args)
            self.assertIn("CODE_SIGNING_REQUIRED=NO", args)
            self.assertEqual(kwargs["timeout"], 60)
            data = plistlib.loads((root / "Probe.xcodeproj/project.pbxproj").read_bytes())
            self.assertEqual(data["objects"][data["rootObject"]]["isa"], "PBXProject")
            return SimpleNamespace(returncode=0)
        with patch.object(probe.subprocess, "run", side_effect=execute):
            self.assertTrue(probe.available({"DEVELOPER_DIR": "/fixture/Xcode"}))
        self.assertFalse(roots[0].exists())

    def test_missing_platform_or_failed_xcode_is_rejected(self):
        with patch.object(probe.subprocess, "run", return_value=SimpleNamespace(returncode=64)):
            self.assertFalse(probe.available())

    def test_timeout_and_missing_executable_are_rejected(self):
        for error in (FileNotFoundError(), subprocess.TimeoutExpired("xcodebuild", 60)):
            with patch.object(probe.subprocess, "run", side_effect=error):
                self.assertFalse(probe.available())


if __name__ == "__main__":
    unittest.main()
