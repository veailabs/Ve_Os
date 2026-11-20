"""Exercise AT-SPI against a real disposable GTK application on Linux CI."""

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def document_readers():
  from veos_runtime.artifacts import Artifacts
  from veos_runtime.controls import Controls
  from veos_runtime.store import Store

  with tempfile.TemporaryDirectory(prefix="veos-document-test-") as directory:
    store = Store(directory)
    pid = store.create_project("Documents", "Read disposable fixtures")
    content = b"BT /F1 24 Tf 50 700 Td (VEOS PDF EVIDENCE) Tj ET"
    objects = [
      b"<< /Type /Catalog /Pages 2 0 R >>",
      b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
      b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
      b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
      b"<< /Length "
      + str(len(content)).encode()
      + b" >>\nstream\n"
      + content
      + b"\nendstream",
    ]
    pdf = b"%PDF-1.4\n"
    offsets = []
    for i, body in enumerate(objects, 1):
      offsets.append(len(pdf))
      pdf += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(pdf)
    pdf += b"xref\n0 6\n0000000000 65535 f \n"
    pdf += b"".join(f"{offset:010} 00000 n \n".encode() for offset in offsets)
    pdf += f"trailer << /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    meta = Artifacts(store).save(pid, "download.pdf", pdf)
    result = Controls(store).invoke(pid, "read_document", {"artifact_id": meta["id"]})
    assert "VEOS PDF EVIDENCE" in result["result"]["text"], result
    image_path = Path(directory) / "ocr.png"
    subprocess.run(
      [
        "/usr/bin/python3",
        "-c",
        "from PIL import Image, ImageDraw, ImageFont; import sys; im=Image.new('RGB',(600,120),'white'); ImageDraw.Draw(im).text((25,25),'VEOS IMAGE EVIDENCE',font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',32),fill='black'); im.save(sys.argv[1])",
        str(image_path),
      ],
      check=True,
      timeout=10,
    )
    meta = Artifacts(store).save(pid, "image.png", image_path.read_bytes())
    result = Controls(store).invoke(pid, "read_document", {"artifact_id": meta["id"]})
    assert "VEOS IMAGE EVIDENCE" in result["result"]["text"], result
    print(
      json.dumps(
        {
          "result": "passed",
          "checks": [
            "real Poppler PDF extraction",
            "real Tesseract image OCR",
            "download MIME detection",
          ],
        }
      )
    )


def fixture():
  import gi

  gi.require_version("Gtk", "3.0")
  from gi.repository import Gtk

  window = Gtk.Window(title="VeOS accessibility fixture")
  box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
  entry = Gtk.Entry()
  entry.get_accessible().set_name("Message")
  entry.set_text("Initial")
  password = Gtk.Entry()
  password.set_visibility(False)
  password.set_text("NEVER_EXPOSE_NATIVE_SECRET")
  password.get_accessible().set_name("Password")
  button = Gtk.Button(label="Apply fixture")
  button.connect("clicked", lambda _: entry.set_text("Applied"))
  administration = Gtk.Button(label="Reveal protected window")
  administration.connect("clicked", lambda _: window.set_title("VeOS · Mission Desk"))
  for widget in (entry, password, button, administration):
    box.pack_start(widget, True, True, 0)
  window.add(box)
  window.connect("destroy", Gtk.main_quit)
  window.show_all()
  Gtk.main()


def main():
  document_readers()
  adapter = (
    Path(__file__).resolve().parents[1] / "src/veos_runtime/native_accessibility.py"
  )
  process = subprocess.Popen(
    ["/usr/bin/python3", __file__, "--fixture"], stdout=subprocess.DEVNULL
  )
  try:

    def call(operation, **args):
      response = subprocess.run(
        [
          "/usr/bin/python3",
          "-c",
          "import runpy,sys,json; module=runpy.run_path(sys.argv[1]); request=json.loads(sys.stdin.read()); print(json.dumps(module['execute'](request['operation'],request['args'])))",
          str(adapter),
        ],
        input=json.dumps(
          {"operation": operation, "args": {"app_pid": process.pid, **args}}
        ),
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
      )
      # Fixture-only traceback: production's CLI intentionally sanitizes errors.
      assert response.returncode == 0, response.stderr
      return json.loads(response.stdout)

    deadline = time.monotonic() + 20
    snapshot = None
    while time.monotonic() < deadline:
      snapshot = call("native_read")
      if any(node["name"] == "Message" for node in snapshot.get("nodes", [])):
        break
      time.sleep(0.2)
    assert "error" not in snapshot, snapshot
    assert "NEVER_EXPOSE_NATIVE_SECRET" not in json.dumps(snapshot)
    entry = next(n for n in snapshot["nodes"] if n["name"] == "Message")
    filled = call(
      "native_fill", node=entry["id"], revision=snapshot["revision"], text="Edited"
    )
    assert filled.get("verified") is True, filled
    stale = call(
      "native_fill", node=entry["id"], revision=snapshot["revision"], text="Stale write"
    )
    assert "error" in stale, stale
    snapshot = call("native_read")
    button = next(n for n in snapshot["nodes"] if n["name"] == "Apply fixture")
    clicked = call(
      "native_action",
      node=button["id"],
      revision=snapshot["revision"],
      action=button["actions"][0],
    )
    assert clicked.get("input_completed") is True, clicked
    assert clicked["verified"] is False
    snapshot = call("native_read")
    assert (
      next(n for n in snapshot["nodes"] if n["name"] == "Message")["text"] == "Applied"
    )
    secret = next(n for n in snapshot["nodes"] if n["name"] == "Password")
    assert "error" in call(
      "native_fill", node=secret["id"], revision=snapshot["revision"], text="blocked"
    )
    button = next(
      n for n in snapshot["nodes"] if n["name"] == "Reveal protected window"
    )
    assert call(
      "native_action",
      node=button["id"],
      revision=snapshot["revision"],
      action=button["actions"][0],
    )["input_completed"]
    assert "error" in call("native_read")
    print(
      json.dumps(
        {
          "result": "passed",
          "checks": [
            "real GTK AT-SPI tree",
            "password omission",
            "editable field read-back",
            "stale revision refused",
            "native action and separate outcome observation",
            "manual password boundary",
          ],
        }
      )
    )
  finally:
    process.terminate()
    process.wait(timeout=5)


if __name__ == "__main__":
  if "--fixture" in sys.argv:
    fixture()
  else:
    main()
