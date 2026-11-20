import concurrent.futures
import tempfile
import unittest
from pathlib import Path

from veos_runtime.artifacts import Artifacts
from veos_runtime.store import Refused, Store


class ArtifactTests(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    self.store = Store(self.temp.name)
    self.pid = self.store.create_project("Files", "Preserve originals")
    self.files = Artifacts(self.store)

  def test_files_scoped_versioned_and_integrity_checked(self):
    one = self.files.save(self.pid, "source.txt", b"original", "text/plain")
    two = self.files.save(self.pid, "source.txt", b"revision", "text/plain")
    self.assertNotEqual(one["id"], two["id"])
    self.assertEqual(self.files.read(self.pid, one["id"])[1], b"original")
    self.assertEqual(self.files.read_text(self.pid, two["id"])["text"], "revision")
    other = self.store.create_project("Private", "Another workspace")
    with self.assertRaises(Refused):
      self.files.read(other, one["id"])
    path = Path(self.temp.name) / "artifacts" / self.pid / one["sha256"]
    path.write_bytes(b"tampered")
    with self.assertRaises(Refused):
      self.files.read(self.pid, one["id"])

  def test_file_names_and_binary_readers_are_bounded(self):
    for name in ["../secrets", "/etc/passwd", "folder/file.txt", "x\nheader"]:
      with self.assertRaises(Refused):
        self.files.save(self.pid, name, b"test")
    source = self.files.save(self.pid, "source.pdf", b"%PDF-fixture", "application/pdf")
    with self.assertRaises(Refused):
      self.files.read_text(self.pid, source["id"])

  def test_concurrent_file_records_deduplicate(self):
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
      rows = list(
        pool.map(
          lambda _: self.files.save(self.pid, "x.txt", b"data", "text/plain"),
          range(4),
        )
      )
    self.assertEqual(len({r["id"] for r in rows}), 1)

  def test_migration_preserves_legacy_tasks_and_evidence(self):
    tid = self.store.create_task(self.pid, "Work", "codex", "legacy")
    eid = self.store.add_evidence(self.pid, "https://example.com", "Legacy source")
    with self.store.db() as db:
      for table, columns in {
        "tasks": ["continuation", "seen_action_version", "action_version"],
        "claims": ["task_id", "run_id"],
        "evidence": ["run_id"],
      }.items():
        for column in columns:
          db.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
      db.execute("PRAGMA user_version=0")
    upgraded = Store(self.temp.name)
    self.assertEqual(upgraded.task(tid)["continuation"], 0)
    self.assertEqual(upgraded.get_evidence(self.pid, eid)["excerpt"], "Legacy source")
