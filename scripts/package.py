#!/usr/bin/env python3
"""Reproducible allowlisted source archive; never archive a configured tree wholesale."""
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import tarfile

ROOT = Path(__file__).resolve().parents[1]
NAME = 'macos-actions-runner-kit'
FORBIDDEN = {'.git', '.env', '.runner', 'config.json', 'language', '__pycache__', 'actions-runner'}
SECRET_SUFFIXES = ('.p12', '.p8', '.pem', '.key', '.jks', '.keystore', '.mobileprovision', '.log')


def manifest_files(root):
    manifest = root / 'source-manifest.json'
    if manifest.is_symlink():
        raise ValueError('Source manifest must not be a symlink')
    names = json.loads(manifest.read_text(encoding='utf-8'))
    if not isinstance(names, list) or not names or any(not isinstance(n, str) for n in names):
        raise ValueError('Source manifest must be a non-empty string list')
    if len(names) != len(set(names)):
        raise ValueError('Duplicate source manifest entries')
    files = []
    for name in sorted(names):
        relative = PurePosixPath(name)
        if (not relative.parts or relative.is_absolute() or '..' in relative.parts
                or str(relative) != name or '\\' in name
                or any(p in FORBIDDEN or p.startswith(('.env.', '.credentials')) for p in relative.parts)
                or name.endswith(SECRET_SUFFIXES)):
            raise ValueError('Unsafe source manifest entry')
        path = root.joinpath(*relative.parts)
        current = root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise ValueError('Source symlinks are not allowed')
        if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError('Manifest source is missing or outside the source root')
        files.append((name, path))
    return files


def archive_bytes(root):
    output = io.BytesIO()
    with gzip.GzipFile(fileobj=output, mode='wb', filename='', mtime=0) as zipped:
        with tarfile.open(fileobj=zipped, mode='w', format=tarfile.PAX_FORMAT) as archive:
            for name, path in manifest_files(root):
                data = path.read_bytes()
                entry = tarfile.TarInfo(f'{NAME}/{name}')
                entry.size = len(data)
                entry.mode = 0o755 if name in ('runner', 'tool/runner/runner', 'scripts/check.sh') else 0o644
                entry.mtime = 0
                archive.addfile(entry, io.BytesIO(data))
    return output.getvalue()


if __name__ == '__main__':
    data = archive_bytes(ROOT)
    target = ROOT / 'dist'
    if target.is_symlink():
        raise SystemExit('Refusing a symlinked output directory')
    target.mkdir(exist_ok=True)
    name = NAME + '-source.tar.gz'
    for output in (target / name, target / 'SHA256SUMS'):
        if output.is_symlink():
            raise SystemExit('Refusing symlinked output files')
    (target / name).write_bytes(data)
    (target / 'SHA256SUMS').write_text(hashlib.sha256(data).hexdigest() + '  ' + name + '\n')
    print(target / name)
