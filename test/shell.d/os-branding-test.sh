#!/bin/bash

set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/base-test.sh"

python3 - "$ROOT" <<'PY'
import importlib.util
from pathlib import Path
import shutil
import sys
import tempfile
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("os_branding", Path(sys.argv[1]) / "install/helpers/os-branding.py")
branding = importlib.util.module_from_spec(spec)
spec.loader.exec_module(branding)
original = 'NAME="Arch Linux"\nPRETTY_NAME="Arch Linux"\nID=arch\nID_LIKE=arch\nVERSION_ID=rolling\nHOME_URL="https://archlinux.org/"\n'
for kind in ("symlink", "file", "absent"):
  with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    (root / "etc").mkdir()
    vendor = root / "usr/lib/os-release"
    vendor.parent.mkdir(parents=True)
    vendor.write_text(original)
    target = root / "etc/os-release"
    if kind == "symlink":
      target.symlink_to("../usr/lib/os-release")
    elif kind == "file":
      target.write_text(original)
    backup = branding.apply_branding(root)
    assert not target.is_symlink()
    assert target.read_text() == original.replace('NAME="Arch Linux"', 'NAME="Ve OS"')
    assert vendor.read_text() == original
    assert target.stat().st_mode & 0o777 == 0o644
    before = target.stat().st_ino
    assert branding.apply_branding(root) is None
    assert target.stat().st_ino == before
    assert len(list((root / "var/backups").iterdir())) == 1
    assert (backup / "os-release.contents").read_text() == original
    target.unlink()
    if kind == "absent":
      assert (backup / "os-release.absent").exists()
    else:
      shutil.copy2(backup / "os-release", target, follow_symlinks=False)
      assert target.is_symlink() == (kind == "symlink")
      assert target.read_text() == original
    with patch.object(branding.os, "replace", side_effect=OSError("write rejected")):
      try:
        branding.apply_branding(root)
        raise AssertionError("failed replacement must be reported")
      except OSError:
        pass
    assert target.exists() == (kind != "absent")
    if target.exists():
      assert target.read_text() == original
    assert not list((root / "etc").glob(".os-release.ve-*"))
with tempfile.TemporaryDirectory() as directory:
  root = Path(directory)
  font = root / "usr/share/fonts/omarchy/omarchy.ttf"
  font.parent.mkdir(parents=True)
  font.write_bytes(b"old font")
  fetch = root / "etc/fastfetch/config.jsonc"
  fetch.parent.mkdir(parents=True)
  fetch.write_text('custom-layout: true\nOS: Omarchy $version\n')
  session = root / "usr/share/wayland-sessions/omarchy.desktop"
  session.parent.mkdir(parents=True)
  session.write_text('Name=Omarchy (Hyprland uwsm)\nExec=custom-session\n')
  unrelated = root / "etc/fastfetch/custom.json"
  unrelated.write_text("Omarchy private preference")
  unit = root / "usr/lib/systemd/user/omarchy-sleep-lock.service"
  unit.parent.mkdir(parents=True)
  unit.write_text('[Unit]\nDescription=Lock Omarchy before suspend\n[Service]\nExecStart=custom-lock\n')
  custom_unit = root / "usr/lib/systemd/user/omarchy-migrate-notify.service"
  custom_unit.write_text('[Unit]\nDescription=My custom Omarchy migration flow\n')
  backup = branding.publish_branding(Path(sys.argv[1]), root)
  assert unit.read_text() == '[Unit]\nDescription=Lock Ve OS before suspend\n[Service]\nExecStart=custom-lock\n'
  assert custom_unit.read_text() == '[Unit]\nDescription=My custom Omarchy migration flow\n'
  assert font.read_bytes() == (Path(sys.argv[1]) / "default/fonts/omarchy/omarchy.ttf").read_bytes()
  assert (backup / font.relative_to(root)).read_bytes() == b"old font"
  assert fetch.read_text() == 'custom-layout: true\nOS: Ve OS (upstream $version)\n'
  assert session.read_text() == 'Name=Ve OS (Hyprland uwsm)\nExec=custom-session\n'
  assert unrelated.read_text() == "Omarchy private preference"
  assert branding.publish_branding(Path(sys.argv[1]), root) is None
print("ok - installed branding preserves custom settings, backs up replaced files and publishes the font and notification icon once")
print("ok - branding preserves base identity and vendor files, is idempotent, supports rollback and keeps the original after write failure")
PY
