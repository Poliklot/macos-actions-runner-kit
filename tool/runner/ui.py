"""Terminal UI with a verified Questionary bundle and a dependency-free fallback."""
from __future__ import annotations

import hashlib
import getpass
import os
from pathlib import Path
import sys

from i18n import tr

ROOT = Path(__file__).resolve().parent
WHEELS = {
    "questionary-2.1.1-py3-none-any.whl":
        "a51af13f345f1cdea62347589fbb6df3b290306ab8930713bfae4d475a7d4a59",
    "prompt_toolkit-3.0.53-py3-none-any.whl":
        "01c0891d7f9237d5e339f7d3e42cdae80b7534abb1c7c0e3352efba6231492f2",
    "wcwidth-0.8.3-py3-none-any.whl":
        "d5b73dba6158a595ec9370350e7f2637bcac8d6c5e4fde34f30fcffb6103a5e4",
}


def _verified_vendor_paths() -> list[str]:
    result = []
    for name, expected in WHEELS.items():
        path = ROOT / "vendor" / name
        try:
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            return []
        if actual != expected:
            return []
        result.append(str(path))
    return result


for _path in reversed(_verified_vendor_paths()):
    if _path not in sys.path:
        sys.path.insert(0, _path)

try:
    import questionary
    from questionary import Choice
except ImportError:  # The operational CLI remains usable if the optional UI bundle is unavailable.
    questionary = None
    Choice = None


STYLE = questionary.Style([
    ("qmark", "fg:#7c3aed bold"),
    ("question", "bold"),
    ("answer", "fg:#16a34a bold"),
    ("pointer", "fg:#7c3aed bold"),
    ("highlighted", "fg:#7c3aed bold"),
    ("selected", "fg:#16a34a"),
    ("instruction", "fg:#6b7280"),
    ("text", ""),
    ("disabled", "fg:#9ca3af italic"),
]) if questionary else None


def interactive() -> bool:
    return bool(sys.stdin.isatty() and sys.stdout.isatty() and os.environ.get("TERM") != "dumb")


def _styled(text: str, style: str = "", *, file=None) -> None:
    file = file or sys.stdout
    if questionary and interactive():
        questionary.print(text, style=style, file=file)
    else:
        print(text, file=file)


def _prefixed(prefix: str, value: str, continuation: str = "  ") -> str:
    lines = value.splitlines() or [""]
    return "\n".join((prefix if index == 0 else continuation) + line
                     for index, line in enumerate(lines))


def title(text: str, *, file=None) -> None:
    _styled("\n" + _prefixed("◆ ", text), "bold fg:#7c3aed", file=file)


def success(text: str, *, file=None) -> None:
    _styled(_prefixed("✓ ", text), "fg:#16a34a", file=file)


def warning(text: str, *, file=None) -> None:
    _styled(_prefixed("! ", text), "fg:#d97706", file=file)


def error(text: str, *, file=None) -> None:
    _styled(_prefixed("✗ ", text), "fg:#dc2626", file=file)


def info(text: str, *, file=None) -> None:
    _styled(_prefixed("  ", text), "fg:#6b7280", file=file)


def text(value: str, *, end: str = "\n", file=None) -> None:
    file = file or sys.stdout
    if end == "\n":
        _styled(value, file=file)
    else:
        print(value, end=end, file=file)


def select(message: str, choices: list[tuple[str, str]], *, default: str | None = None) -> str | None:
    """Return the stable value, or None on Ctrl+C/EOF."""
    if questionary and interactive():
        rendered = [Choice(label, value=value) for label, value in choices]
        try:
            return questionary.select(message, choices=rendered, default=default, style=STYLE,
                                      use_shortcuts=True, use_indicator=True,
                                      instruction=tr("ui_select_instruction")).ask()
        except (KeyboardInterrupt, EOFError):
            return None
    if not interactive():
        return None
    print(message)
    for index, (label, _) in enumerate(choices, 1):
        print(f"  {index}. {label}")
    while True:
        try:
            value = input("> ").strip()
        except (KeyboardInterrupt, EOFError):
            return None
        if value.isdigit() and 1 <= int(value) <= len(choices):
            return choices[int(value) - 1][1]
        warning(tr("ui_invalid_choice"))


def confirm(message: str, *, default: bool = False) -> bool | None:
    if questionary and interactive():
        try:
            return questionary.confirm(message, default=default, style=STYLE).ask()
        except (KeyboardInterrupt, EOFError):
            return None
    answer = select(message, [(tr("ui_yes"), "yes"), (tr("ui_no"), "no")],
                    default="yes" if default else "no")
    return None if answer is None else answer == "yes"


def input_text(message: str, *, default: str = "") -> str | None:
    if questionary and interactive():
        try:
            return questionary.text(message, default=default, style=STYLE).ask()
        except (KeyboardInterrupt, EOFError):
            return None
    if not interactive():
        return None
    try:
        value = input(f"{message} [{default}]: ").strip()
    except (KeyboardInterrupt, EOFError):
        return None
    return value or default


def password(message: str) -> str | None:
    if questionary and interactive():
        try:
            return questionary.password(message, style=STYLE).ask()
        except (KeyboardInterrupt, EOFError):
            return None
    if not sys.stdin.isatty():
        return None
    try:
        return getpass.getpass(message)
    except (getpass.GetPassWarning, EOFError):
        return None


def show_report(report, *, verbose: bool = False, explain: str | None = None,
                docker_host_note: bool = False) -> None:
    checks = report.checks if explain is None else report.matching(explain)
    if explain is not None and not checks:
        raise ValueError(tr("diagnostic_unknown", explain))
    title(tr("ui_doctor_title"))
    for check in checks:
        (success if check.ok else error)(f"{check.title}  [{check.check_id}]")
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
                    info(f"{label}: {value}")
    if docker_host_note and explain is None:
        warning(tr("docker_ci_only"))
    print()
    (success if report.ready else warning)(tr("doctor_ok") if report.ready else tr("doctor_failed"))


def show_readiness_summary(report) -> None:
    passed = sum(check.ok for check in report.checks)
    automatic = sum(not check.ok and bool(check.automation) for check in report.checks)
    manual = sum(not check.ok and not check.automation for check in report.checks)
    title(tr("readiness_title"))
    success(tr("readiness_passed", passed))
    if automatic:
        warning(tr("readiness_automatic", automatic))
    if manual:
        error(tr("readiness_manual", manual))
    for check in report.checks:
        if not check.ok:
            marker = tr("readiness_can_repair") if check.automation else tr("readiness_needs_operator")
            info(f"{check.title} — {marker}")
    if report.ready:
        success(tr("readiness_continue"))


def show_plan(plan) -> None:
    if plan.ready:
        success(tr("plan_ready"))
        return
    title(tr("plan_title", len(plan.steps)))
    for index, step in enumerate(plan.steps, 1):
        actor = step["actor"] or tr("plan_actor_unknown")
        warning(f"{index}. [{actor}] {', '.join(step['checks'])}")
        for value in step["detected"]:
            info(f"{tr('diagnostic_detected')}: {value}")
        for value in step["causes"]:
            info(f"{tr('diagnostic_cause')}: {value}")
        info(f"{tr('diagnostic_fix')}: {step['action']}")
        for value in step["verify"]:
            info(f"{tr('diagnostic_verify')}: {value}")
        if step["docs"]:
            info(f"{tr('diagnostic_docs')}: {step['docs']}")
    print()
    info(tr("plan_footer"))


def show_environment(report: dict) -> None:
    title(tr("env_title").rstrip(":"))
    for item in report["variables"]:
        if item["status"] == "set":
            success(f"{item['name']}={item['value']}")
        else:
            info(f"{item['name']}={tr('env_unset')}")
        info(f"{tr('env_source')}: {item['source']}")
