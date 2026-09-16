#!/bin/bash
set -Eeuo pipefail

# Reviewed upstream release. Update version and both checksums together.
version=1.7.12
arm64_sha256=aba9ced2dee8d27fecca3dc7feb1a7f9a52caefa1eb46f3271ea66b6e0e6953f
amd64_sha256=5b44c3bc2255115c9b69e30efc0fecdf498fdb63c5d58e17084fd5f16324c644
destination=${ACTIONLINT_DESTINATION:-"${RUNNER_TEMP:-$HOME/.cache}/actionlint-bin"}

[[ $(uname -s) == Darwin ]] || { echo 'This recipe installs the macOS actionlint build only.' >&2; exit 1; }
case "$(uname -m)" in
  arm64) architecture=arm64; expected=$arm64_sha256 ;;
  x86_64) architecture=amd64; expected=$amd64_sha256 ;;
  *) echo 'Unsupported macOS architecture.' >&2; exit 1 ;;
esac

temporary=$(mktemp -d "${TMPDIR:-/tmp}/actionlint.XXXXXXXX")
trap 'rm -rf "$temporary"' EXIT
archive="$temporary/actionlint.tar.gz"
url="https://github.com/rhysd/actionlint/releases/download/v${version}/actionlint_${version}_darwin_${architecture}.tar.gz"
curl --fail --location --proto '=https' --tlsv1.2 --output "$archive" "$url"

if command -v shasum >/dev/null 2>&1; then
  actual=$(shasum -a 256 "$archive" | awk '{print $1}')
elif command -v sha256sum >/dev/null 2>&1; then
  actual=$(sha256sum "$archive" | awk '{print $1}')
else
  echo 'Neither shasum nor sha256sum is available.' >&2
  exit 1
fi
[[ $actual == "$expected" ]] || { echo 'actionlint SHA-256 mismatch.' >&2; exit 1; }

# Extract only the expected executable; ignore every other archive member.
tar -xOf "$archive" actionlint > "$temporary/actionlint"
chmod 0755 "$temporary/actionlint"
version_output=$("$temporary/actionlint" -version)
[[ ${version_output%%$'\n'*} == "$version" ]] || {
  echo 'Unexpected actionlint version after extraction.' >&2
  exit 1
}
mkdir -p "$destination"
install -m 0755 "$temporary/actionlint" "$destination/actionlint"
printf '%s\n' "$destination/actionlint"
