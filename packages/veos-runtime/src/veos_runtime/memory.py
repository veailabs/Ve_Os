"""The four memories of the runtime, kept in its one SQLite journal.

Executive memory is the owner's standing direction, project memory is what a
project decided and learned, evidence memory is the existing source and claim
ledger, and procedural memory is what was learned about doing the work.
Entities, signals and a typed link graph tie the records together.

Two rules shape every write. Whatever leaves one project (executive and
procedural memory reach every project's agents) is only proposed by an agent
and takes effect when the owner accepts it. And nothing is edited in place: a
revision is a new record that supersedes the old one, so every statement stays
attributable to the owner or to the task and run that wrote it.

A later feature joins this model without a schema change: add its table to
RECORDS to make its rows linkable and its kinds to KINDS to make them
storable, and keep whatever does not fit a column in a record's `detail`.
"""

import json
import math
import re
import unicodedata

from .providers import read_config
from .store import Refused, bounded, digest, encoded, identity, integer, shaped

KINDS = {
  "executive": (
    "strategy",
    "preference",
    "risk_appetite",
    "operating_rule",
    "priority",
  ),
  "project": ("decision", "outcome", "next_action", "timeline", "note", "correlation"),
  "procedural": ("workflow", "tool_performance", "retry_lesson", "failure_pattern"),
}
ENTITY_KINDS = ("organization", "person", "technology")
RIGHTS = ("public", "licensed", "internal")
# Record type -> table. Every table carries id and project_id.
RECORDS = {
  "entity": "entities",
  "evidence": "evidence",
  "claim": "claims",
  "memory": "memories",
  "signal": "signals",
  "artifact": "artifacts",
  "task": "tasks",
}  # The types in TYPES are linkable as well; they share the records table.
DECISION = {"options": ("list", 10), "rationale": ("text", 4000)}
# Typed project records. A new type is one entry here: its fields, the states
# it moves through (the first is where it starts) and whether an agent's entry
# waits for the owner.
TYPES = {
  "experiment": {
    "fields": {
      "protocol": ("text", 8000),
      "protocol_version": ("text", 80),
      "inputs": ("list", 50),
      "measurements": ("list", 100),
      "controls": ("list", 50),
      "failures": ("list", 50),
      "lab": ("text", 300),
      "ip_terms": ("text", 2000),
    },
    "required": ("protocol",),
    "states": ("planned", "running", "succeeded", "failed", "inconclusive"),
    "proposed": False,
  },
  "relationship": {
    "fields": {
      "parties": ("list", 20),
      "consent": ("enum", ("unknown", "granted", "declined", "withdrawn")),
      "meetings": ("list", 100),
      "crm_reference": ("text", 300),
      "follow_up": ("text", 1000),
      "follow_up_state": ("enum", ("none", "due", "done")),
    },
    "required": ("parties",),
    "states": ("active", "dormant", "closed"),
    "proposed": False,
  },
  "goal": {
    "fields": {
      "parent": ("text", 80),
      "priority": ("int", 1, 5),
      "success": ("list", 20),
      "constraints": ("object",),
      "resume_on": ("list", 20),
      "stop_if": ("list", 20),
    },
    "required": (),
    "states": ("active", "paused", "met", "stopped"),
    "proposed": True,
  },
}
RETIRED = ("superseded", "retracted", "rejected", "proposed")
RECALL = (
  "all",
  "executive",
  "project",
  "procedural",
  "claim",
  "entity",
  "signal",
  *TYPES,
)
WINDOW = 30 * 86400  # Signals this close together are read as one development.
READY_SOURCES = 2  # Distinct feeds naming an entity before it merits a project.
LIMITS = {
  "project_memories": 2000,
  "shared_memories": 1000,
  "proposals": 50,
  "entities": 5000,
  "signals": 20_000,
  "links": 20_000,
  "records": 5000,
}
INBOX_LIMIT = 5000
SLUG = re.compile(r"[a-z][a-z0-9_]{0,39}")
IDENTIFIER = re.compile(r"([A-Za-z][A-Za-z0-9_]{0,30}):(.+)", re.S)
POSSIBLE = "possible_same_as"


def normal(text):
  """Fold a name so spelling, case and punctuation variants share one key."""
  text = unicodedata.normalize("NFKC", text).casefold()
  return " ".join(re.sub(r"[\W_]+", " ", text).split())


def identifier_key(value):
  match = IDENTIFIER.fullmatch(value.strip())
  rest = " ".join(match[2].casefold().split()) if match else ""
  if not rest:
    raise Refused("Write an identifier as type:value, such as domain:example.com")
  return "i:" + match[1].lower() + ":" + rest


def name_key(value):
  key = normal(value)
  if not key:
    raise Refused("A name needs letters or digits")
  return "n:" + key


def strings(value, name, maximum, length=400):
  value = [] if value is None else value
  if (
    not isinstance(value, list)
    or len(value) > maximum
    or any(not isinstance(x, str) for x in value)
  ):
    raise Refused(f"Invalid {name}")
  return list(dict.fromkeys(bounded(x, name, length) for x in value))


def detailed(value):
  value = {} if value is None else value
  if not isinstance(value, dict):
    raise Refused("Detail must be an object")
  try:
    text = encoded(value)
  except (TypeError, ValueError):
    raise Refused("Detail must be plain JSON") from None
  if len(text) > 8000:
    raise Refused("Detail is too large")
  return text


def unpacked(row):
  item = dict(row)
  if "detail" in item:
    item["detail"] = json.loads(item["detail"])
  return item


class Memory:
  def __init__(self, store):
    self.store = store

  # ---------------------------------------------------------------- helpers

  def scoped_task(self, db, pid, task_id):
    if (
      task_id
      and not db.execute(
        "SELECT 1 FROM tasks WHERE id=? AND project_id=?", (task_id, pid)
      ).fetchone()
    ):
      raise Refused("Task is outside this project")

  def writable(self, db, pid, agent):
    project = self.store.require_project(db, pid)
    if agent and project["state"] != "active":
      raise Refused("Project is paused or closed")
    return project

  def room(self, db, table, pid, key, where="1=1"):
    count = db.execute(
      f"SELECT count(*) FROM {table} WHERE project_id=? AND {where}", (pid,)
    ).fetchone()[0]
    if count >= LIMITS[key]:
      raise Refused(f"This project reached its limit of {LIMITS[key]} {key}")

  def evidence_in(self, db, pid, ids):
    for eid in ids:
      self.store.usable_evidence(db, pid, eid)

  def add_link(self, db, pid, subject, relation, obj, confidence, agent, task, run):
    old = db.execute(
      "SELECT id FROM links WHERE project_id=? AND subject_type=? AND subject_id=? AND relation=? AND object_type=? AND object_id=?",
      (pid, *subject, relation, *obj),
    ).fetchone()
    if old:
      return old[0]
    self.room(db, "links", pid, "links")
    lid = identity()
    db.execute(
      "INSERT INTO links VALUES(?,?,?,?,?,?,?,?,'active',?,?,?,?)",
      (
        lid,
        pid,
        *subject,
        relation,
        *obj,
        confidence,
        "agent" if agent else "owner",
        task,
        run,
        self.store.clock(),
      ),
    )
    return lid

  # ------------------------------------------- executive, project, procedural

  def remember(
    self,
    scope,
    kind,
    statement,
    pid=None,
    detail=None,
    evidence=None,
    task_id=None,
    run_id=None,
    agent=False,
  ):
    if scope not in KINDS or kind not in KINDS[scope]:
      raise Refused("Unknown memory scope or kind")
    if kind == "correlation":
      raise Refused("Correlations are recorded from signals, not written")
    statement = bounded(statement, "statement", 2000)
    fields = (
      shaped(detail, DECISION) if kind == "decision" else json.loads(detailed(detail))
    )
    evidence = strings(evidence, "evidence", 20)
    if not pid and (scope == "project" or agent or evidence):
      raise Refused("This memory needs a project")
    # Project memory stays inside the project that wrote it. Anything that
    # every project will read waits for the owner, and so does a decision:
    # an agent recommends, the owner decides.
    state = (
      "proposed" if agent and (scope != "project" or kind == "decision") else "active"
    )
    mid, now = identity(), self.store.clock()
    with self.store.db() as db:
      db.execute("BEGIN IMMEDIATE")
      if pid:
        self.writable(db, pid, agent)
        self.scoped_task(db, pid, task_id)
        self.evidence_in(db, pid, evidence)
      old = db.execute(
        "SELECT id,state FROM memories WHERE scope=? AND statement=? AND state IN ('active','proposed') AND (scope!='project' OR project_id=?)",
        (scope, statement, pid),
      ).fetchone()
      if old:
        return {"id": old["id"], "state": old["state"]}
      if kind == "decision":
        # What the decision rested on, as it stood when it was taken.
        fields["evidence_snapshot"] = [
          dict(
            db.execute(
              "SELECT id,source_hash,retrieved FROM evidence WHERE id=?", (eid,)
            ).fetchone()
          )
          for eid in evidence
        ]
        if state == "active":
          fields |= {"approver": "owner", "approved": now}
      detail = detailed(fields)
      if state == "proposed":
        self.room(db, "memories", pid, "proposals", "state='proposed'")
      elif scope == "project":
        self.room(
          db, "memories", pid, "project_memories", "scope='project' AND state='active'"
        )
      elif (
        db.execute(
          "SELECT count(*) FROM memories WHERE scope!='project' AND state='active'"
        ).fetchone()[0]
        >= LIMITS["shared_memories"]
      ):
        raise Refused("Retract an executive or procedural memory first")
      db.execute(
        "INSERT INTO memories VALUES(?,?,?,?,?,?,?,?,?,?,NULL,?,?)",
        (
          mid,
          scope,
          kind,
          pid,
          statement,
          detail,
          state,
          "agent" if agent else "owner",
          task_id,
          run_id,
          now,
          now,
        ),
      )
      for eid in evidence:
        self.add_link(
          db,
          pid,
          ("memory", mid),
          "supported_by",
          ("evidence", eid),
          None,
          agent,
          task_id,
          run_id,
        )
      self.store.event(db, pid, "memory." + state, {"id": mid, "scope": scope})
    self.store.notify()
    return {"id": mid, "state": state}

  def decide(self, mid, command):
    """The owner's verdict on a proposal, a memory under review or a live one."""
    moves = {
      "accept": ({"proposed", "needs_review"}, "active"),
      "reject": ({"proposed"}, "rejected"),
      "retract": ({"proposed", "active", "needs_review"}, "retracted"),
    }
    if command not in moves:
      raise Refused("Unknown memory command")
    allowed, state = moves[command]
    with self.store.db() as db:
      db.execute("BEGIN IMMEDIATE")
      row = db.execute("SELECT * FROM memories WHERE id=?", (mid,)).fetchone()
      if not row:
        raise Refused("Memory not found")
      if row["state"] not in allowed:
        raise Refused("Memory cannot make that transition")
      now, detail = self.store.clock(), row["detail"]
      if command == "accept" and row["kind"] == "decision":
        detail = encoded(json.loads(detail) | {"approver": "owner", "approved": now})
      db.execute(
        "UPDATE memories SET state=?,detail=?,updated=? WHERE id=?",
        (state, detail, now, mid),
      )
      self.store.event(db, row["project_id"], "memory." + state, {"id": mid})
    self.store.notify()
    return {"id": mid, "state": state}

  def revise(
    self, mid, statement, detail=None, pid=None, task_id=None, run_id=None, agent=False
  ):
    """Supersede a live memory with a new version; the old text stays readable."""
    statement = bounded(statement, "statement", 2000)
    new, now = identity(), self.store.clock()
    with self.store.db() as db:
      db.execute("BEGIN IMMEDIATE")
      row = db.execute("SELECT * FROM memories WHERE id=?", (mid,)).fetchone()
      if not row or (agent and row["project_id"] != pid):
        raise Refused("Memory not found")
      if row["state"] not in {"active", "needs_review"}:
        raise Refused("Only a current memory can be revised")
      if agent:
        if row["scope"] != "project" or row["kind"] in {"decision", "correlation"}:
          raise Refused("Propose a new memory; only the owner revises this one")
        self.writable(db, pid, True)
        self.scoped_task(db, pid, task_id)
      db.execute(
        "INSERT INTO memories VALUES(?,?,?,?,?,?,'active',?,?,?,?,?,?)",
        (
          new,
          row["scope"],
          row["kind"],
          row["project_id"],
          statement,
          row["detail"]
          if detail is None
          else detailed(json.loads(row["detail"]) | shaped(detail, DECISION))
          if row["kind"] == "decision"
          else detailed(detail),
          "agent" if agent else "owner",
          task_id,
          run_id,
          mid,
          now,
          now,
        ),
      )
      db.execute(
        "UPDATE memories SET state='superseded',updated=? WHERE id=?", (now, mid)
      )
      if row["project_id"]:
        # The sources that still stand keep supporting the new wording.
        for link in db.execute(
          "SELECT * FROM links WHERE project_id=? AND subject_type='memory' AND subject_id=? AND state='active'",
          (row["project_id"], mid),
        ).fetchall():
          self.add_link(
            db,
            row["project_id"],
            ("memory", new),
            link["relation"],
            (link["object_type"], link["object_id"]),
            link["confidence"],
            agent,
            task_id,
            run_id,
          )
      self.store.event(
        db, row["project_id"], "memory.revised", {"id": new, "supersedes": mid}
      )
    self.store.notify()
    return {"id": new, "state": "active", "supersedes": mid}

  def versions(self, db, mid):
    """Every version of one memory, oldest first."""
    row = db.execute("SELECT * FROM memories WHERE id=?", (mid,)).fetchone()
    if not row:
      raise Refused("Memory not found")
    chain = [row]
    while chain[0]["supersedes"]:
      chain.insert(
        0,
        db.execute(
          "SELECT * FROM memories WHERE id=?", (chain[0]["supersedes"],)
        ).fetchone(),
      )
    while True:
      later = db.execute(
        "SELECT * FROM memories WHERE supersedes=?", (chain[-1]["id"],)
      ).fetchone()
      if not later:
        return chain
      chain.append(later)

  def history(self, mid):
    with self.store.db() as db:
      return [unpacked(row) for row in self.versions(db, mid)]

  def erase(self, mid):
    """Remove the text of a memory and of every version of it. Identifiers remain."""
    with self.store.db() as db:
      db.execute("BEGIN IMMEDIATE")
      chain = self.versions(db, mid)
      ids = [row["id"] for row in chain]
      marks = ",".join("?" * len(ids))
      db.execute(
        f"UPDATE memories SET statement='[erased]',detail='{{}}',state='erased',updated=? WHERE id IN ({marks})",
        (self.store.clock(), *ids),
      )
      db.execute(
        f"DELETE FROM links WHERE (subject_type='memory' AND subject_id IN ({marks})) OR (object_type='memory' AND object_id IN ({marks}))",
        (*ids, *ids),
      )
      self.store.event(db, chain[0]["project_id"], "memory.erased", {"ids": ids})
    self.store.notify()
    return {"erased": ids}

  def listing(self, pid=None, scope=None, state=None):
    clauses, values = [], []
    if pid:
      clauses.append("(project_id=? OR scope!='project')")
      values.append(pid)
    for column, value in (("scope", scope), ("state", state)):
      if value:
        clauses.append(column + "=?")
        values.append(value)
    with self.store.db() as db:
      return [
        unpacked(row)
        for row in db.execute(
          "SELECT * FROM memories"
          + (" WHERE " + " AND ".join(clauses) if clauses else "")
          + " ORDER BY created",
          values,
        )
      ]

  def context(self, pid, limit=16_000):
    """The live memory one project's agent starts with, newest first, bounded."""
    out = {"truncated": False}
    with self.store.db() as db:
      self.store.require_project(db, pid)
      for scope, share in (
        ("executive", 0.35),
        ("project", 0.35),
        ("procedural", 0.15),
      ):
        out[scope], used = [], 0
        for row in db.execute(
          "SELECT id,kind,statement,author,created FROM memories WHERE scope=? AND state='active' AND (scope!='project' OR project_id=?) ORDER BY created DESC, rowid DESC",
          (scope, pid),
        ):
          size = len(encoded(dict(row))) + 1
          if used + size > limit * share:
            out["truncated"] = True
            break
          out[scope].append(dict(row))
          used += size
      out["goals"], used = [], 0
      for row in db.execute(
        "SELECT id,title,detail,state FROM records WHERE project_id=? AND type='goal' AND state IN ('active','paused') ORDER BY created DESC, rowid DESC",
        (pid,),
      ):
        item = unpacked(row)
        size = len(encoded(item)) + 1
        if used + size > limit * 0.15:
          out["truncated"] = True
          break
        out["goals"].append(item)
        used += size
    return out

  def performance(self, pid=None):
    """Procedural facts the journal already holds: how providers and tools fared.

    Usage is what was really consumed: runs, attempts and seconds. Every
    provider here is a subscription client, so no run has a metered price.
    """
    where, values = (" AND project_id=?", (pid,)) if pid else ("", ())
    with self.store.db() as db:
      if pid:
        self.store.require_project(db, pid)
      return {
        "billing": read_config(self.store.root)["billing"],
        "metered_cost_usd": 0,
        "providers": [
          dict(row)
          for row in db.execute(
            f"SELECT provider,state,count(*) AS runs,round(avg(finished-started),1) AS average_seconds,round(sum(finished-started),1) AS total_seconds FROM runs WHERE finished IS NOT NULL{where} GROUP BY provider,state ORDER BY provider,state",
            values,
          )
        ],
        "tasks": [
          dict(row)
          for row in db.execute(
            f"SELECT provider,state,count(*) AS tasks,round(avg(attempts),2) AS average_attempts FROM tasks WHERE 1=1{where} GROUP BY provider,state ORDER BY provider,state",
            values,
          )
        ],
        "tools": [
          dict(row)
          for row in db.execute(
            f"SELECT tool,state,count(*) AS actions FROM actions WHERE 1=1{where} GROUP BY tool,state ORDER BY tool,state",
            values,
          )
        ],
      }

  # ------------------------------------------------------------------ claims

  def revise_claim(
    self,
    pid,
    cid,
    reason,
    confidence=None,
    supporting=None,
    contradicting=None,
    task_id=None,
    run_id=None,
    agent=False,
  ):
    """Add evidence to a claim or restate its confidence, as a new history line."""
    reason = bounded(reason, "reason", 1000)
    supporting = strings(supporting, "evidence", 50)
    contradicting = strings(contradicting, "evidence", 50)
    if set(supporting) & set(contradicting):
      raise Refused("The same evidence cannot both support and refute a claim")
    if confidence is not None:
      integer(confidence, "confidence", 0, 100)
    with self.store.db() as db:
      db.execute("BEGIN IMMEDIATE")
      self.writable(db, pid, agent)
      self.scoped_task(db, pid, task_id)
      claim = db.execute(
        "SELECT * FROM claims WHERE id=? AND project_id=?", (cid, pid)
      ).fetchone()
      if not claim or claim["state"] == "retracted":
        raise Refused("Claim not found in this project")
      self.evidence_in(db, pid, supporting + contradicting)
      linked = {
        row["evidence_id"]: row["relation"]
        for row in db.execute("SELECT * FROM claim_evidence WHERE claim_id=?", (cid,))
      }
      fresh = []
      for relation, ids in (("supports", supporting), ("refutes", contradicting)):
        for eid in ids:
          if linked.get(eid, relation) != relation:
            raise Refused("That evidence is already linked the other way")
          if eid not in linked:
            fresh.append((cid, eid, relation))
      previous = claim["confidence"]
      if any(relation == "refutes" for _, _, relation in fresh):
        if confidence is None:
          raise Refused("State the lowered confidence with contradicting evidence")
        if previous is not None and confidence > max(previous - 1, 0):
          raise Refused("Contradicting evidence must lower the confidence")
      if not fresh and confidence in {None, previous}:
        raise Refused("Nothing to revise")
      db.executemany("INSERT INTO claim_evidence VALUES(?,?,?)", fresh)
      relations = {
        row[0]
        for row in db.execute(
          "SELECT c.relation FROM claim_evidence c JOIN evidence e ON e.id=c.evidence_id WHERE c.claim_id=? AND e.state='active'",
          (cid,),
        )
      }
      state = (
        "contested"
        if "refutes" in relations
        else "unverified"
        if relations
        else "needs_review"
      )
      confidence = previous if confidence is None else confidence
      db.execute(
        "UPDATE claims SET state=?,confidence=? WHERE id=?", (state, confidence, cid)
      )
      self.store.claim_revision(
        db, cid, confidence, state, reason, task_id, run_id, agent
      )
      self.store.event(db, pid, "claim.revised", {"id": cid, "state": state})
    self.store.notify()
    return {"id": cid, "state": state, "confidence": confidence}

  def retract_claim(self, pid, cid, reason):
    reason = bounded(reason, "reason", 1000)
    with self.store.db() as db:
      db.execute("BEGIN IMMEDIATE")
      claim = db.execute(
        "SELECT * FROM claims WHERE id=? AND project_id=?", (cid, pid)
      ).fetchone()
      if not claim or claim["state"] == "retracted":
        raise Refused("Claim not found in this project")
      db.execute("UPDATE claims SET state='retracted' WHERE id=?", (cid,))
      self.store.claim_revision(db, cid, claim["confidence"], "retracted", reason)
      self.store.event(db, pid, "claim.retracted", {"id": cid})
    self.store.notify()
    return {"id": cid, "state": "retracted"}

  # ---------------------------------------------------------------- entities

  def keys(self, name, aliases, identifiers):
    found = {}
    for label, values in (("name", [name]), ("alias", aliases)):
      for value in values:
        found.setdefault(name_key(value), label)
    for value in identifiers:
      found[identifier_key(value)] = "identifier"
    return found

  def resolve_entity(
    self,
    pid,
    kind,
    name,
    aliases=None,
    identifiers=None,
    detail=None,
    task_id=None,
    run_id=None,
    agent=False,
  ):
    """Find the entity a mention refers to, or record a new one.

    A shared identifier settles a match. A shared name settles it for an
    organization or a technology, unless both carry different identifiers of
    one type; people share names too often for that. Whatever cannot be
    settled is recorded as ambiguous beside its candidates, for the owner.
    """
    if kind not in ENTITY_KINDS:
      raise Refused("Unknown entity kind")
    name = bounded(name, "name", 300)
    keys = self.keys(
      name,
      strings(aliases, "aliases", 20, 300),
      strings(identifiers, "identifiers", 20, 300),
    )
    fields, now = json.loads(detailed(detail)), self.store.clock()
    if kind == "person":
      # A person is recorded from public roles or with their consent, never
      # from private data, and the record says which.
      if fields.setdefault("visibility", "public") not in {"public", "consented"}:
        raise Refused("Record a person only from public roles or with consent")
    with self.store.db() as db:
      db.execute("BEGIN IMMEDIATE")
      self.writable(db, pid, agent)
      self.scoped_task(db, pid, task_id)

      def holders(prefix):
        chosen = [key for key in keys if key.startswith(prefix)]
        if not chosen:
          return []
        marks = ",".join("?" * len(chosen))
        return [
          row["entity_id"]
          for row in db.execute(
            f"SELECT DISTINCT k.entity_id FROM entity_keys k JOIN entities e ON e.id=k.entity_id WHERE k.project_id=? AND k.key IN ({marks}) AND (?='i:' OR e.kind=?) ORDER BY e.created,e.rowid",
            (pid, *chosen, prefix, kind),
          )
        ]

      def differs(eid):
        mine = {}
        for key in keys:
          if key.startswith("i:"):
            mine.setdefault(key.split(":")[1], set()).add(key)
        theirs = {}
        for row in db.execute(
          "SELECT key FROM entity_keys WHERE entity_id=? AND key LIKE 'i:%'", (eid,)
        ):
          theirs.setdefault(row["key"].split(":")[1], set()).add(row["key"])
        return any(not mine[t] & theirs[t] for t in mine.keys() & theirs.keys())

      by_identifier, by_name = holders("i:"), holders("n:")
      target, candidates, resolution = None, [], "new"
      if len(by_identifier) == 1:
        target, resolution = by_identifier[0], "identifier"
      elif by_identifier:
        candidates, resolution = by_identifier, "ambiguous"
      elif len(by_name) == 1 and kind != "person" and not differs(by_name[0]):
        target, resolution = by_name[0], "name"
      elif by_name:
        candidates, resolution = by_name, "ambiguous"
      if target:
        row = db.execute("SELECT * FROM entities WHERE id=?", (target,)).fetchone()
        if row["kind"] != kind:
          raise Refused(f"That identifier already belongs to a {row['kind']}")
        try:  # What is already recorded wins; a mention only fills gaps.
          merged = detailed(fields | json.loads(row["detail"]))
        except Refused:
          merged = row["detail"]
        db.execute(
          "UPDATE entities SET detail=?,updated=? WHERE id=?", (merged, now, target)
        )
        eid, state = target, row["state"]
      else:
        self.room(db, "entities", pid, "entities", "state!='merged'")
        eid, state = identity(), "ambiguous" if candidates else "active"
        db.execute(
          "INSERT INTO entities VALUES(?,?,?,?,?,?,NULL,?,?,?,?)",
          (eid, pid, kind, name, encoded(fields), state, task_id, run_id, now, now),
        )
        for other in candidates:
          self.add_link(
            db,
            pid,
            ("entity", eid),
            POSSIBLE,
            ("entity", other),
            None,
            agent,
            task_id,
            run_id,
          )
        self.store.event(db, pid, "entity." + state, {"id": eid})
      db.executemany(
        "INSERT OR IGNORE INTO entity_keys VALUES(?,?,?,?)",
        [(pid, key, eid, label) for key, label in keys.items()],
      )
    self.store.notify()
    return {
      "id": eid,
      "state": state,
      "resolution": resolution,
      "candidates": candidates,
    }

  def live_entity(self, db, pid, eid):
    row = db.execute(
      "SELECT * FROM entities WHERE id=? AND project_id=?", (eid, pid)
    ).fetchone()
    while row and row["merged_into"]:
      row = db.execute(
        "SELECT * FROM entities WHERE id=?", (row["merged_into"],)
      ).fetchone()
    if not row:
      raise Refused("Entity not found in this project")
    return row

  def merge_entities(self, pid, source, into):
    """The owner's ruling that two records are one entity; `into` survives."""
    with self.store.db() as db:
      db.execute("BEGIN IMMEDIATE")
      a, b = self.live_entity(db, pid, source), self.live_entity(db, pid, into)
      if a["id"] == b["id"]:
        raise Refused("Those are already the same entity")
      if a["kind"] != b["kind"]:
        raise Refused("Only entities of one kind can be merged")
      now = self.store.clock()
      # The ruling answers the source's open question; its other candidates
      # were candidates for the source, not for the survivor.
      db.execute(
        "DELETE FROM links WHERE project_id=? AND relation=? AND (subject_id=? OR object_id=?)",
        (pid, POSSIBLE, a["id"], a["id"]),
      )
      for side in ("subject", "object"):
        db.execute(
          f"UPDATE OR IGNORE links SET {side}_id=? WHERE project_id=? AND {side}_type='entity' AND {side}_id=?",
          (b["id"], pid, a["id"]),
        )
        # What the survivor already held would have collided; it is a duplicate.
        db.execute(
          f"DELETE FROM links WHERE project_id=? AND {side}_type='entity' AND {side}_id=?",
          (pid, a["id"]),
        )
      db.execute(
        "DELETE FROM links WHERE project_id=? AND subject_type='entity' AND object_type='entity' AND subject_id=object_id",
        (pid,),
      )
      db.execute(
        "UPDATE OR IGNORE entity_keys SET entity_id=? WHERE entity_id=?",
        (b["id"], a["id"]),
      )
      db.execute("DELETE FROM entity_keys WHERE entity_id=?", (a["id"],))
      db.execute(
        "UPDATE entities SET state='merged',merged_into=?,updated=? WHERE id=?",
        (b["id"], now, a["id"]),
      )
      db.execute(
        "UPDATE entities SET detail=?,updated=? WHERE id=?",
        (
          detailed(json.loads(a["detail"]) | json.loads(b["detail"])),
          now,
          b["id"],
        ),
      )
      self.settle(db, pid, b["id"])
      self.store.event(db, pid, "entity.merged", {"id": a["id"], "into": b["id"]})
    self.store.notify()
    return {"id": b["id"], "merged": a["id"]}

  def settle(self, db, pid, eid):
    """An ambiguous entity with no open candidate left is simply an entity."""
    if not db.execute(
      "SELECT 1 FROM links WHERE project_id=? AND relation=? AND (subject_id=? OR object_id=?)",
      (pid, POSSIBLE, eid, eid),
    ).fetchone():
      db.execute(
        "UPDATE entities SET state='active' WHERE id=? AND state='ambiguous'", (eid,)
      )

  def distinct_entity(self, pid, eid):
    """The owner's ruling that an ambiguous record is its own entity."""
    with self.store.db() as db:
      db.execute("BEGIN IMMEDIATE")
      row = self.live_entity(db, pid, eid)
      if row["state"] != "ambiguous":
        raise Refused("Entity is not awaiting review")
      db.execute(
        "DELETE FROM links WHERE project_id=? AND relation=? AND (subject_id=? OR object_id=?)",
        (pid, POSSIBLE, row["id"], row["id"]),
      )
      self.settle(db, pid, row["id"])
      self.store.event(db, pid, "entity.distinct", {"id": row["id"]})
    self.store.notify()
    return {"id": row["id"], "state": "active"}

  def entity(self, pid, eid):
    with self.store.db() as db:
      row = self.live_entity(db, pid, eid)
      item = unpacked(row)
      item["keys"] = [
        dict(key)
        for key in db.execute(
          "SELECT key,label FROM entity_keys WHERE entity_id=? ORDER BY label,key",
          (row["id"],),
        )
      ]
    item["links"] = self.neighbors(pid, "entity:" + row["id"])["links"]
    return item

  def projects_for(self, name=None, identifier=None):
    """Which open projects already follow this entity, so a signal joins one."""
    if not name and not identifier:
      raise Refused("Give a name or an identifier")
    # An identifier is the stronger key, so it decides when both are given.
    wanted = [
      identifier_key(bounded(identifier, "identifier", 300))
      if identifier
      else name_key(bounded(name, "name", 300))
    ]
    with self.store.db() as db:
      return [
        dict(row)
        for row in db.execute(
          f"SELECT DISTINCT p.id AS project_id,p.name AS project,e.id AS entity_id,e.kind,e.name FROM entity_keys k JOIN entities e ON e.id=k.entity_id JOIN projects p ON p.id=e.project_id WHERE k.key IN ({','.join('?' * len(wanted))}) AND p.state!='closed' ORDER BY p.created",
          wanted,
        )
      ]

  # ------------------------------------------------------------------ signals

  def record_signal(
    self,
    pid,
    source,
    content,
    uri=None,
    actor=None,
    rights="public",
    published=None,
    entities=None,
    task_id=None,
    run_id=None,
    agent=False,
    observed=None,
  ):
    """Keep one observation from a feed; the same content is kept once."""
    if not isinstance(source, str) or not SLUG.fullmatch(source):
      raise Refused("Name the source in lower case, such as patent or hiring")
    content = bounded(content, "content", 20_000)
    uri = None if uri is None else bounded(uri, "source URI", 4096)
    actor = None if actor is None else bounded(actor, "actor", 300)
    if rights not in RIGHTS:
      raise Refused("Rights must be public, licensed or internal")
    now = self.store.clock()
    if published is not None and (
      type(published) not in (int, float)
      or not math.isfinite(published)
      or not 0 <= published <= now + 86400
    ):
      raise Refused("Invalid publication time")
    entities = strings(entities, "entities", 20)
    checksum = digest({"source": source, "content": normal(content)})
    sid = identity()
    with self.store.db() as db:
      db.execute("BEGIN IMMEDIATE")
      self.writable(db, pid, agent)
      self.scoped_task(db, pid, task_id)
      old = db.execute(
        "SELECT id FROM signals WHERE project_id=? AND content_hash=?", (pid, checksum)
      ).fetchone()
      if old:
        sid, duplicate = old[0], True
      else:
        duplicate = False
        self.room(db, "signals", pid, "signals")
        db.execute(
          "INSERT INTO signals VALUES(?,?,?,?,?,?,?,?,?,?,'active',?,?)",
          (
            sid,
            pid,
            source,
            uri,
            actor,
            content,
            checksum,
            rights,
            now if observed is None else observed,
            published,
            task_id,
            run_id,
          ),
        )
        self.store.event(db, pid, "signal.recorded", {"id": sid})
        self.reawaken(db, pid, sid, source)
      # A repeat sighting may still name an entity the first one did not.
      for eid in entities:
        entity = self.live_entity(db, pid, eid)
        self.add_link(
          db,
          pid,
          ("signal", sid),
          "mentions",
          ("entity", entity["id"]),
          None,
          agent,
          task_id,
          run_id,
        )
        self.correlate(db, pid, entity)
    self.store.notify()
    return {"id": sid, "duplicate": duplicate}

  def correlate(self, db, pid, entity):
    """Note when different feeds name one entity inside the window.

    This is arithmetic over the records, not an inference: which feeds, which
    entity, how close together. What it means is left to a hypothesis.
    """
    now = self.store.clock()
    rows = db.execute(
      "SELECT s.id,s.source FROM links l JOIN signals s ON s.id=l.subject_id WHERE l.project_id=? AND l.subject_type='signal' AND l.relation='mentions' AND l.object_type='entity' AND l.object_id=? AND s.observed>=? AND s.state='active' ORDER BY s.observed",
      (pid, entity["id"], now - WINDOW),
    ).fetchall()
    sources = sorted({row["source"] for row in rows})
    if len(sources) < READY_SOURCES:
      return
    previous = next(
      (
        row
        for row in db.execute(
          "SELECT id,detail FROM memories WHERE project_id=? AND kind='correlation' AND state='active'",
          (pid,),
        ).fetchall()
        if json.loads(row["detail"]).get("entity") == entity["id"]
      ),
      None,
    )
    if previous and json.loads(previous["detail"])["sources"] == sources:
      return
    mid = identity()
    db.execute(
      "INSERT INTO memories VALUES(?,'project','correlation',?,?,?,'active','system',NULL,NULL,?,?,?)",
      (
        mid,
        pid,
        f"Signals from {', '.join(sources)} name {entity['name']} within {WINDOW // 86400} days.",
        encoded(
          {
            "entity": entity["id"],
            "sources": sources,
            "signals": [row["id"] for row in rows][-50:],
          }
        ),
        previous["id"] if previous else None,
        now,
        now,
      ),
    )
    if previous:
      db.execute(
        "UPDATE memories SET state='superseded',updated=? WHERE id=?",
        (now, previous["id"]),
      )
    self.store.event(db, pid, "signal.correlated", {"id": mid, "sources": sources})

  def reawaken(self, db, pid, sid, source):
    """Mark the goals that asked to hear about this kind of signal."""
    for goal in db.execute(
      "SELECT id,detail FROM records WHERE project_id=? AND type='goal' AND state IN ('active','paused')",
      (pid,),
    ).fetchall():
      if {source, "new_" + source} & set(
        json.loads(goal["detail"]).get("resume_on", [])
      ):
        self.add_link(
          db,
          pid,
          ("signal", sid),
          "reawakens",
          ("goal", goal["id"]),
          None,
          False,
          None,
          None,
        )
        self.store.event(db, pid, "goal.reawakened", {"id": goal["id"], "signal": sid})

  # -------------------------------------------------------------------- links

  def reference(self, db, pid, text):
    kind, _, rid = text.partition(":") if isinstance(text, str) else ("", "", "")
    if kind not in {*RECORDS, *TYPES} or not rid:
      raise Refused("Write a record as type:id, such as entity:" + identity())
    if kind == "entity":
      return kind, self.live_entity(db, pid, rid)["id"]
    if kind in TYPES:
      if not db.execute(
        "SELECT 1 FROM records WHERE id=? AND project_id=? AND type=?", (rid, pid, kind)
      ).fetchone():
        raise Refused(f"That {kind} is not in this project")
      return kind, rid
    if not db.execute(
      f"SELECT 1 FROM {RECORDS[kind]} WHERE id=? AND project_id=?", (rid, pid)
    ).fetchone():
      raise Refused(f"That {kind} is not in this project")
    return kind, rid

  def link(
    self,
    pid,
    subject,
    relation,
    obj,
    confidence=None,
    task_id=None,
    run_id=None,
    agent=False,
  ):
    """Relate two records of one project, such as person employed_by organization."""
    if (
      not isinstance(relation, str)
      or not SLUG.fullmatch(relation)
      or relation == POSSIBLE
    ):
      raise Refused("Name the relation in lower case, such as employed_by")
    if confidence is not None:
      integer(confidence, "confidence", 0, 100)
    with self.store.db() as db:
      db.execute("BEGIN IMMEDIATE")
      self.writable(db, pid, agent)
      self.scoped_task(db, pid, task_id)
      first, second = (
        self.reference(db, pid, subject),
        self.reference(db, pid, obj),
      )
      if first == second:
        raise Refused("A record cannot be linked to itself")
      lid = self.add_link(
        db, pid, first, relation, second, confidence, agent, task_id, run_id
      )
      self.store.event(db, pid, "link.added", {"id": lid})
    self.store.notify()
    return {"id": lid}

  def neighbors(self, pid, reference, limit=50):
    """One step of the relationship graph around a record."""
    integer(limit, "limit", 1, 200)
    with self.store.db() as db:
      self.store.require_project(db, pid)
      kind, rid = self.reference(db, pid, reference)
      rows = db.execute(
        "SELECT id,subject_type,subject_id,relation,object_type,object_id,confidence,state,created FROM links WHERE project_id=? AND state='active' AND ((subject_type=? AND subject_id=?) OR (object_type=? AND object_id=?)) ORDER BY created DESC LIMIT ?",
        (pid, kind, rid, kind, rid, limit),
      ).fetchall()
    return {"record": f"{kind}:{rid}", "links": [dict(row) for row in rows]}

  # ------------------------------------- experiments, relationships and goals

  def save_record(
    self,
    pid,
    kind,
    title,
    detail=None,
    state=None,
    revises=None,
    task_id=None,
    run_id=None,
    agent=False,
  ):
    """Record an experiment, relationship or goal, or a new version of one.

    A revision supersedes the earlier version and never replaces it, so a
    failed experiment and the protocol it ran under stay on the record.
    """
    if kind not in TYPES:
      raise Refused("Unknown record type")
    contract = TYPES[kind]
    title = bounded(title, "title", 300)
    fields = shaped(detail, contract["fields"], contract["required"])
    if state is not None and state not in contract["states"]:
      raise Refused("State must be one of: " + ", ".join(contract["states"]))
    rid, now = identity(), self.store.clock()
    with self.store.db() as db:
      db.execute("BEGIN IMMEDIATE")
      self.writable(db, pid, agent)
      self.scoped_task(db, pid, task_id)
      old = None
      if revises:
        old = db.execute(
          "SELECT * FROM records WHERE id=? AND project_id=? AND type=?",
          (revises, pid, kind),
        ).fetchone()
        if not old or old["state"] in RETIRED:
          raise Refused(f"That {kind} is not a current record of this project")
        if agent and contract["proposed"]:
          raise Refused("Propose a new one; only the owner revises this")
      parties = []
      for eid in fields.get("parties", []):
        parties.append(self.live_entity(db, pid, eid)["id"])
      if "parties" in fields:
        if not parties:
          raise Refused("A relationship needs at least one party")
        fields["parties"] = parties
      parent = fields.get("parent")
      if parent and (
        parent == revises
        or not db.execute(
          f"SELECT 1 FROM records WHERE id=? AND project_id=? AND type='goal' AND state NOT IN {RETIRED}",
          (parent, pid),
        ).fetchone()
      ):
        raise Refused("The parent must be a current goal of this project")
      for source in fields.get("resume_on", []):
        if not SLUG.fullmatch(source):
          raise Refused("Name each resume_on signal in lower case, such as new_patent")
      if not old:
        self.room(db, "records", pid, "records", f"state NOT IN {RETIRED}")
      new_state = (
        "proposed"
        if agent and contract["proposed"]
        else state or (old["state"] if old else contract["states"][0])
      )
      if new_state == "proposed":
        self.room(db, "records", pid, "proposals", "state='proposed'")
      db.execute(
        "INSERT INTO records VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (
          rid,
          pid,
          kind,
          title,
          detailed(fields),
          new_state,
          "agent" if agent else "owner",
          task_id,
          run_id,
          revises,
          now,
          now,
        ),
      )
      if old:
        db.execute(
          "UPDATE records SET state='superseded',updated=? WHERE id=?", (now, revises)
        )
        # Links follow the record to its current version, except the two this
        # version restates from its own fields.
        for side in ("subject", "object"):
          db.execute(
            f"UPDATE OR IGNORE links SET {side}_id=? WHERE project_id=? AND {side}_type=? AND {side}_id=? AND relation NOT IN ('party_to','part_of')",
            (rid, pid, kind, revises),
          )
      author = (agent, task_id, run_id)
      for eid in parties:
        self.add_link(db, pid, ("entity", eid), "party_to", (kind, rid), None, *author)
      if parent:
        self.add_link(db, pid, (kind, rid), "part_of", ("goal", parent), None, *author)
      self.store.event(db, pid, kind + "." + new_state, {"id": rid})
    self.store.notify()
    return {"id": rid, "state": new_state} | ({"supersedes": revises} if old else {})

  def decide_record(self, pid, rid, command):
    """The owner's verdict on a proposed goal, or the retraction of any record."""
    with self.store.db() as db:
      db.execute("BEGIN IMMEDIATE")
      row = db.execute(
        "SELECT * FROM records WHERE id=? AND project_id=?", (rid, pid)
      ).fetchone()
      if not row:
        raise Refused("Record not found in this project")
      state = {
        "accept": TYPES[row["type"]]["states"][0],
        "reject": "rejected",
        "retract": "retracted",
      }.get(command)
      if (
        not state
        or (command != "retract" and row["state"] != "proposed")
        or (command == "retract" and row["state"] in RETIRED[:3])
      ):
        raise Refused("Record cannot make that transition")
      db.execute(
        "UPDATE records SET state=?,updated=? WHERE id=?",
        (state, self.store.clock(), rid),
      )
      self.store.event(db, pid, row["type"] + "." + state, {"id": rid})
    self.store.notify()
    return {"id": rid, "state": state}

  def record(self, pid, rid):
    """One record with every version of it, oldest first."""
    with self.store.db() as db:
      row = db.execute(
        "SELECT * FROM records WHERE id=? AND project_id=?", (rid, pid)
      ).fetchone()
      if not row:
        raise Refused("Record not found in this project")
      chain = [row]
      while chain[0]["supersedes"]:
        chain.insert(
          0,
          db.execute(
            "SELECT * FROM records WHERE id=?", (chain[0]["supersedes"],)
          ).fetchone(),
        )
      while later := db.execute(
        "SELECT * FROM records WHERE supersedes=?", (chain[-1]["id"],)
      ).fetchone():
        chain.append(later)
    return {"versions": [unpacked(item) for item in chain]}

  # -------------------------------------------------------- signal routing

  def mention(self, item):
    """One entity named by a feed item, checked like any other entity."""
    if not isinstance(item, dict) or set(item) - {
      "kind",
      "name",
      "aliases",
      "identifiers",
    }:
      raise Refused("Describe each entity with kind, name, aliases and identifiers")
    if item.get("kind") not in ENTITY_KINDS:
      raise Refused("Unknown entity kind")
    out = {
      "kind": item["kind"],
      "name": bounded(item.get("name"), "name", 300),
      "aliases": strings(item.get("aliases"), "aliases", 20, 300),
      "identifiers": strings(item.get("identifiers"), "identifiers", 20, 300),
    }
    keys = self.keys(out["name"], out["aliases"], out["identifiers"])
    # A person is followed by identifier only; a shared name is not a match.
    out["keys"] = sorted(
      key for key in keys if key.startswith("i:") or out["kind"] != "person"
    )
    return out

  def followers(self, db, mention):
    if not mention["keys"]:
      return []
    marks = ",".join("?" * len(mention["keys"]))
    return [
      row[0]
      for row in db.execute(
        f"SELECT DISTINCT p.id FROM entity_keys k JOIN entities e ON e.id=k.entity_id JOIN projects p ON p.id=e.project_id WHERE k.key IN ({marks}) AND (k.key LIKE 'i:%' OR e.kind=?) AND p.state!='closed' ORDER BY p.created,p.rowid",
        (*mention["keys"], mention["kind"]),
      )
    ]

  def ingest_signal(
    self,
    source,
    content,
    entities,
    uri=None,
    actor=None,
    rights="public",
    published=None,
  ):
    """Route one feed item to the projects that already follow what it names.

    A signal about an entity some open project follows is recorded there, so
    a patent, a paper and a job listing about one company update one project.
    One that no project follows is held, and becomes ready for the owner to
    open a project once enough different feeds name the same entity.
    """
    if not isinstance(entities, list) or not 1 <= len(entities) <= 20:
      raise Refused("Name between 1 and 20 entities the signal is about")
    mentions = [self.mention(item) for item in entities]
    with self.store.db() as db:
      targets = {}
      for mention in mentions:
        for pid in self.followers(db, mention):
          targets.setdefault(pid, []).append(mention)
    attached = []
    for pid, named in targets.items():
      attached.append(
        {"project_id": pid}
        | self.attach(pid, source, content, named, uri, actor, rights, published)
      )
    if attached:
      return {"attached": attached, "held": None, "ready": False}
    # Validate as a signal before holding it, so the inbox never keeps what a
    # project could not record.
    if not isinstance(source, str) or not SLUG.fullmatch(source):
      raise Refused("Name the source in lower case, such as patent or hiring")
    content = bounded(content, "content", 20_000)
    uri = None if uri is None else bounded(uri, "source URI", 4096)
    actor = None if actor is None else bounded(actor, "actor", 300)
    if rights not in RIGHTS:
      raise Refused("Rights must be public, licensed or internal")
    now = self.store.clock()
    if published is not None and (
      type(published) not in (int, float)
      or not math.isfinite(published)
      or not 0 <= published <= now + 86400
    ):
      raise Refused("Invalid publication time")
    checksum = digest({"source": source, "content": normal(content)})
    with self.store.db() as db:
      db.execute("BEGIN IMMEDIATE")
      old = db.execute(
        "SELECT id FROM inbox WHERE content_hash=?", (checksum,)
      ).fetchone()
      if old:
        hid = old[0]
      else:
        if (
          db.execute("SELECT count(*) FROM inbox WHERE state='held'").fetchone()[0]
          >= INBOX_LIMIT
        ):
          raise Refused("Dismiss or promote held signals first")
        hid = identity()
        db.execute(
          "INSERT INTO inbox VALUES(?,?,?,?,?,?,?,?,?,?,'held',NULL)",
          (
            hid,
            source,
            uri,
            actor,
            content,
            checksum,
            rights,
            now,
            published,
            encoded(mentions),
          ),
        )
        self.store.event(db, None, "signal.held", {"id": hid})
      ready = hid in {item["id"] for item in self.held(db) if item["ready"]}
    self.store.notify()
    return {"attached": [], "held": hid, "ready": ready}

  def attach(
    self, pid, source, content, mentions, uri, actor, rights, published, observed=None
  ):
    ids = [
      self.resolve_entity(pid, m["kind"], m["name"], m["aliases"], m["identifiers"])[
        "id"
      ]
      for m in mentions
    ]
    return self.record_signal(
      pid, source, content, uri, actor, rights, published, ids, observed=observed
    )

  def held(self, db):
    items = [
      dict(row) | {"mentions": json.loads(row["mentions"])}
      for row in db.execute("SELECT * FROM inbox WHERE state='held' ORDER BY observed")
    ]
    for item in items:
      keys = {key for mention in item["mentions"] for key in mention["keys"]}
      related = [
        other
        for other in items
        if keys & {key for mention in other["mentions"] for key in mention["keys"]}
      ]
      item["sources"] = sorted({other["source"] for other in related})
      item["related"] = [other["id"] for other in related if other["id"] != item["id"]]
      item["ready"] = len(item["sources"]) >= READY_SOURCES
    return items

  def inbox(self):
    """Signals no project follows yet, with the ones that have built a case."""
    with self.store.db() as db:
      return {"signals": self.held(db)}

  def promote(self, ids, name, objective, max_runs=20):
    """Open a project for held signals; the owner's decision, never automatic."""
    ids = strings(ids, "signals", 200)
    if not ids:
      raise Refused("Choose the held signals this project starts from")
    with self.store.db() as db:
      held = {item["id"]: item for item in self.held(db)}
    if set(ids) - set(held):
      raise Refused("Those signals are not waiting in the inbox")
    pid = self.store.create_project(name, objective, max_runs)
    for hid in ids:
      item = held[hid]
      self.attach(
        pid,
        item["source"],
        item["content"],
        item["mentions"],
        item["uri"],
        item["actor"],
        item["rights"],
        item["published"],
        item["observed"],
      )
      with self.store.db() as db:
        db.execute(
          "UPDATE inbox SET state='attached',project_id=? WHERE id=?", (pid, hid)
        )
    self.store.notify()
    return {"project_id": pid, "signals": len(ids)}

  def dismiss(self, hid):
    with self.store.db() as db:
      done = db.execute(
        "UPDATE inbox SET state='dismissed' WHERE id=? AND state='held'", (hid,)
      ).rowcount
      if not done:
        raise Refused("That signal is not waiting in the inbox")
      self.store.event(db, None, "signal.dismissed", {"id": hid})
    self.store.notify()
    return {"id": hid, "state": "dismissed"}

  # ---------------------------------------------------------------- retrieval

  def recall(self, pid, query=None, scope="all", limit=20):
    """Search what this project may read: its own records and the owner's memory."""
    if scope not in RECALL:
      raise Refused("Unknown memory scope")
    integer(limit, "limit", 1, 50)
    terms = (
      list(dict.fromkeys(bounded(query, "search query", 1000).lower().split()))[:12]
      if query is not None
      else []
    )
    sources = {
      "executive": (
        "memory",
        "memories",
        "kind",
        "statement",
        "statement",
        "created",
        "scope='executive' AND state='active'",
        False,
      ),
      "procedural": (
        "memory",
        "memories",
        "kind",
        "statement",
        "statement",
        "created",
        "scope='procedural' AND state='active'",
        False,
      ),
      "project": (
        "memory",
        "memories",
        "kind",
        "statement",
        "statement",
        "created",
        "scope='project' AND state='active'",
        True,
      ),
      "claim": (
        "claim",
        "claims",
        "kind",
        "statement",
        "statement",
        "created",
        "state!='retracted'",
        True,
      ),
      "entity": (
        "entity",
        "entities",
        "kind",
        "name",
        "name || ' ' || coalesce((SELECT group_concat(substr(key,3),' ') FROM entity_keys WHERE entity_id=entities.id),'')",
        "created",
        "state IN ('active','ambiguous')",
        True,
      ),
      "signal": (
        "signal",
        "signals",
        "source",
        "content",
        "content || ' ' || coalesce(actor,'') || ' ' || coalesce(uri,'')",
        "observed",
        "state='active'",
        True,
      ),
    }
    for name in TYPES:
      sources[name] = (
        name,
        "records",
        "type",
        "title",
        "title || ' ' || detail",
        "created",
        f"type='{name}' AND state NOT IN {RETIRED}",
        True,
      )
    results = []
    with self.store.db() as db:
      self.store.require_project(db, pid)
      for name, (kind, table, label, shown, text, when, where, own) in sources.items():
        if scope not in {"all", name}:
          continue
        # Literal substring retrieval, as for evidence: no wildcard has meaning.
        score = "+".join(f"(instr(lower({text}),?)>0)" for _ in terms) or "0"
        extra = ",confidence" if table == "claims" else ""
        rows = db.execute(
          f"SELECT id,{label} AS kind,state,{when} AS created,substr({shown},1,600) AS text,({score}) AS matches{extra} FROM {table} WHERE {where}"
          + (" AND project_id=?" if own else "")
          + (f" AND ({score})>0" if terms else "")
          + " ORDER BY matches DESC,created DESC LIMIT ?",
          (*terms, *((pid,) if own else ()), *terms, limit),
        )
        results += [
          {"type": kind, "memory": name} | dict(row)
          if kind == "memory"
          else {"type": kind} | dict(row)
          for row in rows
        ]
    results.sort(key=lambda item: (-item["matches"], -item["created"]))
    out = {"untrusted": True, "results": results[:limit]}
    if scope == "procedural":
      out["performance"] = self.performance()
    return out

  def brief(self, pid):
    """What an executive brief must rest on, each claim traced to its sources."""
    now = self.store.clock()
    with self.store.db() as db:
      project = dict(self.store.require_project(db, pid))
      claims = []
      for row in db.execute(
        "SELECT * FROM claims WHERE project_id=? AND state!='retracted' ORDER BY created DESC LIMIT 100",
        (pid,),
      ).fetchall():
        sources = [
          dict(source)
          | {"expired": source["expires"] is not None and source["expires"] <= now}
          for source in db.execute(
            "SELECT e.id,e.uri,e.retrieved,e.state,e.expires,e.access,c.relation FROM claim_evidence c JOIN evidence e ON e.id=c.evidence_id WHERE c.claim_id=? ORDER BY e.retrieved",
            (row["id"],),
          )
        ]
        history = [
          dict(line)
          for line in db.execute(
            "SELECT seq,confidence,state,reason,author,created FROM claim_revisions WHERE claim_id=? ORDER BY seq",
            (row["id"],),
          )
        ]
        scores = [
          line["confidence"] for line in history if line["confidence"] is not None
        ]
        claims.append(
          {
            "id": row["id"],
            "kind": row["kind"],
            "statement": row["statement"],
            "state": row["state"],
            "confidence": row["confidence"],
            # Nothing here is verified fact: a claim is at best unverified.
            "uncertain": True,
            "detail": json.loads(row["detail"]),
            "traceable": any(
              source["state"] == "active" and not source["expired"]
              for source in sources
            ),
            "confidence_lowered": len(scores) > 1 and scores[-1] < scores[-2],
            "evidence": sources,
            "history": history,
          }
        )
      memory = {
        kind: [
          dict(row)
          for row in db.execute(
            "SELECT id,statement,detail,author,created FROM memories WHERE project_id=? AND scope='project' AND kind=? AND state='active' ORDER BY created DESC LIMIT 50",
            (pid, kind),
          )
        ]
        for kind in ("decision", "next_action", "outcome", "correlation")
      }
      for items in memory.values():
        for item in items:
          item["detail"] = json.loads(item["detail"])
      records = {
        kind: [
          unpacked(row)
          for row in db.execute(
            f"SELECT id,title,detail,state,author,created FROM records WHERE project_id=? AND type=? AND state NOT IN {RETIRED} ORDER BY created DESC LIMIT 50",
            (pid, kind),
          )
        ]
        for kind in TYPES
      }
      for goal in records["goal"]:
        goal["new_signals"] = [
          row[0]
          for row in db.execute(
            "SELECT subject_id FROM links WHERE project_id=? AND relation='reawakens' AND object_id=? ORDER BY created DESC LIMIT 20",
            (pid, goal["id"]),
          )
        ]

      def count(sql):
        return db.execute(sql, (pid,)).fetchone()[0]

      pending = {
        "memory_proposals": count(
          "SELECT count(*) FROM memories WHERE project_id=? AND state='proposed'"
        ),
        "memories_needing_review": count(
          "SELECT count(*) FROM memories WHERE project_id=? AND state='needs_review'"
        ),
        "ambiguous_entities": count(
          "SELECT count(*) FROM entities WHERE project_id=? AND state='ambiguous'"
        ),
        "approvals": count(
          "SELECT count(*) FROM actions WHERE project_id=? AND state='pending'"
        ),
        "proposed_tasks": count(
          "SELECT count(*) FROM tasks WHERE project_id=? AND state='proposed'"
        ),
        "proposed_goals": count(
          "SELECT count(*) FROM records WHERE project_id=? AND type='goal' AND state='proposed'"
        ),
      }
    return {
      "project": {
        key: project[key]
        for key in ("id", "name", "objective", "state", "runs_used", "max_runs")
      },
      "direction": self.context(pid)["executive"],
      "decisions": memory["decision"],
      "next_actions": memory["next_action"],
      "outcomes": memory["outcome"],
      "correlations": memory["correlation"],
      "goals": records["goal"],
      "experiments": records["experiment"],
      # A failed or inconclusive experiment is a result, listed with the rest.
      "negative_results": [
        item["id"]
        for item in records["experiment"]
        if item["state"] in {"failed", "inconclusive"}
      ],
      "relationships": records["relationship"],
      "follow_ups_due": [
        item["id"]
        for item in records["relationship"]
        if item["detail"].get("follow_up_state") == "due"
      ],
      "do_not_contact": [
        item["id"]
        for item in records["relationship"]
        if item["detail"].get("consent") in {"declined", "withdrawn"}
      ],
      "claims": claims,
      "changed": [claim["id"] for claim in claims if claim["confidence_lowered"]],
      "untraced": [claim["id"] for claim in claims if not claim["traceable"]],
      "pending": pending,
    }
