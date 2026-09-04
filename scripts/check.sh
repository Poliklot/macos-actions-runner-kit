#!/bin/bash
set -Eeuo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
python=""
for candidate in "${PYTHON:-python3}" /opt/homebrew/bin/python3 /usr/local/bin/python3; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 12))' 2>/dev/null; then
    python=$candidate
    break
  fi
done
[[ -n "$python" ]] || { echo 'Python 3.12+ is required.' >&2; exit 1; }
while IFS= read -r script; do bash -n "$script"; done < <(find tool scripts -type f -name '*.sh' | sort)
bash -n runner
bash -n tool/runner/runner
pycache=$(mktemp -d)
trap 'rm -rf "$pycache"' EXIT
export PYTHONPYCACHEPREFIX="$pycache"
"$python" -m compileall -q tool tests scripts
"$python" -m unittest discover -s tests -p 'test_*.py'
echo 'Offline regression tests and shell/Python syntax checks passed.'
