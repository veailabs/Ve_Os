"""The qualified native Hermes CLI, in a VeOS-owned subscription profile."""

import hashlib
import json
import os
import subprocess
from pathlib import Path

from .store import Refused

REVISION = "2de9f51b8008"
PROVIDER = "openai-codex"


def profile(state_dir):
  root = Path(state_dir).resolve()
  home = root / "hermes"
  marker = home / "veos-profile.json"
  if home.is_symlink():
    raise Refused("The VeOS Hermes profile must not be a symlink")
  if home.exists() and not marker.is_file():
    raise Refused("Refusing to adopt an existing Hermes profile")
  home.mkdir(mode=0o700, exist_ok=True)
  if home.stat().st_uid != os.getuid() or home.stat().st_mode & 0o077:
    raise Refused("The VeOS Hermes profile must be private to this user")
  identity = {"owner": "veos", "state_dir": str(root), "version": 1}
  if marker.exists():
    if marker.is_symlink() or json.loads(marker.read_text()) != identity:
      raise Refused("The Hermes profile belongs to another runtime")
  else:
    marker.write_text(json.dumps(identity))
    marker.chmod(0o600)
  for name in ("config.yaml", ".env", "plugins", "hooks"):
    path = home / name
    if path.is_symlink():
      raise Refused("The VeOS Hermes profile cannot link external configuration")
  env_file = home / ".env"
  if env_file.exists() and env_file.read_text().strip():
    raise Refused(
      "Keep API credentials and custom environment out of the VeOS Hermes profile"
    )
  for name in ("plugins", "hooks"):
    path = home / name
    if path.is_dir() and any(path.iterdir()):
      raise Refused(
        "Custom plugins and hooks are not enabled in the VeOS Hermes profile"
      )
  return home


def environment(state_dir, base):
  home = profile(state_dir)
  return {
    **base,
    "HERMES_HOME": str(home),
    "HERMES_PROFILE_NAME": "veos-"
    + hashlib.sha256(str(home).encode()).hexdigest()[:12],
    "HERMES_IGNORE_RULES": "1",
    "HERMES_DISABLE_LAZY_INSTALLS": "1",
  }


def configure(state_dir, model, mcp=None, timeout=600):
  home = profile(state_dir)
  config = {
    "_config_version": 54,
    "model": {"provider": PROVIDER, "default": model},
    "fallback_model": [],
    "gateway": {"standalone": True},
    "agent": {"max_turns": 20, "run_budget_seconds": timeout},
    "compression": {"enabled": False},
    "tools": {"tool_search": {"enabled": "off"}},
    "auxiliary": {
      "title_generation": {"enabled": False, "model_upgrade_enabled": False},
      "background_review": {"enabled": False},
    },
    "memory": {"memory_enabled": False, "user_profile_enabled": False},
    "platform_toolsets": {"cli": ["veos"] if mcp else []},
    "mcp_servers": {"veos": mcp} if mcp else {},
    "plugins": {"enabled": []},
    "hooks": {},
  }
  temp = home / "config.next"
  if temp.is_symlink():
    raise Refused("Unsafe Hermes configuration staging file")
  with open(temp, "w", opener=lambda p, flags: os.open(p, flags, 0o600)) as file:
    json.dump(config, file)
  os.replace(temp, home / "config.yaml")
  return home


def qualified(executable, env, run=subprocess.run):
  result = run(
    [executable, "--version"], env=env, capture_output=True, text=True, timeout=15
  )
  return result.returncode == 0 and REVISION in result.stdout


def check(executable, config, env, run=subprocess.run):
  model = config["providers"]["hermes"].get("model")
  if not model:
    return {"ready": False, "reason": "hermes_model_required"}
  env = environment(config["_state_dir"], env)
  if (Path(env["HERMES_HOME"]) / "stop-unconfirmed").exists():
    return {"ready": False, "reason": "hermes_stop_unconfirmed"}
  if not qualified(executable, env, run):
    return {"ready": False, "reason": "hermes_version_unqualified"}
  result = run(
    [executable, "auth", "status", PROVIDER],
    env=env,
    capture_output=True,
    text=True,
    timeout=15,
  )
  ready = (
    result.returncode == 0 and f"{PROVIDER}: logged in" in result.stdout.splitlines()
  )
  return {
    "ready": ready,
    "reason": "subscription" if ready else "hermes_sign_in_required",
  }


def stop(executable, env, run=subprocess.run):
  home = Path(env["HERMES_HOME"])
  marker = home / "stop-unconfirmed"
  marker.write_text("The owned Hermes gateway must be stopped before another task.\n")
  try:
    result = run(
      [executable, "gateway", "stop", "--yes"],
      env=env,
      capture_output=True,
      text=True,
      timeout=12,
    )
    if result.returncode != 0:
      return False
    status = run(
      [executable, "gateway", "status"],
      env=env,
      capture_output=True,
      text=True,
      timeout=12,
    )
    if (
      status.returncode != 0
      or "✗ Gateway is not running" not in status.stdout.splitlines()
    ):
      return False
    marker.unlink(missing_ok=True)
    return True
  except (OSError, subprocess.TimeoutExpired):
    return False


def begin(env):
  marker = Path(env["HERMES_HOME"]) / "stop-unconfirmed"
  try:
    fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
  except FileExistsError:
    raise Refused(
      "Stop the previous owned Hermes gateway before another task"
    ) from None
  with os.fdopen(fd, "w") as file:
    file.write("A native run may remain active until gateway shutdown is confirmed.\n")
    file.flush()
    os.fsync(file.fileno())
