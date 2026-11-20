"""Incoming voice/MCP delegation into the one existing local task journal."""

import json
import secrets
import threading

from .providers import PROVIDERS
from .store import Refused, bounded, digest

HARNESS_TOOLS = [
  {
    "name": "run_local_agent",
    "description": "Queue the user's task on the selected local harness. Reuse request_key only for retries of the same task. Queued or blocked does not mean completed.",
    "inputSchema": {
      "type": "object",
      "properties": {
        "task": {"type": "string", "minLength": 1, "maxLength": 8000},
        "request_key": {"type": "string", "minLength": 1, "maxLength": 120},
      },
      "required": ["task", "request_key"],
      "additionalProperties": False,
    },
    "annotations": {"readOnlyHint": False, "idempotentHint": True},
  },
  {
    "name": "local_agent_status",
    "description": "Read the scoped local task and its action receipts. Do not treat model text or input delivery as confirmation of an external effect.",
    "inputSchema": {
      "type": "object",
      "properties": {"task_id": {"type": "string", "minLength": 1, "maxLength": 200}},
      "required": ["task_id"],
      "additionalProperties": False,
    },
    "annotations": {"readOnlyHint": True},
  },
  {
    "name": "cancel_local_agent",
    "description": "Cancel pending work. An already submitted external action may finish; cancellation does not undo it.",
    "inputSchema": {
      "type": "object",
      "properties": {"task_id": {"type": "string", "minLength": 1, "maxLength": 200}},
      "required": ["task_id"],
      "additionalProperties": False,
    },
    "annotations": {"readOnlyHint": False, "idempotentHint": True},
  },
]


class Harness:
  def __init__(self, store):
    self.store = store
    self.lock = threading.Lock()
    self.connections = {}
    with store.db() as db:
      db.execute(
        "CREATE TABLE IF NOT EXISTS harness_tasks(task_id TEXT PRIMARY KEY REFERENCES tasks(id),project_id TEXT NOT NULL,provider TEXT NOT NULL)"
      )

  def connect(self, pid, provider):
    if provider not in PROVIDERS or provider == "antigravity":
      raise Refused("Choose an implemented local harness")
    with self.store.db() as db:
      if self.store.require_project(db, pid)["state"] != "active":
        raise Refused("Project is not active")
    token = secrets.token_urlsafe(32)
    with self.lock:
      now = self.store.clock()
      self.connections = {
        key: value for key, value in self.connections.items() if value["expires"] > now
      }
      if len(self.connections) >= 20:
        raise Refused("Close an existing local connection first")
      self.connections[digest(token)] = {
        "project_id": pid,
        "provider": provider,
        "expires": now + 3600,
      }
    return {"token": token, "expires_at": now + 3600, "tools": HARNESS_TOOLS}

  def authorize(self, token):
    with self.lock:
      binding = self.connections.get(digest(token))
      if not binding or binding["expires"] <= self.store.clock():
        raise PermissionError("Local harness connection expired or was revoked")
      return dict(binding)

  def disconnect(self, token):
    with self.lock:
      self.connections.pop(digest(token), None)

  def snapshot(self, binding, tid):
    with self.store.db() as db:
      if not db.execute(
        "SELECT 1 FROM harness_tasks WHERE task_id=? AND project_id=? AND provider=?",
        (tid, binding["project_id"], binding["provider"]),
      ).fetchone():
        raise Refused("Task is outside this connection's scope")
    row = self.store.task(tid)
    actions = self.store.task_actions(tid)
    result = row.get("result") or ""
    return {
      "task_id": tid,
      "state": row["state"],
      "reason": row.get("reason"),
      "result": result[:6000],
      "result_truncated": len(result) > 6000,
      "actions": [
        {
          "id": a["id"],
          "tool": a["tool"],
          "state": a["state"],
          "business_outcome_confirmed": bool(
            (a["result"] or {}).get("business_outcome_confirmed")
          ),
        }
        for a in actions
      ],
      "external_action_confirmed": bool(actions)
      and all(
        a["state"] == "succeeded"
        and (a["result"] or {}).get("business_outcome_confirmed") is True
        for a in actions
      ),
      "note": "Task state and narrative are not independent proof of an external effect. Review action receipts.",
    }

  def call(self, binding, name, args):
    spec = next((t for t in HARNESS_TOOLS if t["name"] == name), None)
    if (
      not spec
      or not isinstance(args, dict)
      or set(args) != set(spec["inputSchema"]["required"])
    ):
      raise Refused("Unknown harness tool or invalid arguments")
    for key, value in args.items():
      bounded(value, key, spec["inputSchema"]["properties"][key]["maxLength"])
    if name == "run_local_agent":
      with self.store.db() as db:
        if self.store.require_project(db, binding["project_id"])["state"] != "active":
          raise Refused("Project is paused or closed")
      tid = self.store.create_task(
        binding["project_id"],
        args["task"],
        binding["provider"],
        "harness:"
        + digest({"provider": binding["provider"], "key": args["request_key"]}),
      )
      with self.store.db() as db:
        db.execute(
          "INSERT OR IGNORE INTO harness_tasks VALUES(?,?,?)",
          (tid, binding["project_id"], binding["provider"]),
        )
        self.store.event(
          db, binding["project_id"], "harness.admitted", {"task_id": tid}
        )
      self.store.notify()
    else:
      tid = args["task_id"]
      before = self.snapshot(binding, tid)
      if name == "cancel_local_agent" and before["state"] not in {
        "succeeded",
        "cancelled",
      }:
        try:
          self.store.task_command(tid, "cancel")
        except Refused:
          if self.snapshot(binding, tid)["state"] not in {"succeeded", "cancelled"}:
            raise
    return self.snapshot(binding, tid)


def serve_stdio(url, token):
  import sys
  import urllib.request

  for line in sys.stdin:
    request = None
    try:
      if len(line) > 20000:
        continue
      request = json.loads(line)
      if "id" not in request:
        continue
      method = request.get("method")
      if method == "initialize":
        result = {
          "protocolVersion": "2025-03-26",
          "capabilities": {"tools": {}},
          "serverInfo": {"name": "veos-local-harness", "version": "0.3.0"},
        }
      elif method == "tools/list":
        result = {"tools": HARNESS_TOOLS}
      elif method == "ping":
        result = {}
      elif method == "tools/call":
        req = urllib.request.Request(
          url + "/api/harness/tools",
          json.dumps(request.get("params", {})).encode(),
          {"Authorization": "Bearer " + token, "Content-Type": "application/json"},
          method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as response:
          data = response.read(65537)
          if len(data) > 65536:
            raise ValueError("Oversized task result")
          content = json.loads(data)
        result = {
          "content": [{"type": "text", "text": json.dumps(content)}],
          "structuredContent": content,
        }
      else:
        result = {
          "isError": True,
          "content": [{"type": "text", "text": "Unsupported local harness method"}],
        }
    except Exception:  # noqa: BLE001 — Never return local connection credentials.
      if not isinstance(request, dict) or "id" not in request:
        continue
      result = {
        "isError": True,
        "content": [
          {
            "type": "text",
            "text": "Local task request failed or was refused. Check Mission Desk; do not assume completion.",
          }
        ],
      }
    print(
      json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True
    )
