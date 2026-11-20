"""Install the shipped wheel/service/widget and click it in the disposable VM."""

import json
import os
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path


def command(*args, env=None, timeout=60):
  result = subprocess.run(
    args, check=False, capture_output=True, text=True, timeout=timeout, env=env
  )
  if result.returncode:
    raise AssertionError(
      f"Command {args!r} failed ({result.returncode})\n"
      f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
  return result.stdout.strip()


def wait_for(check, message, timeout=45):
  deadline = time.monotonic() + timeout
  while time.monotonic() < deadline:
    result = check()
    if result:
      return result
    time.sleep(0.3)
  raise AssertionError(message)


def main():
  assert os.environ.get("VEOS_DISPOSABLE_ACCEPTANCE") == "1"
  assert command("systemd-detect-virt", "--vm") in {"kvm", "qemu"}
  assert os.environ.get("WAYLAND_DISPLAY")
  home = Path.home()
  artifacts = Path(os.environ["OMARCHY_ACCEPTANCE_DIR"])
  package = Path(__file__).resolve().parents[1]
  wheels = package / "artifacts/wayland-wheel"
  wheel = next(wheels.glob("veos_runtime-*.whl"))
  pip = next(wheels.glob("pip-*.whl"))
  venv = home / ".local/share/veos-acceptance-venv"
  executable = home / ".local/bin/veos"
  state = home / ".local/state/veos"
  plugin = home / ".config/omarchy/plugins/veos.missions"
  service = home / ".config/systemd/user/veos-runtime.service"
  launcher = home / ".local/share/applications/veos.desktop"
  owned = [venv, executable, state, plugin, service, launcher]
  assert all(not p.exists() and not p.is_symlink() for p in owned), owned
  config = home / ".config/omarchy/shell.json"
  original_config = config.read_bytes() if config.exists() else None
  original_clients = {
    w["address"] for w in json.loads(command("hyprctl", "clients", "-j"))
  }
  since = command("date", "+%Y-%m-%d %H:%M:%S")
  clean_env = dict(os.environ)
  clean_env.pop("PYTHONPATH", None)
  started = False
  installed = False
  token = ""
  checks = []

  def passed(name):
    checks.append(name)
    print("ok - " + name, flush=True)

  def capture(name):
    command("grim", str(artifacts / (name + ".png")))

  try:
    command("python3", "-m", "venv", "--without-pip", str(venv))
    command(
      str(venv / "bin/python"),
      "-m",
      "pip",
      "install",
      "--no-index",
      "--no-deps",
      str(wheel),
      env={**clean_env, "PYTHONPATH": str(pip)},
    )
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.symlink_to(venv / "bin/veos")
    location = command(
      str(venv / "bin/python"),
      "-c",
      "import veos_runtime; print(veos_runtime.__file__)",
      env=clean_env,
    )
    assert Path(location).is_relative_to(venv), location
    command(str(executable), "install-desktop", env=clean_env)
    installed = True
    command("omarchy-plugin-validate", str(plugin))
    command("systemctl", "--user", "daemon-reload")
    command("systemctl", "--user", "enable", "--now", "veos-runtime.service")
    started = True
    connection = state / "connection.json"
    wait_for(connection.exists, "Installed user service did not start")
    info = json.loads(connection.read_text())
    token = info["token"]
    assert connection.stat().st_mode & 0o777 == 0o600
    assert (
      command("systemctl", "--user", "is-active", "veos-runtime.service") == "active"
    )
    assert (
      Path(
        command(
          "systemctl",
          "--user",
          "show",
          "veos-runtime.service",
          "--property=FragmentPath",
          "--value",
        )
      )
      == service
    )

    def request(path, payload=None):
      req = urllib.request.Request(
        info["url"] + path,
        None if payload is None else json.dumps(payload).encode(),
        {"Authorization": "Bearer " + token, "Content-Type": "application/json"},
      )
      with urllib.request.urlopen(req, timeout=5) as response:
        return response.read()

    assert b"Mission Desk" in request("/")
    request(
      "/api/projects",
      {
        "name": "Widget acceptance",
        "objective": "Verify the installed desktop launcher without model calls",
      },
    )
    settings = json.loads((state / "config.json").read_text())
    assert all(not v["enabled"] for v in settings["providers"].values())
    passed(
      "installed wheel, shipped user service, private session and disabled providers"
    )

    command("omarchy-shell", "shell", "rescanPlugins")
    wait_for(
      lambda: "veos.missions" in command("omarchy-shell", "shell", "listPlugins"),
      "Installed widget was not discovered",
    )
    (artifacts / "installed-plugin-enable.txt").write_text(
      Path(shutil.which("omarchy-plugin-enable")).read_text()
    )
    (artifacts / "installed-plugins.json").write_text(
      command("omarchy-shell", "shell", "listPlugins")
    )
    command("omarchy-plugin-enable", "veos.missions", "--section", "right")

    def geometry():
      entries = json.loads(command("omarchy-shell", "shell", "debugBarGeometry"))
      return next(
        (
          e
          for e in entries
          if e["id"] == "veos.missions"
          and e["visible"]
          and e["itemVisible"]
          and e["width"] > 0
        ),
        None,
      )

    position = wait_for(geometry, "Installed widget did not render in the live bar")
    capture("success-veos-09-installed-widget")
    (artifacts / "widget-geometry.json").write_text(
      json.dumps(position, indent=2) + "\n"
    )
    monitors = json.loads(command("hyprctl", "monitors", "-j"))
    assert len(monitors) == 1, "Fixture expects one VM monitor"
    layers = json.loads(command("hyprctl", "layers", "-j"))
    bar = next(
      layer
      for monitor in layers.values()
      for level in monitor["levels"].values()
      for layer in level
      if layer["namespace"] == "omarchy-bar"
    )
    x = round(monitors[0]["x"] + bar["x"] + position["x"] + position["width"] / 2)
    y = round(monitors[0]["y"] + bar["y"] + position["y"] + position["height"] / 2)
    command(
      "hyprctl", "eval", f"hl.dispatch(hl.dsp.cursor.move({{ x = {x}, y = {y} }}))"
    )
    (artifacts / "widget-click-request").touch()
    wait_for(
      (artifacts / "widget-click-sent").exists,
      "VM helper did not send the widget click",
    )
    wait_for(
      lambda: any(
        "Mission Desk" in w["title"]
        for w in json.loads(command("hyprctl", "clients", "-j"))
      ),
      "Clicking the actual widget did not open Mission Desk",
    )

    def visible_workspace():
      capture("success-veos-10-mission-desk")
      observed = command(
        "tesseract",
        str(artifacts / "success-veos-10-mission-desk.png"),
        "stdout",
        "--psm",
        "11",
      )
      return "Widget acceptance" in observed and "Personal workspace" in observed

    wait_for(
      visible_workspace,
      "Installed Mission Desk did not display its authenticated workspace",
    )
    journal = command(
      "journalctl", "--user", "--since", since, "-t", "omarchy-shell", "--no-pager"
    )
    assert not any(
      "veos.missions" in line
      and any(
        term in line.lower() for term in ("error", "failed", "not found", "unavailable")
      )
      for line in journal.splitlines()
    ), journal
    assert token not in journal
    (artifacts / "widget-shell.log").write_text(journal)
    actual_state = json.loads(request("/api/state"))
    assert not actual_state["tasks"]
    assert actual_state["projects"][0]["runs_used"] == 0
    passed(
      "real Omarchy widget click opens authenticated installed Mission Desk without model runs"
    )
  finally:
    journal = command(
      "journalctl", "--user", "--since", since, "-t", "omarchy-shell", "--no-pager"
    )
    (artifacts / "widget-shell.log").write_text(
      journal.replace(token, "[redacted]") if token else journal
    )
    (artifacts / "widget-click-request").unlink(missing_ok=True)
    if installed:
      subprocess.run(
        ["omarchy-plugin-disable", "veos.missions"], capture_output=True, timeout=15
      )
      if original_config is None:
        config.unlink(missing_ok=True)
      else:
        config.write_bytes(original_config)
      command("omarchy-shell", "shell", "reloadConfig")
    for window in json.loads(command("hyprctl", "clients", "-j")):
      if window["address"] not in original_clients:
        command(
          "hyprctl",
          "dispatch",
          'hl.dsp.window.close({ window = "address:' + window["address"] + '" })',
        )
    if started:
      command("systemctl", "--user", "disable", "--now", "veos-runtime.service")
      assert not (state / "connection.json").exists(), (
        "User service did not remove its session on clean stop"
      )
      journal = command(
        "journalctl",
        "--user",
        "-u",
        "veos-runtime.service",
        "--since",
        since,
        "--no-pager",
      )
      assert token not in journal
      (artifacts / "installed-service.log").write_text(journal)
    for path in owned:
      if path.is_symlink() or path.is_file():
        path.unlink()
      elif path.is_dir():
        shutil.rmtree(path)
    if installed:
      command("systemctl", "--user", "daemon-reload")
      command("omarchy-shell", "shell", "rescanPlugins")
    capture("success-veos-11-widget-cleanup")
  assert (config.read_bytes() if config.exists() else None) == original_config
  passed(
    "installed service stopped cleanly; shell configuration and test-owned assets restored"
  )
  (artifacts / "veos-widget-result.json").write_text(
    json.dumps({"result": "passed", "checks": checks}, indent=2) + "\n"
  )


if __name__ == "__main__":
  main()
