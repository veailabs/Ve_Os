import concurrent.futures
import threading
from pathlib import Path
from unittest.mock import patch

from test_runtime import Fixture, IntegrationFixture

from veos_runtime.store import Refused, Store
from veos_runtime.worker import Worker


class LifecycleTests(IntegrationFixture):
  def test_exhausted_budget_waiting_for_approval_does_not_busy_loop(self):
    self.proposal()
    self.finish_turn()
    with self.store.db() as db:
      db.execute("UPDATE projects SET max_runs=runs_used WHERE id=?", (self.pid,))
    revision = self.store.change_version
    self.store.maintain()
    self.assertEqual(self.store.change_version, revision)
    self.assertEqual(self.store.task(self.tid)["state"], "waiting_approval")

  def finish_turn(self):
    row = self.store.task(self.tid)
    self.store.finish(
      self.tid, row["lease"], "succeeded", "Waiting for the action receipt"
    )

  def test_pending_action_blocks_dependent_then_receipt_resumes(self):
    child = self.task("dependent", dependencies=[self.tid])
    aid = self.proposal()
    self.finish_turn()
    self.assertEqual(self.store.task(self.tid)["state"], "waiting_approval")
    self.assertIsNone(self.store.claim(child))
    self.actions.decide(aid, True)
    self.assertEqual(self.store.task(self.tid)["state"], "queued")
    self.assertIsNone(self.store.claim(child))
    lease = self.store.claim(self.tid)
    self.assertEqual(self.store.task(self.tid)["attempts"], 1)
    self.assertEqual(self.store.task_actions(self.tid)[0]["state"], "succeeded")
    self.store.finish(self.tid, lease, "succeeded", "Confirmed receipt-123")
    self.assertIsNotNone(self.store.claim(child))
    self.assertEqual(len(self.client.calls), 1)

  def test_receipt_during_running_turn_queues_continuation(self):
    aid = self.proposal()
    self.actions.decide(aid, True)
    self.finish_turn()
    self.assertEqual(self.store.task(self.tid)["state"], "queued")
    self.assertEqual(self.store.task(self.tid)["continuation"], 1)

  def test_denial_is_explained_but_cannot_be_marked_completed(self):
    aid = self.proposal()
    self.finish_turn()
    self.actions.decide(aid, False)
    self.store.claim(self.tid)
    with self.assertRaises(Refused):
      self.proposal({"body": "changed to evade denial", "to": "x@example.com"})
    self.finish_turn()
    self.assertEqual(self.store.task(self.tid)["state"], "blocked")
    self.assertEqual(self.client.calls, [])

  def test_unknown_receipt_never_automatically_restarts(self):
    aid = self.proposal()
    self.finish_turn()
    self.client.fail = True
    self.actions.decide(aid, True)
    self.assertEqual(self.store.task(self.tid)["state"], "unknown")
    self.assertEqual(self.store.ready(), [])

  def test_expiry_wakes_waiting_task_and_survives_restart(self):
    self.proposal()
    self.finish_turn()
    self.now += 601
    recovered = Store(self.store.root, clock=lambda: self.now)
    recovered.recover()
    recovered.maintain()
    self.assertEqual(recovered.task(self.tid)["state"], "queued")
    self.assertEqual(recovered.task_actions(self.tid)[0]["state"], "expired")

  def test_interrupted_action_does_not_leave_parent_waiting(self):
    aid = self.proposal()
    self.finish_turn()
    with self.store.db() as db:
      db.execute("UPDATE actions SET state='executing' WHERE id=?", (aid,))
    self.store.recover()
    self.assertEqual(self.store.task(self.tid)["state"], "unknown")

  def test_close_cancels_waiting_parent(self):
    self.proposal()
    self.finish_turn()
    self.store.set_project_state(self.pid, "closed")
    self.assertEqual(self.store.task(self.tid)["state"], "cancelled")


class RecoveryEvidenceTests(Fixture):
  def test_setup_failure_cleans_lease_and_capability(self):
    tid = self.task()
    caps = {"lock": threading.Lock(), "tokens": {}}
    worker = Worker(
      self.store, caps, "http://127.0.0.1:8764", prober=lambda *_: {"ready": True}
    )
    real = Path.mkdir

    def fail_project(path, *args, **kwargs):
      if path.name == self.pid:
        raise OSError("Disk failure")
      return real(path, *args, **kwargs)

    with patch.object(Path, "mkdir", fail_project):
      worker.once()
    self.assertEqual(self.store.task(tid)["state"], "unknown")
    self.assertIsNone(self.store.task(tid)["lease"])
    self.assertEqual(caps["tokens"], {})
    self.assertEqual(self.store.snapshot()["runs"][0]["state"], "unknown")

  def test_concurrent_sources_return_one_id(self):
    barrier = threading.Barrier(8)

    def record(_):
      barrier.wait()
      return self.store.add_evidence(
        self.pid, "https://example.com/source", "Same immutable source"
      )

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
      ids = list(pool.map(record, range(8)))
    self.assertEqual(len(set(ids)), 1)
    self.assertEqual(len(self.store.snapshot()["evidence"]), 1)

  def test_large_task_history_does_not_starve_sources(self):
    for i in range(20):
      tid = self.task(str(i))
      with self.store.db() as db:
        db.execute(
          "UPDATE tasks SET result=?,state='succeeded' WHERE id=?",
          ("r" * 8000, tid),
        )
    eid = self.store.add_evidence(
      self.pid, "https://example.com/key", "essential " + "s" * 20000
    )
    context = self.store.context(self.pid)
    self.assertTrue(context["evidence"])
    self.assertEqual(
      self.store.search_evidence(self.pid, "essential")["results"][0]["id"], eid
    )
    first = self.store.get_evidence(self.pid, eid)
    second = self.store.get_evidence(self.pid, eid, first["next_offset"])
    self.assertEqual(len(first["excerpt"] + second["excerpt"]), 20010)
    other = self.store.create_project("Other", "Private")
    with self.assertRaises(Refused):
      self.store.get_evidence(other, eid)

  def test_task_run_provenance_is_stored(self):
    tid = self.task()
    lease = self.store.claim(tid)
    eid = self.store.add_evidence(
      self.pid, "https://example.com", "Evidence", tid, lease
    )
    self.store.add_claim(
      self.pid, "Unverified finding", "claim", [eid], task_id=tid, run_id=lease
    )
    self.assertEqual(self.store.snapshot()["claims"][0]["run_id"], lease)
    self.assertEqual(self.store.get_evidence(self.pid, eid)["run_id"], lease)
