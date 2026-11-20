# VeOS Mission Desk

A personal, local workspace for projects, native subscription agents, evidence, files and reviewed app actions. It is an optional package; cloning or installing Ve OS does not enable the runtime or a model account.

The runtime owns a loopback API and SQLite task/evidence journal. It launches the user's native Codex, Claude or qualified Hermes client for reasoning. Those clients still use their provider's cloud; this is not an offline model. Providers start disabled, paid API fallback is refused, and a user must confirm that provider-side paid extra usage is disabled before enabling one.

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

### Hermes

The Hermes adapter is qualified against `NousResearch/hermes-agent` commit `2de9f51b8008a7d49a49221c184f2f11cbfc4af1`, with Python 3.14 and its MCP extra. A different revision is refused until requalified. Install from that source checkout using its own `uv` configuration, which includes required upstream dependency overrides:

```sh
git clone https://github.com/NousResearch/hermes-agent.git "$HOME/.local/share/veos-hermes-source"
cd "$HOME/.local/share/veos-hermes-source"
git checkout --detach 2de9f51b8008a7d49a49221c184f2f11cbfc4af1
uv venv --python 3.14 .venv
uv pip install --python .venv/bin/python -e '.[mcp]'
export PATH="$PWD/.venv/bin:$PATH"
```

Make that executable available on the VeOS user service's PATH too. Stop the VeOS worker before managing its Hermes profile, then use native sign-in and select a model your subscription actually supports:

```sh
veos provider hermes login --model YOUR_SUBSCRIPTION_MODEL
veos provider hermes enable --model YOUR_SUBSCRIPTION_MODEL --confirm-no-paid-extra-usage
veos provider hermes check
```

Hermes owns its native OAuth credentials under the private `hermes/` directory inside the VeOS state directory. VeOS does not parse or copy those credentials. The adapter uses `openai-codex`, not an OpenAI API key. It creates a separate native session for each task, exposes only the task-scoped VeOS MCP tools, disables native rule discovery, plugin installation, background review and title-generation calls, and keeps the project/task journal in VeOS. Ambient API-key environment variables and custom Hermes plugins/hooks are refused.

Hermes's CLI can detach while its gateway continues working. VeOS writes and flushes an active-run marker before launch, stops its own gateway on completion, cancellation and error, and independently checks that it is no longer running before clearing the marker. After an abrupt worker crash, the marker blocks new Hermes work until gateway recovery succeeds. An unconfirmed stop leaves the task `unknown` and blocks further Hermes tasks. After stopping the worker, `veos provider hermes stop` performs recovery; the runtime never uses Hermes's `--all` switch or adopts an existing personal Hermes profile. This is a single-OS-user boundary, not a container sandbox.

## Task lifecycle

Projects have objectives, deadlines and run budgets. Tasks have dependencies, a scheduled start, bounded attempts and an idempotency key. Only one local worker claims work; transactions and fenced leases prevent competing claims and stale completion writes.

A model finishing its reply does not complete a task with pending actions. The task enters `waiting_approval`, and dependent work stays blocked. A receipt schedules a fresh native turn with the original instruction and stored action results. This continuation consumes a project run but not a failure attempt. Denied or uncertain actions cannot be silently replayed.

Closing a project cancels pending work. Already-submitted external actions may still complete. Interrupted tasks and actions recover as `unknown`, retaining checkpoints; they need review before an explicit retry. This is durable context, not resumption of a native CLI session.

## Browser and files

The Project browser panel grants individual website origins and opens a visible local Chromium window. Each project has a separate persistent profile. Sign in directly in that window. Website expiry and revocation still control how long a sign-in lasts.

The agent's `browser` tool supports paginated observations, targeted control search, open shadow trees, granted frames, tab switching, editable fields, checkboxes, dropdowns, keys, scrolling to a control, drag/drop, explicit dialogs, screenshots and immutable file upload/download. Safe field values and control state are observable. Actions carry the observed snapshot and exact arguments into review. Password and verification-code inputs require manual entry. A successful input receipt is not proof of a business outcome; the agent must inspect the website afterward. Website grants are not a general network sandbox.

Files up to 5 MB are stored as immutable SHA-256-addressed blobs. Original versions remain available; browser download and screenshot receipts link the corresponding artifacts. Text files have bounded retrieval; generated Markdown reports can be downloaded. The separately granted `desktop.read_document` operation extracts PDF text with local Poppler (`pdftotext`) and image text with local Tesseract. It has no paid OCR fallback or general image-understanding model. Install the appropriate tools before using it.

## Desktop control and customization

The Desktop & settings panel grants capabilities per project and opens existing Omarchy settings for branding, themes, backgrounds, fonts, icons, bar, dock, window layout, keyboard/input, defaults, plugins, agent selection, network, displays, startup and notifications. Opening a settings panel does not change its settings. `Take control` pauses the project; already-submitted actions may finish.

Agents discover the `desktop` operation contracts through `available_tools`. Available operations include application/window discovery, launch/focus, audio volume/mute, clipboard, screenshot, AT-SPI application observation/actions/field editing, installed theme/font selection, and typed appearance/input settings. Changes require exact action review and recheck the project grant. Typed config writes preserve personal configuration and previous VeOS overrides, create a private backup and restore the file after a failed reload. Agent-provided shell strings and arbitrary Lua are not accepted.

Linux controls require the signed-in graphical session and the relevant command. Native accessibility additionally requires distribution Python with `python-gobject`/GI, `at-spi2-core` and accessible controls exposed by the application. Browsers, terminals, web-content trees and Mission Desk administration are refused by the native adapter; websites must use the granted project browser. AT-SPI does not make every application automatable. The UI exposes availability and prerequisite notes; no kernel, root-level input driver or universal screenshot-click fallback is installed.

## Incoming MCP and optional voice

A compatible local MCP client can delegate to the same journal and worker:

```json
{
  "mcpServers": {
    "veos": {
      "command": "veos",
      "args": ["incoming-mcp", "--project-id", "YOUR_PROJECT_ID", "--provider", "codex"]
    }
  }
}
```

Start `veos serve` first. Use `claude` or `hermes` for those implemented adapters after their native setup. The client gets `run_local_agent`, `local_agent_status` and `cancel_local_agent`, bound to that project/provider. It receives no administrator tools or direct desktop actions. Reuse a request key only for retries of the same work. A queued task is not completed work; status distinguishes the task narrative from verified action receipts. Connections expire after an hour or are revoked on disconnect/restart. Reconnect to inspect admitted tasks through the same project/provider scope.

MCP compatibility does not establish that ChatGPT or another provider's subscription voice accepts this local stdio tool. VeOS does not extract subscription cookies or repurpose a consumer voice session as an API. A remote-only MCP client cannot directly reach this loopback server.

The Mission Desk microphone is a separate, explicitly enabled API voice path. Configure `VEOS_VOICE_API_ENABLED=true`, an authenticated `VEOS_VOICE_SESSION_URL` on `https://backend.ve.ai/.../core/voice/sessions`, and the short-lived `VEOS_VE_ACCESS_TOKEN` in the runtime environment. The matching Super Agent implementation is [PR #98](https://github.com/veailabs/ve-core-super-agent/pull/98), deployed to development; the pilot still needs its dedicated funded API key and verified tenant/user configuration. Credentials must not be committed. Without configuration the microphone button is disabled. The runtime does not yet own native Ve login or refresh: restart with a fresh authenticated token when it expires; do not put it in a permanent service file.

Ve mints a temporary OpenAI client secret with `executionTarget=local_harness`; the browser establishes WebRTC directly with OpenAI. Only the ephemeral secret goes to OpenAI, while tool calls return to a scoped local connection and the selected native agent. The voice layer never executes model-generated JavaScript. Audio goes to OpenAI; task data may go to the selected model provider. This is not fully local inference. Voice is limited to 10 minutes per session, three connection attempts per 15 minutes, no automatic reconnection, and 100 tool calls per session. Existing project budgets still apply.

`Stop speaking` cancels the current voice response and clears queued audio; `Disconnect voice` releases the microphone and local connection. Neither silently cancels tasks. Use `Cancel voice task`, the work queue or `Take control` for work cancellation/pause. The transient transcript stays in the page, while delegated tasks and receipts persist in SQLite. Real audio quality and end-to-end cloud voice remain a separate live acceptance check.

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
| `state.sqlite3` | Projects, tasks, runs, evidence, claims, action receipts, browser/desktop grants, harness task scope, file metadata and events |
| `artifacts/<project>/<hash>` | Uploaded originals and generated reports |
| `browser-profiles/<project>` | Chromium cookies and site storage |
| `projects/<project>` | Native client work directories |
| `config.json` | Runtime settings and provider enablement assertions |
| `connection.json` | Private local UI session, recreated at startup |
| `~/.config/hypr/*.lua` | Explicitly reviewed desktop overrides; adjacent private files preserve previous content |

Native provider sign-in remains in its native client's own storage. VeOS does not copy provider credentials. This is one trusted OS account with filesystem permissions, not tenant/VM isolation or independently encrypted storage. The JSON export includes metadata and records, not browser profiles or file bytes. No cloud state synchronization is implemented.

## Desktop startup

On Linux, `veos install-desktop` copies the optional user plugin, desktop launcher and systemd service. It does not enable them:

```sh
systemctl --user daemon-reload
systemctl --user enable --now veos-runtime.service
omarchy-shell shell rescanPlugins
omarchy plugin enable veos.missions
```

The widget launches Mission Desk on click; it does not poll or run a model inside the shell. The Wayland acceptance workflow installs this wheel and user service in a disposable Omarchy VM, enables the actual widget and verifies a real click opens Mission Desk. Check the exact release commit's workflow and screenshot artifacts before deployment. Installing or copying these files does not install an OS or run migrations.

On macOS, `veos install-desktop` creates a user LaunchAgent. Start it with `launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/ai.ve.veos-runtime.plist"`; stop it with the equivalent `launchctl bootout`. The service starts at user login and restarts after process exit. It cannot execute tasks while the computer is off. Existing differing service/widget files are never silently overwritten.

## Verification

From this package directory:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
node --check src/veos_runtime/web/app.js
node --check src/veos_runtime/web/voice.js
node --test tests/voice_client.test.cjs
uv run --with ruff --no-project ruff check src tests
uv run --with ruff --no-project ruff format src tests --check
uv build --wheel
uv run --no-project --with ./dist/veos_runtime-0.3.0-py3-none-any.whl python tests/installed_smoke.py
PYTHONPATH=src uv run --with playwright --no-project python tests/browser_smoke.py
PYTHONPATH=src uv run --with playwright --no-project python tests/local_browser_smoke.py
```

Record test output in files and inspect it, following the repository's testing guide. On Linux, the browser smoke tests need Playwright Chromium installed. They use temporary local fixture data and perform no live model calls, message sends or Integration actions. UI screenshots are written to ignored `artifacts/`; attach them as review evidence rather than committing them.

The Linux workflow also exercises a real disposable GTK application over AT-SPI and real Poppler/Tesseract document extraction. A separate Omarchy VM workflow exercises the actual Wayland session. The Hermes workflow runs its pinned native gateway against a local inference fixture, discovers the real MCP tools, executes a project read and verifies completion, in-flight cancellation and gateway shutdown; it uses no provider credentials. Runtime/fixture tests do not establish live subscription entitlement, live mailbox connectivity, real audio quality or semantic completion of every task. Cloud supervision/enrollment/sync, recurring external signals and external teammate adapters remain outside this package's implementation. See [the implementation and acceptance guide](../../docs/veos-agent-controls.md) for contracts and limits.
