"""Run reviewed desktop actions in the disposable installed Omarchy VM only."""

import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

from veos_runtime.artifacts import Artifacts
from veos_runtime.controls import Controls
from veos_runtime.desktop_actions import DesktopActions
from veos_runtime.store import Refused, Store


def command(*args):
  return subprocess.run(
    args, check=True, capture_output=True, text=True, timeout=60
  ).stdout.strip()


def wait_for(check, message, timeout=30):
  deadline = time.monotonic() + timeout
  while time.monotonic() < deadline:
    result = check()
    if result:
      return result
    time.sleep(0.25)
  raise AssertionError(message)


def main():
  assert os.environ.get("VEOS_DISPOSABLE_ACCEPTANCE") == "1"
  assert command("systemd-detect-virt", "--vm") in {"kvm", "qemu"}
  assert os.environ.get("WAYLAND_DISPLAY")
  artifacts = Path(os.environ["OMARCHY_ACCEPTANCE_DIR"])
  artifacts.mkdir(parents=True, exist_ok=True)
  config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "hypr"
  paths = [config / "looknfeel.lua", config / "input.lua"]
  original_files = {p: p.read_bytes() if p.exists() else None for p in paths}
  original_theme = command("omarchy-theme-current")
  original_font = command("omarchy-font-current")
  original_clients = {
    w["address"] for w in json.loads(command("hyprctl", "clients", "-j"))
  }
  original_clipboard = subprocess.run(
    ["wl-paste", "--no-newline", "--type", "text/plain"],
    capture_output=True,
    timeout=10,
  )
  checks = []
  changed_theme = changed_font = False
  cleanup_errors = []

  def capture(name):
    subprocess.run(["grim", str(artifacts / (name + ".png"))], check=True, timeout=15)

  def passed(name):
    checks.append(name)
    print("ok - " + name, flush=True)

  def option(name):
    return json.loads(command("hyprctl", "getoption", name, "-j"))

  def menu_ready():
    return (
      subprocess.run(
        ["omarchy-menu", "ping"], capture_output=True, timeout=10
      ).returncode
      == 0
    )

  def cleanup_call(*args):
    try:
      command(*args)
    except (OSError, subprocess.SubprocessError) as exc:
      cleanup_errors.append(type(exc).__name__ + ": " + args[0])

  try:
    with tempfile.TemporaryDirectory(prefix="veos-wayland-") as directory:
      store = Store(directory)
      pid = store.create_project(
        "Wayland acceptance", "Exercise disposable desktop", max_runs=50
      )
      controls = Controls(store)
      desktop = DesktopActions(store, controls)
      counter = 0

      def proposal(operation, args):
        nonlocal counter
        counter += 1
        tid = store.create_task(pid, "Acceptance " + operation, "codex", str(counter))
        assert store.claim(tid)
        desktop.grant(pid, operation, True)
        action = desktop.call(pid, tid, operation, args)
        assert action["state"] == "pending"
        return tid, action["action_id"]

      def act(operation, args):
        tid, aid = proposal(operation, args)
        decision = desktop.decide(aid, True)
        receipt = next(a for a in store.task_actions(tid) if a["id"] == aid)
        assert decision["state"] == "succeeded", receipt
        return receipt["result"]

      def read(operation, args=None):
        desktop.grant(pid, operation, True)
        return desktop.call(pid, None, operation, args or {})["result"]

      assert read("displays")
      capture("success-veos-01-original")
      apps = read("apps")
      app = next(a for a in apps if a["id"] == "foot.desktop")
      act("open_app", {"app_id": app["id"]})
      window = wait_for(
        lambda: next(
          (
            w
            for w in read("windows")
            if w["address"] not in original_clients and w["class"].lower() == "foot"
          ),
          None,
        ),
        "The installed Foot application did not open",
      )
      result = act("focus_window", {"address": window["address"]})
      assert result["business_outcome_confirmed"] is True
      assert (
        json.loads(command("hyprctl", "activewindow", "-j"))["address"]
        == window["address"]
      )
      capture("success-veos-02-open-focus")
      passed(
        "installed application discovery, reviewed launch and actual Wayland focus"
      )

      act("clipboard_write", {"text": "VeOS disposable clipboard baseline"})
      tid, aid = proposal("clipboard_write", {"text": "must wait for approval"})
      assert read("clipboard_read")["text"] == "VeOS disposable clipboard baseline"
      desktop.grant(pid, "clipboard_write", False)
      assert desktop.decide(aid, True)["state"] == "failed"
      assert read("clipboard_read")["text"] == "VeOS disposable clipboard baseline"
      tid, aid = proposal("clipboard_write", {"text": "must not run while paused"})
      store.set_project_state(pid, "paused")
      try:
        desktop.decide(aid, True)
      except Refused:
        pass
      else:
        raise AssertionError("Paused project performed a desktop action")
      store.set_project_state(pid, "active")
      assert read("clipboard_read")["text"] == "VeOS disposable clipboard baseline"
      act("clipboard_write", {"text": "VeOS reviewed clipboard update"})
      assert read("clipboard_read")["text"] == "VeOS reviewed clipboard update"
      passed("real clipboard, pending review, revoked grant and paused project")

      result = act("screenshot", {})
      _, screenshot = Artifacts(store).read(pid, result["result"]["id"])
      assert screenshot.startswith(b"\x89PNG\r\n\x1a\n")
      (artifacts / "success-veos-03-reviewed-capture.png").write_bytes(screenshot)
      passed("reviewed compositor screenshot stored as a project artifact")

      themes = read("themes")
      theme = next(t for t in themes if t != original_theme)
      changed_theme = True
      act("set_theme", {"name": theme})
      wait_for(
        lambda: command("omarchy-theme-current") == theme, "Theme did not change"
      )
      capture("success-veos-04-theme")
      passed("discovered theme applied through Omarchy and independently read back")
      fonts = read("fonts")
      font = next(f for f in fonts if f != original_font)
      changed_font = True
      act("set_font", {"name": font})
      wait_for(lambda: command("omarchy-font-current") == font, "Font did not change")
      capture("success-veos-05-font")
      passed("discovered monospace font applied and independently read back")

      before = paths[0].read_text()
      result = act("configure", {"area": "appearance", "values": {"rounding": 13}})
      assert Path(result["result"]["backup"]).read_text() == before
      assert option("decoration.rounding")["int"] == 13
      act("configure", {"area": "appearance", "values": {"border_size": 3}})
      assert option("decoration.rounding")["int"] == 13
      assert option("general.border_size")["int"] == 3
      assert before.rstrip() in paths[0].read_text()
      act(
        "configure",
        {
          "area": "input",
          "values": {"sensitivity": 0.25, "natural_scroll": True, "tap_to_click": True},
        },
      )
      assert abs(option("input.sensitivity")["float"] - 0.25) < 0.001
      assert option("input.touchpad.natural_scroll")["bool"] is True
      assert option("input.touchpad.tap_to_click")["bool"] is True
      capture("success-veos-06-configuration")
      passed(
        "Lua settings loaded by Hyprland, private backup and earlier overrides preserved"
      )

      before = paths[0].read_bytes()
      input_before = paths[1].read_bytes()
      try:
        paths[1].write_bytes(
          input_before + b'\nerror("VeOS disposable reload fault")\n'
        )
        tid, aid = proposal(
          "configure", {"area": "appearance", "values": {"rounding": 17}}
        )
        assert desktop.decide(aid, True)["state"] == "failed", store.task_actions(tid)
        assert paths[0].read_bytes() == before
      finally:
        paths[1].write_bytes(input_before)
        command("hyprctl", "reload")
      assert command("hyprctl", "configerrors") in {"", "ok"}
      assert option("decoration.rounding")["int"] == 13
      passed("actual compositor parse failure rolls back the changed settings file")

      wait_for(menu_ready, "Menu did not become ready")
      act("open_settings", {"area": "appearance"})
      wait_for(
        lambda: "omarchy-menu" in command("hyprctl", "layers", "-j"),
        "Appearance settings panel did not open",
      )
      capture("success-veos-07-settings-panel")
      command("omarchy-menu", "close")
      passed("existing appearance settings panel opens in the Wayland shell")
  except BaseException:
    capture("failure-veos-controls")
    raise
  finally:
    cleanup_call("omarchy-menu", "close")
    if changed_theme:
      cleanup_call("omarchy-theme-set", original_theme)
    if changed_font:
      cleanup_call("omarchy-font-set", original_font)
    for path, data in original_files.items():
      if data is None:
        path.unlink(missing_ok=True)
      else:
        path.write_bytes(data)
    cleanup_call("hyprctl", "reload")
    for window in json.loads(command("hyprctl", "clients", "-j")):
      if (
        window["address"] not in original_clients and window["class"].lower() == "foot"
      ):
        cleanup_call(
          "hyprctl",
          "dispatch",
          'hl.dsp.window.close({ window = "address:' + window["address"] + '" })',
        )
    subprocess.run(
      ["wl-copy", "--type", "text/plain"]
      if original_clipboard.returncode == 0
      else ["wl-copy", "--clear"],
      input=original_clipboard.stdout if original_clipboard.returncode == 0 else None,
      check=True,
      timeout=10,
    )
    capture("success-veos-08-restored")
  assert not cleanup_errors, cleanup_errors
  assert command("omarchy-theme-current") == original_theme
  assert command("omarchy-font-current") == original_font
  assert all(
    (p.read_bytes() if p.exists() else None) == data
    for p, data in original_files.items()
  )
  assert command("hyprctl", "configerrors") in {"", "ok"}
  passed("original theme, font and personal Hyprland files restored")
  (artifacts / "veos-result.json").write_text(
    json.dumps({"result": "passed", "checks": checks}, indent=2) + "\n"
  )


if __name__ == "__main__":
  main()
