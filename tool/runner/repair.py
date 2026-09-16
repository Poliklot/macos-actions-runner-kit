"""Allowlisted, non-privileged remediation actions for the dedicated CI account."""
from __future__ import annotations

from dataclasses import dataclass
import shutil

from i18n import tr


@dataclass(frozen=True)
class RepairAction:
    action_id: str
    title: str
    checks: tuple[str, ...]


def available(report) -> list[RepairAction]:
    grouped: dict[str, list[str]] = {}
    for check in report.checks:
        if not check.ok and check.automation:
            grouped.setdefault(check.automation, []).append(check.check_id)
    actions = []
    for action_id, checks in grouped.items():
        if action_id == "docker.colima":
            actions.append(RepairAction(action_id, tr("repair_docker_colima"), tuple(checks)))
    return actions


def apply(action: RepairAction, *, env: dict, run) -> tuple[bool, str]:
    """Apply one known CI-owned change. Never invokes sudo, package managers or deletion."""
    if action.action_id != "docker.colima":
        return False, tr("repair_unknown", action.action_id)
    colima = shutil.which("colima", path=env.get("PATH"))
    docker = shutil.which("docker", path=env.get("PATH"))
    if not colima or not docker:
        return False, tr("repair_tools_missing")
    started = run([colima, "start", "--runtime", "docker"], env=env, capture=False, timeout=600)
    if started.returncode:
        return False, tr("repair_command_failed", "colima start")
    selected = run([docker, "context", "use", "colima"], env=env, capture=False, timeout=30)
    if selected.returncode:
        return False, tr("repair_command_failed", "docker context use colima")
    return True, tr("repair_docker_done")
