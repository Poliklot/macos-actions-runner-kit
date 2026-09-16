#!/usr/bin/env python3
"""Keep verified run-bound artifacts locally when GitHub artifact upload is unavailable."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import uuid


def required(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise ValueError(f"{name} is required")
    return value


def identity() -> tuple[str, str, int]:
    repository = required("GITHUB_REPOSITORY")
    run_id = required("GITHUB_RUN_ID")
    attempt = required("GITHUB_RUN_ATTEMPT")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("invalid GITHUB_REPOSITORY")
    if not run_id.isdigit() or not attempt.isdigit() or int(attempt) < 1:
        raise ValueError("invalid GitHub run identity")
    return repository, run_id, int(attempt)


def cache_root() -> Path:
    allowed = (Path.home() / "Library/Caches").resolve()
    configured = Path(os.environ.get("LOCAL_ARTIFACT_CACHE", allowed / "local-ci-artifacts"))
    if not configured.is_absolute():
        raise ValueError("LOCAL_ARTIFACT_CACHE must be absolute")
    resolved = configured.resolve(strict=False)
    if not resolved.is_relative_to(allowed):
        raise ValueError("artifact cache must stay under ~/Library/Caches")
    current = allowed
    for part in resolved.relative_to(allowed).parts:
        current /= part
        if current.is_symlink():
            raise ValueError("artifact cache path must not contain symlinks")
    return resolved


def checkpoint_path() -> tuple[Path, str, str, int]:
    repository, run_id, attempt = identity()
    owner, name = repository.split("/", 1)
    return cache_root() / f"{owner}--{name}" / run_id, repository, run_id, attempt


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            value.update(chunk)
    return value.hexdigest()


def private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)


def write_private(path: Path, data: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(data)


def store(names: list[str]) -> None:
    checkpoint, repository, run_id, attempt = checkpoint_path()
    if checkpoint.exists() or checkpoint.is_symlink():
        raise ValueError(f"checkpoint already exists: {checkpoint}")
    workspace = Path(required("GITHUB_WORKSPACE")).resolve(strict=True)
    sources = []
    seen = set()
    for name in names:
        path = Path(name)
        if not path.is_absolute():
            path = workspace / path
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"artifact must be a regular non-symlink file: {name}")
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(workspace):
            raise ValueError(f"artifact is outside GITHUB_WORKSPACE: {name}")
        if resolved.name in seen:
            raise ValueError(f"duplicate artifact basename: {resolved.name}")
        seen.add(resolved.name)
        sources.append(resolved)
    if not sources:
        raise ValueError("at least one artifact is required")

    private_directory(cache_root())
    private_directory(checkpoint.parent)
    temporary = checkpoint.parent / f".{run_id}.tmp-{uuid.uuid4().hex}"
    temporary.mkdir(mode=0o700)
    try:
        entries = []
        for source in sources:
            target = temporary / source.name
            with source.open("rb") as input_file:
                descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "wb") as output:
                    shutil.copyfileobj(input_file, output, 1024 * 1024)
            entries.append({"name": source.name, "bytes": target.stat().st_size,
                            "sha256": digest(target)})
        manifest = {"schema": 1, "repository": repository, "run_id": run_id,
                    "producer_attempt": attempt, "artifacts": entries}
        write_private(temporary / "manifest.json",
                      (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode())
        os.replace(temporary, checkpoint)
    except Exception:
        if temporary.exists() and not temporary.is_symlink():
            shutil.rmtree(temporary)
        raise
    print(f"stored {len(sources)} verified artifacts in {checkpoint}")


def verified_manifest() -> tuple[Path, dict]:
    checkpoint, repository, run_id, attempt = checkpoint_path()
    if checkpoint.is_symlink() or not checkpoint.is_dir():
        raise ValueError(f"checkpoint is missing or unsafe: {checkpoint}")
    manifest_path = checkpoint / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("artifact manifest is missing or unsafe")
    value = json.loads(manifest_path.read_text())
    if (not isinstance(value, dict) or value.get("schema") != 1
            or value.get("repository") != repository or value.get("run_id") != run_id
            or not isinstance(value.get("producer_attempt"), int)
            or value["producer_attempt"] > attempt or not isinstance(value.get("artifacts"), list)):
        raise ValueError("artifact manifest does not match this repository/run attempt")
    names = set()
    for item in value["artifacts"]:
        if (not isinstance(item, dict) or set(item) != {"name", "bytes", "sha256"}
                or not isinstance(item["name"], str) or Path(item["name"]).name != item["name"]
                or item["name"] in names or type(item["bytes"]) is not int
                or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])):
            raise ValueError("invalid artifact manifest entry")
        names.add(item["name"])
        path = checkpoint / item["name"]
        if (path.is_symlink() or not path.is_file() or path.stat().st_size != item["bytes"]
                or digest(path) != item["sha256"]):
            raise ValueError(f"artifact integrity check failed: {item['name']}")
    if not names:
        raise ValueError("artifact manifest is empty")
    return checkpoint, value


def verify() -> None:
    checkpoint, value = verified_manifest()
    print(f"verified {len(value['artifacts'])} artifacts in {checkpoint}")


def restore(destination: str) -> None:
    checkpoint, value = verified_manifest()
    workspace = Path(required("GITHUB_WORKSPACE")).resolve(strict=True)
    target = Path(destination)
    if not target.is_absolute():
        target = workspace / target
    resolved = target.resolve(strict=False)
    if not resolved.is_relative_to(workspace) or target.exists() or target.is_symlink():
        raise ValueError("restore destination must be a new directory inside GITHUB_WORKSPACE")
    if target.parent.is_symlink() or not target.parent.is_dir():
        raise ValueError("restore destination parent must already exist and must not be a symlink")
    temporary = target.parent / f".{target.name}.tmp-{uuid.uuid4().hex}"
    temporary.mkdir(mode=0o700)
    try:
        for item in value["artifacts"]:
            source = checkpoint / item["name"]
            descriptor = os.open(temporary / item["name"],
                                 os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with source.open("rb") as input_file, os.fdopen(descriptor, "wb") as output:
                shutil.copyfileobj(input_file, output, 1024 * 1024)
        os.replace(temporary, target)
    except Exception:
        if temporary.exists() and not temporary.is_symlink():
            shutil.rmtree(temporary)
        raise
    print(f"restored {len(value['artifacts'])} verified artifacts to {target}")


def purge() -> None:
    checkpoint, value = verified_manifest()
    shutil.rmtree(checkpoint)
    print(f"purged {len(value['artifacts'])} verified artifacts from {checkpoint}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    store_parser = sub.add_parser("store")
    store_parser.add_argument("artifacts", nargs="+")
    sub.add_parser("verify")
    restore_parser = sub.add_parser("restore")
    restore_parser.add_argument("destination")
    sub.add_parser("purge")
    args = parser.parse_args(argv)
    try:
        if args.command == "store":
            store(args.artifacts)
        elif args.command == "restore":
            restore(args.destination)
        elif args.command == "verify":
            verify()
        else:
            purge()
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"artifact checkpoint error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
