import io
import json
import os
import signal
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


class ControlHTTPTests(HTTPFixture):
  def setUp(self):
    super().setUp()
    with self.request("/api/control/connect", {"pid": self.pid}) as response:
      self.control_token = json.load(response)["token"]

  def call(self, name, args=None, token=None):
    with self.request(
      "/api/control/tools",
      {"name": name, "arguments": args or {}},
      token or self.control_token,
    ) as response:
      return json.load(response)

  def start(self, key="one", token=None):
    return self.call(
      "start_control_task", {"task": "Inspect local sources", "request_key": key}, token
    )

  def pending_action(self):
    self.app.desktop_actions.grant(self.pid, "clipboard_write", True)
    return self.call(
      "desktop",
      {
        "operation": "clipboard_write",
        "args": {"text": "fixture"},
        "request_key": "clipboard",
      },
    )

  def test_project_scope_and_administration_are_enforced(self):
    with self.assertRaises(urllib.error.HTTPError):
      self.call("project_context")
    for path, data in [
      ("/api/state", None),
      ("/api/tools", {"name": "project_context"}),
      ("/api/control/connect", {"pid": self.pid}),
      (
        "/api/desktop/grant",
        {"pid": self.pid, "operation": "clipboard_write", "allow": True},
      ),
      (
        "/api/harness/tools",
        {"name": "run_local_agent", "arguments": {"task": "x", "request_key": "x"}},
      ),
    ]:
      with self.subTest(path=path), self.assertRaises(urllib.error.HTTPError):
        self.request(path, data, self.control_token)
    with self.assertRaises(urllib.error.HTTPError):
      self.request(
        "/api/control/tools",
        {"name": "start_control_task", "arguments": {"task": "x", "request_key": "x"}},
      )
    self.start()
    other = self.store.create_project("Private", "Other project")
    eid = self.store.add_evidence(other, "https://example.org/private", "private data")
    with self.assertRaises(urllib.error.HTTPError):
      self.call("get_evidence", {"id": eid})
    with self.assertRaises(urllib.error.HTTPError):
      self.call("project_context", {"project_id": other})
    with self.assertRaises(urllib.error.HTTPError):
      self.call("desktop", {"operation": "clipboard_read"})
    result = self.call("project_context")
    self.assertNotIn("private data", json.dumps(result))

  def test_external_ownership_idempotency_budget_and_local_artifacts(self):
    first = self.start()
    tid = first["task_id"]
    self.assertEqual(first, self.start())
    self.assertEqual(self.store.task(tid)["attempts"], 1)
    self.assertIsNone(self.store.claim(tid))
    self.assertNotIn(tid, [t["id"] for t in self.store.ready()])
    native = self.task("native")
    self.assertIsNone(self.store.claim(native, execution_owner="external"))
    with self.request("/api/control/connect", {"pid": self.pid}) as response:
      other = json.load(response)["token"]
    with self.assertRaises(urllib.error.HTTPError):
      self.start(token=other)
    with self.assertRaises(urllib.error.HTTPError):
      self.start("second")
    with self.assertRaises(urllib.error.HTTPError):
      self.call(
        "start_control_task", {"task": "Different instruction", "request_key": "one"}
      )
    report = self.call(
      "write_report", {"name": "local.md", "text": "Verified local report"}
    )
    self.assertIn(
      "Verified local report", json.dumps(self.call("read_file", {"id": report["id"]}))
    )
    result = self.call(
      "finish_control_task",
      {"result": "Saved report", "outcome": "succeeded", "receipt_version": 0},
    )
    self.assertEqual(result["state"], "succeeded")
    self.assertEqual(self.start()["state"], "succeeded")
    with self.assertRaises(urllib.error.HTTPError):
      self.call("write_report", {"name": "late.md", "text": "too late"})
    self.assertEqual(self.store.snapshot(self.pid)["projects"][0]["runs_used"], 1)

  def test_review_receipts_and_stale_completion(self):
    tid = self.start()["task_id"]
    action = self.pending_action()
    finish = {"result": "Done", "outcome": "succeeded", "receipt_version": 0}
    with self.assertRaises(urllib.error.HTTPError):
      self.call("finish_control_task", finish)
    # Exercise the real approval/receipt lifecycle without touching this Mac's clipboard.
    with patch.object(
      self.app.desktop_actions.controls,
      "invoke",
      return_value={"input_completed": True, "verified": False},
    ) as invoke:
      self.app.desktop_actions.decide(action["action_id"], True)
      invoke.assert_called_once()
    with self.assertRaises(urllib.error.HTTPError):
      self.call("finish_control_task", finish)
    status = self.call("control_task_status")
    self.assertEqual(
      status["actions"][0]["result"], {"input_completed": True, "verified": False}
    )
    self.assertEqual(self.store.task(tid)["state"], "running")
    self.assertNotIn(tid, [t["id"] for t in self.store.ready()])
    self.assertEqual(
      self.call(
        "finish_control_task", {**finish, "receipt_version": status["receipt_version"]}
      )["state"],
      "succeeded",
    )

  def test_disconnect_revokes_session_cancels_reviews_and_fences_retries(self):
    tid = self.start()["task_id"]
    self.pending_action()
    with self.request("/api/control/disconnect", {}, self.control_token):
      pass
    self.assertEqual(self.store.task(tid)["state"], "unknown")
    self.assertEqual(self.store.task_actions(tid)[0]["state"], "cancelled")
    with self.assertRaises(urllib.error.HTTPError):
      self.call("project_context")
    with self.request("/api/control/connect", {"pid": self.pid}) as response:
      token = json.load(response)["token"]
    with self.assertRaises(urllib.error.HTTPError):
      self.start(token=token)
    self.store.task_command(tid, "retry")
    self.assertEqual(self.store.ready(), [])
    self.assertIsNone(self.store.claim(tid))
    self.assertEqual(self.start(token=token)["task_id"], tid)

  def test_denied_and_unknown_actions_never_become_success(self):
    for outcome in ("denied", "unknown"):
      with self.subTest(outcome=outcome):
        self.start(outcome)
        action = self.pending_action()
        if outcome == "denied":
          self.app.desktop_actions.decide(action["action_id"], False)
        else:
          with patch.object(
            self.app.desktop_actions.controls, "invoke", side_effect=TimeoutError
          ):
            self.app.desktop_actions.decide(action["action_id"], True)
        status = self.call("control_task_status")
        finished = self.call(
          "finish_control_task",
          {
            "result": "A claimed success",
            "outcome": "succeeded",
            "receipt_version": status["receipt_version"],
          },
        )
        self.assertEqual(
          finished["state"], "blocked" if outcome == "denied" else "unknown"
        )
        with self.assertRaises(urllib.error.HTTPError):
          self.start(outcome)

  def test_pause_expiry_and_cancel_revoke_execution(self):
    tid = self.start()["task_id"]
    self.store.set_project_state(self.pid, "paused")
    with self.assertRaises(urllib.error.HTTPError):
      self.call("record_evidence", {"uri": "https://example.org", "excerpt": "late"})
    self.assertEqual(self.call("control_task_status")["task_id"], tid)
    self.store.set_project_state(self.pid, "active")
    self.pending_action()
    self.now += 3601
    with self.assertRaises(urllib.error.HTTPError):
      self.call("project_context")
    self.assertEqual(self.store.task(tid)["state"], "unknown")
    self.assertEqual(self.store.task_actions(tid)[0]["state"], "cancelled")
    with self.request("/api/control/connect", {"pid": self.pid}) as response:
      self.control_token = json.load(response)["token"]
    next_task = self.start("cancel")
    self.pending_action()
    self.assertEqual(self.call("cancel_control_task")["state"], "cancelled")
    self.assertEqual(
      self.store.task_actions(next_task["task_id"])[0]["state"], "cancelled"
    )
    with self.assertRaises(urllib.error.HTTPError):
      self.call("available_tools")

  def test_recovery_prevents_stale_session_from_cancelling_new_owner(self):
    tid = self.start()["task_id"]
    old_lease = self.store.task(tid)["lease"]
    self.pending_action()
    self.store.recover()
    self.assertEqual(self.store.task_actions(tid)[0]["state"], "cancelled")
    self.store.task_command(tid, "retry")
    with self.request("/api/control/connect", {"pid": self.pid}) as response:
      new_token = json.load(response)["token"]
    self.start(token=new_token)
    with self.assertRaises(Refused):
      self.store.task_command(tid, "cancel", expected_lease=old_lease)
    with self.assertRaises(urllib.error.HTTPError):
      self.call("cancel_control_task")
    with self.assertRaises(urllib.error.HTTPError):
      self.call(
        "finish_control_task",
        {"result": "late", "outcome": "succeeded", "receipt_version": 0},
      )
    self.assertEqual(self.store.task(tid)["state"], "running")

  def test_stdio_eof_and_signal_release_unfinished_task(self):
    (self.store.root / "connection.json").write_text(
      json.dumps({"url": self.app.base_url, "token": self.app.token})
    )
    for end in ("eof", "signal"):
      with self.subTest(end=end):
        command = [
          sys.executable,
          "-m",
          "veos_runtime",
          "--state-dir",
          str(self.store.root),
          "control-mcp",
          "--project-id",
          self.pid,
        ]
        process = subprocess.Popen(
          command,
          stdin=subprocess.PIPE,
          stdout=subprocess.PIPE,
          stderr=subprocess.PIPE,
          text=True,
        )
        request = {
          "jsonrpc": "2.0",
          "id": 1,
          "method": "tools/call",
          "params": {
            "name": "start_control_task",
            "arguments": {"task": "Read local files", "request_key": end},
          },
        }
        try:
          process.stdin.write(json.dumps(request) + "\n")
          process.stdin.flush()
          # Bound a blocked protocol read if the child fails to answer.
          import select

          self.assertTrue(select.select([process.stdout], [], [], 5)[0])
          reply = json.loads(process.stdout.readline())
          tid = reply["result"]["structuredContent"]["task_id"]
          if end == "signal":
            process.send_signal(signal.SIGTERM)
          out, err = process.communicate(timeout=5)
          self.assertEqual(process.returncode, 0, err)
          self.assertEqual(self.store.task(tid)["state"], "unknown")
          self.assertNotIn(self.app.token, out + err)
        finally:
          if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)


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
