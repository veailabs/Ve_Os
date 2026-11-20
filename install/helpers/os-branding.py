import os
from pathlib import Path
import shutil
import subprocess
import tempfile


def apply_branding(root=Path("/")):
  target = root / "etc/os-release"
  source = target if target.exists() else root / "usr/lib/os-release"
  original = source.read_text()
  fields = {"NAME": 'NAME="Ve OS"', "PRETTY_NAME": 'PRETTY_NAME="Ve OS"', "LOGO": 'LOGO="ve"'}
  lines = []
  for line in original.splitlines():
    key = line.partition("=")[0]
    if key in fields:
      if fields[key] is not None:
        lines.append(fields[key])
        fields[key] = None
    else:
      lines.append(line)
  lines.extend(value for value in fields.values() if value is not None)
  rendered = "\n".join(lines) + "\n"
  if original == rendered and not target.is_symlink() and target.exists():
    return None
  backups = root / "var/backups"
  backups.mkdir(parents=True, exist_ok=True)
  backup = Path(tempfile.mkdtemp(prefix="ve-os-branding-", dir=backups))
  if target.exists() or target.is_symlink():
    shutil.copy2(target, backup / "os-release", follow_symlinks=False)
  else:
    (backup / "os-release.absent").touch()
  (backup / "os-release.contents").write_text(original)
  target.parent.mkdir(parents=True, exist_ok=True)
  fd, temporary = tempfile.mkstemp(prefix=".os-release.ve-", dir=target.parent)
  try:
    with os.fdopen(fd, "w") as output:
      output.write(rendered)
      output.flush()
      os.fchmod(output.fileno(), 0o644)
      os.fsync(output.fileno())
    os.replace(temporary, target)
  finally:
    if os.path.exists(temporary):
      os.unlink(temporary)
  return backup


def publish_branding(source, root=Path("/")):
  updates = {}
  for original, destination in (
    ("default/fonts/omarchy/omarchy.ttf", "usr/share/fonts/omarchy/omarchy.ttf"),
    ("logo.svg", "usr/share/icons/hicolor/scalable/apps/ve.svg"),
  ):
    data = (source / original).read_bytes()
    target = root / destination
    if not target.exists() or target.read_bytes() != data:
      updates[target] = data
  replacements = {
    "usr/share/wayland-sessions/omarchy.desktop": (("Name=Omarchy (Hyprland uwsm)", "Name=Ve OS (Hyprland uwsm)"), ("Comment=Omarchy Hyprland session", "Comment=Ve OS Hyprland session")),
    "etc/fastfetch/config.jsonc": (("Omarchy $version", "Ve OS (upstream $version)"),),
    "etc/limine-entry-tool.d/omarchy-defaults.conf": (('TARGET_OS_NAME="Omarchy"', 'TARGET_OS_NAME="Ve OS"'),),
    "boot/limine.conf": (("interface_branding: Omarchy Bootloader", "interface_branding: Ve OS Bootloader"), ("/+Omarchy\n", "/+Ve OS\n")),
  }
  for scope in ("system", "user"):
    units = list((source / "default/systemd" / scope).glob("*.service"))
    if scope == "system":
      units.extend((source / "install/provisioning").glob("*.service"))
    for unit in units:
      descriptions = [line for line in unit.read_text().splitlines() if line.startswith("Description=") and "Ve OS" in line]
      pairs = tuple((line.replace("Ve OS", "Omarchy") + "\n", line + "\n") for line in descriptions)
      for prefix in ("usr/lib/systemd", "etc/systemd"):
        replacements[f"{prefix}/{scope}/{unit.name}"] = pairs
  for relative, pairs in replacements.items():
    target = root / relative
    if not target.exists():
      continue
    original = target.read_text()
    rendered = original
    for old, new in pairs:
      rendered = rendered.replace(old, new)
    if rendered != original:
      updates[target] = rendered.encode()
  if not updates:
    return None
  backups = root / "var/backups"
  backups.mkdir(parents=True, exist_ok=True)
  backup = Path(tempfile.mkdtemp(prefix="ve-os-assets-", dir=backups))
  for target, data in updates.items():
    saved = backup / target.relative_to(root)
    saved.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or target.is_symlink():
      shutil.copy2(target, saved, follow_symlinks=False)
    else:
      saved.with_name(saved.name + ".absent").touch()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".ve-branding-", dir=target.parent)
    try:
      with os.fdopen(fd, "wb") as output:
        output.write(data)
        output.flush()
        os.fchmod(output.fileno(), target.stat().st_mode & 0o777 if target.exists() else 0o644)
        os.fsync(output.fileno())
      os.replace(temporary, target)
    finally:
      if os.path.exists(temporary):
        os.unlink(temporary)
  return backup


if __name__ == "__main__":
  if os.geteuid() != 0:
    raise SystemExit("Ve OS system branding requires root")
  source = Path(__file__).resolve().parents[2]
  backup = apply_branding()
  print(f"Ve OS system identity applied; original saved in {backup}" if backup else "Ve OS system identity is already current")
  assets = publish_branding(source)
  if assets:
    print(f"Ve OS installed resources applied; originals saved in {assets}")
  subprocess.run(["fc-cache", "-f", "/usr/share/fonts/omarchy"], check=True)
  if assets and Path("/run/systemd/system").is_dir():
    subprocess.run(["systemctl", "daemon-reload"], check=True)
