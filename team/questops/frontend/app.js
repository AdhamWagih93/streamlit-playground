/* QuestOps SPA — no build step, plain ES2020. */

const state = {
  token: localStorage.getItem("qo_token") || null,
  me: null,
  view: "overview",
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

// external-link button (demo URLs start with '#' and get no link)
function linkBtn(url, cls = "btn btn-sm btn-ghost") {
  return url && !url.startsWith("#")
    ? `<a class="${cls}" href="${esc(url)}" target="_blank" rel="noopener" title="open">↗</a>` : "";
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
  for (const q of game.team_quests_completed || [])
    toast(`🏆 <b>TEAM quest:</b> ${esc(q.name)} — <b>+${q.bonus} XP for everyone</b>`, "toast-quest", 6000);
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
const VIEWS = { overview: renderOverview, focus: renderFocus, board: renderBoard,
                ci: renderCI, actions: renderActions, prompts: renderPrompts,
                repos: renderRepos, team: renderTeam, me: renderProfile };

function route() {
  const name = (location.hash.replace("#/", "") || "overview").split("?")[0];
  state.view = VIEWS[name] ? name : "overview";
  document.querySelectorAll("#nav a").forEach((a) =>
    a.classList.toggle("active", a.dataset.view === state.view));
  view().innerHTML = `<div class="empty">loading…</div>`;
  VIEWS[state.view]().catch((e) => { view().innerHTML = `<div class="empty">⚠ ${esc(e.message)}</div>`; });
}
window.addEventListener("hashchange", route);

/* ================= OVERVIEW ================= */
async function renderOverview() {
  const data = await api("/api/overview");
  const j = data.jira, ci = data.ci, kpi = data.kpi, team = data.team;
  const pctCls = (p) => p >= 90 ? "pct-good" : p >= 70 ? "pct-warn" : "pct-bad";

  const tile = (href, value, label, cls = "", note = "") => `
    <a class="stat-tile ov-tile ${cls}" href="${href}">
      <b>${value}</b><span>${label}</span>${note ? `<small>${note}</small>` : ""}
    </a>`;
  const tiles = [
    tile("#/board", j.open_total, "open tickets",
         j.overdue ? "ov-bad" : "", j.overdue ? `⚠ ${j.overdue} overdue` : (j.due_soon ? `${j.due_soon} due soon` : "")),
    tile("#/board", j.unassigned, "in the pool", "", "unassigned — claim them"),
    tile("#/ci", ci.failures, "red builds", ci.failures ? "ov-bad" : "ov-good",
         ci.long_running ? `+ ${ci.long_running} stuck` : ""),
    tile("#/ci", `${kpi.overall_pct}%`, "pipeline success (24h)", pctCls(kpi.overall_pct),
         kpi.at_risk ? `⚠ ${kpi.at_risk} failure(s) entering KPIs` : `${kpi.success}/${kpi.total} builds`),
    tile("#/actions", data.approvals.pending, "pending approvals",
         data.approvals.pending ? "ov-warn" : ""),
    tile("#/team", j.missing_objective, "tickets w/o objective",
         j.missing_objective ? "ov-warn" : "ov-good"),
    tile("#/team", team.this_week.xp, "team XP this week",
         team.this_week.xp >= team.last_week.xp ? "ov-good" : "",
         `${team.this_week.xp >= team.last_week.xp ? "▲" : "▼"} vs ${team.last_week.xp} last wk`),
  ].join("");

  const maxCol = Math.max(...j.columns.map((c) => c.count), 1);
  const boardRows = j.columns.map((c) => `
    <div class="lb-row">
      <span class="lb-name"><b>${esc(c.label)}</b></span>
      <span class="lb-bar"><div style="width:${(c.count / maxCol) * 100}%"></div></span>
      <span class="lb-xp">${c.count}</span>
    </div>`).join("") || `<div class="empty">board unavailable (${esc(j.error || j.source)})</div>`;
  const boardChips = [
    j.reopened ? `<span class="chip chip-red">↩ ${j.reopened} reopened</span>` : "",
    j.overdue ? `<span class="chip chip-red">⏰ ${j.overdue} overdue</span>` : "",
    j.due_soon ? `<span class="chip chip-amber">📅 ${j.due_soon} due ≤2d</span>` : "",
    j.unassigned ? `<span class="chip chip-cyan">🖐 ${j.unassigned} unassigned</span>` : "",
  ].filter(Boolean).join(" ");

  const maxObj = Math.max(...j.objectives.map((o) => o.open), 1);
  const objRows = j.objectives.map((o) => `
    <div class="lb-row">
      <span class="lb-name"><b>🎯 ${esc(o.name)}</b><small>${o.open} open · ${o.closed_recent} closed recently</small></span>
      <span class="lb-bar"><div style="width:${(o.open / maxObj) * 100}%"></div></span>
      <span class="lb-xp">${o.open}</span>
    </div>`).join("") || `<div class="empty">no objectives defined</div>`;

  const attention = [
    ...ci.top_failures.map((f) => `
      <div class="ci-row"><span class="ci-dot dot-red"></span>
        <span class="ci-job">${esc(f.job)} <small>#${f.number}</small></span>
        <span class="ci-meta">failed ${f.ago_min}m ago${f.claimed_by ? ` · 🛠 @${esc(f.claimed_by)}` : ""}</span>
        ${linkBtn(f.url)}<a class="btn btn-sm" href="#/ci">act ▸</a></div>`),
    ...ci.stuck.map((l) => `
      <div class="ci-row"><span class="ci-dot dot-amber"></span>
        <span class="ci-job">${esc(l.job)} <small>#${l.number}</small></span>
        <span class="ci-meta">running ${l.running_min}m${l.avg_min ? ` vs ~${l.avg_min}m avg` : ""}</span>
        ${linkBtn(l.url)}<a class="btn btn-sm" href="#/ci">act ▸</a></div>`),
    data.approvals.pending ? `
      <div class="ci-row"><span class="ci-dot dot-amber"></span>
        <span class="ci-job">🛡 ${data.approvals.pending} repo action(s) awaiting approval</span>
        <a class="btn btn-sm" href="#/actions">review ▸</a></div>` : "",
    j.missing_objective ? `
      <div class="ci-row"><span class="ci-dot dot-amber"></span>
        <span class="ci-job">🎯 ${j.missing_objective} open ticket(s) without an objective</span>
        <a class="btn btn-sm" href="#/board">tag them ▸</a></div>` : "",
  ].filter(Boolean).join("") || `<div class="empty">✅ all clear — nothing needs attention</div>`;

  const medals = ["🥇", "🥈", "🥉"];
  const top3 = team.top3.map((r, i) => `
    <div class="ci-row"><span>${medals[i]}</span>
      <span class="ci-job">${esc(r.display_name || r.username)}</span>
      <span class="lb-xp">${r.xp} XP</span></div>`).join("")
    || `<div class="empty">no XP earned this week yet</div>`;
  const questRows = team.quests.map((q) => `
    <div class="ov-quest ${q.complete ? "complete" : ""}">
      <span>${q.complete ? "✅" : "🏆"} ${esc(q.name)}</span>
      <div class="quest-track"><div class="quest-fill" style="width:${(q.progress / q.target) * 100}%"></div></div>
      <span class="ci-meta">${q.progress}/${q.target}</span>
    </div>`).join("");

  const feed = data.activity.map((e) => `
    <div class="tl-item kind-${esc(e.kind)}">
      <div class="tl-msg"><b>@${esc(e.username)}</b> ${esc(e.message || e.kind.replace(/_/g, " "))}
        ${e.points ? `<span class="tl-pts">+${e.points}</span>` : ""}</div>
      <div class="tl-meta">${ago(e.at)}</div>
    </div>`).join("") || `<div class="empty">no activity yet</div>`;

  const srcNote = [["Jira", j], ["Jenkins", ci], ["Elasticsearch", kpi]]
    .filter(([, s]) => s.source === "error")
    .map(([n, s]) => `⚠ ${n}: ${esc(s.error || "unavailable")}`).join(" · ");

  view().innerHTML = `
    <div class="view-head"><h1>OVERVIEW</h1>
      <span class="sub">the whole picture · ${esc(j.project || "")} · ${j.source}</span>
      <span class="spacer"></span>
      <button class="btn btn-primary" id="ov-add">+ Add ticket</button></div>
    ${srcNote ? `<div class="kpi-note" style="margin-bottom:10px">${srcNote}</div>` : ""}
    <div class="stat-tiles">${tiles}</div>
    <div class="ov-grid">
      <div>
        <div class="panel" style="margin-bottom:18px"><h2>🚨 needs attention</h2>${attention}</div>
        <div class="panel" style="margin-bottom:18px"><h2>▦ board at a glance
          <a class="ov-more" href="#/board">open board ▸</a></h2>
          ${boardRows}
          ${boardChips ? `<div class="filter-row" style="margin-top:10px;flex-wrap:wrap">${boardChips}</div>` : ""}</div>
        <div class="panel"><h2>🎯 objectives
          <a class="ov-more" href="#/team">details ▸</a></h2>${objRows}</div>
      </div>
      <div>
        <div class="panel" style="margin-bottom:18px"><h2>♛ team pulse — this week
          <a class="ov-more" href="#/team">team ▸</a></h2>
          <div class="ci-row"><span class="ci-job">tickets closed</span><span class="lb-xp">${team.this_week.tickets_done}</span></div>
          <div class="ci-row"><span class="ci-job">builds fixed</span><span class="lb-xp">${team.this_week.builds_fixed}</span></div>
          ${top3}
          ${questRows ? `<div style="margin-top:10px">${questRows}</div>` : ""}</div>
        <div class="panel"><h2>latest activity</h2><div class="timeline">${feed}</div></div>
      </div>
    </div>`;

  $("#ov-add").onclick = openQuickAdd;
}

/* ================= QUICK ADD TICKET ================= */
// importance × urgency presets → priority (+ a due date when it's urgent)
const QUICK_PRESETS = [
  { key: "now",   label: "🔥 Do now",    hint: "important + urgent",   priority: "Highest", dueDays: 0 },
  { key: "plan",  label: "📌 Plan it",   hint: "important, can wait",  priority: "High",    dueDays: null },
  { key: "quick", label: "⚡ Quick win", hint: "urgent, not critical", priority: "Medium",  dueDays: 1 },
  { key: "later", label: "🧊 Backlog",   hint: "no rush",              priority: "Low",     dueDays: null },
];

async function openQuickAdd() {
  if ($("#qa-back")) return;
  const [objectives, members] = await Promise.all([
    api("/api/objectives").then((d) => d.objectives.map((o) => o.name)).catch(() => []),
    api("/api/members").then((d) => d.members).catch(() => []),
  ]);

  const presets = QUICK_PRESETS.map((p) => `
    <button type="button" class="preset-chip" data-preset="${p.key}">
      <b>${p.label}</b><small>${p.hint} → ${p.priority}</small></button>`).join("");
  const memberOpts = members.map((m) => `
    <option value="${esc(m.username)}">${esc(m.display_name || m.username)}${m.username === state.me.username ? " (me)" : ""}</option>`).join("");
  const objBoxes = objectives.map((o) => `
    <label class="qa-obj"><input type="checkbox" value="${esc(o)}"> ${esc(o)}</label>`).join("");

  const back = document.createElement("div");
  back.id = "qa-back";
  back.className = "modal-back";
  back.innerHTML = `
    <div class="modal panel">
      <div class="action-head"><span class="action-title">＋ add a ticket to the pool</span>
        <button class="btn btn-ghost" id="qa-close">✕</button></div>
      <label class="qa-label">Summary
        <input id="qa-summary" placeholder="what needs doing?" maxlength="255"></label>
      <div class="preset-row">${presets}</div>
      <div class="form-grid" style="margin:10px 0 0">
        <label>Type<select id="qa-type">
          <option>Task</option><option>Bug</option><option>Story</option><option>Spike</option>
        </select></label>
        <label>Priority<select id="qa-priority">
          <option>Highest</option><option>High</option><option selected>Medium</option>
          <option>Low</option><option>Lowest</option>
        </select></label>
        <label>Due date<input id="qa-due" type="date"></label>
        <label>Assignee<select id="qa-assignee">
          <option value="">— leave in the pool (unassigned)</option>${memberOpts}
        </select></label>
      </div>
      ${objectives.length ? `<div class="qa-label" style="margin-top:10px">Objectives
        <div class="qa-objs">${objBoxes}</div></div>` : ""}
      <label class="qa-label" style="margin-top:10px">Description (optional)
        <textarea id="qa-desc" rows="3" placeholder="context, links, acceptance criteria…"></textarea></label>
      <div class="action-buttons">
        <button class="btn btn-primary" id="qa-submit">Create ticket +8 XP</button>
        <button class="btn btn-ghost" id="qa-cancel">cancel</button>
      </div>
    </div>`;
  document.body.appendChild(back);
  $("#qa-summary").focus();

  const close = () => back.remove();
  $("#qa-close").onclick = close;
  $("#qa-cancel").onclick = close;
  back.addEventListener("click", (e) => { if (e.target === back) close(); });
  back.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });

  back.querySelectorAll("[data-preset]").forEach((b) => b.onclick = () => {
    const p = QUICK_PRESETS.find((x) => x.key === b.dataset.preset);
    back.querySelectorAll(".preset-chip").forEach((c) => c.classList.toggle("active", c === b));
    $("#qa-priority").value = p.priority;
    $("#qa-due").value = p.dueDays === null ? ""
      : new Date(Date.now() + p.dueDays * 864e5).toISOString().slice(0, 10);
  });

  $("#qa-submit").onclick = async () => {
    const summary = $("#qa-summary").value.trim();
    if (!summary) return oops(new Error("summary is required"));
    const components = [...back.querySelectorAll(".qa-obj input:checked")].map((c) => c.value);
    $("#qa-submit").disabled = true;
    try {
      const data = await api("/api/issues", { method: "POST", body: {
        summary, type: $("#qa-type").value, priority: $("#qa-priority").value,
        due: $("#qa-due").value || null, assignee: $("#qa-assignee").value || null,
        components, description: $("#qa-desc").value,
      }});
      handleGame(data.game);
      toast(`🎫 <b>${esc(data.issue.key)}</b> created · ${esc(data.issue.priority)}${data.issue.assignee ? ` · @${esc(data.issue.assignee)}` : " · in the pool"}`, "toast-xp", 5000);
      close();
      if (["overview", "board", "focus"].includes(state.view)) route();
    } catch (e) { oops(e); $("#qa-submit").disabled = false; }
  };
}

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
            &nbsp;${esc(it.key)} · ${esc(it.subtitle)}${it.created ? ` · created ${ago(it.created)}` : ""}${it.updated ? ` · updated ${ago(it.updated)}` : ""}</div>
          <div class="focus-why">${esc(it.why)}</div>
        </div>
        <div class="focus-actions">${linkBtn(it.url)}${buttons}</div>
      </div>`;
  }).join("") || `<div class="empty">Nothing urgent. Enjoy it — or grab a quest.</div>`;

  const questCard = (q) => `
    <div class="quest-card ${q.complete ? "complete" : ""}">
      <div class="quest-name">${q.complete ? "✅" : q.team ? "🏆" : "🎯"} ${esc(q.name)}
        ${q.team ? '<span class="chip chip-amber">TEAM</span>' : ""}</div>
      <div class="quest-desc">${esc(q.desc)}</div>
      <div class="quest-track"><div class="quest-fill" style="width:${(q.progress / q.target) * 100}%"></div></div>
      <div class="quest-meta"><span>${q.progress}/${q.target}</span><span>+${q.bonus} XP${q.team ? " each" : ""}</span></div>
    </div>`;
  const quests = data.quests.map(questCard).join("");
  const teamQuests = (data.team_quests || []).map(questCard).join("");

  view().innerHTML = `
    <div class="view-head"><h1>FOCUS</h1>
      <span class="sub">what matters right now, ranked · ci source: ${data.ci_source}</span></div>
    <div class="panel briefing"><h2>✦ daily briefing</h2>
      <div id="briefing-box" class="empty">summoning your briefing…</div>
      <button class="btn btn-sm btn-ghost" id="briefing-refresh">↻ regenerate</button></div>
    <div class="focus-grid">
      <div>${items}</div>
      <div><div class="panel"><h2>daily quests</h2>${quests}
        <h2 style="margin-top:16px">team quests</h2>${teamQuests}</div></div>
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
    advanceIssue(b.dataset.advance, b.dataset.status, state.me.username));
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

// every status change asks: take the ticket, or keep the current assignee?
// (skipped when it's already yours; backend restores the original assignee
// either way so the Jira service account never ends up owning it)
function transitionIssue(key, status, assignee) {
  let assignToMe = false;
  if (assignee !== state.me.username) {
    assignToMe = confirm(
      `${key} → ${status}\n\nOK — assign to me (@${state.me.username})\n` +
      `Cancel — keep current assignee${assignee ? ` (@${assignee})` : " (unassigned)"}`);
  }
  act(api(`/api/issues/${key}/transition`,
          { method: "POST", body: { status, assign_to_me: assignToMe } }));
}

async function advanceIssue(key, current, assignee) {
  if (!BOARD_STATUSES.length) BOARD_STATUSES = (await api("/api/board")).columns.map((c) => c.name);
  const idx = BOARD_STATUSES.indexOf(current);
  // unknown status (e.g. Reopened) → advance means "back to work"
  const next = idx === -1 ? BOARD_STATUSES[1]
    : BOARD_STATUSES[Math.min(idx + 1, BOARD_STATUSES.length - 1)];
  if (next === current) return;
  transitionIssue(key, next, assignee);
}

async function renderBoard() {
  const data = await api("/api/board");
  BOARD_STATUSES = data.columns.map((c) => c.name);

  const cardHtml = (i) => `
        <div class="card" draggable="true" data-key="${esc(i.key)}" data-assignee="${esc(i.assignee || "")}">
          <div class="card-key">${esc(i.key)} · ${esc(i.type)}
            <span class="card-dates">created ${ago(i.created)} · upd ${ago(i.updated)}</span></div>
          <div class="card-sum">${esc(i.summary)}</div>
          <div class="card-foot">
            <span class="prio prio-${esc(i.priority)}">${esc(i.priority)}</span>
            ${i.due ? `<span class="chip">${esc(i.due)}</span>` : ""}
            ${(i.components || []).length ? `<button class="chip chip-violet" data-objective="${esc(i.key)}" data-current="${esc(i.components.join("|"))}" title="${esc(i.components.join(", "))} — click to edit">🎯 ${esc(i.components[0])}${i.components.length > 1 ? " +" + (i.components.length - 1) : ""}</button>` : ""}
            ${i.needs_objective ? `<button class="chip chip-red" data-objective="${esc(i.key)}" title="assign team objectives">⚠ no objective</button>` : ""}
            <span class="assignee">${i.assignee ? "@" + esc(i.assignee) : "unassigned"}</span>
          </div>
          <div class="card-foot" style="margin-top:6px">
            ${!i.assignee ? `<button class="btn btn-sm" data-claim="${esc(i.key)}">Claim</button>` : ""}
            <button class="btn btn-sm btn-ghost" data-comment="${esc(i.key)}">💬</button>
            ${i.url && !i.url.startsWith("#") ? `<a class="btn btn-sm btn-ghost" href="${esc(i.url)}" target="_blank">↗</a>` : ""}
          </div>
        </div>`;

  // cards cluster under their longest-common-prefix group; loners go last
  const colHtml = (col) => {
    const byGroup = {};
    col.issues.forEach((i) => { const g = i.group || ""; (byGroup[g] = byGroup[g] || []).push(i); });
    const names = Object.keys(byGroup).sort((a, b) =>
      a === "" ? 1 : b === "" ? -1 : a.localeCompare(b));
    const body = names.map((g) =>
      (g ? `<div class="group-head">▾ ${esc(g)}<span>${byGroup[g].length}</span></div>`
         : (names.length > 1 ? `<div class="group-head group-other">other<span>${byGroup[g].length}</span></div>` : ""))
      + byGroup[g].map(cardHtml).join("")).join("");
    return `
    <div class="col" data-col="${esc(col.name)}">
      <div class="col-head"><span>${esc(col.label || col.name)}</span><span>${col.issues.length}</span></div>
      ${body}
    </div>`;
  };
  const cols = data.columns.map(colHtml).join("");

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
      const card = view().querySelector(`[data-key="${key}"]`);
      transitionIssue(key, col.dataset.col, card?.dataset.assignee || null);
    });
  });
  view().querySelectorAll("[data-objective]").forEach((b) => b.onclick = async () => {
    try {
      const data = await api("/api/objectives");
      const names = data.objectives.map((o) => o.name);
      const current = (b.dataset.current || "").split("|").filter(Boolean);
      const preset = current.map((c) => names.indexOf(c) + 1).filter((n) => n > 0).join(",");
      const pick = prompt(
        `Objectives for ${b.dataset.objective} — a ticket can serve several.\n` +
        names.map((n, i) => `${i + 1}. ${n}${current.includes(n) ? " ✓" : ""}`).join("\n") +
        "\n\nEnter numbers separated by commas (e.g. 1,3):", preset);
      if (pick === null) return;
      const chosen = [...new Set(pick.split(",")
        .map((s) => names[parseInt(s.trim(), 10) - 1]).filter(Boolean))];
      if (!chosen.length) return oops(new Error("pick at least one objective"));
      act(api(`/api/issues/${b.dataset.objective}/components`,
              { method: "POST", body: { components: chosen } }));
    } catch (e) { oops(e); }
  });
  view().querySelectorAll("[data-claim]").forEach((b) => b.onclick = () =>
    act(api(`/api/issues/${b.dataset.claim}/claim`, { method: "POST" })));
  view().querySelectorAll("[data-comment]").forEach((b) => b.onclick = () => {
    const text = prompt(`Comment on ${b.dataset.comment}:`);
    if (text) act(api(`/api/issues/${b.dataset.comment}/comment`, { method: "POST", body: { body: text } }));
  });
}

/* ================= PIPELINES ================= */
let KPI_TIMER = null;

function startKpiCountdown(seconds) {
  clearInterval(KPI_TIMER);
  const end = Date.now() + seconds * 1000;
  KPI_TIMER = setInterval(() => {
    const el = document.getElementById("kpi-countdown");
    if (!el) { clearInterval(KPI_TIMER); return; }
    const s = Math.max(0, Math.round((end - Date.now()) / 1000));
    el.textContent = `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
    if (s <= 0) { clearInterval(KPI_TIMER); renderCI().catch(() => {}); }
  }, 1000);
}

const FLAG_COLORS = ["chip-red", "chip-amber", "chip-cyan", "chip-green", "chip-violet"];
const flagClass = (flag, flags) => FLAG_COLORS[Math.max(0, flags.indexOf(flag)) % FLAG_COLORS.length];

async function renderCI() {
  const kpiHours = state.kpiHours || 24;
  const [data, kpi, errs] = await Promise.all([
    api("/api/ci"), api(`/api/kpi?hours=${kpiHours}`), api("/api/errors")]);
  const failures = data.failures.map((f) => `
    <div class="ci-row">
      <span class="ci-dot dot-red"></span>
      <span class="ci-job">${esc(f.job)} <small>#${f.number}</small></span>
      <span class="ci-meta">${esc(f.result)} · ${f.ago_min}m ago${f.claimed_by ? ` · 🛠 @${esc(f.claimed_by)}` : ""}</span>
      ${linkBtn(f.url)}
      ${f.claimed_by
        ? `<button class="btn btn-sm" data-fixed="${esc(f.job)}">It's green +35</button>`
        : `<button class="btn btn-sm" data-claim="${esc(f.job)}">I'm on it +10</button>`}
    </div>`).join("") || `<div class="empty">no failing builds 🎉</div>`;

  const longRunning = data.long_running.map((l) => `
    <div class="ci-row">
      <span class="ci-dot dot-amber"></span>
      <span class="ci-job">${esc(l.job)} <small>#${l.number}</small></span>
      <span class="ci-meta">running ${l.running_min}m${l.avg_min ? ` · avg ${l.avg_min}m` : ""}${l.claimed_by ? ` · 👀 @${esc(l.claimed_by)}` : ""}</span>
      ${linkBtn(l.url)}
      ${l.claimed_by ? "" : `<button class="btn btn-sm" data-claim="${esc(l.job)}">Investigate +10</button>`}
    </div>`).join("") || `<div class="empty">nothing stuck</div>`;

  const jobs = data.jobs.map((j) => {
    const dot = j.building ? "dot-amber" : j.result === "SUCCESS" ? "dot-green"
      : j.result ? "dot-red" : "dot-grey";
    return `<div class="ci-row"><span class="ci-dot ${dot}"></span>
      <span class="ci-job">${esc(j.name)}</span>
      <span class="ci-meta">${j.building ? "building…" : esc(j.result || "—")}${j.duration_min ? ` · ${j.duration_min}m` : ""}</span>
      ${linkBtn(j.url)}</div>`;
  }).join("");

  // --- KPI window: countdown to the next loader run + failures at risk ---
  const nextAt = new Date(kpi.next_sync);
  const hhmm = `${String(nextAt.getHours()).padStart(2, "0")}:${String(nextAt.getMinutes()).padStart(2, "0")}`;
  const atRisk = kpi.at_risk.map((f) => `
    <div class="ci-row">
      <span class="ci-dot dot-red"></span>
      <span class="ci-job">${esc(f.job)} <small>#${f.number}</small></span>
      <span class="ci-meta">failed ${f.ago_min}m ago</span>
      ${linkBtn(f.url)}
      ${f.claimed_by ? `<span class="chip">🛠 @${esc(f.claimed_by)}</span>`
        : `<button class="btn btn-sm" data-claim="${esc(f.job)}">I'm on it +10</button>`}
    </div>`).join("")
    || `<div class="empty">✅ KPI window is clean — nothing bad gets loaded at ${hhmm}</div>`;
  const kpiPanel = `
    <div class="panel kpi-panel" style="margin-bottom:18px">
      <div class="kpi-clock">
        <div id="kpi-countdown" class="kpi-count">--:--</div>
        <div class="kpi-sub">until KPI load @ ${hhmm}<br>
          <small>runs at :${kpi.sync_marks.map((m) => String(m).padStart(2, "0")).join(" / :")} · ${kpi.source}</small></div>
      </div>
      <div class="kpi-risk">
        <h2>⚠ will enter your KPIs unless cleaned up</h2>
        ${atRisk}
        <div class="kpi-note">${kpi.loaded_failures.length} failure(s) already loaded in the last ${kpi.hours}h
          (${kpi.loaded_total} builds total)</div>
      </div>
    </div>`;

  // --- the actual KPI documents already in the index ---
  const kpiDot = (s) => {
    const u = String(s || "").toUpperCase();
    return u === "SUCCESS" ? "dot-green" : u.startsWith("FAIL") || u === "UNSTABLE" || u === "ABORTED"
      ? "dot-red" : "dot-grey";
  };
  const hourChips = [6, 24, 72, 168].map((h) =>
    `<button class="btn btn-sm ${h === kpiHours ? "btn-primary" : ""}" data-hours="${h}">${h < 48 ? h + "h" : h / 24 + "d"}</button>`).join(" ");
  const loadedRows = kpi.loaded.map((d) => `
    <div class="ci-row">
      <span class="ci-dot ${kpiDot(d.status)}"></span>
      <span class="ci-job">${esc(d.jobpath || d.jobname)} <small>#${esc(d.buildnumber)}</small></span>
      <span class="ci-meta">${esc(String(d.status || "").toUpperCase())} · ${ago(d.builddate || d["@timestamp"])}
        · ${esc(d.triggertype || "?")}${d.triggeredby ? " by " + esc(d.triggeredby) : ""}</span>
      ${linkBtn(d.buildurl)}
    </div>`).join("") || `<div class="empty">nothing loaded in this window</div>`;
  const kpiWarn = kpi.es_error
    ? `<div class="empty">⚠ Elasticsearch query failed on '${esc(kpi.index)}': ${esc(kpi.es_error)}</div>`
    : (!kpi.window_applied
      ? `<div class="kpi-note">⚠ the ${kpi.hours}h window matched nothing on @timestamp/builddate — showing the newest records in '${esc(kpi.index)}' instead</div>`
      : "");
  const pctCls = (p) => p >= 90 ? "pct-good" : p >= 70 ? "pct-warn" : "pct-bad";
  const st = kpi.stats || { total: 0, pipelines: [] };
  const kpiStats = st.total ? `
    <div class="kpi-stats">
      <div class="kpi-overall">
        <b class="${pctCls(st.overall_pct)}">${st.overall_pct}%</b>
        <span>overall success<br>${st.success}/${st.total} builds</span>
      </div>
      <div class="kpi-pipes">
        ${st.pipelines.map((p) => `
          <div class="kpi-pipe">
            <span class="ci-job" title="${esc(p.job)}">${esc(p.job)}</span>
            <span class="lb-bar"><div class="${pctCls(p.pct)}" style="width:${p.pct}%"></div></span>
            <span class="kpi-pct ${pctCls(p.pct)}">${p.pct}%</span>
            <span class="ci-meta">${p.success}/${p.total}</span>
          </div>`).join("")}
      </div>
    </div>` : "";
  const loadedPanel = `
    <div class="panel" style="margin-bottom:18px">
      <h2>📦 pipeline KPIs — ${esc(kpi.source)} · ${kpi.loaded_total} builds in window</h2>
      <div class="filter-row" style="margin-bottom:10px">${hourChips}</div>
      ${kpiWarn}
      ${kpiStats}
      <details class="filebox"><summary>📄 loaded records (showing ${kpi.loaded.length} of ${kpi.loaded_total})</summary>
        <div class="kpi-loaded" style="padding:4px 10px">${loadedRows}</div>
      </details>
    </div>`;

  // --- error analysis (grouped by TicketFlag) ---
  const flag = state.errorFlag || "all";
  const flagChips = [`<button class="btn btn-sm ${flag === "all" ? "btn-primary" : ""}" data-flag="all">All (${errs.errors.length})</button>`]
    .concat(errs.flags.map((f) => {
      const n = errs.errors.filter((e) => (e.TicketFlag || "Unflagged") === f).length;
      return `<button class="btn btn-sm ${flag === f ? "btn-primary" : ""}" data-flag="${esc(f)}">${esc(f)} (${n})</button>`;
    })).join(" ");
  const errRows = errs.errors
    .filter((e) => flag === "all" || (e.TicketFlag || "Unflagged") === flag)
    .map((e) => `
      <div class="err-row">
        <div class="err-head">
          <span class="chip ${flagClass(e.TicketFlag || "Unflagged", errs.flags)}">${esc(e.TicketFlag || "Unflagged")}</span>
          <span class="ci-job">${esc(e.jobpath || e.jobname)}</span>
          <span class="chip chip-red">${esc(e.ErrorCode || "?")}</span>
          <span class="ci-meta">${esc(e.ErrorType || "")} · ${ago(e.Date)}</span>
          ${linkBtn(e.buildurl)}
        </div>
        <div class="err-action">→ ${esc(e.ErrorAction || "no action recorded")}</div>
        <details class="filebox"><summary>✦ AI analysis ${e.AIConfidence ? `(confidence ${esc(e.AIConfidence)})` : ""}</summary>
          <pre>${esc(`type:   ${e.AIErrorType || "-"}\ncode:   ${e.AIErrorCode || "-"}\naction: ${e.AIErrorAction || "-"}\nticket: ${e.AITicketFlag || "-"}\n\n${e.AIRaw || ""}`)}</pre>
        </details>
      </div>`).join("") || `<div class="empty">no analyzed errors for this filter</div>`;

  view().innerHTML = `
    <div class="view-head"><h1>PIPELINES</h1><span class="sub">Jenkins · ${data.source}</span></div>
    ${kpiPanel}
    ${loadedPanel}
    <div class="ci-grid">
      <div>
        <div class="panel" style="margin-bottom:18px"><h2>🔴 recent failures (last ${data.failure_window_days}d)</h2>${failures}</div>
        <div class="panel"><h2>⏳ long-running (past their average)</h2>${longRunning}</div>
      </div>
      <div class="panel"><h2>all jobs</h2>${jobs}</div>
    </div>
    <div class="panel" style="margin-top:18px"><h2>🧬 error analysis — last ${errs.days}d · ${errs.source}</h2>
      <div class="filter-row" style="margin-bottom:12px;flex-wrap:wrap">${flagChips}</div>
      ${errRows}</div>`;

  startKpiCountdown(kpi.seconds_remaining);
  view().querySelectorAll("[data-flag]").forEach((b) => b.onclick = () => {
    state.errorFlag = b.dataset.flag;
    renderCI();
  });
  view().querySelectorAll("[data-hours]").forEach((b) => b.onclick = () => {
    state.kpiHours = parseInt(b.dataset.hours, 10);
    renderCI();
  });

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

/* ================= REPOSITORIES ================= */
async function renderRepos() {
  const data = await api("/api/repos");
  if (!data.repos.length) {
    view().innerHTML = `
      <div class="view-head"><h1>REPOSITORIES</h1></div>
      <div class="empty">no repositories configured — set REPO1_URL … REPO6_URL
        (+ _USER / _PASSWORD) and restart</div>`;
    return;
  }
  if (!data.repos.some((r) => r.slot === state.repoSlot)) {
    state.repoSlot = data.repos[0].slot;
    state.repoPath = ""; state.repoFile = null;
  }
  const cur = data.repos.find((r) => r.slot === state.repoSlot);

  const chips = data.repos.map((r) => `
    <button class="btn btn-sm ${r.slot === cur.slot ? "btn-primary" : ""}" data-repo="${r.slot}">
      ⛁ ${esc(r.name)}${r.dirty ? ` <span class="dirty-badge">${r.dirty}</span>` : ""}${r.cloned ? "" : " ⬇"}
    </button>`).join(" ");

  let body;
  if (!cur.cloned) {
    body = `
      <div class="panel" style="text-align:center;padding:40px">
        <p style="color:var(--dim);margin-bottom:6px">${esc(cur.url)}</p>
        <p style="color:var(--faint);font-size:12px;margin-bottom:18px">not cloned yet</p>
        <button class="btn btn-primary" id="repo-clone">⬇ Clone repository</button>
      </div>`;
  } else {
    const [treeData, fileData, diffData] = await Promise.all([
      api(`/api/repos/${cur.slot}/tree?path=${encodeURIComponent(state.repoPath || "")}`),
      state.repoFile ? api(`/api/repos/${cur.slot}/file?path=${encodeURIComponent(state.repoFile)}`).catch((e) => ({ error: e.message })) : null,
      state.repoFile ? api(`/api/repos/${cur.slot}/diff?path=${encodeURIComponent(state.repoFile)}`).catch(() => ({ diff: "" })) : null,
    ]);

    const segs = (state.repoPath || "").split("/").filter(Boolean);
    const crumbs = [`<a href="javascript:void 0" data-crumb="">${esc(cur.name)}</a>`]
      .concat(segs.map((s, i) =>
        `<a href="javascript:void 0" data-crumb="${esc(segs.slice(0, i + 1).join("/"))}">${esc(s)}</a>`))
      .join(" / ");

    const up = state.repoPath
      ? `<div class="tree-item" data-dir="${esc(segs.slice(0, -1).join("/"))}">📁 ..</div>` : "";
    const items = treeData.entries.map((e) => e.type === "dir"
      ? `<div class="tree-item ${e.dirty ? "dirty" : ""}" data-dir="${esc(e.path)}">📁 ${esc(e.name)}</div>`
      : `<div class="tree-item ${e.dirty ? "dirty" : ""} ${e.path === state.repoFile ? "active" : ""}" data-file="${esc(e.path)}">📄 ${esc(e.name)}<small>${(e.size / 1024).toFixed(1)}k</small></div>`
    ).join("") || `<div class="empty">empty directory</div>`;

    const editor = !state.repoFile
      ? `<div class="empty" style="padding-top:120px">select a file to view or edit<br>
           <small>edits stay on the server — nothing is pushed to remote</small></div>`
      : fileData.error
        ? `<div class="empty">⚠ ${esc(fileData.error)}</div>`
        : `
        <div class="editor-bar">
          <span class="ci-job">${esc(state.repoFile)}</span>
          <span class="spacer"></span>
          <button class="btn btn-sm btn-primary" id="repo-save">💾 Save (local)</button>
        </div>
        <textarea id="repo-editor" spellcheck="false">${esc(fileData.content)}</textarea>
        ${diffData.diff ? `<details class="filebox" open><summary>± local changes vs HEAD</summary><pre>${esc(diffData.diff)}</pre></details>` : ""}`;

    body = `
      <div class="repo-bar">
        <span class="crumbs">${crumbs}</span>
        <span class="spacer"></span>
        <span class="ci-meta">${esc(cur.branch)} · ${esc(cur.last_commit)}
          ${cur.dirty ? ` · <span class="pct-warn">${cur.dirty} locally modified</span>` : ""}</span>
        <button class="btn btn-sm" id="repo-pull">⇣ Pull</button>
        <button class="btn btn-sm btn-danger" id="repo-discard">Discard local edits</button>
      </div>
      <div class="repo-grid">
        <div class="panel tree-panel">${up}${items}</div>
        <div class="panel editor-panel">${editor}</div>
      </div>`;
  }

  view().innerHTML = `
    <div class="view-head"><h1>REPOSITORIES</h1>
      <span class="sub">shared local workspaces · edits are never pushed</span></div>
    <div class="filter-row" style="margin-bottom:16px;flex-wrap:wrap">${chips}</div>
    ${body}`;

  view().querySelectorAll("[data-repo]").forEach((b) => b.onclick = () => {
    state.repoSlot = parseInt(b.dataset.repo, 10);
    state.repoPath = ""; state.repoFile = null;
    renderRepos();
  });
  const on = (id, fn) => { const el = document.getElementById(id); if (el) el.onclick = fn; };
  on("repo-clone", async () => {
    try { await api(`/api/repos/${cur.slot}/clone`, { method: "POST" });
          toast(`⛁ ${esc(cur.name)} cloned`, "toast-xp"); renderRepos(); }
    catch (e) { oops(e); }
  });
  on("repo-pull", async () => {
    try { const r = await api(`/api/repos/${cur.slot}/pull`, { method: "POST" });
          toast(`⇣ ${esc(r.output.split("\n")[0])}`); renderRepos(); }
    catch (e) { oops(e); }
  });
  on("repo-discard", async () => {
    if (!confirm(`Discard ALL local edits in ${cur.name}?`)) return;
    try { await api(`/api/repos/${cur.slot}/discard`, { method: "POST" }); renderRepos(); }
    catch (e) { oops(e); }
  });
  on("repo-save", async () => {
    try {
      await api(`/api/repos/${cur.slot}/file`, {
        method: "PUT",
        body: { path: state.repoFile, content: document.getElementById("repo-editor").value } });
      toast(`💾 ${esc(state.repoFile)} saved locally`);
      renderRepos();
    } catch (e) { oops(e); }
  });
  view().querySelectorAll("[data-dir]").forEach((el) => el.onclick = () => {
    state.repoPath = el.dataset.dir; state.repoFile = null; renderRepos();
  });
  view().querySelectorAll("[data-file]").forEach((el) => el.onclick = () => {
    state.repoFile = el.dataset.file; renderRepos();
  });
  view().querySelectorAll("[data-crumb]").forEach((el) => el.onclick = () => {
    state.repoPath = el.dataset.crumb; state.repoFile = null; renderRepos();
  });
}

/* ================= TEAM ================= */
const TEAM_WINDOWS = [["7", "7d"], ["14", "14d"], ["30", "30d"], ["90", "90d"], ["all", "All"]];

async function renderTeam() {
  const win = state.teamWindow || "7";
  const days = win === "all" ? 3650 : parseInt(win, 10);
  const [lb, recap, badges, obj, act_] = await Promise.all([
    api(`/api/leaderboard?window=${win}`), api(`/api/recap?days=${Math.min(days, 365)}`),
    api("/api/badges"), api("/api/objectives"), api(`/api/activity?days=${days}`)]);

  const maxXp = Math.max(...lb.rows.map((r) => r.xp), 1);
  const rows = lb.rows.map((r, i) => `
    <div class="lb-row ${r.username === state.me.username ? "me" : ""}">
      <span class="lb-rank r${i + 1}">${i === 0 ? "♛" : i + 1}</span>
      <span class="lb-name"><b>${esc(r.display_name || r.username)}</b>
        <small>LV ${r.level.level} ${esc(r.level.rank)}${r.role === "approver" ? " · 🛡" : ""} · 🔥${r.streak} · ${r.badges} badges
          · ✅${r.stats.tickets_done} 👁${r.stats.resolved} ⛑${r.stats.builds_fixed} 🛡${r.stats.reviews} ⇄${r.stats.actions}</small></span>
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

  const maxObjOpen = Math.max(...obj.objectives.map((o) => o.open), 1);
  const objRows = obj.objectives.map((o) => `
    <div class="lb-row">
      <span class="lb-name"><b>🎯 ${esc(o.name)}</b>
        <small>${o.open} open · ${o.closed_recent} recently closed</small></span>
      <span class="lb-bar"><div style="width:${(o.open / maxObjOpen) * 100}%"></div></span>
      <span class="lb-xp">${o.open}</span>
    </div>`).join("");
  const missing = obj.missing.length ? `
    <div class="obj-missing">⚠ ${obj.missing.length} open ticket(s) without an objective:
      ${obj.missing.map((m) => `<div class="obj-missing-row">${esc(m.key)} — ${esc(m.summary).slice(0, 60)}
        <span class="assignee">${m.assignee ? "@" + esc(m.assignee) : "unassigned"}</span>${linkBtn(m.url)}</div>`).join("")}
      <a href="#/board" class="btn btn-sm" style="margin-top:6px">fix on the board ▸</a></div>`
    : `<div class="empty">✅ every open ticket has an objective</div>`;

  const feed = act_.events.map((e) => `
    <div class="tl-item kind-${esc(e.kind)}">
      <div class="tl-msg"><b>@${esc(e.username)}</b> ${esc(e.message || e.kind.replace(/_/g, " "))}
        ${e.points ? `<span class="tl-pts">+${e.points}</span>` : ""}</div>
      <div class="tl-meta">${esc(e.kind)} · ${ago(e.at)}</div>
    </div>`).join("") || `<div class="empty">no activity in this window</div>`;

  const filters = TEAM_WINDOWS.map(([v, label]) =>
    `<button class="btn btn-sm ${v === win ? "btn-primary" : ""}" data-win="${v}">${label}</button>`).join("");

  view().innerHTML = `
    <div class="view-head"><h1>TEAM</h1>
      <span class="sub">the team, last ${win === "all" ? "∞" : win + " days"}</span>
      <span class="spacer"></span><div class="filter-row">${filters}</div></div>
    <div class="stat-tiles">
      <div class="stat-tile"><b>${tw.xp}</b><span>team XP</span> ${delta(tw.xp, lw.xp)}</div>
      <div class="stat-tile"><b>${tw.tickets_done}</b><span>tickets closed</span> ${delta(tw.tickets_done, lw.tickets_done)}</div>
      <div class="stat-tile"><b>${tw.builds_fixed}</b><span>builds fixed</span> ${delta(tw.builds_fixed, lw.builds_fixed)}</div>
      <div class="stat-tile"><b>${tw.reviews}</b><span>reviews</span> ${delta(tw.reviews, lw.reviews)}</div>
      <div class="stat-tile"><b>@${esc(tw.top_user)}</b><span>MVP of the window</span></div>
    </div>
    <div class="guild-grid">
      <div>
        <div class="panel" style="margin-bottom:18px"><h2>♛ leaderboard</h2>${rows}</div>
        <div class="panel" style="margin-bottom:18px"><h2>🎯 objectives coverage</h2>
          ${objRows}${missing}</div>
        <div class="panel"><h2>team activity</h2><div class="timeline">${feed}</div></div>
      </div>
      <div class="panel"><h2>badge wall</h2><div class="badge-grid">${badgeTiles}</div></div>
    </div>`;

  view().querySelectorAll("[data-win]").forEach((b) => b.onclick = () => {
    state.teamWindow = b.dataset.win;
    renderTeam();
  });
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

$("#quick-add").addEventListener("click", () => { if (state.me) openQuickAdd(); });

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
