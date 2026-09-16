#!/usr/bin/env python3
"""Reject developer-home paths and escaping symlinks in selected project inputs."""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys

HOME_PATH = re.compile(rb"(?<![A-Za-z0-9_.-])(?:/private)?/Users/[^/\s:'\"]+(?:/|$)")
MAX_FILE_BYTES = 16 * 1024 * 1024


def selected_files(root: Path, names: list[str]) -> tuple[list[Path], list[str]]:
    files: list[Path] = []
    errors: list[str] = []
    for name in names:
        candidate = root / name
        try:
            candidate.relative_to(root)
        except ValueError:
            errors.append(f"outside repository: {name}")
            continue
        if not candidate.exists() and not candidate.is_symlink():
            errors.append(f"missing selected path: {name}")
            continue
        paths = [candidate]
        if candidate.is_dir() and not candidate.is_symlink():
            paths = sorted(candidate.rglob("*"))
        for path in paths:
            relative = path.relative_to(root)
            if path.is_symlink():
                try:
                    resolved = path.resolve(strict=True)
                except OSError:
                    errors.append(f"broken symlink: {relative}")
                    continue
                if not resolved.is_relative_to(root):
                    errors.append(f"symlink escapes repository: {relative} -> {resolved}")
                continue
            if path.is_file():
                files.append(path)
    return files, errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", help="reviewed repository-relative files/directories")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="repository root")
    args = parser.parse_args(argv)
    root = args.root.resolve(strict=True)
    files, errors = selected_files(root, args.paths)
    findings = list(errors)
    for path in files:
        relative = path.relative_to(root)
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            findings.append(f"selected file exceeds {MAX_FILE_BYTES} bytes: {relative}")
            continue
        for number, line in enumerate(path.read_bytes().splitlines(), 1):
            match = HOME_PATH.search(line)
            if match:
                value = match.group(0).decode("utf-8", "backslashreplace")
                findings.append(f"developer-home path: {relative}:{number}: {value}")
    if findings:
        print("macOS portability check failed:", file=sys.stderr)
        for finding in findings:
            print(f"- {finding}", file=sys.stderr)
        return 1
    print(f"macOS portability check passed: {len(files)} selected files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
