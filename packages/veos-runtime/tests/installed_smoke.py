"""Run against an installed wheel, outside the source import path."""

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.request
from pathlib import Path

import veos_runtime


def main():
  repo = Path(__file__).resolve().parents[1]
  assert not Path(veos_runtime.__file__).resolve().is_relative_to(repo)
  with tempfile.TemporaryDirectory(prefix="veos-installed-test-") as directory:
    root = Path(directory)
    process = subprocess.Popen(
      [
        sys.executable,
        "-m",
        "veos_runtime",
        "--state-dir",
        directory,
        "serve",
        "--port",
        "0",
      ],
      stdout=subprocess.PIPE,
      stderr=subprocess.PIPE,
      text=True,
      start_new_session=True,
    )
    try:
      deadline = time.monotonic() + 10
      connection = root / "connection.json"
      while not connection.exists() and time.monotonic() < deadline:
        if process.poll() is not None:
          raise AssertionError("Installed service exited before becoming ready")
        time.sleep(0.05)
      info = json.loads(connection.read_text())
      assert connection.stat().st_mode & 0o777 == 0o600

      def request(path, payload=None):
        req = urllib.request.Request(
          info["url"] + path,
          None if payload is None else json.dumps(payload).encode(),
          {
            "Authorization": "Bearer " + info["token"],
            "Content-Type": "application/json",
          },
        )
        with urllib.request.urlopen(req, timeout=3) as response:
          return response.read()

      assert b"Mission Desk" in request("/")
      assert b"use strict" in request("/app.js")
      assert b"createVeOSVoice" in request("/voice.js")
      assert b".sidebar" in request("/style.css")
      pid = json.loads(
        request(
          "/api/projects",
          {
            "name": "Installed test",
            "objective": "Verify packaging without model execution",
          },
        )
      )["id"]
      request(
        "/api/tasks",
        {
          "pid": pid,
          "instruction": "This must stay blocked while the provider is disabled",
          "provider": "codex",
          "request_key": "wheel-smoke",
        },
      )
      while time.monotonic() < deadline:
        state = json.loads(request("/api/state"))
        if state["tasks"][0]["state"] == "blocked":
          break
        time.sleep(0.05)
      assert state["tasks"][0]["state"] == "blocked"
      assert state["tasks"][0]["attempts"] == 0
      assert state["projects"][0]["runs_used"] == 0
      # Every generated client configuration must launch the installed server;
      # resource loading catches missing packaged skill files.
      for client in ("codex", "claude", "gemini", "hermes"):
        generated = subprocess.run(
          [
            sys.executable,
            "-m",
            "veos_runtime",
            "--state-dir",
            directory,
            "mcp-config",
            "--client",
            client,
            "--project-id",
            pid,
          ],
          text=True,
          capture_output=True,
          check=True,
          timeout=5,
        ).stdout
        settings = (
          tomllib.loads(generated) if client == "codex" else json.loads(generated)
        )
        config = settings[
          "mcp_servers" if client in {"codex", "hermes"} else "mcpServers"
        ]["veos"]
        requests = [
          {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
          {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "resources/read",
            "params": {"uri": "veos://skills/application-control"},
          },
        ]
        if client == "gemini":
          requests += [
            {
              "jsonrpc": "2.0",
              "id": 3,
              "method": "tools/call",
              "params": {
                "name": "start_control_task",
                "arguments": {
                  "task": "Save a local report",
                  "request_key": "installed-direct",
                },
              },
            },
            {
              "jsonrpc": "2.0",
              "id": 4,
              "method": "tools/call",
              "params": {
                "name": "write_report",
                "arguments": {
                  "name": "smoke.md",
                  "text": "Local MCP execution verified",
                },
              },
            },
            {
              "jsonrpc": "2.0",
              "id": 5,
              "method": "tools/call",
              "params": {
                "name": "finish_control_task",
                "arguments": {
                  "result": "Saved local report",
                  "outcome": "succeeded",
                  "receipt_version": 0,
                },
              },
            },
          ]
        run = subprocess.run(
          [config["command"], *config["args"]],
          input="\n".join(json.dumps(r) for r in requests) + "\n",
          text=True,
          capture_output=True,
          check=True,
          timeout=10,
        )
        replies = [json.loads(line) for line in run.stdout.splitlines()]
        assert len(replies) == len(requests)
        assert all(not r["result"].get("isError") for r in replies)
        assert replies[0]["result"]["instructions"]
        assert replies[1]["result"]["contents"][0]["text"].startswith(
          "---\nname: veos-app-control"
        )
        assert info["token"] not in generated + run.stdout + run.stderr
        if client == "gemini":
          assert replies[-1]["result"]["structuredContent"]["state"] == "succeeded"
      state = json.loads(request("/api/state"))
      external = next(t for t in state["tasks"] if t["execution_owner"] == "external")
      assert external["state"] == "succeeded" and external["attempts"] == 1
    finally:
      process.send_signal(signal.SIGTERM)
      try:
        out, err = process.communicate(timeout=10)
      except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=3)
        raise AssertionError("Service did not stop cleanly") from None
    assert process.returncode == 0, err
    assert info["token"] not in out + err
    assert not connection.exists()
    status = subprocess.run(
      [sys.executable, "-m", "veos_runtime", "--state-dir", directory, "status"],
      capture_output=True,
      text=True,
      check=True,
      timeout=3,
    )
    assert json.loads(status.stdout)["tasks"] == {"blocked": 1, "succeeded": 1}
    print(
      json.dumps(
        {
          "result": "passed",
          "checks": [
            "installed wheel assets",
            "private session file",
            "disabled provider consumes no runs",
            "clean shutdown",
            "persistent state after process exit",
            "four client configurations launch the installed MCP server",
            "packaged application-control skill resource",
            "direct MCP task writes a local report without a native model run",
          ],
        }
      )
    )


if __name__ == "__main__":
  main()
