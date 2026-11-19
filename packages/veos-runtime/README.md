# VeOS Mission Desk

A personal, local workspace for projects, native subscription agents, evidence, files and reviewed app actions. It is an optional package; cloning or installing Ve OS does not enable the runtime or a model account.

The runtime owns a loopback API and SQLite task/evidence journal. It launches the user's native Codex or Claude client for reasoning. Those clients still use their provider's cloud; this is not an offline model. Providers start disabled, paid API fallback is refused, and a user must confirm that provider-side paid extra usage is disabled before enabling one.

## Installation

From the repository root, with Python 3.11+ and uv installed:

```sh
uv tool install './packages/veos-runtime[browser]'
veos init
veos serve
```

In another terminal, run `veos open`. It opens an authenticated session at `http://127.0.0.1:8764`; the local session token is removed from the URL fragment immediately. The API verifies the Host/Origin and bearer token. Do not publish or reverse-proxy this single-user server.

The optional browser component needs Chrome or Chromium. Use a separate profile created by VeOS; do not copy a personal browser profile into it. The base package has no third-party Python runtime dependencies, and the browser extra adds Playwright.

## Native agent setup

Install and sign in through the native provider client. After verifying that paid extra usage/top-ups are disabled in that provider account:

```sh
veos provider codex enable --confirm-no-paid-extra-usage
veos provider codex check
```

Use `claude` for the Claude adapter. The flag records the user's billing assertion; it does not change or inspect the provider's billing settings. Antigravity remains unavailable until its subscription authentication and execution contract are qualified.

Every run rechecks native subscription sign-in. Agent children receive a filtered environment, an explicit task-scoped MCP connection and native read-oriented execution settings. There is no automatic switch to an API key, another provider or another account after quota exhaustion. A recognizable structured quota error becomes `waiting_subscription`; unknown failures remain unknown.

## Task lifecycle

Projects have objectives, deadlines and run budgets. Tasks have dependencies, a scheduled start, bounded attempts and an idempotency key. Only one local worker claims work; transactions and fenced leases prevent competing claims and stale completion writes.

A model finishing its reply does not complete a task with pending actions. The task enters `waiting_approval`, and dependent work stays blocked. A receipt schedules a fresh native turn with the original instruction and stored action results. This continuation consumes a project run but not a failure attempt. Denied or uncertain actions cannot be silently replayed.

Closing a project cancels pending work. Already-submitted external actions may still complete. Interrupted tasks and actions recover as `unknown`, retaining checkpoints; they need review before an explicit retry. This is durable context, not resumption of a native CLI session.

## Browser and files

The Project browser panel grants individual website origins and opens a visible local Chromium window. Each project has a separate persistent profile. Sign in directly in that window. Website expiry and revocation still control how long a sign-in lasts.

The agent's `browser` tool supports open/read and proposed click/fill/press operations. Changes require review of the exact target and arguments, bound to the observed page and form values. Password and verification-code inputs require manual entry. A successful input receipt is not proof of a business outcome; the agent must inspect the website afterward. Website grants are not a general network sandbox.

Files up to 5 MB are stored as immutable SHA-256-addressed blobs. Original versions remain available with task/run lineage. Text files have bounded retrieval; generated Markdown reports can be downloaded. PDF and image originals are preserved, but no OCR or image interpreter is connected.

## Evidence

Sources record URIs, excerpts, capture times, hashes and creating runs. Claims/hypotheses link supporting and contradicting sources; source withdrawal marks affected claims for review. These records remain assessments, not automatically verified facts. Context reserves space for evidence; `search_evidence` and `get_evidence` retrieve omitted text within the project.

Use `available_tools` to discover configured browser and Ve capabilities. The remaining tools record sources/claims, propose follow-up tasks, list/read files, write reports and invoke explicitly allowed Ve tools. Child tasks require user acceptance. No model is separately billed as a router or evaluator.

## Optional Ve connection

The mail adapter is disabled by default. It requires an independently verified HTTPS Integration endpoint, mailbox UUID and exact read/write tool allowlists in the state directory's `config.json`. It accepts a user-authorized short-lived `VEOS_VE_ACCESS_TOKEN` through the runtime environment, mints a scoped MCP grant and binds every tool to the configured mailbox. It does not forward the user token to the agent.

Interactive Ve sign-in, secure token persistence and refresh are not implemented. Expired access fails closed. Do not enable a tool whose backend introduces separately billed inference under a subscription-only configuration. A mocked transport proves adapter behavior, not the live service or account setup.

Writes require review and an explicit success receipt. Duplicate approval races submit locally at most once; uncertain remote outcomes do not retry. Mutable draft-send-by-ID operations are refused until an immutable content/version contract exists. This does not claim exactly-once remote delivery.

## Local storage and trust boundary

State defaults to `~/.local/state/veos` or `$XDG_STATE_HOME/veos`. Set `--state-dir` before the CLI subcommand to use an independent workspace.

| Path | Contents |
|---|---|
| `state.sqlite3` | Projects, tasks, runs, evidence, claims, action receipts, browser grants, file metadata and events |
| `artifacts/<project>/<hash>` | Uploaded originals and generated reports |
| `browser-profiles/<project>` | Chromium cookies and site storage |
| `projects/<project>` | Native client work directories |
| `config.json` | Runtime settings and provider enablement assertions |
| `connection.json` | Private local UI session, recreated at startup |

Native provider sign-in remains in its native client's own storage. VeOS does not copy provider credentials. This is one trusted OS account with filesystem permissions, not tenant/VM isolation or independently encrypted storage. The JSON export includes metadata and records, not browser profiles or file bytes. No cloud state synchronization is implemented.

## Desktop startup

On Linux, `veos install-desktop` copies the optional user plugin, desktop launcher and systemd service. It does not enable them:

```sh
systemctl --user daemon-reload
systemctl --user enable --now veos-runtime.service
omarchy-shell shell rescanPlugins
omarchy plugin enable veos.missions
```

The widget launches Mission Desk on click; it does not poll or run a model inside the shell. The target Omarchy/Quickshell session still needs validation. Installing or copying these files does not install an OS or run migrations.

On macOS, `veos install-desktop` creates a user LaunchAgent. Start it with `launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/ai.ve.veos-runtime.plist"`; stop it with the equivalent `launchctl bootout`. The service starts at user login and restarts after process exit. It cannot execute tasks while the computer is off. Existing differing service/widget files are never silently overwritten.

## Verification

From this package directory:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
node --check src/veos_runtime/web/app.js
uv run --with ruff --no-project ruff check src tests
uv run --with ruff --no-project ruff format src tests --check
uv build --wheel
uv run --no-project --with ./dist/veos_runtime-0.2.0-py3-none-any.whl python tests/installed_smoke.py
PYTHONPATH=src uv run --with playwright --no-project python tests/browser_smoke.py
PYTHONPATH=src uv run --with playwright --no-project python tests/local_browser_smoke.py
```

Record test output in files and inspect it, following the repository's testing guide. On Linux, the browser smoke tests need Playwright Chromium installed. They use temporary local fixture data and perform no live model calls, message sends or Integration actions. UI screenshots are written to ignored `artifacts/`; attach them as review evidence rather than committing them.

Runtime/Chrome tests do not establish Linux shell behavior, live subscription execution, live mailbox connectivity or semantic completion of every task. Cloud enrollment/sync, recurring signals, voice, general native-app control and external teammate adapters are outside this package's current implementation.
