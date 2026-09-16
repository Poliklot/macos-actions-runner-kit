"""Verified terminal UI bundle, interactive repair flow and plain fallback."""
from contextlib import redirect_stdout
import hashlib
import io
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tool/runner"))
from diagnostics import CheckResult, Report
import repair
import runner
import ui


class UiBundleTests(unittest.TestCase):
    def test_vendored_wheels_match_pins_and_questionary_imports(self):
        for name, expected in ui.WHEELS.items():
            path = runner.ROOT / "vendor" / name
            self.assertTrue(path.is_file())
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), expected)
        self.assertIsNotNone(ui.questionary)
        self.assertEqual(ui.questionary.__version__, "2.1.1")

    def test_plain_output_fallback_keeps_semantic_symbols(self):
        output = io.StringIO()
        with patch.object(ui, "interactive", return_value=False), redirect_stdout(output):
            ui.title("Runner")
            ui.success("ready")
            ui.warning("attention")
            ui.error("failed")
        for value in ("◆ Runner", "✓ ready", "! attention", "✗ failed"):
            self.assertIn(value, output.getvalue())

    def test_startup_summary_separates_automatic_and_manual_failures(self):
        report = Report([
            CheckResult("host.macos", True, "macOS", ""),
            CheckResult("docker.daemon", False, "Docker", "fix", automation="docker.colima"),
            CheckResult("runtime.java", False, "Java", "install"),
        ], host=False)
        output = io.StringIO()
        with patch.object(ui, "interactive", return_value=False), redirect_stdout(output):
            ui.show_readiness_summary(report)
        self.assertIn("1", output.getvalue())
        self.assertIn("Docker", output.getvalue())
        self.assertIn("Java", output.getvalue())
        self.assertRegex(output.getvalue(), r"авто|automatic")


class RepairActionTests(unittest.TestCase):
    def report(self):
        return Report([CheckResult("docker.daemon", False, "Docker", "Start Colima",
                                  automation="docker.colima")], host=False)

    def test_only_allowlisted_action_is_offered_and_runs_without_sudo(self):
        actions = repair.available(self.report())
        self.assertEqual([action.action_id for action in actions], ["docker.colima"])
        run = Mock(side_effect=[SimpleNamespace(returncode=0), SimpleNamespace(returncode=0)])
        with patch.object(repair.shutil, "which", side_effect=["/opt/homebrew/bin/colima",
                                                               "/opt/homebrew/bin/docker"]):
            ok, _ = repair.apply(actions[0], env={"PATH": "/opt/homebrew/bin"}, run=run)
        self.assertTrue(ok)
        self.assertEqual(run.call_args_list[0].args[0],
                         ["/opt/homebrew/bin/colima", "start", "--runtime", "docker"])
        self.assertEqual(run.call_args_list[1].args[0],
                         ["/opt/homebrew/bin/docker", "context", "use", "colima"])
        self.assertFalse(any("sudo" in arg for call in run.call_args_list for arg in call.args[0]))

    def test_unknown_action_is_refused_without_execution(self):
        run = Mock()
        ok, message = repair.apply(repair.RepairAction("unknown", "unknown", ()), env={}, run=run)
        self.assertFalse(ok)
        self.assertIn("unknown", message)
        run.assert_not_called()


class InteractiveReadinessTests(unittest.TestCase):
    def setUp(self):
        self.cfg = runner.configuration.for_profile(
            runner.config(runner.ROOT / "config.example.json"), "backend")
        self.failed = Report([CheckResult("docker.daemon", False, "Docker", "Start Colima",
                                          automation="docker.colima")], host=False)
        self.ready = Report([CheckResult("docker.daemon", True, "Docker", "")], host=False)

    def test_user_can_apply_automatic_fix_then_continue(self):
        with patch.object(runner, "doctor_report", side_effect=[self.failed, self.ready]), \
                patch.object(runner.ui, "show_readiness_summary"), \
                patch.object(runner.ui, "interactive", return_value=True), \
                patch.object(runner.ui, "select", return_value="automatic"), \
                patch.object(runner.ui, "title"), patch.object(runner.ui, "info"), \
                patch.object(runner.ui, "success"), \
                patch.object(runner, "environment", return_value={"PATH": "/fixture"}), \
                patch.object(runner.repair, "apply", return_value=(True, "fixed")) as apply:
            self.assertTrue(runner.interactive_readiness(self.cfg))
        apply.assert_called_once()

    def test_cancel_never_applies_changes(self):
        with patch.object(runner, "doctor_report", return_value=self.failed), \
                patch.object(runner.ui, "show_readiness_summary"), \
                patch.object(runner.ui, "interactive", return_value=True), \
                patch.object(runner.ui, "select", return_value="cancel"), \
                patch.object(runner.ui, "warning"), patch.object(runner.repair, "apply") as apply:
            self.assertIsNone(runner.interactive_readiness(self.cfg))
        apply.assert_not_called()


class ConfigureWizardTests(unittest.TestCase):
    def test_configure_without_flags_uses_interactive_questionary_flow(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            with patch.object(runner.ui, "interactive", return_value=True), \
                    patch.object(runner.ui, "input_text",
                                 side_effect=["sample-org/private-app", "local-mobile", "ci_mobile"]), \
                    patch.object(runner.ui, "select", return_value="mobile"), \
                    patch.object(runner.ui, "title"), patch.object(runner.ui, "success"), \
                    patch.object(runner.os, "getuid", return_value=501):
                self.assertEqual(runner.main(["--config", str(path), "configure"]), 0)
            value = runner.config(path)
            self.assertEqual(value["repository"], "sample-org/private-app")
            self.assertEqual(value["ci_user"], "ci_mobile")
            self.assertEqual(value["label"], "local-mobile")
            self.assertEqual(value["capabilities"], ["android", "ios", "java", "ruby"])


if __name__ == "__main__":
    unittest.main()
