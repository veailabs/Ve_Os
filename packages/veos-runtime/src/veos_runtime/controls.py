"""Typed, user-session desktop operations. No model-provided shell commands."""

import configparser
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .artifacts import Artifacts
from .store import Refused, bounded, digest, integer

SETTINGS = {
  "branding": ["omarchy-menu", "summon", "appearance.branding"],
  "appearance": ["omarchy-menu", "summon", "appearance"],
  "theme": ["omarchy-menu", "summon", "appearance.theme"],
  "background": ["omarchy-menu", "summon", "appearance.background"],
  "font": ["omarchy-menu", "summon", "appearance.font"],
  "icons": ["omarchy-menu", "summon", "appearance.icons"],
  "bar": ["omarchy-menu", "summon", "appearance.top-bar"],
  "dock": ["omarchy-menu", "summon", "appearance.dock"],
  "windows": ["omarchy-menu", "summon", "appearance.windows"],
  "layout": ["omarchy-menu", "summon", "appearance.workspace-layout"],
  "keyboard": ["omarchy-menu", "summon", "setup.keyboard"],
  "input": ["omarchy-menu", "summon", "setup.input"],
  "applications": ["omarchy-menu", "summon", "setup.default"],
  "plugins": ["omarchy-menu", "summon", "setup.plugin"],
  "agents": ["omarchy-menu", "summon", "agent.harness"],
  "network": ["omarchy-menu", "summon", "setup.network"],
}
READS = {
  "windows",
  "displays",
  "apps",
  "themes",
  "fonts",
  "audio",
  "clipboard_read",
  "native_read",
  "read_document",
}
COMMANDS = {
  "windows": "hyprctl",
  "displays": "hyprctl",
  "focus_window": "hyprctl",
  "apps": "gio",
  "open_app": "gio",
  "themes": "omarchy-theme-list",
  "set_theme": "omarchy-theme-set",
  "fonts": "omarchy-font-list",
  "set_font": "omarchy-font-set",
  "audio": "wpctl",
  "set_audio": "wpctl",
  "clipboard_read": "wl-paste",
  "clipboard_write": "wl-copy",
  "screenshot": "grim",
  "native_read": "python3",
  "native_action": "python3",
  "native_fill": "python3",
  "open_settings": "omarchy-menu",
  "configure": "hyprctl",
  "read_document": "tesseract",
}
SPECS = {
  "windows": {},
  "displays": {},
  "apps": {},
  "themes": {},
  "fonts": {},
  "focus_window": {"address": "string"},
  "open_app": {"app_id": "string"},
  "set_theme": {"name": "string"},
  "set_font": {"name": "string"},
  "audio": {"device": "string"},
  "set_audio": {"device": "string", "volume": "integer", "muted": "boolean"},
  "clipboard_read": {},
  "clipboard_write": {"text": "string"},
  "screenshot": {},
  "open_settings": {"area": "string"},
  "configure": {"area": "string", "values": "object"},
  "read_document": {"artifact_id": "string", "offset": "integer"},
  "native_read": {"app_pid": "integer", "offset": "integer"},
  "native_action": {
    "app_pid": "integer",
    "node": "string",
    "revision": "string",
    "action": "string",
  },
  "native_fill": {
    "app_pid": "integer",
    "node": "string",
    "revision": "string",
    "text": "string",
  },
}


class Controls:
  def __init__(self, store, runner=subprocess.run):
    self.store, self.runner = store, runner

  def catalogue(self):
    linux = sys.platform == "linux" and bool(os.environ.get("WAYLAND_DISPLAY"))
    result = []
    for op, command in COMMANDS.items():
      available = linux and shutil.which(command) is not None
      reason = (
        None
        if available
        else "Requires the matching tool in a Linux Wayland user session"
      )
      if op == "read_document":
        available, reason = (
          True,
          "Text is built in; PDF requires pdftotext, images require tesseract",
        )
      if op.startswith("native_"):
        # GI lives in the distribution's Python, not the runtime's isolated venv.
        available = linux and Path("/usr/bin/python3").exists()
        reason = "Requires python-gobject, at-spi2-core and accessibility support in the target application; checked on use"
      result.append(
        {
          "operation": op,
          "read_only": op in READS,
          "available": available,
          "reason": reason,
          "arguments": SPECS[op],
          "requires_approval": op not in READS,
        }
      )
    return result

  def run(self, argv, data=None, timeout=15):
    # Redirect output to a bounded read, not a model-controlled command string.
    with tempfile.TemporaryFile() as output:
      result = self.runner(
        argv,
        input=data,
        stdout=output,
        stderr=subprocess.DEVNULL,
        timeout=timeout,
        check=False,
      )
      if result.returncode:
        raise Refused("Desktop command failed; inspect the application before retrying")
      output.seek(0)
      raw = output.read(5_000_001)
      if len(raw) > 5_000_000:
        raise Refused("Desktop result exceeds the size limit")
      return raw

  def json_command(self, *args):
    return json.loads(self.run(list(args)))

  def apps(self):
    roots = [
      Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
      / "applications"
    ]
    roots += [
      Path(path) / "applications"
      for path in os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(
        ":"
      )
    ]
    found = {}
    for root in roots:
      for path in sorted(root.glob("*.desktop"))[:1000]:
        if path.name in found:
          continue
        config = configparser.ConfigParser(interpolation=None, strict=False)
        try:
          config.read(path)
          entry = config["Desktop Entry"]
          if entry.get("Type") != "Application" or entry.get("Hidden") == "true":
            continue
          found[path.name] = {
            "id": path.name,
            "name": entry.get("Name", path.stem),
            "path": str(path),
          }
        except (configparser.Error, KeyError):
          continue
    return found

  def validate(self, op, args):
    if op not in SPECS or not isinstance(args, dict) or set(args) - set(SPECS[op]):
      raise Refused("Unknown desktop operation or arguments")
    for key, value in args.items():
      kind = SPECS[op][key]
      if (
        type(value)
        is not {"string": str, "integer": int, "boolean": bool, "object": dict}[kind]
      ):
        raise Refused("Invalid desktop argument type")
      if isinstance(value, str) and (len(value) > 10000 or "\x00" in value):
        raise Refused("Desktop text exceeds its limit")
    optional = {"offset", "device", "volume", "muted"}
    if set(SPECS[op]) - optional - set(args):
      raise Refused("Missing required desktop arguments")
    if "offset" in args:
      integer(args["offset"], "offset", 0, 5_000_000)
    if op in {"focus_window"} and not re.fullmatch(
      r"0x[0-9a-fA-F]+", args.get("address", "")
    ):
      raise Refused("Use an observed window address")
    if op in {"audio", "set_audio"} and args.get("device", "output") not in {
      "input",
      "output",
    }:
      raise Refused("Choose input or output audio")
    if op == "set_audio":
      if "volume" not in args and "muted" not in args:
        raise Refused("Specify volume or muted state")
      if "volume" in args:
        integer(args["volume"], "volume", 0, 100)
    if op == "open_settings" and args.get("area") not in {
      *SETTINGS,
      "displays",
      "startup",
      "notifications",
    }:
      raise Refused("Choose an advertised settings area")
    if op == "configure":
      self.config_content(args)
    if op.startswith("native_"):
      integer(args.get("app_pid"), "application process", 1, 2**31 - 1)
    if op in {"clipboard_write", "native_fill"} and "text" not in args:
      raise Refused("Text is required")

  def binding(self, op, args):
    if op == "open_app":
      app = self.apps().get(args.get("app_id"))
      if not app:
        raise Refused("Choose an installed application ID")
      return digest(Path(app["path"]).read_bytes().hex())
    if op == "configure":
      path, _ = self.config_content(args)
      if path.is_symlink():
        raise Refused("Review the symlinked settings file manually")
      return digest(path.read_text() if path.exists() else "")
    if op == "set_audio":
      return self.run(["wpctl", "inspect", self.audio_device(args)]).decode()
    if op in {"focus_window", "screenshot"}:
      return digest(self.json_command("hyprctl", "clients", "-j"))
    return None

  @staticmethod
  def audio_device(args):
    return (
      "@DEFAULT_AUDIO_SOURCE@"
      if args.get("device") == "input"
      else "@DEFAULT_AUDIO_SINK@"
    )

  @staticmethod
  def write_atomic(path, text):
    fd, temporary = tempfile.mkstemp(prefix=".veos-", dir=path.parent)
    try:
      with os.fdopen(fd, "w") as output:
        output.write(text)
        output.flush()
        os.fsync(output.fileno())
      Path(temporary).replace(path)
    finally:
      Path(temporary).unlink(missing_ok=True)

  def save_configuration(self, args):
    path, content = self.config_content(args)
    if path.is_symlink():
      raise Refused("Review the symlinked settings file manually")
    before = path.read_text() if path.exists() else ""
    start, end = "-- BEGIN VEOS SETTINGS", "-- END VEOS SETTINGS"
    existing = re.search(
      re.escape(start) + r"\n(.*?)\n" + re.escape(end), before, flags=re.S
    )
    previous = {}
    if existing:
      # Persist only generated, typed settings. Never evaluate user Lua.
      lines = existing[1].splitlines()
      for line in lines:
        match = re.fullmatch(r"hl.config\((.+)\)", line)
        if not match:
          raise Refused(
            "VeOS settings were edited manually; review the block before changing it"
          )
        key = line.rsplit(" = ", 1)[0]
        previous[key] = line
    for line in content.splitlines():
      previous[line.rsplit(" = ", 1)[0]] = line
    body = re.sub(
      re.escape(start) + r".*?" + re.escape(end), "", before, flags=re.S
    ).rstrip()
    desired = (
      body + "\n\n" + start + "\n" + "\n".join(previous.values()) + "\n" + end + "\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, backup = tempfile.mkstemp(prefix=path.name + ".veos-backup-", dir=path.parent)
    with os.fdopen(fd, "w") as output:
      output.write(before)
    self.write_atomic(path, desired)
    try:
      self.run(["hyprctl", "reload"])
      errors = self.run(["hyprctl", "configerrors"]).decode().strip()
      if errors and errors != "ok":
        raise Refused(
          "Hyprland reported configuration errors; the previous file was restored"
        )
    except Exception:  # noqa: BLE001 — Restore before propagating reload failures.
      if path.read_text() == desired:
        self.write_atomic(path, before)
        self.run(["hyprctl", "reload"])
      raise
    return {
      "file": str(path),
      "backup": backup,
      "configuration_saved": True,
      "verification": "Inspect the resulting desktop behavior",
    }

  def config_content(self, args):
    area, values = args.get("area"), args.get("values")
    if not isinstance(values, dict) or not values:
      raise Refused("Specify supported configuration values")
    schema = {
      "appearance": {
        "gaps_in": ("general.gaps_in", int, 0, 50),
        "gaps_out": ("general.gaps_out", int, 0, 100),
        "border_size": ("general.border_size", int, 0, 10),
        "rounding": ("decoration.rounding", int, 0, 40),
      },
      "input": {
        "sensitivity": ("input.sensitivity", float, -1, 1),
        "natural_scroll": ("input.touchpad.natural_scroll", bool, None, None),
        "tap_to_click": ("input.touchpad.tap-to-click", bool, None, None),
      },
    }
    if area not in schema or set(values) - set(schema[area]):
      raise Refused(
        "Unsupported settings; use the named settings panel for other customizations"
      )
    entries = []
    for key, value in values.items():
      setting, kind, low, high = schema[area][key]
      valid = type(value) is kind or kind is float and type(value) is int
      if not valid or low is not None and not low <= value <= high:
        raise Refused("Configuration value is outside its supported range")
      encoded = str(value).lower()
      parts = setting.split(".")
      node = encoded
      for part in reversed(parts):
        node = '{ ["' + part + '"] = ' + node + " }"
      entries.append("hl.config(" + node + ")")
    root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / "hypr" / (
      "looknfeel.lua" if area == "appearance" else "input.lua"
    ), "\n".join(entries)

  def invoke(self, pid, op, args):
    self.validate(op, args)
    if op != "read_document" and (
      sys.platform != "linux" or not os.environ.get("WAYLAND_DISPLAY")
    ):
      raise Refused("Desktop control requires an active Linux Wayland user session")
    verified = False
    result = None
    if op in {"windows", "displays"}:
      result = self.json_command(
        "hyprctl", "clients" if op == "windows" else "monitors", "-j"
      )
    elif op == "apps":
      result = [
        {"id": item["id"], "name": item["name"]} for item in self.apps().values()
      ]
    elif op == "open_app":
      entry = self.apps().get(args.get("app_id"))
      if not entry:
        raise Refused("Choose an installed application")
      self.run(["gio", "launch", entry["path"]])
      result = {
        "launched": entry["id"],
        "verification": "Inspect windows/native_read to verify the application opened",
      }
    elif op == "focus_window":
      address = args["address"]
      windows = self.json_command("hyprctl", "clients", "-j")
      if not any(w.get("address") == address for w in windows):
        raise Refused("Window no longer exists")
      self.run(
        ["hyprctl", "dispatch", 'hl.dsp.focus({ window = "address:' + address + '" })']
      )
      result = self.json_command("hyprctl", "activewindow", "-j")
      verified = result.get("address") == address
    elif op in {"themes", "fonts"}:
      result = self.run([COMMANDS[op]]).decode().splitlines()[:300]
    elif op in {"set_theme", "set_font"}:
      name = bounded(args.get("name"), "selection", 200)
      listed = (
        self.run([COMMANDS["themes" if op == "set_theme" else "fonts"]])
        .decode()
        .splitlines()
      )
      if name not in listed:
        raise Refused("Choose an installed theme/font returned by discovery")
      self.run([COMMANDS[op], name], timeout=45)
      result = {
        "requested": name,
        "verification": "Inspect the desktop after the settings change",
      }
    elif op == "open_settings":
      area = args["area"]
      if area in {"displays", "startup"}:
        config = (
          Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
          / "hypr"
          / ("monitors.lua" if area == "displays" else "autostart.lua")
        )
        self.run(["omarchy-launch-editor", str(config)])
      elif area == "notifications":
        self.run(["omarchy-shell", "shell", "summon", "omarchy.notifications"])
      else:
        self.run(SETTINGS[area])
      result = {"opened": area, "settings_changed": False}
    elif op == "configure":
      result = self.save_configuration(args)
    elif op in {"audio", "set_audio"}:
      device = self.audio_device(args)
      if op == "set_audio":
        if "volume" in args:
          self.run(["wpctl", "set-volume", device, str(args["volume"]) + "%"])
        if "muted" in args:
          self.run(["wpctl", "set-mute", device, "1" if args["muted"] else "0"])
      raw = self.run(["wpctl", "get-volume", device]).decode()
      match = re.search(r"Volume:\s+([0-9.]+)", raw)
      if not match:
        raise Refused("Audio state could not be verified")
      result = {"volume": round(float(match[1]) * 100), "muted": "MUTED" in raw}
      verified = all(result[k] == v for k, v in args.items() if k != "device")
    elif op in {"clipboard_read", "clipboard_write"}:
      if op == "clipboard_write":
        self.run(["wl-copy", "--type", "text/plain"], args["text"].encode())
      text = self.run(["wl-paste", "--no-newline", "--type", "text/plain"]).decode()
      result = {"text": text[:10000], "truncated": len(text) > 10000}
      verified = op == "clipboard_write" and text == args["text"]
    elif op == "screenshot":
      result = Artifacts(self.store).save(
        pid, "desktop.png", self.run(["grim", "-"]), "image/png"
      )
      verified = True
    elif op == "read_document":
      meta, data = Artifacts(self.store).read(pid, args.get("artifact_id"))
      # Browser downloads can have no MIME metadata; inspect their signature.
      is_pdf = meta["media_type"] == "application/pdf" or data.startswith(b"%PDF-")
      is_image = meta["media_type"] in {"image/png", "image/jpeg"} or data.startswith(
        (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff")
      )
      if is_pdf:
        raw = self.run(["pdftotext", "-layout", "-", "-"], data)
      elif is_image:
        raw = self.run(["tesseract", "stdin", "stdout"], data)
      else:
        return Artifacts(self.store).read_text(
          pid, args.get("artifact_id"), args.get("offset", 0)
        )
      offset = integer(args.get("offset", 0), "text offset", 0, 5_000_000)
      text = raw.decode("utf-8")
      result = {
        "file": meta,
        "text": text[offset : offset + 12000],
        "next_offset": offset + 12000 if offset + 12000 < len(text) else None,
        "method": "pdftotext" if is_pdf else "tesseract",
        "untrusted": True,
      }
    elif op.startswith("native_"):
      result = json.loads(
        self.run(
          [
            "/usr/bin/python3",
            str(Path(__file__).with_name("native_accessibility.py")),
          ],
          json.dumps({"operation": op, "args": args}).encode(),
        )
      )
      if result.get("error"):
        raise Refused(result["error"])
      # A field read-back proves the edit, not completion of the user's task.
      verified = False
    return {
      "operation": op,
      "result": result,
      "untrusted": op in READS,
      "business_outcome_confirmed": verified,
    }
