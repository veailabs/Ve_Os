import base64
import hmac
import json
import secrets
import shutil
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .artifacts import Artifacts
from .browser import BrowserActions, LocalBrowser
from .control_session import ControlSessions
from .controls import SETTINGS
from .desktop_actions import DesktopActions
from .goals import GoalPlans
from .harness import Harness
from .integrations import IntegrationActions
from .mcp import TOOLS
from .memory import Memory
from .providers import PROVIDERS, probe, read_config
from .store import Refused, encoded
from .voice import Voice

WEB = Path(__file__).with_name("web")


class Application:
  def __init__(self, store, token=None):
    self.store = store
    self.token = token or secrets.token_urlsafe(32)
    self.capabilities = {"lock": threading.Lock(), "tokens": {}}
    self.integrations = IntegrationActions(store)
    self.browser = LocalBrowser(store)
    self.browser_actions = BrowserActions(store, self.browser)
    self.desktop_actions = DesktopActions(store)
    self.harness = Harness(store)
    self.voice = Voice()
    self.artifacts = Artifacts(store)
    self.memory = Memory(store)
    self.goals = GoalPlans(store)
    self.control_sessions = ControlSessions(store, self.call_tool)
    self.base_url = ""

  def authorize(self, bearer, task_only=False):
    if not task_only and hmac.compare_digest(bearer, self.token):
      return {"admin": True}
    with self.capabilities["lock"]:
      capability = self.capabilities["tokens"].get(bearer)
      if capability:
        capability = dict(capability)
    if capability and self.store.permitted(capability["task_id"], capability["lease"]):
      return capability
    raise PermissionError("Session is missing or no longer active")

  def call_tool(self, capability, name, args):
    descriptor = next((t for t in TOOLS if t["name"] == name), None)
    if descriptor is None or not isinstance(args, dict):
      raise Refused("Unknown tool or invalid arguments")
    schema = descriptor["inputSchema"]
    if set(args) - set(schema["properties"]) or set(schema["required"]) - set(args):
      raise Refused("Tool arguments do not match its contract")
    for key, value in args.items():
      spec = schema["properties"][key]
      expected = {"string": str, "array": list, "object": dict, "integer": int}[
        spec["type"]
      ]
      if type(value) is not expected or ("enum" in spec and value not in spec["enum"]):
        raise Refused("Invalid tool argument type")
      if (
        isinstance(value, list)
        and spec.get("items", {}).get("type") == "string"
        and any(not isinstance(x, str) for x in value)
      ):
        raise Refused("Evidence IDs must be strings")
    pid, tid = capability["project_id"], capability["task_id"]
    with self.store.db() as db:
      db.execute("BEGIN IMMEDIATE")
      count = db.execute(
        "SELECT count(*) FROM events WHERE kind='tool.called' AND payload=?",
        (encoded({"task": tid, "run": capability["lease"]}),),
      ).fetchone()[0]
      if count >= 60:
        raise Refused("Task tool-call budget reached")
      self.store.event(
        db, pid, "tool.called", {"task": tid, "run": capability["lease"]}
      )
    if name == "search_evidence":
      return self.store.search_evidence(pid, **args)
    if name == "get_evidence":
      return self.store.get_evidence(
        pid, args["id"], args.get("offset", 0), args.get("limit", 12000)
      )
    if name == "project_context":
      return {
        "untrusted": True,
        "project": self.store.context(pid),
        "memory": self.memory.context(pid),
      }
    author = {"task_id": tid, "run_id": capability["lease"], "agent": True}
    if name == "recall_memory":
      return self.memory.recall(
        pid, args.get("query"), args.get("scope", "all"), args.get("limit", 20)
      )
    if name == "remember":
      return self.memory.remember(
        args["scope"],
        args["kind"],
        args["statement"],
        pid,
        args.get("detail"),
        args.get("evidence"),
        **author,
      )
    if name == "propose_goal_plan":
      return self.goals.propose(pid, **args, **author)
    if name == "goal_progress":
      return self.goals.progress(pid, args["goal_id"], args.get("offset", 0))
    if name == "save_record":
      return self.memory.save_record(
        pid,
        args["type"],
        args["title"],
        args.get("detail"),
        args.get("state"),
        args.get("revises"),
        **author,
      )
    if name == "revise_claim":
      return self.memory.revise_claim(
        pid,
        args["id"],
        args["reason"],
        args.get("confidence"),
        args.get("supporting"),
        args.get("contradicting"),
        **author,
      )
    if name == "resolve_entity":
      return self.memory.resolve_entity(
        pid,
        args["kind"],
        args["name"],
        args.get("aliases"),
        args.get("identifiers"),
        args.get("detail"),
        **author,
      )
    if name == "link_records":
      return self.memory.link(
        pid,
        args["subject"],
        args["relation"],
        args["object"],
        args.get("confidence"),
        **author,
      )
    if name == "related_records":
      return {"untrusted": True} | self.memory.neighbors(
        pid, args["record"], args.get("limit", 50)
      )
    if name == "record_signal":
      return self.memory.record_signal(
        pid,
        args["source"],
        args["content"],
        args.get("uri"),
        args.get("actor"),
        args.get("rights", "public"),
        args.get("published"),
        args.get("entities"),
        **author,
      )
    if name == "browser":
      return self.browser_actions.call(
        pid, tid, args["operation"], args.get("args", {})
      )
    if name == "desktop":
      return self.desktop_actions.call(
        pid, tid, args["operation"], args.get("args", {}), args.get("request_key")
      )
    if name == "available_tools":
      result = {
        "browser": self.browser.status(pid),
        "desktop": self.desktop_actions.status(pid),
        "settings_areas": [*SETTINGS, "displays", "startup", "notifications"],
        "ve": [],
      }
      config = read_config(self.store.root)["ve"]
      if config["endpoint"]:
        try:
          _, _, _, catalogue = self.integrations.connect()
          result["ve"] = [
            {
              **descriptor,
              "requires_approval": name in config["write_tools"],
            }
            for name, descriptor in catalogue.items()
            if name in config["read_tools"] + config["write_tools"]
          ]
        except Exception:  # noqa: BLE001 — Do not expose upstream credentials through discovery errors.
          result["ve_error"] = (
            "Ve connection unavailable; reconnect using the configured user identity."
          )
      return result
    if name == "list_files":
      return {"files": self.artifacts.list(pid)}
    if name == "read_file":
      return self.artifacts.read_text(
        pid, args["id"], args.get("offset", 0), args.get("limit", 12000)
      )
    if name == "write_report":
      return self.artifacts.save(
        pid,
        args["name"],
        args["text"].encode("utf-8"),
        "text/markdown",
        tid,
        capability["lease"],
      )
    if name == "record_evidence":
      return {
        "id": self.store.add_evidence(
          pid,
          args["uri"],
          args["excerpt"],
          tid,
          capability["lease"],
          args.get("expires"),
          args.get("access", "public"),
          args.get("confidence"),
        )
      }
    if name == "record_claim":
      return {
        "id": self.store.add_claim(
          pid,
          args["statement"],
          args["kind"],
          args["supporting"],
          args.get("contradicting"),
          tid,
          capability["lease"],
          args.get("confidence"),
          args.get("detail"),
        )
      }
    if name == "propose_task":
      return {
        "id": self.store.create_task(
          pid,
          args["instruction"],
          args["provider"],
          args["request_key"],
          parent_id=tid,
          proposed=True,
        ),
        "state": "proposed",
      }
    return self.integrations.call(pid, tid, args["tool"], args["args"])


def make_server(app, port=8764):
  class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
      pass

    def send(self, code, data, content_type="application/json; charset=utf-8"):
      raw = data if isinstance(data, bytes) else encoded(data).encode()
      self.send_response(code)
      self.send_header("Content-Type", content_type)
      self.send_header("Content-Length", str(len(raw)))
      self.send_header("Cache-Control", "no-store")
      self.send_header("X-Content-Type-Options", "nosniff")
      self.send_header("Referrer-Policy", "no-referrer")
      self.send_header(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self' https://api.openai.com; media-src 'self' blob:; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
      )
      self.end_headers()
      try:
        self.wfile.write(raw)
      except (BrokenPipeError, ConnectionResetError):
        pass

    def guard(self, task_only=False, harness_only=False, control_only=False):
      if self.headers.get("Host") != urlsplit(app.base_url).netloc:
        raise PermissionError("Invalid host")
      if self.headers.get("Origin") not in {None, app.base_url}:
        raise PermissionError("Cross-origin access refused")
      auth = self.headers.get("Authorization", "")
      if not auth.startswith("Bearer "):
        raise PermissionError("Sign in from the VeOS launcher")
      if harness_only:
        return app.harness.authorize(auth[7:])
      if control_only:
        return app.control_sessions.authorize(auth[7:])
      cap = app.authorize(auth[7:], task_only)
      if not task_only and not cap.get("admin"):
        raise PermissionError("Only the user may manage the runtime")
      return cap

    def body(self):
      if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
        raise Refused("Expected JSON")
      length = int(self.headers.get("Content-Length", "0"))
      maximum = 7_000_000 if urlsplit(self.path).path == "/api/files" else 100_000
      if not 0 < length <= maximum:
        raise Refused("Invalid request size")
      data = json.loads(self.rfile.read(length))
      if not isinstance(data, dict):
        raise Refused("Expected an object")
      return data

    def do_GET(self):
      try:
        path = urlsplit(self.path)
        if path.path in {"/", "/app.js", "/voice.js", "/style.css"}:
          if self.headers.get("Host") != urlsplit(app.base_url).netloc:
            raise PermissionError("Invalid host")
          filename = "index.html" if path.path == "/" else path.path[1:]
          mime = {
            "index.html": "text/html",
            "app.js": "text/javascript",
            "voice.js": "text/javascript",
            "style.css": "text/css",
          }[filename]
          self.send(200, (WEB / filename).read_bytes(), mime + "; charset=utf-8")
          return
        self.guard()
        query = parse_qs(path.query)
        if path.path in {"/api/state", "/api/export"}:
          snapshot = app.store.snapshot(query.get("project", [None])[0])
          if path.path == "/api/state":
            # The page counts signals; only an export carries their text.
            snapshot["signals"] = [
              {"id": s["id"], "project_id": s["project_id"]}
              for s in snapshot["signals"]
            ]
          self.send(200, snapshot)
        elif path.path == "/api/files":
          self.send(
            200,
            {"files": app.artifacts.list(query.get("project", [None])[0])},
          )
        elif path.path == "/api/files/content":
          metadata, content = app.artifacts.read(
            query.get("project", [None])[0], query.get("id", [None])[0]
          )
          self.send(
            200,
            {
              "file": metadata,
              "base64": base64.b64encode(content).decode(),
            },
          )
        elif path.path == "/api/browser":
          self.send(200, app.browser.status(query.get("project", [None])[0]))
        elif path.path == "/api/desktop":
          self.send(
            200,
            {
              "capabilities": app.desktop_actions.status(
                query.get("project", [None])[0]
              ),
              "settings_areas": [*SETTINGS, "displays", "startup", "notifications"],
            },
          )
        elif path.path == "/api/memory/brief":
          self.send(200, app.memory.brief(query.get("project", [None])[0]))
        elif path.path == "/api/memory/history":
          self.send(200, {"versions": app.memory.history(query.get("id", [None])[0])})
        elif path.path == "/api/memory/performance":
          self.send(200, app.memory.performance(query.get("project", [None])[0]))
        elif path.path == "/api/goals/progress":
          self.send(
            200,
            app.goals.progress(
              query.get("project", [None])[0],
              query.get("id", [None])[0],
              int(query.get("offset", ["0"])[0]),
            ),
          )
        elif path.path == "/api/records":
          self.send(
            200,
            app.memory.record(
              query.get("project", [None])[0], query.get("id", [None])[0]
            ),
          )
        elif path.path == "/api/inbox":
          self.send(200, app.memory.inbox())
        elif path.path == "/api/entities":
          self.send(
            200,
            app.memory.entity(
              query.get("project", [None])[0], query.get("id", [None])[0]
            ),
          )
        elif path.path == "/api/voice/status":
          self.send(200, app.voice.status())
        elif path.path == "/api/providers":
          config = read_config(app.store.root)
          self.send(
            200,
            {
              "billing": config["billing"],
              "providers": {
                p: {
                  **config["providers"][p],
                  "installed": shutil.which(exe) is not None,
                }
                for p, exe in PROVIDERS.items()
              },
            },
          )
        elif path.path == "/api/events":
          after = max(0, int(query.get("after", ["0"])[0]))
          with app.store.changed:
            events = app.store.events(after)
            if not events:
              app.store.changed.wait(timeout=20)
              events = app.store.events(after)
          self.send(200, {"events": events})
        else:
          self.send(404, {"error": "Not found"})
      except Exception as exc:  # noqa: BLE001 — HTTP boundary maps errors to sanitized responses.
        self.failure(exc)

    def do_POST(self):
      try:
        path = urlsplit(self.path).path
        cap = self.guard(
          task_only=path == "/api/tools",
          harness_only=path in {"/api/harness/tools", "/api/harness/disconnect"},
          control_only=path in {"/api/control/tools", "/api/control/disconnect"},
        )
        data = self.body()
        if path == "/api/tools":
          result = app.call_tool(cap, data.get("name"), data.get("arguments", {}))
        elif path == "/api/control/connect":
          if set(data) != {"pid"}:
            raise Refused("Choose one project for the control connection")
          result = app.control_sessions.connect(data["pid"])
        elif path == "/api/control/tools":
          result = app.control_sessions.call(
            cap, data.get("name"), data.get("arguments", {})
          )
        elif path == "/api/control/disconnect":
          app.control_sessions.disconnect(cap)
          result = {"disconnected": True, "pending_actions_cancelled": True}
        elif path == "/api/harness/connect":
          result = app.harness.connect(data["pid"], data["provider"])
        elif path == "/api/harness/tools":
          result = app.harness.call(cap, data.get("name"), data.get("arguments", {}))
        elif path == "/api/harness/disconnect":
          app.harness.disconnect(self.headers["Authorization"][7:])
          result = {"disconnected": True, "tasks_cancelled": False}
        elif path == "/api/voice/start":
          connection = app.harness.connect(data["pid"], data["provider"])
          try:
            result = {**app.voice.mint(), "harness": connection}
            with app.store.db() as db:
              if app.store.require_project(db, data["pid"])["state"] != "active":
                raise Refused("Project is no longer active")
          except Exception:
            app.harness.disconnect(connection["token"])
            raise
        elif path == "/api/projects":
          result = {"id": app.store.create_project(**data)}
        elif path == "/api/tasks":
          if data.get("parent_id") or data.get("proposed"):
            raise Refused("Use the agent task proposal tool")
          result = {"id": app.store.create_task(**data)}
        elif path == "/api/evidence":
          result = {"id": app.store.add_evidence(**data)}
        elif path == "/api/files":
          result = app.artifacts.save(
            data["pid"],
            data["name"],
            base64.b64decode(data["base64"], validate=True),
            data.get("media_type", "application/octet-stream"),
          )
        elif path == "/api/claims":
          result = {"id": app.store.add_claim(**data)}
        elif path == "/api/memory":
          result = app.memory.remember(
            data["scope"],
            data["kind"],
            data["statement"],
            data.get("pid"),
            data.get("detail"),
            data.get("evidence"),
          )
        elif path == "/api/memory/revise":
          result = app.memory.revise(data["id"], data["statement"], data.get("detail"))
        elif path == "/api/memory/erase":
          result = app.memory.erase(data["id"])
        elif path.startswith("/api/memory/"):
          parts = path.split("/")
          if len(parts) != 5:
            raise Refused("Invalid memory route")
          result = app.memory.decide(parts[3], parts[4])
        elif path == "/api/claims/revise":
          result = app.memory.revise_claim(
            data["pid"],
            data["id"],
            data["reason"],
            data.get("confidence"),
            data.get("supporting"),
            data.get("contradicting"),
          )
        elif path == "/api/claims/retract":
          result = app.memory.retract_claim(data["pid"], data["id"], data["reason"])
        elif path == "/api/entities/merge":
          result = app.memory.merge_entities(data["pid"], data["id"], data["into"])
        elif path == "/api/entities/distinct":
          result = app.memory.distinct_entity(data["pid"], data["id"])
        elif path == "/api/goals/plans":
          result = app.goals.propose(
            data["pid"],
            data["goal_id"],
            data["request_key"],
            data["steps"],
            data.get("signal_id"),
          )
        elif path == "/api/goals/plans/decide":
          result = app.goals.decide(data["pid"], data["id"], data["command"])
        elif path == "/api/records":
          result = app.memory.save_record(
            data["pid"],
            data["type"],
            data["title"],
            data.get("detail"),
            data.get("state"),
            data.get("revises"),
          )
        elif path == "/api/records/decide":
          result = app.memory.decide_record(data["pid"], data["id"], data["command"])
        elif path == "/api/signals":
          result = app.memory.ingest_signal(
            data["source"],
            data["content"],
            data["entities"],
            data.get("uri"),
            data.get("actor"),
            data.get("rights", "public"),
            data.get("published"),
          )
        elif path == "/api/inbox/promote":
          result = app.memory.promote(
            data["ids"], data["name"], data["objective"], data.get("max_runs", 20)
          )
        elif path == "/api/inbox/dismiss":
          result = app.memory.dismiss(data["id"])
        elif path == "/api/evidence/withdraw":
          app.store.withdraw_evidence(data["pid"], data["id"])
          result = {"ok": True}
        elif path.startswith("/api/tasks/"):
          parts = path.split("/")
          if len(parts) != 5:
            raise Refused("Invalid task route")
          app.store.task_command(parts[3], parts[4])
          result = {"ok": True}
        elif path.startswith("/api/projects/"):
          app.store.set_project_state(path.split("/")[-1], data["state"])
          result = {"ok": True}
        elif path.startswith("/api/actions/"):
          aid = path.split("/")[-1]
          with app.store.db() as db:
            row = db.execute("SELECT tool FROM actions WHERE id=?", (aid,)).fetchone()
          if not row:
            raise Refused("Action not found")
          actions = (
            app.browser_actions
            if row[0].startswith("browser.")
            else app.desktop_actions
            if row[0].startswith("desktop.")
            else app.integrations
          )
          result = actions.decide(aid, data["approve"])
        elif path == "/api/browser/grant":
          result = app.browser.grant(data["pid"], data["url"], data.get("allow", True))
        elif path == "/api/desktop/grant":
          result = app.desktop_actions.grant(
            data["pid"], data["operation"], data["allow"]
          )
        elif path == "/api/desktop/settings":
          with app.store.db() as db:
            app.store.require_project(db, data["pid"])
          result = app.desktop_actions.controls.invoke(
            data["pid"], "open_settings", {"area": data["area"]}
          )
        elif path == "/api/browser/open":
          result = app.browser.invoke(data["pid"], "open", {"url": data["url"]})
        elif path == "/api/providers/check":
          provider = data.get("provider")
          if provider not in PROVIDERS:
            raise Refused("Unknown provider")
          result = probe(provider, read_config(app.store.root))
        else:
          self.send(404, {"error": "Not found"})
          return
        self.send(200, result)
      except Exception as exc:  # noqa: BLE001 — HTTP boundary maps errors to sanitized responses.
        self.failure(exc)

    def failure(self, exc):
      if isinstance(exc, PermissionError):
        self.send(401, {"error": str(exc)})
      elif isinstance(exc, (Refused, ValueError, TypeError, KeyError)):
        self.send(
          400,
          {"error": str(exc)[:500] if isinstance(exc, Refused) else "Invalid request"},
        )
      elif isinstance(exc, urllib.error.HTTPError):
        self.send(
          502,
          {
            "error": "Ve connection unavailable; check sign-in and the configured MCP endpoint"
          },
        )
      else:
        self.send(
          500,
          {
            "error": "Operation did not complete; inspect its stored state before retrying"
          },
        )

  server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
  server.daemon_threads = True
  app.base_url = f"http://127.0.0.1:{server.server_port}"
  app.browser.forbidden_origin = app.base_url
  return server
