"""Real local UI test. No provider calls or live Integration requests."""

import json
import tempfile
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

from veos_runtime.memory import Memory
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
        assert page.locator("#voice-start").is_disabled()
        page.get_by_text("Application and device access", exact=True).click()
        permission = page.locator("#desktop-capabilities .card").filter(
          has_text="read document"
        )
        permission.get_by_role("button", name="Allow access", exact=True).click()
        permission.get_by_role("button", name="Remove access", exact=True).wait_for()
        page.get_by_text("Application and device access", exact=True).click()
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
        memory = Memory(store)
        panel = page.locator("#memory")
        page.get_by_role("button", name="Add memory").click()
        page.get_by_label("Memory kind").select_option("operating_rule")
        page.get_by_label("Remember", exact=True).fill(
          "Draft outreach for my approval; never send it."
        )
        page.get_by_role("button", name="Save memory").click()
        panel.locator(".card .badge.active").wait_for()
        # An agent's proposal arrives live and takes effect only when accepted.
        agent = {"task_id": tid, "run_id": None, "agent": True}
        proposal = memory.remember(
          "procedural",
          "retry_lesson",
          "Deduplicate patent families before counting filings.",
          pid,
          **agent,
        )
        proposed = panel.locator(".card").filter(has_text="Deduplicate patent families")
        proposed.locator(".badge.proposed").wait_for()
        assert memory.context(pid)["procedural"] == []
        proposed.get_by_role("button", name="Accept", exact=True).click()
        proposed.locator(".badge.active").wait_for()
        assert [m["id"] for m in memory.context(pid)["procedural"]] == [proposal["id"]]
        rule = panel.locator(".card").filter(has_text="Draft outreach")
        rule.get_by_role("button", name="Revise", exact=True).click()
        assert page.get_by_label("Memory kind").is_disabled()
        page.get_by_label("Remember", exact=True).fill(
          "Draft outreach for my approval; never send or schedule it."
        )
        page.get_by_role("button", name="Save memory").click()
        panel.get_by_text("never send or schedule it").wait_for()
        assert [m["state"] for m in memory.listing(scope="executive")] == [
          "superseded",
          "active",
        ]
        known = memory.resolve_entity(pid, "person", "Wei Zhang")
        memory.resolve_entity(pid, "person", "Wei Zhang")
        unsettled = panel.locator(".card").filter(has_text="Entity · person")
        unsettled.get_by_role("button", name="Same as Wei Zhang").click()
        unsettled.wait_for(state="detached")
        assert [
          e["id"] for e in store.snapshot(pid)["entities"] if e["state"] == "active"
        ] == [known["id"]]
        claim = store.snapshot(pid)["claims"][0]
        memory.revise_claim(pid, claim["id"], "First estimate", 70)
        refuting = store.add_evidence(
          pid, "https://example.org/contract-terms", "Illustrative contrary source."
        )
        memory.revise_claim(
          pid, claim["id"], "Contract terms contradict it", 40, contradicting=[refuting]
        )
        page.locator("#claims").get_by_text(
          "Confidence 40% · lowered from 70%"
        ).wait_for()
        # A goal an agent writes waits for the owner; a failed experiment stays.
        goal = memory.save_record(pid, "goal", "Qualify three manufacturers", **agent)
        memory.save_record(
          pid,
          "experiment",
          "Pilot batch at 10 L",
          {"protocol": "Microfluidic mixing", "failures": ["Aggregation after 2 h"]},
          "failed",
        )
        proposed_goal = panel.locator(".card").filter(
          has_text="Qualify three manufacturers"
        )
        proposed_goal.locator(".badge.proposed").wait_for()
        assert memory.context(pid)["goals"] == []
        proposed_goal.get_by_role("button", name="Accept", exact=True).click()
        proposed_goal.locator(".badge.active").wait_for()
        assert [g["id"] for g in memory.context(pid)["goals"]] == [goal["id"]]
        panel.locator(".card").filter(has_text="Pilot batch at 10 L").locator(
          ".badge.failed"
        ).wait_for()
        store.add_claim(
          pid,
          "The partner is preparing scale-up.",
          "hypothesis",
          [eid],
          detail={
            "alternatives": ["Routine maintenance hiring"],
            "falsification": "No pilot-plant filing within two quarters",
          },
        )
        page.locator("#claims").get_by_text(
          "Would be disproved by: No pilot-plant filing within two quarters"
        ).wait_for()
        # Two feeds name a company no project follows; only the owner opens one.
        acme = {
          "kind": "organization",
          "name": "Acme Therapeutics",
          "identifiers": ["domain:acme.example"],
        }
        assert page.locator("#inbox-panel").is_hidden()
        memory.ingest_signal("patent", "Acme files a lipid application.", [acme])
        waiting = page.locator("#inbox .card").filter(has_text="Acme files")
        waiting.wait_for()
        assert waiting.get_by_role("button", name="Open a project").count() == 0
        memory.ingest_signal("hiring", "Acme hires a formulation lead.", [acme])
        waiting.locator(".badge.ready").wait_for()
        page.locator("#inbox-panel").scroll_into_view_if_needed()
        page.locator("#inbox-panel").screenshot(
          path=str(artifacts / "mission-desk-inbox.png")
        )
        projects = len(store.snapshot()["projects"])
        waiting.get_by_role("button", name="Open a project").click()
        assert (
          page.get_by_label("Name", exact=True).input_value() == "Acme Therapeutics"
        )
        page.get_by_label("Objective", exact=True).fill("Invent, partner or wait.")
        page.get_by_role("button", name="Create project", exact=True).click()
        page.get_by_role("heading", name="Acme Therapeutics").wait_for()
        page.locator("#inbox-panel").wait_for(state="hidden")
        panel.locator(".card").filter(
          has_text="Signals from hiring, patent name Acme Therapeutics"
        ).get_by_text("Recorded by VeOS from signals").wait_for()
        assert len(store.snapshot()["projects"]) == projects + 1
        assert memory.inbox()["signals"] == []
        panel.locator("xpath=..").screenshot(
          path=str(artifacts / "mission-desk-signals.png")
        )
        page.locator("#projects").get_by_role(
          "button", name="A better path to market"
        ).click()
        page.get_by_role("heading", name="A better path to market").wait_for()
        panel.scroll_into_view_if_needed()
        panel.locator("xpath=..").screenshot(
          path=str(artifacts / "mission-desk-memory.png")
        )
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
        page.evaluate("window.scrollTo(0,0)")
        page.screenshot(path=str(artifacts / "mission-desk.png"), full_page=True)
        page.get_by_role("button", name="Take control", exact=True).click()
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
        page.evaluate("window.scrollTo(0,0)")
        page.screenshot(path=str(artifacts / "mission-desk-mobile.png"))
        page.get_by_role(
          "heading", name="Desktop & settings", exact=True
        ).scroll_into_view_if_needed()
        page.screenshot(path=str(artifacts / "mission-desk-mobile-controls.png"))
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
                "memory add, proposal acceptance and revision",
                "ambiguous entity merge",
                "lowered claim confidence",
                "proposed goal acceptance and failed experiment",
                "hypothesis alternatives and falsification",
                "signal inbox to owner-opened project with correlation",
                "file upload/download",
                "live events",
                "pause/resume",
                "export",
                "reload session",
                "mobile layout",
                "no JS errors",
                "desktop permission grant",
                "take control pauses the project",
                "API voice disabled without configuration",
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
