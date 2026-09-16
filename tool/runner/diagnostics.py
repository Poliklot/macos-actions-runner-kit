"""Structured, serializable readiness diagnostics."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from typing import Iterable

from i18n import tr


@dataclass(frozen=True)
class CheckResult:
    """One stable readiness check with human remediation metadata."""

    check_id: str
    ok: bool
    title: str
    remediation: str
    detected: str | None = None
    cause: str | None = None
    verify: str | None = None
    actor: str | None = None
    docs: str | None = None

    def json_value(self) -> dict:
        value = asdict(self)
        value["id"] = value.pop("check_id")
        value["status"] = "ok" if self.ok else "required"
        return {key: item for key, item in value.items() if item is not None}


class Report:
    """A complete doctor result that can be rendered without rerunning probes."""

    def __init__(self, checks: Iterable[CheckResult], *, host: bool):
        self.checks = list(checks)
        self.host = host

    @property
    def ready(self) -> bool:
        return all(check.ok for check in self.checks)

    def matching(self, selector: str) -> list[CheckResult]:
        return [check for check in self.checks
                if check.check_id == selector or check.check_id.startswith(selector + ".")]

    def json_text(self) -> str:
        return json.dumps({
            "schema": 1,
            "ready": self.ready,
            "scope": "host" if self.host else "ci",
            "checks": [check.json_value() for check in self.checks],
        }, ensure_ascii=False, sort_keys=True, indent=2) + "\n"

    def human_text(self, *, verbose: bool = False, explain: str | None = None,
                   docker_host_note: bool = False) -> str:
        checks = self.checks if explain is None else self.matching(explain)
        if explain is not None and not checks:
            raise ValueError(tr("diagnostic_unknown", explain))
        lines: list[str] = []
        for check in checks:
            status = "OK" if check.ok else tr("needs_attention")
            lines.append(f"{status}  {check.title} [{check.check_id}]")
            show_details = verbose or explain is not None or not check.ok
            if show_details:
                fields = (
                    (tr("diagnostic_detected"), check.detected),
                    (tr("diagnostic_cause"), check.cause if not check.ok else None),
                    (tr("diagnostic_fix"), check.remediation if not check.ok else None),
                    (tr("diagnostic_actor"), check.actor if not check.ok else None),
                    (tr("diagnostic_verify"), check.verify if not check.ok else None),
                    (tr("diagnostic_docs"), check.docs if not check.ok else None),
                )
                for label, value in fields:
                    if value:
                        lines.append(f"       {label}: {value}")
        if docker_host_note and explain is None:
            lines.append(tr("docker_ci_only"))
        if explain is None:
            lines.extend(("", tr("doctor_ok") if self.ready else tr("doctor_failed")))
        return "\n".join(lines) + "\n"
