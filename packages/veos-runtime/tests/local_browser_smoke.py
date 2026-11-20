"""Exercise the real local browser against a disposable HTTP app, without an LLM."""

import json
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from veos_runtime.artifacts import Artifacts
from veos_runtime.browser import BrowserActions, LocalBrowser
from veos_runtime.store import Refused, Store


class Site(BaseHTTPRequestHandler):
  def log_message(self, *_):
    pass

  def do_GET(self):
    if self.path == "/download":
      data = b"original download bytes"
      self.send_response(200)
      self.send_header("Content-Disposition", 'attachment; filename="example.txt"')
      self.send_header("Content-Length", str(len(data)))
      self.end_headers()
      self.wfile.write(data)
      return
    if self.path == "/capture":
      content = b"<!doctype html><h1>Disposable screenshot fixture</h1>"
    elif self.path == "/controls":
      content = b"""<!doctype html><title>Control regression fixture</title>
      <span id=clock></span><form onsubmit="return false">
      <label>Name<input id=name value="prefilled"></label>
      <label>Enabled<input id=toggle type=checkbox></label>
      <label>Choice<select><option value=a>Alpha</option><option value=b>Beta</option></select></label>
      <span id=editor-label>Rich message</span><div contenteditable=true aria-labelledby=editor-label>Original</div>
      <div role=checkbox aria-label="Custom toggle" aria-checked=false onclick="this.setAttribute('aria-checked',this.getAttribute('aria-checked')!=='true')">Toggle</div>
      <button type=button onclick="if(confirm('Save this fixture?'))document.querySelector('#result').textContent='CONFIRMED'">Confirm save</button>
      <label>Upload<input type=file></label><a href=/download download>Download fixture</a>
      <a href=/popup target=_blank>Open popup</a><div id=result></div></form>
      <div role=button draggable=true ondragstart="event.dataTransfer.setData('text/plain','fixture')">Drag source</div>
      <div role=button ondragover="event.preventDefault()" ondrop="event.preventDefault();this.textContent='Dropped fixture'">Drop target</div>
      <iframe srcdoc="<label>Frame input<input value='inside'></label>"></iframe>
      <div id=shadow></div><div id=many></div><p id=long></p>
      <script>
      shadow.attachShadow({mode:'open'}).innerHTML='<button>Shadow button</button><span style="display:none">SHADOW_SECRET</span>';
      for(let i=0;i<180;i++){const b=document.createElement('button');b.textContent='More '+i;many.appendChild(b);}
      long.textContent='Long text '.repeat(4000)+'END_MARKER';
      setInterval(()=>clock.textContent=Date.now(),100);
      </script>"""
    else:
      content = b"""<!doctype html><title>VeOS browser fixture</title>
        <h1>Local fixture, no real account</h1>
        <label>Message<input id=message></label>
        <label>Password<input type=password value=NEVER_EXPOSE_THIS></label>
        <button id=save onclick="localStorage.setItem('message',document.querySelector('#message').value);document.querySelector('#result').textContent='Saved: '+localStorage.getItem('message')">Save locally</button>
        <p id=result></p><script>document.querySelector('#result').textContent=localStorage.getItem('message')||'Nothing saved';</script>"""
    self.send_response(200)
    self.send_header("Content-Type", "text/html")
    self.send_header("Content-Length", str(len(content)))
    self.end_headers()
    self.wfile.write(content)


def refused(fn):
  try:
    fn()
  except Refused:
    return
  raise AssertionError("Expected operation to be refused")


def extended_controls(store, pid, browser, actions, url):
  tid = store.create_task(pid, "Exercise semantic controls", "codex", "controls")
  store.claim(tid)
  browser.invoke(pid, "open", {"url": url + "controls"})

  def observe(**args):
    return browser.invoke(pid, "read", args)

  def act(operation, name=None, **args):
    reading = {
      key: args[key] for key in ("frame_id", "control_offset", "query") if key in args
    }
    snap = observe(**reading)
    if name:
      args["element_id"] = next(
        c["id"] for c in snap["snapshot"]["controls"] if c["name"] == name
      )
    args["snapshot_id"] = snap["snapshot_id"]
    aid = actions.call(pid, tid, operation, args)["action_id"]
    result = actions.decide(aid, True)
    assert result["state"] == "succeeded", (operation, result, store.task_actions(tid))
    return store.task_actions(tid)[-1]["result"]

  snap = observe()
  assert (
    next(c for c in snap["snapshot"]["controls"] if c["name"] == "Name")["value"]
    == "prefilled"
  )
  time.sleep(0.25)  # Exercise an unrelated live clock changing between observations.
  assert observe()["snapshot_id"] == snap["snapshot_id"]
  act("fill", "Name", text="")
  assert (
    next(c for c in observe()["snapshot"]["controls"] if c["name"] == "Name")["value"]
    == ""
  )
  before = observe()["snapshot_id"]
  act("set_checked", "Enabled", checked=True)
  checked = observe()
  assert checked["snapshot_id"] != before
  assert (
    next(c for c in checked["snapshot"]["controls"] if c["name"] == "Enabled")[
      "checked"
    ]
    is True
  )
  act("set_checked", "Enabled", checked=False)
  act("select_option", "Choice", value="b")
  assert (
    next(c for c in observe()["snapshot"]["controls"] if c["name"] == "Choice")["value"]
    == "b"
  )
  act("fill", "Rich message", text="Rich replacement")
  act("click", "Custom toggle")
  assert (
    next(c for c in observe()["snapshot"]["controls"] if c["name"] == "Custom toggle")[
      "checked"
    ]
    == "true"
  )
  act("click", "Shadow button", query="Shadow button")
  assert "SHADOW_SECRET" not in observe()["snapshot"]["text"]
  second = observe(control_offset=100)
  assert any(c["name"] == "More 179" for c in second["snapshot"]["controls"])
  act("click", "More 179", query="More 179")
  act("scroll", "More 179", query="More 179")
  target = next(
    c["id"] for c in observe()["snapshot"]["controls"] if c["name"] == "Drop target"
  )
  act("drag", "Drag source", target_id=target)
  assert "Dropped fixture" in observe()["snapshot"]["text"]
  final_text = observe(text_offset=35000)
  assert "END_MARKER" in final_text["snapshot"]["text"]
  frame = next(f for f in observe()["snapshot"]["frames"] if not f["main"])
  act("fill", "Frame input", text="Updated frame", frame_id=frame["id"])
  assert (
    observe(frame_id=frame["id"])["snapshot"]["controls"][0]["value"] == "Updated frame"
  )
  act("click", "Confirm save")
  dialog = observe()["snapshot"]["dialog"]
  assert dialog["message"] == "Save this fixture?"
  act("dialog", **dialog, accept=True)
  assert "CONFIRMED" in observe()["snapshot"]["text"]
  meta = Artifacts(store).save(pid, "upload.txt", b"immutable file", "text/plain")
  act("upload", "Upload", artifact_id=meta["id"], sha256=meta["sha256"])
  act("download", "Download fixture")
  download = next(
    a["result"]["artifact"]
    for a in store.task_actions(tid)
    if a["tool"] == "browser.download"
  )
  assert Artifacts(store).read(pid, download["id"])[1] == b"original download bytes"
  original = observe()["snapshot"]["tab_id"]
  act("click", "Open popup")
  popup = next(t for t in observe()["snapshot"]["tabs"] if t["id"] != original)
  switched = browser.invoke(pid, "switch_tab", {"tab_id": popup["id"]})
  assert switched["snapshot"]["tab_id"] == popup["id"]
  browser.invoke(pid, "switch_tab", {"tab_id": original})
  browser.invoke(pid, "open", {"url": url + "capture"})
  act("screenshot")
  capture = next(
    a["result"]["artifact"]
    for a in store.task_actions(tid)
    if a["tool"] == "browser.screenshot"
  )
  assert Artifacts(store).read(pid, capture["id"])[1].startswith(b"\x89PNG")
  print(
    json.dumps(
      {
        "extended_controls": "passed",
        "checks": [
          "empty edit",
          "checkbox state",
          "select option",
          "rich text",
          "custom checkbox",
          "shadow DOM",
          "pagination",
          "iframe",
          "clock stability",
          "explicit dialog",
          "immutable upload",
          "original download",
          "tab switch",
          "scroll",
          "drag and drop",
          "screenshot artifact",
        ],
      }
    )
  )


def main():
  site = ThreadingHTTPServer(("127.0.0.1", 0), Site)
  thread = threading.Thread(target=site.serve_forever, daemon=True)
  thread.start()
  url = f"http://127.0.0.1:{site.server_port}/"
  chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
  if chrome.exists():
    executable = str(chrome)
  else:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
      executable = playwright.chromium.executable_path
  with tempfile.TemporaryDirectory(prefix="veos-browser-test-") as directory:
    store = Store(directory)
    pid = store.create_project("Browser test", "Only use the local fixture")
    browser = LocalBrowser(store, headless=True, executable=executable)
    actions = BrowserActions(store, browser)
    try:
      refused(lambda: browser.invoke(pid, "open", {"url": url}))
      browser.forbidden_origin = "http://127.0.0.1:8764"
      refused(lambda: browser.grant(pid, "http://127.0.0.1:8764"))
      browser.grant(pid, url)
      snap = browser.invoke(pid, "open", {"url": url})
      assert "NEVER_EXPOSE_THIS" not in json.dumps(snap)
      tid = store.create_task(pid, "Save a local fixture value", "codex", "test")
      lease = store.claim(tid)
      field = next(c for c in snap["snapshot"]["controls"] if c["name"] == "Message")
      secret = next(c for c in snap["snapshot"]["controls"] if c["sensitive"])
      refused(
        lambda: actions.call(
          pid,
          tid,
          "fill",
          {
            "element_id": secret["id"],
            "snapshot_id": snap["snapshot_id"],
            "text": "must not enter",
          },
        )
      )
      aid = actions.call(
        pid,
        tid,
        "fill",
        {
          "element_id": field["id"],
          "snapshot_id": snap["snapshot_id"],
          "text": "persistent fixture",
        },
      )["action_id"]
      store.finish(tid, lease, "succeeded", "Waiting for approval")
      assert store.task(tid)["state"] == "waiting_approval"
      assert actions.decide(aid, True)["state"] == "succeeded"
      refused(lambda: actions.decide(aid, True))
      lease = store.claim(tid)
      snap = browser.invoke(pid, "read", {})
      button = next(
        c for c in snap["snapshot"]["controls"] if c["name"] == "Save locally"
      )
      aid = actions.call(
        pid,
        tid,
        "click",
        {"element_id": button["id"], "snapshot_id": snap["snapshot_id"]},
      )["action_id"]
      assert "Saved:" not in browser.invoke(pid, "read", {})["snapshot"]["text"]
      store.finish(tid, lease, "succeeded", "Waiting for save")
      assert actions.decide(aid, True)["state"] == "succeeded"
      assert (
        "Saved: persistent fixture"
        in browser.invoke(pid, "read", {})["snapshot"]["text"]
      )
      lease = store.claim(tid)
      store.finish(tid, lease, "succeeded", "Verified the website's saved value")
      assert store.task(tid)["state"] == "succeeded"
      # User edits after approval was proposed must invalidate the exact click.
      stale_task = store.create_task(pid, "Test stale input binding", "codex", "stale")
      store.claim(stale_task)
      snap = browser.invoke(pid, "read", {})
      button = next(
        c for c in snap["snapshot"]["controls"] if c["name"] == "Save locally"
      )
      stale_action = actions.call(
        pid,
        stale_task,
        "click",
        {"element_id": button["id"], "snapshot_id": snap["snapshot_id"]},
      )["action_id"]
      field = next(c for c in snap["snapshot"]["controls"] if c["name"] == "Message")
      browser.invoke(
        pid,
        "fill",
        {
          "element_id": field["id"],
          "snapshot_id": snap["snapshot_id"],
          "text": "user changed the form",
        },
      )
      assert actions.decide(stale_action, True)["state"] == "failed"
      assert (
        "Saved: persistent fixture"
        in browser.invoke(pid, "read", {})["snapshot"]["text"]
      )
      fresh = browser.invoke(pid, "read", {})
      aid = actions.call(
        pid,
        stale_task,
        "fill",
        {
          "element_id": field["id"],
          "snapshot_id": fresh["snapshot_id"],
          "text": "fresh proposal",
        },
      )["action_id"]
      assert actions.decide(aid, True)["state"] == "succeeded"
      assert any(a["state"] == "superseded" for a in store.task_actions(stale_task))
      extended_controls(store, pid, browser, actions, url)
      browser.close()
      browser = LocalBrowser(store, headless=True, executable=executable)
      snap = browser.invoke(pid, "open", {"url": url})
      assert "persistent fixture" in snap["snapshot"]["text"]
      other = store.create_project("Other browser", "Independent profile")
      browser.grant(other, url)
      assert (
        "Nothing saved"
        in browser.invoke(other, "open", {"url": url})["snapshot"]["text"]
      )
      browser.grant(pid, url, False)
      refused(lambda: browser.invoke(pid, "read", {}))
      print(
        json.dumps(
          {
            "result": "passed",
            "checks": [
              "website grants",
              "runtime origin blocked",
              "password omission",
              "pending action has no effect",
              "exact approval",
              "no duplicate execution",
              "receipt continuation",
              "page result verified",
              "profile survives restart",
              "project profile isolation",
              "permission revocation",
              "stale form approval rejected",
            ],
          }
        )
      )
    finally:
      browser.close()
      site.shutdown()
      site.server_close()
      thread.join(timeout=3)


if __name__ == "__main__":
  main()
