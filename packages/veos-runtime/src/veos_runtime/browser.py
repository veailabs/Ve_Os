"""Local Chromium automation. No cloud browser account or paid API is involved."""

import asyncio
import importlib.util
import json
import os
import shutil
import threading
from urllib.parse import urlsplit

from .store import Refused, bounded, digest, encoded, identity


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


# Never return field values, cookies, local storage, or hidden DOM to an agent.
SNAPSHOT = """() => {
  const visible = el => !!(el.getClientRects().length) && getComputedStyle(el).visibility !== 'hidden';
  const controls = [...document.querySelectorAll('a,button,input,textarea,select,[role=button]')]
    .filter(visible).slice(0,150).map((el, i) => {
      const id = String(i); el.setAttribute('data-veos-id', id);
      return {id, tag: el.tagName.toLowerCase(), type: el.type || '',
        name: (el.getAttribute('aria-label') || el.labels?.[0]?.innerText || el.innerText || el.name || '').slice(0,300),
        href: el.tagName === 'A' ? el.href : '', disabled: !!el.disabled,
        sensitive: el.type === 'password' || /password|cc-number|cc-csc|one-time-code/.test(el.autocomplete || '')};
    });
  const fieldValues = controls.filter(c => !c.sensitive && ['input','textarea','select'].includes(c.tag))
    .map(c => [c.id, document.querySelector('[data-veos-id="'+c.id+'"]').value]);
  return {url: location.href, title: document.title, text: (document.body?.innerText || '').slice(0,30000), controls, fieldValues};
}"""


class LocalBrowser:
  def __init__(self, store, *, headless=False, executable=None):
    self.store, self.headless, self.executable = store, headless, executable
    self.thread = None
    self.lock = threading.Lock()
    self.contexts = {}
    self.pages = {}
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
        "accept_downloads": False,
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
    page = self.pages[pid]
    if page.is_closed():
      page = await self.contexts[pid].new_page()
      self.pages[pid] = page
    return page

  async def _perform(self, pid, operation, args):
    if operation == "open":
      url = bounded(args.get("url"), "website URL", 4096)
      self.check(pid, url)
      page = await self._page(pid)
      await page.goto(url, wait_until="domcontentloaded", timeout=20000)
      await page.bring_to_front()
    else:
      page = await self._page(pid)
    self.check(pid, page.url)
    snapshot = await page.evaluate(SNAPSHOT)
    snapshot_id = digest(
      snapshot
    )  # Bind approvals to form values, without disclosing them.
    snapshot.pop("fieldValues")
    if operation in {"open", "read", "inspect"}:
      return {"untrusted": True, "snapshot": snapshot, "snapshot_id": snapshot_id}
    if operation not in {"click", "fill", "press"}:
      raise Refused("Unsupported browser operation")
    if args.get("snapshot_id") != snapshot_id:
      raise Refused("The page changed. Inspect it and propose a new action")
    element = next(
      (e for e in snapshot["controls"] if e["id"] == args.get("element_id")), None
    )
    if not element or element["sensitive"] or element["disabled"]:
      raise Refused("Target is unavailable or requires the user's manual input")
    locator = page.locator('[data-veos-id="' + element["id"] + '"]')
    if operation == "click":
      await locator.click(no_wait_after=True)
    elif operation == "fill":
      await locator.fill(bounded(args.get("text"), "field text", 10000))
    else:
      key = args.get("key")
      if key not in {"Enter", "Tab", "Escape", "ArrowDown", "ArrowUp"}:
        raise Refused("Unsupported key")
      await locator.press(key)
    return {
      "performed": operation,
      "url": page.url,
      "element": element,
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
      self.playwright = None


class BrowserActions:
  def __init__(self, store, browser):
    self.store, self.browser = store, browser

  def call(self, pid, tid, operation, args):
    if not isinstance(args, dict):
      raise Refused("Browser arguments must be an object")
    if operation in {"read", "open"}:
      return self.browser.invoke(pid, operation, args)
    if operation not in {"fill", "click", "press"}:
      raise Refused("Use open, read, fill, click or press")
    snapshot = self.browser.invoke(pid, "read", {})
    if snapshot["snapshot_id"] != args.get("snapshot_id"):
      raise Refused("Inspect the current page before proposing an action")
    element = next(
      (
        e for e in snapshot["snapshot"]["controls"] if e["id"] == args.get("element_id")
      ),
      None,
    )
    if not element or element["sensitive"]:
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
    fingerprint = digest({"task": tid, "browser": payload})
    aid = identity()
    with self.store.db() as db:
      db.execute("BEGIN IMMEDIATE")
      row = db.execute(
        "SELECT state,project_id FROM tasks WHERE id=?", (tid,)
      ).fetchone()
      if not row or row["state"] != "running" or row["project_id"] != pid:
        raise Refused("Task is not running")
      prior = db.execute(
        "SELECT id,state FROM actions WHERE fingerprint=? AND project_id=?",
        (fingerprint, pid),
      ).fetchone()
      if prior:
        return {"action_id": prior["id"], "state": prior["state"]}
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
        state, result = "failed", {"message": str(exc)}
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
