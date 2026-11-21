import json
import os
import re
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit, urlunsplit

from .providers import read_config
from .store import Refused, digest, encoded, identity


class NoRedirect(urllib.request.HTTPRedirectHandler):
  def redirect_request(self, req, fp, code, msg, headers, newurl):
    return None


class VeMCP:
  def __init__(self, config, token, transport=None):
    self.endpoint = config.get("endpoint", "")
    parsed = urlsplit(self.endpoint)
    if (
      parsed.scheme != "https"
      or parsed.hostname != "backend.ve.ai"
      or parsed.port not in {None, 443}
      or parsed.username
      or parsed.password
      or parsed.query
      or parsed.fragment
      or not re.fullmatch(r"/(?:[A-Za-z0-9_.-]+/)*mcp/(gmail|outlookmail)", parsed.path)
    ):
      raise Refused("Configure an HTTPS Gmail or Outlook MCP endpoint on backend.ve.ai")
    if not token:
      raise Refused(
        "Ve sign-in is required: provide VEOS_VE_ACCESS_TOKEN to this runtime"
      )
    self.access_token, self.token, self.session = token, None, None
    self.expires = 0
    self.mint_url = urlunsplit(
      (
        parsed.scheme,
        parsed.netloc,
        parsed.path.rsplit("/", 1)[0] + "/token",
        "",
        "",
      )
    )
    self.transport = transport or self.http
    self.config = config

  def mint(self):
    # Ve's normal person path: POST /mcp/token. Never send the user JWT to
    # a provider MCP route, and never use an IAM worker alias as this user.
    req = urllib.request.Request(
      self.mint_url,
      b"",
      {
        "Authorization": "Bearer " + self.access_token,
        "Accept": "application/json",
      },
      method="POST",
    )
    with urllib.request.build_opener(NoRedirect()).open(req, timeout=8) as response:
      raw = response.read(8193)
      if response.status != 200 or len(raw) > 8192:
        raise Refused("Ve did not issue a valid MCP grant")
      grant = json.loads(raw)
    self.accept_grant(grant)

  def accept_grant(self, grant):
    now = time.time()
    if (
      not isinstance(grant, dict)
      or not isinstance(grant.get("token"), str)
      or not re.fullmatch(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\.[A-Za-z0-9_-]{43}",
        grant["token"],
      )
      or type(grant.get("expiresAt")) is not int
      or not now + 5 < grant["expiresAt"] <= now + 605
    ):
      raise Refused("Ve did not issue a valid short-lived MCP grant")
    self.token, self.expires = grant["token"], grant["expiresAt"]
    self.session = None

  def http(self, body):
    if not self.token:
      self.mint()
    if self.expires <= time.time():
      raise Refused("MCP grant expired; reconnect before attempting another operation")
    headers = {
      "Authorization": "Bearer " + self.token,
      "Content-Type": "application/json",
      "Accept": "application/json, text/event-stream",
      "MCP-Protocol-Version": "2025-03-26",
    }
    if self.session:
      headers["Mcp-Session-Id"] = self.session
    req = urllib.request.Request(
      self.endpoint, encoded(body).encode(), headers, method="POST"
    )
    with urllib.request.build_opener(NoRedirect()).open(req, timeout=25) as response:
      self.session = response.headers.get("Mcp-Session-Id", self.session)
      raw = response.read(1_000_001)
      if len(raw) > 1_000_000:
        raise Refused("Integration result exceeds the size limit")
      if not raw:
        return {}
      if "text/event-stream" in response.headers.get("Content-Type", ""):
        for block in raw.decode().replace("\r\n", "\n").split("\n\n"):
          data = "\n".join(
            line[5:].lstrip() for line in block.splitlines() if line.startswith("data:")
          )
          if data:
            item = json.loads(data)
            if item.get("id") == body.get("id"):
              return item
        raise Refused("Integration did not return a completed MCP response")
      return json.loads(raw)

  def request(self, method, params):
    request_id = identity()
    result = self.transport(
      {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
    )
    if (
      not isinstance(result, dict)
      or result.get("id") != request_id
      or result.get("jsonrpc") != "2.0"
      or not isinstance(result.get("result"), dict)
    ):
      raise Refused("Integration response is incomplete or does not match this request")
    if "error" in result:
      raise Refused("Integration rejected the request")
    return result["result"]

  def catalogue(self):
    self.request(
      "initialize",
      {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "veos-runtime", "version": "0.1.0"},
      },
    )
    self.transport({"jsonrpc": "2.0", "method": "notifications/initialized"})
    tools, cursor = {}, None
    for _ in range(10):
      result = self.request("tools/list", {"cursor": cursor} if cursor else {})
      for tool in result.get("tools", []):
        tools[tool["name"]] = tool
      cursor = result.get("nextCursor")
      if not cursor:
        return tools
    raise Refused("Integration catalogue exceeds the page limit")

  def call(self, tool, args):
    result = self.request("tools/call", {"name": tool, "arguments": args})
    if not isinstance(result.get("content"), list) or (
      "isError" in result and type(result["isError"]) is not bool
    ):
      raise Refused("Integration did not confirm a tool result")
    return result


class IntegrationActions:
  def __init__(
    self,
    store,
    client_factory=VeMCP,
    token=lambda: os.environ.get("VEOS_VE_ACCESS_TOKEN", ""),
  ):
    self.store, self.client_factory, self.token = store, client_factory, token

  def connect(self):
    config = read_config(self.store.root)["ve"]
    token = self.token()
    client = self.client_factory(config, token)
    return config, token, client, client.catalogue()

  def call(self, pid, tid, tool, args):
    if (
      not isinstance(tool, str)
      or not isinstance(args, dict)
      or len(encoded(args)) > 50_000
    ):
      raise Refused("Invalid integration call")
    config, token, client, catalogue = self.connect()
    if tool not in catalogue:
      raise Refused("Tool is not available on this connection")
    args = self.bound_args(config, catalogue[tool], args)
    if tool in config.get("read_tools", []):
      hints = catalogue[tool].get("annotations", {})
      if hints.get("readOnlyHint") is not True or hints.get("destructiveHint") is True:
        raise Refused("Configured read tool is not marked read-only by Integration")
      result = client.call(tool, args)
      return {"untrusted": True, "state": result_state(result), "result": result}
    if tool not in config.get("write_tools", []):
      raise Refused("Tool has not been enabled by the user")
    binding = digest(
      {
        "endpoint": config["endpoint"],
        "token": digest(token),
        "schema": catalogue[tool],
        "task": tid,
        "tool": tool,
        "args": args,
      }
    )
    aid = identity()
    with self.store.db() as db:
      db.execute("BEGIN IMMEDIATE")
      p = self.store.require_project(db, pid)
      if p["state"] != "active":
        raise Refused("Project is not active")
      task = db.execute(
        "SELECT project_id,state FROM tasks WHERE id=?", (tid,)
      ).fetchone()
      if not task or task["project_id"] != pid or task["state"] != "running":
        raise Refused("Task is not active in this project")
      existing = db.execute(
        "SELECT * FROM actions WHERE project_id=? AND fingerprint=?",
        (pid, binding),
      ).fetchone()
      if existing:
        return {
          "action_id": existing["id"],
          "state": existing["state"],
          "result": json.loads(existing["result"]) if existing["result"] else None,
        }
      if db.execute(
        "SELECT 1 FROM actions WHERE task_id=? AND state IN ('denied','expired','unknown','failed','cancelled')",
        (tid,),
      ).fetchone():
        raise Refused(
          "A previous action was not completed. Review it before creating a new task; do not repeat it automatically."
        )
      db.execute(
        "INSERT INTO actions VALUES(?,?,?,?,?,?,'pending',?,NULL,?)",
        (
          aid,
          pid,
          tid,
          binding,
          tool,
          encoded(args),
          self.store.clock() + 600,
          self.store.clock(),
        ),
      )
      self.store.event(db, pid, "approval.requested", {"id": aid, "tool": tool})
    self.store.notify()
    return {
      "action_id": aid,
      "state": "pending",
      "message": "Nothing was executed. The user must review this exact action in VeOS.",
    }

  @staticmethod
  def bound_args(config, tool, args):
    connection = config.get("connection_id", "")
    if not re.fullmatch(
      r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", connection
    ):
      raise Refused("Configure the exact connected mailbox ID")
    if "connectedAppId" not in tool.get("inputSchema", {}).get("properties", {}):
      raise Refused("This tool cannot be bound explicitly to the selected mailbox")
    if "connectedAppId" in args and args["connectedAppId"] != connection:
      raise Refused("Tool arguments name a different mailbox")
    return {**args, "connectedAppId": connection}

  def decide(self, aid, approve):
    if type(approve) is not bool:
      raise Refused("Approval must be a boolean")
    with self.store.db() as db:
      row = db.execute("SELECT * FROM actions WHERE id=?", (aid,)).fetchone()
      if not row or row["state"] != "pending":
        raise Refused("Action is not awaiting approval")
      row = dict(row)
    client = None
    if approve:
      config, token, client, catalogue = self.connect()
      args = json.loads(row["args"])
      if (
        row["tool"] not in config.get("write_tools", []) or row["tool"] not in catalogue
      ):
        raise Refused(
          "Connection or enabled tools changed; action must be proposed again"
        )
      self.bound_args(config, catalogue[row["tool"]], args)
      binding = digest(
        {
          "endpoint": config["endpoint"],
          "token": digest(token),
          "schema": catalogue[row["tool"]],
          "task": row["task_id"],
          "tool": row["tool"],
          "args": args,
        }
      )
      if binding != row["fingerprint"]:
        raise Refused("Identity or tool schema changed; action must be proposed again")
      if any(k.lower() in {"draftid", "draft_id"} for k in args) or (
        "draft" in row["tool"].lower() and "send" in row["tool"].lower()
      ):
        raise Refused(
          "Sending a mutable draft by ID needs a content snapshot; use an explicit-content action"
        )
    with self.store.db() as db:
      db.execute("BEGIN IMMEDIATE")
      project = self.store.require_project(db, row["project_id"])
      if approve:
        self.store.check_goal_deadline(db, row["task_id"])
      state = (
        "denied"
        if not approve
        else "expired"
        if row["expires"] <= self.store.clock()
        else "executing"
      )
      if approve and project["state"] != "active":
        raise Refused("Project is not active")
      task = db.execute(
        "SELECT state FROM tasks WHERE id=?", (row["task_id"],)
      ).fetchone()
      if approve and (
        not task
        or task["state"] == "cancelled"
        or (
          project["deadline"] is not None and project["deadline"] <= self.store.clock()
        )
      ):
        raise Refused("Task was cancelled or its project deadline passed")
      changed = db.execute(
        "UPDATE actions SET state=? WHERE id=? AND state='pending'",
        (state, aid),
      ).rowcount
      if not changed:
        raise Refused("Action was already decided")
      self.store.event(
        db, row["project_id"], "approval.decided", {"id": aid, "state": state}
      )
      if state != "executing":
        self.store._action_changed(db, row["task_id"])
    if state == "executing":
      try:
        result = client.call(row["tool"], json.loads(row["args"]))
        state = result_state(result)
      except Exception:  # noqa: BLE001 — Uncertain external effects must never be retried automatically.
        state, result = (
          "unknown",
          {
            "message": "The external outcome is unknown. Check the provider before retrying."
          },
        )
      self.store.settle_action(aid, state, result)
    self.store.notify()
    return {"id": aid, "state": state}


def result_state(result):
  """Ve mcpError can be HTTP 200 without isError. Interpret its own envelope."""
  if (
    not isinstance(result, dict)
    or result.get("isError", False) is not False
    or not isinstance(result.get("content"), list)
  ):
    return "unknown"
  text = "\n".join(
    x["text"]
    for x in result["content"]
    if isinstance(x, dict)
    and x.get("type") == "text"
    and isinstance(x.get("text"), str)
  )
  try:
    body = json.loads(text)
  except ValueError:
    return "unknown"
  if not isinstance(body, dict):
    return "unknown"
  if body.get("success") is True:
    return "succeeded"
  if (
    body.get("success") is False
    and type(body.get("code")) is int
    and 400 <= body["code"] <= 499
  ):
    return "failed"
  return "unknown"
