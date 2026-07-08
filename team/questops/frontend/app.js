/* QuestOps SPA — no build step, plain ES2020. */

const state = {
  token: localStorage.getItem("qo_token") || null,
  me: null,
  view: "focus",
  aiHistory: [],
  templates: [],
};

const $ = (sel) => document.querySelector(sel);
const view = () => $("#view");

/* ---------------- api ---------------- */
async function api(path, opts = {}) {
  const res = await fetch(path, {
    ...opts,
    headers: {
      "Content-Type": "application/json",
      ...(state.token ? { Authorization: `Bearer ${state.token}` } : {}),
    },
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (res.status === 401 && !path.startsWith("/api/login")) {
    logout();
    throw new Error("session expired");
  }
  if (!res.ok) {
    const detail = (await res.json().catch(() => ({}))).detail || res.statusText;
    throw new Error(detail);
  }
  return res.json();
}

/* ---------------- helpers ---------------- */
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// markdown-lite: bold, code, bullets — everything else escaped
function md(text) {
  const lines = esc(text).split("\n");
  let html = "", inList = false;
  for (const line of lines) {
    const bullet = line.match(/^\s*[-*•]\s+(.*)/);
    if (bullet) {
      if (!inList) { html += "<ul>"; inList = true; }
      html += `<li>${inline(bullet[1])}</li>`;
    } else {
      if (inList) { html += "</ul>"; inList = false; }
      if (line.trim()) html += `<p>${inline(line)}</p>`;
    }
  }
  if (inList) html += "</ul>";
  return `<div class="md">${html}</div>`;
  function inline(s) {
    return s.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
            .replace(/`(.+?)`/g, "<code>$1</code>");
  }
}

function ago(iso) {
  if (!iso) return "";
  const min = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (min < 1) return "just now";
  if (min < 60) return `${min}m ago`;
  if (min < 60 * 24) return `${Math.round(min / 60)}h ago`;
  return `${Math.round(min / 1440)}d ago`;
}

/* ---------------- toasts ---------------- */
function toast(html, cls = "", ms = 3800) {
  const el = document.createElement("div");
  el.className = `toast ${cls}`;
  el.innerHTML = html;
  $("#toasts").appendChild(el);
  setTimeout(() => { el.classList.add("out"); setTimeout(() => el.remove(), 450); }, ms);
}

function handleGame(game) {
  if (!game) return;
  if (game.points > 0)
    toast(`<b>+${game.points} XP</b> &nbsp;${esc(game.message)}`, "toast-xp");
  for (const q of game.quests_completed || [])
    toast(`🏁 <b>Quest complete:</b> ${esc(q.name)} <b>+${q.bonus} XP</b>`, "toast-quest", 5000);
  for (const b of game.new_badges || [])
    toast(`<span class="b-big">${b.icon}</span><b>Badge unlocked:</b> ${esc(b.name)}`, "toast-badge", 6000);
  if (game.level_up)
    toast(`⬆ <b>LEVEL ${game.level_up}</b> — ${esc(game.level.rank)}`, "toast-level", 6000);
  if (state.me) {
    state.me.level = game.level;
    state.me.streak = game.streak;
    renderTopbar();
  }
}

const oops = (e) => toast(`⚠ ${esc(e.message || e)}`, "toast-err", 5000);

/* ---------------- auth ---------------- */
async function boot() {
  const health = await fetch("/api/health").then((r) => r.json()).catch(() => ({}));
  if (health.demo_mode) $("#login-hint").classList.remove("hidden");
  $("#mode-chip").classList.toggle("hidden", !health.demo_mode);
  if (state.token) {
    try {
      state.me = await api("/api/me");
      showApp();
      return;
    } catch { /* fall through to login */ }
  }
  $("#login-screen").classList.remove("hidden");
}

$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("#login-error").textContent = "";
  try {
    const data = await api("/api/login", {
      method: "POST",
      body: { username: $("#login-user").value, password: $("#login-pass").value },
    });
    state.token = data.token;
    state.me = data.user;
    localStorage.setItem("qo_token", data.token);
    showApp();
  } catch (err) { $("#login-error").textContent = err.message; }
});

function logout() {
  localStorage.removeItem("qo_token");
  state.token = null; state.me = null;
  $("#app").classList.add("hidden");
  $("#login-screen").classList.remove("hidden");
}
$("#logout").addEventListener("click", logout);

function showApp() {
  $("#login-screen").classList.add("hidden");
  $("#app").classList.remove("hidden");
  renderTopbar();
  route();
}

/* ---------------- topbar ---------------- */
function renderTopbar() {
  const me = state.me;
  if (!me) return;
  const lv = me.level;
  $("#top-name").textContent = me.display_name || me.username;
  $("#top-rank").textContent = `LV ${lv.level} · ${lv.rank}${me.role === "approver" ? " · 🛡 approver" : ""}`;
  $("#ring-level").textContent = lv.level;
  $("#ring-fg").style.strokeDashoffset = 119.4 * (1 - lv.progress);
  $("#xp-now").textContent = `${lv.xp} XP`;
  $("#xp-next").textContent = `next level: ${lv.next_level_xp} XP`;
  $("#xp-fill").style.width = `${Math.round(lv.progress * 100)}%`;
  $("#streak-chip").textContent = `🔥 ${me.streak}`;
}

async function refreshMe() {
  try { state.me = await api("/api/me"); renderTopbar(); } catch { /* ignore */ }
}

/* ---------------- router ---------------- */
const VIEWS = { focus: renderFocus, board: renderBoard, ci: renderCI,
                actions: renderActions, prompts: renderPrompts,
                guild: renderGuild, me: renderProfile };

function route() {
  const name = (location.hash.replace("#/", "") || "focus").split("?")[0];
  state.view = VIEWS[name] ? name : "focus";
  document.querySelectorAll("#nav a").forEach((a) =>
    a.classList.toggle("active", a.dataset.view === state.view));
  view().innerHTML = `<div class="empty">loading…</div>`;
  VIEWS[state.view]().catch((e) => { view().innerHTML = `<div class="empty">⚠ ${esc(e.message)}</div>`; });
}
window.addEventListener("hashchange", route);

/* ================= FOCUS ================= */
async function renderFocus() {
  const data = await api("/api/focus");
  const srcCls = { jira: "chip-cyan", jenkins: "chip-red", approval: "chip-amber" };

  const items = data.items.map((it, i) => {
    const heat = it.score >= 85 ? "score-hot" : it.score >= 60 ? "score-warm" : "score-cool";
    let buttons = "";
    if (it.source === "jira" && it.unassigned)
      buttons = `<button class="btn btn-sm" data-claim="${esc(it.key)}">Claim +5</button>`;
    else if (it.source === "jira")
      buttons = `<button class="btn btn-sm" data-advance="${esc(it.key)}" data-status="${esc(it.status)}">Advance ▸</button>`;
    else if (it.source === "jenkins" && !it.claimed)
      buttons = `<button class="btn btn-sm" data-ciclaim="${esc(it.key)}">I'm on it +10</button>`;
    else if (it.source === "jenkins")
      buttons = `<button class="btn btn-sm" data-cifixed="${esc(it.key)}">It's green +35</button>`;
    else if (it.source === "approval")
      buttons = `<a class="btn btn-sm" href="#/actions">Review +15</a>`;
    return `
      <div class="focus-item" style="animation-delay:${i * 40}ms">
        <div class="score-pill ${heat}">${it.score}</div>
        <div class="focus-body">
          <div class="focus-title">${esc(it.title)}</div>
          <div class="focus-sub"><span class="chip ${srcCls[it.source]}">${it.source}</span>
            &nbsp;${esc(it.key)} · ${esc(it.subtitle)}</div>
          <div class="focus-why">${esc(it.why)}</div>
        </div>
        <div class="focus-actions">${buttons}</div>
      </div>`;
  }).join("") || `<div class="empty">Nothing urgent. Enjoy it — or grab a quest.</div>`;

  const quests = data.quests.map((q) => `
    <div class="quest-card ${q.complete ? "complete" : ""}">
      <div class="quest-name">${q.complete ? "✅" : "🎯"} ${esc(q.name)}</div>
      <div class="quest-desc">${esc(q.desc)}</div>
      <div class="quest-track"><div class="quest-fill" style="width:${(q.progress / q.target) * 100}%"></div></div>
      <div class="quest-meta"><span>${q.progress}/${q.target}</span><span>+${q.bonus} XP</span></div>
    </div>`).join("");

  view().innerHTML = `
    <div class="view-head"><h1>FOCUS</h1>
      <span class="sub">what matters right now, ranked · ci source: ${data.ci_source}</span></div>
    <div class="panel briefing"><h2>✦ daily briefing</h2>
      <div id="briefing-box" class="empty">summoning your briefing…</div>
      <button class="btn btn-sm btn-ghost" id="briefing-refresh">↻ regenerate</button></div>
    <div class="focus-grid">
      <div>${items}</div>
      <div><div class="panel"><h2>daily quests</h2>${quests}</div></div>
    </div>`;

  loadBriefing(false);
  $("#briefing-refresh").onclick = () => loadBriefing(true);

  view().querySelectorAll("[data-claim]").forEach((b) => b.onclick = () =>
    act(api(`/api/issues/${b.dataset.claim}/claim`, { method: "POST" })));
  view().querySelectorAll("[data-ciclaim]").forEach((b) => b.onclick = () =>
    act(api("/api/ci/claim", { method: "POST", body: { job: b.dataset.ciclaim } })));
  view().querySelectorAll("[data-cifixed]").forEach((b) => b.onclick = () =>
    act(api("/api/ci/fixed", { method: "POST", body: { job: b.dataset.cifixed } })));
  view().querySelectorAll("[data-advance]").forEach((b) => b.onclick = () =>
    advanceIssue(b.dataset.advance, b.dataset.status));
}

async function loadBriefing(refresh) {
  const box = $("#briefing-box");
  if (!box) return;
  try {
    const data = await api(`/api/ai/briefing?refresh=${refresh}`);
    if ($("#briefing-box")) $("#briefing-box").outerHTML =
      `<div id="briefing-box">${md(data.briefing)}</div>`;
  } catch (e) { box.textContent = `briefing unavailable: ${e.message}`; }
}

async function act(promise) {
  try { const data = await promise; handleGame(data.game); route(); }
  catch (e) { oops(e); }
}

/* ================= BOARD ================= */
let BOARD_STATUSES = [];

async function advanceIssue(key, current) {
  if (!BOARD_STATUSES.length) BOARD_STATUSES = (await api("/api/board")).columns.map((c) => c.name);
  const idx = BOARD_STATUSES.indexOf(current);
  // unknown status (e.g. Reopened) → advance means "back to work"
  const next = idx === -1 ? BOARD_STATUSES[1]
    : BOARD_STATUSES[Math.min(idx + 1, BOARD_STATUSES.length - 1)];
  if (next === current) return;
  act(api(`/api/issues/${key}/transition`, { method: "POST", body: { status: next } }));
}

async function renderBoard() {
  const data = await api("/api/board");
  BOARD_STATUSES = data.columns.map((c) => c.name);

  const cols = data.columns.map((col) => `
    <div class="col" data-col="${esc(col.name)}">
      <div class="col-head"><span>${esc(col.name)}</span><span>${col.issues.length}</span></div>
      ${col.issues.map((i) => `
        <div class="card" draggable="true" data-key="${esc(i.key)}">
          <div class="card-key">${esc(i.key)} · ${esc(i.type)}</div>
          <div class="card-sum">${esc(i.summary)}</div>
          <div class="card-foot">
            <span class="prio prio-${esc(i.priority)}">${esc(i.priority)}</span>
            ${i.due ? `<span class="chip">${esc(i.due)}</span>` : ""}
            <span class="assignee">${i.assignee ? "@" + esc(i.assignee) : "unassigned"}</span>
          </div>
          <div class="card-foot" style="margin-top:6px">
            ${!i.assignee ? `<button class="btn btn-sm" data-claim="${esc(i.key)}">Claim</button>` : ""}
            <button class="btn btn-sm btn-ghost" data-comment="${esc(i.key)}">💬</button>
            ${i.url && !i.url.startsWith("#") ? `<a class="btn btn-sm btn-ghost" href="${esc(i.url)}" target="_blank">↗</a>` : ""}
          </div>
        </div>`).join("")}
    </div>`).join("");

  view().innerHTML = `
    <div class="view-head"><h1>BOARD</h1>
      <span class="sub">Jira project ${esc(data.project)} · ${data.source} · drag cards to transition</span></div>
    <div class="board">${cols}</div>`;

  view().querySelectorAll(".card").forEach((card) => {
    card.addEventListener("dragstart", (e) =>
      e.dataTransfer.setData("text/plain", card.dataset.key));
  });
  view().querySelectorAll(".col").forEach((col) => {
    col.addEventListener("dragover", (e) => { e.preventDefault(); col.classList.add("dragover"); });
    col.addEventListener("dragleave", () => col.classList.remove("dragover"));
    col.addEventListener("drop", (e) => {
      e.preventDefault();
      col.classList.remove("dragover");
      const key = e.dataTransfer.getData("text/plain");
      act(api(`/api/issues/${key}/transition`, { method: "POST", body: { status: col.dataset.col } }));
    });
  });
  view().querySelectorAll("[data-claim]").forEach((b) => b.onclick = () =>
    act(api(`/api/issues/${b.dataset.claim}/claim`, { method: "POST" })));
  view().querySelectorAll("[data-comment]").forEach((b) => b.onclick = () => {
    const text = prompt(`Comment on ${b.dataset.comment}:`);
    if (text) act(api(`/api/issues/${b.dataset.comment}/comment`, { method: "POST", body: { body: text } }));
  });
}

/* ================= PIPELINES ================= */
async function renderCI() {
  const data = await api("/api/ci");
  const failures = data.failures.map((f) => `
    <div class="ci-row">
      <span class="ci-dot dot-red"></span>
      <span class="ci-job">${esc(f.job)} <small>#${f.number}</small></span>
      <span class="ci-meta">${esc(f.result)} · ${f.ago_min}m ago${f.claimed_by ? ` · 🛠 @${esc(f.claimed_by)}` : ""}</span>
      ${f.claimed_by
        ? `<button class="btn btn-sm" data-fixed="${esc(f.job)}">It's green +35</button>`
        : `<button class="btn btn-sm" data-claim="${esc(f.job)}">I'm on it +10</button>`}
    </div>`).join("") || `<div class="empty">no failing builds 🎉</div>`;

  const longRunning = data.long_running.map((l) => `
    <div class="ci-row">
      <span class="ci-dot dot-amber"></span>
      <span class="ci-job">${esc(l.job)} <small>#${l.number}</small></span>
      <span class="ci-meta">running ${l.running_min}m${l.claimed_by ? ` · 👀 @${esc(l.claimed_by)}` : ""}</span>
      ${l.claimed_by ? "" : `<button class="btn btn-sm" data-claim="${esc(l.job)}">Investigate +10</button>`}
    </div>`).join("") || `<div class="empty">nothing stuck</div>`;

  const jobs = data.jobs.map((j) => {
    const dot = j.building ? "dot-amber" : j.result === "SUCCESS" ? "dot-green"
      : j.result ? "dot-red" : "dot-grey";
    return `<div class="ci-row"><span class="ci-dot ${dot}"></span>
      <span class="ci-job">${esc(j.name)}</span>
      <span class="ci-meta">${j.building ? "building…" : esc(j.result || "—")}${j.duration_min ? ` · ${j.duration_min}m` : ""}</span></div>`;
  }).join("");

  view().innerHTML = `
    <div class="view-head"><h1>PIPELINES</h1><span class="sub">Jenkins · ${data.source}</span></div>
    <div class="ci-grid">
      <div>
        <div class="panel" style="margin-bottom:18px"><h2>🔴 recent failures</h2>${failures}</div>
        <div class="panel"><h2>⏳ long-running (possibly stuck)</h2>${longRunning}</div>
      </div>
      <div class="panel"><h2>all jobs</h2>${jobs}</div>
    </div>`;

  view().querySelectorAll("[data-claim]").forEach((b) => b.onclick = () =>
    act(api("/api/ci/claim", { method: "POST", body: { job: b.dataset.claim } })));
  view().querySelectorAll("[data-fixed]").forEach((b) => b.onclick = () =>
    act(api("/api/ci/fixed", { method: "POST", body: { job: b.dataset.fixed } })));
}

/* ================= REPO ACTIONS ================= */
async function renderActions() {
  const [data, tpl] = await Promise.all([api("/api/actions"), api("/api/prompts")]);
  state.templates = tpl.templates;

  const cards = data.actions.map((a) => `
    <div class="panel action-card">
      <div class="action-head">
        <span class="action-title">${esc(a.title)}</span>
        <span class="status status-${esc(a.status)}">${esc(a.status).replace("_", " ")}</span>
      </div>
      <div class="action-meta">template: ${esc(a.template_name)} · repo: ${esc(a.repo_url)}
        ${a.branch ? "· branch: " + esc(a.branch) : ""} · by @${esc(a.requested_by)} · ${ago(a.created_at)}
        ${a.decided_by ? `· decided by @${esc(a.decided_by)}` : ""}</div>
      <div class="action-plan">${md(a.plan)}</div>
      ${(a.files || []).map((f) => `
        <details class="filebox"><summary>📄 ${esc(f.path)}</summary><pre>${esc(f.content)}</pre></details>`).join("")}
      ${a.result ? `<details class="filebox"><summary>🧾 execution log</summary><pre>${esc(a.result)}</pre></details>` : ""}
      ${a.status === "pending_approval" && data.can_approve ? `
        <div class="action-buttons">
          <button class="btn btn-primary" data-approve="${a.id}">✓ Approve &amp; execute</button>
          <button class="btn btn-danger" data-reject="${a.id}">✕ Reject</button>
        </div>` : ""}
    </div>`).join("") || `<div class="empty">no repo actions yet</div>`;

  const tplOptions = state.templates.map((t) =>
    `<option value="${t.id}">${esc(t.name)}</option>`).join("");

  view().innerHTML = `
    <div class="view-head"><h1>REPO ACTIONS</h1>
      <span class="sub">AI drafts the change · a human approves before anything is pushed</span>
      <span class="spacer"></span>
      <button class="btn btn-primary" id="new-action">+ New action</button></div>
    <div id="action-form-slot"></div>
    ${cards}`;

  $("#new-action").onclick = () => renderActionForm();
  view().querySelectorAll("[data-approve]").forEach((b) => b.onclick = async () => {
    const note = prompt("Approval note (optional):") ?? "";
    b.disabled = true; b.textContent = "executing…";
    act(api(`/api/actions/${b.dataset.approve}/approve`, { method: "POST", body: { note } }));
  });
  view().querySelectorAll("[data-reject]").forEach((b) => b.onclick = () => {
    const note = prompt("Why reject?") ?? "";
    act(api(`/api/actions/${b.dataset.reject}/reject`, { method: "POST", body: { note } }));
  });
}

function renderActionForm() {
  const slot = $("#action-form-slot");
  const first = state.templates[0];
  slot.innerHTML = `
    <div class="panel" style="margin-bottom:16px">
      <h2>new repo action</h2>
      <div class="form-grid">
        <label>Template<select id="af-template">${state.templates.map((t) =>
          `<option value="${t.id}">${esc(t.name)}</option>`).join("")}</select></label>
        <label>Repo URL<input id="af-repo" placeholder="https://git.example.com/team/service.git"></label>
        <label>Branch<input id="af-branch" placeholder="questops/my-change"></label>
        <label>Title<input id="af-title" placeholder="(defaults to template name)"></label>
      </div>
      <div id="af-params" class="form-grid"></div>
      <div class="action-buttons">
        <button class="btn btn-primary" id="af-submit">✦ Draft with AI → send for approval</button>
        <button class="btn btn-ghost" id="af-cancel">cancel</button>
      </div>
    </div>`;

  const renderParams = () => {
    const t = state.templates.find((x) => x.id == $("#af-template").value);
    $("#af-params").innerHTML = (t?.variables || []).map((v) =>
      `<label><span class="var-chip">{{${esc(v)}}}</span><input data-param="${esc(v)}"></label>`).join("");
  };
  renderParams();
  $("#af-template").onchange = renderParams;
  $("#af-cancel").onclick = () => (slot.innerHTML = "");
  $("#af-submit").onclick = async () => {
    const params = {};
    slot.querySelectorAll("[data-param]").forEach((i) => (params[i.dataset.param] = i.value));
    $("#af-submit").disabled = true;
    $("#af-submit").textContent = "✦ AI is drafting the change…";
    act(api("/api/actions", {
      method: "POST",
      body: { template_id: Number($("#af-template").value), repo_url: $("#af-repo").value,
              branch: $("#af-branch").value, title: $("#af-title").value, params },
    }));
  };
}

/* ================= PROMPTS ================= */
async function renderPrompts() {
  const data = await api("/api/prompts");
  state.templates = data.templates;

  const cards = data.templates.map((t) => `
    <div class="panel prompt-card">
      <div class="action-head"><span class="action-title">✎ ${esc(t.name)}</span>
        ${(t.variables || []).map((v) => `<span class="var-chip">{{${esc(v)}}}</span>`).join("")}</div>
      <div class="action-meta">${esc(t.description)} · updated by @${esc(t.updated_by)} ${ago(t.updated_at)}</div>
      <pre class="mono">${esc(t.body)}</pre>
      <div class="action-buttons">
        <button class="btn btn-sm" data-edit="${t.id}">Edit</button>
        <button class="btn btn-sm" data-refine="${t.id}">✦ Refine with AI +8</button>
        <button class="btn btn-sm btn-danger" data-del="${t.id}">Delete</button>
      </div>
      <div id="refine-slot-${t.id}"></div>
    </div>`).join("") || `<div class="empty">no templates yet</div>`;

  view().innerHTML = `
    <div class="view-head"><h1>PROMPT TEMPLATES</h1>
      <span class="sub">the playbook behind repo actions — visible, versionable, AI-tunable</span>
      <span class="spacer"></span>
      <button class="btn btn-primary" id="new-prompt">+ New template</button></div>
    <div id="prompt-form-slot"></div>
    ${cards}`;

  $("#new-prompt").onclick = () => promptForm();
  view().querySelectorAll("[data-edit]").forEach((b) => b.onclick = () =>
    promptForm(state.templates.find((t) => t.id == b.dataset.edit)));
  view().querySelectorAll("[data-del]").forEach((b) => b.onclick = () => {
    if (confirm("Delete this template?"))
      act(api(`/api/prompts/${b.dataset.del}`, { method: "DELETE" }));
  });
  view().querySelectorAll("[data-refine]").forEach((b) => b.onclick = async () => {
    const instruction = prompt("What should the AI improve? (leave empty for a general pass)") ?? "";
    b.disabled = true; b.textContent = "✦ refining…";
    try {
      const data = await api(`/api/prompts/${b.dataset.refine}/refine`,
        { method: "POST", body: { instruction } });
      handleGame(data.game);
      const t = state.templates.find((x) => x.id == b.dataset.refine);
      $(`#refine-slot-${t.id}`).innerHTML = `
        <div class="panel" style="margin-top:10px;border-color:rgba(165,139,245,.4)">
          <h2 style="color:var(--violet)">✦ AI proposal</h2>
          <pre class="mono">${esc(data.proposal)}</pre>
          <div class="action-buttons">
            <button class="btn btn-primary" id="apply-${t.id}">Save proposal</button>
            <button class="btn btn-ghost" id="drop-${t.id}">Discard</button>
          </div></div>`;
      $(`#apply-${t.id}`).onclick = () => act(api(`/api/prompts/${t.id}`, {
        method: "PUT",
        body: { name: t.name, description: t.description, body: data.proposal } }));
      $(`#drop-${t.id}`).onclick = () => ($(`#refine-slot-${t.id}`).innerHTML = "");
    } catch (e) { oops(e); b.disabled = false; b.textContent = "✦ Refine with AI +8"; }
  });
}

function promptForm(t = null) {
  $("#prompt-form-slot").innerHTML = `
    <div class="panel form-col" style="margin-bottom:16px">
      <h2>${t ? "edit" : "new"} template</h2>
      <label>Name<input id="pf-name" value="${esc(t?.name || "")}"></label>
      <label>Description<input id="pf-desc" value="${esc(t?.description || "")}"></label>
      <label>Body — use <code>{{variable}}</code> placeholders
        <textarea id="pf-body">${esc(t?.body || "")}</textarea></label>
      <div class="action-buttons">
        <button class="btn btn-primary" id="pf-save">Save${t ? "" : " +10 XP"}</button>
        <button class="btn btn-ghost" id="pf-cancel">cancel</button>
      </div></div>`;
  $("#pf-cancel").onclick = () => ($("#prompt-form-slot").innerHTML = "");
  $("#pf-save").onclick = () => {
    const body = { name: $("#pf-name").value, description: $("#pf-desc").value,
                   body: $("#pf-body").value };
    act(t ? api(`/api/prompts/${t.id}`, { method: "PUT", body })
          : api("/api/prompts", { method: "POST", body }));
  };
}

/* ================= GUILD ================= */
async function renderGuild() {
  const [lb, recap, badges] = await Promise.all([
    api("/api/leaderboard?window=week"), api("/api/recap"), api("/api/badges")]);

  const maxXp = Math.max(...lb.rows.map((r) => r.xp), 1);
  const rows = lb.rows.map((r, i) => `
    <div class="lb-row ${r.username === state.me.username ? "me" : ""}">
      <span class="lb-rank r${i + 1}">${i === 0 ? "♛" : i + 1}</span>
      <span class="lb-name"><b>${esc(r.display_name || r.username)}</b>
        <small>LV ${r.level.level} ${esc(r.level.rank)}${r.role === "approver" ? " · 🛡" : ""} · 🔥${r.streak} · ${r.badges} badges</small></span>
      <span class="lb-bar"><div style="width:${(r.xp / maxXp) * 100}%"></div></span>
      <span class="lb-xp">${r.xp} XP</span>
    </div>`).join("");

  const tw = recap.this_week, lw = recap.last_week;
  const delta = (a, b) => a >= b
    ? `<span class="delta-up">▲ ${a - b} vs last wk</span>`
    : `<span class="delta-down">▼ ${b - a} vs last wk</span>`;

  const badgeTiles = badges.catalog.map((b) => `
    <div class="badge-tile ${b.holders.length ? "owned" : ""}">
      <div class="b-ico">${b.icon}</div><b>${esc(b.name)}</b>
      <small>${esc(b.desc)}</small>
      <span class="holders">${b.holders.length ? b.holders.map((h) => "@" + esc(h)).join(" ") : "unclaimed"}</span>
    </div>`).join("");

  view().innerHTML = `
    <div class="view-head"><h1>GUILD</h1><span class="sub">the team, this week</span></div>
    <div class="stat-tiles">
      <div class="stat-tile"><b>${tw.xp}</b><span>team XP</span> ${delta(tw.xp, lw.xp)}</div>
      <div class="stat-tile"><b>${tw.tickets_done}</b><span>tickets done</span> ${delta(tw.tickets_done, lw.tickets_done)}</div>
      <div class="stat-tile"><b>${tw.builds_fixed}</b><span>builds fixed</span> ${delta(tw.builds_fixed, lw.builds_fixed)}</div>
      <div class="stat-tile"><b>${tw.reviews}</b><span>reviews</span> ${delta(tw.reviews, lw.reviews)}</div>
      <div class="stat-tile"><b>@${esc(tw.top_user)}</b><span>MVP of the week</span></div>
    </div>
    <div class="guild-grid">
      <div class="panel"><h2>♛ weekly leaderboard</h2>${rows}</div>
      <div class="panel"><h2>badge wall</h2><div class="badge-grid">${badgeTiles}</div></div>
    </div>`;
}

/* ================= PROFILE ================= */
async function renderProfile() {
  const [me, hist] = await Promise.all([api("/api/me"), api("/api/history")]);
  state.me = me; renderTopbar();

  const pts = hist.daily.map((d) => d.xp);
  const maxY = Math.max(...pts, 10);
  const coords = pts.map((v, i) =>
    `${(i / (pts.length - 1)) * 280},${58 - (v / maxY) * 52}`).join(" ");
  const area = `0,58 ${coords} 280,58`;

  const timeline = hist.events.map((e) => `
    <div class="tl-item kind-${esc(e.kind)}">
      <div class="tl-msg">${esc(e.message || e.kind.replace(/_/g, " "))}
        ${e.points ? `<span class="tl-pts">+${e.points}</span>` : ""}</div>
      <div class="tl-meta">${esc(e.kind)} · ${ago(e.at)}</div>
    </div>`).join("") || `<div class="empty">no activity yet — go earn some XP</div>`;

  view().innerHTML = `
    <div class="view-head"><h1>MY JOURNEY</h1>
      <span class="sub">LV ${me.level.level} ${esc(me.level.rank)} · ${me.level.xp} XP lifetime · 🔥 ${me.streak}-day streak</span></div>
    <div class="guild-grid">
      <div>
        <div class="panel" style="margin-bottom:18px"><h2>xp — last 28 days</h2>
          <svg class="spark" viewBox="0 0 280 60" preserveAspectRatio="none">
            <polygon class="area" points="${area}"></polygon>
            <polyline points="${coords}"></polyline>
          </svg></div>
        <div class="panel"><h2>history</h2><div class="timeline">${timeline}</div></div>
      </div>
      <div class="panel"><h2>my badges</h2>
        <div class="badge-grid">${me.badges.length ? me.badges.map((b) => `
          <div class="badge-tile owned"><div class="b-ico">${b.icon}</div><b>${esc(b.name)}</b></div>`).join("")
          : `<div class="empty">none yet — check the badge wall in Guild</div>`}
        </div></div>
    </div>`;
}

/* ================= AI DRAWER ================= */
$("#ai-toggle").addEventListener("click", async () => {
  $("#ai-drawer").classList.toggle("open");
  try {
    const s = await api("/api/ai/status");
    $("#ai-model").textContent = s.available ? s.model : `${s.model} · offline`;
  } catch { /* ignore */ }
});
$("#ai-close").addEventListener("click", () => $("#ai-drawer").classList.remove("open"));

$("#ai-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = $("#ai-input");
  const msg = input.value.trim();
  if (!msg) return;
  input.value = "";
  const log = $("#ai-log");
  log.insertAdjacentHTML("beforeend", `<div class="ai-msg ai-user">${esc(msg)}</div>`);
  log.insertAdjacentHTML("beforeend", `<div class="ai-msg ai-bot" id="ai-pending">✦ thinking…</div>`);
  log.scrollTop = log.scrollHeight;
  try {
    const data = await api("/api/ai/chat",
      { method: "POST", body: { message: msg, history: state.aiHistory } });
    state.aiHistory.push({ role: "user", content: msg },
                         { role: "assistant", content: data.reply });
    $("#ai-pending").outerHTML = `<div class="ai-msg ai-bot">${md(data.reply)}</div>`;
  } catch (err) {
    $("#ai-pending").outerHTML = `<div class="ai-msg ai-bot">⚠ ${esc(err.message)}</div>`;
  }
  log.scrollTop = log.scrollHeight;
});

/* ---------------- go ---------------- */
boot();
