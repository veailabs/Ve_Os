import io
import json
import os
import subprocess
import sys
import time
import unittest
import urllib.error
from unittest.mock import patch

from test_runtime import Fixture, HTTPFixture

from veos_runtime.harness import Harness
from veos_runtime.store import Refused
from veos_runtime.voice import Voice


class HarnessTests(Fixture):
  def setUp(self):
    super().setUp()
    self.harness = Harness(self.store)
    self.connection = self.harness.connect(self.pid, "codex")
    self.binding = self.harness.authorize(self.connection["token"])

  def test_bound_project_provider_deduplication_and_cancellation(self):
    args = {"task": "Read my local document", "request_key": "voice-1"}
    first = self.harness.call(self.binding, "run_local_agent", args)
    second = self.harness.call(self.binding, "run_local_agent", args)
    self.assertEqual(first["task_id"], second["task_id"])
    self.assertEqual(first["state"], "queued")
    self.assertFalse(first["external_action_confirmed"])
    with self.assertRaises(Refused):
      self.harness.call(self.binding, "run_local_agent", {**args, "task": "Different"})
    for bad in [{**args, "provider": "claude"}, {**args, "project_id": "other"}]:
      with self.assertRaises(Refused):
        self.harness.call(self.binding, "run_local_agent", bad)
    other_provider = self.harness.authorize(
      self.harness.connect(self.pid, "claude")["token"]
    )
    with self.assertRaises(Refused):
      self.harness.call(
        other_provider, "local_agent_status", {"task_id": first["task_id"]}
      )
    cancelled = self.harness.call(
      self.binding, "cancel_local_agent", {"task_id": first["task_id"]}
    )
    self.assertEqual(cancelled["state"], "cancelled")
    self.assertEqual(
      self.harness.call(
        self.binding, "cancel_local_agent", {"task_id": first["task_id"]}
      )["state"],
      "cancelled",
    )

  def test_existing_tasks_private_and_expiry_revocation_pause(self):
    tid = self.task()
    with self.assertRaises(Refused):
      self.harness.call(self.binding, "local_agent_status", {"task_id": tid})
    self.store.set_project_state(self.pid, "paused")
    with self.assertRaises(Refused):
      self.harness.call(
        self.binding, "run_local_agent", {"task": "work", "request_key": "one"}
      )
    self.now += 3601
    with self.assertRaises(PermissionError):
      self.harness.authorize(self.connection["token"])
    self.store.set_project_state(self.pid, "active")
    connection = self.harness.connect(self.pid, "codex")
    self.harness.disconnect(connection["token"])
    with self.assertRaises(PermissionError):
      self.harness.authorize(connection["token"])


class HarnessHTTPTests(HTTPFixture):
  def test_delegate_cannot_access_administration_or_agent_tools(self):
    with self.request(
      "/api/harness/connect", {"pid": self.pid, "provider": "codex"}
    ) as response:
      connection = json.load(response)
    token = connection["token"]
    for path, data in [
      ("/api/state", None),
      ("/api/tools", {"name": "project_context"}),
      (
        "/api/desktop/grant",
        {"pid": self.pid, "operation": "clipboard_read", "allow": True},
      ),
    ]:
      with self.subTest(path=path), self.assertRaises(urllib.error.HTTPError):
        self.request(path, data, token)
    with self.assertRaises(urllib.error.HTTPError):
      self.request(
        "/api/harness/tools",
        {"name": "local_agent_status", "arguments": {"task_id": "fake"}},
      )
    with self.request(
      "/api/harness/tools",
      {
        "name": "run_local_agent",
        "arguments": {"task": "A local task", "request_key": "one"},
      },
      token,
    ) as response:
      self.assertEqual(json.load(response)["state"], "queued")
    with self.request("/api/harness/disconnect", {}, token):
      pass
    with self.assertRaises(urllib.error.HTTPError):
      self.request("/api/harness/tools", {}, token)

  def test_real_stdio_mcp_uses_same_journal(self):
    (self.store.root / "connection.json").write_text(
      json.dumps({"url": self.app.base_url, "token": self.app.token})
    )
    requests = [
      {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": "2025-03-26"},
      },
      {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
      {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {
          "name": "run_local_agent",
          "arguments": {"task": "Inspect local project", "request_key": "stdio"},
        },
      },
    ]
    result = subprocess.run(
      [
        sys.executable,
        "-m",
        "veos_runtime",
        "--state-dir",
        str(self.store.root),
        "incoming-mcp",
        "--project-id",
        self.pid,
        "--provider",
        "codex",
      ],
      input="\n".join(json.dumps(r) for r in requests) + "\n",
      text=True,
      capture_output=True,
      timeout=15,
      check=True,
    )
    replies = [json.loads(line) for line in result.stdout.splitlines()]
    self.assertEqual([r["id"] for r in replies], [1, 2, 3])
    task = replies[2]["result"]["structuredContent"]
    self.assertEqual(
      self.store.task(task["task_id"])["instruction"], "Inspect local project"
    )
    self.assertEqual(self.app.harness.connections, {})
    self.assertNotIn(self.app.token, result.stdout + result.stderr)

  def test_voice_failure_revokes_delegation(self):
    with patch.object(self.app.voice, "mint", side_effect=Refused("Not configured")):
      with self.assertRaises(urllib.error.HTTPError):
        self.request("/api/voice/start", {"pid": self.pid, "provider": "codex"})
    self.assertEqual(self.app.harness.connections, {})


class VoiceTests(unittest.TestCase):
  def setUp(self):
    self.env = patch.dict(
      os.environ,
      {
        "VEOS_VOICE_API_ENABLED": "true",
        "VEOS_VOICE_SESSION_URL": "https://backend.ve.ai/2.0/dev/core/voice/sessions",
        "VEOS_VE_ACCESS_TOKEN": "fixture-person-session",
      },
    )
    self.env.start()
    self.addCleanup(self.env.stop)
    self.voice = Voice()
    self.data = {
      "version": "ve.voice.session.v1",
      "provider": "openai",
      "transport": "webrtc",
      "executionTarget": "local_harness",
      "connectUrl": "https://api.openai.com/v1/realtime/calls",
      "model": "fixture",
      "clientSecret": {
        "value": "ek_0123456789fixture",
        "expiresAt": int(time.time()) + 60,
      },
    }

  def response(self):
    response = io.BytesIO(json.dumps(self.data).encode())
    response.status = 200
    return response

  def test_opt_in_no_fallback_and_correct_mint_contract(self):
    with (
      patch.dict(os.environ, {"VEOS_VOICE_API_ENABLED": "false"}),
      patch("urllib.request.build_opener") as opener,
    ):
      with self.assertRaises(Refused):
        self.voice.mint()
      opener.assert_not_called()
    with patch("urllib.request.build_opener") as opener:
      opener.return_value.open.return_value = self.response()
      result = self.voice.mint()
      request = opener.return_value.open.call_args.args[0]
      self.assertEqual(
        json.loads(request.data),
        {"version": "ve.voice.session.request.v1", "executionTarget": "local_harness"},
      )
      self.assertEqual(
        request.headers["Authorization"], "Bearer fixture-person-session"
      )
      self.assertEqual(result["clientSecret"], self.data["clientSecret"])
      self.assertNotIn("fixture-person-session", json.dumps(result))

  def test_malformed_expired_or_redirected_contract_never_retried(self):
    for change in [
      {"connectUrl": "https://other.example/calls"},
      {
        "clientSecret": {
          "value": "ek_0123456789fixture",
          "expiresAt": int(time.time()) - 1,
        }
      },
      {"executionTarget": "cloud_agent"},
    ]:
      voice = Voice()
      with patch("urllib.request.build_opener") as opener:
        old = self.data.copy()
        self.data.update(change)
        opener.return_value.open.return_value = self.response()
        with self.assertRaises(Refused) as error:
          voice.mint()
        self.assertNotIn("fixture", str(error.exception))
        self.assertEqual(opener.return_value.open.call_count, 1)
        self.data = old
    with patch("urllib.request.build_opener") as opener:
      opener.return_value.open.side_effect = OSError("fixture-person-session")
      for _ in range(4):
        with self.assertRaises(Refused):
          self.voice.mint()
      self.assertEqual(opener.return_value.open.call_count, 3)
