#!/bin/bash

set -euo pipefail

source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/base-test.sh"

if [[ ${VEOS_DISPOSABLE_ACCEPTANCE:-} != "1" ]]; then
  printf 'skip - VeOS controls require the explicit disposable VM run (test/ci-vm ... veos-controls)\n'
  exit 0
fi
systemd-detect-virt --vm >/dev/null || fail "VeOS controls require a virtual machine"
[[ -n ${WAYLAND_DISPLAY:-} && -n ${HYPRLAND_INSTANCE_SIGNATURE:-} ]] || fail "VeOS controls require the real Wayland session"

export PYTHONPATH="$ROOT/packages/veos-runtime/src"
export OMARCHY_ACCEPTANCE_DIR="$ARTIFACTS"
python3 "$ROOT/packages/veos-runtime/tests/wayland_controls_smoke.py" || fail "VeOS desktop controls pass against Omarchy"
pass "VeOS desktop controls pass against Omarchy"
