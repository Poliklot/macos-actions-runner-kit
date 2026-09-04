#!/bin/bash
# Shared with i18n.py. Read only bundled templates, never evaluate their contents.
msg() {
  local key=$1 template
  shift
  # kit is the validated source/installed directory supplied by provision.sh.
  # shellcheck disable=SC2154
  template=$(/usr/bin/plutil -extract "$key.${CI_RUNNER_LANG:-ru}" raw -o - "$kit/messages.json") || return
  # Templates contain only %s substitutions (enforced by the catalog tests).
  # shellcheck disable=SC2059
  printf "$template\n" "$@"
}
