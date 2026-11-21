"""Local Chromium automation. No cloud browser account or paid API is involved."""

import asyncio
import importlib.util
import json
import os
import shutil
import threading
from pathlib import Path
from urllib.parse import urlsplit

from .browser_observation import SNAPSHOT
from .store import Refused, bounded, digest, encoded, identity, integer


def origin(url):
  parsed = urlsplit(url)
  if (
    parsed.scheme not in {"http", "https"}
    or not parsed.hostname
    or parsed.username
    or parsed.password
  ):
    raise Refused("Use an HTTP or HTTPS website address without credentials")
  host = parsed.hostname.lower()
  if ":" in host:
    host = "[" + host + "]"
  port = parsed.port
  return f"{parsed.scheme}://{host}" + (
    f":{port}" if port and port != {"http": 80, "https": 443}[parsed.scheme] else ""
  )


class LocalBrowser:
  def __init__(self, store, *, headless=False, executable=None):
    self.store, self.headless, self.executable = store, headless, executable
    self.thread = None
    self.lock = threading.Lock()
    self.contexts = {}
    self.pages = {}
    self.tabs = {}
    self.frames = {}
    self.dialogs = {}
    self.download_expected = set()
    self.playwright = None
    self.forbidden_origin = ""
    with store.db() as db:
      db.execute(
        "CREATE TABLE IF NOT EXISTS browser_grants(project_id TEXT NOT NULL REFERENCES projects(id),origin TEXT NOT NULL,created REAL NOT NULL,PRIMARY KEY(project_id,origin))"
      )

  def status(self, pid):
    with self.store.db() as db:
      self.store.require_project(db, pid)
      grants = [
        r[0]
        for r in db.execute(
          "SELECT origin FROM browser_grants WHERE project_id=? ORDER BY origin",
          (pid,),
        )
      ]
    return {
      "available": importlib.util.find_spec("playwright") is not None,
      "origins": grants,
      "location": "this_computer",
      "persistent_profile": True,
    }

  def grant(self, pid, url, allow=True):
    if type(allow) is not bool:
      raise Refused("Allow must be a boolean")
    site = origin(url)
    if site == self.forbidden_origin:
      raise Refused(
        "The runtime's own administration page cannot be controlled by an agent"
      )
    with self.store.db() as db:
      self.store.require_project(db, pid)
      if allow:
        db.execute(
          "INSERT OR IGNORE INTO browser_grants VALUES(?,?,?)",
          (pid, site, self.store.clock()),
        )
      else:
        db.execute(
          "DELETE FROM browser_grants WHERE project_id=? AND origin=?",
          (pid, site),
        )
      self.store.event(
        db, pid, "browser.permission", {"origin": site, "allowed": allow}
      )
    self.store.notify()
    return self.status(pid)

  def check(self, pid, url):
    site = origin(url)
    if site == self.forbidden_origin:
      raise Refused("Runtime administration is not available to browser tools")
    with self.store.db() as db:
      project = self.store.require_project(db, pid)
      if project["state"] != "active":
        raise Refused("Project is not active")
      if not db.execute(
        "SELECT 1 FROM browser_grants WHERE project_id=? AND origin=?",
        (pid, site),
      ).fetchone():
        raise Refused(
          f"Allow this website in the project's browser panel first: {site}"
        )

  def invoke(self, pid, operation, args):
    with self.lock:
      if self.thread is None:
        if importlib.util.find_spec("playwright") is None:
          raise Refused("Install veos-runtime[browser] to enable the local browser")
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.serial = None
      future = asyncio.run_coroutine_threadsafe(
        self._invoke(pid, operation, args), self.loop
      )
    try:
      return future.result(timeout=40)
    except TimeoutError:
      future.cancel()
      raise TimeoutError(
        "Browser operation timed out; inspect its outcome before retrying"
      ) from None

  async def _invoke(self, pid, operation, args):
    if self.serial is None:
      self.serial = asyncio.Lock()
    async with self.serial:
      return await self._perform(pid, operation, args)

  async def _page(self, pid):
    if pid not in self.contexts:
      from playwright.async_api import async_playwright

      if self.playwright is None:
        self.playwright = await async_playwright().start()
      profile = self.store.root / "browser-profiles" / pid
      profile.mkdir(parents=True, exist_ok=True, mode=0o700)
      os.chmod(profile, 0o700)
      options = {
        "headless": self.headless,
        "accept_downloads": True,
        "downloads_path": str(self.store.root / "downloads" / pid),
        "service_workers": "block",
      }
      if self.executable:
        options["executable_path"] = self.executable
      elif binary := (
        shutil.which("google-chrome")
        or shutil.which("chromium")
        or shutil.which("chromium-browser")
      ):
        options["executable_path"] = binary
      else:
        options["channel"] = "chrome"
      context = await self.playwright.chromium.launch_persistent_context(
        str(profile), **options
      )
      context.set_default_timeout(8000)

      async def route(request_route):
        # Navigation needs a website grant. Static subresources may be on CDNs.
        request = request_route.request
        try:
          if origin(request.url) == self.forbidden_origin:
            await request_route.abort()
            return
          if request.is_navigation_request():
            self.check(pid, request.url)
        except (Refused, ValueError):
          await request_route.abort()
          return
        await request_route.continue_()

      await context.route("**/*", route)
      self.contexts[pid] = context
      self.pages[pid] = context.pages[0] if context.pages else await context.new_page()
      self.tabs[pid] = {}

      def track(page):
        tab_id = identity()
        self.tabs[pid][tab_id] = page
        page.on("dialog", lambda dialog: self.dialogs.__setitem__(pid, dialog))

        async def download_started(download):
          if page in self.download_expected:
            self.download_expected.discard(page)
          else:
            await download.cancel()

        page.on("download", download_started)

      for existing in context.pages:
        track(existing)
      context.on("page", track)
    page = self.pages[pid]
    if page.is_closed():
      page = await self.contexts[pid].new_page()
      self.pages[pid] = page
    return page

  async def observe(self, pid, page, args):
    self.check(pid, page.url)
    options = {}
    for key, default, maximum in [
      ("control_offset", 0, 12000),
      ("control_limit", 100, 150),
      ("text_offset", 0, 10_000_000),
      ("text_limit", 12000, 30000),
    ]:
      options[key] = integer(
        args.get(key, default), key, 0 if key.endswith("offset") else 1, maximum
      )
    if args.get("query"):
      options["query"] = bounded(args["query"], "control query", 300)
    frame_items = []
    for frame in page.frames[:50]:
      self.frames.setdefault(frame, identity())
      try:
        self.check(pid, frame.url if frame.url != "about:srcdoc" else page.url)
      except (Refused, ValueError):
        continue
      frame_items.append(
        {
          "id": self.frames[frame],
          "url": frame.url,
          "name": frame.name,
          "main": frame == page.main_frame,
        }
      )
    frame = page.main_frame
    if args.get("frame_id"):
      frame = next(
        (f for f in page.frames if self.frames.get(f) == args["frame_id"]), None
      )
      if frame is None or not any(f["id"] == args["frame_id"] for f in frame_items):
        raise Refused("Frame is unavailable or its website has not been allowed")
    snapshot = await frame.evaluate(SNAPSHOT, options)
    binding = snapshot.pop("binding")
    snapshot.pop("fieldValues")
    tab_id = next(key for key, value in self.tabs[pid].items() if value == page)
    snapshot.update(
      {
        "tab_id": tab_id,
        "frame_id": self.frames[frame],
        "frames": frame_items,
        "tabs": [
          {"id": key, "url": tab.url, "active": tab == page}
          for key, tab in self.tabs[pid].items()
          if not tab.is_closed()
        ],
        "dialog": {
          "type": self.dialogs[pid].type,
          "message": self.dialogs[pid].message[:2000],
        }
        if pid in self.dialogs
        else None,
      }
    )
    return (
      snapshot,
      digest({"tab": tab_id, "frame": self.frames[frame], "state": binding}),
      frame,
    )

  async def _perform(self, pid, operation, args):
    if operation == "open":
      url = bounded(args.get("url"), "website URL", 4096)
      self.check(pid, url)
      page = await self._page(pid)
      await page.goto(url, wait_until="domcontentloaded", timeout=20000)
      await page.bring_to_front()
    else:
      page = await self._page(pid)
    if operation == "switch_tab":
      page = self.tabs[pid].get(args.get("tab_id"))
      if page is None or page.is_closed():
        raise Refused("Tab is no longer available")
      self.check(pid, page.url)
      self.pages[pid] = page
      await page.bring_to_front()
    self.check(pid, page.url)
    if pid in self.dialogs:
      dialog = self.dialogs[pid]
      if operation == "dialog":
        if (
          args.get("message") != dialog.message[:2000]
          or args.get("type") != dialog.type
        ):
          raise Refused("The dialog changed; inspect it again")
        if type(args.get("accept")) is not bool:
          raise Refused("Specify whether to accept the dialog")
        if args["accept"]:
          await dialog.accept()
        else:
          await dialog.dismiss()
        self.dialogs.pop(pid, None)
        return {
          "performed": "dialog",
          "meaning": "Dialog answered; verify the application outcome",
        }
      if operation in {"read", "inspect"}:
        return {
          "untrusted": True,
          "snapshot": {
            "url": page.url,
            "controls": [],
            "text": "",
            "dialog": {"type": dialog.type, "message": dialog.message[:2000]},
          },
          "snapshot_id": digest({"url": page.url, "message": dialog.message}),
        }
      raise Refused("A dialog needs an explicit response before continuing")
    snapshot, snapshot_id, frame = await self.observe(pid, page, args)
    if operation in {"open", "read", "inspect"}:
      return {"untrusted": True, "snapshot": snapshot, "snapshot_id": snapshot_id}
    if operation == "switch_tab":
      return {"untrusted": True, "snapshot": snapshot, "snapshot_id": snapshot_id}
    if operation not in {
      "click",
      "fill",
      "press",
      "set_checked",
      "select_option",
      "scroll",
      "upload",
      "download",
      "drag",
      "screenshot",
    }:
      raise Refused("Unsupported browser operation")
    if args.get("snapshot_id") != snapshot_id:
      raise Refused("The page changed. Inspect it and propose a new action")
    if snapshot["scan_truncated"]:
      raise Refused(
        "The page exceeds the observation limit; use a narrower application view or manual controls"
      )
    if operation == "screenshot":
      from .artifacts import Artifacts

      if (
        snapshot["has_sensitive_controls"]
        or snapshot["scan_truncated"]
        or len(page.frames) > 1
      ):
        raise Refused("A page with sensitive fields requires manual capture")
      data = await page.screenshot(type="png")
      return {
        "artifact": Artifacts(self.store).save(pid, "browser.png", data, "image/png"),
        "business_outcome_confirmed": False,
      }
    element = next(
      (e for e in snapshot["controls"] if e["id"] == args.get("element_id")), None
    )
    if not element or element["sensitive"] or element["disabled"]:
      raise Refused("Target is unavailable or requires the user's manual input")
    locator = frame.locator('[data-veos-id="' + element["id"] + '"]')
    if operation == "click":
      if element["tag"] == "a" and element["target"] == "_blank":
        async with page.expect_popup(timeout=8000) as opening:
          await locator.click(no_wait_after=True)
        popup = await opening.value
        await popup.wait_for_load_state("domcontentloaded", timeout=10000)
        return {
          "performed": "click",
          "opened_tab": next(
            key for key, value in self.tabs[pid].items() if value == popup
          ),
          "business_outcome_confirmed": False,
        }
      # A native dialog can keep Playwright's click pending. Deliver input while
      # surfacing the dialog for its own explicit response instead of dismissing it.
      pending = asyncio.create_task(locator.click(no_wait_after=True))
      for _ in range(80):
        if pending.done():
          await pending
          break
        if pid in self.dialogs:
          pending.add_done_callback(
            lambda task: task.exception() if not task.cancelled() else None
          )
          return {
            "performed": "click",
            "awaiting_dialog": True,
            "meaning": "Input opened a dialog; application outcome is not confirmed",
          }
        await asyncio.wait({pending}, timeout=0.1)
      else:
        await pending
    elif operation == "fill":
      value = args.get("text")
      if not isinstance(value, str) or len(value) > 10000 or "\x00" in value:
        raise Refused("Invalid field text")
      await locator.fill(value)
    elif operation == "set_checked":
      if type(args.get("checked")) is not bool:
        raise Refused("Checked must be a boolean")
      await locator.set_checked(args["checked"])
    elif operation == "select_option":
      value = args.get("value")
      if not isinstance(value, str) or len(value) > 2000:
        raise Refused("Invalid option value")
      await locator.select_option(value=value)
    elif operation == "scroll":
      await locator.scroll_into_view_if_needed()
    elif operation == "upload":
      from .artifacts import Artifacts

      meta, data = Artifacts(self.store).read(pid, args.get("artifact_id"))
      if args.get("sha256") != meta["sha256"]:
        raise Refused("Select an immutable project file and its SHA-256")
      await locator.set_input_files(
        {"name": meta["name"], "mimeType": meta["media_type"], "buffer": data}
      )
    elif operation == "download":
      from .artifacts import Artifacts

      self.download_expected.add(page)
      try:
        async with page.expect_download(timeout=10000) as waiting:
          await locator.click(no_wait_after=True)
        download = await waiting.value
        completed = None
        try:
          self.check(pid, download.url)
          completed = asyncio.create_task(download.path())
          directory = self.store.root / "downloads" / pid
          for _ in range(150):
            if any(
              path.stat().st_size > 5_000_000
              for path in directory.glob("*")
              if path.is_file()
            ):
              raise Refused("Download exceeds the 5 MB limit")
            if completed.done():
              path = Path(await completed)
              with path.open("rb") as stream:
                data = stream.read(5_000_001)
              artifact = Artifacts(self.store).save(
                pid, download.suggested_filename, data
              )
              return {
                "performed": "download",
                "artifact": artifact,
                "business_outcome_confirmed": True,
              }
            await asyncio.wait({completed}, timeout=0.1)
          raise TimeoutError("Download did not finish within its limit")
        finally:
          await download.cancel()
          await download.delete()
          if completed and not completed.done():
            completed.cancel()
      finally:
        self.download_expected.discard(page)
    elif operation == "drag":
      target = next(
        (c for c in snapshot["controls"] if c["id"] == args.get("target_id")), None
      )
      if not target or target["sensitive"]:
        raise Refused("Select an observed non-sensitive drop target")
      await locator.drag_to(frame.locator('[data-veos-id="' + target["id"] + '"]'))
    elif operation == "press":
      key = args.get("key")
      if key not in {"Enter", "Tab", "Escape", "ArrowDown", "ArrowUp"}:
        raise Refused("Unsupported key")
      await locator.press(key)
    return {
      "performed": operation,
      "url": page.url,
      "element": element,
      "input_completed": True,
      "business_outcome_confirmed": False,
      "meaning": "Browser input completed; inspect the website to verify the business outcome.",
    }

  def close(self):
    if self.thread is None:
      return

    async def shutdown():
      if self.serial:
        async with self.serial:
          for context in self.contexts.values():
            await context.close()
          if self.playwright:
            await self.playwright.stop()

    future = asyncio.run_coroutine_threadsafe(shutdown(), self.loop)
    try:
      future.result(timeout=30)
    finally:
      self.loop.call_soon_threadsafe(self.loop.stop)
      self.thread.join(timeout=5)
      self.loop.close()
      self.thread = None
      self.contexts.clear()
      self.pages.clear()
      self.tabs.clear()
      self.frames.clear()
      self.dialogs.clear()
      self.playwright = None


class BrowserActions:
  def __init__(self, store, browser):
    self.store, self.browser = store, browser

  def call(self, pid, tid, operation, args):
    if not isinstance(args, dict):
      raise Refused("Browser arguments must be an object")
    if operation in {"read", "open", "switch_tab"}:
      return self.browser.invoke(pid, operation, args)
    if operation not in {
      "fill",
      "click",
      "press",
      "set_checked",
      "select_option",
      "scroll",
      "dialog",
      "upload",
      "download",
      "drag",
      "screenshot",
    }:
      raise Refused("Unsupported browser operation")
    observation_args = {
      key: value
      for key, value in args.items()
      if key
      in {
        "frame_id",
        "control_offset",
        "control_limit",
        "query",
        "text_offset",
        "text_limit",
      }
    }
    snapshot = self.browser.invoke(pid, "read", observation_args)
    if snapshot["snapshot_id"] != args.get("snapshot_id"):
      raise Refused("Inspect the current page before proposing an action")
    element = (
      {"name": "Capture current page", "sensitive": False}
      if operation == "screenshot"
      else snapshot["snapshot"].get("dialog")
      if operation == "dialog"
      else next(
        (
          e
          for e in snapshot["snapshot"]["controls"]
          if e["id"] == args.get("element_id")
        ),
        None,
      )
    )
    if not element or element.get("sensitive"):
      raise Refused(
        "Select a visible non-sensitive element; passwords and verification codes require manual entry"
      )
    payload = {
      "operation": operation,
      "arguments": args,
      "target": element,
      "url": snapshot["snapshot"]["url"],
    }
    if len(encoded(payload)) > 50000:
      raise Refused("Action is too large")
    request_key = args.get("request_key")
    if request_key is not None:
      bounded(request_key, "action request key", 160)
    fingerprint = digest(
      {"task": tid, "intent": request_key}
      if request_key
      else {"task": tid, "browser": payload}
    )
    aid = identity()
    with self.store.db() as db:
      db.execute("BEGIN IMMEDIATE")
      row = db.execute(
        "SELECT state,project_id FROM tasks WHERE id=?", (tid,)
      ).fetchone()
      if not row or row["state"] != "running" or row["project_id"] != pid:
        raise Refused("Task is not running")
      prior = db.execute(
        "SELECT id,state,args FROM actions WHERE fingerprint=? AND project_id=?",
        (fingerprint, pid),
      ).fetchone()
      if prior:
        if json.loads(prior["args"]) != payload:
          raise Refused("Action request key was already used for different arguments")
        return {"action_id": prior["id"], "state": prior["state"]}
      # Staleness was refused before any input was sent. Preserve the receipt,
      # but permit a fresh explicitly reviewed proposal in the same task.
      db.execute(
        "UPDATE actions SET state='superseded' WHERE task_id=? AND state='failed' AND json_extract(result,'$.retryable')=1",
        (tid,),
      )
      if db.execute(
        "SELECT 1 FROM actions WHERE task_id=? AND state IN ('unknown','denied','expired','failed','cancelled')",
        (tid,),
      ).fetchone():
        raise Refused(
          "Review the previous unsuccessful action before creating another task"
        )
      db.execute(
        "INSERT INTO actions VALUES(?,?,?,?,?,?,'pending',?,NULL,?)",
        (
          aid,
          pid,
          tid,
          fingerprint,
          "browser." + operation,
          encoded(payload),
          self.store.clock() + 600,
          self.store.clock(),
        ),
      )
      self.store.event(
        db,
        pid,
        "approval.requested",
        {"id": aid, "tool": "browser." + operation},
      )
    self.store.notify()
    return {
      "action_id": aid,
      "state": "pending",
      "message": "Review the exact browser action in VeOS; nothing has been entered or clicked.",
    }

  def decide(self, aid, approve):
    if type(approve) is not bool:
      raise Refused("Approval must be a boolean")
    with self.store.db() as db:
      db.execute("BEGIN IMMEDIATE")
      row = db.execute(
        "SELECT * FROM actions WHERE id=? AND state='pending' AND tool LIKE 'browser.%'",
        (aid,),
      ).fetchone()
      if not row:
        raise Refused("Browser action is not awaiting approval")
      project = self.store.require_project(db, row["project_id"])
      if approve:
        self.store.check_goal_deadline(db, row["task_id"])
      if approve and (
        project["state"] != "active"
        or (
          project["deadline"] is not None and project["deadline"] <= self.store.clock()
        )
      ):
        raise Refused("Project is paused, closed or past its deadline")
      state = (
        "denied"
        if not approve
        else "expired"
        if row["expires"] <= self.store.clock()
        else "executing"
      )
      db.execute("UPDATE actions SET state=? WHERE id=?", (state, aid))
      self.store.event(
        db, row["project_id"], "approval.decided", {"id": aid, "state": state}
      )
      if state != "executing":
        self.store._action_changed(db, row["task_id"])
    if state == "executing":
      payload = json.loads(row["args"])
      try:
        result = self.browser.invoke(
          row["project_id"], payload["operation"], payload["arguments"]
        )
        state = "succeeded"
      except Refused as exc:
        state, result = (
          "failed",
          {
            "message": str(exc),
            "retryable": str(exc)
            == "The page changed. Inspect it and propose a new action",
          },
        )
      except Exception:  # noqa: BLE001 — Browser timeouts may follow an effect; never replay automatically.
        state, result = (
          "unknown",
          {
            "message": "Browser outcome is uncertain. Inspect the website before another action."
          },
        )
      self.store.settle_action(aid, state, result)
    self.store.notify()
    return {"id": aid, "state": state}
