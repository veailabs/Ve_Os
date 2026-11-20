"""Exercise the real pinned Hermes gateway against a local inference fixture."""

import json
import secrets
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from veos_runtime.hermes import begin, configure, environment, qualified, stop
from veos_runtime.providers import StreamResult, clean_env, commands, execute
from veos_runtime.server import Application, make_server
from veos_runtime.store import Store


class Inference(BaseHTTPRequestHandler):
  def log_message(self, *_):
    pass

  def do_GET(self):
    self.reply(
      {
        "object": "list",
        "data": [{"id": "veos-fixture", "object": "model", "context_length": 65536}],
      }
    )

  def do_POST(self):
    body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
    if self.path != "/v1/chat/completions":
      self.send_error(404)
      return
    self.server.requests.append(body)
    names = [t["function"]["name"] for t in body.get("tools", [])]
    assert names and all(n.startswith("mcp__veos__") for n in names), names
    if self.server.cancel_next:
      self.server.in_flight.set()
      self.server.release.wait(15)
      return
    returned = [m for m in body.get("messages", []) if m.get("role") == "tool"]
    direct = any(n.endswith("_start_control_task") for n in names)
    if direct and len(returned) < 3:
      steps = [
        (
          "start_control_task",
          {
            "task": "Inspect this project's context",
            "request_key": "hermes-direct-smoke",
          },
        ),
        ("project_context", {}),
        (
          "finish_control_task",
          {
            "result": "Read the local project",
            "outcome": "succeeded",
            "receipt_version": 0,
          },
        ),
      ]
      name, args = steps[len(returned)]
      message = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
          {
            "id": "direct-" + name,
            "type": "function",
            "function": {
              "name": next(n for n in names if n.endswith("_" + name)),
              "arguments": json.dumps(args),
            },
          }
        ],
      }
      finish = "tool_calls"
    elif returned:
      assert "Hermes qualification" in json.dumps(returned)
      message = {
        "role": "assistant",
        "content": "Verified the local project through VeOS.",
      }
      finish = "stop"
    else:
      tool = next(n for n in names if n.endswith("_project_context"))
      message = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
          {
            "id": "fixture-call",
            "type": "function",
            "function": {"name": tool, "arguments": "{}"},
          }
        ],
      }
      finish = "tool_calls"
    answer = {
      "id": "fixture",
      "object": "chat.completion",
      "model": "veos-fixture",
      "choices": [{"index": 0, "message": message, "finish_reason": finish}],
      "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
    }
    if body.get("stream"):
      self.send_response(200)
      self.send_header("Content-Type", "text/event-stream")
      self.end_headers()
      if "tool_calls" in message:
        message["tool_calls"][0]["index"] = 0
      chunk = {
        **answer,
        "object": "chat.completion.chunk",
        "choices": [{"index": 0, "delta": message, "finish_reason": finish}],
      }
      self.wfile.write(("data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n").encode())
    else:
      self.reply(answer)

  def reply(self, body):
    raw = json.dumps(body).encode()
    self.send_response(200)
    self.send_header("Content-Type", "application/json")
    self.send_header("Content-Length", str(len(raw)))
    self.end_headers()
    self.wfile.write(raw)


def main():
  executable = str(Path(sys.argv[1]).resolve())
  with tempfile.TemporaryDirectory(prefix="veos-hermes-") as temp:
    store = Store(Path(temp) / "state")
    pid = store.create_project("Hermes qualification", "Read a test project")
    tid = store.create_task(pid, "Read project_context", "hermes", "native-smoke")
    lease = store.claim(tid)
    app = Application(store)
    server = make_server(app, 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    token = secrets.token_urlsafe(32)
    app.capabilities["tokens"][token] = {
      "task_id": tid,
      "project_id": pid,
      "lease": lease,
    }
    inference = ThreadingHTTPServer(("127.0.0.1", 0), Inference)
    inference.requests = []
    inference.cancel_next = False
    inference.in_flight = threading.Event()
    inference.release = threading.Event()
    threading.Thread(target=inference.serve_forever, daemon=True).start()
    env = environment(store.root, clean_env())
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
      assert qualified(executable, env)
      mcp = {
        "command": sys.executable,
        "args": [
          str(Path(__file__).resolve().parents[1] / "src/veos_runtime/mcp.py"),
          app.base_url,
        ],
        "env": {"VEOS_TASK_TOKEN": token},
      }
      home = configure(store.root, "veos-fixture", mcp, timeout=90)
      config = json.loads((home / "config.yaml").read_text())
      config["model"] = {
        "default": "veos-fixture",
        "provider": "custom",
        "base_url": f"http://127.0.0.1:{inference.server_port}/v1",
      }
      (home / "config.yaml").write_text(json.dumps(config))
      workdir = store.root / "projects" / pid
      workdir.mkdir(parents=True)
      session = Path(temp) / "run"
      session.mkdir()
      argv = commands("hermes", executable, workdir, session, "veos-fixture")
      argv[argv.index("--provider") + 1] = "custom"
      argv += ["--base-url", config["model"]["base_url"], "--api-key", "local-fixture"]
      feed = StreamResult.feed

      def record(parser, line):
        event = json.loads(line)
        if event.get("type") == "result" and event.get("error"):
          print(json.dumps({"fixture_error": event["error"]}), flush=True)
        feed(parser, line)

      with patch.object(StreamResult, "feed", record):
        begin(env)
        outcome = execute(
          "hermes",
          argv,
          "Use project_context to inspect the project.",
          workdir,
          120,
          lambda: True,
          task_token=token,
          provider_env=env,
        )
      assert outcome.state == "succeeded", outcome
      assert outcome.result == "Verified the local project through VeOS.", outcome
      assert len(inference.requests) >= 2
      assert stop(executable, env), "Owned gateway stop not confirmed"
      # Reuse the real native harness against the public direct-control interface.
      # Its private MCP process obtains a scoped connection, not the admin token.
      (store.root / "connection.json").write_text(
        json.dumps({"url": app.base_url, "token": app.token})
      )
      config["mcp_servers"]["veos"] = {
        "command": sys.executable,
        "args": [
          "-m",
          "veos_runtime",
          "--state-dir",
          str(store.root),
          "control-mcp",
          "--project-id",
          pid,
        ],
        "env": {"PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
      }
      (home / "config.yaml").write_text(json.dumps(config))
      begin(env)
      direct = execute(
        "hermes",
        argv,
        "Start a control task, read project_context, then finish the task.",
        workdir,
        120,
        lambda: True,
        provider_env=env,
      )
      assert direct.state == "succeeded", direct
      tasks = [
        t for t in store.snapshot(pid)["tasks"] if t["execution_owner"] == "external"
      ]
      assert len(tasks) == 1 and tasks[0]["state"] == "succeeded", tasks
      assert stop(executable, env), "Direct-control gateway stop not confirmed"
      inference.cancel_next = True
      started = time.monotonic()
      begin(env)
      outcome = execute(
        "hermes",
        argv,
        "Use project_context to inspect the project.",
        workdir,
        90,
        lambda: not inference.in_flight.is_set(),
        task_token=token,
        provider_env=env,
      )
      assert inference.in_flight.is_set(), outcome
      assert outcome.state == "unknown" and outcome.reason == "stopped_or_paused", (
        outcome
      )
      assert time.monotonic() - started < 90
      assert stop(executable, env), "Cancelled gateway is still running"
      inference.release.set()
      print(
        json.dumps(
          {
            "result": "passed",
            "checks": [
              "pinned real Hermes CLI",
              "real gateway and MCP discovery",
              "only VeOS tools exposed",
              "tool executes against local project",
              "structured final result",
              "external Hermes starts, reads and finishes through direct MCP controls",
              "owned gateway stop",
              "cancellation while native model request is in flight",
              "gateway absence independently verified after cancellation",
            ],
          }
        )
      )
    finally:
      inference.release.set()
      stop(executable, env)
      server.shutdown()
      server.server_close()
      inference.shutdown()
      inference.server_close()
      app.browser.close()
      app.control_sessions.close()


if __name__ == "__main__":
  main()
