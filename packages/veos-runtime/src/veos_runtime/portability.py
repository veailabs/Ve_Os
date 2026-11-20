"""Host-specific configuration around one local MCP implementation."""

import json
import signal
import sys
import urllib.request
from urllib.parse import urlsplit

from .store import Refused


def client_config(client, state_dir, project_id, mode="control", provider=None):
  if mode == "delegate" and provider not in {"codex", "claude", "hermes"}:
    raise Refused("Choose a native provider for delegation")
  if mode == "control" and provider:
    raise Refused("Direct controls use the connected harness, not another provider")
  args = [
    "-m",
    "veos_runtime",
    "--state-dir",
    str(state_dir.resolve()),
    "control-mcp" if mode == "control" else "incoming-mcp",
    "--project-id",
    project_id,
  ]
  if mode == "delegate":
    args += ["--provider", provider]
  config = {"command": sys.executable, "args": args}
  if client == "codex":
    return (
      "[mcp_servers.veos]\n"
      + "\n".join(f"{key} = {json.dumps(value)}" for key, value in config.items())
      + "\n"
    )
  key = "mcp_servers" if client == "hermes" else "mcpServers"
  return json.dumps({key: {"veos": config}}, indent=2) + "\n"


def serve_connection(store, project_id, mode, provider=None):
  info = json.loads((store.root / "connection.json").read_text())
  parsed = urlsplit(info["url"])
  if (
    parsed.scheme != "http"
    or parsed.hostname != "127.0.0.1"
    or parsed.username
    or parsed.password
    or parsed.path
    or parsed.query
    or parsed.fragment
  ):
    raise Refused("Only the local running VeOS service is supported")
  prefix = "/api/control" if mode == "control" else "/api/harness"

  def post(path, body, token, timeout=10):
    request = urllib.request.Request(
      info["url"] + prefix + path,
      json.dumps(body).encode(),
      {"Authorization": "Bearer " + token, "Content-Type": "application/json"},
      method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
      return json.loads(response.read(20000))

  data = {"pid": project_id}
  if mode == "delegate":
    data["provider"] = provider
  connection = post("/connect", data, info.pop("token"))

  def stop(*_):
    raise SystemExit(0)

  previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGTERM, signal.SIGINT)}
  try:
    if mode == "control":
      from .control_session import CONTROL_TOOLS
      from .mcp import serve

      serve(info["url"], connection["token"], CONTROL_TOOLS, "/api/control/tools")
    else:
      from .harness import serve_stdio

      serve_stdio(info["url"], connection["token"])
  finally:
    for sig, handler in previous.items():
      signal.signal(sig, handler)
    try:
      post("/disconnect", {}, connection["token"], timeout=5)
    except OSError:
      pass  # Runtime expiry/recovery fences any interrupted connection.
