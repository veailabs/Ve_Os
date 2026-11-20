import concurrent.futures
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from veos_runtime import mcp
from veos_runtime.integrations import IntegrationActions, VeMCP, result_state
from veos_runtime.providers import (
  Outcome,
  StreamResult,
  clean_env,
  commands,
  default_config,
  execute,
  probe,
  read_config,
  save_config,
)
from veos_runtime.server import Application, make_server
from veos_runtime.store import Refused, Store
from veos_runtime.worker import Worker


class Fixture(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    self.now = 1000.0
    self.store = Store(self.temp.name, clock=lambda: self.now)
    self.config = default_config()
    save_config(self.store.root, self.config)
    self.pid = self.store.create_project(
      "Research", "Find documented evidence", max_runs=3
    )

  def task(self, key="first", **kwargs):
    return self.store.create_task(
      self.pid, "Compare the supplied sources", "codex", key, **kwargs
    )


class StoreTests(Fixture):
  def test_idempotency_rejects_different_work(self):
    tid = self.task()
    self.assertEqual(tid, self.task())
    with self.assertRaises(Refused):
      self.store.create_task(self.pid, "Different", "codex", "first")
    self.assertEqual(len(self.store.snapshot()["tasks"]), 1)

  def test_dependencies_and_scheduled_start(self):
    parent = self.task()
    child = self.task("child", dependencies=[parent], not_before=1100)
    self.assertIsNone(self.store.claim(child))
    self.store.finish(parent, self.store.claim(parent), "succeeded", "Done")
    self.assertIsNone(self.store.claim(child))
    self.now = 1101
    self.assertIsNotNone(self.store.claim(child))

  def test_cross_project_dependencies_and_evidence_rejected(self):
    other = self.store.create_project("Private", "Different project")
    tid = self.store.create_task(other, "Work", "claude", "a")
    eid = self.store.add_evidence(other, "https://example.org", "Private source")
    with self.assertRaises(Refused):
      self.task(dependencies=[tid])
    with self.assertRaises(Refused):
      self.store.add_claim(self.pid, "Claim", "claim", [eid])
    with self.assertRaises(Refused):
      self.store.add_evidence(self.pid, "https://example.com", "Source", tid)
    self.assertNotIn(tid, [t["id"] for t in self.store.snapshot(self.pid)["tasks"]])

  def test_atomic_claim_and_project_budget(self):
    tids = [self.task(str(i)) for i in range(12)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
      claims = list(pool.map(self.store.claim, tids))
    self.assertEqual(sum(bool(x) for x in claims), 3)
    self.assertEqual(self.store.snapshot(self.pid)["projects"][0]["runs_used"], 3)
    self.assertEqual(self.store.ready(), [])

  def test_same_task_claimed_only_once(self):
    tid = self.task()
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
      leases = list(pool.map(lambda _: self.store.claim(tid), range(6)))
    self.assertEqual(sum(bool(x) for x in leases), 1)

  def test_cancel_fences_old_completion(self):
    tid = self.task()
    lease = self.store.claim(tid)
    self.store.task_command(tid, "cancel")
    self.assertFalse(self.store.permitted(tid, lease))
    self.assertFalse(self.store.finish(tid, lease, "succeeded", "Late result"))
    self.assertEqual(self.store.task(tid)["state"], "cancelled")

  def test_pause_does_not_accept_late_success(self):
    tid = self.task()
    lease = self.store.claim(tid)
    self.store.set_project_state(self.pid, "paused")
    self.store.finish(tid, lease, "succeeded", "Late result")
    self.assertEqual(self.store.task(tid)["state"], "unknown")
    self.assertEqual(self.store.ready(), [])

  def test_crash_keeps_checkpoint_and_requires_explicit_retry(self):
    tid = self.task()
    lease = self.store.claim(tid)
    with self.store.db() as db:
      db.execute("UPDATE tasks SET result='Partial evidence' WHERE id=?", (tid,))
    recovered = Store(self.temp.name, clock=lambda: self.now)
    self.assertEqual(recovered.recover(), 1)
    self.assertEqual(recovered.task(tid)["result"], "Partial evidence")
    self.assertFalse(recovered.finish(tid, lease, "succeeded", "stale"))
    self.assertEqual(recovered.ready(), [])
    recovered.task_command(tid, "retry")
    new = recovered.claim(tid)
    self.assertNotEqual(new, lease)
    recovered.finish(tid, new, "failed")
    with self.assertRaises(Refused):
      recovered.task_command(tid, "retry")

  def test_deadline_and_nonfinite_times(self):
    pid = self.store.create_project("Deadline", "Finish soon", deadline=1001)
    tid = self.store.create_task(pid, "Work", "codex", "x")
    self.now = 1002
    self.assertIsNone(self.store.claim(tid))
    for value in [float("nan"), float("inf"), True]:
      with self.assertRaises(Refused):
        self.task(str(value), not_before=value)

  def test_child_proposal_requires_acceptance(self):
    tid = self.task()
    with self.assertRaises(Refused):
      self.task("child", parent_id=tid)
    child = self.task("child", parent_id=tid, proposed=True)
    self.assertIsNone(self.store.claim(child))
    self.store.task_command(child, "accept")
    self.assertIsNotNone(self.store.claim(child))

  def test_contradiction_and_source_withdrawal(self):
    one = self.store.add_evidence(self.pid, "https://example.org/a", "Supports A")
    two = self.store.add_evidence(self.pid, "https://example.org/b", "Refutes A")
    self.assertEqual(
      one,
      self.store.add_evidence(self.pid, "https://example.org/a", "Supports A"),
    )
    self.store.add_claim(self.pid, "A might work", "hypothesis", [one], [two])
    self.assertEqual(self.store.snapshot(self.pid)["claims"][0]["state"], "contested")
    self.store.withdraw_evidence(self.pid, one)
    self.assertEqual(
      self.store.snapshot(self.pid)["claims"][0]["state"], "needs_review"
    )
    with self.assertRaises(Refused):
      self.store.add_claim(self.pid, "Invalid", "claim", [one])

  def test_closed_project_cannot_restart(self):
    tid = self.task()
    self.store.set_project_state(self.pid, "closed")
    self.assertEqual(self.store.task(tid)["state"], "cancelled")
    with self.assertRaises(Refused):
      self.store.task_command(tid, "retry")
    with self.assertRaises(Refused):
      self.store.set_project_state(self.pid, "active")

  def test_model_context_bounded_without_truncating_stored_evidence(self):
    for i in range(25):
      self.store.add_evidence(self.pid, f"https://example.org/{i}", "e" * 40_000)
    context = self.store.context(self.pid)
    self.assertLess(len(json.dumps(context, separators=(",", ":"))), 121_000)
    self.assertTrue(context["truncated"])
    self.assertTrue(context["evidence"][0]["excerpt_truncated"])
    self.assertEqual(len(self.store.snapshot()["evidence"][0]["excerpt"]), 40_000)


class ProviderTests(Fixture):
  def test_adding_hermes_preserves_existing_configuration_and_keeps_it_disabled(self):
    self.config["providers"].pop("hermes")
    self.config["providers"]["codex"]["enabled"] = True
    save_config(self.store.root, self.config)
    upgraded = read_config(self.store.root)
    self.assertTrue(upgraded["providers"]["codex"]["enabled"])
    self.assertFalse(upgraded["providers"]["hermes"]["enabled"])
    save_config(self.store.root, upgraded)
    self.assertNotIn(
      "_state_dir", json.loads((self.store.root / "config.json").read_text())
    )

  def test_hermes_completion_requires_terminal_success(self):
    parser = StreamResult("hermes")
    parser.feed('{"type":"text","text":"Partial answer"}')
    self.assertEqual(parser.outcome(0).state, "unknown")
    parser.feed('{"type":"result","exit_code":0,"text":"Verified result"}')
    self.assertEqual(parser.outcome(0).result, "Verified result")
    self.assertEqual(parser.outcome(0).state, "succeeded")
    parser.feed('{"type":"result","exit_code":1,"error":"usage limit reached"}')
    self.assertEqual(parser.outcome(1).state, "waiting_subscription")
    malformed = StreamResult("hermes")
    malformed.feed('{"type":"result","exit_code":false,"text":"Not evidence"}')
    self.assertNotEqual(malformed.outcome(0).state, "succeeded")

  def test_hermes_profile_refuses_foreign_state_and_ambient_credentials(self):
    from veos_runtime.hermes import configure, environment, profile

    foreign = self.store.root / "foreign"
    (foreign / "hermes").mkdir(parents=True)
    with self.assertRaises(Refused):
      profile(foreign)
    home = configure(
      self.store.root, "subscription-model", {"command": "/python", "args": []}
    )
    config = json.loads((home / "config.yaml").read_text())
    self.assertEqual(config["fallback_model"], [])
    self.assertEqual(config["platform_toolsets"]["cli"], ["veos"])
    self.assertEqual(config["model"]["provider"], "openai-codex")
    with patch.dict(
      os.environ, {"OPENAI_API_KEY": "not-for-hermes", "HERMES_HOME": "/other/profile"}
    ):
      env = environment(self.store.root, clean_env())
    self.assertNotIn("OPENAI_API_KEY", env)
    self.assertEqual(env["HERMES_HOME"], str(home))
    (home / ".env").write_text("OPENAI_API_KEY=unapproved")
    with self.assertRaises(Refused):
      environment(self.store.root, clean_env())

  def test_hermes_probe_requires_qualified_cli_and_native_subscription(self):
    from veos_runtime.hermes import REVISION, check, stop

    config = read_config(self.store.root)
    config["providers"]["hermes"]["model"] = "subscription-model"
    calls = []

    def run(argv, **kwargs):
      calls.append(argv)
      output = REVISION if argv[-1] == "--version" else "openai-codex: logged in\n"
      if argv[-2:] == ["gateway", "status"]:
        output = "✗ Gateway is not running\n"
      return subprocess.CompletedProcess(argv, 0, output, "")

    self.assertTrue(check("/hermes", config, clean_env(), run)["ready"])
    self.assertEqual(calls[-1], ["/hermes", "auth", "status", "openai-codex"])

    def bad(argv, **kw):
      return subprocess.CompletedProcess(argv, 0, "unqualified", "")

    self.assertFalse(check("/hermes", config, clean_env(), bad)["ready"])
    env = {**clean_env(), "HERMES_HOME": str(self.store.root / "hermes")}

    def failed(argv, **kw):
      return subprocess.CompletedProcess(argv, 1, "", "")

    self.assertFalse(stop("/hermes", env, failed))
    self.assertEqual(
      check("/hermes", config, clean_env(), run)["reason"], "hermes_stop_unconfirmed"
    )
    self.assertTrue(stop("/hermes", env, run))
    self.assertEqual(calls[-2], ["/hermes", "gateway", "stop", "--yes"])
    self.assertEqual(calls[-1], ["/hermes", "gateway", "status"])
    self.assertFalse(stop("/hermes", env, bad))

  def test_hermes_run_marker_survives_abrupt_process_exit_and_blocks_admission(self):
    from veos_runtime.hermes import check, environment

    env = environment(self.store.root, clean_env())
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    crashed = subprocess.run(
      [
        sys.executable,
        "-c",
        "import os; from veos_runtime.hermes import begin; begin(os.environ); os._exit(9)",
      ],
      env=env,
      capture_output=True,
      timeout=5,
    )
    self.assertEqual(crashed.returncode, 9)
    config = read_config(self.store.root)
    config["providers"]["hermes"]["model"] = "subscription-model"
    with patch(
      "subprocess.run", side_effect=AssertionError("No native process may start")
    ) as run:
      self.assertEqual(
        check("/hermes", config, clean_env(), run)["reason"], "hermes_stop_unconfirmed"
      )
      run.assert_not_called()

  def test_config_and_environment_prevent_api_fallback(self):
    with patch.dict(
      os.environ,
      {
        "OPENAI_API_KEY": "secret",
        "ANTHROPIC_API_KEY": "secret",
        "AWS_PROFILE": "production",
        "VEOS_VE_ACCESS_TOKEN": "secret",
        "SSH_AUTH_SOCK": "secret",
      },
    ):
      env = clean_env()
      for key in [
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "AWS_PROFILE",
        "VEOS_VE_ACCESS_TOKEN",
        "SSH_AUTH_SOCK",
      ]:
        self.assertNotIn(key, env)
    self.config["paid_api_fallback"] = True
    save_config(self.store.root, self.config)
    with self.assertRaises(Refused):
      read_config(self.store.root)

  @patch("veos_runtime.providers.shutil.which", return_value="/native/client")
  def test_native_auth_probe_distinguishes_subscription_and_api(self, _):
    for name in ["codex", "claude"]:
      self.config["providers"][name] = {
        "enabled": True,
        "extra_usage_disabled_confirmed": True,
      }

    def run_with(stdout="", stderr="", code=0):
      return lambda *a, **kw: subprocess.CompletedProcess(a, code, stdout, stderr)

    self.assertTrue(
      probe("codex", self.config, run_with(stderr="Logged in using ChatGPT"))["ready"]
    )
    self.assertFalse(
      probe("codex", self.config, run_with(stderr="Logged in using an API key"))[
        "ready"
      ]
    )
    claude = {
      "loggedIn": True,
      "authMethod": "claude.ai",
      "subscriptionType": "max",
    }
    self.assertTrue(probe("claude", self.config, run_with(json.dumps(claude)))["ready"])
    claude["apiKeySource"] = "environment"
    self.assertFalse(
      probe("claude", self.config, run_with(json.dumps(claude)))["ready"]
    )

  def test_structured_completion_and_quota(self):
    parser = StreamResult("codex")
    parser.feed(
      '{"type":"item.completed","item":{"type":"agent_message","text":"Done"}}'
    )
    self.assertEqual(parser.outcome(0).state, "unknown")
    parser.feed('{"type":"turn.completed"}')
    self.assertEqual(parser.outcome(0).state, "succeeded")
    parser.feed('{"type":"turn.failed","error":{"message":"usage limit reached"}}')
    self.assertEqual(parser.outcome(1).state, "waiting_subscription")
    claude = StreamResult("claude")
    claude.feed(
      '{"type":"result","subtype":"success","is_error":false,"result":"Evidence recorded"}'
    )
    self.assertEqual(claude.outcome(0).state, "succeeded")

  def test_process_drains_output_while_writing_large_input(self):
    script = "import sys,json; print('x'*80000,flush=True); sys.stdin.read(); print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'Complete'}})); print(json.dumps({'type':'turn.completed'}))"
    result = execute(
      "codex",
      [sys.executable, "-c", script],
      "p" * 250000,
      self.temp.name,
      4,
      lambda: True,
    )
    self.assertEqual(result.state, "succeeded")
    self.assertEqual(result.result, "Complete")

  def test_timeout_and_cancel_stop_real_process(self):
    script = "import time; time.sleep(30)"
    start = time.monotonic()
    result = execute(
      "codex",
      [sys.executable, "-c", script],
      "x" * 250000,
      self.temp.name,
      0.3,
      lambda: True,
    )
    self.assertEqual(result.reason, "execution_timeout")
    self.assertLess(time.monotonic() - start, 4)
    result = execute(
      "codex",
      [sys.executable, "-c", script],
      "x",
      self.temp.name,
      30,
      lambda: False,
    )
    self.assertEqual(result.reason, "stopped_or_paused")

  def test_command_does_not_expose_task_token_in_arguments(self):
    session = self.store.root
    (session / "mcp.json").write_text(
      json.dumps(
        {
          "mcpServers": {
            "veos": {
              "command": sys.executable,
              "args": ["mcp.py", "http://127.0.0.1:8764"],
              "env": {"VEOS_TASK_TOKEN": "never-in-argv"},
            }
          }
        }
      )
    )
    argv = commands("codex", "/codex", session, session)
    self.assertNotIn("never-in-argv", " ".join(argv))
    self.assertIn("--ignore-user-config", argv)
    self.assertIn('forced_login_method="chatgpt"', argv)


class FakeIntegration:
  def __init__(self):
    self.calls = []
    self.fail = False
    schema = {
      "type": "object",
      "properties": {"connectedAppId": {"type": "string"}},
    }
    self.tools = {
      "read_mail": {
        "name": "read_mail",
        "inputSchema": schema,
        "annotations": {"readOnlyHint": True},
      },
      "send_mail": {"name": "send_mail", "inputSchema": schema},
    }

  def catalogue(self):
    return self.tools

  def call(self, name, args):
    self.calls.append((name, args))
    if self.fail:
      raise TimeoutError("Uncertain remote outcome")
    return {
      "content": [
        {
          "type": "text",
          "text": json.dumps({"success": True, "data": {"id": "receipt-123"}}),
        }
      ],
      "isError": False,
    }


class IntegrationFixture(Fixture):
  def setUp(self):
    super().setUp()
    self.config["ve"] = {
      "endpoint": "https://backend.ve.ai/test/mcp/gmail",
      "connection_id": "11111111-1111-4111-8111-111111111111",
      "read_tools": ["read_mail"],
      "write_tools": ["send_mail"],
    }
    save_config(self.store.root, self.config)
    self.client = FakeIntegration()
    self.token = "fixture-identity"
    self.actions = IntegrationActions(
      self.store, lambda *a: self.client, token=lambda: self.token
    )
    self.tid = self.task()
    self.store.claim(self.tid)

  def proposal(self, args=None):
    return self.actions.call(
      self.pid,
      self.tid,
      "send_mail",
      args or {"to": "recipient@example.com", "body": "Test fixture only"},
    )["action_id"]


class IntegrationTests(IntegrationFixture):
  def test_write_requires_exact_approval_and_never_replays(self):
    aid = self.proposal()
    self.assertEqual(self.client.calls, [])
    self.assertEqual(aid, self.proposal())
    self.assertEqual(self.actions.decide(aid, True)["state"], "succeeded")
    self.assertEqual(len(self.client.calls), 1)
    self.assertEqual(aid, self.proposal())
    with self.assertRaises(Refused):
      self.actions.decide(aid, True)
    self.assertEqual(len(self.client.calls), 1)

  def test_concurrent_approval_executes_once(self):
    aid = self.proposal()

    def decide():
      try:
        return self.actions.decide(aid, True)
      except Refused:
        return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
      results = list(pool.map(lambda _: decide(), range(4)))
    self.assertEqual(sum(bool(x) for x in results), 1)
    self.assertEqual(len(self.client.calls), 1)

  def test_cancel_invalidates_approval(self):
    aid = self.proposal()
    self.store.task_command(self.tid, "cancel")
    with self.assertRaises(Refused):
      self.actions.decide(aid, True)
    self.assertEqual(self.client.calls, [])
    self.assertEqual(self.store.snapshot()["actions"][0]["state"], "cancelled")

  def test_approval_is_bound_to_identity_and_tool_contract(self):
    aid = self.proposal()
    self.token = "changed-user-or-token"
    with self.assertRaises(Refused):
      self.actions.decide(aid, True)
    self.token = "fixture-identity"
    self.client.tools["send_mail"]["description"] = "Changed meaning"
    with self.assertRaises(Refused):
      self.actions.decide(aid, True)
    self.assertEqual(self.client.calls, [])

  def test_expiry_and_deny_do_not_execute(self):
    aid = self.proposal()
    self.now += 601
    self.assertEqual(self.actions.decide(aid, True)["state"], "expired")
    with self.assertRaises(Refused):
      self.proposal({"to": "someone@example.com", "body": "Do not evade expiry"})
    self.tid = self.task("new-request")
    self.store.claim(self.tid)
    other = self.proposal(
      {"to": "someone@example.com", "body": "Another exact payload"}
    )
    self.assertEqual(self.actions.decide(other, False)["state"], "denied")
    self.assertEqual(self.client.calls, [])

  def test_unknown_external_outcome_is_not_retried(self):
    aid = self.proposal()
    self.client.fail = True
    self.assertEqual(self.actions.decide(aid, True)["state"], "unknown")
    self.assertEqual(aid, self.proposal())
    with self.assertRaises(Refused):
      self.actions.decide(aid, True)
    self.assertEqual(len(self.client.calls), 1)

  def test_read_hint_and_mutable_draft_contract(self):
    self.client.tools["read_mail"]["annotations"]["readOnlyHint"] = False
    with self.assertRaises(Refused):
      self.actions.call(self.pid, self.tid, "read_mail", {})
    aid = self.proposal({"draft_id": "mutable-id"})
    with self.assertRaises(Refused):
      self.actions.decide(aid, True)
    self.assertEqual(self.client.calls, [])

  def test_malformed_mcp_cannot_claim_success(self):
    config = self.config["ve"]
    with self.assertRaises(Refused):
      VeMCP(config, "fixture", lambda _: {}).call("send_mail", {})

    def rpc(body):
      return {"jsonrpc": "2.0", "id": body["id"], "result": {}}

    with self.assertRaises(Refused):
      VeMCP(config, "fixture", rpc).call("send_mail", {})
    with self.assertRaises(Refused):
      VeMCP({"endpoint": "https://attacker.example"}, "fixture")

  def test_ve_provider_failure_envelope_never_reports_success(self):
    for body, expected in [
      ({"success": False, "code": 403}, "failed"),
      ({"success": False, "code": 503}, "unknown"),
      ({"success": False, "code": "INTERNAL_ERROR"}, "unknown"),
      ({"success": True, "data": {"id": "sent"}}, "succeeded"),
    ]:
      self.assertEqual(
        result_state({"content": [{"type": "text", "text": json.dumps(body)}]}),
        expected,
      )

  def test_mailbox_cannot_be_changed_or_omitted_by_schema(self):
    self.proposal()
    args = json.loads(self.store.snapshot()["actions"][0]["args"])
    self.assertEqual(args["connectedAppId"], self.config["ve"]["connection_id"])
    with self.assertRaises(Refused):
      self.proposal({"connectedAppId": "22222222-2222-4222-8222-222222222222"})
    self.client.tools["send_mail"]["inputSchema"] = {
      "type": "object",
      "properties": {},
    }
    with self.assertRaises(Refused):
      self.proposal()

  def test_web_grant_accepts_600_seconds_but_not_expired_or_api_keys(self):
    client = VeMCP(self.config["ve"], "user-jwt")
    self.assertEqual(client.mint_url, "https://backend.ve.ai/test/mcp/token")
    token = "11111111-1111-4111-8111-111111111111." + "a" * 43
    with patch("veos_runtime.integrations.time.time", return_value=1000):
      client.accept_grant({"token": token, "expiresAt": 1600})
      self.assertNotEqual(client.token, client.access_token)
      for grant in [
        {"token": token, "expiresAt": 999},
        {"token": token, "expiresAt": 1800},
        {"token": "user-jwt", "expiresAt": 1600},
      ]:
        with self.assertRaises(Refused):
          client.accept_grant(grant)

  def test_person_jwt_only_goes_to_mint_route_not_mcp_route(self):
    observed = []
    grant = "11111111-1111-4111-8111-111111111111." + "a" * 43

    class Response:
      status = 200

      def __init__(self, data):
        self.data = json.dumps(data).encode()
        self.headers = {"Content-Type": "application/json"}

      def __enter__(self):
        return self

      def __exit__(self, *a):
        pass

      def read(self, n):
        return self.data[:n]

    class Opener:
      def open(self, request, timeout):
        observed.append((request.full_url, request.headers["Authorization"]))
        if request.full_url.endswith("/token"):
          return Response({"token": grant, "expiresAt": int(time.time()) + 600})
        data = json.loads(request.data)
        return Response({"jsonrpc": "2.0", "id": data["id"], "result": {"tools": []}})

    client = VeMCP(self.config["ve"], "person-jwt-fixture")
    with patch(
      "veos_runtime.integrations.urllib.request.build_opener",
      return_value=Opener(),
    ):
      client.request("tools/list", {})
    self.assertEqual(
      observed,
      [
        (client.mint_url, "Bearer person-jwt-fixture"),
        (client.endpoint, "Bearer " + grant),
      ],
    )


class HTTPFixture(Fixture):
  def setUp(self):
    super().setUp()
    self.app = Application(self.store, "test-admin-token")
    self.server = make_server(self.app, 0)
    self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
    self.thread.start()
    self.addCleanup(self.stop_server)

  def stop_server(self):
    self.server.shutdown()
    self.app.control_sessions.close()
    self.server.server_close()
    self.thread.join(timeout=2)

  def request(self, path, data=None, token="test-admin-token", extra=None):
    headers = {
      "Authorization": "Bearer " + token,
      "Content-Type": "application/json",
    }
    headers.update(extra or {})
    req = urllib.request.Request(
      self.app.base_url + path,
      None if data is None else json.dumps(data).encode(),
      headers,
    )
    return urllib.request.urlopen(req, timeout=3)

  def capability(self):
    tid = self.task()
    cap = {"task_id": tid, "project_id": self.pid, "lease": self.store.claim(tid)}
    self.app.capabilities["tokens"]["task-token"] = cap
    return cap


class HTTPTests(HTTPFixture):
  def test_auth_origin_host_and_static_csp(self):
    for kwargs in [
      {"token": "bad"},
      {"extra": {"Origin": "https://evil.example"}},
      {"extra": {"Host": "evil.example"}},
    ]:
      with self.assertRaises(urllib.error.HTTPError) as error:
        self.request("/api/state", **kwargs)
      self.assertEqual(error.exception.code, 401)
    with self.request("/") as response:
      self.assertIn(
        "frame-ancestors 'none'", response.headers["Content-Security-Policy"]
      )
      self.assertIn(b"Mission Desk", response.read())

  def test_task_capability_is_project_scoped_and_not_admin(self):
    cap = self.capability()
    other = self.store.create_project("Secret", "PRIVATE_MARKER")
    with self.request(
      "/api/tools", {"name": "project_context", "arguments": {}}, "task-token"
    ) as response:
      self.assertNotIn("PRIVATE_MARKER", response.read().decode())
    with self.assertRaises(urllib.error.HTTPError):
      self.request("/api/state", token="task-token")
    with self.assertRaises(urllib.error.HTTPError):
      self.request(
        "/api/tools",
        {
          "name": "record_evidence",
          "arguments": {
            "pid": other,
            "uri": "https://example.org",
            "excerpt": "data",
          },
        },
        "task-token",
      )
    self.store.task_command(cap["task_id"], "cancel")
    with self.assertRaises(urllib.error.HTTPError):
      self.request(
        "/api/tools", {"name": "project_context", "arguments": {}}, "task-token"
      )

  def test_real_stdio_mcp_to_runtime_records_evidence(self):
    self.capability()
    requests = [
      {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
      {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
          "name": "record_evidence",
          "arguments": {
            "uri": "https://example.org/study",
            "excerpt": "Fixture evidence",
          },
        },
      },
    ]
    result = subprocess.run(
      [sys.executable, str(Path(mcp.__file__)), self.app.base_url],
      input="\n".join(json.dumps(r) for r in requests) + "\n",
      text=True,
      capture_output=True,
      check=False,
      env={**os.environ, "VEOS_TASK_TOKEN": "task-token"},
      timeout=5,
    )
    self.assertEqual(result.returncode, 0)
    messages = [json.loads(x) for x in result.stdout.splitlines()]
    self.assertNotIn("isError", messages[-1]["result"])
    self.assertEqual(len(self.store.snapshot(self.pid)["evidence"]), 1)


class WorkerTests(Fixture):
  @patch("veos_runtime.worker.shutil.which", return_value="/native/hermes")
  def test_hermes_worker_requires_confirmed_gateway_stop(self, _):
    config = read_config(self.store.root)
    config["providers"]["hermes"]["model"] = "subscription-model"
    save_config(self.store.root, config)
    tid = self.store.create_task(self.pid, "Inspect the project", "hermes", "native")
    app = Application(self.store)

    def runner(
      provider, argv, prompt, cwd, timeout, permitted, checkpoint, token, provider_env
    ):
      self.assertEqual(provider, "hermes")
      self.assertEqual(provider_env["HERMES_HOME"], str(self.store.root / "hermes"))
      self.assertTrue((self.store.root / "hermes/stop-unconfirmed").is_file())
      self.assertNotIn("OPENAI_API_KEY", provider_env)
      self.assertEqual(app.authorize(token)["task_id"], tid)
      native = json.loads((self.store.root / "hermes/config.yaml").read_text())
      self.assertEqual(native["platform_toolsets"]["cli"], ["veos"])
      return Outcome("succeeded", "Read the project")

    worker = Worker(
      self.store,
      app.capabilities,
      "http://127.0.0.1:1",
      prober=lambda *a: {"ready": True},
      runner=runner,
    )
    with patch("veos_runtime.hermes.stop", return_value=False) as stopped:
      self.assertTrue(worker.once())
    stopped.assert_called_once()
    self.assertEqual(self.store.task(tid)["state"], "unknown")
    self.assertFalse(app.capabilities["tokens"])

  def test_only_one_worker_can_recover_and_own_a_workspace(self):
    app = Application(self.store)
    first = Worker(self.store, app.capabilities, "http://127.0.0.1:1")
    second = Worker(self.store, app.capabilities, "http://127.0.0.1:1")
    first.start()
    started = time.monotonic()
    try:
      with self.assertRaises(Refused):
        second.start()
      with self.assertRaises(Refused):
        second.start(run_worker=False)
    finally:
      first.close()
    self.assertFalse(first.thread.is_alive())
    self.assertLess(time.monotonic() - started, 2)
    second.start(run_worker=False)
    try:
      self.assertIsNone(second.thread)
      with self.assertRaises(Refused):
        first.start()
    finally:
      second.close()

  def test_unavailable_provider_is_blocked_without_budget_use(self):
    tid = self.task()
    app = Application(self.store)
    worker = Worker(
      self.store,
      app.capabilities,
      "http://127.0.0.1:1",
      prober=lambda *a: {"ready": False, "reason": "sign_in_required"},
    )
    self.assertTrue(worker.once())
    self.assertEqual(self.store.task(tid)["state"], "blocked")
    self.assertEqual(self.store.task(tid)["attempts"], 0)
    self.assertEqual(self.store.snapshot()["projects"][0]["runs_used"], 0)

  @patch("veos_runtime.worker.shutil.which", return_value="/native/codex")
  def test_worker_checkpoint_quota_no_fallback_and_capability_revocation(self, _):
    tid = self.task()
    app = Application(self.store)
    calls = []

    def runner(provider, argv, prompt, cwd, timeout, permitted, checkpoint, token):
      calls.append(provider)
      self.assertTrue(permitted())
      self.assertEqual(app.authorize(token)["task_id"], tid)
      checkpoint("Partial research")
      return Outcome("waiting_subscription", "Partial research", "subscription_limit")

    worker = Worker(
      self.store,
      app.capabilities,
      "http://127.0.0.1:1",
      prober=lambda *a: {"ready": True},
      runner=runner,
    )
    worker.once()
    self.assertFalse(worker.once())
    self.assertEqual(calls, ["codex"])
    self.assertEqual(app.capabilities["tokens"], {})
    self.assertEqual(self.store.task(tid)["state"], "waiting_subscription")
    self.assertEqual(self.store.task(tid)["result"], "Partial research")
    self.assertEqual(list(self.store.root.glob("run-*")), [])


if __name__ == "__main__":
  unittest.main()
