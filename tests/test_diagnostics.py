"""Structured doctor output is stable, actionable and safe to automate."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tool/runner"))
from diagnostics import CheckResult, RemediationPlan, Report
import configuration
import i18n
import runner
import workloads


class ReportTests(unittest.TestCase):
    def test_json_has_stable_ids_status_and_no_null_fields(self):
        report = Report([
            CheckResult("host.macos", True, "macOS", "Install macOS", detected="Darwin"),
            CheckResult("tool.gpg", False, "gpg", "Install GPG", cause="not found",
                        actor="Mac administrator", verify="command -v gpg"),
        ], host=False)
        value = json.loads(report.json_text())
        self.assertEqual(value["schema"], 1)
        self.assertFalse(value["ready"])
        self.assertEqual(value["scope"], "ci")
        self.assertEqual(value["checks"][0]["id"], "host.macos")
        self.assertEqual(value["checks"][0]["status"], "ok")
        self.assertNotIn("cause", value["checks"][0])
        self.assertEqual(value["checks"][1]["status"], "required")

    def test_human_failure_answers_what_why_who_fix_and_verify(self):
        report = Report([CheckResult(
            "docker.daemon", False, "Docker daemon", "Start isolated Colima",
            detected="unix:///Users/ci/.colima/docker.sock", cause="socket missing",
            actor="CI user", verify="docker info",
        )], host=False)
        output = report.human_text()
        for text in ("[docker.daemon]", "unix:///Users/ci/.colima/docker.sock",
                     "socket missing", "Start isolated Colima", "CI user", "docker info"):
            self.assertIn(text, output)

    def test_explain_accepts_group_and_rejects_unknown_id(self):
        report = Report([
            CheckResult("docker.cli", True, "Docker CLI", "Install it"),
            CheckResult("docker.daemon", False, "Docker daemon", "Start it"),
            CheckResult("runtime.node", True, "Node", "Install it"),
        ], host=False)
        output = report.human_text(explain="docker")
        self.assertIn("docker.cli", output)
        self.assertIn("docker.daemon", output)
        self.assertNotIn("runtime.node", output)
        with self.assertRaisesRegex(ValueError, "missing.check"):
            report.human_text(explain="missing.check")


class RemediationPlanTests(unittest.TestCase):
    def test_plan_contains_only_failures_and_deduplicates_same_action(self):
        report = Report([
            CheckResult("runtime.node", False, "Node", "Install Node", detected="missing",
                        actor="administrator", verify="node --version"),
            CheckResult("runtime.npm", False, "npm", "Install Node", detected="missing",
                        actor="administrator", verify="npm --version"),
            CheckResult("tool.git", True, "git", "Install git"),
            CheckResult("docker.daemon", False, "Docker", "Start Colima", cause="socket missing",
                        actor="CI user", verify="docker info"),
        ], host=False)
        plan = RemediationPlan(report)
        self.assertEqual(len(plan.steps), 2)
        self.assertEqual(plan.steps[0]["checks"], ["runtime.node", "runtime.npm"])
        self.assertNotIn("tool.git", plan.human_text())
        value = json.loads(plan.json_text())
        self.assertFalse(value["ready"])
        self.assertEqual(value["steps"][0]["number"], 1)
        self.assertEqual(value["steps"][1]["actor"], "CI user")

    def test_empty_plan_is_ready(self):
        plan = RemediationPlan(Report([CheckResult("host.macos", True, "macOS", "fix")], host=True))
        self.assertTrue(plan.ready)
        self.assertEqual(json.loads(plan.json_text())["steps"], [])


class DoctorOutputTests(unittest.TestCase):
    def setUp(self):
        base = runner.config(runner.ROOT / "config.example.json")
        self.cfg = configuration.for_profile(base, "generic")

    def patches(self):
        return (
            patch.object(runner.platform, "system", return_value="Darwin"),
            patch.object(runner.platform, "machine", return_value="arm64"),
            patch.object(runner.shutil, "disk_usage", return_value=SimpleNamespace(free=30 * 1024**3)),
            patch.object(runner.shutil, "which", return_value="/fixture/tool"),
            patch.object(runner, "current_ci", return_value=True),
        )

    def test_doctor_json_is_machine_readable_and_does_not_include_inherited_secret(self):
        stream = io.StringIO()
        patches = self.patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
                patch.dict(runner.os.environ, {"SECRET_FIXTURE": "must-not-leak"}), \
                redirect_stdout(stream):
            self.assertTrue(runner.doctor(self.cfg, json_output=True))
        self.assertNotIn("must-not-leak", stream.getvalue())
        value = json.loads(stream.getvalue())
        self.assertTrue(value["ready"])
        self.assertEqual(value["scope"], "ci")
        self.assertIn("host.macos", [check["id"] for check in value["checks"]])

    def test_verbose_lists_detected_values_and_stable_ids(self):
        stream = io.StringIO()
        patches = self.patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4], redirect_stdout(stream):
            self.assertTrue(runner.doctor(self.cfg, verbose=True))
        self.assertIn("[host.macos]", stream.getvalue())
        self.assertIn("Darwin", stream.getvalue())
        self.assertIn("[tool.python3]", stream.getvalue())

    def test_known_tool_remediation_is_copy_pasteable(self):
        previous = i18n.LANGUAGE
        self.addCleanup(setattr, i18n, "LANGUAGE", previous)
        i18n.LANGUAGE = "ru"
        self.assertIn("brew install gnupg", runner.tool_install_guidance("gpg"))
        self.assertIn("brew install actionlint", runner.tool_install_guidance("actionlint"))
        self.assertIn("brew install shellcheck", runner.tool_install_guidance("shellcheck"))
        self.assertNotIn("brew install gnupg", runner.tool_install_guidance("terraform"))

    def test_docker_failure_explains_detected_context_and_colima_action(self):
        previous = i18n.LANGUAGE
        self.addCleanup(setattr, i18n, "LANGUAGE", previous)
        i18n.LANGUAGE = "ru"
        cfg = configuration.for_profile(runner.config(runner.ROOT / "config.example.json"),
                                        "backend", node="24")
        docker = workloads.DockerProbe(
            "socket-missing", cli="/opt/homebrew/bin/docker", context="colima",
            host="unix:///Users/ci_backend/.colima/default/docker.sock",
            socket="/Users/ci_backend/.colima/default/docker.sock", providers=("colima",),
        )
        stream = io.StringIO()
        patches = self.patches()
        def output(args, _env):
            if args == ["node", "--version"]:
                return True, "v24.1.0"
            if args == ["npm", "--version"]:
                return True, "11.0.0"
            self.fail("Unexpected command: " + repr(args))
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
                patch.object(runner, "output", side_effect=output), \
                patch.object(workloads, "docker_probe", return_value=docker), \
                redirect_stdout(stream):
            self.assertFalse(runner.doctor(cfg, explain="docker"))
        value = stream.getvalue()
        self.assertIn("context: colima", value)
        self.assertIn("socket", value)
        self.assertIn("colima start --runtime docker", value)
        self.assertIn("CI-пользователь", value)


if __name__ == "__main__":
    unittest.main()
