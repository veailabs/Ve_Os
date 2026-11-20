"use strict";
const $ = (id) => document.getElementById(id);
const incoming = location.hash.slice(1);
if (incoming) {
  sessionStorage.setItem("veos-session", incoming);
  history.replaceState(null, "", "/");
}
const token = sessionStorage.getItem("veos-session");
let state = {
  projects: [],
  tasks: [],
  evidence: [],
  claims: [],
  actions: [],
  claim_evidence: [],
  claim_revisions: [],
  memories: [],
  entities: [],
  signals: [],
  links: [],
};
const memoryKinds = {
  executive: ["strategy", "preference", "risk_appetite", "operating_rule", "priority"],
  project: ["decision", "outcome", "next_action", "timeline", "note"],
  procedural: ["workflow", "tool_performance", "retry_lesson", "failure_pattern"],
};
const memoryScopes = {
  executive: "Your direction",
  project: "This project",
  procedural: "How work is done",
};
let selected = sessionStorage.getItem("veos-project");
let revision = 0;
let voiceClient = null;
let voiceTask = null;
let voiceAvailable = false;
const names = {
  codex: "Codex · ChatGPT",
  claude: "Claude Code · Claude",
  hermes: "Hermes · ChatGPT",
  antigravity: "Antigravity · Google",
};
function node(tag, text, cls) {
  const e = document.createElement(tag);
  if (text !== undefined) e.textContent = text;
  if (cls) e.className = cls;
  return e;
}
function message(text) {
  $("message").textContent = text;
  $("message").hidden = !text;
}
async function api(path, data) {
  const r = await fetch(path, {
    method: data === undefined ? "GET" : "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: data === undefined ? undefined : JSON.stringify(data),
  });
  const body = await r.json();
  if (!r.ok) throw new Error(body.error || "The request did not complete.");
  return body;
}
async function perform(fn) {
  try {
    message("");
    await fn();
    await refresh();
  } catch (error) {
    message(error.message);
  }
}
function button(text, action, cls) {
  const b = node("button", text, cls);
  b.type = "button";
  b.onclick = () => perform(action);
  return b;
}
function blank(text) {
  return node("p", text, "hint");
}
function badge(value) {
  return node("span", value.replaceAll("_", " "), `badge ${value}`);
}
function details(label, text, pre = false) {
  const d = node("details");
  d.append(
    node("summary", label),
    node(pre ? "pre" : "div", text, pre ? "" : "result"),
  );
  return d;
}
function scoped(kind) {
  return state[kind].filter((x) => x.project_id === selected);
}
function project() {
  return state.projects.find((x) => x.id === selected);
}
function localTime(time) {
  return new Date(time * 1000).toLocaleString();
}
function memoryDialog(memory) {
  const form = $("memory-form"),
    scope = form.elements.scope,
    kind = form.elements.kind;
  const kinds = () =>
    kind.replaceChildren(
      ...memoryKinds[scope.value].map((k) => {
        const o = node("option", k.replaceAll("_", " "));
        o.value = k;
        return o;
      }),
    );
  form.reset();
  form.dataset.revise = memory?.id || "";
  scope.value = memory?.scope || "executive";
  scope.onchange = kinds;
  kinds();
  if (memory) {
    kind.value = memory.kind;
    form.elements.statement.value = memory.statement;
  }
  scope.disabled = kind.disabled = !!memory;
  $("memory-dialog").showModal();
}
function memoryCard(m) {
  const c = node("article", undefined, "card"),
    meta = node("div", undefined, "meta"),
    actions = node("div", undefined, "actions"),
    origin = state.projects.find((p) => p.id === m.project_id);
  meta.append(
    node("span", `${memoryScopes[m.scope]} · ${m.kind.replaceAll("_", " ")}`),
    badge(m.state),
  );
  c.append(
    meta,
    node("p", m.statement),
    blank(
      `${m.author === "agent" ? `Written by an agent${origin ? ` in ${origin.name}` : ""}` : "Written by you"} · ${localTime(m.created)}`,
    ),
  );
  if (m.state === "proposed")
    actions.append(
      button("Accept", () => api(`/api/memory/${m.id}/accept`, {}), "primary"),
      button("Reject", () => api(`/api/memory/${m.id}/reject`, {})),
    );
  else {
    if (m.state === "needs_review")
      actions.append(
        button("Keep", () => api(`/api/memory/${m.id}/accept`, {}), "primary"),
      );
    actions.append(
      button("Revise", async () => memoryDialog(m)),
      button("Retract", () => {
        if (confirm("Retract this memory? Agents will stop receiving it."))
          return api(`/api/memory/${m.id}/retract`, {});
      }),
    );
  }
  c.append(actions);
  return c;
}
function entityCard(e) {
  const c = node("article", undefined, "card"),
    meta = node("div", undefined, "meta"),
    actions = node("div", undefined, "actions");
  meta.append(node("span", `Entity · ${e.kind}`), badge(e.state));
  c.append(
    meta,
    node("p", e.name),
    blank("An agent could not tell whether this is a record you already have."),
  );
  for (const link of state.links.filter(
    (l) => l.relation === "possible_same_as" && l.subject_id === e.id,
  )) {
    const other = state.entities.find((x) => x.id === link.object_id);
    if (other)
      actions.append(
        button(`Same as ${other.name}`, () =>
          api("/api/entities/merge", { pid: selected, id: e.id, into: other.id }),
        ),
      );
  }
  actions.append(
    button("A different entity", () =>
      api("/api/entities/distinct", { pid: selected, id: e.id }),
    ),
  );
  c.append(actions);
  return c;
}
function count(n, one, many = `${one}s`) {
  return `${n} ${n === 1 ? one : many}`;
}
function renderMemory() {
  const order = ["proposed", "needs_review", "active"],
    memories = state.memories
      .filter(
        (m) =>
          order.includes(m.state) &&
          (m.scope !== "project" || m.project_id === selected),
      )
      .sort(
        (a, b) =>
          order.indexOf(a.state) - order.indexOf(b.state) ||
          b.created - a.created,
      ),
    unsettled = scoped("entities").filter((e) => e.state === "ambiguous"),
    entities = scoped("entities").filter((e) => e.state !== "merged").length;
  $("memory").replaceChildren(
    ...unsettled.map(entityCard),
    ...memories.map(memoryCard),
    blank(
      memories.length || unsettled.length
        ? `Recorded in this project: ${count(entities, "entity", "entities")}, ${count(scoped("signals").length, "signal")}, ${count(scoped("links").length, "link")}.`
        : "Nothing remembered yet. Add your standing direction, or let an agent record what this project decides.",
    ),
  );
}
function render() {
  $("projects").replaceChildren(
    ...state.projects.map((p) =>
      button(
        p.name,
        () => {
          selected = p.id;
          sessionStorage.setItem("veos-project", selected);
          render();
        },
        p.id === selected ? "selected" : "",
      ),
    ),
  );
  const p = project();
  if (voiceClient?.projectId && (voiceClient.projectId !== selected || p?.state !== "active")) voiceClient.stop();
  $("empty").hidden = !!p;
  $("project").hidden = !p;
  if (!p) return;
  $("files").replaceChildren(
    ...(state.artifacts || [])
      .filter((f) => f.project_id === selected)
      .map((file) => {
        const c = node("div", undefined, "card");
        c.append(
          node("strong", file.name),
          blank(
            `${file.size.toLocaleString()} bytes · ${file.sha256.slice(0, 12)}…`,
          ),
          button("Download file", async () => {
            const r = await api(
              `/api/files/content?project=${encodeURIComponent(selected)}&id=${encodeURIComponent(file.id)}`,
            );
            const bytes = Uint8Array.from(atob(r.base64), (c) =>
              c.charCodeAt(0),
            );
            const url = URL.createObjectURL(
              new Blob([bytes], { type: r.file.media_type }),
            );
            const a = node("a");
            a.href = url;
            a.download = r.file.name;
            a.click();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
          }),
        );
        return c;
      }),
  );
  $("project-name").textContent = p.name;
  $("objective").textContent = p.objective;
  $("project-state").textContent =
    `${p.state} project${p.deadline ? " · Deadline " + localTime(p.deadline) : ""}`;
  $("run-budget").textContent = `${p.runs_used} / ${p.max_runs}`;
  $("task-count").textContent = scoped("tasks").length;
  $("evidence-count").textContent = scoped("evidence").filter(
    (e) => e.state === "active",
  ).length;
  $("approval-count").textContent = scoped("actions").filter(
    (a) => a.state === "pending",
  ).length;
  $("pause").textContent = p.state === "paused" ? "Resume" : "Pause";
  for (const id of ["pause", "close-project", "new-task"])
    $(id).disabled = p.state === "closed";
  const tasks = scoped("tasks").slice().reverse();
  $("tasks").replaceChildren(
    ...(tasks.length
      ? tasks.map((t) => {
          const c = node("article", undefined, "card"),
            meta = node("div", undefined, "meta");
          meta.append(node("span", names[t.provider]), badge(t.state));
          c.append(meta, node("h3", t.instruction));
          if (t.not_before > Date.now() / 1000)
            c.append(blank("Scheduled: " + localTime(t.not_before)));
          if (t.reason) c.append(blank(t.reason.replaceAll("_", " ")));
          if (t.result) c.append(details("Read result", t.result));
          const runs = (state.runs || []).filter((r) => r.task_id === t.id);
          if (runs.length)
            c.append(
              details(
                "Run history",
                runs
                  .map(
                    (r) =>
                      `${localTime(r.started)} · ${r.provider} · ${r.state}${r.reason ? " · " + r.reason : ""}`,
                  )
                  .join("\n"),
                true,
              ),
            );
          const actions = node("div", undefined, "actions");
          if (t.state === "proposed")
            actions.append(
              button("Accept task", () => api(`/api/tasks/${t.id}/accept`, {})),
            );
          if (
            ["blocked", "failed", "unknown", "waiting_subscription"].includes(
              t.state,
            ) &&
            t.attempts < t.max_attempts
          )
            actions.append(
              button("Retry after review", () => {
                if (
                  confirm(
                    "Check any external outcome first. Start another attempt using the same provider and the remaining project budget?",
                  )
                )
                  return api(`/api/tasks/${t.id}/retry`, {});
              }),
            );
          if (!["succeeded", "cancelled"].includes(t.state))
            actions.append(
              button("Cancel", () => api(`/api/tasks/${t.id}/cancel`, {})),
            );
          c.append(actions);
          return c;
        })
      : [blank("No tasks yet. Add a concrete next step to begin.")]),
  );
  $("claims").replaceChildren(
    ...(scoped("claims").length
      ? scoped("claims").map((c) => {
          const e = node("article", undefined, "card"),
            meta = node("div", undefined, "meta");
          meta.append(node("span", c.kind), badge(c.state));
          e.append(meta, node("h3", c.statement));
          const scores = state.claim_revisions
            .filter((r) => r.claim_id === c.id && r.confidence !== null)
            .map((r) => r.confidence);
          if (c.confidence !== null && c.confidence !== undefined)
            e.append(
              blank(
                `Confidence ${c.confidence}%${scores.length > 1 && scores.at(-1) < scores.at(-2) ? ` · lowered from ${scores.at(-2)}%` : ""}`,
              ),
            );
          for (const link of state.claim_evidence.filter(
            (x) => x.claim_id === c.id,
          )) {
            const source = state.evidence.find(
              (x) => x.id === link.evidence_id,
            );
            e.append(
              blank(
                `${link.relation === "supports" ? "Supports" : "Contradicts"}: ${source?.uri || link.evidence_id}`,
              ),
            );
          }
          return e;
        })
      : [
          blank(
            "Assessments will appear here with their supporting and contradicting sources.",
          ),
        ]),
  );
  $("evidence").replaceChildren(
    ...(scoped("evidence").length
      ? scoped("evidence")
          .slice()
          .reverse()
          .map((e) => {
            const c = node("article", undefined, "card"),
              link = node("a", e.uri, "source");
            if (/^https?:\/\//i.test(e.uri)) {
              link.href = e.uri;
              link.target = "_blank";
              link.rel = "noopener noreferrer";
            }
            c.append(
              link,
              node("blockquote", e.excerpt),
              blank(`Recorded ${localTime(e.retrieved)} · ${e.state}`),
            );
            if (e.state === "active")
              c.append(
                button("Withdraw source", () => {
                  if (
                    confirm(
                      "Withdraw this source and mark linked assessments for review?",
                    )
                  )
                    return api("/api/evidence/withdraw", {
                      pid: selected,
                      id: e.id,
                    });
                }),
              );
            return c;
          })
      : [
          blank(
            "Sources and excerpts recorded by you or an agent will stay with this project.",
          ),
        ]),
  );
  $("approvals").replaceChildren(
    ...(scoped("actions").length
      ? scoped("actions")
          .slice()
          .reverse()
          .map((a) => {
            const c = node("article", undefined, "card"),
              meta = node("div", undefined, "meta");
            meta.append(node("span", a.tool), badge(a.state));
            c.append(
              meta,
              details(
                "Exact action arguments",
                JSON.stringify(JSON.parse(a.args), null, 2),
                true,
              ),
            );
            if (a.state === "pending") {
              c.querySelector("details").open = true;
              c.append(
                blank(
                  `Expires ${localTime(a.expires)}. Review the complete arguments before approving.`,
                ),
              );
              const actions = node("div", undefined, "actions");
              actions.append(
                button(
                  "Approve exact action",
                  () => api(`/api/actions/${a.id}`, { approve: true }),
                  "primary",
                ),
                button("Deny", () =>
                  api(`/api/actions/${a.id}`, { approve: false }),
                ),
              );
              c.append(actions);
            }
            if (a.result)
              c.append(
                details(
                  "Action receipt",
                  JSON.stringify(JSON.parse(a.result), null, 2),
                  true,
                ),
              );
            return c;
          })
      : [
          blank(
            "No actions awaiting review. An agent can propose an external change; you approve it here.",
          ),
        ]),
  );
  renderMemory();
  browserStatus().catch((error) => message(error.message));
  desktopStatus().catch((error) => message(error.message));
}
async function desktopStatus() {
  const pid = selected;
  if (!pid) return;
  const result = await api(`/api/desktop?project=${encodeURIComponent(pid)}`);
  if (pid !== selected) return;
  const previous = $("settings-area").value;
  $("settings-area").replaceChildren(...result.settings_areas.map(area => {
    const option = node("option", area.replaceAll("_", " "));
    option.value = area;
    return option;
  }));
  if (result.settings_areas.includes(previous)) $("settings-area").value = previous;
  $("open-settings").disabled = !result.capabilities.find(c => c.operation === "open_settings")?.available;
  $("take-control").disabled = project()?.state !== "active";
  $("desktop-capabilities").replaceChildren(...result.capabilities.map(cap => {
    const row = node("div", undefined, "card");
    row.append(node("strong", cap.operation.replaceAll("_", " ")),
      blank(cap.reason || (cap.read_only ? "Read access" : "Changes require action review")),
      button(cap.allowed ? "Remove access" : "Allow access", () => api("/api/desktop/grant", {
        pid, operation: cap.operation, allow: !cap.allowed
      })));
    return row;
  }));
}
$("take-control").onclick = () => perform(async () => {
  await api(`/api/projects/${selected}`, {state: "paused"});
  message("Project paused. You have control. Inspect any action already submitted before resuming.");
});
$("open-settings").onclick = () => perform(async () => {
  await api("/api/desktop/settings", {pid: selected, area: $("settings-area").value});
  message("Settings opened on this computer. Opening the panel does not change its settings.");
});
async function browserStatus() {
  const pid = selected;
  if (!pid) return;
  const result = await api(`/api/browser?project=${encodeURIComponent(pid)}`);
  if (pid !== selected) return;
  $("browser-open").disabled = !result.available;
  $("browser-sites").replaceChildren(
    ...(!result.available
      ? [blank("Browser component is not installed.")]
      : []),
    ...result.origins.map((site) => {
      const row = node("div", undefined, "card");
      row.append(
        node("span", site),
        button("Remove access", () =>
          api("/api/browser/grant", { pid, url: site, allow: false }),
        ),
      );
      return row;
    }),
  );
}
$("browser-allow").onclick = () =>
  perform(() =>
    api("/api/browser/grant", { pid: selected, url: $("browser-url").value }),
  );
$("browser-open").onclick = () =>
  perform(async () => {
    await api("/api/browser/open", {
      pid: selected,
      url: $("browser-url").value,
    });
    message(
      "The project browser is open on this computer. You can sign in directly in its window.",
    );
  });
$("file-upload").onchange = () =>
  perform(async () => {
    const file = $("file-upload").files[0];
    if (!file) return;
    if (file.size > 5000000)
      throw new Error("Choose a file no larger than 5 MB.");
    const base64 = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result.split(",")[1]);
      reader.onerror = reject;
      reader.readAsDataURL(file);
    });
    const media = [
      "text/plain",
      "text/markdown",
      "text/csv",
      "application/json",
      "application/pdf",
      "image/png",
      "image/jpeg",
    ].includes(file.type)
      ? file.type
      : /\.md$/i.test(file.name)
        ? "text/markdown"
        : "application/octet-stream";
    await api("/api/files", {
      pid: selected,
      name: file.name,
      media_type: media,
      base64,
    });
    $("file-upload").value = "";
  });
async function refresh() {
  state = await api("/api/state");
  revision = state.revision;
  if (!project()) selected = state.projects[0]?.id || null;
  render();
}
async function providers() {
  const data = await api("/api/providers");
  $("providers").replaceChildren(
    ...Object.entries(data.providers).map(([id, p]) => {
      const e = node("div", undefined, "provider");
      e.append(
        node("strong", names[id]),
        node(
          "p",
          id === "antigravity"
            ? "Unavailable: subscription authentication still needs qualification."
            : !p.installed
              ? "Native client is not installed."
              : p.enabled
                ? "Enabled. Subscription sign-in is checked before every run."
                : "Installed; not enabled for execution.",
        ),
      );
      e.append(
        button("Check connection", async () => {
          const r = await api("/api/providers/check", { provider: id });
          message(`${names[id]}: ${r.reason.replaceAll("_", " ")}`);
        }),
      );
      return e;
    }),
  );
}
async function voiceStatus() {
  const result = await api("/api/voice/status");
  voiceAvailable = result.available;
  $("voice-start").disabled = !result.available;
  $("voice-status").textContent = result.available ? "API voice ready when you choose to connect" : result.reason;
}
$("voice-start").onclick = () => perform(async () => {
  if (!voiceClient) voiceClient = window.createVeOSVoice({
    api,
    audioOutput: $("voice-output"),
    onStatus: (text, active) => {
      $("voice-status").textContent = text;
      $("voice-start").disabled = active || !voiceAvailable;
      $("voice-disconnect").disabled = !active;
      $("voice-stop-speaking").disabled = !active;
    },
    onTask: id => { voiceTask = id; $("voice-cancel-task").disabled = false; },
    onTranscript: (speaker, text) => {
      const transcript = $("voice-transcript");
      transcript.append(node("p", `${speaker}: ${text.slice(0,8000)}`));
      while (transcript.children.length > 30) transcript.firstChild.remove();
    },
  });
  await voiceClient.start(selected, $("voice-provider").value);
});
$("voice-disconnect").onclick = () => voiceClient?.stop();
$("voice-stop-speaking").onclick = () => voiceClient?.stopSpeaking();
$("voice-cancel-task").onclick = () => perform(async () => {
  if (voiceTask) await api(`/api/tasks/${voiceTask}/cancel`,{});
  $("voice-cancel-task").disabled = true;
});
window.addEventListener("pagehide", () => voiceClient?.stop());
for (const b of document.querySelectorAll("[data-dismiss]"))
  b.onclick = () => b.closest("dialog").close();
$("new-project").onclick = $("first-project").onclick = () =>
  $("project-dialog").showModal();
$("new-task").onclick = () => {
  $("task-form").dataset.requestKey = crypto.randomUUID();
  $("dependencies").replaceChildren(
    ...scoped("tasks").map((t) => {
      const o = node("option", t.instruction.slice(0, 90));
      o.value = t.id;
      return o;
    }),
  );
  $("task-dialog").showModal();
};
$("new-evidence").onclick = () => $("evidence-dialog").showModal();
$("new-memory").onclick = () => memoryDialog();
$("new-claim").onclick = () => {
  for (const field of ["supporting", "contradicting"])
    $("claim-form").elements[field].replaceChildren(
      ...scoped("evidence")
        .filter((e) => e.state === "active")
        .map((e) => {
          const o = node("option", e.uri);
          o.value = e.id;
          return o;
        }),
    );
  $("claim-dialog").showModal();
};
function form(id, submit) {
  $(id).onsubmit = async (e) => {
    e.preventDefault();
    const form = e.currentTarget,
      send = form.querySelector("button.primary");
    send.disabled = true;
    try {
      await submit(new FormData(form));
      form.closest("dialog").close();
      form.reset();
      await refresh();
      message("");
    } catch (error) {
      message(error.message);
      form.closest("dialog").close();
    } finally {
      send.disabled = false;
    }
  };
}
form("project-form", async (f) => {
  const p = await api("/api/projects", {
    name: f.get("name"),
    objective: f.get("objective"),
    max_runs: Number(f.get("max_runs")),
    deadline: f.get("deadline")
      ? new Date(f.get("deadline")).getTime() / 1000
      : null,
  });
  selected = p.id;
});
form("task-form", (f) =>
  api("/api/tasks", {
    pid: selected,
    instruction: f.get("instruction"),
    provider: f.get("provider"),
    request_key: $("task-form").dataset.requestKey,
    dependencies: f.getAll("dependencies"),
    not_before: f.get("not_before")
      ? new Date(f.get("not_before")).getTime() / 1000
      : null,
  }),
);
form("evidence-form", (f) =>
  api("/api/evidence", {
    pid: selected,
    uri: f.get("uri"),
    excerpt: f.get("excerpt"),
  }),
);
form("memory-form", (f) =>
  $("memory-form").dataset.revise
    ? api("/api/memory/revise", {
        id: $("memory-form").dataset.revise,
        statement: f.get("statement"),
      })
    : api("/api/memory", {
        scope: f.get("scope"),
        kind: f.get("kind"),
        statement: f.get("statement"),
        pid: f.get("scope") === "project" ? selected : null,
      }),
);
form("claim-form", (f) =>
  api("/api/claims", {
    pid: selected,
    statement: f.get("statement"),
    kind: f.get("kind"),
    supporting: f.getAll("supporting"),
    contradicting: f.getAll("contradicting"),
  }),
);
$("pause").onclick = () =>
  perform(() =>
    api(`/api/projects/${selected}`, {
      state: project().state === "paused" ? "active" : "paused",
    }),
  );
$("close-project").onclick = () =>
  perform(() => {
    if (
      confirm(
        "Close this project? Pending tasks and approvals will be cancelled. Its records remain available.",
      )
    )
      return api(`/api/projects/${selected}`, { state: "closed" });
  });
$("export").onclick = () =>
  perform(async () => {
    const data = await api(
        `/api/export?project=${encodeURIComponent(selected)}`,
      ),
      url = URL.createObjectURL(
        new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }),
      ),
      a = node("a");
    a.href = url;
    a.download = `veos-${selected}.json`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
async function watch() {
  while (true) {
    try {
      const data = await api(`/api/events?after=${revision}`);
      if (data.events.length) await refresh();
    } catch (e) {
      message(
        `${e.message} Reopen the workspace with 'veos open' if the runtime restarted.`,
      );
      break;
    }
  }
}
if (!token)
  message(
    "Open this workspace with 'veos open' to establish your private local session.",
  );
else
  perform(async () => {
    await refresh();
    await providers();
    await voiceStatus();
    watch();
  });
