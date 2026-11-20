#!/bin/bash

set -euo pipefail

source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/base-test.sh"

if [[ ${VEOS_DISPOSABLE_ACCEPTANCE:-} != "1" ]]; then
  printf 'skip - VeOS controls require the explicit disposable VM run (test/ci-vm ... veos-controls)\n'
  exit 0
fi
systemd-detect-virt --vm >/dev/null || fail "VeOS controls require a virtual machine"
[[ -n ${WAYLAND_DISPLAY:-} && -n ${HYPRLAND_INSTANCE_SIGNATURE:-} ]] || fail "VeOS controls require the real Wayland session"

# Exercise our runtime against the installed desktop. Quickshell selects its
# IPC instance by config path; test/ci-vm's source checkout is not that shell.
session_omarchy_path=$(systemctl --user show-environment | sed -n 's/^OMARCHY_PATH=//p' | tail -n 1)
[[ -n $session_omarchy_path && -f $session_omarchy_path/shell/shell.qml ]] || fail "VeOS controls require the session's installed Omarchy tree"
export OMARCHY_PATH="$session_omarchy_path"
export PATH="$OMARCHY_PATH/bin:$PATH"
printf 'Desktop under test: %s; VeOS source: %s\n' "$OMARCHY_PATH" "$ROOT"

export PYTHONPATH="$ROOT/packages/veos-runtime/src"
export OMARCHY_ACCEPTANCE_DIR="$ARTIFACTS"
python3 "$ROOT/packages/veos-runtime/tests/wayland_controls_smoke.py" || fail "VeOS desktop controls pass against Omarchy"
pass "VeOS desktop controls pass against Omarchy"
