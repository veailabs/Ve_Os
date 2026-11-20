"""Bounded AT-SPI adapter, isolated from the runtime by a process timeout.

Uses the distribution's python3/gi bindings in the signed-in Linux session.
"""

import hashlib
import json
import os
import re
import sys
from pathlib import Path


def execute(operation, args):
  import gi

  gi.require_version("Atspi", "2.0")
  from gi.repository import Atspi

  # Native control must not bypass the browser's origin grants or drive the
  # runtime's own approval page through the browser's accessibility tree.
  executable = Path(os.readlink(f"/proc/{args['app_pid']}/exe")).name.lower()
  if re.search(
    r"chrome|chromium|firefox|librewolf|brave|vivaldi|opera|msedge|electron|terminal|alacritty|kitty|ghostty|konsole|xterm|^foot$",
    executable,
  ):
    return {
      "error": "Use the granted project browser for websites. Browser, terminal and runtime administration controls are not available through native accessibility"
    }
  desktop = Atspi.get_desktop(0)
  application = next(
    (
      desktop.get_child_at_index(i)
      for i in range(desktop.get_child_count())
      if desktop.get_child_at_index(i).get_process_id() == args["app_pid"]
    ),
    None,
  )
  if application is None:
    return {"error": "Application is not present on the accessibility bus"}
  rows, objects = [], {}
  queue = [(application, "0")]
  while queue and len(rows) < 1000:
    item, path = queue.pop(0)
    state = item.get_state_set()
    name = item.get_name() or ""
    sensitive = item.get_role() == Atspi.Role.PASSWORD_TEXT or bool(
      re.search(
        r"password|verification.?code|api.?key|access.?token|secret", name, re.I
      )
    )
    row = {
      "id": path,
      "name": name[:300],
      "role": item.get_role_name(),
      "sensitive": sensitive,
      "enabled": state.contains(Atspi.StateType.ENABLED),
      "focused": state.contains(Atspi.StateType.FOCUSED),
      "checked": state.contains(Atspi.StateType.CHECKED),
    }
    if "Mission Desk" in name or item.get_role() == Atspi.Role.DOCUMENT_WEB:
      return {
        "error": "This application exposes browser or VeOS administration controls; use the project browser for permitted websites"
      }
    if not sensitive:
      interfaces = item.get_interfaces()
      action = item.get_action_iface() if "Action" in interfaces else None
      row["actions"] = (
        [action.get_action_name(i) for i in range(action.get_n_actions())]
        if action
        else []
      )
      text = item.get_text_iface() if "Text" in interfaces else None
      if text:
        count = text.get_character_count()
        content = text.get_text(0, min(count, 10000))
        row["text"] = content[:2000]
        row["text_length"] = count
        row["text_truncated"] = count > 2000
        row["text_revision"] = hashlib.sha256(content.encode()).hexdigest()
    rows.append(row)
    objects[path] = item
    for index in range(min(item.get_child_count(), 300)):
      child = item.get_child_at_index(index)
      if child:
        queue.append((child, path + "/" + str(index)))
  revision = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
  if operation == "native_read":
    offset = args.get("offset", 0)
    if type(offset) is not int or offset < 0:
      return {"error": "Invalid observation offset"}
    return {
      "app_pid": args["app_pid"],
      "revision": revision,
      "nodes": rows[offset : offset + 100],
      "next_offset": offset + 100 if offset + 100 < len(rows) else None,
      "truncated": bool(queue) or offset + 100 < len(rows),
    }
  row = next((row for row in rows if row["id"] == args.get("node")), None)
  if queue:
    return {
      "error": "Application exceeds the accessibility scan limit; use its supported API or manual controls"
    }
  if (
    args.get("revision") != revision
    or not row
    or row["sensitive"]
    or not row["enabled"]
    or row.get("text_length", 0) > 10000
  ):
    return {
      "error": "Application state changed or target requires manual input; observe again"
    }
  item = objects[args["node"]]
  if operation == "native_action":
    if args.get("action") not in row.get("actions", []):
      return {"error": "Choose an action advertised by this control"}
    delivered = item.get_action_iface().do_action(row["actions"].index(args["action"]))
    return {
      "input_completed": bool(delivered),
      "verified": False,
      "meaning": "Read the application again to verify its outcome",
    }
  if operation == "native_fill":
    editor = (
      item.get_editable_text_iface()
      if "EditableText" in item.get_interfaces()
      else None
    )
    if not editor:
      return {"error": "This control does not expose editable text"}
    text = args.get("text")
    if not isinstance(text, str) or len(text) > 10000:
      return {"error": "Invalid editable text"}
    delivered = editor.set_text_contents(text)
    reader = item.get_text_iface()
    observed = (
      reader.get_text(0, min(reader.get_character_count(), 10001)) if reader else None
    )
    return {
      "input_completed": bool(delivered),
      "verified": observed == text,
      "meaning": "Editable field read-back; verify the complete task separately",
    }
  return {"error": "Unsupported accessibility operation"}


if __name__ == "__main__":
  try:
    request = json.loads(sys.stdin.read(20000))
    result = execute(request["operation"], request["args"])
  except Exception:  # noqa: BLE001 — No bus details or application secrets in errors.
    result = {
      "error": "Accessibility unavailable; install python-gobject and at-spi2-core and enable the application's accessibility support"
    }
  print(json.dumps(result))
