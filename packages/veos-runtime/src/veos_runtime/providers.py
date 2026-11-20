import json
import os
import selectors
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from .store import Refused, encoded

PROVIDERS = {
  "codex": "codex",
  "claude": "claude",
  "hermes": "hermes",
  "antigravity": "agy",
}
SAFE_ENV = {
  "HOME",
  "PATH",
  "USER",
  "LOGNAME",
  "SHELL",
  "LANG",
  "LC_ALL",
  "TMPDIR",
  "TERM",
  "XDG_CONFIG_HOME",
  "XDG_DATA_HOME",
  "XDG_STATE_HOME",
  "XDG_RUNTIME_DIR",
  "DBUS_SESSION_BUS_ADDRESS",
}


def clean_env():
  env = {key: value for key, value in os.environ.items() if key in SAFE_ENV}
  env.update({"NO_COLOR": "1", "TERM": "dumb"})
  return env


def default_config():
  return {
    "version": 1,
    "billing": "subscription_only",
    "paid_api_fallback": False,
    "execution": "local_user",
    "timeout_seconds": 600,
    "providers": {
      p: {"enabled": False, "extra_usage_disabled_confirmed": False} for p in PROVIDERS
    },
    "ve": {
      "endpoint": "",
      "connection_id": "",
      "read_tools": [],
      "write_tools": [],
    },
  }


def read_config(root):
  path = Path(root) / "config.json"
  if not path.exists():
    save_config(root, default_config())
  if path.is_symlink() or path.stat().st_uid != os.getuid():
    raise Refused("Unsafe configuration file")
  config = json.loads(path.read_text())
  if not isinstance(config, dict) or config.get("version") != 1:
    raise Refused("Unsupported configuration version")
  if isinstance(config.get("providers"), dict) and set(config["providers"]) == {
    "codex",
    "claude",
    "antigravity",
  }:
    config["providers"]["hermes"] = {
      "enabled": False,
      "extra_usage_disabled_confirmed": False,
    }
  if (
    config.get("billing") != "subscription_only"
    or config.get("paid_api_fallback") is not False
  ):
    raise Refused(
      "This runtime only supports subscription-only execution with no paid API fallback"
    )
  if config.get("execution") != "local_user":
    raise Refused("Hosted subscription execution is not enabled by this implementation")
  if (
    type(config.get("timeout_seconds")) is not int
    or not 10 <= config["timeout_seconds"] <= 1800
  ):
    raise Refused("Invalid execution timeout")
  if not isinstance(config.get("providers"), dict) or set(config["providers"]) != set(
    PROVIDERS
  ):
    raise Refused("Every supported provider needs an explicit configuration")
  ve = config.get("ve")
  if (
    not isinstance(ve, dict)
    or not isinstance(ve.get("endpoint"), str)
    or not isinstance(ve.get("connection_id"), str)
  ):
    raise Refused("Invalid Ve connection")
  for kind in ("read_tools", "write_tools"):
    if (
      not isinstance(ve.get(kind), list)
      or len(ve[kind]) > 100
      or any(not isinstance(x, str) or not x or len(x) > 200 for x in ve[kind])
    ):
      raise Refused("Invalid Ve tool allowlist")
  if set(ve["read_tools"]) & set(ve["write_tools"]):
    raise Refused("A tool cannot be both read-only and a write")
  for p, setting in config["providers"].items():
    if (
      p not in PROVIDERS
      or not isinstance(setting, dict)
      or set(setting) - {"enabled", "extra_usage_disabled_confirmed", "model"}
    ):
      raise Refused("Invalid provider configuration")
    if any(
      type(setting.get(k)) is not bool
      for k in ("enabled", "extra_usage_disabled_confirmed")
    ):
      raise Refused("Provider enablement and billing confirmation must be booleans")
    if setting.get("model") and (
      not isinstance(setting["model"], str)
      or len(setting["model"]) > 160
      or setting["model"].startswith("-")
    ):
      raise Refused("Invalid model")
  return {**config, "_state_dir": str(Path(root).resolve())}


def save_config(root, config):
  path = Path(root) / "config.json"
  fd, name = tempfile.mkstemp(prefix="config-", suffix=".tmp", dir=root)
  try:
    with os.fdopen(fd, "w") as file:
      file.write(
        json.dumps(
          {k: v for k, v in config.items() if k != "_state_dir"},
          indent=2,
          allow_nan=False,
        )
        + "\n"
      )
      file.flush()
      os.fsync(file.fileno())
    os.replace(name, path)
  finally:
    Path(name).unlink(missing_ok=True)


def probe(provider, config, run=subprocess.run):
  settings = config.get("providers", {}).get(provider, {})
  executable = shutil.which(PROVIDERS[provider])
  if not executable:
    return {"ready": False, "reason": "not_installed"}
  if not settings.get("enabled"):
    return {"ready": False, "reason": "not_enabled"}
  if not settings.get("extra_usage_disabled_confirmed"):
    return {"ready": False, "reason": "billing_not_confirmed"}
  if provider == "antigravity":
    return {"ready": False, "reason": "subscription_auth_probe_not_qualified"}
  if provider == "hermes":
    from .hermes import check

    try:
      return check(executable, config, clean_env(), run)
    except (Refused, OSError, ValueError, subprocess.TimeoutExpired):
      return {"ready": False, "reason": "hermes_profile_unavailable"}
  argv = (
    [executable, "login", "status"]
    if provider == "codex"
    else [executable, "auth", "status", "--json"]
  )
  try:
    result = run(argv, env=clean_env(), capture_output=True, text=True, timeout=15)
  except (OSError, subprocess.TimeoutExpired):
    return {"ready": False, "reason": "auth_probe_unavailable"}
  if result.returncode:
    return {"ready": False, "reason": "sign_in_required"}
  if provider == "codex":
    status = (result.stdout + result.stderr).lower()
    ready = "logged in using chatgpt" in status and "api key" not in status
  else:
    try:
      status = json.loads(result.stdout)
      ready = (
        status.get("loggedIn") is True
        and status.get("authMethod") in {"claude.ai", "oauth"}
        and status.get("subscriptionType")
        in {"pro", "max", "ultra", "team", "enterprise"}
        and not status.get("apiKeySource")
      )
    except (ValueError, AttributeError):
      ready = False
  return {
    "ready": ready,
    "reason": "subscription" if ready else "subscription_not_verified",
  }


def commands(provider, executable, workdir, session_dir, model=None):
  common = [] if model is None else ["--model", model]
  mcp = session_dir / "mcp.json"
  if provider == "codex":
    settings = json.loads(mcp.read_text())["mcpServers"]["veos"]
    return [
      executable,
      "exec",
      "--ignore-user-config",
      "--ignore-rules",
      "--json",
      "--ephemeral",
      "--skip-git-repo-check",
      "--sandbox",
      "read-only",
      "-c",
      'approval_policy="never"',
      "-c",
      'model_provider="openai"',
      "-c",
      'forced_login_method="chatgpt"',
      "-c",
      "mcp_servers.veos.command=" + json.dumps(settings["command"]),
      "-c",
      "mcp_servers.veos.args=" + json.dumps(settings["args"]),
      "-c",
      'mcp_servers.veos.env_vars=["VEOS_TASK_TOKEN"]',
      "--cd",
      str(workdir),
      *common,
      "-",
    ]
  if provider == "claude":
    return [
      executable,
      "--print",
      "--output-format",
      "stream-json",
      "--verbose",
      "--restricted",
      "--permission-mode",
      "dontAsk",
      "--tools",
      "Read,Glob,Grep,WebSearch",
      "--strict-mcp-config",
      "--mcp-config",
      str(mcp),
      "--setting-sources",
      "",
      "--allowedTools",
      "Read,Glob,Grep,WebSearch,mcp__veos__project_context,mcp__veos__available_tools,mcp__veos__list_files,mcp__veos__read_file,mcp__veos__write_report,mcp__veos__search_evidence,mcp__veos__get_evidence,mcp__veos__record_evidence,mcp__veos__record_claim,mcp__veos__propose_task,mcp__veos__ve_call,mcp__veos__browser,mcp__veos__desktop",
      *common,
    ]
  if provider == "hermes":
    if not model:
      raise Refused("Select a subscription model before enabling Hermes")
    return [
      executable,
      "chat",
      "--provider",
      "openai-codex",
      *common,
      "--toolsets",
      "veos",
      "--ignore-rules",
      "--format",
      "stream-json",
      "--max-turns",
      "20",
      "--in",
      str(workdir),
      "--query-file",
      "-",
    ]
  raise Refused(
    "This provider's unattended subscription authentication has not been qualified"
  )


@dataclass
class Outcome:
  state: str
  result: str = ""
  reason: str | None = None


class StreamResult:
  def __init__(self, provider):
    self.provider = provider
    self.text = ""
    self.completed = False
    self.error = None

  def feed(self, line):
    try:
      event = json.loads(line)
    except ValueError:
      return
    if not isinstance(event, dict):
      return
    if self.provider == "codex":
      item = event.get("item", {})
      if (
        event.get("type") == "item.completed"
        and isinstance(item, dict)
        and item.get("type") == "agent_message"
      ):
        self.text = str(item.get("text", ""))[:200_000]
      if event.get("type") == "turn.completed":
        self.completed = True
      if event.get("type") in {"turn.failed", "error"}:
        self.error = self.classify(event)
    elif self.provider == "hermes":
      if event.get("type") == "text" and isinstance(event.get("text"), str):
        self.text = (self.text + event["text"])[:200_000]
      if event.get("type") == "result":
        if (
          type(event.get("exit_code")) is not int
          or event["exit_code"] != 0
          or event.get("error")
        ):
          self.error = self.classify(event)
        elif isinstance(event.get("text"), str):
          self.completed = True
          self.text = event["text"][:200_000]
    elif event.get("type") == "result":
      if event.get("is_error") or event.get("subtype") != "success":
        self.error = self.classify(event)
      else:
        self.completed = True
        self.text = str(event.get("result", ""))[:200_000]

  def classify(self, event):
    text = encoded(event).lower()
    if any(
      x in text
      for x in ("rate_limit", "usage_limit", "usage limit", "quota", "rate limit")
    ):
      return "subscription_limit"
    if any(
      x in text
      for x in (
        "authentication",
        "not logged in",
        "unauthorized",
        "login required",
      )
    ):
      return "sign_in_required"
    return "provider_error"

  def outcome(self, code):
    if self.error == "subscription_limit":
      return Outcome("waiting_subscription", self.text, self.error)
    if self.error:
      return Outcome("failed", self.text, self.error)
    if code == 0 and self.completed and self.text.strip():
      return Outcome("succeeded", self.text)
    return Outcome("unknown", self.text, "completion_not_confirmed")


def terminate(process):
  # The provider can exit before its MCP children. Stop the whole owned group.
  try:
    os.killpg(process.pid, signal.SIGTERM)
    if process.poll() is None:
      process.wait(timeout=3)
  except (ProcessLookupError, subprocess.TimeoutExpired):
    pass
  finally:
    try:
      os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
      pass
    process.wait(timeout=3)


def execute(
  provider,
  argv,
  prompt,
  cwd,
  timeout,
  permitted,
  checkpoint=lambda text: None,
  task_token=None,
  provider_env=None,
):
  parser, total, buffers = StreamResult(provider), 0, {}
  try:
    env = clean_env() if provider_env is None else provider_env
    if task_token:
      env["VEOS_TASK_TOKEN"] = task_token
    process = subprocess.Popen(
      argv,
      cwd=cwd,
      env=env,
      stdin=subprocess.PIPE,
      stdout=subprocess.PIPE,
      stderr=subprocess.PIPE,
      start_new_session=True,
    )
  except OSError:
    return Outcome("blocked", reason="provider_start_failed")
  try:
    pending = memoryview(prompt.encode())
    deadline = time.monotonic() + timeout
    with selectors.DefaultSelector() as selector:
      os.set_blocking(process.stdin.fileno(), False)
      selector.register(process.stdin, selectors.EVENT_WRITE)
      for pipe in (process.stdout, process.stderr):
        os.set_blocking(pipe.fileno(), False)
        selector.register(pipe, selectors.EVENT_READ)
        buffers[pipe.fileno()] = b""
      while selector.get_map():
        if not permitted():
          return Outcome("unknown", parser.text, "stopped_or_paused")
        if time.monotonic() >= deadline:
          return Outcome("unknown", parser.text, "execution_timeout")
        for key, _ in selector.select(timeout=0.2):
          if key.fileobj is process.stdin:
            if pending:
              try:
                pending = pending[os.write(key.fd, pending[:65536]) :]
              except BrokenPipeError:
                pending = memoryview(b"")
            if not pending:
              selector.unregister(process.stdin)
              process.stdin.close()
            continue
          chunk = os.read(key.fd, 65536)
          if not chunk:
            if key.fileobj is process.stdout and buffers[key.fd]:
              parser.feed(buffers[key.fd].decode("utf-8", errors="replace"))
            selector.unregister(key.fileobj)
            continue
          total += len(chunk)
          if total > 4_000_000:
            return Outcome("unknown", parser.text, "output_limit")
          if key.fileobj is process.stderr:
            continue
          buffers[key.fd] += chunk
          while b"\n" in buffers[key.fd]:
            line, buffers[key.fd] = buffers[key.fd].split(b"\n", 1)
            before = parser.text
            parser.feed(line.decode("utf-8", errors="replace"))
            if parser.text != before:
              checkpoint(parser.text)
          if len(buffers[key.fd]) > 1_000_000:
            return Outcome("unknown", parser.text, "event_limit")
    return parser.outcome(process.wait(timeout=5))
  except (BrokenPipeError, subprocess.TimeoutExpired):
    return Outcome("unknown", parser.text, "provider_interrupted")
  finally:
    terminate(process)
    process.stdin.close()
    process.stdout.close()
    process.stderr.close()
