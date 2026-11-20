"""Project-scoped MCP controls owned by an external reasoning harness."""

import json
import secrets
import threading

from .mcp import TOOLS, tool
from .store import Refused, bounded, digest, integer

SESSION_TOOLS = [
  tool(
    "start_control_task",
    "Start or reconnect to work in this connection's project. Reuse request_key only for the same task. This harness executes the task; no other agent is launched. Only one active task per connection.",
    {"task": {"type": "string"}, "request_key": {"type": "string"}},
    ["task", "request_key"],
  ),
  tool(
    "control_task_status",
    "Read this connection's task and paginated action receipts, including result data. Pending review is not execution; input delivery is not business success. Read all pages and verify application state before finishing.",
    {"offset": {"type": "integer"}, "limit": {"type": "integer"}},
    [],
  ),
  tool(
    "finish_control_task",
    "Save the result and release the current task. For success, supply receipt_version from the latest status; pending actions prevent completion. Other outcomes cancel pending approvals, but cannot undo submitted effects.",
    {
      "result": {"type": "string"},
      "outcome": {
        "type": "string",
        "enum": ["succeeded", "blocked", "failed", "unknown"],
      },
      "receipt_version": {"type": "integer"},
    },
    ["result", "outcome", "receipt_version"],
  ),
  tool(
    "cancel_control_task",
    "Cancel the current task and pending approvals. Submitted effects may still finish; cancellation does not undo them.",
    {},
    [],
  ),
]
CONTROL_TOOLS = SESSION_TOOLS + TOOLS


class ControlSessions:
  def __init__(self, store, call_tool):
    self.store, self.call_tool = store, call_tool
    self.lock = threading.RLock()
    self.connections = {}

  def connect(self, pid):
    with self.lock:
      with self.store.db() as db:
        project = self.store.require_project(db, pid)
        if project["state"] != "active" or (
          project["deadline"] is not None and project["deadline"] <= self.store.clock()
        ):
          raise Refused("Project is not active or has passed its deadline")
      if len(self.connections) >= 20:
        raise Refused("Close an existing control connection first")
      token = secrets.token_urlsafe(32)
      key = digest(token)
      expires = self.store.clock() + 3600
      timer = threading.Timer(
        3600, self._disconnect, args=(key, "control_session_expired")
      )
      timer.daemon = True
      self.connections[key] = {
        "project_id": pid,
        "expires": expires,
        "task_id": None,
        "lease": None,
        "timer": timer,
      }
      timer.start()
      return {"token": token, "expires_at": expires}

  def authorize(self, token):
    with self.lock:
      key = digest(token)
      binding = self.connections.get(key)
      if binding and binding["expires"] <= self.store.clock():
        self._disconnect(key, "control_session_expired")
        binding = None
      if not binding:
        raise PermissionError("Control connection expired or was revoked")
      return key

  def _disconnect(self, key, reason="control_session_disconnected"):
    with self.lock:
      binding = self.connections.pop(key, None)
      if binding:
        binding["timer"].cancel()
        if binding["lease"]:
          self.store.finish(
            binding["task_id"], binding["lease"], "unknown", reason=reason
          )

  def disconnect(self, key):
    self._disconnect(key)

  def close(self):
    with self.lock:
      for key in list(self.connections):
        self._disconnect(key, "control_runtime_stopped")

  def snapshot(self, binding, offset=0, limit=5):
    integer(offset, "offset", 0, 10000)
    integer(limit, "limit", 1, 10)
    if not binding["task_id"]:
      raise Refused("Start a control task first")
    # Keep the receipt version and receipt data from the same DB snapshot.
    with self.store.db() as db:
      db.execute("BEGIN")
      row = dict(
        db.execute("SELECT * FROM tasks WHERE id=?", (binding["task_id"],)).fetchone()
      )
      actions = [
        {**dict(a), "result": json.loads(a["result"]) if a["result"] else None}
        for a in db.execute(
          "SELECT id,tool,state,result FROM actions WHERE task_id=? ORDER BY created,id LIMIT ? OFFSET ?",
          (row["id"], limit + 1, offset),
        )
      ]
    return {
      "task_id": row["id"],
      "state": row["state"],
      "reason": row["reason"],
      "result": (row["result"] or "")[:6000],
      "receipt_version": row["action_version"],
      "actions": actions[:limit],
      "next_offset": offset + limit if len(actions) > limit else None,
      "note": "This harness owns execution. Receipts and task narratives do not by themselves prove the user's objective was achieved.",
    }

  def start(self, binding, task, request_key):
    bounded(task, "task", 8000)
    bounded(request_key, "request_key", 120)
    with self.store.db() as db:
      if self.store.require_project(db, binding["project_id"])["state"] != "active":
        raise Refused("Project is not active")
    key = "control:" + digest(request_key)
    if binding["task_id"]:
      current = self.store.task(binding["task_id"])
      if current["state"] == "running" and current["request_key"] != key:
        raise Refused("Finish or cancel the current control task first")
    tid = self.store.create_task(
      binding["project_id"], task, "external", key, execution_owner="external"
    )
    row = self.store.task(tid)
    if tid == binding["task_id"] and self.store.permitted(tid, binding["lease"]):
      return self.snapshot(binding)
    if row["state"] in {"succeeded", "cancelled"}:
      binding.update(task_id=tid, lease=None)
      return self.snapshot(binding)
    if row["state"] != "queued":
      raise Refused(
        "Task is owned elsewhere or needs explicit review and retry in Mission Desk"
      )
    lease = self.store.claim(tid, execution_owner="external")
    if not lease:
      raise Refused(
        "Task cannot start; inspect its ownership, project budget and deadline"
      )
    binding.update(task_id=tid, lease=lease)
    return self.snapshot(binding)

  def call(self, key, name, args):
    with self.lock:
      binding = self.connections.get(key)
      if not binding or binding["expires"] <= self.store.clock():
        self._disconnect(key, "control_session_expired")
        raise PermissionError("Control connection expired or was revoked")
      descriptor = next((t for t in SESSION_TOOLS if t["name"] == name), None)
      if descriptor:
        schema = descriptor["inputSchema"]
        if (
          not isinstance(args, dict)
          or set(args) - set(schema["properties"])
          or set(schema["required"]) - set(args)
        ):
          raise Refused("Invalid control task arguments")
        for field, value in args.items():
          spec = schema["properties"][field]
          if type(value) is not {"string": str, "integer": int}[spec["type"]] or (
            "enum" in spec and value not in spec["enum"]
          ):
            raise Refused("Invalid control task argument type")
        if name == "start_control_task":
          return self.start(binding, **args)
        if name == "control_task_status":
          return self.snapshot(binding, **args)
        if not binding["task_id"]:
          raise Refused("Start a control task first")
        if name == "cancel_control_task":
          row = self.store.task(binding["task_id"])
          if row["state"] not in {"succeeded", "cancelled"}:
            if not binding["lease"] or row["lease"] != binding["lease"]:
              raise Refused("Task is no longer owned by this connection")
            self.store.task_command(
              row["id"], "cancel", expected_lease=binding["lease"]
            )
        else:
          bounded(args["result"], "result", 20000)
          if not self.store.finish(
            binding["task_id"],
            binding["lease"],
            args["outcome"],
            args["result"],
            receipt_version=args["receipt_version"],
          ):
            raise Refused("Task is no longer owned by this connection")
        binding["lease"] = None
        return self.snapshot(binding)
      if not binding["lease"] or not self.store.permitted(
        binding["task_id"], binding["lease"]
      ):
        raise PermissionError(
          "Start an active control task before using application tools"
        )
      return self.call_tool(
        {k: binding[k] for k in ("project_id", "task_id", "lease")}, name, args
      )
