#!/usr/bin/env python3
"""Prepare or clean up a recoverable temporary Apple signing keychain."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys

SECURITY = Path("/usr/bin/security")


def required(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise ValueError(f"{name} is required")
    return value


def state_directory() -> Path:
    name = os.environ.get("SIGNING_STATE_NAME", "local-ci-signing-state")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}-signing-state", name):
        raise ValueError("SIGNING_STATE_NAME must end in -signing-state and contain only safe characters")
    cache = Path.home() / "Library/Caches"
    return cache / name


def command(*args: str, capture: bool = True) -> str:
    result = subprocess.run([str(SECURITY), *args], text=True, capture_output=capture, check=False)
    if result.returncode:
        message = (result.stderr or result.stdout or "security command failed").strip()
        raise RuntimeError(message)
    return (result.stdout or "").strip()


def keychain_paths(output: str) -> list[str]:
    values = []
    for line in output.splitlines():
        value = line.strip()
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = value[1:-1].replace(r'\"', '"').replace(r"\\", "\\")
        if value:
            values.append(value)
    return values


def checked_file(name: str) -> Path:
    path = Path(required(name)).expanduser()
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{name} must name a regular non-symlink file")
    return path.resolve()


def remove_state(state: Path, record: dict) -> None:
    errors = []
    try:
        previous = record.get("previous_keychains", [])
        if not isinstance(previous, list) or not all(isinstance(value, str) for value in previous):
            raise ValueError("invalid previous keychain state")
        command("list-keychains", "-d", "user", "-s", *previous)
        default = record.get("previous_default")
        if default:
            command("default-keychain", "-d", "user", "-s", default)
    except (RuntimeError, ValueError) as error:
        errors.append(str(error))
    keychain = state / "job.keychain-db"
    try:
        if keychain.exists():
            command("delete-keychain", str(keychain))
    except RuntimeError as error:
        errors.append(str(error))
    if errors:
        raise RuntimeError("; ".join(errors))
    shutil.rmtree(state)


def prepare() -> None:
    state = state_directory()
    if state.exists() or state.is_symlink():
        raise ValueError(f"recovery state already exists: {state}; run cleanup or inspect it")
    certificate = checked_file("SIGNING_CERTIFICATE_PATH")
    certificate_password = required("SIGNING_CERTIFICATE_PASSWORD")
    wwdr = checked_file("APPLE_WWDR_CERTIFICATE_PATH")
    expected = required("APPLE_WWDR_SHA256")
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError("APPLE_WWDR_SHA256 must be a lowercase SHA-256")
    if hashlib.sha256(wwdr.read_bytes()).hexdigest() != expected:
        raise ValueError("Apple WWDR certificate SHA-256 mismatch")
    if not SECURITY.is_file():
        raise ValueError("/usr/bin/security is required on macOS")

    record = {
        "schema": 1,
        "previous_default": next(iter(keychain_paths(command("default-keychain", "-d", "user"))), ""),
        "previous_keychains": keychain_paths(command("list-keychains", "-d", "user")),
    }
    state.mkdir(mode=0o700, parents=True)
    if state.is_symlink():
        raise ValueError("refusing a symlinked signing state directory")
    keychain = state / "job.keychain-db"
    password = secrets.token_urlsafe(32)
    try:
        record_path = state / "state.json"
        descriptor = os.open(record_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as output:
            output.write(json.dumps(record, indent=2) + "\n")
        command("create-keychain", "-p", password, str(keychain))
        command("set-keychain-settings", "-lut", "21600", str(keychain))
        command("unlock-keychain", "-p", password, str(keychain))
        command("import", str(wwdr), "-k", str(keychain))
        command("import", str(certificate), "-k", str(keychain), "-P", certificate_password,
                "-T", "/usr/bin/codesign", "-T", "/usr/bin/security")
        command("set-key-partition-list", "-S", "apple-tool:,apple:,codesign:", "-s", "-k",
                password, str(keychain))
        command("list-keychains", "-d", "user", "-s", str(keychain), *record["previous_keychains"])
        command("default-keychain", "-d", "user", "-s", str(keychain))
        identities = command("find-identity", "-v", "-p", "codesigning", str(keychain))
        if "0 valid identities found" in identities or "valid identities found" not in identities:
            raise RuntimeError("no valid code-signing identity was imported")
        prepared = state / "prepared"
        descriptor = os.open(prepared, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as output:
            output.write("ready\n")
    except Exception:
        remove_state(state, record)
        raise
    finally:
        password = ""
        certificate_password = ""
    print(f"temporary signing keychain prepared: {keychain}")


def cleanup(*, if_present: bool = False) -> None:
    state = state_directory()
    if if_present and not state.exists() and not state.is_symlink():
        print("no temporary signing keychain state to clean up")
        return
    if state.is_symlink() or not state.is_dir():
        raise ValueError(f"signing recovery state is missing or unsafe: {state}")
    record_path = state / "state.json"
    if record_path.is_symlink() or not record_path.is_file():
        raise ValueError("signing recovery record is missing or unsafe")
    record = json.loads(record_path.read_text())
    if not isinstance(record, dict) or record.get("schema") != 1:
        raise ValueError("unsupported signing recovery record")
    remove_state(state, record)
    print("temporary signing keychain cleaned up and previous state restored")


def main(argv: list[str]) -> int:
    if argv not in (["prepare"], ["cleanup"], ["cleanup-if-present"]):
        print("usage: apple-signing-keychain.py prepare|cleanup|cleanup-if-present", file=sys.stderr)
        return 2
    try:
        if argv[0] == "prepare":
            prepare()
        else:
            cleanup(if_present=argv[0] == "cleanup-if-present")
        return 0
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as error:
        print(f"signing keychain error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
