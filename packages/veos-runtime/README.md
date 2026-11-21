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

### Direct controls from another harness

`veos control-mcp --project-id YOUR_PROJECT_ID` gives a compatible local harness the same 21 application, context and memory tools used by the managed workers, plus four task-lifecycle tools. The connected harness does the reasoning itself; VeOS does not launch Hermes or another model behind it. The existing `incoming-mcp` interface remains the three-tool delegation path used by voice.

Generate a configuration fragment for the installed runtime:

```sh
veos mcp-config --client codex --project-id YOUR_PROJECT_ID
veos mcp-config --client claude --project-id YOUR_PROJECT_ID
veos mcp-config --client gemini --project-id YOUR_PROJECT_ID
veos mcp-config --client hermes --project-id YOUR_PROJECT_ID
```

Merge the fragment into that client's MCP settings; the command does not overwrite personal settings or enable providers. Codex receives TOML, Claude/Gemini receive `mcpServers` JSON, and Hermes receives `mcp_servers` JSON (also valid YAML). Fragments bind the absolute installed Python executable and state directory, contain no credentials, and require `veos serve` to be running. Regenerate after moving or reinstalling the environment. To configure delegation instead, append `--mode delegate --provider hermes` (or `codex`/`claude`). Gemini is supported as an external MCP client; a managed Gemini worker and its subscription authentication are not implemented. An external client's own tools, account and billing are governed by that client, not by VeOS's native-worker subscription checks.

The direct workflow is:

1. `start_control_task(task, request_key)` binds a task to this connection and consumes one project run. Reusing the same key for the same task does not start it twice; different instructions require a different key.
2. `available_tools` discovers actual capabilities and grants. Use `browser`, `desktop`, `ve_call` and the shared context/evidence/file tools as needed. A pending write remains subject to the existing exact-action review in Mission Desk.
3. `control_task_status(offset, limit)` returns paginated action receipts, task state and a receipt version. After review, read the receipts and observe the application outcome. The external harness keeps ownership; no background agent wakes to duplicate the task.
4. `finish_control_task(result, outcome, receipt_version)` records the outcome. Success requires the current receipt version and no pending/executing actions. A stale version is refused; denied/failed/unknown actions cannot become a successful task. `cancel_control_task` cancels pending work.

Connections are project-scoped, memory-only and expire after one hour. EOF, SIGINT/SIGTERM, expiry and clean service shutdown revoke the connection; unfinished tasks become `unknown` and pending approvals are cancelled. Already-submitted effects can still finish. An abruptly killed client may retain its connection until expiry; a runtime crash is recovered on restart. Review interrupted work and explicitly retry in Mission Desk before reconnecting with its original key. External tasks use `execution_owner=external` in the existing SQLite journal and are excluded from worker discovery and claims, including after retry. The service remains a single trusted OS user's loopback service, not a remote multi-tenant sandbox.

The portable `veos-app-control` skill ships inside the wheel. Managed Hermes/Codex/Claude workers receive its body directly; MCP clients can discover/read `veos://skills/application-control`, and initialization supplies short workflow guidance. `veos skill` prints the same `SKILL.md` for a client's own skill installation mechanism. Installing the skill alone supplies instructions, not an MCP connection or additional permission. The shared skill does not enable Hermes's native memory/plugins or broaden its restricted toolset.

### Optional API voice

The Mission Desk microphone is a separate, explicitly enabled API voice path. Configure `VEOS_VOICE_API_ENABLED=true`, an authenticated `VEOS_VOICE_SESSION_URL` on `https://backend.ve.ai/.../core/voice/sessions`, and the short-lived `VEOS_VE_ACCESS_TOKEN` in the runtime environment. The matching Super Agent implementation is [PR #98](https://github.com/veailabs/ve-core-super-agent/pull/98), deployed to development; the pilot still needs its dedicated funded API key and verified tenant/user configuration. Credentials must not be committed. Without configuration the microphone button is disabled. The runtime does not yet own native Ve login or refresh: restart with a fresh authenticated token when it expires; do not put it in a permanent service file.

Ve mints a temporary OpenAI client secret with `executionTarget=local_harness`; the browser establishes WebRTC directly with OpenAI. Only the ephemeral secret goes to OpenAI, while tool calls return to a scoped local connection and the selected native agent. The voice layer never executes model-generated JavaScript. Audio goes to OpenAI; task data may go to the selected model provider. This is not fully local inference. Voice is limited to 10 minutes per session, three connection attempts per 15 minutes, no automatic reconnection, and 100 tool calls per session. Existing project budgets still apply.

`Stop speaking` cancels the current voice response and clears queued audio; `Disconnect voice` releases the microphone and local connection. Neither silently cancels tasks. Use `Cancel voice task`, the work queue or `Take control` for work cancellation/pause. The transient transcript stays in the page, while delegated tasks and receipts persist in SQLite. Real audio quality and end-to-end cloud voice remain a separate live acceptance check.

## Evidence

Sources record URIs, excerpts, capture times, hashes and creating runs. Claims/hypotheses link supporting and contradicting sources; source withdrawal marks affected claims for review. These records remain assessments, not automatically verified facts. Context reserves space for evidence; `search_evidence` and `get_evidence` retrieve omitted text within the project.

Use `available_tools` to discover configured browser and Ve capabilities. The remaining tools record sources/claims, propose follow-up tasks, list/read files, write reports and invoke explicitly allowed Ve tools. Child tasks require user acceptance. No model is separately billed as a router or evaluator.

## Memory

The runtime keeps four memories in the same journal, so a project outlives the agents that worked on it:

| Memory | Holds | Who writes it |
|---|---|---|
| Executive | Your strategy, preferences, risk appetite, operating rules and priorities, for every project | You. An agent can only propose an entry; it reaches no agent until you accept it |
| Project | One project's decisions, outcomes, next actions, timeline and notes, and the correlations VeOS notes between its signals | You or that project's agents. An agent's decision is a recommendation until you approve it |
| Evidence | Sources, claims and hypotheses with a confidence and a full revision history, plus observed signals | You or that project's agents |
| Procedural | Workflows, retry lessons and failure patterns, for every project, beside provider and tool results computed from the journal | You. An agent can only propose an entry |

Nothing is edited in place. Revising a memory writes a new version that supersedes the old one, and each version names its author and, for an agent, the task and run. A claim keeps every confidence it has had with the reason for each change; contradicting evidence must come with a lower confidence. Withdrawing a source returns the claims and memories that rest on it to review and takes them out of agent context until you keep them. `veos memory erase` removes the text of a memory and all its versions, leaving only identifiers.

A decision carries its options and rationale, a snapshot of the sources it rested on as they stood, and you as its approver. A hypothesis carries its competing explanations and the observation that would disprove it. A source can carry an expiry, an access class (public, licensed or internal) and a confidence of its own; once expired it supports no new claim or memory, and the brief lists the claims left resting on it.

A project also keeps three typed records, each versioned like a memory:

| Record | Fields | Notes |
|---|---|---|
| Experiment | Protocol and version, inputs, measurements, controls, failures, lab, IP terms | States planned, running, succeeded, failed, inconclusive. A failed run is a result: it cannot be deleted, and the brief lists it |
| Relationship | Parties (entities of the project), consent, meetings, CRM reference, follow-up and its state | The brief lists follow-ups due and the relationships whose consent is declined or withdrawn. Recording one contacts nobody |
| Goal | Parent goal, priority, success criteria, constraints, the signals that should reawaken it, stop conditions | A goal an agent writes is proposed until you accept it. Active goals start every task's context |

Another record type is one entry in `TYPES` in `memory.py`: its fields, its states and whether an agent's entry waits for you.

Entities are the organizations, people and technologies a project follows. A person is recorded only from public roles or with consent, and the record says which. `resolve_entity` matches a mention to an existing record by identifier (`domain:example.com`, `patent:US1234567`, `orcid:…`) or, for organizations and technologies, by a name or alias that differs only in case, spacing or punctuation. People who share a name, and records whose identifiers disagree, are recorded as ambiguous beside their candidates until you rule in the Memory panel that they are the same or different. Links relate any two records of one project (`person employed_by organization`, `claim about entity`, `signal mentions entity`); they are the project's relationship graph. A signal is one observed event from a feed, kept once per project however often it is seen.

### Signals from feeds

`veos signal ingest` reads one JSON object per line from standard input, so any feed reader can pipe into it. VeOS collects nothing itself and calls no model here.

```sh
echo '{"source":"patent","content":"Acme files a lipid application","entities":[{"kind":"organization","name":"Acme Therapeutics","identifiers":["domain:acme.example"]}]}' | veos signal ingest
veos signal inbox
veos signal promote HELD_ID --name "Acme opportunity" --objective "Invent, partner or wait"
veos signal dismiss HELD_ID
```

A signal about an entity that an open project already follows is recorded in that project, so a patent, a publication and a job listing about one company update one project rather than opening three. People are matched by identifier only. When two different feeds name the same entity within 30 days, VeOS records a correlation in the project's memory; it states which feeds and which entity, not what it means. A goal whose `resume_on` names that kind of signal is marked as having news. A signal nothing follows is held, and becomes ready once two different feeds name the same entity. Opening the project is your decision, in the Mission Desk or with `promote`: nothing creates a project or spends a run on its own.

Every agent task starts with your active direction, set apart from the rest of its context, and with the project's memory and the accepted lessons as untrusted records. Direction grants no permission and approves no action: grants and action review still decide every effect. Agents use `recall_memory`, `remember`, `revise_claim`, `resolve_entity`, `link_records`, `related_records`, `record_signal` and `save_record`. An agent reads and writes only its own project's records; another project's memory, entities and signals are never returned to it.

```sh
veos memory add --scope executive --kind operating_rule --statement "Draft outreach for approval; never send it."
veos memory list --state proposed
veos memory accept MEMORY_ID      # or reject, retract, erase
veos memory revise MEMORY_ID --statement "New wording"
veos memory history MEMORY_ID
veos memory brief --project-id YOUR_PROJECT_ID
veos memory performance
```

`veos memory brief` returns what an executive brief must rest on: your direction, the project's goals, decisions, next actions and correlations, its experiments and relationships, each claim with its confidence history and timestamped sources, the claims whose confidence fell, those left without a standing source, and what awaits your review. `veos memory performance` reports real usage by provider, task and tool, for everything or one project (`--project-id`): runs, attempts and seconds. Every provider is a subscription client, so the metered cost is zero and is reported as such. No claim is presented as verified fact.

Memory is local to this OS user, like the rest of the journal, and `veos export` includes it. Retrieval matches literal words; there is no embedding model or separately billed search. It is not yet synchronized with the Ve memory engine, so it does not follow you to another device.

### Bounded goal plans

An accepted, active goal can now drive a reviewed plan of 1–10 steps through the existing task queue. An agent proposes a plan with `propose_goal_plan` and reads its state with `goal_progress`; managed workers and direct MCP harnesses share these tools. The three-tool incoming delegation/voice interface stays unchanged. Proposing a plan starts no worker and enables no provider. Only the owner accepts it through the CLI or authenticated owner API.

Each step names a provider, an instruction, expected outputs, dependencies on earlier step keys and an attempt limit (1–5, default 1). Requiring earlier dependencies makes the graph acyclic. Goal context travels with every step, including success criteria, constraints and stop conditions; source text and goal records grant no permissions. The existing worker still checks native subscription sign-in and project grants. External actions still require their own exact-action approval.

Save a step array as `plan.json`, for example:

```json
[
  {"key":"research","provider":"claude","instruction":"Collect evidence for the opportunity","outputs":["Evidence IDs, source dates and uncertainties"]},
  {"key":"brief","provider":"codex","instruction":"Compare invent, partner and wait","dependencies":["research"],"outputs":["Source-linked brief with alternatives and decisions needed"]}
]
```

```sh
veos goal plan GOAL_ID --project-id PROJECT_ID --request-key opportunity-review-v1 < plan.json
veos goal progress GOAL_ID --project-id PROJECT_ID
veos goal accept PLAN_ID --project-id PROJECT_ID
veos goal cancel PLAN_ID --project-id PROJECT_ID
```

Use `reject` to decline a proposed plan. Add `--signal-id SIGNAL_ID` when proposing a follow-up to a signal that reawakens that goal. One signal gets at most one plan per goal version, even after rejection or cancellation; a repeated request key returns the original plan, while different work under the same key is refused. A new investigation without that trigger requires a new, separately reviewed plan.

Accepting a plan creates its tasks and dependencies in one transaction; a full queue rolls back the entire acceptance. Repeated or concurrent approval creates no duplicate tasks. Accepted tasks appear in the existing Mission Desk work queue. Plan creation, review and detailed progress are currently CLI/API/MCP features; there is no dedicated plan editor in the page.

A goal's `detail.constraints.max_runs` (integer 1–1000) caps all runs from all its plans, including retries and action-receipt continuations. It defaults to the project's run cap. `deadline_days` (integer 1–366) is measured from that goal version's creation time. The project budget and deadline also apply. Acceptance checks that the plan's requested attempts fit the remaining budgets; it does not reserve runs, so another plan may consume them first. Every claim rechecks the limits transactionally. Exhaustion blocks remaining work with a reason; a goal deadline also fences active runs and new action approvals. Providers remain disabled until explicitly configured.

Revising a goal, including pausing or marking it met/stopped, or retracting it cancels that version's proposed plans, unfinished tasks and pending action approvals in the same transaction. Completed results remain in history. An effect already submitted may still finish. Resuming a revised goal requires a new plan and approval. Pausing the whole project instead retains the existing project pause/resume behavior.

Progress reports task states, result previews, remaining work and run allowance, five plans per page. Follow `next_offset` with CLI `--offset`, the API `offset` query parameter or the MCP `offset` argument. Successful tasks produce `needs_outcome_review`; they never automatically mark a goal met. Expected outputs and natural-language stop conditions are instructions for the worker and owner to evaluate, not a semantic validator. Dollar budgets are not enforced by this subscription-run counter. No automatic feed watcher, model planner, evaluator, provider switching or cloud supervisor is introduced.

Owner API: `POST /api/goals/plans` takes `{pid, goal_id, request_key, steps, signal_id?}`; `POST /api/goals/plans/decide` takes `{pid, id, command}`; `GET /api/goals/progress?project=PROJECT_ID&id=GOAL_ID` returns the reviewed goal version and its plans. Plan data is included in project export. Schema version 6 adds the plan table and nullable task reference without rewriting existing records.

This extends the local foundations from [runtime #2](https://github.com/veailabs/Ve_Os/pull/2), [agent controls #3](https://github.com/veailabs/Ve_Os/pull/3), [harness portability #4](https://github.com/veailabs/Ve_Os/pull/4) and memory #5–#6. It implements a local slice of the Element OS blueprint v1.0 sections 8–9 and 14: goal contracts, bounded task graphs, idempotency and run limits. The blueprint's cloud supervisor, Postgres/Vespa authority, automatic evidence evaluation and live multi-provider acceptance remain separate work.

## Optional Ve connection

The mail adapter is disabled by default. It requires an independently verified HTTPS Integration endpoint, mailbox UUID and exact read/write tool allowlists in the state directory's `config.json`. It accepts a user-authorized short-lived `VEOS_VE_ACCESS_TOKEN` through the runtime environment, mints a scoped MCP grant and binds every tool to the configured mailbox. It does not forward the user token to the agent.

Interactive Ve sign-in, secure token persistence and refresh are not implemented. Expired access fails closed. Do not enable a tool whose backend introduces separately billed inference under a subscription-only configuration. A mocked transport proves adapter behavior, not the live service or account setup.

Writes require review and an explicit success receipt. Duplicate approval races submit locally at most once; uncertain remote outcomes do not retry. Mutable draft-send-by-ID operations are refused until an immutable content/version contract exists. This does not claim exactly-once remote delivery.

## Local storage and trust boundary

State defaults to `~/.local/state/veos` or `$XDG_STATE_HOME/veos`. Set `--state-dir` before the CLI subcommand to use an independent workspace.

| Path | Contents |
|---|---|
| `state.sqlite3` | Projects, tasks, runs, evidence, claims, memories, records, entities, signals, held signals, links, action receipts, browser/desktop grants, harness task scope, file metadata and events |
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
