import json
import os
from pathlib import Path
from unittest.mock import patch

from test_runtime import Fixture

from veos_runtime.artifacts import Artifacts
from veos_runtime.controls import Controls
from veos_runtime.desktop_actions import DesktopActions
from veos_runtime.store import Refused


class DesktopTests(Fixture):
  def setUp(self):
    super().setUp()
    self.controls = Controls(self.store)
    self.actions = DesktopActions(self.store, self.controls)
    self.tid = self.task()
    self.store.claim(self.tid)

  def test_capability_revoked_between_proposal_and_approval(self):
    self.actions.grant(self.pid, "clipboard_write", True)
    action = self.actions.call(self.pid, self.tid, "clipboard_write", {"text": "hello"})
    self.actions.grant(self.pid, "clipboard_write", False)
    with patch.object(self.controls, "invoke") as invoke:
      self.assertEqual(
        self.actions.decide(action["action_id"], True)["state"], "failed"
      )
      invoke.assert_not_called()

  def test_exact_review_idempotency_and_unknown_completion(self):
    self.actions.grant(self.pid, "clipboard_write", True)
    args = {"text": "hello"}
    action = self.actions.call(self.pid, self.tid, "clipboard_write", args, "first")
    self.assertEqual(
      action["action_id"],
      self.actions.call(self.pid, self.tid, "clipboard_write", args, "first")[
        "action_id"
      ],
    )
    with self.assertRaises(Refused):
      self.actions.call(
        self.pid, self.tid, "clipboard_write", {"text": "different"}, "first"
      )
    with patch.object(self.controls, "invoke", side_effect=TimeoutError) as invoke:
      self.assertEqual(
        self.actions.decide(action["action_id"], True)["state"], "unknown"
      )
      with self.assertRaises(Refused):
        self.actions.decide(action["action_id"], True)
      self.assertEqual(invoke.call_count, 1)
    with self.assertRaises(Refused):
      self.actions.call(self.pid, self.tid, "clipboard_write", args, "another")

  def test_changed_config_is_not_written(self):
    with patch.dict(os.environ, {"XDG_CONFIG_HOME": self.temp.name}):
      path = Path(self.temp.name) / "hypr/looknfeel.lua"
      path.parent.mkdir()
      path.write_text("-- original")
      self.actions.grant(self.pid, "configure", True)
      action = self.actions.call(
        self.pid,
        self.tid,
        "configure",
        {"area": "appearance", "values": {"rounding": 8}},
      )
      path.write_text("-- user changed the config")
      self.assertEqual(
        self.actions.decide(action["action_id"], True)["state"], "failed"
      )
      self.assertEqual(path.read_text(), "-- user changed the config")

  def test_settings_preserve_previous_overrides_and_rollback(self):
    with patch.dict(os.environ, {"XDG_CONFIG_HOME": self.temp.name}):
      path = Path(self.temp.name) / "hypr/looknfeel.lua"
      path.parent.mkdir()
      original = "-- Personal config\nhl.config({general={gaps_in=3}})\n"
      path.write_text(original)
      with patch.object(self.controls, "run", return_value=b""):
        first = self.controls.save_configuration(
          {"area": "appearance", "values": {"rounding": 8}}
        )
        self.controls.save_configuration(
          {"area": "appearance", "values": {"gaps_in": 5}}
        )
        self.controls.save_configuration(
          {"area": "appearance", "values": {"rounding": 10}}
        )
      after = path.read_text()
      self.assertIn(original.rstrip(), after)
      self.assertIn('["rounding"] = 10', after)
      self.assertNotIn('["rounding"] = 8', after)
      self.assertIn('["gaps_in"] = 5', after)
      self.assertEqual(Path(first["backup"]).read_text(), original)
      with patch.object(
        self.controls, "run", side_effect=[b"ok", b"invalid config", b"ok"]
      ):
        with self.assertRaises(Refused):
          self.controls.save_configuration(
            {"area": "appearance", "values": {"rounding": 12}}
          )
      self.assertEqual(path.read_text(), after)

  def test_typed_controls_reject_code_and_out_of_range_values(self):
    for op, args in [
      ("configure", {"area": "input", "values": {"sensitivity": float("nan")}}),
      (
        "configure",
        {"area": "appearance", "values": {"rounding": "1);os.execute('bad')"}},
      ),
      ("configure", {"area": "appearance", "values": {"rounding": True}}),
      ("focus_window", {"address": '0x1"; bad'}),
      ("set_audio", {"volume": 101}),
      ("set_audio", {}),
      ("native_action", {"app_pid": 1}),
      ("shell", {"command": "echo bad"}),
      ("read_document", {"artifact_id": "id", "offset": -1}),
    ]:
      with self.subTest(op=op, args=args), self.assertRaises(Refused):
        self.controls.validate(op, args)

  def test_document_read_uses_immutable_project_files_and_paging(self):
    meta = Artifacts(self.store).save(
      self.pid, "note.txt", ("abcd" * 5000).encode(), "text/plain"
    )
    self.actions.grant(self.pid, "read_document", True)
    result = self.actions.call(
      self.pid, self.tid, "read_document", {"artifact_id": meta["id"]}
    )
    self.assertIn("abcd", json.dumps(result))
    other = self.store.create_project("Other", "Other files")
    self.actions.grant(other, "read_document", True)
    with self.assertRaises(Refused):
      self.actions.call(other, self.tid, "read_document", {"artifact_id": meta["id"]})
