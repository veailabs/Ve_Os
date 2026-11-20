"""Real local UI test. No provider calls or live Integration requests."""

import json
import tempfile
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

from veos_runtime.providers import default_config, save_config
from veos_runtime.server import Application, make_server
from veos_runtime.store import Store


def main():
  artifacts = Path(__file__).resolve().parents[1] / "artifacts"
  artifacts.mkdir(exist_ok=True)
  with tempfile.TemporaryDirectory(prefix="veos-ui-test-") as directory:
    store = Store(directory)
    save_config(store.root, default_config())
    app = Application(store)
    server = make_server(app, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
      with sync_playwright() as p:
        chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        browser = p.chromium.launch(
          headless=True,
          **({"executable_path": str(chrome)} if chrome.exists() else {}),
        )
        page = browser.new_page(
          viewport={"width": 1440, "height": 1100}, device_scale_factor=1
        )
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(app.base_url + "/#" + app.token)
        page.get_by_role("button", name="Create your first project").click()
        page.get_by_label("Name", exact=True).fill("A better path to market")
        page.get_by_label("Objective", exact=True).fill(
          "Compare a direct product launch with a manufacturing partnership. Preserve evidence, challenge assumptions, and identify the next experiment."
        )
        page.get_by_role("button", name="Create project", exact=True).click()
        page.get_by_role("heading", name="A better path to market").wait_for()
        page.get_by_role("button", name="Add task").click()
        page.get_by_label("Task", exact=True).fill(
          "Compare the two routes using public sources. Identify the three largest uncertainties."
        )
        page.get_by_role("button", name="Queue task").click()
        page.locator("#tasks .card").wait_for()
        pid = store.snapshot()["projects"][0]["id"]
        tid = store.snapshot()["tasks"][0]["id"]
        page.get_by_role("button", name="Record source", exact=True).click()
        page.get_by_label("Source URL or artifact URN").fill(
          "https://example.org/market-study"
        )
        page.get_by_label("Exact excerpt").fill(
          "Illustrative test source: manufacturing partners may reduce initial capital requirements, while limiting control over delivery schedules."
        )
        page.get_by_role("button", name="Record source", exact=True).last.click()
        page.locator("#evidence .card").wait_for()
        page.get_by_role("button", name="Record claim").click()
        page.get_by_label("Statement", exact=True).fill(
          "A manufacturing partnership may be a lower-capital entry route. Contract terms need validation."
        )
        page.get_by_label("Kind", exact=True).select_option("hypothesis")
        eid = store.snapshot()["evidence"][0]["id"]
        page.get_by_label("Supporting sources").select_option(eid)
        page.get_by_role("button", name="Record assessment").click()
        page.locator("#claims .card").wait_for()
        page.locator("#file-upload").set_input_files(
          {
            "name": "source.txt",
            "mimeType": "text/plain",
            "buffer": b"Local artifact test fixture",
          }
        )
        page.locator("#files .card").wait_for()
        with page.expect_download() as file_download:
          page.get_by_role("button", name="Download file", exact=True).click()
        assert (
          Path(file_download.value.path()).read_bytes()
          == b"Local artifact test fixture"
        )
        lease = store.claim(tid)
        store.finish(
          tid,
          lease,
          "succeeded",
          "UI test fixture — not live research.\n\nThe partner route warrants an initial feasibility conversation. Validate minimum volumes, control of IP and manufacturing lead times before deciding.\n\nNext step: gather comparable quotes from three qualified manufacturers.",
        )
        page.locator("#tasks .badge.succeeded").wait_for()
        page.get_by_text("Read result", exact=True).click()
        page.screenshot(path=str(artifacts / "mission-desk.png"), full_page=True)
        page.get_by_role("button", name="Pause", exact=True).click()
        page.get_by_role("button", name="Resume", exact=True).wait_for()
        page.get_by_role("button", name="Resume", exact=True).click()
        page.get_by_role("button", name="Pause", exact=True).wait_for()
        with page.expect_download() as download:
          page.get_by_role("button", name="Export", exact=True).click()
        path = artifacts / "ui-export-fixture.json"
        download.value.save_as(path)
        assert json.loads(path.read_text())["projects"][0]["id"] == pid
        page.reload()
        page.get_by_role("heading", name="A better path to market").wait_for()
        assert "#" not in page.url
        page.set_viewport_size({"width": 390, "height": 844})
        page.screenshot(path=str(artifacts / "mission-desk-mobile.png"), full_page=True)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (
          "Horizontal overflow"
        )
        assert not errors, errors
        browser.close()
        print(
          json.dumps(
            {
              "result": "passed",
              "checks": [
                "project creation",
                "task creation",
                "evidence",
                "linked hypothesis",
                "file upload/download",
                "live events",
                "pause/resume",
                "export",
                "reload session",
                "mobile layout",
                "no JS errors",
              ],
              "screenshots": str(artifacts),
            }
          )
        )
    finally:
      server.shutdown()
      server.server_close()
      thread.join(timeout=3)


if __name__ == "__main__":
  main()
