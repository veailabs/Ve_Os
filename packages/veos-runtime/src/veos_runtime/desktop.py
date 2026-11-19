import os
import plistlib
import shutil
import sys
from pathlib import Path

from .store import Refused


def install():
  """Install our own assets; never modify the upstream OS or enable a service."""
  if sys.platform == "darwin":
    executable = shutil.which("veos")
    if not executable:
      raise Refused("Install the veos command before installing its login service")
    target = Path.home() / "Library/LaunchAgents/ai.ve.veos-runtime.plist"
    log = Path.home() / ".local/state/veos/logs"
    log.mkdir(parents=True, exist_ok=True, mode=0o700)
    data = plistlib.dumps(
      {
        "Label": "ai.ve.veos-runtime",
        "ProgramArguments": [executable, "serve"],
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 10,
        "EnvironmentVariables": {"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
        "StandardOutPath": str(log / "runtime.log"),
        "StandardErrorPath": str(log / "runtime-error.log"),
        "Umask": 0o077,
      }
    )
    if target.exists() and target.read_bytes() != data:
      raise Refused(f"Review the existing login service before replacing it: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    os.chmod(target, 0o600)
    return [str(target)]
  if sys.platform != "linux":
    raise Refused(
      "Omarchy desktop installation requires Linux. Use 'veos serve' and 'veos open' on this computer."
    )
  config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
  data = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
  assets = Path(__file__).with_name("desktop")
  plugin = config / "omarchy/plugins/veos.missions"
  targets = {
    "manifest.json": plugin / "manifest.json",
    "Widget.qml": plugin / "Widget.qml",
    "veos-runtime.service": config / "systemd/user/veos-runtime.service",
    "veos.desktop": data / "applications/veos.desktop",
  }
  for source, target in targets.items():
    if target.exists() and target.read_bytes() != (assets / source).read_bytes():
      raise Refused(f"Review the existing file before replacing it: {target}")
  for source, target in targets.items():
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes((assets / source).read_bytes())
  return list(map(str, targets.values()))
