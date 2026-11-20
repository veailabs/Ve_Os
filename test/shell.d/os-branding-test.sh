#!/bin/bash

set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/base-test.sh"

python3 - "$ROOT" <<'PY'
import importlib.util
import os
import runpy
import subprocess
from pathlib import Path
import shutil
import sys
import tempfile
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("os_branding", Path(sys.argv[1]) / "install/helpers/os-branding.py")
branding = importlib.util.module_from_spec(spec)
spec.loader.exec_module(branding)
with patch("os.geteuid", return_value=1000), patch("pathlib.Path.mkdir") as mkdir, patch("pathlib.Path.write_text") as write_text, patch("pathlib.Path.write_bytes") as write_bytes, patch("os.replace") as replace, patch("tempfile.mkdtemp") as mkdtemp, patch("subprocess.run") as run:
  try:
    runpy.run_path(spec.origin, run_name="__main__")
    raise AssertionError("non-root entrypoint must refuse")
  except SystemExit as error:
    assert str(error) == "Ve OS system branding requires root"
  for operation in (mkdir, write_text, write_bytes, replace, mkdtemp, run):
    operation.assert_not_called()
with tempfile.TemporaryDirectory() as directory:
  hostile = Path(directory)
  helper = hostile / "install/helpers/os-branding.py"
  helper.parent.mkdir(parents=True)
  marker = hostile / "executed"
  helper.write_text(f"from pathlib import Path; Path({str(marker)!r}).touch()")
  command = Path(sys.argv[1]) / "bin/omarchy-apply-os-branding"
  link = hostile / "branding-command"
  link.symlink_to(command)
  for entrypoint in (command, link):
    result = subprocess.run([str(entrypoint)], env={**os.environ, "OMARCHY_PATH": str(hostile)}, capture_output=True, text=True)
    assert result.returncode != 0
    assert "OMARCHY_PATH does not match this Ve OS command" in result.stderr, result.stderr
    assert not marker.exists()
print("ok - privileged entrypoint rejects an unrelated source tree and non-root execution before writes or subprocesses")
original = 'NAME="Arch Linux"\nPRETTY_NAME="Arch Linux"\nLOGO=archlinux-logo\nID=arch\nID_LIKE=arch\nVERSION_ID=rolling\nHOME_URL="https://archlinux.org/"\n'
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
    assert target.read_text() == original.replace('NAME="Arch Linux"', 'NAME="Ve OS"').replace('LOGO=archlinux-logo', 'LOGO="ve"')
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
  limine_defaults = root / "etc/limine-entry-tool.d/omarchy-defaults.conf"
  limine_defaults.parent.mkdir(parents=True)
  limine_defaults.write_text('TARGET_OS_NAME="Omarchy"\nCUSTOM_UKI_NAME="omarchy"\nBOOT_ORDER="linux-custom, *"\n')
  limine_boot = root / "boot/limine.conf"
  limine_boot.parent.mkdir(parents=True)
  limine_boot.write_text('interface_branding: Omarchy Bootloader\ntimeout: 7\n/+Omarchy\n//linux-custom\n/+My rescue OS\n')
  backup = branding.publish_branding(Path(sys.argv[1]), root)
  assert limine_defaults.read_text() == 'TARGET_OS_NAME="Ve OS"\nCUSTOM_UKI_NAME="omarchy"\nBOOT_ORDER="linux-custom, *"\n'
  assert limine_boot.read_text() == 'interface_branding: Ve OS Bootloader\ntimeout: 7\n/+Ve OS\n//linux-custom\n/+My rescue OS\n'
  assert unit.read_text() == '[Unit]\nDescription=Lock Ve OS before suspend\n[Service]\nExecStart=custom-lock\n'
  assert custom_unit.read_text() == '[Unit]\nDescription=My custom Omarchy migration flow\n'
  assert font.read_bytes() == (Path(sys.argv[1]) / "default/fonts/omarchy/omarchy.ttf").read_bytes()
  assert (backup / font.relative_to(root)).read_bytes() == b"old font"
  assert fetch.read_text() == 'custom-layout: true\nOS: Ve OS (upstream $version)\n'
  assert session.read_text() == 'Name=Ve OS (Hyprland uwsm)\nExec=custom-session\n'
  assert unrelated.read_text() == "Omarchy private preference"
  assert branding.publish_branding(Path(sys.argv[1]), root) is None
  limine_defaults.write_text('TARGET_OS_NAME="My Omarchy variant"\nBOOT_ORDER="linux-custom, *"\n')
  limine_boot.write_text('interface_branding: My Omarchy Bootloader\ntimeout: 7\n/+My rescue OS\n')
  assert branding.publish_branding(Path(sys.argv[1]), root) is None
  assert limine_defaults.read_text() == 'TARGET_OS_NAME="My Omarchy variant"\nBOOT_ORDER="linux-custom, *"\n'
  assert limine_boot.read_text() == 'interface_branding: My Omarchy Bootloader\ntimeout: 7\n/+My rescue OS\n'

print("ok - installed branding preserves custom settings, backs up replaced files and publishes the font and notification icon once")
print("ok - branding preserves base identity and vendor files, is idempotent, supports rollback and keeps the original after write failure")
PY
