import hashlib
import json
import math
import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


class Refused(ValueError):
  pass


def encoded(value):
  return json.dumps(
    value,
    sort_keys=True,
    separators=(",", ":"),
    ensure_ascii=False,
    allow_nan=False,
  )


def digest(value):
  return hashlib.sha256(encoded(value).encode()).hexdigest()


def identity():
  return str(uuid.uuid4())


def bounded(value, name, maximum=20_000):
  if (
    not isinstance(value, str)
    or not value.strip()
    or len(value) > maximum
    or "\x00" in value
  ):
    raise Refused(f"Invalid {name}")
  return value.strip()


def integer(value, name, low, high):
  if type(value) is not int or not low <= value <= high:
    raise Refused(f"{name} must be between {low} and {high}")
  return value


def shaped(value, spec, required=()):
  """Check a record's structured fields against its type's contract.

  spec maps a field to ("text", max), ("list", max items), ("enum", choices),
  ("int", low, high) or ("object",).
  """
  value = {} if value is None else value
  if not isinstance(value, dict) or set(value) - set(spec):
    raise Refused("Unknown field; allowed: " + ", ".join(sorted(spec)))
  out = {}
  for key, item in value.items():
    kind = spec[key]
    if kind[0] == "text":
      out[key] = bounded(item, key, kind[1])
    elif kind[0] == "list":
      if not isinstance(item, list) or len(item) > kind[1]:
        raise Refused(f"Invalid {key}")
      out[key] = [bounded(x, key, 1000) for x in item]
    elif kind[0] == "enum":
      if item not in kind[1]:
        raise Refused(f"{key} must be one of: " + ", ".join(kind[1]))
      out[key] = item
    elif kind[0] == "int":
      out[key] = integer(item, key, kind[1], kind[2])
    else:
      if not isinstance(item, dict) or len(encoded(item)) > 4000:
        raise Refused(f"Invalid {key}")
      out[key] = item
  if set(required) - set(out):
    raise Refused("Missing: " + ", ".join(sorted(set(required) - set(out))))
  return out


ACCESS = ("public", "licensed", "internal")
CLAIM_DETAIL = {
  "claim": {},
  "hypothesis": {"alternatives": ("list", 10), "falsification": ("text", 2000)},
  "decision": {"options": ("list", 10), "rationale": ("text", 4000)},
}


class Store:
  def __init__(self, root, clock=time.time):
    self.root = Path(root).resolve()
    self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if self.root.stat().st_uid != os.getuid():
      raise Refused("The runtime directory must belong to this OS user")
    os.chmod(self.root, 0o700)
    self.clock = clock
    self.changed = threading.Condition()
    self.change_version = 0
    with self.db() as db:
      db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS projects (
                  id TEXT PRIMARY KEY, name TEXT NOT NULL, objective TEXT NOT NULL,
                  state TEXT NOT NULL DEFAULT 'active', max_runs INTEGER NOT NULL,
                  runs_used INTEGER NOT NULL DEFAULT 0, deadline REAL,
                  created REAL NOT NULL, updated REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tasks (
                  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                  request_key TEXT NOT NULL, request_hash TEXT NOT NULL,
                  instruction TEXT NOT NULL, provider TEXT NOT NULL,
                  state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                  max_attempts INTEGER NOT NULL, not_before REAL NOT NULL,
                  parent_id TEXT REFERENCES tasks(id), result TEXT, reason TEXT,
                  lease TEXT, created REAL NOT NULL, updated REAL NOT NULL,
                  UNIQUE(project_id, request_key)
                );
                CREATE TABLE IF NOT EXISTS dependencies (
                  task_id TEXT NOT NULL REFERENCES tasks(id),
                  dependency_id TEXT NOT NULL REFERENCES tasks(id),
                  PRIMARY KEY(task_id, dependency_id)
                );
                CREATE TABLE IF NOT EXISTS evidence (
                  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                  task_id TEXT REFERENCES tasks(id), uri TEXT NOT NULL,
                  excerpt TEXT NOT NULL, source_hash TEXT NOT NULL,
                  retrieved REAL NOT NULL, state TEXT NOT NULL DEFAULT 'active',
                  UNIQUE(project_id, source_hash)
                );
                CREATE TABLE IF NOT EXISTS claims (
                  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                  statement TEXT NOT NULL, kind TEXT NOT NULL, state TEXT NOT NULL,
                  created REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS claim_evidence (
                  claim_id TEXT NOT NULL REFERENCES claims(id),
                  evidence_id TEXT NOT NULL REFERENCES evidence(id),
                  relation TEXT NOT NULL, PRIMARY KEY(claim_id, evidence_id)
                );
                CREATE TABLE IF NOT EXISTS actions (
                  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                  task_id TEXT REFERENCES tasks(id), fingerprint TEXT NOT NULL,
                  tool TEXT NOT NULL, args TEXT NOT NULL, state TEXT NOT NULL,
                  expires REAL NOT NULL, result TEXT, created REAL NOT NULL,
                  UNIQUE(project_id, fingerprint)
                );
                CREATE TABLE IF NOT EXISTS events (
                  seq INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT,
                  kind TEXT NOT NULL, payload TEXT NOT NULL, created REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS tasks_ready ON tasks(state, not_before, created);
                CREATE INDEX IF NOT EXISTS evidence_project ON evidence(project_id, retrieved);
                CREATE INDEX IF NOT EXISTS claims_project ON claims(project_id, created);
                CREATE TABLE IF NOT EXISTS runs (
                  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                  task_id TEXT NOT NULL REFERENCES tasks(id), provider TEXT NOT NULL,
                  state TEXT NOT NULL, started REAL NOT NULL, finished REAL,
                  result TEXT, reason TEXT
                );
                CREATE TABLE IF NOT EXISTS artifacts (
                  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                  task_id TEXT REFERENCES tasks(id), run_id TEXT REFERENCES runs(id),
                  name TEXT NOT NULL, media_type TEXT NOT NULL, sha256 TEXT NOT NULL,
                  size INTEGER NOT NULL, created REAL NOT NULL,
                  UNIQUE(project_id,name,sha256)
                );
                CREATE TABLE IF NOT EXISTS memories (
                  id TEXT PRIMARY KEY, scope TEXT NOT NULL, kind TEXT NOT NULL,
                  project_id TEXT REFERENCES projects(id), statement TEXT NOT NULL,
                  detail TEXT NOT NULL DEFAULT '{}', state TEXT NOT NULL,
                  author TEXT NOT NULL, task_id TEXT REFERENCES tasks(id),
                  run_id TEXT REFERENCES runs(id),
                  supersedes TEXT REFERENCES memories(id),
                  created REAL NOT NULL, updated REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS memories_scope ON memories(scope, state, project_id, created);
                CREATE TABLE IF NOT EXISTS claim_revisions (
                  claim_id TEXT NOT NULL REFERENCES claims(id), seq INTEGER NOT NULL,
                  confidence INTEGER, state TEXT NOT NULL, reason TEXT NOT NULL,
                  author TEXT NOT NULL, task_id TEXT REFERENCES tasks(id),
                  run_id TEXT REFERENCES runs(id), created REAL NOT NULL,
                  PRIMARY KEY(claim_id, seq)
                );
                CREATE TABLE IF NOT EXISTS entities (
                  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                  kind TEXT NOT NULL, name TEXT NOT NULL,
                  detail TEXT NOT NULL DEFAULT '{}', state TEXT NOT NULL,
                  merged_into TEXT REFERENCES entities(id),
                  task_id TEXT REFERENCES tasks(id), run_id TEXT REFERENCES runs(id),
                  created REAL NOT NULL, updated REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS entities_project ON entities(project_id, state, created);
                CREATE TABLE IF NOT EXISTS entity_keys (
                  project_id TEXT NOT NULL REFERENCES projects(id), key TEXT NOT NULL,
                  entity_id TEXT NOT NULL REFERENCES entities(id), label TEXT NOT NULL,
                  PRIMARY KEY(project_id, key, entity_id)
                );
                CREATE INDEX IF NOT EXISTS entity_keys_entity ON entity_keys(entity_id);
                CREATE INDEX IF NOT EXISTS entity_keys_key ON entity_keys(key);
                CREATE TABLE IF NOT EXISTS signals (
                  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                  source TEXT NOT NULL, uri TEXT, actor TEXT, content TEXT NOT NULL,
                  content_hash TEXT NOT NULL, rights TEXT NOT NULL,
                  observed REAL NOT NULL, published REAL,
                  state TEXT NOT NULL DEFAULT 'active',
                  task_id TEXT REFERENCES tasks(id), run_id TEXT REFERENCES runs(id),
                  UNIQUE(project_id, content_hash)
                );
                CREATE TABLE IF NOT EXISTS links (
                  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                  subject_type TEXT NOT NULL, subject_id TEXT NOT NULL,
                  relation TEXT NOT NULL, object_type TEXT NOT NULL,
                  object_id TEXT NOT NULL, confidence INTEGER,
                  state TEXT NOT NULL DEFAULT 'active', author TEXT NOT NULL,
                  task_id TEXT REFERENCES tasks(id), run_id TEXT REFERENCES runs(id),
                  created REAL NOT NULL,
                  UNIQUE(project_id, subject_type, subject_id, relation, object_type, object_id)
                );
                CREATE INDEX IF NOT EXISTS links_object ON links(project_id, object_type, object_id);
                CREATE TABLE IF NOT EXISTS records (
                  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                  type TEXT NOT NULL, title TEXT NOT NULL,
                  detail TEXT NOT NULL DEFAULT '{}', state TEXT NOT NULL,
                  author TEXT NOT NULL, task_id TEXT REFERENCES tasks(id),
                  run_id TEXT REFERENCES runs(id),
                  supersedes TEXT REFERENCES records(id),
                  created REAL NOT NULL, updated REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS records_project ON records(project_id, type, state, created);
                CREATE TABLE IF NOT EXISTS goal_plans (
                  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                  goal_id TEXT NOT NULL REFERENCES records(id),
                  signal_id TEXT REFERENCES signals(id), request_key TEXT NOT NULL,
                  request_hash TEXT NOT NULL, steps TEXT NOT NULL,
                  state TEXT NOT NULL, author TEXT NOT NULL,
                  task_id TEXT REFERENCES tasks(id), run_id TEXT REFERENCES runs(id),
                  created REAL NOT NULL, updated REAL NOT NULL,
                  UNIQUE(project_id, request_key), UNIQUE(goal_id, signal_id)
                );
                CREATE TABLE IF NOT EXISTS inbox (
                  id TEXT PRIMARY KEY, source TEXT NOT NULL, uri TEXT, actor TEXT,
                  content TEXT NOT NULL, content_hash TEXT NOT NULL UNIQUE,
                  rights TEXT NOT NULL, observed REAL NOT NULL, published REAL,
                  mentions TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'held',
                  project_id TEXT REFERENCES projects(id)
                );
            """)
      # Additive migrations also upgrade workspaces created by 0.1.0.
      db.execute("BEGIN IMMEDIATE")
      for table, columns in {
        "tasks": {
          "goal_plan_id": "TEXT REFERENCES goal_plans(id)",
          "execution_owner": "TEXT NOT NULL DEFAULT 'worker'",
          "action_version": "INTEGER NOT NULL DEFAULT 0",
          "seen_action_version": "INTEGER NOT NULL DEFAULT 0",
          "continuation": "INTEGER NOT NULL DEFAULT 0",
        },
        "claims": {
          "task_id": "TEXT REFERENCES tasks(id)",
          "run_id": "TEXT REFERENCES runs(id)",
          "confidence": "INTEGER",
          "detail": "TEXT NOT NULL DEFAULT '{}'",
        },
        "evidence": {
          "run_id": "TEXT REFERENCES runs(id)",
          "expires": "REAL",
          "access": "TEXT NOT NULL DEFAULT 'public'",
          "confidence": "INTEGER",
        },
      }.items():
        present = {r["name"] for r in db.execute(f"PRAGMA table_info({table})")}
        for column, definition in columns.items():
          if column not in present:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
      db.execute("CREATE INDEX IF NOT EXISTS tasks_goal_plan ON tasks(goal_plan_id)")
      db.execute("PRAGMA user_version=6")
    os.chmod(self.root / "state.sqlite3", 0o600)

  @contextmanager
  def db(self):
    db = sqlite3.connect(self.root / "state.sqlite3", timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    try:
      yield db
      db.commit()
    except BaseException:
      db.rollback()
      raise
    finally:
      db.close()

  def event(self, db, project, kind, payload):
    db.execute(
      "INSERT INTO events(project_id,kind,payload,created) VALUES(?,?,?,?)",
      (project, kind, encoded(payload), self.clock()),
    )

  def notify(self):
    with self.changed:
      self.change_version += 1
      self.changed.notify_all()

  def require_project(self, db, project_id):
    row = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    if not row:
      raise Refused("Project not found")
    return row

  def create_project(self, name, objective, max_runs=20, deadline=None):
    name, objective = bounded(name, "name", 120), bounded(objective, "objective")
    integer(max_runs, "max_runs", 1, 1000)
    if deadline is not None and (
      type(deadline) not in (int, float)
      or not math.isfinite(deadline)
      or deadline <= self.clock()
    ):
      raise Refused("Deadline must be in the future")
    pid, now = identity(), self.clock()
    with self.db() as db:
      db.execute(
        "INSERT INTO projects VALUES(?,?,?,'active',?,0,?,?,?)",
        (pid, name, objective, max_runs, deadline, now, now),
      )
      self.event(db, pid, "project.created", {"id": pid})
    self.notify()
    return pid

  def set_project_state(self, pid, state):
    if state not in {"active", "paused", "closed"}:
      raise Refused("Invalid project state")
    with self.db() as db:
      db.execute("BEGIN IMMEDIATE")
      project = self.require_project(db, pid)
      if project["state"] == "closed":
        raise Refused("A closed project cannot be reopened")
      db.execute(
        "UPDATE projects SET state=?,updated=? WHERE id=?",
        (state, self.clock(), pid),
      )
      if state == "closed":
        db.execute(
          "UPDATE tasks SET state='cancelled',lease=NULL,updated=? WHERE project_id=? AND state IN ('proposed','queued','running','waiting_subscription','waiting_approval','blocked','unknown','failed')",
          (self.clock(), pid),
        )
        db.execute(
          "UPDATE actions SET state='cancelled' WHERE project_id=? AND state='pending'",
          (pid,),
        )
        db.execute(
          "UPDATE runs SET state='cancelled',finished=? WHERE project_id=? AND state='running'",
          (self.clock(), pid),
        )
      self.event(db, pid, "project.state", {"state": state})
    self.notify()

  def create_task(
    self,
    pid,
    instruction,
    provider,
    request_key,
    dependencies=None,
    max_attempts=2,
    not_before=None,
    parent_id=None,
    proposed=False,
    execution_owner="worker",
  ):
    with self.db() as db:
      db.execute("BEGIN IMMEDIATE")
      tid = self._create_task(
        db,
        pid,
        instruction,
        provider,
        request_key,
        dependencies,
        max_attempts,
        not_before,
        parent_id,
        proposed,
        execution_owner,
      )
    self.notify()
    return tid

  def _create_task(
    self,
    db,
    pid,
    instruction,
    provider,
    request_key,
    dependencies=None,
    max_attempts=2,
    not_before=None,
    parent_id=None,
    proposed=False,
    execution_owner="worker",
    goal_plan_id=None,
  ):
    """Insert inside the caller's write transaction, including a whole goal plan."""
    instruction = bounded(instruction, "instruction")
    request_key = bounded(request_key, "request_key", 160)
    if execution_owner not in {"worker", "external"} or (
      execution_owner == "external" and provider != "external"
    ):
      raise Refused("Invalid task execution owner")
    if provider not in {"codex", "claude", "hermes", "antigravity"} and not (
      execution_owner == "external" and provider == "external"
    ):
      raise Refused("Unsupported subscription provider")
    integer(max_attempts, "max_attempts", 1, 5)
    deps = dependencies or []
    if (
      not isinstance(deps, list)
      or len(deps) > 20
      or any(not isinstance(x, str) for x in deps)
    ):
      raise Refused("Invalid dependencies")
    when = self.clock() if not_before is None else not_before
    if (
      type(when) not in (int, float)
      or not math.isfinite(when)
      or when < 0
      or when > self.clock() + 366 * 86400
    ):
      raise Refused("Invalid scheduled time")
    body = {
      "instruction": instruction,
      "provider": provider,
      "dependencies": sorted(set(deps)),
      "max_attempts": max_attempts,
      "not_before": not_before,
      "parent_id": parent_id,
      "proposed": proposed,
    }
    if execution_owner == "external":
      body["execution_owner"] = execution_owner
    if goal_plan_id:
      body["goal_plan_id"] = goal_plan_id
    tid, now = identity(), self.clock()
    p = self.require_project(db, pid)
    if p["state"] == "closed":
      raise Refused("Project is closed")
    old = db.execute(
      "SELECT id,request_hash FROM tasks WHERE project_id=? AND request_key=?",
      (pid, request_key),
    ).fetchone()
    if old:
      if old["request_hash"] != digest(body):
        raise Refused("Idempotency key was already used for different work")
      return old["id"]
    if parent_id and proposed is not True:
      raise Refused("Agent-created tasks must first be accepted by the user")
    for dep in set(deps + ([parent_id] if parent_id else [])):
      row = db.execute("SELECT project_id FROM tasks WHERE id=?", (dep,)).fetchone()
      if not row or row["project_id"] != pid:
        raise Refused("Dependency belongs to another project or does not exist")
    count = db.execute(
      "SELECT count(*) FROM tasks WHERE project_id=? AND state IN ('proposed','queued','running')",
      (pid,),
    ).fetchone()[0]
    if count >= 100:
      raise Refused("Project already has 100 pending tasks")
    db.execute(
      "INSERT INTO tasks(id,project_id,request_key,request_hash,instruction,provider,state,max_attempts,not_before,parent_id,created,updated,execution_owner,goal_plan_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
      (
        tid,
        pid,
        request_key,
        digest(body),
        instruction,
        provider,
        "proposed" if proposed else "queued",
        max_attempts,
        when,
        parent_id,
        now,
        now,
        execution_owner,
        goal_plan_id,
      ),
    )
    db.executemany(
      "INSERT INTO dependencies VALUES(?,?)",
      [(tid, dep) for dep in set(deps)],
    )
    self.event(db, pid, "task.created", {"id": tid, "proposed": proposed})
    return tid

  def task(self, tid):
    with self.db() as db:
      row = db.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
      if not row:
        raise Refused("Task not found")
      return dict(row)

  def task_command(self, tid, command, expected_lease=None):
    with self.db() as db:
      db.execute("BEGIN IMMEDIATE")
      row = db.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
      if not row:
        raise Refused("Task not found")
      if expected_lease is not None and row["lease"] != expected_lease:
        raise Refused("Task is no longer owned by this connection")
      project = self.require_project(db, row["project_id"])
      if command != "cancel" and project["state"] == "closed":
        raise Refused("Project is closed")
      if command == "accept" and row["state"] == "proposed":
        state = "queued"
      elif command == "retry" and row["state"] in {
        "blocked",
        "failed",
        "unknown",
        "waiting_subscription",
      }:
        if row["attempts"] >= row["max_attempts"]:
          raise Refused(
            "Task attempt limit reached; create a new task after reviewing the outcome"
          )
        state = "queued"
      elif command == "cancel" and row["state"] not in {"succeeded", "cancelled"}:
        state = "cancelled"
      else:
        raise Refused("Task cannot make that transition")
      db.execute(
        "UPDATE tasks SET state=?,lease=NULL,reason=NULL,updated=? WHERE id=?",
        (state, self.clock(), tid),
      )
      if state == "cancelled":
        db.execute(
          "UPDATE actions SET state='cancelled' WHERE task_id=? AND state='pending'",
          (tid,),
        )
        db.execute(
          "UPDATE runs SET state='cancelled',finished=? WHERE task_id=? AND state='running'",
          (self.clock(), tid),
        )
      self.event(db, row["project_id"], "task.state", {"id": tid, "state": state})
    self.notify()

  def recover(self):
    with self.db() as db:
      db.execute("BEGIN IMMEDIATE")
      rows = db.execute(
        "SELECT id,project_id FROM tasks WHERE state='running'"
      ).fetchall()
      for row in rows:
        db.execute(
          "UPDATE actions SET state='cancelled' WHERE task_id=? AND state='pending' AND EXISTS(SELECT 1 FROM tasks WHERE id=? AND execution_owner='external')",
          (row["id"], row["id"]),
        )
        db.execute(
          "UPDATE tasks SET state='unknown',reason='interrupted_process',lease=NULL,updated=? WHERE id=?",
          (self.clock(), row["id"]),
        )
        self.event(db, row["project_id"], "task.interrupted", {"id": row["id"]})
      db.execute(
        "UPDATE runs SET state='unknown',reason='interrupted_process',finished=? WHERE state='running'",
        (self.clock(),),
      )
      interrupted = db.execute(
        "SELECT DISTINCT task_id FROM actions WHERE state='executing'"
      ).fetchall()
      db.execute("UPDATE actions SET state='unknown' WHERE state='executing'")
      for action in interrupted:
        self._action_changed(db, action["task_id"])
    self.notify()
    return len(rows)

  def goal_limits(self, db, goal_id):
    row = db.execute(
      "SELECT g.detail,g.created,p.max_runs FROM records g JOIN projects p ON p.id=g.project_id WHERE g.id=? AND g.type='goal'",
      (goal_id,),
    ).fetchone()
    if not row:
      raise Refused("Goal not found")
    constraints = json.loads(row["detail"]).get("constraints", {})
    limit = integer(
      constraints.get("max_runs", row["max_runs"]), "goal max_runs", 1, 1000
    )
    days = constraints.get("deadline_days")
    deadline = (
      None
      if days is None
      else row["created"] + integer(days, "goal deadline_days", 1, 366) * 86400
    )
    used = db.execute(
      "SELECT count(*) FROM runs r JOIN tasks t ON t.id=r.task_id JOIN goal_plans gp ON gp.id=t.goal_plan_id WHERE gp.goal_id=?",
      (goal_id,),
    ).fetchone()[0]
    return limit, used, deadline

  def goal_block_reason(self, db, plan_id, check_budget=True):
    if not plan_id:
      return None
    row = db.execute("SELECT goal_id FROM goal_plans WHERE id=?", (plan_id,)).fetchone()
    limit, used, deadline = self.goal_limits(db, row["goal_id"])
    if deadline is not None and deadline <= self.clock():
      return "goal_deadline_passed"
    if check_budget and used >= limit:
      return "goal_run_budget_exhausted"
    return None

  def check_goal_deadline(self, db, tid):
    row = db.execute("SELECT goal_plan_id FROM tasks WHERE id=?", (tid,)).fetchone()
    if row and self.goal_block_reason(db, row["goal_plan_id"], check_budget=False):
      raise Refused("Goal deadline passed")

  def ready(self):
    with self.db() as db:
      out = []
      for row in db.execute(
        """SELECT t.* FROM tasks t JOIN projects p ON p.id=t.project_id
            WHERE t.state='queued' AND t.execution_owner='worker' AND t.not_before<=? AND p.state='active'
            AND p.runs_used<p.max_runs AND (p.deadline IS NULL OR p.deadline>?)
            AND (t.attempts<t.max_attempts OR t.continuation=1)
            AND NOT EXISTS(SELECT 1 FROM dependencies d JOIN tasks x ON x.id=d.dependency_id WHERE d.task_id=t.id AND x.state!='succeeded')
            ORDER BY t.created""",
        (self.clock(), self.clock()),
      ):
        if not self.goal_block_reason(db, row["goal_plan_id"]):
          out.append(dict(row))
          if len(out) == 100:
            break
      return out

  def block(self, tid, reason, state="blocked"):
    with self.db() as db:
      row = db.execute("SELECT project_id FROM tasks WHERE id=?", (tid,)).fetchone()
      if row:
        db.execute(
          "UPDATE tasks SET state=?,reason=?,updated=? WHERE id=? AND state='queued'",
          (state, reason, self.clock(), tid),
        )
        self.event(db, row[0], "task.blocked", {"id": tid, "reason": reason})
    self.notify()

  def claim(self, tid, execution_owner="worker"):
    lease, now = identity(), self.clock()
    with self.db() as db:
      db.execute("BEGIN IMMEDIATE")
      row = db.execute(
        "SELECT t.*,p.state AS project_state,p.runs_used,p.max_runs,p.deadline FROM tasks t JOIN projects p ON p.id=t.project_id WHERE t.id=?",
        (tid,),
      ).fetchone()
      if (
        not row
        or row["execution_owner"] != execution_owner
        or row["state"] != "queued"
        or row["project_state"] != "active"
        or self.goal_block_reason(db, row["goal_plan_id"])
        or row["not_before"] > now
        or row["runs_used"] >= row["max_runs"]
        or (row["attempts"] >= row["max_attempts"] and not row["continuation"])
        or (row["deadline"] and row["deadline"] <= now)
      ):
        return None
      if db.execute(
        "SELECT 1 FROM dependencies d JOIN tasks t ON t.id=d.dependency_id WHERE d.task_id=? AND t.state!='succeeded'",
        (tid,),
      ).fetchone():
        return None
      db.execute(
        "UPDATE tasks SET state='running',attempts=attempts+1-continuation,continuation=0,seen_action_version=action_version,lease=?,updated=? WHERE id=?",
        (lease, now, tid),
      )
      db.execute(
        "UPDATE projects SET runs_used=runs_used+1,updated=? WHERE id=?",
        (now, row["project_id"]),
      )
      self.event(db, row["project_id"], "task.started", {"id": tid})
      db.execute(
        "INSERT INTO runs(id,project_id,task_id,provider,state,started) VALUES(?,?,?,?,'running',?)",
        (lease, row["project_id"], tid, row["provider"], now),
      )
    self.notify()
    return lease

  def permitted(self, tid, lease):
    with self.db() as db:
      row = db.execute(
        "SELECT t.goal_plan_id FROM tasks t JOIN projects p ON p.id=t.project_id WHERE t.id=? AND t.lease=? AND t.state='running' AND p.state='active' AND (p.deadline IS NULL OR p.deadline>?)",
        (tid, lease, self.clock()),
      ).fetchone()
      return row is not None and not self.goal_block_reason(
        db, row["goal_plan_id"], check_budget=False
      )

  def finish(self, tid, lease, state, result=None, reason=None, receipt_version=None):
    if state not in {
      "succeeded",
      "failed",
      "unknown",
      "waiting_subscription",
      "blocked",
    }:
      raise Refused("Invalid completion state")
    if result is not None and len(result) > 200_000:
      raise Refused("Result too large")
    with self.db() as db:
      db.execute("BEGIN IMMEDIATE")
      row = db.execute(
        "SELECT * FROM tasks WHERE id=? AND lease=? AND state='running'",
        (tid, lease),
      ).fetchone()
      if not row:
        return False
      project = self.require_project(db, row["project_id"])
      external = row["execution_owner"] == "external"
      if external and state == "succeeded":
        if type(receipt_version) is not int or receipt_version != row["action_version"]:
          raise Refused("Read the current control task receipts before finishing")
        if db.execute(
          "SELECT 1 FROM actions WHERE task_id=? AND state IN ('pending','executing')",
          (tid,),
        ).fetchone():
          raise Refused("Wait for action review and receipts before finishing")
      elif external:
        db.execute(
          "UPDATE actions SET state='cancelled' WHERE task_id=? AND state='pending'",
          (tid,),
        )
      turn_state, turn_reason = state, reason
      continuation = 0
      actions = {
        r[0] for r in db.execute("SELECT state FROM actions WHERE task_id=?", (tid,))
      }
      if state == "succeeded":
        if "unknown" in actions:
          state, reason = "unknown", "external_outcome_unknown"
        elif actions & {"pending", "executing"}:
          state, reason = "waiting_approval", "awaiting_action_receipt"
        elif not external and row["action_version"] > row["seen_action_version"]:
          state, reason, continuation = (
            "queued",
            "action_receipt_available",
            1,
          )
        elif actions - {"succeeded"}:
          state, reason = "blocked", "required_action_not_completed"
      if project["state"] != "active" or (
        project["deadline"] is not None and project["deadline"] <= self.clock()
      ):
        state, reason = "unknown", "stopped_or_paused"
      if goal_reason := self.goal_block_reason(
        db, row["goal_plan_id"], check_budget=False
      ):
        state, reason = "unknown", goal_reason
      db.execute(
        "UPDATE tasks SET state=?,result=?,reason=?,continuation=?,lease=NULL,updated=? WHERE id=?",
        (state, result, reason, continuation, self.clock(), tid),
      )
      db.execute(
        "UPDATE runs SET state=?,result=?,reason=?,finished=? WHERE id=?",
        (turn_state, result, turn_reason, self.clock(), lease),
      )
      self.event(db, row["project_id"], "task.finished", {"id": tid, "state": state})
    self.notify()
    return True

  def add_evidence(
    self,
    pid,
    uri,
    excerpt,
    task_id=None,
    run_id=None,
    expires=None,
    access="public",
    confidence=None,
  ):
    from urllib.parse import urlsplit

    if access not in ACCESS:
      raise Refused("Access must be public, licensed or internal")
    if confidence is not None:
      integer(confidence, "confidence", 0, 100)
    if expires is not None and (
      type(expires) not in (int, float)
      or not math.isfinite(expires)
      or expires <= self.clock()
    ):
      raise Refused("A source must expire in the future")

    uri, excerpt = (
      bounded(uri, "source URI", 4096),
      bounded(excerpt, "excerpt", 50_000),
    )
    parsed = urlsplit(uri)
    if (
      parsed.scheme not in {"https", "http", "urn"}
      or parsed.username
      or parsed.password
    ):
      raise Refused("Use a public source URI or an artifact URN")
    eid, checksum = identity(), digest({"uri": uri, "excerpt": excerpt})
    with self.db() as db:
      db.execute("BEGIN IMMEDIATE")
      self.require_project(db, pid)
      if task_id:
        row = db.execute(
          "SELECT project_id FROM tasks WHERE id=?", (task_id,)
        ).fetchone()
        if not row or row[0] != pid:
          raise Refused("Task is outside this project")
      old = db.execute(
        "SELECT id FROM evidence WHERE project_id=? AND source_hash=?",
        (pid, checksum),
      ).fetchone()
      if old:
        return old[0]
      db.execute(
        "INSERT INTO evidence(id,project_id,task_id,uri,excerpt,source_hash,retrieved,run_id,expires,access,confidence) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
          eid,
          pid,
          task_id,
          uri,
          excerpt,
          checksum,
          self.clock(),
          run_id,
          expires,
          access,
          confidence,
        ),
      )
      self.event(db, pid, "evidence.added", {"id": eid})
    self.notify()
    return eid

  def add_claim(
    self,
    pid,
    statement,
    kind,
    supporting,
    contradicting=None,
    task_id=None,
    run_id=None,
    confidence=None,
    detail=None,
  ):
    statement = bounded(statement, "statement", 10_000)
    if kind not in {"claim", "hypothesis", "decision"}:
      raise Refused("Invalid claim kind")
    detail = encoded(shaped(detail, CLAIM_DETAIL[kind]))
    if confidence is not None:
      integer(confidence, "confidence", 0, 100)
    supporting, contradicting = supporting or [], contradicting or []
    if (
      not isinstance(supporting, list)
      or not isinstance(contradicting, list)
      or not supporting + contradicting
      or len(supporting + contradicting) > 50
      or any(not isinstance(x, str) for x in supporting + contradicting)
    ):
      raise Refused("Claims require source evidence")
    if set(supporting) & set(contradicting):
      raise Refused("The same evidence cannot both support and refute a claim")
    cid = identity()
    with self.db() as db:
      db.execute("BEGIN IMMEDIATE")
      self.require_project(db, pid)
      if (
        task_id
        and not db.execute(
          "SELECT 1 FROM tasks WHERE id=? AND project_id=?", (task_id, pid)
        ).fetchone()
      ):
        raise Refused("Task is outside this project")
      for eid in supporting + contradicting:
        self.usable_evidence(db, pid, eid)
      state = "contested" if contradicting else "unverified"
      db.execute(
        "INSERT INTO claims(id,project_id,statement,kind,state,created,task_id,run_id,confidence,detail) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
          cid,
          pid,
          statement,
          kind,
          state,
          self.clock(),
          task_id,
          run_id,
          confidence,
          detail,
        ),
      )
      self.claim_revision(
        db, cid, confidence, state, "recorded", task_id, run_id, bool(task_id)
      )
      db.executemany(
        "INSERT INTO claim_evidence VALUES(?,?,?)",
        [(cid, eid, "supports") for eid in set(supporting)]
        + [(cid, eid, "refutes") for eid in set(contradicting)],
      )
      self.event(db, pid, "claim.added", {"id": cid})
    self.notify()
    return cid

  def usable_evidence(self, db, pid, eid):
    """A source a new assessment may rest on: this project's, standing, unexpired."""
    row = db.execute(
      "SELECT project_id,state,expires FROM evidence WHERE id=?", (eid,)
    ).fetchone()
    if not row or row[0] != pid or row[1] != "active":
      raise Refused("Evidence is missing, withdrawn or outside this project")
    if row[2] is not None and row[2] <= self.clock():
      raise Refused("That source has expired; record it again from the original")

  def claim_revision(
    self, db, cid, confidence, state, reason, task_id=None, run_id=None, agent=False
  ):
    """Append one immutable line to a claim's history, inside the caller's transaction."""
    seq = db.execute(
      "SELECT coalesce(max(seq),0)+1 FROM claim_revisions WHERE claim_id=?", (cid,)
    ).fetchone()[0]
    db.execute(
      "INSERT INTO claim_revisions VALUES(?,?,?,?,?,?,?,?,?)",
      (
        cid,
        seq,
        confidence,
        state,
        reason,
        "agent" if agent else "owner",
        task_id,
        run_id,
        self.clock(),
      ),
    )

  def withdraw_evidence(self, pid, eid):
    with self.db() as db:
      db.execute("BEGIN IMMEDIATE")
      row = db.execute(
        "SELECT state FROM evidence WHERE id=? AND project_id=?", (eid, pid)
      ).fetchone()
      if not row:
        raise Refused("Evidence not found")
      if row["state"] == "withdrawn":
        return
      db.execute("UPDATE evidence SET state='withdrawn' WHERE id=?", (eid,))
      # Everything interpreted from a withdrawn source returns to review; the
      # records and their history stay.
      for claim in db.execute(
        "SELECT id,confidence FROM claims WHERE state!='retracted' AND id IN (SELECT claim_id FROM claim_evidence WHERE evidence_id=?)",
        (eid,),
      ).fetchall():
        db.execute("UPDATE claims SET state='needs_review' WHERE id=?", (claim["id"],))
        self.claim_revision(
          db, claim["id"], claim["confidence"], "needs_review", "evidence_withdrawn"
        )
      db.execute(
        """UPDATE memories SET state='needs_review',updated=? WHERE state='active' AND id IN (
             SELECT subject_id FROM links WHERE project_id=? AND subject_type='memory' AND object_type='evidence' AND object_id=?
             UNION SELECT object_id FROM links WHERE project_id=? AND object_type='memory' AND subject_type='evidence' AND subject_id=?)""",
        (self.clock(), pid, eid, pid, eid),
      )
      db.execute(
        "UPDATE links SET state='source_withdrawn' WHERE project_id=? AND state='active' AND ((object_type='evidence' AND object_id=?) OR (subject_type='evidence' AND subject_id=?))",
        (pid, eid, eid),
      )
      self.event(db, pid, "evidence.withdrawn", {"id": eid})
    self.notify()

  def snapshot(self, pid=None):
    with self.db() as db:
      # One read transaction: the revision must describe exactly the rows
      # returned, or a page that saw older rows would never ask for newer ones.
      db.execute("BEGIN")
      if pid:
        self.require_project(db, pid)
      out = {}
      for table in (
        "projects",
        "tasks",
        "evidence",
        "claims",
        "actions",
        "runs",
        "artifacts",
      ):
        clause = (
          " WHERE " + ("id" if table == "projects" else "project_id") + "=?"
          if pid
          else ""
        )
        out[table] = [
          dict(x)
          for x in db.execute(f"SELECT * FROM {table}{clause}", (pid,) if pid else ())
        ]
      claim_ids = {x["id"] for x in out["claims"]}
      out["claim_evidence"] = [
        dict(x)
        for x in db.execute("SELECT * FROM claim_evidence")
        if x["claim_id"] in claim_ids
      ]
      out["claim_revisions"] = [
        dict(x)
        for x in db.execute("SELECT * FROM claim_revisions ORDER BY claim_id,seq")
        if x["claim_id"] in claim_ids
      ]
      # Executive and procedural memory belong to the owner, not to one project,
      # so a project view carries them along with that project's own records.
      out["memories"] = [
        dict(x)
        for x in db.execute(
          "SELECT * FROM memories"
          + (" WHERE project_id=? OR scope!='project'" if pid else "")
          + " ORDER BY created",
          (pid,) if pid else (),
        )
      ]
      for table in (
        "entities",
        "entity_keys",
        "signals",
        "links",
        "records",
        "goal_plans",
      ):
        out[table] = [
          dict(x)
          for x in db.execute(
            f"SELECT * FROM {table}" + (" WHERE project_id=?" if pid else ""),
            (pid,) if pid else (),
          )
        ]
      out["revision"] = db.execute(
        "SELECT coalesce(max(seq),0) FROM events"
      ).fetchone()[0]
    for task in out["tasks"]:
      task.pop("lease", None)
      task.pop("request_hash", None)
    return out

  def events(self, after=0):
    with self.db() as db:
      return [
        dict(x) | {"payload": json.loads(x["payload"])}
        for x in db.execute(
          "SELECT * FROM events WHERE seq>? ORDER BY seq LIMIT 1000", (after,)
        )
      ]

  def task_actions(self, tid):
    with self.db() as db:
      rows = [
        dict(x)
        for x in db.execute(
          "SELECT id,tool,args,state,result,expires FROM actions WHERE task_id=? ORDER BY created",
          (tid,),
        )
      ]
    for row in rows:
      row["args"] = json.loads(row["args"])
      row["result"] = json.loads(row["result"]) if row["result"] else None
    return rows

  def _action_changed(self, db, tid):
    """Called inside the receipt transaction, including denial and expiry."""
    row = db.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
    if not row:
      return
    db.execute("UPDATE tasks SET action_version=action_version+1 WHERE id=?", (tid,))
    if row["state"] != "waiting_approval":
      return  # A running turn observes the new version in finish().
    states = {
      r[0] for r in db.execute("SELECT state FROM actions WHERE task_id=?", (tid,))
    }
    if states & {"pending", "executing"}:
      return
    state, reason = (
      ("unknown", "external_outcome_unknown")
      if "unknown" in states
      else ("queued", "action_receipt_available")
    )
    db.execute(
      "UPDATE tasks SET state=?,reason=?,continuation=?,updated=? WHERE id=?",
      (state, reason, int(state == "queued"), self.clock(), tid),
    )
    self.event(db, row["project_id"], "task.receipt_ready", {"id": tid, "state": state})

  def settle_action(self, aid, state, result=None):
    if state not in {"succeeded", "failed", "unknown"}:
      raise Refused("Invalid action result")
    with self.db() as db:
      db.execute("BEGIN IMMEDIATE")
      row = db.execute(
        "SELECT * FROM actions WHERE id=? AND state='executing'", (aid,)
      ).fetchone()
      if not row:
        raise Refused("Action is no longer executing")
      db.execute(
        "UPDATE actions SET state=?,result=? WHERE id=?",
        (state, encoded(result), aid),
      )
      self.event(db, row["project_id"], "action.finished", {"id": aid, "state": state})
      self._action_changed(db, row["task_id"])
    self.notify()

  def maintain(self):
    """Expire approvals and make exhausted queues visible without executing work."""
    changed = False
    with self.db() as db:
      db.execute("BEGIN IMMEDIATE")
      rows = db.execute(
        "SELECT * FROM actions WHERE state='pending' AND expires<=?",
        (self.clock(),),
      ).fetchall()
      for row in rows:
        changed = True
        db.execute("UPDATE actions SET state='expired' WHERE id=?", (row["id"],))
        self.event(db, row["project_id"], "approval.expired", {"id": row["id"]})
        self._action_changed(db, row["task_id"])
      tasks = db.execute(
        "SELECT t.id,t.project_id,t.goal_plan_id,p.runs_used,p.max_runs,p.deadline FROM tasks t JOIN projects p ON p.id=t.project_id WHERE t.state IN ('queued','waiting_approval') AND (t.goal_plan_id IS NOT NULL OR p.runs_used>=p.max_runs OR p.deadline<=?)",
        (self.clock(),),
      ).fetchall()
      for row in tasks:
        goal_reason = self.goal_block_reason(db, row["goal_plan_id"])
        if (
          not goal_reason
          and row["runs_used"] < row["max_runs"]
          and (row["deadline"] is None or row["deadline"] > self.clock())
        ):
          continue
        # Let an already requested action finish; only the continuation needs a run.
        if db.execute(
          "SELECT 1 FROM actions WHERE task_id=? AND state IN ('pending','executing')",
          (row["id"],),
        ).fetchone():
          continue
        reason = goal_reason or (
          "project_run_budget_exhausted"
          if row["runs_used"] >= row["max_runs"]
          else "project_deadline_passed"
        )
        db.execute(
          "UPDATE tasks SET state='blocked',reason=?,updated=? WHERE id=?",
          (reason, self.clock(), row["id"]),
        )
        changed = True
        self.event(
          db,
          row["project_id"],
          "task.blocked",
          {"id": row["id"], "reason": reason},
        )
    if changed:
      self.notify()

  def search_evidence(self, pid, query, limit=10):
    query = bounded(query, "search query", 1000)
    integer(limit, "limit", 1, 50)
    terms = list(dict.fromkeys(query.lower().split()))[:12]
    with self.db() as db:
      self.require_project(db, pid)
      # Local literal substring retrieval; wildcards and quotes have no special meaning.
      score = "+".join("(instr(lower(uri || ' ' || excerpt),?)>0)" for _ in terms)
      rows = db.execute(
        f"SELECT id,uri,state,source_hash,retrieved,substr(excerpt,1,1000) AS preview,({score}) AS matches FROM evidence WHERE project_id=? AND state='active' AND ({score})>0 ORDER BY matches DESC,retrieved DESC LIMIT ?",
        (*terms, pid, *terms, limit),
      ).fetchall()
      return {"untrusted": True, "results": [dict(r) for r in rows]}

  def get_evidence(self, pid, eid, offset=0, limit=12000):
    integer(offset, "offset", 0, 50000)
    integer(limit, "limit", 1, 20000)
    with self.db() as db:
      row = db.execute(
        "SELECT * FROM evidence WHERE id=? AND project_id=?", (eid, pid)
      ).fetchone()
    if not row:
      raise Refused("Evidence not found in this project")
    result = dict(row)
    excerpt = result["excerpt"]
    result.update(
      excerpt=excerpt[offset : offset + limit],
      offset=offset,
      total_length=len(excerpt),
      next_offset=offset + limit if offset + limit < len(excerpt) else None,
      untrusted=True,
    )
    return result

  def context(self, pid, limit=120_000):
    """Bound model context without changing the complete durable records."""
    snapshot = self.snapshot(pid)
    out = {
      "project": snapshot["projects"][0],
      "truncated": False,
      "retrieval": "Use search_evidence and get_evidence to read sources omitted or shortened here.",
    }
    budgets = {
      "tasks": 0.24,
      "evidence": 0.30,
      "claims": 0.16,
      "actions": 0.20,
      "claim_evidence": 0.08,
    }
    available = max(0, limit - len(encoded(out)) - 200)
    for kind, share in budgets.items():
      out[kind] = []
      used = 0
      for original in reversed(snapshot[kind]):
        item = dict(original)
        for field in ("result", "excerpt", "args", "instruction", "statement"):
          if isinstance(item.get(field), str) and len(item[field]) > 8000:
            item[field] = item[field][:8000]
            item[field + "_truncated"] = True
            out["truncated"] = True
        size = len(encoded(item)) + 1
        if used + size > available * share:
          out["truncated"] = True
          continue
        out[kind].append(item)
        used += size
    return out

  def next_wake(self):
    with self.db() as db:
      value = db.execute(
        "SELECT min(t.not_before) FROM tasks t JOIN projects p ON p.id=t.project_id WHERE t.state='queued' AND p.state='active' AND t.not_before>?",
        (self.clock(),),
      ).fetchone()[0]
      return None if value is None else max(0.1, value - self.clock())
