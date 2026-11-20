"""Immutable local files with project/run provenance and authenticated retrieval."""

import hashlib
import os
import tempfile
from pathlib import Path

from .store import Refused, bounded, identity


class Artifacts:
  def __init__(self, store):
    self.store = store

  def save(
    self,
    pid,
    name,
    data,
    media_type="application/octet-stream",
    task_id=None,
    run_id=None,
  ):
    name = bounded(name, "file name", 200)
    if (
      Path(name).name != name
      or name in {".", ".."}
      or "\\" in name
      or any(ord(c) < 32 for c in name)
    ):
      raise Refused("Use a file name without directories or control characters")
    if not isinstance(data, bytes) or not 0 < len(data) <= 5_000_000:
      raise Refused("Files must contain between 1 byte and 5 MB")
    if media_type not in {
      "text/plain",
      "text/markdown",
      "text/csv",
      "application/json",
      "application/pdf",
      "image/png",
      "image/jpeg",
      "application/octet-stream",
    }:
      raise Refused("Unsupported file type")
    checksum = hashlib.sha256(data).hexdigest()
    with self.store.db() as db:
      db.execute("BEGIN IMMEDIATE")
      self.store.require_project(db, pid)
      if (
        task_id
        and not db.execute(
          "SELECT 1 FROM tasks WHERE id=? AND project_id=?", (task_id, pid)
        ).fetchone()
      ):
        raise Refused("Task is outside this project")
      existing = db.execute(
        "SELECT * FROM artifacts WHERE project_id=? AND name=? AND sha256=?",
        (pid, name, checksum),
      ).fetchone()
      if existing:
        return dict(existing)
      directory = self.store.root / "artifacts" / pid
      directory.mkdir(parents=True, exist_ok=True, mode=0o700)
      path = directory / checksum
      fd, temporary = tempfile.mkstemp(prefix="upload-", dir=directory)
      try:
        with os.fdopen(fd, "wb") as stream:
          stream.write(data)
          stream.flush()
          os.fsync(stream.fileno())
        os.replace(temporary, path)
      finally:
        Path(temporary).unlink(missing_ok=True)
      aid = identity()
      db.execute(
        "INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?,?)",
        (
          aid,
          pid,
          task_id,
          run_id,
          name,
          media_type,
          checksum,
          len(data),
          self.store.clock(),
        ),
      )
      self.store.event(db, pid, "artifact.saved", {"id": aid, "sha256": checksum})
      result = dict(db.execute("SELECT * FROM artifacts WHERE id=?", (aid,)).fetchone())
    self.store.notify()
    return result

  def read(self, pid, aid):
    with self.store.db() as db:
      row = db.execute(
        "SELECT * FROM artifacts WHERE id=? AND project_id=?", (aid, pid)
      ).fetchone()
    if not row:
      raise Refused("File not found in this project")
    data = (self.store.root / "artifacts" / pid / row["sha256"]).read_bytes()
    if hashlib.sha256(data).hexdigest() != row["sha256"]:
      raise Refused("Stored file integrity check failed")
    return dict(row), data

  def list(self, pid):
    with self.store.db() as db:
      self.store.require_project(db, pid)
      return [
        dict(r)
        for r in db.execute(
          "SELECT * FROM artifacts WHERE project_id=? ORDER BY created DESC",
          (pid,),
        )
      ]

  def read_text(self, pid, aid, offset=0, limit=12000):
    from .store import integer

    integer(offset, "offset", 0, 5_000_000)
    integer(limit, "limit", 1, 20000)
    meta, data = self.read(pid, aid)
    if meta["media_type"] not in {
      "text/plain",
      "text/markdown",
      "text/csv",
      "application/json",
    }:
      raise Refused(
        "This file needs a qualified document/image reader; no paid OCR fallback is configured"
      )
    try:
      value = data.decode("utf-8")
    except UnicodeDecodeError:
      raise Refused("Text file is not UTF-8") from None
    return {
      "untrusted": True,
      "file": meta,
      "text": value[offset : offset + limit],
      "next_offset": offset + limit if offset + limit < len(value) else None,
    }
