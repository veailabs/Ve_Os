"""Exercise the real local browser against a disposable HTTP app, without an LLM."""

import json
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from veos_runtime.browser import BrowserActions, LocalBrowser
from veos_runtime.store import Refused, Store


class Site(BaseHTTPRequestHandler):
  def log_message(self, *_):
    pass

  def do_GET(self):
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
