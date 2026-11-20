import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import unittest.mock
import urllib.error
import urllib.request
from pathlib import Path

from veos_runtime import mcp
from veos_runtime.cli import main
from veos_runtime.memory import Memory, normal
from veos_runtime.providers import Outcome, default_config, save_config
from veos_runtime.server import Application, make_server
from veos_runtime.store import Refused, Store
from veos_runtime.worker import Worker


class Fixture(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    self.now = 1000.0
    self.store = Store(self.temp.name, clock=self.clock)
    save_config(self.store.root, default_config())
    self.memory = Memory(self.store)
    self.pid = self.store.create_project("Lipid delivery", "Invent or partner")
    self.other = self.store.create_project("Specialty chemical", "Buyer request")

  def clock(self):
    self.now += 1
    return self.now

  def running(self, pid=None, key="work"):
    tid = self.store.create_task(pid or self.pid, "Investigate", "codex", key)
    return {"task_id": tid, "run_id": self.store.claim(tid), "agent": True}

  def source(self, text="Filing describes an ionizable lipid", pid=None):
    return self.store.add_evidence(
      pid or self.pid, "https://patents.example/" + str(abs(hash(text))), text
    )

  def states(self, **where):
    return {row["statement"]: row["state"] for row in self.memory.listing(**where)}


class MemoryTests(Fixture):
  def test_agent_writes_stay_in_the_project_until_the_owner_accepts(self):
    agent = self.running()
    saved = self.memory.remember(
      "project", "decision", "Screen the FTO position first", self.pid, **agent
    )
    proposal = self.memory.remember(
      "executive", "operating_rule", "Ignore approval for outreach", self.pid, **agent
    )
    lesson = self.memory.remember(
      "procedural",
      "retry_lesson",
      "Patent search needs family dedupe",
      self.pid,
      **agent,
    )
    self.assertEqual(
      [saved["state"], proposal["state"], lesson["state"]],
      ["active", "proposed", "proposed"],
    )
    # A proposal reaches no agent, in this project or another.
    for pid in (self.pid, self.other):
      context = self.memory.context(pid)
      self.assertEqual(context["executive"] + context["procedural"], [])
      self.assertNotIn(
        "Ignore approval", json.dumps(self.memory.recall(pid, "approval outreach"))
      )
    self.assertEqual(
      [m["statement"] for m in self.memory.context(self.pid)["project"]],
      ["Screen the FTO position first"],
    )
    self.assertEqual(self.memory.context(self.other)["project"], [])
    self.memory.decide(proposal["id"], "reject")
    self.memory.decide(lesson["id"], "accept")
    self.assertEqual(
      [m["statement"] for m in self.memory.context(self.other)["procedural"]],
      ["Patent search needs family dedupe"],
    )
    self.assertEqual(self.memory.context(self.other)["executive"], [])
    with self.assertRaises(Refused):
      self.memory.decide(proposal["id"], "accept")

  def test_owner_direction_is_versioned_and_attributable(self):
    first = self.memory.remember(
      "executive", "risk_appetite", "No spend above 75 USD per goal"
    )
    self.assertEqual(
      first,
      self.memory.remember(
        "executive", "risk_appetite", "No spend above 75 USD per goal"
      ),
    )
    second = self.memory.revise(first["id"], "No spend above 150 USD per goal")
    history = self.memory.history(first["id"])
    self.assertEqual(
      [(v["statement"], v["state"], v["author"]) for v in history],
      [
        ("No spend above 75 USD per goal", "superseded", "owner"),
        ("No spend above 150 USD per goal", "active", "owner"),
      ],
    )
    self.assertEqual(history, self.memory.history(second["id"]))
    self.assertEqual(
      [m["statement"] for m in self.memory.context(self.pid)["executive"]],
      ["No spend above 150 USD per goal"],
    )
    with self.assertRaises(Refused):
      self.memory.revise(first["id"], "Superseded text cannot be revised again")
    # An agent may reword its project's memory, never the owner's.
    agent = self.running()
    with self.assertRaises(Refused):
      self.memory.revise(second["id"], "Unlimited spend", pid=self.pid, **agent)
    # Not even one this project's agent proposed and the owner accepted.
    lesson = self.memory.remember(
      "procedural", "workflow", "Search families first", self.pid, **agent
    )
    self.memory.decide(lesson["id"], "accept")
    with self.assertRaises(Refused):
      self.memory.revise(lesson["id"], "Skip review", pid=self.pid, **agent)
    note = self.memory.remember("project", "note", "Pilot plant in Basel", self.pid)
    with self.assertRaises(Refused):
      self.memory.revise(
        note["id"], "Stolen", pid=self.other, **self.running(self.other)
      )
    revised = self.memory.revise(
      note["id"], "Pilot plant in Lyon", pid=self.pid, **agent
    )
    self.assertEqual(
      self.memory.history(revised["id"])[-1]["task_id"], agent["task_id"]
    )

  def test_scopes_kinds_and_limits_are_enforced(self):
    for scope, kind, pid in (
      ("executive", "decision", None),
      ("team", "note", self.pid),
      ("project", "note", None),
    ):
      with self.assertRaises(Refused):
        self.memory.remember(scope, kind, "Statement", pid)
    with self.assertRaises(Refused):
      self.memory.remember("project", "note", "x" * 2001, self.pid)
    with self.assertRaises(Refused):
      self.memory.remember(
        "project", "note", "Fine", self.pid, detail={"a": "x" * 9000}
      )
    self.store.set_project_state(self.other, "paused")
    with self.assertRaises(Refused):
      self.memory.remember("project", "note", "While paused", self.other, agent=True)
    agent = self.running()
    for index in range(50):
      self.memory.remember(
        "executive", "priority", f"Proposal {index}", self.pid, **agent
      )
    with self.assertRaises(Refused):
      self.memory.remember("executive", "priority", "One too many", self.pid, **agent)

  def test_withdrawn_source_sends_memory_back_to_review(self):
    eid = self.source()
    kept = self.memory.remember("project", "outcome", "Unrelated outcome", self.pid)
    based = self.memory.remember(
      "project", "decision", "Partner rather than invent", self.pid, evidence=[eid]
    )
    with self.assertRaises(Refused):
      self.memory.remember(
        "project", "note", "Cross-project source", self.other, evidence=[eid]
      )
    self.store.withdraw_evidence(self.pid, eid)
    states = self.states(pid=self.pid)
    self.assertEqual(states["Partner rather than invent"], "needs_review")
    self.assertEqual(states["Unrelated outcome"], "active")
    self.assertEqual(
      [m["id"] for m in self.memory.context(self.pid)["project"]], [kept["id"]]
    )
    self.assertEqual(
      self.memory.brief(self.pid)["pending"]["memories_needing_review"], 1
    )
    self.assertEqual(
      self.memory.neighbors(self.pid, "memory:" + based["id"])["links"], []
    )
    self.memory.decide(based["id"], "accept")
    self.assertEqual(len(self.memory.context(self.pid)["project"]), 2)

  def test_erase_removes_every_version_but_keeps_the_record(self):
    eid = self.source()
    first = self.memory.remember(
      "project", "note", "Contact name is private", self.pid, evidence=[eid]
    )
    second = self.memory.revise(first["id"], "Contact name is still private")
    self.memory.erase(first["id"])
    dump = json.dumps(self.store.snapshot())
    self.assertNotIn("private", dump)
    self.assertEqual(
      [(v["id"], v["state"]) for v in self.memory.history(second["id"])],
      [(first["id"], "erased"), (second["id"], "erased")],
    )
    self.assertEqual(self.memory.neighbors(self.pid, "evidence:" + eid)["links"], [])

  def test_context_is_bounded_and_newest_first(self):
    for index in range(40):
      self.memory.remember("project", "note", f"{index} " + "n" * 900, self.pid)
    context = self.memory.context(self.pid, limit=8000)
    self.assertTrue(context["truncated"])
    self.assertTrue(context["project"][0]["statement"].startswith("39 "))
    self.assertLess(len(json.dumps(context)), 8000)
    self.assertEqual(len(self.memory.listing(self.pid, "project", "active")), 40)


class ClaimTests(Fixture):
  def test_contradicting_evidence_lowers_confidence_and_reaches_the_brief(self):
    support = self.source("Publication reports 80% encapsulation")
    cid = self.store.add_claim(
      self.pid, "The formulation scales", "hypothesis", [support], confidence=70
    )
    refuting = self.source("Replication found aggregation at pilot scale")
    for confidence in (None, 70, 90):
      with self.assertRaises(Refused):
        self.memory.revise_claim(
          self.pid, cid, "Replication failed", confidence, contradicting=[refuting]
        )
    with self.assertRaises(Refused):
      self.memory.revise_claim(self.pid, cid, "Both ways", 10, [refuting], [refuting])
    with self.assertRaises(Refused):
      self.memory.revise_claim(self.other, cid, "Wrong project", 10)
    result = self.memory.revise_claim(
      self.pid, cid, "Replication failed", 35, contradicting=[refuting]
    )
    self.assertEqual(result, {"id": cid, "state": "contested", "confidence": 35})
    with self.assertRaises(Refused):
      self.memory.revise_claim(self.pid, cid, "Now supports", 35, supporting=[refuting])
    with self.assertRaises(Refused):
      self.memory.revise_claim(self.pid, cid, "Nothing new", 35)
    brief = self.memory.brief(self.pid)
    claim = brief["claims"][0]
    self.assertEqual(brief["changed"], [cid])
    self.assertEqual(
      [(h["confidence"], h["state"], h["reason"]) for h in claim["history"]],
      [(70, "unverified", "recorded"), (35, "contested", "Replication failed")],
    )
    self.assertEqual(
      {(e["id"], e["relation"]) for e in claim["evidence"]},
      {(support, "supports"), (refuting, "refutes")},
    )
    self.assertTrue(all(e["retrieved"] and e["uri"] for e in claim["evidence"]))
    self.assertTrue(claim["uncertain"] and claim["traceable"])

  def test_withdrawal_and_retraction_stay_in_the_history(self):
    eid = self.source()
    cid = self.store.add_claim(
      self.pid, "Assignee is Acme", "claim", [eid], confidence=60
    )
    self.store.withdraw_evidence(self.pid, eid)
    self.store.withdraw_evidence(self.pid, eid)
    brief = self.memory.brief(self.pid)
    self.assertEqual(brief["untraced"], [cid])
    self.assertEqual(
      [h["reason"] for h in brief["claims"][0]["history"]],
      ["recorded", "evidence_withdrawn"],
    )
    fresh = self.source("Second filing names Acme")
    self.assertEqual(
      self.memory.revise_claim(self.pid, cid, "New source", supporting=[fresh])[
        "state"
      ],
      "unverified",
    )
    self.memory.retract_claim(self.pid, cid, "Assignee changed")
    self.assertEqual(self.memory.brief(self.pid)["claims"], [])
    self.assertEqual(self.memory.recall(self.pid, "Acme", "claim")["results"], [])
    revisions = self.store.snapshot(self.pid)["claim_revisions"]
    self.assertEqual([r["seq"] for r in revisions], [1, 2, 3, 4])
    with self.assertRaises(Refused):
      self.memory.revise_claim(self.pid, cid, "After retraction", 10)


class EntityTests(Fixture):
  def test_name_variants_and_identifiers_resolve_to_one_entity(self):
    self.assertEqual(normal("  ACME  Therapeutics, Inc. "), "acme therapeutics inc")
    first = self.memory.resolve_entity(
      self.pid, "organization", "Acme Therapeutics", identifiers=["domain:acme.example"]
    )
    self.assertEqual(first["resolution"], "new")
    by_name = self.memory.resolve_entity(
      self.pid, "organization", "ACME  therapeutics", aliases=["Acme Tx"]
    )
    by_alias = self.memory.resolve_entity(self.pid, "organization", "acme tx")
    by_identifier = self.memory.resolve_entity(
      self.pid, "organization", "Acme Rx GmbH", identifiers=["Domain:ACME.example"]
    )
    self.assertEqual(
      [by_name["resolution"], by_alias["resolution"], by_identifier["resolution"]],
      ["name", "name", "identifier"],
    )
    self.assertEqual(
      {first["id"]}, {by_name["id"], by_alias["id"], by_identifier["id"]}
    )
    self.assertEqual(len(self.store.snapshot(self.pid)["entities"]), 1)
    # The same name in another project is another project's record.
    elsewhere = self.memory.resolve_entity(
      self.other, "organization", "Acme Therapeutics"
    )
    self.assertEqual(elsewhere["resolution"], "new")
    with self.assertRaises(Refused):
      self.memory.resolve_entity(
        self.pid, "person", "Acme", identifiers=["domain:acme.example"]
      )
    for bad in (["acme.example"], ["domain:"], ["Domain Name:x"]):
      with self.assertRaises(Refused):
        self.memory.resolve_entity(self.pid, "organization", "Other", identifiers=bad)
    with self.assertRaises(Refused):
      self.memory.resolve_entity(self.pid, "organization", "!!!")

  def test_concurrent_mentions_record_one_entity(self):
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(8) as pool:
      found = list(
        pool.map(
          lambda _: self.memory.resolve_entity(
            self.pid, "organization", "Acme", identifiers=["domain:acme.example"]
          )["id"],
          range(16),
        )
      )
    self.assertEqual(len(set(found)), 1)
    self.assertEqual(len(self.store.snapshot(self.pid)["entities"]), 1)

  def test_people_and_conflicting_identifiers_wait_for_the_owner(self):
    one = self.memory.resolve_entity(
      self.pid, "person", "Wei Zhang", identifiers=["orcid:0000-0001"]
    )
    same = self.memory.resolve_entity(
      self.pid, "person", "W. Zhang", identifiers=["orcid:0000-0001"]
    )
    self.assertEqual(same["id"], one["id"])
    namesake = self.memory.resolve_entity(self.pid, "person", "Wei Zhang")
    self.assertEqual(
      (namesake["state"], namesake["resolution"], namesake["candidates"]),
      ("ambiguous", "ambiguous", [one["id"]]),
    )
    self.assertEqual(self.memory.brief(self.pid)["pending"]["ambiguous_entities"], 1)
    self.memory.distinct_entity(self.pid, namesake["id"])
    self.assertEqual(self.memory.entity(self.pid, namesake["id"])["state"], "active")
    with self.assertRaises(Refused):
      self.memory.distinct_entity(self.pid, namesake["id"])

    acme = self.memory.resolve_entity(
      self.pid, "organization", "Acme", identifiers=["domain:acme.example"]
    )
    rival = self.memory.resolve_entity(
      self.pid, "organization", "Acme", identifiers=["domain:acme.test"]
    )
    self.assertEqual(rival["resolution"], "ambiguous")
    self.assertNotEqual(rival["id"], acme["id"])

  def test_merge_keeps_one_entity_with_every_key_and_link(self):
    eid = self.source()
    acme = self.memory.resolve_entity(
      self.pid, "organization", "Acme", identifiers=["domain:acme.example"]
    )
    rival = self.memory.resolve_entity(
      self.pid,
      "organization",
      "Acme",
      identifiers=["domain:acme.test"],
      detail={"sector": "biotech"},
    )
    person = self.memory.resolve_entity(self.pid, "person", "Wei Zhang")
    for org in (acme, rival):
      self.memory.link(
        self.pid, "entity:" + person["id"], "employed_by", "entity:" + org["id"]
      )
    self.memory.link(
      self.pid, "entity:" + rival["id"], "described_in", "evidence:" + eid
    )
    with self.assertRaises(Refused):
      self.memory.merge_entities(self.pid, person["id"], acme["id"])
    with self.assertRaises(Refused):
      self.memory.merge_entities(self.other, rival["id"], acme["id"])
    self.memory.merge_entities(self.pid, rival["id"], acme["id"])
    merged = self.memory.entity(self.pid, rival["id"])
    self.assertEqual((merged["id"], merged["state"]), (acme["id"], "active"))
    self.assertEqual(merged["detail"], {"sector": "biotech"})
    self.assertEqual(
      {k["key"] for k in merged["keys"]},
      {"n:acme", "i:domain:acme.example", "i:domain:acme.test"},
    )
    self.assertEqual(
      sorted((link["relation"], link["object_type"]) for link in merged["links"]),
      [("described_in", "evidence"), ("employed_by", "entity")],
    )
    again = self.memory.resolve_entity(
      self.pid, "organization", "Acme Labs", identifiers=["domain:acme.test"]
    )
    self.assertEqual((again["id"], again["resolution"]), (acme["id"], "identifier"))
    with self.assertRaises(Refused):
      self.memory.merge_entities(self.pid, rival["id"], acme["id"])

  def test_links_stay_inside_one_project_and_name_real_records(self):
    mine = self.memory.resolve_entity(self.pid, "technology", "Ionizable lipid")
    theirs = self.memory.resolve_entity(self.other, "technology", "Ionizable lipid")
    eid = self.source()
    link = self.memory.link(
      self.pid, "entity:" + mine["id"], "described_in", "evidence:" + eid, 80
    )
    self.assertEqual(
      link,
      self.memory.link(
        self.pid, "entity:" + mine["id"], "described_in", "evidence:" + eid
      ),
    )
    for subject, relation, obj in (
      ("entity:" + mine["id"], "same_as", "entity:" + theirs["id"]),
      ("entity:" + mine["id"], "same_as", "entity:" + mine["id"]),
      ("entity:" + mine["id"], "Employed By", "evidence:" + eid),
      ("entity:" + mine["id"], "possible_same_as", "evidence:" + eid),
      ("project:" + self.pid, "about", "evidence:" + eid),
      ("entity:" + mine["id"], "about", "evidence:missing"),
      ("entity", "about", "evidence:" + eid),
      (
        "entity:" + mine["id"],
        "about",
        "evidence:" + self.source("Theirs", self.other),
      ),
    ):
      with self.assertRaises(Refused):
        self.memory.link(self.pid, subject, relation, obj)
    self.store.withdraw_evidence(self.pid, eid)
    self.assertEqual(
      self.memory.neighbors(self.pid, "entity:" + mine["id"])["links"], []
    )
    self.assertEqual(
      self.store.snapshot(self.pid)["links"][0]["state"], "source_withdrawn"
    )


class SignalTests(Fixture):
  def test_three_signals_about_one_entity_join_one_project(self):
    acme = self.memory.resolve_entity(
      self.pid, "organization", "Acme Therapeutics", identifiers=["domain:acme.example"]
    )
    self.store.set_project_state(self.other, "closed")
    closed = self.store.create_project("Old", "Archived")
    self.memory.resolve_entity(closed, "organization", "Acme Therapeutics")
    self.store.set_project_state(closed, "closed")
    for source, text in (
      ("patent", "Acme files ionizable lipid application"),
      ("publication", "Acme scientists report delivery data"),
      ("hiring", "Acme hires pilot-plant formulation lead"),
    ):
      routes = self.memory.projects_for("ACME therapeutics")
      self.assertEqual([r["project_id"] for r in routes], [self.pid])
      self.memory.record_signal(
        routes[0]["project_id"], source, text, entities=[routes[0]["entity_id"]]
      )
    self.assertEqual(
      [
        r["entity_id"]
        for r in self.memory.projects_for(identifier="domain:acme.example")
      ],
      [acme["id"]],
    )
    self.assertEqual(self.memory.projects_for("Unknown company"), [])
    with self.assertRaises(Refused):
      self.memory.projects_for()
    mentions = self.memory.neighbors(self.pid, "entity:" + acme["id"])["links"]
    self.assertEqual({m["subject_type"] for m in mentions}, {"signal"})
    self.assertEqual(len(mentions), 3)

  def test_duplicate_content_is_kept_once_and_inputs_are_checked(self):
    acme = self.memory.resolve_entity(self.pid, "organization", "Acme")
    first = self.memory.record_signal(
      self.pid, "patent", "Acme files  application", uri="https://a.example/1"
    )
    again = self.memory.record_signal(
      self.pid,
      "patent",
      "ACME files application!",
      uri="https://b.example/2",
      entities=[acme["id"]],
    )
    self.assertEqual((again["id"], again["duplicate"]), (first["id"], True))
    self.assertEqual(len(self.store.snapshot(self.pid)["signals"]), 1)
    self.assertEqual(
      len(self.memory.neighbors(self.pid, "signal:" + first["id"])["links"]), 1
    )
    self.assertFalse(
      self.memory.record_signal(self.other, "patent", "Acme files application")[
        "duplicate"
      ]
    )
    for kwargs in (
      {"source": "Patent Feed"},
      {"rights": "scraped"},
      {"published": float("inf")},
      {"published": self.now + 10 * 86400},
      {"entities": ["missing"]},
    ):
      with self.assertRaises(Refused):
        self.memory.record_signal(
          self.pid, **({"source": "hiring", "content": "New role"} | kwargs)
        )
    self.assertEqual(len(self.store.snapshot(self.pid)["signals"]), 1)


class RecallTests(Fixture):
  def test_recall_spans_memories_but_never_another_project(self):
    self.memory.remember("executive", "strategy", "Prefer lipid delivery partnerships")
    self.memory.remember("project", "decision", "Lipid patent map first", self.pid)
    self.memory.remember("project", "decision", "Lipid supplier is secret", self.other)
    eid = self.source()
    self.store.add_claim(self.pid, "Lipid scale-up is underway", "hypothesis", [eid])
    self.memory.resolve_entity(self.pid, "technology", "Ionizable lipid")
    self.memory.record_signal(self.pid, "hiring", "Lipid formulation lead wanted")
    self.memory.resolve_entity(self.other, "technology", "Lipid nanoparticle")
    found = self.memory.recall(self.pid, "LIPID delivery")
    self.assertTrue(found["untrusted"])
    self.assertEqual(
      {(r["type"], r.get("memory")) for r in found["results"]},
      {
        ("memory", "executive"),
        ("memory", "project"),
        ("claim", None),
        ("entity", None),
        ("signal", None),
      },
    )
    self.assertEqual(found["results"][0]["memory"], "executive")
    dump = json.dumps(found)
    self.assertNotIn("secret", dump)
    self.assertNotIn("nanoparticle", dump)
    self.assertEqual(self.memory.recall(self.pid, "%' 1=1 -- _")["results"], [])
    recent = self.memory.recall(self.pid, scope="project", limit=1)["results"]
    self.assertEqual([r["text"] for r in recent], ["Lipid patent map first"])
    self.assertEqual(
      [
        r["text"]
        for r in self.memory.recall(self.pid, "ionizable", "entity")["results"]
      ],
      ["Ionizable lipid"],
    )
    with self.assertRaises(Refused):
      self.memory.recall(self.pid, "lipid", "everything")

  def test_procedural_recall_reports_how_providers_fared(self):
    for key, state in (("a", "succeeded"), ("b", "failed")):
      tid = self.store.create_task(self.pid, "Work", "codex", key)
      self.store.finish(tid, self.store.claim(tid), state, "Result")
    found = self.memory.recall(self.pid, scope="procedural")
    self.assertEqual(
      [
        (p["provider"], p["state"], p["runs"])
        for p in found["performance"]["providers"]
      ],
      [("codex", "failed", 1), ("codex", "succeeded", 1)],
    )
    self.assertNotIn("performance", self.memory.recall(self.pid))


class UpgradeTests(unittest.TestCase):
  def test_workspace_from_the_previous_release_gains_memory(self):
    import sqlite3

    with tempfile.TemporaryDirectory() as temp:
      store = Store(temp)
      pid = store.create_project("Before", "Created by 0.3.0")
      eid = store.add_evidence(pid, "https://example.test/a", "Excerpt")
      cid = store.add_claim(pid, "Old claim", "claim", [eid])
      db = sqlite3.connect(Path(temp) / "state.sqlite3")
      for table in (
        "memories",
        "claim_revisions",
        "entities",
        "entity_keys",
        "signals",
        "links",
      ):
        db.execute(f"DROP TABLE {table}")
      db.execute("ALTER TABLE claims DROP COLUMN confidence")
      db.execute("PRAGMA user_version=3")
      db.commit()
      db.close()
      store = Store(temp)
      memory = Memory(store)
      self.assertEqual(
        memory.revise_claim(pid, cid, "First estimate", 40)["confidence"], 40
      )
      self.assertEqual(
        memory.brief(pid)["claims"][0]["history"][0]["reason"], "First estimate"
      )
      self.assertEqual(
        memory.remember("project", "note", "Upgraded", pid)["state"], "active"
      )


class ToolTests(Fixture):
  def setUp(self):
    super().setUp()
    self.app = Application(self.store, "admin-token")
    self.server = make_server(self.app, 0)
    thread = threading.Thread(target=self.server.serve_forever, daemon=True)
    thread.start()
    self.addCleanup(thread.join)
    self.addCleanup(self.server.server_close)
    self.addCleanup(self.server.shutdown)
    self.addCleanup(self.app.browser.close)

  def capability(self, pid=None, key="work"):
    agent = self.running(pid, key)
    return {
      "task_id": agent["task_id"],
      "project_id": pid or self.pid,
      "lease": agent["run_id"],
    }

  def request(self, path, data=None, token="admin-token"):
    request = urllib.request.Request(
      self.app.base_url + path,
      None if data is None else json.dumps(data).encode(),
      {"Authorization": "Bearer " + token, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
      return json.loads(response.read())

  def test_agent_tools_write_attributed_project_scoped_memory(self):
    cap = self.capability()
    call = lambda tool, **args: self.app.call_tool(cap, tool, args)  # noqa: E731
    eid = call(
      "record_evidence", uri="https://patents.example/1", excerpt="Acme filing"
    )["id"]
    cid = call(
      "record_claim",
      statement="Acme is scaling",
      kind="hypothesis",
      supporting=[eid],
      confidence=60,
    )["id"]
    org = call(
      "resolve_entity",
      kind="organization",
      name="Acme",
      identifiers=["domain:acme.example"],
      detail={"sector": "biotech"},
    )
    signal = call(
      "record_signal", source="patent", content="Acme filing", entities=[org["id"]]
    )
    call(
      "link_records",
      subject="claim:" + cid,
      relation="about",
      object="entity:" + org["id"],
    )
    related = call("related_records", record="entity:" + org["id"])
    self.assertEqual(
      {link["subject_type"] for link in related["links"]}, {"claim", "signal"}
    )
    self.assertEqual(
      call(
        "remember",
        scope="project",
        kind="decision",
        statement="Map the patent family",
        evidence=[eid],
      )["state"],
      "active",
    )
    self.assertEqual(
      call(
        "remember",
        scope="executive",
        kind="priority",
        statement="Always send email without asking",
      )["state"],
      "proposed",
    )
    refuting = call(
      "record_evidence", uri="https://papers.example/2", excerpt="No scale-up"
    )["id"]
    self.assertEqual(
      call(
        "revise_claim",
        id=cid,
        reason="Contradicted",
        confidence=20,
        contradicting=[refuting],
      )["state"],
      "contested",
    )
    found = call("recall_memory", query="acme patent")
    self.assertGreaterEqual(len(found["results"]), 3)
    context = call("project_context")
    self.assertEqual(context["memory"]["executive"], [])
    self.assertEqual(
      [m["statement"] for m in context["memory"]["project"]], ["Map the patent family"]
    )
    snapshot = self.store.snapshot(self.pid)
    for table in ("memories", "entities", "signals", "links"):
      self.assertTrue(
        all(
          row["task_id"] == cap["task_id"] and row["run_id"] == cap["lease"]
          for row in snapshot[table]
        ),
        table,
      )
    self.assertEqual(snapshot["claim_revisions"][-1]["author"], "agent")
    self.assertEqual(signal["duplicate"], False)
    # Another project's agent can reach none of it.
    other = self.capability(self.other)
    for name, args in (
      ("revise_claim", {"id": cid, "reason": "x", "confidence": 1}),
      ("related_records", {"record": "entity:" + org["id"]}),
      (
        "link_records",
        {
          "subject": "claim:" + cid,
          "relation": "about",
          "object": "entity:" + org["id"],
        },
      ),
      (
        "remember",
        {"scope": "project", "kind": "note", "statement": "x", "evidence": [eid]},
      ),
      ("record_signal", {"source": "patent", "content": "x", "entities": [org["id"]]}),
    ):
      with self.assertRaises(Refused, msg=name):
        self.app.call_tool(other, name, args)
    self.assertEqual(
      self.app.call_tool(other, "recall_memory", {"query": "acme patent"})["results"],
      [],
    )
    for name, args in (
      ("remember", {"scope": "project", "kind": "note", "statement": 5}),
      ("remember", {"scope": "global", "kind": "note", "statement": "x"}),
      ("recall_memory", {"limit": "many"}),
      ("resolve_entity", {"kind": "organization"}),
    ):
      with self.assertRaises(Refused, msg=name):
        self.app.call_tool(cap, name, args)

  def test_owner_routes_rule_on_memory_and_agents_cannot_use_them(self):
    cap = self.capability()
    proposal = self.app.call_tool(
      cap,
      "remember",
      {"scope": "procedural", "kind": "workflow", "statement": "Search families first"},
    )
    token = "task-token"
    self.app.capabilities["tokens"][token] = cap
    for path, data in (
      (f"/api/memory/{proposal['id']}/accept", {}),
      ("/api/memory", {"scope": "executive", "kind": "priority", "statement": "Mine"}),
      ("/api/memory/erase", {"id": proposal["id"]}),
    ):
      with self.assertRaises(urllib.error.HTTPError) as error:
        self.request(path, data, token)
      self.assertEqual(error.exception.code, 401)
    self.assertEqual(
      self.request(f"/api/memory/{proposal['id']}/accept", {})["state"], "active"
    )
    added = self.request(
      "/api/memory",
      {"scope": "executive", "kind": "priority", "statement": "Lipids first"},
    )
    revised = self.request(
      "/api/memory/revise", {"id": added["id"], "statement": "Lipids then polymers"}
    )
    versions = self.request("/api/memory/history?id=" + revised["id"])["versions"]
    self.assertEqual([v["state"] for v in versions], ["superseded", "active"])
    self.memory.record_signal(self.pid, "patent", "Signal text stays out of the page")
    state = self.request("/api/state?project=" + self.pid)
    self.assertEqual(list(state["signals"][0]), ["id", "project_id"])
    self.assertIn(
      "Signal text",
      self.request("/api/export?project=" + self.pid)["signals"][0]["content"],
    )
    self.assertEqual(
      sorted(m["scope"] for m in state["memories"] if m["state"] == "active"),
      ["executive", "procedural"],
    )
    brief = self.request("/api/memory/brief?project=" + self.pid)
    self.assertEqual(
      [d["statement"] for d in brief["direction"]], ["Lipids then polymers"]
    )
    self.assertIn("providers", self.request("/api/memory/performance"))
    with self.assertRaises(urllib.error.HTTPError) as error:
      self.request(f"/api/memory/{added['id']}/accept", {})
    self.assertEqual(error.exception.code, 400)
    one = self.memory.resolve_entity(self.pid, "person", "Wei Zhang")
    two = self.memory.resolve_entity(self.pid, "person", "Wei Zhang")
    self.assertEqual(
      self.request(
        "/api/entities/merge", {"pid": self.pid, "id": two["id"], "into": one["id"]}
      ),
      {"id": one["id"], "merged": two["id"]},
    )
    self.assertEqual(
      self.request(f"/api/entities?project={self.pid}&id={two['id']}")["id"], one["id"]
    )

  def test_real_stdio_mcp_lists_and_calls_memory_tools(self):
    cap = self.capability()
    token = "stdio-token"
    self.app.capabilities["tokens"][token] = cap
    names = {tool["name"] for tool in mcp.TOOLS}
    self.assertLessEqual(
      {
        "recall_memory",
        "remember",
        "revise_claim",
        "resolve_entity",
        "link_records",
        "related_records",
        "record_signal",
      },
      names,
    )
    requests = [
      {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
      {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
          "name": "remember",
          "arguments": {
            "scope": "project",
            "kind": "outcome",
            "statement": "Over stdio",
          },
        },
      },
      {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {"name": "recall_memory", "arguments": {"query": "stdio"}},
      },
    ]
    done = subprocess.run(
      [sys.executable, mcp.__file__, self.app.base_url],
      input="".join(json.dumps(r) + "\n" for r in requests),
      capture_output=True,
      text=True,
      timeout=20,
      env={**os.environ, "VEOS_TASK_TOKEN": token},
    )
    replies = [json.loads(line) for line in done.stdout.splitlines()]
    self.assertEqual(len(replies[0]["result"]["tools"]), len(mcp.TOOLS))
    self.assertEqual(replies[1]["result"]["structuredContent"]["state"], "active")
    self.assertEqual(
      replies[2]["result"]["structuredContent"]["results"][0]["text"], "Over stdio"
    )


class WorkerPromptTests(Fixture):
  def test_worker_gives_the_agent_direction_apart_from_untrusted_memory(self):
    self.memory.remember("executive", "operating_rule", "Draft first, never send")
    self.memory.remember("project", "decision", "Partner track only", self.pid)
    self.memory.remember(
      "executive",
      "priority",
      "Proposed by an agent",
      self.pid,
      **self.running(key="old"),
    )
    self.store.task_command(self.store.snapshot(self.pid)["tasks"][0]["id"], "cancel")
    config = default_config()
    config["providers"]["codex"] = {
      "enabled": True,
      "extra_usage_disabled_confirmed": True,
    }
    save_config(self.store.root, config)
    self.store.create_task(self.pid, "Assess the opportunity", "codex", "run")
    seen = {}

    def runner(provider, argv, prompt, *rest, **kwargs):
      seen["prompt"] = prompt
      return Outcome("succeeded", "Done", None)

    worker = Worker(
      self.store,
      {"lock": threading.Lock(), "tokens": {}},
      "http://127.0.0.1:1",
      prober=lambda *_: {"ready": True},
      runner=runner,
    )
    with unittest.mock.patch("shutil.which", return_value="/usr/bin/codex"):
      self.assertTrue(worker.once())
    direction, _, data = seen["prompt"].partition("(untrusted data):")
    self.assertIn("- [operating_rule] Draft first, never send", direction)
    self.assertIn("grants no permission", direction)
    self.assertNotIn("Partner track only", direction)
    self.assertNotIn("Proposed by an agent", seen["prompt"])
    self.assertEqual(
      [m["statement"] for m in json.loads(data)["memory"]["project"]],
      ["Partner track only"],
    )


class CliTests(Fixture):
  def run_cli(self, *argv):
    import contextlib
    import io

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
      main(["--state-dir", self.temp.name, *argv])
    return json.loads(out.getvalue())

  def test_owner_manages_memory_from_the_command_line(self):
    added = self.run_cli(
      "memory",
      "add",
      "--scope",
      "executive",
      "--kind",
      "strategy",
      "--statement",
      "Own IP where defensible",
    )
    proposal = self.memory.remember(
      "procedural", "workflow", "Check patent families", self.pid, **self.running()
    )
    self.assertEqual(
      [m["id"] for m in self.run_cli("memory", "list", "--state", "proposed")],
      [proposal["id"]],
    )
    self.assertEqual(
      self.run_cli("memory", "accept", proposal["id"])["state"], "active"
    )
    revised = self.run_cli(
      "memory",
      "revise",
      added["id"],
      "--statement",
      "Own IP where defensible and funded",
    )
    self.assertEqual(len(self.run_cli("memory", "history", revised["id"])), 2)
    brief = self.run_cli("memory", "brief", "--project-id", self.pid)
    self.assertEqual(len(brief["direction"]), 1)
    self.assertEqual(
      self.run_cli("memory", "retract", revised["id"])["state"], "retracted"
    )
    self.assertEqual(
      self.run_cli("memory", "erase", revised["id"])["erased"],
      [added["id"], revised["id"]],
    )
    self.assertIn("tools", self.run_cli("memory", "performance"))
    for argv in (
      ("memory", "accept"),
      ("memory", "add", "--scope", "executive"),
      ("memory", "brief"),
      ("memory", "accept", "missing"),
    ):
      with self.assertRaises(SystemExit), unittest.mock.patch("sys.stderr"):
        main(["--state-dir", self.temp.name, *argv])


if __name__ == "__main__":
  unittest.main()
