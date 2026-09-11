#!/bin/bash
# Privileged one-time installation only. Never executes build jobs as root.
set -Eeuo pipefail
export PATH=/usr/bin:/bin:/usr/sbin:/sbin
umask 077
# Initial validation needs no Python, root or external command.
case "${4:-ru}" in
  en) bootstrap_error='Use runner setup on macOS; invalid provisioning arguments.' ;;
  *) bootstrap_error='Используй runner setup на macOS; некорректные аргументы подготовки.' ;;
esac
[[ $# == 3 || $# == 4 ]] || { echo "$bootstrap_error" >&2; exit 1; }
kit=$1
configuration=$2
source_sdk=$3
CI_RUNNER_LANG=${4:-ru}
case "$CI_RUNNER_LANG" in ru|en) ;; *) echo "$bootstrap_error" >&2; exit 1 ;; esac
[[ $(uname -s) == Darwin ]] || { echo "$bootstrap_error" >&2; exit 1; }
for name in messages.sh messages.json; do
  [[ -f "$kit/$name" && ! -L "$kit/$name" ]] || { echo "$bootstrap_error" >&2; exit 1; }
done
# shellcheck source=tool/runner/messages.sh
source "$kit/messages.sh"
fail() { msg error "$(msg "$@")" >&2; exit 1; }
[[ $(id -u) == 0 ]] || fail provision_platform
for name in runner runner.py configuration.py workloads.py keychain_state.py ios_platform_probe.py provision.sh i18n.py messages.json messages.sh; do
  [[ -f "$kit/$name" && ! -L "$kit/$name" ]] || fail provision_source "$name"
done
field() { /usr/bin/plutil -extract "$1" raw -o - "$configuration"; }
ci_user=$(field ci_user)
repository=$(field repository)
label=$(field label)
schema=$(field schema_version)
xcode=$(field versions.xcode 2>/dev/null) || xcode=""
ruby=$(field versions.ruby 2>/dev/null) || ruby=""
minimum_free_gib=$(field minimum_free_gib)
capabilities=$(/usr/bin/plutil -extract capabilities json -o - "$configuration" | tr -d '[:space:]')
[[ "$ci_user" =~ ^[a-z][a-z0-9_]{0,30}$ ]] || fail provision_user
case "$ci_user" in root|admin|daemon|nobody|guest) fail provision_user_required ;; esac
[[ "$repository" =~ ^[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+$ ]] || fail provision_repo
[[ "$label" =~ ^[A-Za-z0-9][A-Za-z0-9_-]{0,62}$ ]] || fail provision_label
[[ "$schema" == 2 ]] || fail schema_unsupported
[[ -z "$xcode" || "$xcode" =~ ^[0-9]+\.[0-9]+$ ]] || fail provision_versions
[[ -z "$ruby" || "$ruby" =~ ^[0-9]+\.[0-9]+$ ]] || fail provision_versions
[[ "$minimum_free_gib" =~ ^[0-9]+$ && "$minimum_free_gib" -ge 20 && "$minimum_free_gib" -le 1000 ]] || fail provision_disk
capability_pattern='^\[("(android|docker|ios|node|ruby)"(,"(android|docker|ios|node|ruby)")*)?\]$'
[[ "$capabilities" =~ $capability_pattern ]] || fail capabilities_invalid
[[ "$capabilities" != *ios* || -n "$xcode" ]] || fail provision_versions
[[ "$capabilities" != *ruby* || -n "$ruby" ]] || fail provision_versions
home=/Users/$ci_user
sdk=$home/Library/Android/sdk
copy_marker=$sdk/.local-ci-sdk-copy-in-progress
install_parent='/Library/Application Support/Local Actions'
installed=$install_parent/$ci_user

# Validate every ancestor used by privileged mkdir/copy/chown, not just the leaf.
directories=("$home" "$home/Library" "$home/bin" '/Library/Application Support' "$install_parent" "$installed")
if [[ "$capabilities" == *android* ]]; then
  directories+=("$home/Library/Android" "$sdk")
fi
for directory in "${directories[@]}"; do
  [[ ! -L "$directory" ]] || fail provision_symlink "$directory"
done
for directory in "$install_parent" "$installed"; do
  if [[ -e "$directory" ]]; then
    [[ -d "$directory" && $(stat -f %u "$directory") == 0 ]] || fail provision_owner
    [[ $((8#$(stat -f %Lp "$directory") & 022)) == 0 ]] || fail provision_writable
  fi
done
if [[ -f "$installed/config.json" ]]; then
  [[ ! -L "$installed/config.json" ]] || fail provision_config_symlink
  previous_repository=$(/usr/bin/plutil -extract repository raw -o - "$installed/config.json")
  [[ "$previous_repository" == "$repository" ]] || fail provision_other_repo
  previous_label=$(/usr/bin/plutil -extract label raw -o - "$installed/config.json")
  [[ "$previous_label" == "$label" ]] || fail provision_other_label
fi
copy_sdk=false
required_kib=$((minimum_free_gib * 1024 * 1024))
if [[ "$capabilities" == *android* ]]; then
  [[ -d "$source_sdk" && ! -L "$source_sdk" && -x "$source_sdk/platform-tools/adb" &&
     -x "$source_sdk/cmdline-tools/latest/bin/sdkmanager" ]] || fail provision_sdk_missing
  [[ "$source_sdk" != "$sdk" ]] || fail provision_sdk_same
  if [[ -e "$copy_marker" || ! -x "$sdk/platform-tools/adb" || ! -x "$sdk/emulator/emulator" ||
        ! -x "$sdk/cmdline-tools/latest/bin/sdkmanager" || ! -d "$sdk/build-tools" ]]; then
    copy_sdk=true
    source_kib=$(/usr/bin/du -sk "$source_sdk" | /usr/bin/awk '{print $1}')
    required_kib=$((required_kib + source_kib))
  fi
fi
free_kib=$(/bin/df -k /Users | /usr/bin/awk 'NR == 2 {print $4}')
[[ "$free_kib" -ge "$required_kib" ]] || fail provision_space "$minimum_free_gib"

if ! id "$ci_user" >/dev/null 2>&1; then
  msg provision_create "$ci_user"
  /usr/sbin/sysadminctl -addUser "$ci_user" -fullName "CI Runner ($ci_user)" -home "$home" -shell /bin/zsh -password - ||
    fail provision_password_error
  id "$ci_user" >/dev/null 2>&1 || fail provision_account_error
fi
ci_uid=$(id -u "$ci_user")
[[ "$ci_uid" -ge 501 ]] || fail provision_uid
[[ $(dscl . -read "/Users/$ci_user" NFSHomeDirectory) == "NFSHomeDirectory: $home" ]] || fail provision_home
[[ $(dscl . -read "/Users/$ci_user" UserShell) == 'UserShell: /bin/zsh' ]] || fail provision_shell
if id -Gn "$ci_user" | tr ' ' '\n' | grep -qx admin; then
  fail provision_admin
fi
if /usr/bin/pgrep -u "$ci_uid" -f '[R]unner.Listener' >/dev/null; then
  fail provision_running
fi
if [[ ! -d "$home" ]]; then
  /usr/sbin/createhomedir -c -l -u "$ci_user"
fi
[[ -d "$home" && ! -L "$home" && $(stat -f %u "$home") == "$ci_uid" ]] || fail provision_home_owner

if [[ "$capabilities" == *android* ]]; then
  /usr/bin/sudo -H -u "$ci_user" /bin/mkdir -p "$home/Library/Android"
  if [[ "$copy_sdk" == true ]]; then
    msg provision_sdk_copy
    /usr/bin/sudo -H -u "$ci_user" /bin/mkdir -p "$sdk"
    [[ ! -L "$copy_marker" ]] || fail provision_marker_symlink
    /usr/bin/sudo -H -u "$ci_user" /usr/bin/touch "$copy_marker"
    components=()
    for component in cmdline-tools platform-tools build-tools emulator platforms ndk cmake licenses system-images; do
      [[ ! -L "$source_sdk/$component" && ! -L "$sdk/$component" ]] || fail provision_component "$component"
      if [[ -d "$source_sdk/$component" ]]; then
        components+=("$component")
      fi
    done
    # Only reading the administrator's SDK needs root. Extraction runs as ci:
    # a writable destination/symlink can never redirect a root-owned write.
    COPYFILE_DISABLE=1 /usr/bin/tar -cf - --no-acls --no-xattrs --no-mac-metadata \
      -C "$source_sdk" "${components[@]}" |
      /usr/bin/sudo -H -u "$ci_user" /usr/bin/env COPYFILE_DISABLE=1 /usr/bin/tar \
        -xf - --no-same-owner --no-same-permissions --no-acls --no-xattrs --no-mac-metadata -C "$sdk"
    /usr/bin/sudo -H -u "$ci_user" /bin/rm "$copy_marker"
  fi
  [[ $(stat -f %u "$sdk") == "$ci_uid" ]] || fail provision_sdk_owner
fi

# Code installed outside the checkout is root-owned and can be read by ci even
# when the administrator's personal home directory is private.
/usr/bin/install -d -o root -g wheel -m 755 "$install_parent" "$installed"
for name in runner runner.py configuration.py workloads.py keychain_state.py ios_platform_probe.py provision.sh i18n.py messages.json messages.sh; do
  [[ ! -L "$installed/$name" ]] || fail provision_source_symlink
  /usr/bin/install -o root -g wheel -m 644 "$kit/$name" "$installed/$name"
done
[[ ! -L "$installed/config.json" ]] || fail provision_config_symlink
/usr/bin/install -o root -g wheel -m 644 "$configuration" "$installed/config.json"
[[ ! -L "$installed/language" ]] || fail provision_config_symlink
printf '%s\n' "$CI_RUNNER_LANG" > "$installed/language"
/bin/chmod 644 "$installed/language"
/usr/sbin/chown root:wheel "$installed/language"

/usr/bin/sudo -H -u "$ci_user" /usr/bin/env -i HOME="$home" USER="$ci_user" LOGNAME="$ci_user" \
  SHELL=/bin/zsh PATH=/usr/bin:/bin:/usr/sbin:/sbin LANG=en_US.UTF-8 \
  KIT_INSTALLED="$installed" CI_RUNNER_LANG="$CI_RUNNER_LANG" CI_CAPABILITIES="$capabilities" \
  /bin/bash -s <<'CI_SETUP'
set -Eeuo pipefail
umask 077
cd "$HOME"
kit=$KIT_INSTALLED
# shellcheck source=tool/runner/messages.sh
source "$kit/messages.sh"
mkdir -p "$HOME/bin" "$HOME/.config/local-ci" "$HOME/Library/Caches/local-ci"
if [[ "$CI_CAPABILITIES" == *ios* ]]; then
  mkdir -p "$HOME/Library/Developer/Xcode/DerivedData" "$HOME/Library/MobileDevice/Provisioning Profiles"
fi
for file in "$HOME/bin/ci-runner" "$HOME/.zprofile" "$HOME/.config/local-ci/env.sh"; do
  [[ ! -L "$file" ]] || { msg provision_symlink "$file" >&2; exit 1; }
done
printf '#!/bin/bash\nexec /bin/bash %q "$@"\n' "$KIT_INSTALLED/runner" > "$HOME/bin/ci-runner"
chmod 700 "$HOME/bin/ci-runner"
# The same validated renderer is used by doctor/start and the login shell.
# Run as ci, never execute user-supplied shell snippets as root.
"$HOME/bin/ci-runner" shell-env > "$HOME/.config/local-ci/env.sh"
touch "$HOME/.zprofile"
# shellcheck disable=SC2016
line='source "$HOME/.config/local-ci/env.sh"'
if ! grep -Fqx "$line" "$HOME/.zprofile"; then printf '\n%s\n' "$line" >> "$HOME/.zprofile"; fi
# shellcheck disable=SC1091
source "$HOME/.config/local-ci/env.sh"

msg provision_installed
if ! "$HOME/bin/ci-runner" doctor; then
  msg provision_incomplete
  exit 2
fi
CI_SETUP
msg provision_next "$ci_user"
msg provision_done
