// Podcast Workspace UI. Plain JS, no build step. All server/third-party text is inserted with
// textContent (never innerHTML) so inspected repo descriptions cannot inject markup.
"use strict";

const $ = (s, r = document) => r.querySelector(s);
function h(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v === true) e.setAttribute(k, "");
    else if (v !== false && v != null) e.setAttribute(k, v);
  }
  for (const k of kids.flat()) if (k != null && k !== false) e.append(k.nodeType ? k : document.createTextNode(String(k)));
  return e;
}
const badge = (txt, cls) => h("span", { class: "badge b-" + cls }, txt);
const usd = (n) => "US$" + Number(n).toFixed(2);

async function api(path, opts = {}) {
  const r = await fetch("/api" + path, {
    credentials: "same-origin",
    headers: opts.body ? { "Content-Type": "application/json" } : {},
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (r.status === 401) { showLogin(); throw new Error("signed out"); }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const d = data.detail;
    throw new Error(typeof d === "string" ? d : d && d.errors ? d.errors.join("\n") : JSON.stringify(d || data));
  }
  return data;
}

// ------------------------------------------------------------------ session
function showLogin() { $("#app").hidden = true; $("#login").hidden = false; }
async function boot() {
  try { await refreshState(); } catch { return showLogin(); }
  $("#login").hidden = true; $("#app").hidden = false;
  loadChat(); loadModels(); loadProjects(); loadJobs(); renderCostNotes();
}
$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await fetch("/api/login", { method: "POST", headers: { "Content-Type": "application/json" }, credentials: "same-origin",
      body: JSON.stringify({ token: $("#token").value }) }).then(async (r) => { if (!r.ok) throw new Error((await r.json()).detail); });
    $("#token").value = ""; boot();
  } catch (err) { $("#login-err").textContent = err.message; }
});
$("#logout").addEventListener("click", async () => { await fetch("/api/logout", { method: "POST", credentials: "same-origin" }); showLogin(); });

let STATE = null;
async function refreshState() {
  STATE = await api("/state");
  const b = STATE.budget;
  const chip = $("#budget-chip");
  chip.textContent = `C$${b.spent_cad.toFixed(2)} / C$${b.monthly_budget_cad.toFixed(0)} this month`;
  chip.className = "chip " + (b.soft_alert ? "warn" : "");
  const c = $("#cloud-chip");
  c.textContent = STATE.cloud_enabled ? "cloud GPU: enabled" : "cloud GPU: off";
  c.className = "chip " + (STATE.cloud_enabled ? "ok" : "");
}
setInterval(() => { if (!$("#app").hidden) refreshState().catch(() => {}); }, 15000);

// ------------------------------------------------------------------ tabs
$("#tabs").addEventListener("click", (e) => {
  const t = e.target.dataset && e.target.dataset.tab; if (!t) return;
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b === e.target));
  document.querySelectorAll(".tab").forEach((s) => (s.hidden = s.id !== "tab-" + t));
  if (t === "projects") loadProjects(); if (t === "jobs") loadJobs(); if (t === "models") loadModels();
  if (t === "budget") loadBudget(); if (t === "terminal") openTerminal();
});
function gotoTab(t) { document.querySelector(`#tabs [data-tab="${t}"]`).click(); }

// ------------------------------------------------------------------ chat
function addMsg(role, text, extra) {
  const m = h("div", { class: "msg " + role }, text);
  if (extra) m.append(extra);
  $("#chat-log").append(m); m.scrollIntoView({ block: "end" });
  return m;
}
async function loadChat() {
  $("#chat-log").replaceChildren();
  const hist = await api("/chat");
  if (!hist.length) addMsg("assistant", "Hi. Describe an episode, or search/inspect models. Nothing runs or bills until you press Start (and approve, if it is paid).");
  for (const r of hist) addMsg(r.role, r.text, r.data ? renderReplyExtras(r.data) : null);
}
$("#chat-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = $("#chat-input").value.trim(); if (!text) return;
  $("#chat-input").value = "";
  addMsg("user", text);
  try {
    const r = await api("/chat", { method: "POST", body: { text } });
    addMsg("assistant", r.text, renderReplyExtras(r));
  } catch (err) { addMsg("assistant", "Error: " + err.message); }
});
function renderReplyExtras(r) {
  const out = h("div");
  if (r.proposal) out.append(projectCard(r.proposal.project, r.proposal));
  if (r.results) out.append(h("ul", { class: "tight" }, r.results.map((x) => h("li", {}, h("a", { href: x.url, target: "_blank", rel: "noopener noreferrer" }, x.repo),
    ` — ${x.stars != null ? "★" + x.stars : (x.downloads ?? 0) + " downloads"} ${x.license || x.pipeline || ""} `,
    h("button", { class: "ghost", onclick: () => inspectUrl(x.url) }, "inspect")))));
  if (r.inspection) out.append(inspectionView(r.inspection));
  if (r.plan) out.append(planView(r.plan));
  return out;
}

// ------------------------------------------------------------------ project card (plan, start, approve, progress)
function projectCard(p, extra = {}) {
  const spec = p.spec, pf = extra.preflight || { errors: [], warnings: [] }, est = extra.estimate;
  const card = h("div", { class: "card" });
  card.append(h("h2", {}, p.title || "Podcast project"),
    h("div", { class: "muted" }, `${spec.hosts.map((x) => x.name).join(" & ")} · ${Math.round(spec.duration_s)}s · ${spec.width}×${spec.height}@${spec.fps} · captions ${spec.captions ? "on" : "off"}`),
    h("div", {}, `Bottom-left name: “${spec.group_name}”`),
    h("div", {}, "Cameras: wide, close-up on each host"),
    h("div", {}, badge(spec.llm, "unknown"), badge(spec.tts, "unknown"), badge(spec.visuals, "unknown")));
  for (const n of extra.notes || []) card.append(h("p", { class: "muted" }, "ℹ " + n));
  if (pf.errors.length) card.append(h("h3", {}, "Blocked"), h("ul", { class: "tight err" }, pf.errors.map((x) => h("li", {}, x))));
  if (pf.warnings.length) card.append(h("h3", {}, "Heads up"), h("ul", { class: "tight" }, pf.warnings.map((x) => h("li", {}, x))));
  if (est) card.append(h("div", { class: "muted" }, est.lines.length
    ? `Estimated cloud cost: ${usd(est.typical_usd)} typical, up to ${usd(est.high_usd)} (${est.all_measured ? "from measured runs" : "built on ASSUMED throughput - not yet measured"})`
    : "Estimated cost: US$0.00 (runs on this machine's CPU)"));
  const slot = h("div", { class: "jobslot" });
  const start = h("button", { class: "primary", disabled: pf.errors.length > 0, onclick: async () => {
    start.disabled = true;
    try {
      const r = await api(`/projects/${p.id}/start`, { method: "POST", body: {} });
      slot.replaceChildren(jobView(r.job.id, r));
    } catch (err) { slot.replaceChildren(h("p", { class: "err" }, err.message)); start.disabled = false; }
  } }, est && est.lines.length ? "Request approval to start" : "Start (free)");
  card.append(h("div", { class: "row" }, start), slot);
  const running = (p.jobs || []).find((j) => ["queued", "running", "awaiting_approval"].includes(j.status));
  if (running) slot.append(jobView(running.id));
  return card;
}

function jobView(jobId, ctxInfo) {
  const box = h("div", { class: "card" });
  const title = h("div"), bar = h("div", { class: "progress" }, h("div")), logs = h("div", { class: "logs" }),
        actions = h("div", { class: "row" }), result = h("div");
  box.append(title, bar, actions, result, logs);
  let last = 0, es;
  const paint = (j) => {
    title.replaceChildren(h("strong", {}, `Job ${j.id}`), " ", badge(j.status, j.status === "succeeded" ? "working" : j.status === "failed" ? "blocked" : "unknown"),
      ` ${j.stage || ""} ${Math.round(j.progress * 100)}% · attempt ${j.attempts}/${j.max_attempts} · spent ${usd(j.spent_usd)}`);
    bar.firstChild.style.width = Math.round(j.progress * 100) + "%";
    actions.replaceChildren();
    if (j.status === "awaiting_approval") {
      const cap = h("input", { type: "number", step: "0.5", min: "0.5", value: (ctxInfo && ctxInfo.estimate ? Math.max(ctxInfo.estimate.high_usd, 1) : 5).toFixed(2), style: "width:7em" });
      actions.append(h("span", {}, "Approve spending up to US$"), cap, h("button", { class: "primary", onclick: async () => {
        try { await api(`/jobs/${j.id}/approve`, { method: "POST", body: { cost_cap_usd: parseFloat(cap.value) } }); refreshState(); }
        catch (err) { alert(err.message); } } }, "Approve & start"),
        h("span", { class: "muted" }, "The job stops itself at this cap."));
    }
    if (["queued", "running", "awaiting_approval"].includes(j.status))
      actions.append(h("button", { class: "danger", onclick: () => api(`/jobs/${j.id}/cancel`, { method: "POST" }) }, "Cancel"));
    if (["failed", "cancelled"].includes(j.status))
      actions.append(h("button", { onclick: async () => { const r = await api(`/jobs/${j.id}/retry`, { method: "POST" }); box.replaceWith(jobView(r.job.id, r)); } }, "Resume from checkpoints"));
    if (j.error) actions.append(h("span", { class: "err" }, j.error));
    if (j.status === "succeeded" && j.result && j.project_id) result.replaceChildren(videoView(j.project_id, j.result));
  };
  const addLog = (e) => { logs.append(h("div", { class: e.level }, `${new Date(e.ts * 1000).toLocaleTimeString()} ${e.message}`)); logs.scrollTop = logs.scrollHeight; };
  es = new EventSource(`/api/jobs/${jobId}/stream`);
  es.onmessage = (m) => { const e = JSON.parse(m.data); last = e.id; addLog(e); };
  es.addEventListener("status", (m) => paint(JSON.parse(m.data)));
  es.addEventListener("done", () => { es.close(); refreshState(); loadProjects(); });
  es.onerror = () => { /* browser retries; a closed tab leaves the job running server-side */ };
  api(`/jobs/${jobId}`).then(paint).catch(() => {});
  return box;
}

function videoView(pid, result) {
  const f = (n, dl) => `/api/projects/${pid}/files/${n}${dl ? "?download=true" : ""}`;
  const v = result.verified || {};
  return h("div", {},
    h("video", { controls: true, preload: "metadata", src: f("episode.mp4") }),
    h("div", { class: "row" }, h("a", { href: f("episode.mp4", true) }, "Download MP4"), h("a", { href: f("captions.srt", true) }, "Captions (.srt)"),
      h("a", { href: f("script.json", true) }, "Script (.json)")),
    h("div", { class: "muted" }, `Measured: ${v.duration_s}s · ${v.width}×${v.height} · ${v.video_codec}/${v.audio_codec} · ${v.size_mb} MB`),
    result.draft_quality ? h("div", { class: "muted" }, "Draft-quality stylised animatic from the free renderer — not realistic video. Never published automatically.") : null);
}

// ------------------------------------------------------------------ projects & jobs lists
async function loadProjects() {
  const ps = await api("/projects"), root = $("#projects");
  root.replaceChildren(ps.length ? null : h("p", { class: "muted" }, "No projects yet. Describe one in Chat."));
  for (const p of ps) {
    const c = h("div", { class: "card" }, h("h2", {}, p.title), h("div", { class: "muted" }, `${p.status} · ${Math.round(p.spec.duration_s)}s · created ${new Date(p.created_at * 1000).toLocaleString()}`));
    const ok = p.jobs.find((j) => j.status === "succeeded" && j.result);
    if (p.has_video && ok) c.append(videoView(p.id, ok.result));
    c.append(h("div", { class: "row" },
      h("button", { onclick: async () => { try { const r = await api(`/projects/${p.id}/start`, { method: "POST", body: {} }); c.append(jobView(r.job.id, r)); } catch (e) { alert(e.message); } } }, "Run again"),
      h("button", { class: "danger", onclick: async () => { if (confirm("Delete this project and its files?")) { try { await api(`/projects/${p.id}`, { method: "DELETE" }); loadProjects(); } catch (e) { alert(e.message); } } } }, "Delete")));
    const act = p.jobs.find((j) => ["queued", "running", "awaiting_approval"].includes(j.status));
    if (act) c.append(jobView(act.id));
    root.append(c);
  }
}
async function loadJobs() {
  const js = await api("/jobs"), root = $("#jobs");
  root.replaceChildren(js.length ? null : h("p", { class: "muted" }, "No jobs yet."));
  for (const j of js) {
    const row = h("div", { class: "card" }, h("strong", {}, `${j.kind} ${j.id}`), " ", badge(j.status, j.status === "succeeded" ? "working" : j.status === "failed" ? "blocked" : "unknown"),
      h("div", { class: "muted" }, `${Math.round(j.progress * 100)}% · spent ${usd(j.spent_usd)} · ${j.error || ""}`));
    row.append(h("button", { class: "ghost", onclick: () => row.append(jobView(j.id)) }, "Show logs"));
    root.append(row);
  }
}

// ------------------------------------------------------------------ models & research
const STATUS_HELP = {
  working: "tested end-to-end here", "implemented-untested": "code exists, never run against the real service",
  placeholder: "stub only", candidate: "researched, no adapter", blocked: "licence forbids commercial use",
};
async function loadModels() {
  const ms = await api("/models");
  $("#registry-note").textContent = `Licence and size facts read from Hugging Face on ${STATE.registry_verified_on}. Re-check before commercial use.`;
  $("#legend").replaceChildren(...Object.entries(STATUS_HELP).map(([k, v]) => h("span", { style: "margin-right:12px" }, badge(k, k), v)));
  const t = h("table", {}, h("tr", {}, ["Model", "Kind", "Status", "Licence", "Runs on", "Hardware", "Install", ""].map((x) => h("th", {}, x))));
  for (const m of ms) {
    const hw = m.hardware || {};
    t.append(h("tr", {},
      h("td", {}, h("strong", {}, m.name), h("div", { class: "muted" }, m.id), m.notes ? h("div", { class: "muted" }, m.notes) : null),
      h("td", {}, m.kind), h("td", {}, badge(m.status, m.status)),
      h("td", {}, badge(m.license.class, m.license.class), h("div", { class: "muted" }, m.license.name)),
      h("td", {}, m.runs_on), h("td", {}, hw.weights_gb ? `${hw.weights_gb} GB weights · ${hw.suggested_gpu || ""}` : "—"),
      h("td", {}, m.install.status, m.install.detail ? h("div", { class: "muted" }, m.install.detail.slice(0, 80)) : null),
      h("td", {}, h("button", { onclick: async () => showPlan(m.id) }, "Plan"), " ",
        h("button", { onclick: async () => { try { const j = await api(`/models/${m.id}/install`, { method: "POST", body: {} }); gotoTab("jobs"); } catch (e) { alert(e.message); } } }, "Install"))));
  }
  $("#models-table").replaceChildren(t);
}
function planView(p) {
  return h("div", { class: "card" }, h("h2", {}, "Install plan: " + p.name),
    h("div", {}, `Installs to: ${p.installs_to}`), h("div", {}, `Weights: ${p.weights_gb} GB · pinned revision ${p.pinned_revision ? p.pinned_revision.slice(0, 10) : "n/a"}`),
    h("div", {}, `Storage if billed: ~${usd(p.storage_usd_per_month_if_billed)}/month (list price). Owner approval needed: ${p.needs_owner_approval ? "yes" : "no"}.`),
    h("div", {}, "Third-party code executed on the app server: none."),
    p.blockers.length ? h("ul", { class: "tight err" }, p.blockers.map((b) => h("li", {}, b))) : h("div", { class: "muted" }, "No blockers."));
}
async function showPlan(id) { $("#research-out").replaceChildren(planView(await api(`/models/${id}/plan`))); }
function inspectionView(r) {
  const c = r.compatibility, d = h("div", { class: "card" });
  d.append(h("h2", {}, `${r.repo} (${r.type})`),
    h("div", {}, badge("licence: " + r.license.class, r.license.class), r.license.id || "none declared", r.license_name ? ` (${r.license_name})` : ""),
    h("div", { class: "muted" }, r.license.note),
    h("div", {}, `Revision: ${r.revision ? r.revision.slice(0, 12) : "unknown"}`),
    r.weights_gb != null ? h("div", {}, `Weights: ${r.weights_gb} GB of ${r.repo_size_gb} GB repo · VRAM planning estimate ${r.hardware.estimate_gb} GB → ${r.hardware.suggested_gpu} (${r.hardware.basis})`) : null,
    r.gated ? h("div", { class: "err" }, "Gated repo") : null,
    r.dependencies.length ? h("div", { class: "muted" }, `${r.dependencies.length} dependencies parsed (${r.dependencies.filter((x) => x.pinned).length} pinned)`) : null,
    h("div", {}, badge(c.verdict, c.verdict === "registered" ? "working" : "unknown"), c.message));
  if (r.warnings.length) d.append(h("h3", {}, "Warnings"), h("ul", { class: "tight" }, r.warnings.map((w) => h("li", {}, w))));
  if (c.verdict !== "registered") d.append(h("div", { class: "row" }, h("button", { onclick: async () => {
    try { const s = await api(`/research/scaffold?url=${encodeURIComponent(r.type === "github" ? "https://github.com/" + r.repo : "https://huggingface.co/" + r.repo)}`, { method: "POST" }); alert(`Draft written to ${s.manifest}\n${s.next}`); }
    catch (e) { alert(e.message); } } }, "Scaffold a custom adapter draft"), h("span", { class: "muted" }, "Writes a manifest only. Nothing is downloaded or run.")));
  return d;
}
async function inspectUrl(url) {
  $("#research-out").replaceChildren(h("p", { class: "muted" }, "Inspecting…"));
  gotoTab("models");
  try { $("#research-out").replaceChildren(inspectionView(await api(`/research/inspect?url=${encodeURIComponent(url)}`))); }
  catch (e) { $("#research-out").replaceChildren(h("p", { class: "err" }, e.message)); }
}
$("#inspect-form").addEventListener("submit", (e) => { e.preventDefault(); inspectUrl($("#inspect-url").value.trim()); });
$("#search-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const out = $("#research-out"); out.replaceChildren(h("p", { class: "muted" }, "Searching…"));
  try {
    const res = await api(`/research/search?q=${encodeURIComponent($("#search-q").value)}&source=${$("#search-src").value}`);
    out.replaceChildren(h("div", { class: "card" }, renderReplyExtras({ results: res })));
  } catch (err) { out.replaceChildren(h("p", { class: "err" }, err.message)); }
});

// ------------------------------------------------------------------ budget
async function loadBudget() {
  await refreshState();
  const b = STATE.budget;
  $("#budget-card").replaceChildren(h("h2", {}, "This month"),
    h("div", {}, `Tracked spend: C$${b.spent_cad.toFixed(2)} (${usd(b.spent_usd)}) of C$${b.monthly_budget_cad.toFixed(0)} cap (${usd(b.monthly_budget_usd)} at ${b.usd_per_cad} USD/CAD — update USD_PER_CAD).`),
    h("div", {}, `Reserved by approved jobs: ${usd(b.reserved_usd)} · remaining ${usd(b.remaining_usd)} · per-job cap C$${b.per_job_cap_cad}`),
    h("p", { class: "muted" }, "These numbers come from this app's own ledger of measured GPU seconds. Also set a spend limit in the provider's dashboard: that is the authoritative cap."),
    b.breakdown.length ? h("table", {}, h("tr", {}, ["Provider", "Resource", "Units", "USD"].map((x) => h("th", {}, x))), ...b.breakdown.map((r) => h("tr", {}, h("td", {}, r.provider), h("td", {}, r.resource), h("td", {}, `${r.units} ${r.unit}`), h("td", {}, r.usd)))) : h("p", { class: "muted" }, "No usage recorded yet."));
}
$("#est-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const dur = Math.round(parseFloat($("#est-min").value) * 60), cloud = $("#est-cloud").checked;
  const r = await api(`/estimate?duration_s=${dur}&visuals=${cloud ? "infinitetalk" : "procedural-draft"}`);
  $("#est-out").replaceChildren(r.lines.length ? h("div", {}, h("table", {}, h("tr", {}, ["Stage", "GPU", "GPU-seconds", "USD", "Basis"].map((x) => h("th", {}, x))),
      ...r.lines.map((l) => h("tr", {}, h("td", {}, l.stage), h("td", {}, l.gpu), h("td", {}, l.gpu_seconds), h("td", {}, l.usd), h("td", {}, l.basis)))),
      h("p", {}, `Typical ${usd(r.typical_usd)} (C$${r.typical_cad}); high ${usd(r.high_usd)} (C$${r.high_cad}).`),
      h("p", { class: "muted" }, "ASSUMED rows are guesses until a real run is measured. Treat the high figure as the planning number.")) : h("p", {}, "US$0.00 — CPU only."));
});
function renderCostNotes() {
  $("#cost-notes").replaceChildren(h("ul", { class: "tight" },
    h("li", {}, "GPU time: billed per second only while a container runs. With scale-to-zero there is no idle GPU charge."),
    h("li", {}, "Idle tail: after each call the container stays warm for the scaledown window (default 60 s, we set 10 s) and that tail IS billed. 100 short calls ≈ 100 tails — batch work into few long calls."),
    h("li", {}, "Storage: model weights live on a persistent volume billed per GiB-month even when nothing runs. ~150–250 GB of weights is a real monthly line item; delete weights you stop using."),
    h("li", {}, "Egress: small (a 20-minute MP4 is hundreds of MB). Your app host (a small VM) is a separate, constant monthly cost."),
    h("li", {}, "Builds and downloads: image builds and weight downloads use CPU time and bandwidth and are billed like any other function.")));
}

// ------------------------------------------------------------------ terminal
let termOpen = false;
function openTerminal() {
  if (termOpen) return;
  if (!STATE.terminal_enabled) { $("#term-msg").textContent = "Terminal is disabled. Set ENABLE_TERMINAL=1 on the server to turn it on (owner-only; see docs/SECURITY.md)."; return; }
  termOpen = true;
  $("#term-msg").textContent = "Connected as owner. This is a real shell on the server with a scrubbed environment.";
  const term = new Terminal({ cursorBlink: true, fontSize: 13, theme: { background: "#0a0d11" } });
  const fit = new FitAddon.FitAddon(); term.loadAddon(fit); term.open($("#term")); fit.fit();
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/terminal`);
  ws.binaryType = "arraybuffer";
  ws.onopen = () => ws.send(JSON.stringify({ type: "resize", rows: term.rows, cols: term.cols }));
  ws.onmessage = (e) => term.write(new Uint8Array(e.data));
  ws.onclose = (e) => { termOpen = false; term.write(`\r\n[disconnected${e.code >= 4000 ? ` (code ${e.code})` : ""}]\r\n`); };
  term.onData((d) => ws.readyState === 1 && ws.send(new TextEncoder().encode(d)));
  addEventListener("resize", () => { fit.fit(); if (ws.readyState === 1) ws.send(JSON.stringify({ type: "resize", rows: term.rows, cols: term.cols })); });
}

boot();
