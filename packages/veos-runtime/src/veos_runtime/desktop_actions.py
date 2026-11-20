"""Project grants and durable reviewed actions for the desktop adapter."""

import json
import threading

from .controls import COMMANDS, READS, Controls
from .store import Refused, digest, encoded, identity


class DesktopActions:
  def __init__(self, store, controls=None):
    self.store = store
    self.controls = controls or Controls(store)
    self.serial = threading.RLock()
    with store.db() as db:
      db.execute(
        "CREATE TABLE IF NOT EXISTS desktop_grants(project_id TEXT NOT NULL REFERENCES projects(id), operation TEXT NOT NULL, PRIMARY KEY(project_id,operation))"
      )

  def status(self, pid):
    with self.store.db() as db:
      self.store.require_project(db, pid)
      granted = {
        row[0]
        for row in db.execute(
          "SELECT operation FROM desktop_grants WHERE project_id=?", (pid,)
        )
      }
    return [
      {**item, "allowed": item["operation"] in granted}
      for item in self.controls.catalogue()
    ]

  def grant(self, pid, operation, allow):
    if operation not in COMMANDS or type(allow) is not bool:
      raise Refused("Choose a desktop capability and a boolean permission")
    with self.store.db() as db:
      self.store.require_project(db, pid)
      if allow:
        db.execute("INSERT OR IGNORE INTO desktop_grants VALUES(?,?)", (pid, operation))
      else:
        db.execute(
          "DELETE FROM desktop_grants WHERE project_id=? AND operation=?",
          (pid, operation),
        )
      self.store.event(
        db, pid, "desktop.permission", {"operation": operation, "allowed": allow}
      )
    self.store.notify()
    return self.status(pid)

  def check(self, pid, operation):
    with self.store.db() as db:
      if self.store.require_project(db, pid)["state"] != "active":
        raise Refused("Project is not active")
      if not db.execute(
        "SELECT 1 FROM desktop_grants WHERE project_id=? AND operation=?",
        (pid, operation),
      ).fetchone():
        raise Refused("Allow this desktop capability in the project before using it")

  def call(self, pid, tid, operation, args, request_key=None):
    self.check(pid, operation)
    self.controls.validate(operation, args)
    if operation in READS:
      return self.controls.invoke(pid, operation, args)
    payload = {
      "operation": operation,
      "arguments": args,
      "binding": self.controls.binding(operation, args),
    }
    if len(encoded(payload)) > 20000:
      raise Refused("Desktop action exceeds its limit")
    if request_key is not None and (
      not isinstance(request_key, str) or not 1 <= len(request_key) <= 160
    ):
      raise Refused("Invalid action request key")
    fingerprint = digest({"task": tid, "desktop": request_key or payload})
    aid = identity()
    with self.store.db() as db:
      db.execute("BEGIN IMMEDIATE")
      row = db.execute(
        "SELECT project_id,state FROM tasks WHERE id=?", (tid,)
      ).fetchone()
      if not row or row["project_id"] != pid or row["state"] != "running":
        raise Refused("Task is not running")
      prior = db.execute(
        "SELECT * FROM actions WHERE fingerprint=?", (fingerprint,)
      ).fetchone()
      if prior:
        if json.loads(prior["args"]) != payload:
          raise Refused("Action request key already identifies different work")
        return {"action_id": prior["id"], "state": prior["state"]}
      if db.execute(
        "SELECT 1 FROM actions WHERE task_id=? AND state IN ('unknown','failed','denied','expired','cancelled')",
        (tid,),
      ).fetchone():
        raise Refused("Review the previous unsuccessful action before continuing")
      db.execute(
        "INSERT INTO actions VALUES(?,?,?,?,?,?,'pending',?,NULL,?)",
        (
          aid,
          pid,
          tid,
          fingerprint,
          "desktop." + operation,
          encoded(payload),
          self.store.clock() + 600,
          self.store.clock(),
        ),
      )
      self.store.event(
        db, pid, "approval.requested", {"id": aid, "tool": "desktop." + operation}
      )
    self.store.notify()
    return {
      "action_id": aid,
      "state": "pending",
      "message": "Review the desktop action in VeOS; no change has been made",
    }

  def decide(self, aid, approve):
    with self.serial:
      return self._decide(aid, approve)

  def _decide(self, aid, approve):
    if type(approve) is not bool:
      raise Refused("Approval must be boolean")
    with self.store.db() as db:
      db.execute("BEGIN IMMEDIATE")
      row = db.execute(
        "SELECT * FROM actions WHERE id=? AND state='pending' AND tool LIKE 'desktop.%'",
        (aid,),
      ).fetchone()
      if not row:
        raise Refused("Action is not pending")
      project = self.store.require_project(db, row["project_id"])
      if approve and (
        project["state"] != "active"
        or project["deadline"] is not None
        and project["deadline"] <= self.store.clock()
      ):
        raise Refused("Project is paused, closed or past its deadline")
      state = (
        "denied"
        if not approve
        else "expired"
        if row["expires"] <= self.store.clock()
        else "executing"
      )
      db.execute("UPDATE actions SET state=? WHERE id=?", (state, aid))
      self.store.event(
        db, row["project_id"], "approval.decided", {"id": aid, "state": state}
      )
      if state != "executing":
        self.store._action_changed(db, row["task_id"])
    if state == "executing":
      payload = json.loads(row["args"])
      try:
        self.check(row["project_id"], payload["operation"])
        current = self.controls.binding(payload["operation"], payload["arguments"])
        if current != payload["binding"]:
          raise Refused("Desktop target changed; observe it and request a new action")
        result = self.controls.invoke(
          row["project_id"], payload["operation"], payload["arguments"]
        )
        state = "succeeded"
      except Refused as exc:
        state, result = "failed", {"message": str(exc)}
      except Exception:  # noqa: BLE001 — A submitted operation may have taken effect.
        state, result = (
          "unknown",
          {"message": "Inspect the desktop outcome before retrying"},
        )
      self.store.settle_action(aid, state, result)
    self.store.notify()
    return {"id": aid, "state": state}
