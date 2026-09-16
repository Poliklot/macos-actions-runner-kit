#!/bin/bash
set -Eeuo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

echo '== Source-tree offline gate =='
bash scripts/check.sh
python3 scripts/package.py

temporary=$(mktemp -d "${TMPDIR:-/tmp}/runner-kit-acceptance.XXXXXXXX")
trap 'rm -rf -- "$temporary"' EXIT
tar -xzf dist/macos-actions-runner-kit-source.tar.gz -C "$temporary"
extracted="$temporary/macos-actions-runner-kit"

for forbidden in .git .env tool/runner/config.json actions-runner .runner; do
  [[ ! -e "$extracted/$forbidden" ]] || {
    echo "Runtime/private path leaked into archive: $forbidden" >&2
    exit 1
  }
done

echo '== Fresh extracted-archive gate =='
(
  cd "$extracted"
  bash scripts/check.sh
  python3 scripts/package.py
)
cmp -s dist/macos-actions-runner-kit-source.tar.gz \
  "$extracted/dist/macos-actions-runner-kit-source.tar.gz" || {
  echo 'Archive rebuilt from extracted source is not byte-for-byte reproducible.' >&2
  exit 1
}

if command -v shasum >/dev/null 2>&1; then
  digest=$(shasum -a 256 dist/macos-actions-runner-kit-source.tar.gz | awk '{print $1}')
else
  digest=$(sha256sum dist/macos-actions-runner-kit-source.tar.gz | awk '{print $1}')
fi
printf 'Acceptance gate passed: %s/%s; archive sha256=%s\n' "$(uname -s)" "$(uname -m)" "$digest"
