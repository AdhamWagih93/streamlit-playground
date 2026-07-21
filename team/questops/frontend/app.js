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

// a minutes count as a compact, human-readable duration: "45m", "2h 10m",
// "3d 4h", "2mo 5d" (two units max — the leading one carries the meaning)
function humanMins(min) {
  min = Math.round(Number(min));
  if (!Number.isFinite(min) || min < 0) return "";
  if (min < 60) return `${min}m`;
  if (min < 1440) { const h = Math.floor(min / 60), m = min % 60; return m ? `${h}h ${m}m` : `${h}h`; }
  if (min < 1440 * 30) { const d = Math.floor(min / 1440), h = Math.floor((min % 1440) / 60); return h ? `${d}d ${h}h` : `${d}d`; }
  if (min < 1440 * 365) { const mo = Math.floor(min / (1440 * 30)), d = Math.floor((min % (1440 * 30)) / 1440); return d ? `${mo}mo ${d}d` : `${mo}mo`; }
  const y = Math.floor(min / (1440 * 365)), mo = Math.floor((min % (1440 * 365)) / (1440 * 30));
  return mo ? `${y}y ${mo}mo` : `${y}y`;
}

// "…ago" from a timestamp (ISO/Date) …
function ago(iso) {
  if (!iso) return "";
  const min = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (!Number.isFinite(min)) return "";
  return min < 1 ? "just now" : `${humanMins(min)} ago`;
}

// … or from a minutes count the backend already computed (pipeline ago_min etc.)
function agoMins(min) {
  if (min == null || !Number.isFinite(Number(min))) return "";
  return min < 1 ? "just now" : `${humanMins(min)} ago`;
}

/* ---------------- theme ---------------- */
function applyTheme(t) {
  document.documentElement.dataset.theme = t;
  localStorage.setItem("qo_theme", t);
  document.querySelectorAll(".theme-toggle-btn").forEach((b) => {
    b.textContent = t === "light" ? "🌙" : "☀️";
    b.title = t === "light" ? "switch to dark mode" : "switch to light mode";
  });
}
document.querySelectorAll(".theme-toggle-btn").forEach((b) =>
  b.addEventListener("click", () =>
    applyTheme(document.documentElement.dataset.theme === "light" ? "dark" : "light")));
applyTheme(document.documentElement.dataset.theme || "dark");

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
                repos: renderRepos, deps: renderRepos, access: renderAccess,
                migration: renderMigration,
                upgrades: renderUpgrades, team: renderTeam, me: renderProfile };

// bumped on every navigation; async renders capture it and bail if it
// changed while they were awaiting — so a slow page (or a background poll)
// can never paint over the page you navigated to.
let NAV_EPOCH = 0;
const navToken = () => NAV_EPOCH;
const navStale = (tok) => tok !== NAV_EPOCH;

function route() {
  const name = (location.hash.replace("#/", "") || "overview").split("?")[0];
  const next = VIEWS[name] ? name : "overview";
  NAV_EPOCH++;
  state.view = next;
  document.querySelectorAll("#nav a").forEach((a) =>
    a.classList.toggle("active", a.dataset.view === state.view));
  const tok = navToken();
  view().innerHTML = `<div class="empty">loading…</div>`;
  VIEWS[state.view]().catch((e) => {
    if (!navStale(tok)) view().innerHTML = `<div class="empty">⚠ ${esc(e.message)}</div>`;
  });
}
window.addEventListener("hashchange", route);

/* ================= OVERVIEW ================= */
// live refresh: poll a cheap cursor every 5s (any member's action bumps it)
// and do a full re-pull every 60s for changes made outside QuestOps
let OV_CURSOR = null, OV_RENDERED = 0, OV_BUSY = false;
const OV_POLL_MS = 5000, OV_STALE_MS = 60000;

setInterval(async () => {
  if (state.view !== "overview" || document.hidden || OV_BUSY || !state.me) return;
  try {
    const { cursor } = await api("/api/overview/cursor");
    // re-check AFTER the await — the user may have navigated away meanwhile
    if (state.view !== "overview" || document.hidden) return;
    if (cursor !== OV_CURSOR || Date.now() - OV_RENDERED > OV_STALE_MS)
      await renderOverview();
  } catch { /* transient — next tick retries */ }
}, OV_POLL_MS);

async function renderOverview() {
  OV_BUSY = true;
  try {
    await renderOverviewInner();
  } finally { OV_BUSY = false; }
}

async function renderOverviewInner() {
  const tok = navToken();
  const [data, cur] = await Promise.all([
    api("/api/overview"), api("/api/overview/cursor").catch(() => null)]);
  if (navStale(tok)) return;  // navigated away during the fetch — don't paint
  OV_CURSOR = cur ? cur.cursor : OV_CURSOR;
  OV_RENDERED = Date.now();
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
        <span class="ci-meta">failed ${agoMins(f.ago_min)}${f.claimed_by ? ` · 🛠 @${esc(f.claimed_by)}` : ""}</span>
        ${linkBtn(f.url)}<a class="btn btn-sm" href="#/ci">act ▸</a></div>`),
    ...ci.stuck.map((l) => `
      <div class="ci-row"><span class="ci-dot dot-amber"></span>
        <span class="ci-job">${esc(l.job)} <small>#${l.number}</small></span>
        <span class="ci-meta">running ${humanMins(l.running_min)}${l.avg_min ? ` vs ~${humanMins(l.avg_min)} avg` : ""}</span>
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

  const scroll = view().scrollTop;  // live re-renders must not yank the page
  view().innerHTML = `
    <div class="view-head"><h1>OVERVIEW</h1>
      <span class="sub">the whole picture · ${esc(j.project || "")} · ${j.source}
        · <span class="ov-live">live</span></span>
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
  view().scrollTop = scroll;

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

const BOARD_FILTERS = [["all", "All issues"], ["mine", "My issues"], ["unassigned", "Unassigned"]];

async function renderBoard() {
  const data = await api("/api/board");
  BOARD_STATUSES = data.columns.map((c) => c.name);
  const filt = state.boardFilter || "all";
  const keep = (i) => filt === "mine" ? i.assignee === state.me.username
    : filt === "unassigned" ? !i.assignee : true;

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
    const shown = col.issues.filter(keep);
    const byGroup = {};
    shown.forEach((i) => { const g = i.group || ""; (byGroup[g] = byGroup[g] || []).push(i); });
    const names = Object.keys(byGroup).sort((a, b) =>
      a === "" ? 1 : b === "" ? -1 : a.localeCompare(b));
    const body = names.map((g) =>
      (g ? `<div class="group-head">▾ ${esc(g)}<span>${byGroup[g].length}</span></div>`
         : (names.length > 1 ? `<div class="group-head group-other">other<span>${byGroup[g].length}</span></div>` : ""))
      + byGroup[g].map(cardHtml).join("")).join("")
      || `<div class="empty" style="padding:24px 8px">nothing ${filt === "mine" ? "assigned to you" : "unassigned"} here</div>`;
    return `
    <div class="col" data-col="${esc(col.name)}">
      <div class="col-head"><span>${esc(col.label || col.name)}</span>
        <span>${shown.length}${shown.length !== col.issues.length ? ` / ${col.issues.length}` : ""}</span></div>
      ${body}
    </div>`;
  };
  const cols = data.columns.map(colHtml).join("");

  const filterChips = BOARD_FILTERS.map(([v, label]) =>
    `<button class="btn btn-sm ${v === filt ? "btn-primary" : ""}" data-bfilter="${v}">${label}</button>`).join("");

  view().innerHTML = `
    <div class="view-head"><h1>BOARD</h1>
      <span class="sub">Jira project ${esc(data.project)} · ${data.source} · drag cards to transition</span>
      <span class="spacer"></span>
      <div class="filter-row">${filterChips}</div></div>
    <div class="board">${cols}</div>`;

  view().querySelectorAll("[data-bfilter]").forEach((b) => b.onclick = () => {
    state.boardFilter = b.dataset.bfilter;
    renderBoard();
  });

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

/* ---- Failure Dive: log first, AI guidance only after the user confirms ---- */
function diveBtn(job, number) {
  return `<button class="btn btn-sm" data-dive="${esc(job)}" data-divenum="${number}"
    title="console log + pipeline source + AI root-cause guidance">🔎 Dive</button>`;
}

async function renderDive() {
  const { job, number } = state.dive;
  const [logData, pipe] = await Promise.all([
    api(`/api/dive/log?job=${encodeURIComponent(job)}&number=${number}`)
      .catch((e) => ({ log: "", error: e.message })),
    api(`/api/dive/pipeline?job=${encodeURIComponent(job)}`)
      .catch((e) => ({ script_path: "", note: e.message })),
  ]);

  const logHtml = (logData.log || "").split("\n").map((l) =>
    /(?:ERROR|Exception|FAILED|FAILURE|fatal|Caused by)/i.test(l)
      ? `<span class="log-err">${esc(l)}</span>` : esc(l)).join("\n");

  const pipePanel = pipe.script_path ? `
    <div class="panel" style="margin-bottom:18px">
      <h2>⚙ pipeline definition (from the Jenkins job's SCM config)</h2>
      <div class="repo-bar" style="margin-bottom:8px">
        <span class="chip chip-violet">📜 ${esc(pipe.script_path)}</span>
        ${pipe.repo ? `<span class="chip ${pipe.repo.cloned ? "chip-green" : "chip-amber"}">⛁ ${esc(pipe.repo.name)}${pipe.repo.cloned ? "" : " — not cloned"}</span>` : ""}
        ${pipe.defined_on && pipe.defined_on !== job ? `<span class="ci-meta">defined on ${esc(pipe.defined_on)}</span>` : ""}
        <span class="spacer"></span>
        ${pipe.repo && pipe.repo.cloned && pipe.script ? `<button class="btn btn-sm" id="dive-open-repo">open in Repositories ▸</button>` : ""}
      </div>
      ${pipe.note ? `<div class="kpi-note">${esc(pipe.note)}</div>` : ""}
      ${pipe.script ? `<details class="filebox" open><summary>groovy source</summary><pre>${esc(pipe.script)}</pre></details>` : ""}
    </div>` : `
    <div class="panel" style="margin-bottom:18px">
      <h2>⚙ pipeline definition</h2>
      <div class="empty">${esc(pipe.note || "no pipeline-from-SCM definition found")}</div>
    </div>`;

  const cached = state.diveAnalysis && state.diveAnalysis.key === `${job}#${number}`
    ? state.diveAnalysis : null;
  const aiPanel = cached ? `
    <div class="panel briefing"><h2>✦ AI root-cause analysis
      <span class="ci-meta" style="float:right">engine: ${esc(cached.engine)}${cached.used_pipeline ? " · pipeline source included" : ""}</span></h2>
      ${md(cached.analysis)}
      <button class="btn btn-sm btn-ghost" id="dive-reanalyze">↻ re-run analysis</button>
    </div>` : `
    <div class="panel briefing"><h2>✦ AI root-cause analysis</h2>
      <p style="color:var(--dim);font-size:13px;margin-bottom:10px">
        Sends the log tail${pipe.script ? ", the pipeline groovy source" : ""} and known
        error patterns for this job to your local Ollama — nothing runs until you confirm.</p>
      <button class="btn btn-primary" id="dive-analyze">✦ Analyze this failure</button>
    </div>`;

  view().innerHTML = `
    <div class="view-head"><h1>FAILURE DIVE</h1>
      <span class="sub">${esc(job)} · build #${number}</span>
      <span class="spacer"></span>
      <button class="btn btn-sm" id="dive-back">◂ back to pipelines</button></div>
    ${aiPanel}
    ${pipePanel}
    <div class="panel">
      <h2>🧾 console log${logData.error ? "" : " (tail)"}</h2>
      ${logData.error ? `<div class="empty">⚠ ${esc(logData.error)}</div>`
        : `<pre class="dive-log">${logHtml}</pre>`}
    </div>`;

  $("#dive-back").onclick = () => { state.dive = null; state.diveAnalysis = null; renderCI(); };
  const analyze = async (btn) => {
    btn.disabled = true;
    btn.textContent = "✦ analyzing…";
    try {
      const r = await api("/api/dive/analyze", { method: "POST", body: { job, number } });
      state.diveAnalysis = { key: `${job}#${number}`, ...r };
      renderDive();
    } catch (e) { oops(e); btn.disabled = false; btn.textContent = "✦ Analyze this failure"; }
  };
  const ab = document.getElementById("dive-analyze");
  if (ab) ab.onclick = () => analyze(ab);
  const rb = document.getElementById("dive-reanalyze");
  if (rb) rb.onclick = () => { state.diveAnalysis = null; renderDive().then(() => {
    const b = document.getElementById("dive-analyze"); if (b) b.click(); }); };
  const or_ = document.getElementById("dive-open-repo");
  if (or_) or_.onclick = () => {
    state.repoSlot = pipe.repo.slot;
    state.repoFile = pipe.script_path;
    state.repoPath = pipe.script_path.split("/").slice(0, -1).join("/");
    location.hash = "#/repos";
  };
}

function renderScmGroups(d) {
  if (!(d.groups || []).length && !(d.no_scm || []).length)
    return `<div class="empty">no pipelines found (${esc(d.source)})</div>`;
  const pipeRow = (p) => `
    <div class="scm-row">
      <span class="scm-job">${esc(p.job)}</span>
      ${p.scm_url
        ? `<a class="scm-url" href="${esc(p.scm_url)}" target="_blank" rel="noopener" title="${esc(p.scm_url)}">${esc(p.scm_url)}</a>`
        : `<span class="scm-url ci-meta">— inline Jenkinsfile —</span>`}
    </div>`;
  const groups = (d.groups || []).map((g) => `
    <details class="filebox" open>
      <summary>🌐 <b>${esc(g.host)}</b> <span class="chip chip-cyan">${g.count} pipeline(s)</span></summary>
      <div class="scm-list">${g.pipelines.map(pipeRow).join("")}</div>
    </details>`).join("");
  const noScm = (d.no_scm || []).length ? `
    <details class="filebox">
      <summary>📄 <b>${d.no_scm.length}</b> pipeline(s) with no pipeline-from-SCM definition
        <span class="ci-meta">· inline Jenkinsfile / freestyle</span></summary>
      <div class="scm-list">${d.no_scm.map(pipeRow).join("")}</div>
    </details>` : "";
  return `<div class="ci-meta" style="margin:2px 0 10px">${d.host_count} SCM host(s) · ${d.total} pipeline(s) · ${esc(d.source)}</div>${groups}${noScm}`;
}

async function renderCI() {
  if (state.dive) return renderDive();
  const kpiHours = state.kpiHours || 168;  // default: the past week
  const [data, kpi, errs] = await Promise.all([
    api("/api/ci"), api(`/api/kpi?hours=${kpiHours}`), api("/api/errors")]);
  const failures = data.failures.map((f) => `
    <div class="ci-row">
      <span class="ci-dot dot-red"></span>
      <span class="ci-job">${esc(f.job)} <small>#${f.number}</small></span>
      <span class="ci-meta">${esc(f.result)} · ${agoMins(f.ago_min)}${f.claimed_by ? ` · 🛠 @${esc(f.claimed_by)}` : ""}</span>
      ${f.latest_ok ? `<span class="chip chip-green" title="the pipeline's latest run succeeded — this failure is from an earlier run (e.g. another project on the same pipeline)">latest ✓</span>` : ""}
      ${linkBtn(f.url)}
      ${diveBtn(f.job, f.number)}
      ${f.claimed_by
        ? `<button class="btn btn-sm" data-fixed="${esc(f.job)}">It's green +35</button>`
        : `<button class="btn btn-sm" data-claim="${esc(f.job)}">I'm on it +10</button>`}
    </div>`).join("") || `<div class="empty">no failing builds 🎉</div>`;

  const longRunning = data.long_running.map((l) => `
    <div class="ci-row">
      <span class="ci-dot dot-amber"></span>
      <span class="ci-job">${esc(l.job)} <small>#${l.number}</small></span>
      <span class="ci-meta">running ${humanMins(l.running_min)}${l.avg_min ? ` · avg ${humanMins(l.avg_min)}` : ""}${l.claimed_by ? ` · 👀 @${esc(l.claimed_by)}` : ""}</span>
      ${linkBtn(l.url)}
      ${diveBtn(l.job, l.number)}
      ${l.claimed_by ? "" : `<button class="btn btn-sm" data-claim="${esc(l.job)}">Investigate +10</button>`}
    </div>`).join("") || `<div class="empty">nothing stuck</div>`;

  // only the 10 most active jobs (builds inside the window) — the rest are noise
  const topJobs = [...data.jobs]
    .sort((a, b) => (b.recent_builds || 0) - (a.recent_builds || 0))
    .slice(0, 10);
  const jobs = topJobs.map((j) => {
    const dot = j.building ? "dot-amber" : j.result === "SUCCESS" ? "dot-green"
      : j.result ? "dot-red" : "dot-grey";
    return `<div class="ci-row"><span class="ci-dot ${dot}"></span>
      <span class="ci-job">${esc(j.name)}</span>
      <span class="ci-meta">${j.recent_builds ? `${j.recent_builds} builds · ` : ""}${j.building ? "building…" : esc(j.result || "—")}${j.duration_min ? ` · ${humanMins(j.duration_min)}` : ""}</span>
      ${linkBtn(j.url)}</div>`;
  }).join("");

  // --- KPI window: countdown to the next loader run + failures at risk ---
  const nextAt = new Date(kpi.next_sync);
  const hhmm = `${String(nextAt.getHours()).padStart(2, "0")}:${String(nextAt.getMinutes()).padStart(2, "0")}`;
  const atRisk = kpi.at_risk.map((f) => `
    <div class="ci-row">
      <span class="ci-dot dot-red"></span>
      <span class="ci-job">${esc(f.job)} <small>#${f.number}</small></span>
      <span class="ci-meta">failed ${agoMins(f.ago_min)}</span>
      ${f.latest_ok ? `<span class="chip chip-green" title="latest run succeeded — earlier run failed">latest ✓</span>` : ""}
      ${linkBtn(f.url)}
      ${diveBtn(f.job, f.number)}
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
  const hourChips = [6, 24, 72, 168, 336, 720, 2160, 4320, 8760].map((h) =>
    `<button class="btn btn-sm ${h === kpiHours ? "btn-primary" : ""}" data-hours="${h}">${h < 48 ? h + "h" : h / 24 + "d"}</button>`).join(" ");
  const loadedRows = kpi.loaded.map((d) => `
    <div class="ci-row">
      <span class="ci-dot ${kpiDot(d.status)}"></span>
      <span class="ci-job">${esc(d.jobpath || d.jobname)} <small>#${esc(d.buildnumber)}</small></span>
      <span class="ci-meta">${esc(String(d.status || "").toUpperCase())} · ${ago(d.builddate || d["@timestamp"])}
        · ${esc(d.triggertype || "?")}${d.triggeredby ? " by " + esc(d.triggeredby) : ""}</span>
      ${linkBtn(d.buildurl)}
    </div>`).join("") || `<div class="empty">nothing loaded in this window</div>`;
  const runningRows = (kpi.running_builds || []).map((d) => `
    <div class="ci-row">
      <span class="ci-dot dot-amber"></span>
      <span class="ci-job">${esc(d.jobpath || d.jobname)} <small>#${esc(d.buildnumber)}</small></span>
      <span class="ci-meta">running · started ${ago(d.builddate || d["@timestamp"])}
        · ${esc(d.triggertype || "?")}${d.triggeredby && d.triggeredby !== "null" ? " by " + esc(d.triggeredby) : ""}</span>
      ${linkBtn(d.buildurl)}
    </div>`).join("");
  const kpiWarn = kpi.es_error
    ? `<div class="empty">⚠ Elasticsearch query failed on '${esc(kpi.index)}': ${esc(kpi.es_error)}</div>`
    : !kpi.window_applied
      ? `<div class="kpi-note">⚠ no parseable dates in '${esc(kpi.index)}' — the ${kpi.hours}h window could not be applied; showing the newest records</div>`
      : kpi.window_source === "client"
        ? `<div class="kpi-note">ℹ the index's date fields aren't date-mapped — the ${kpi.hours}h window is enforced on parsed builddate values instead</div>`
        : "";
  const pctCls = (p) => p >= 90 ? "pct-good" : p >= 70 ? "pct-warn" : "pct-bad";
  const st = kpi.stats || { total: 0, pipelines: [] };
  // completed = total − running; success % is over COMPLETED builds only.
  // 'compl' falls back to total for older payloads without the running split.
  const compl = (p) => (p.completed != null ? p.completed : p.total);
  // failing pipelines are front and centre WITH their links; fully-green ones
  // collapse behind a stat box; pipelines with builds ONLY running show separately
  const failing = st.pipelines.filter((p) => compl(p) > 0 && p.success < compl(p));
  const green = st.pipelines.filter((p) => compl(p) > 0 && p.success === compl(p));
  const runningOnly = st.pipelines.filter((p) => compl(p) === 0 && (p.running || 0) > 0);
  const greenPct = st.pipelines.length ? Math.round((green.length / st.pipelines.length) * 100) : 0;
  const pipeName = (p) => p.url && !p.url.startsWith("#")
    ? `<a class="ci-job" href="${esc(p.url)}" target="_blank" rel="noopener" title="open ${esc(p.job)} in Jenkins">${esc(p.job)} ↗</a>`
    : `<span class="ci-job" title="${esc(p.job)}">${esc(p.job)}</span>`;
  const runChip = (p) => (p.running || 0) > 0
    ? ` <span class="chip chip-cyan" title="in-progress builds, excluded from %">▶ ${p.running}</span>` : "";
  // a signed pts-delta vs the previous same-length window
  const winLabel = kpiHours < 48 ? kpiHours + "h" : kpiHours / 24 + "d";
  const deltaEl = (d) => d == null ? ""
    : `<span class="kpi-delta ${d > 0.05 ? "pct-good" : d < -0.05 ? "pct-bad" : "ci-meta"}" title="vs previous ${winLabel}">${d > 0.05 ? "▲" : d < -0.05 ? "▼" : "■"} ${Math.abs(d)} pts</span>`;
  const pipeRow = (p) => `
    <div class="kpi-pipe">
      ${pipeName(p)}
      <span class="lb-bar"><div class="${pctCls(p.pct)}" style="width:${p.pct}%"></div></span>
      <span class="kpi-pct ${pctCls(p.pct)}">${p.pct}%</span>
      <span class="ci-meta">${p.success}/${compl(p)}${runChip(p)}${p.delta != null ? " " + deltaEl(p.delta) : ""}</span>
    </div>`;
  const running = st.running || 0;
  const cmp = st.prev
    ? (st.prev.total
        ? `<br><span class="kpi-cmp">vs prev ${winLabel}: ${st.prev.overall_pct}% ${deltaEl(st.overall_delta)}</span>`
        : `<br><span class="kpi-cmp ci-meta">no builds in the prior ${winLabel}</span>`)
    : "";
  const kpiStats = st.total ? `
    <div class="kpi-stats">
      <div class="kpi-overall">
        <b class="${pctCls(st.overall_pct)}">${st.overall_pct}%</b>
        <span>overall success<br>${st.success}/${st.completed != null ? st.completed : st.total} completed${cmp}${running ? `<br><span class="kpi-running">▶ ${running} running (excluded)</span>` : ""}</span>
      </div>
      <div class="kpi-pipes">
        ${failing.map(pipeRow).join("") || `<div class="empty">no failing pipelines in this window 🎉</div>`}
        ${runningOnly.length ? `<div class="ci-meta" style="margin-top:6px">▶ ${runningOnly.length} pipeline(s) with only in-progress builds: ${runningOnly.map((p) => esc(p.job.split("/").slice(-2).join("/"))).join(", ")}</div>` : ""}
        ${green.length ? `
          <details class="green-group">
            <summary><b>${green.length}</b> pipeline(s) fully green —
              <b>${greenPct}%</b> of ${st.pipelines.length} pipelines
              <span class="ci-meta">· click to view them</span></summary>
            ${green.map(pipeRow).join("")}
          </details>` : ""}
      </div>
    </div>` : "";
  const loadedPanel = `
    <div class="panel" style="margin-bottom:18px">
      <h2>📦 pipeline KPIs — ${esc(kpi.source)} · ${kpi.loaded_total} builds in window</h2>
      <div class="filter-row" style="margin-bottom:10px">${hourChips}</div>
      ${kpi.index_expanded ? `<div class="remote-banner remote-new" style="margin:6px 0">
        <b>ℹ auto-searched sibling indices</b>
        <div class="ci-meta">the configured index had no recent builds, so QuestOps searched the pattern <code>${esc(kpi.index_expanded)}</code> and found your builds there. Set <b>QO_JENKINS_KPI_INDEX=${esc(kpi.index_expanded)}</b> to make it permanent.</div></div>` : ""}
      ${kpiWarn}
      ${kpi.stats_exact
        ? `<div class="kpi-note">✓ success %s computed over <b>all ${kpi.loaded_total}</b> builds in the window (server-side aggregation)${kpi.pipelines_truncated ? ` · pipeline list capped at ${(kpi.stats.pipelines || []).length}` : ""}</div>`
        : (kpi.truncated ? `<div class="kpi-note">⚠ the window holds ${kpi.loaded_total} builds — stats fell back to the newest ${kpi.fetched} (aggregation unavailable)</div>` : "")}
      ${!kpi.loaded_total && kpi.diagnostics ? `
        <details class="filebox" open><summary>🔎 why 0 builds? — query diagnostics</summary>
          <div style="padding:8px 12px">
            ${kpi.diagnostics.date_like_fields ? `<div class="kpi-note">windowing on <b>${esc((kpi.diagnostics.configured_date_fields || []).join(", ") || "—")}</b> (QO_KPI_DATE_FIELDS)${kpi.diagnostics.server_now ? ` · server now <b>${esc(kpi.diagnostics.server_now)}</b>` : ""}</div>
            <div class="ci-meta" style="margin:2px 0 6px">date-like fields in your docs: <b>${esc((kpi.diagnostics.date_like_fields || []).join(", ") || "none found")}</b>${(kpi.diagnostics.date_like_fields || []).length ? " — if the real build time is one of these and isn't listed above, set QO_KPI_DATE_FIELDS to it" : ""}</div>` : ""}
            ${(kpi.diagnostics.indices || []).length ? `
              <div class="kpi-note" style="margin-top:8px">indices matching <code>${esc((kpi.diagnostics.configured_index || "").replace(/[*]+$/, ""))}*</code> — QuestOps reads only <code>${esc(kpi.diagnostics.configured_index || "")}</code>; if fresh builds are in a dated/rolled-over sibling below, set <b>QO_JENKINS_KPI_INDEX</b> to a pattern like <code>${esc((kpi.diagnostics.configured_index || "").replace(/[*]+$/, ""))}*</code>:</div>
              ${kpi.diagnostics.indices.map((ix) => {
                const fresh = ix.newest && (Date.now() - new Date(ix.newest).getTime()) < 86400e3 * 14;
                const read = ix.index === kpi.diagnostics.configured_index;
                return `<div class="ci-meta">${fresh ? "🟢" : "•"} <code>${esc(ix.index)}</code> — ${esc(String(ix.docs ?? "?"))} docs${ix.newest ? ` · newest ${ago(ix.newest)}` : " · no dated builds"}${read ? " · <b>← currently read</b>" : ""}</div>`;
              }).join("")}` : ""}
            ${(kpi.diagnostics.attempts || []).map((a) => `<div class="ci-meta">• ${esc(a)}</div>`).join("")}
            ${(kpi.diagnostics.sample || []).length ? `<div class="kpi-note" style="margin-top:6px">sample raw dates from the index:</div>` : ""}
            ${(kpi.diagnostics.sample || []).map((s) => `<div class="ci-meta">• builddate=${esc(JSON.stringify(s.builddate))} · @timestamp=${esc(JSON.stringify(s["@timestamp"]))} · parseable: ${s.parsed ? "yes" : "NO"}</div>`).join("")}
            ${(kpi.diagnostics.doc_fields || []).length ? `<details style="margin-top:6px"><summary class="ci-meta">all fields in a sample document (${kpi.diagnostics.doc_fields.length})</summary><div class="ci-meta" style="margin-top:4px">${esc(kpi.diagnostics.doc_fields.join(", "))}</div></details>` : ""}
          </div>
        </details>` : ""}
      ${kpi.ignored ? `<div class="kpi-note">🚫 ${kpi.ignored} build(s) excluded by KPI_IGNORE (${esc((kpi.ignore_tokens || []).join(", "))})</div>` : ""}
      ${!kpi.loaded_total && kpi.newest_at ? `<div class="remote-banner remote-new" style="margin:6px 0">
        <b>⏳ no builds in the last ${kpiHours < 48 ? kpiHours + "h" : kpiHours / 24 + "d"}</b>
        <div class="ci-meta">the newest build in <code>${esc(kpi.index)}</code> ran <b>${ago(kpi.newest_at)}</b> — pick a wider window above to see it</div></div>` : ""}
      ${kpiStats}
      ${(kpi.stats && kpi.stats.running) ? `<details class="filebox" ${runningRows ? "" : ""}>
        <summary>▶ ${kpi.stats.running} running build(s) — in progress, excluded from success %${runningRows && (kpi.running_builds || []).length < kpi.stats.running ? ` (showing ${(kpi.running_builds || []).length})` : ""}</summary>
        <div class="kpi-loaded" style="padding:4px 10px">${runningRows || '<div class="ci-meta" style="padding:6px">running builds counted via aggregation; individual builds not in the fetched sample</div>'}</div>
      </details>` : ""}
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

  // one-look layout: failures + their error analysis together on the left,
  // long-running + top-10 most-active on the right; long lists scroll in place
  view().innerHTML = `
    <div class="view-head"><h1>PIPELINES</h1><span class="sub">Jenkins · ${data.source}</span></div>
    ${kpiPanel}
    ${loadedPanel}
    <div class="ci-grid">
      <div class="panel">
        <h2>🔴 recent failures (last ${data.failure_window_days}d)
          <span class="ov-more" style="color:var(--faint)">every failed run counts — not just the last</span></h2>
        <div class="ci-scroll">${failures}</div>
        <h2 class="panel-divider">🧬 error analysis — last ${errs.days}d · ${errs.source}</h2>
        <div class="filter-row" style="margin-bottom:10px;flex-wrap:wrap">${flagChips}</div>
        <div class="ci-scroll">${errRows}</div>
      </div>
      <div>
        <div class="panel" style="margin-bottom:18px"><h2>⏳ long-running (past their average)</h2>${longRunning}</div>
        <div class="panel"><h2>⚡ most active jobs — top ${topJobs.length} of ${data.jobs.length}</h2>${jobs}</div>
      </div>
    </div>
    <details class="panel scm-panel" id="scm-panel" style="margin-top:18px">
      <summary class="scm-sum">🔗 <b>pipelines by SCM host</b>
        <span class="ci-meta">· each pipeline's Git remote, grouped by hostname · reads each job's config.xml</span></summary>
      <div id="scm-body" style="margin-top:10px"><div class="empty">expand to load…</div></div>
    </details>`;

  const scmDet = document.getElementById("scm-panel");
  if (scmDet) scmDet.ontoggle = async () => {
    if (!scmDet.open || scmDet.dataset.loaded) return;
    scmDet.dataset.loaded = "1";
    const body = document.getElementById("scm-body");
    body.innerHTML = `<div class="empty acc-loading">⏳ reading each pipeline's config.xml…</div>`;
    try {
      body.innerHTML = renderScmGroups(await api("/api/ci/scm"));
    } catch (e) {
      scmDet.dataset.loaded = "";
      body.innerHTML = `<div class="empty">⚠ couldn't load: ${esc(e.message)}</div>`;
    }
  };

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
  view().querySelectorAll("[data-dive]").forEach((b) => b.onclick = () => {
    state.dive = { job: b.dataset.dive, number: parseInt(b.dataset.divenum, 10) };
    state.diveAnalysis = null;
    renderCI();
  });
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

async function renderActionForm() {
  const slot = $("#action-form-slot");
  // actions only target repositories DEFINED on the Repositories page
  const repoData = await api("/api/repos").catch(() => ({ repos: [] }));
  if (!repoData.repos.length) {
    slot.innerHTML = `
      <div class="panel" style="margin-bottom:16px"><h2>new repo action</h2>
        <div class="empty">no repositories defined —
          <a href="#/repos">add one on the Repositories page</a> first</div></div>`;
    return;
  }
  slot.innerHTML = `
    <div class="panel" style="margin-bottom:16px">
      <h2>new repo action</h2>
      <div class="form-grid">
        <label>Template<select id="af-template">${state.templates.map((t) =>
          `<option value="${t.id}">${esc(t.name)}</option>`).join("")}</select></label>
        <label>Repository<select id="af-repo">${repoData.repos.map((r) =>
          `<option value="${esc(r.url)}">⛁ ${esc(r.name)}</option>`).join("")}</select></label>
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
function diffHtml(d) {
  return esc(d).split("\n").map((l) =>
    l.startsWith("+++") || l.startsWith("---") || l.startsWith("commit ")
      ? `<span class="diff-file">${l}</span>`
      : l.startsWith("+") ? `<span class="diff-add">${l}</span>`
      : l.startsWith("-") ? `<span class="diff-del">${l}</span>`
      : l.startsWith("@@") ? `<span class="diff-hunk">${l}</span>` : l).join("\n");
}

function remoteBannerHtml(r) {
  if (!r) return "";
  const n = r.behind || 0, p = r.wt_pending || 0;
  if (!n && !p)
    return `<div class="remote-banner">✓ in sync with the server${r.branch ? ` · ${esc(r.branch)}` : ""}${r.fetch_error ? ` · ⚠ fetch failed: ${esc(r.fetch_error)}` : " · auto-checked every minute"}</div>`;
  return `<div class="remote-banner remote-new">
    <b>⇣ ${n ? `${n} new commit(s) on the server` : ""}${n && p ? " · " : ""}${p ? `${p} commit(s) not yet in your workspace` : ""}</b>
    ${(r.incoming || []).map((c) => `<div class="ci-meta">• ${esc(c.subject)} — ${esc(c.author)} · ${ago(new Date(c.at * 1000).toISOString())}</div>`).join("")}
    <button class="btn btn-sm btn-primary" id="remote-sync" style="margin-top:6px">⟳ Update my workspace</button>
  </div>`;
}

async function syncWorkspace(slot) {
  try {
    const r = await api(`/api/repos/${slot}/pull`, { method: "POST" });
    toast(`⟳ ${esc(r.output.split("\n")[0])}`);
    renderRepos();
  } catch (e) { oops(e); }
}

function wireRemoteSync() {
  const b = document.getElementById("remote-sync");
  if (b) b.onclick = () => syncWorkspace(state.repoSlot);
}

// server-change watcher: refreshes ONLY the banner node so a member mid-edit
// in the editor is never clobbered by a full re-render
setInterval(async () => {
  if (state.view !== "repos" || document.hidden || !state.me || !state.repoSlot) return;
  const el = document.getElementById("remote-banner");
  if (!el) return;
  try {
    const r = await api(`/api/repos/${state.repoSlot}/remote`);
    el.innerHTML = remoteBannerHtml(r);
    wireRemoteSync();
  } catch { /* next tick retries */ }
}, 60000);

function historyPanelHtml(hist) {
  if (!hist) return "";
  const scopeChips = state.repoFile ? `
    <button class="btn btn-sm ${state.historyScope !== "file" ? "btn-primary" : ""}" data-hscope="repo">whole repo</button>
    <button class="btn btn-sm ${state.historyScope === "file" ? "btn-primary" : ""}" data-hscope="file">${esc(state.repoFile.split("/").pop())}</button>` : "";
  const rows = (hist.commits || []).map((c) => `
    <div class="hist-row ${state.commitDiff && state.commitDiff.sha === c.sha ? "open" : ""}" data-commit="${esc(c.sha)}">
      <code class="hist-sha">${esc(c.short)}</code>
      <span class="hist-subject">${esc(c.subject)}</span>
      <span class="ci-meta">${esc(c.author)} · ${ago(new Date(c.at * 1000).toISOString())}</span>
      <span class="ci-meta">${state.commitDiff && state.commitDiff.sha === c.sha ? "▾ diff" : "▸ diff"}</span>
    </div>
    ${state.commitDiff && state.commitDiff.sha === c.sha
      ? `<pre class="dive-log commit-diff">${diffHtml(state.commitDiff.diff)}</pre>` : ""}`
  ).join("") || `<div class="empty">${esc(hist.error || "no commits")}</div>`;
  return `
    <div class="panel" style="margin-bottom:16px">
      <h2>🕘 commit history${hist.path ? ` — ${esc(hist.path)}` : ""}
        ${scopeChips ? `<span class="hist-scope">${scopeChips}</span>` : ""}</h2>
      <div class="hist-list">${rows}</div>
    </div>`;
}
function repoAddHtml() {
  const d = state.repoDiscover;
  const collFilter = state.repoDiscoverColl || "";
  const collChips = d && !d.error && (d.collections || []).length > 1 ? `
    <div class="filter-row" style="margin:8px 0;flex-wrap:wrap">
      <button class="btn btn-sm ${!collFilter ? "btn-primary" : ""}" data-disc-coll="">all collections</button>
      ${d.collections.map((c) => `<button class="btn btn-sm ${collFilter === c ? "btn-primary" : ""}" data-disc-coll="${esc(c)}">🗄 ${esc(c)}</button>`).join(" ")}
    </div>` : "";
  const list = !d ? `<div class="empty">browsing the ADO instance…</div>`
    : d.error ? `<div class="empty">⚠ ${esc(d.error)}<br>
        <button class="btn btn-sm" id="repo-discover-retry" style="margin-top:8px">↻ retry</button></div>`
    : (d.repos || []).map((r) => `
        <div class="ci-row"><span class="ci-job">⛁ ${esc(r.name)}</span>
          <span class="ci-meta">🗄 ${esc(r.collection)} · ${esc(r.project)}</span>
          <button class="btn btn-sm" data-adourl="${esc(r.url)}" data-adoname="${esc(r.name)}">+ add</button>
        </div>`).join("") || `<div class="empty">no repositories found${collFilter ? " in " + esc(collFilter) : " on the ADO instance"}</div>`;
  return `
    <div class="panel" style="margin-bottom:16px">
      <h2>add repository — cloned with the shared ADO credentials</h2>
      <div class="repo-bar">
        <input id="repo-new-url" placeholder="https://ado.mycorp.local/Collection/Project/_git/my-repo" style="flex:1">
        <input id="repo-new-name" placeholder="name (required — e.g. Engine, UI, inventories)" style="width:250px">
        <button class="btn btn-primary btn-sm" id="repo-add-submit">Add</button>
      </div>
      <div class="kpi-note" style="margin-top:6px">the name matters: the Failure Dive looks for your
        pipeline groovy sources in the repo named <b>Engine</b> (or the one matching the job's SCM URL)</div>
      <h2 style="margin-top:14px">or pick from the ADO instance
        ${d && !d.error ? `<button class="btn btn-sm ov-more" id="repo-discover-refresh">↻ refresh</button>` : ""}</h2>
      ${collChips}
      <div class="kpi-loaded">${list}</div>
    </div>`;
}

// patch ONLY the add-panel node — never a full re-render, so a slow page
// render can't clobber the discover results (and vice versa)
function updateAddPanel() {
  const slot = document.getElementById("repo-add-slot");
  if (!slot) return;
  slot.innerHTML = state.repoAddOpen ? repoAddHtml() : "";
  wireAddPanel();
}

async function loadDiscover(force = false) {
  if (state.repoDiscoverLoading || (state.repoDiscover && !force)) return;
  state.repoDiscoverLoading = true;
  const coll = state.repoDiscoverColl || "";
  try {
    state.repoDiscover = await api(`/api/repos/discover${coll ? "?collection=" + encodeURIComponent(coll) : ""}`);
  } catch (e) { state.repoDiscover = { error: e.message, repos: [], collections: [] }; }
  state.repoDiscoverLoading = false;
  updateAddPanel();
}

async function addRepo(url, name) {
  try {
    const r = await api("/api/repos", { method: "POST", body: { url, name } });
    toast(`⛁ <b>${esc(r.repo.name)}</b> defined — clone it to explore`, "toast-xp");
    state.repoSlot = r.repo.slot; state.repoPath = ""; state.repoFile = null;
    state.repoAddOpen = false;
    renderRepos();
  } catch (e) { oops(e); }
}

function wireAddPanel() {
  const submit = document.getElementById("repo-add-submit");
  if (submit) submit.onclick = () => {
    const url = $("#repo-new-url").value.trim(), name = $("#repo-new-name").value.trim();
    if (!name) return oops(new Error("repository name is required (e.g. Engine, UI, inventories, ocp-templates)"));
    addRepo(url, name);
  };
  const slot = document.getElementById("repo-add-slot");
  (slot || view()).querySelectorAll("[data-adourl]").forEach((b) => b.onclick = () =>
    addRepo(b.dataset.adourl, b.dataset.adoname));
  const retry = document.getElementById("repo-discover-retry");
  if (retry) retry.onclick = () => { state.repoDiscover = null; updateAddPanel(); loadDiscover(true); };
  const refresh = document.getElementById("repo-discover-refresh");
  if (refresh) refresh.onclick = () => { state.repoDiscover = null; updateAddPanel(); loadDiscover(true); };
  (slot || view()).querySelectorAll("[data-disc-coll]").forEach((b) => b.onclick = () => {
    state.repoDiscoverColl = b.dataset.discColl;
    state.repoDiscover = null;  // re-browse narrowed to the collection (spares the instance)
    updateAddPanel();
    loadDiscover(true);
  });
}

function wireRepoAdd() {
  const t = document.getElementById("repo-add-toggle");
  if (t) t.onclick = () => {
    state.repoAddOpen = !state.repoAddOpen;
    if (state.repoAddOpen && !state.repoDiscover) loadDiscover();
    t.textContent = state.repoAddOpen ? "✕ close" : "+ Add repository";
    t.classList.toggle("btn-primary", !state.repoAddOpen);
    updateAddPanel();
  };
  wireAddPanel();
}

function scanPanelHtml(s) {
  if (s.error) return `<div class="panel" style="margin-bottom:16px">
    <h2>🔬 tech scan</h2><div class="empty">⚠ ${esc(s.error)}</div></div>`;
  const cards = s.technologies.map((t) => `
    <div class="scan-card">
      <div class="scan-head">${t.icon} <b>${esc(t.name)}</b></div>
      <div class="scan-evidence">${t.evidence.map((e) => `<span class="chip">${esc(e)}</span>`).join(" ")}</div>
      ${t.recommendations.length
        ? `<ul class="scan-recs">${t.recommendations.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>`
        : `<div class="scan-ok">✓ no findings</div>`}
    </div>`).join("") || `<div class="empty">no known technologies detected</div>`;
  const general = s.general.length ? `
    <div class="obj-missing" style="margin-top:12px">⚠ repo hygiene:
      <ul class="scan-recs">${s.general.map((g) => `<li>${esc(g)}</li>`).join("")}</ul></div>` : "";
  return `
    <div class="panel" style="margin-bottom:16px">
      <h2>🔬 tech scan — ${s.files_scanned} files${s.truncated ? " (truncated)" : ""}
        <button class="btn btn-sm ov-more" id="repo-rescan">↻ rescan</button></h2>
      <div class="scan-grid">${cards}</div>
      ${general}
    </div>`;
}

function agentState(slot) {
  state.agents = state.agents || {};
  return (state.agents[slot] = state.agents[slot]
    || { msgs: [], write: false, busy: false, pending: [], session: null });
}

function fmtAgentArgs(tool, input) {
  try {
    const a = JSON.parse(input);
    if (a.command) return a.command;
    if (a.path !== undefined)
      return a.path + (a.content !== undefined ? ` (${a.content.length} chars)` : "");
    return JSON.stringify(a);
  } catch { return input; }
}

const AGENT_ST_CLS = { executed: "chip-green", denied: "chip-red",
                       pending: "chip-amber", error: "chip-red" };

function handleAgentResponse(ag, r) {
  if (r.status === "pending") {
    ag.session = r.session;
    ag.pending = r.pending;
  } else {
    ag.msgs.push({ role: "assistant", content: r.reply, steps: r.steps, engine: r.engine });
    ag.pending = [];
    ag.session = null;
  }
}

function agentPanelHtml(cur, logData) {
  const ag = agentState(cur.slot);
  const stepHtml = (s) => `
    <div class="agent-step"><b>${esc(s.tool)}</b> <code>${esc(fmtAgentArgs(s.tool, s.input))}</code>
      <span class="chip ${AGENT_ST_CLS[s.status] || ""}">${esc(s.status)}</span>
      ${s.output && s.status !== "denied" ? `<pre>${esc(s.output)}</pre>` : ""}</div>`;
  const msgs = ag.msgs.map((m) => m.role === "user"
    ? `<div class="ai-msg ai-user">${esc(m.content)}</div>`
    : `<div class="ai-msg ai-bot">${md(m.content)}
        ${(m.steps || []).length ? `<details class="filebox"><summary>🔧 ${m.steps.length} command(s) this turn — every one human-decided &amp; logged</summary>
          ${m.steps.map(stepHtml).join("")}</details>` : ""}
        ${m.engine && m.engine !== "langchain+ollama" ? `<div class="ci-meta" style="margin-top:4px">engine: ${esc(m.engine)}</div>` : ""}
      </div>`).join("");

  const pendingBlock = ag.pending.length ? `
    <div class="agent-pending">
      <div class="agent-pending-head">🛡 the agent wants to run ${ag.pending.length} command(s) —
        nothing executes without your approval</div>
      ${ag.pending.map((p) => `
        <div class="agent-cmd ${p.write ? "agent-cmd-write" : ""}">
          <span class="chip ${p.write ? "chip-amber" : "chip-cyan"}">${p.write ? "WRITE" : "read-only"}</span>
          <code>${esc(p.tool)}: ${esc(fmtAgentArgs(p.tool, p.input))}</code>
          <button class="btn btn-sm btn-primary" data-agent-approve="${p.id}">✓ Approve</button>
          <button class="btn btn-sm btn-danger" data-agent-deny="${p.id}">✕ Deny</button>
        </div>`).join("")}
      ${ag.pending.length > 1 ? `<button class="btn btn-sm" id="agent-approve-all">✓ approve all ${ag.pending.length}</button>` : ""}
    </div>` : "";

  const log = (logData && logData.log) || [];
  const audit = `
    <details class="filebox" style="margin-top:10px">
      <summary>🗒 agent audit log (${log.length}) — every command, decision and output is stored in the database</summary>
      ${log.map((l) => `
        <div class="agent-step"><b>${esc(l.tool)}</b> <code>${esc(fmtAgentArgs(l.tool, l.input))}</code>
          <span class="chip ${AGENT_ST_CLS[l.status] || ""}">${esc(l.status)}</span>
          <span class="ci-meta">@${esc(l.username)} · ${ago(l.at)}${l.decided_by ? ` · decided by @${esc(l.decided_by)}` : ""}</span>
          ${l.output && l.status !== "denied" ? `<pre>${esc(l.output)}</pre>` : ""}</div>`).join("")
        || `<div class="empty">no agent activity yet for this repository</div>`}
    </details>`;

  return `
    <div class="panel" style="margin-top:18px">
      <h2>✦ repo agent — ${esc(cur.name)}
        <label class="agent-write-toggle" title="offer the agent write tools (LOCAL workspace only — never pushed; each write still needs your approval)">
          <input type="checkbox" id="agent-write" ${ag.write ? "checked" : ""}> enable write actions</label></h2>
      ${ag.write ? `<div class="kpi-note" style="margin-bottom:8px">⚠ write actions ON — the agent may PROPOSE file writes; each one still needs your approval, lands only in the local workspace, and is reviewable as a diff.</div>` : ""}
      <div class="ai-log agent-log" id="agent-log">
        ${msgs || `<div class="ai-msg ai-bot">Ask me about this repository. I explore with read-only commands
          (ls, grep, find, git log…) — but <b>every command waits for your approval</b> before it runs,
          and everything is logged to the audit trail below.</div>`}
        ${pendingBlock}
        ${ag.busy ? `<div class="ai-msg ai-bot">✦ working…</div>` : ""}
      </div>
      <form class="ai-form agent-form" id="agent-form">
        <div class="agent-input-wrap">
          <div id="agent-ac" class="agent-ac hidden"></div>
          <input id="agent-input" autocomplete="off"
            placeholder="${ag.pending.length ? "approve or deny the proposed commands first" : "ask about this repo — @ references files/folders, # references Jira tickets"}"
            ${ag.busy || ag.pending.length ? "disabled" : ""}>
        </div>
        <button class="btn btn-primary" ${ag.busy || ag.pending.length ? "disabled" : ""}>➤</button>
      </form>
      ${audit}
    </div>`;
}

async function renderRepos() {
  const data = await api("/api/repos");
  const addPanel = `<div id="repo-add-slot">${state.repoAddOpen ? repoAddHtml() : ""}</div>`;
  const headHtml = `
    <div class="view-head"><h1>REPOSITORIES</h1>
      <span class="sub">your personal workspace (@${esc(state.me.username)}) — teammates never overlap · server changes auto-watched · edits never pushed</span>
      <span class="spacer"></span>
      <button class="btn btn-sm ${state.repoAddOpen ? "" : "btn-primary"}" id="repo-add-toggle">
        ${state.repoAddOpen ? "✕ close" : "+ Add repository"}</button></div>`;
  if (!data.repos.length) {
    view().innerHTML = headHtml + addPanel +
      `<div class="empty">no repositories defined yet — add one from your ADO instance ↑</div>`;
    wireRepoAdd();
    return;
  }
  if (!data.repos.some((r) => r.slot === state.repoSlot)) {
    state.repoSlot = data.repos[0].slot;
    state.repoPath = ""; state.repoFile = null;
  }
  const cur = data.repos.find((r) => r.slot === state.repoSlot);
  // the dependency analysis is Engine-specific for now (other named repos —
  // UI, inventories, ocp-templates, Tools — will get their own logic later)
  const isEngine = (cur.name || "").toLowerCase() === "engine";

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
        <div class="repo-bar" style="justify-content:center;margin-bottom:14px">
          <input id="repo-branch" placeholder="branch (empty = default branch)"
            style="width:260px" spellcheck="false">
        </div>
        <button class="btn btn-primary" id="repo-clone">⬇ Clone repository</button>
        <button class="btn btn-danger" id="repo-remove">🗑 Remove</button>
      </div>`;
  } else {
    let scanHtml = "";
    if (state.scanOpen) {
      if (!state.scanData || state.scanData._slot !== cur.slot) {
        try {
          state.scanData = { ...(await api(`/api/repos/${cur.slot}/scan`)), _slot: cur.slot };
        } catch (e) {
          state.scanData = { _slot: cur.slot, error: e.message, technologies: [], general: [] };
        }
      }
      scanHtml = scanPanelHtml(state.scanData);
    }
    // Engine-only dependency analysis, embedded on demand
    let depsHtml = "";
    if (isEngine && state.depsOpen) {
      if (!state.depsData || state.depsData._slot !== cur.slot || state.depsRefresh) {
        try {
          state.depsData = { ...(await api(`/api/deps?slot=${cur.slot}${state.depsRefresh ? "&refresh=true" : ""}`)), _slot: cur.slot };
        } catch (e) {
          state.depsData = { _slot: cur.slot, error: e.message };
        }
        state.depsRefresh = false;
      }
      depsHtml = `<div class="deps-embed">${state.depsData.error
        ? `<div class="panel"><div class="empty">⚠ ${esc(state.depsData.error)}</div></div>`
        : depPanelHtml(state.depsData)}</div>`;
    }
    const histPath = state.historyScope === "file" && state.repoFile ? state.repoFile : "";
    const [treeData, fileData, diffData, agentLogData, remoteData, histData] = await Promise.all([
      api(`/api/repos/${cur.slot}/tree?path=${encodeURIComponent(state.repoPath || "")}`),
      state.repoFile ? api(`/api/repos/${cur.slot}/file?path=${encodeURIComponent(state.repoFile)}`).catch((e) => ({ error: e.message })) : null,
      state.repoFile ? api(`/api/repos/${cur.slot}/diff?path=${encodeURIComponent(state.repoFile)}`).catch(() => ({ diff: "" })) : null,
      api(`/api/repos/${cur.slot}/agent/log`).catch(() => ({ log: [] })),
      api(`/api/repos/${cur.slot}/remote`).catch(() => null),
      state.historyOpen ? api(`/api/repos/${cur.slot}/history?path=${encodeURIComponent(histPath)}`)
        .catch((e) => ({ commits: [], error: e.message })) : null,
    ]);
    state.agentLog = agentLogData;

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
        ${diffData.diff ? `<details class="filebox" open><summary>± my local changes vs HEAD</summary><pre>${diffHtml(diffData.diff)}</pre></details>` : ""}`;

    body = `
      <div class="repo-bar">
        <span class="crumbs">${crumbs}</span>
        <span class="spacer"></span>
        <span class="ci-meta">${esc(cur.branch)} · ${esc(cur.last_commit)}
          ${cur.dirty ? ` · <span class="pct-warn">${cur.dirty} locally modified</span>` : ""}</span>
        <button class="btn btn-sm ${state.scanOpen ? "btn-primary" : ""}" id="repo-scan">🔬 Tech scan</button>
        ${isEngine ? `<button class="btn btn-sm ${state.depsOpen ? "btn-primary" : ""}" id="repo-deps" title="pipelines → playbooks / roles / scripts">⛓ Dependencies</button>` : ""}
        <button class="btn btn-sm ${state.historyOpen ? "btn-primary" : ""}" id="repo-history">🕘 History</button>
        <button class="btn btn-sm" id="repo-pull" title="fetch the server copy and move your workspace to it">⟳ Sync</button>
        <button class="btn btn-sm btn-danger" id="repo-discard">Discard my edits</button>
        <button class="btn btn-sm btn-danger" id="repo-remove"
          title="remove from QuestOps (all members' workspaces deleted; the remote repo is untouched)">🗑</button>
      </div>
      <div id="remote-banner">${remoteBannerHtml(remoteData)}</div>
      ${scanHtml}
      ${depsHtml}
      ${state.historyOpen ? historyPanelHtml(histData) : ""}
      <div class="repo-grid">
        <div class="panel tree-panel">${up}${items}</div>
        <div class="panel editor-panel">${editor}</div>
      </div>`;
  }

  view().innerHTML = `
    ${headHtml}
    ${addPanel}
    <div class="filter-row" style="margin-bottom:16px;flex-wrap:wrap">${chips}</div>
    ${body}
    ${cur.cloned ? agentPanelHtml(cur, state.agentLog) : ""}`;
  wireRepoAdd();

  view().querySelectorAll("[data-repo]").forEach((b) => b.onclick = () => {
    state.repoSlot = parseInt(b.dataset.repo, 10);
    state.repoPath = ""; state.repoFile = null;
    renderRepos();
  });
  const on = (id, fn) => { const el = document.getElementById(id); if (el) el.onclick = fn; };
  on("repo-clone", async () => {
    const branch = ($("#repo-branch") ? $("#repo-branch").value : "").trim();
    try {
      await api(`/api/repos/${cur.slot}/clone`, { method: "POST", body: { branch } });
      toast(`⛁ ${esc(cur.name)} cloned${branch ? ` · ${esc(branch)}` : ""}`, "toast-xp");
      renderRepos();
    } catch (e) { oops(e); }
  });
  on("repo-pull", () => syncWorkspace(cur.slot));
  wireRemoteSync();
  on("repo-history", () => {
    state.historyOpen = !state.historyOpen;
    state.commitDiff = null;
    renderRepos();
  });
  view().querySelectorAll("[data-commit]").forEach((el) => el.onclick = async () => {
    const sha = el.dataset.commit;
    if (state.commitDiff && state.commitDiff.sha === sha) {
      state.commitDiff = null;
      return renderRepos();
    }
    try {
      state.commitDiff = await api(`/api/repos/${cur.slot}/commit/${sha}`);
      renderRepos();
    } catch (e) { oops(e); }
  });
  view().querySelectorAll("[data-hscope]").forEach((b) => b.onclick = () => {
    state.historyScope = b.dataset.hscope;
    state.commitDiff = null;
    renderRepos();
  });
  on("repo-discard", async () => {
    if (!confirm(`Discard ALL of YOUR local edits in ${cur.name}?\n(teammates' workspaces are untouched)`)) return;
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
  on("repo-scan", () => { state.scanOpen = !state.scanOpen; renderRepos(); });
  on("repo-rescan", () => { state.scanData = null; renderRepos(); });
  on("repo-deps", () => { state.depsOpen = !state.depsOpen; renderRepos(); });
  on("dep-refresh", () => { state.depsRefresh = true; state.depRoot = null; renderRepos(); });
  if (isEngine && state.depsOpen && state.depsData && !state.depsData.error)
    wireDepPanel(state.depsData);
  on("repo-remove", async () => {
    if (!confirm(`Remove ${cur.name} from QuestOps?\n\nThe local workspace (including un-pushed edits) is deleted.\nThe remote repository is untouched.`)) return;
    try {
      await api(`/api/repos/${cur.slot}`, { method: "DELETE" });
      toast(`🗑 ${esc(cur.name)} removed`);
      state.repoSlot = null; state.scanData = null;
      renderRepos();
    } catch (e) { oops(e); }
  });
  const agentForm = document.getElementById("agent-form");
  if (agentForm) {
    const ag = agentState(cur.slot);
    const wt = document.getElementById("agent-write");
    if (wt) wt.onchange = () => { ag.write = wt.checked; renderRepos(); };
    const log = document.getElementById("agent-log");
    if (log) log.scrollTop = log.scrollHeight;

    const decide = async (ids, approve) => {
      if (ag.busy) return;
      ag.busy = true;
      renderRepos();
      try {
        let r = null;
        for (const id of ids)  // deciding the last call of a round resumes the agent
          r = await api("/api/repos/agent/decide",
                        { method: "POST", body: { command_id: id, approve } });
        if (r) handleAgentResponse(ag, r);
      } catch (err) { oops(err); }
      ag.busy = false;
      renderRepos();
    };
    view().querySelectorAll("[data-agent-approve]").forEach((b) =>
      b.onclick = () => decide([parseInt(b.dataset.agentApprove, 10)], true));
    view().querySelectorAll("[data-agent-deny]").forEach((b) =>
      b.onclick = () => decide([parseInt(b.dataset.agentDeny, 10)], false));
    on("agent-approve-all", () => decide(ag.pending.map((p) => p.id), true));

    // ---- autocomplete: '@' = workspace paths, '#' = Jira tickets ----
    const acInput = document.getElementById("agent-input");
    const acBox = document.getElementById("agent-ac");
    let acList = [], acIdx = 0, acStart = -1, acTrig = "";

    const pathsFor = async () => {
      state.repoPaths = state.repoPaths || {};
      const c = state.repoPaths[cur.slot];
      if (c && Date.now() - c.at < 120000) return c.paths;
      const r = await api(`/api/repos/${cur.slot}/files`).catch(() => ({ paths: [] }));
      state.repoPaths[cur.slot] = { at: Date.now(), paths: r.paths || [] };
      return state.repoPaths[cur.slot].paths;
    };
    const ticketsFor = async () => {
      const c = state.agentTickets;
      if (c && Date.now() - c.at < 120000) return c.items;
      const b = await api("/api/board").catch(() => ({ columns: [] }));
      const items = (b.columns || []).flatMap((col) => col.issues.map((i) => ({
        key: i.key, summary: i.summary || "", status: i.status || "" })));
      state.agentTickets = { at: Date.now(), items };
      return items;
    };
    const closeAc = () => { acBox.classList.add("hidden"); acList = []; acStart = -1; };
    const renderAc = () => {
      acBox.innerHTML = acList.map((it, i) => `
        <div class="ac-item ${i === acIdx ? "active" : ""}" data-ac="${i}">
          ${acTrig === "@"
            ? `${it.type === "dir" ? "📁" : "📄"} ${esc(it.path)}${it.type === "dir" ? "/" : ""}`
            : `🎫 ${esc(it.key)} <span class="ac-sub">${esc(it.summary.slice(0, 60))} · ${esc(it.status)}</span>`}
        </div>`).join("");
      acBox.classList.remove("hidden");
      acBox.querySelectorAll("[data-ac]").forEach((el) =>
        el.onmousedown = (e) => { e.preventDefault(); pickAc(parseInt(el.dataset.ac, 10)); });
      const active = acBox.querySelector(".ac-item.active");
      if (active) active.scrollIntoView({ block: "nearest" });
    };
    const updateAc = async () => {
      const pos = acInput.selectionStart;
      const upto = acInput.value.slice(0, pos);
      const at = Math.max(upto.lastIndexOf("@"), upto.lastIndexOf("#"));
      if (at === -1) return closeAc();
      acTrig = upto[at];
      const q = upto.slice(at + 1);
      if (/\s/.test(q)) return closeAc();
      const ql = q.toLowerCase();
      if (acTrig === "@") {
        const paths = await pathsFor();
        acList = paths.filter((p) => p.path.toLowerCase().includes(ql))
          .sort((a, b) => {
            const ap = a.path.toLowerCase().startsWith(ql) ? 0 : 1;
            const bp = b.path.toLowerCase().startsWith(ql) ? 0 : 1;
            return ap - bp || a.path.length - b.path.length;
          }).slice(0, 8);
      } else {
        const tickets = await ticketsFor();
        acList = tickets.filter((t) =>
          t.key.toLowerCase().includes(ql) || t.summary.toLowerCase().includes(ql))
          .sort((a, b) => {
            const ap = a.key.toLowerCase().startsWith(ql) ? 0 : 1;
            const bp = b.key.toLowerCase().startsWith(ql) ? 0 : 1;
            return ap - bp || a.key.localeCompare(b.key);
          }).slice(0, 8);
      }
      acIdx = 0; acStart = at;
      acList.length ? renderAc() : closeAc();
    };
    const pickAc = (i) => {
      const it = acList[i];
      if (!it || acStart < 0) return;
      const pos = acInput.selectionStart;
      // keep the marker; a trailing '/' on folders keeps the drill-down going
      const insert = acTrig === "@"
        ? `@${it.path}${it.type === "dir" ? "/" : " "}`
        : `#${it.key} `;
      acInput.value = acInput.value.slice(0, acStart) + insert + acInput.value.slice(pos);
      const np = acStart + insert.length;
      acInput.setSelectionRange(np, np);
      closeAc();
      acInput.focus();
      if (acTrig === "@" && it.type === "dir") updateAc();
    };
    acInput.addEventListener("input", updateAc);
    acInput.addEventListener("keydown", (e) => {
      if (acBox.classList.contains("hidden")) return;
      if (e.key === "ArrowDown") { e.preventDefault(); acIdx = (acIdx + 1) % acList.length; renderAc(); }
      else if (e.key === "ArrowUp") { e.preventDefault(); acIdx = (acIdx - 1 + acList.length) % acList.length; renderAc(); }
      else if (e.key === "Enter" || e.key === "Tab") { e.preventDefault(); pickAc(acIdx); }
      else if (e.key === "Escape") { e.preventDefault(); closeAc(); }
    });
    acInput.addEventListener("blur", () => setTimeout(closeAc, 150));

    agentForm.onsubmit = async (e) => {
      e.preventDefault();
      const msg = document.getElementById("agent-input").value.trim();
      if (!msg || ag.busy || ag.pending.length) return;
      const history = ag.msgs.slice(-8).map((m) => ({ role: m.role, content: m.content }));
      ag.msgs.push({ role: "user", content: msg });
      ag.busy = true;
      renderRepos();
      try {
        const r = await api(`/api/repos/${cur.slot}/agent`, { method: "POST",
          body: { message: msg, history, allow_write: ag.write } });
        handleAgentResponse(ag, r);
      } catch (err) {
        ag.msgs.push({ role: "assistant", content: `⚠ ${err.message}`, steps: [] });
      }
      ag.busy = false;
      renderRepos();
    };
  }
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

/* ================= DEPENDENCIES ================= */
const DEP_ICON = (n) => n.type === "pipeline" ? "⚙" : n.type === "playbook" ? "📜"
  : n.type === "role" ? "🎭" : n.type === "caller" ? "🔁"
  : n.path.endsWith(".py") ? "🐍" : "🐚";

function roleTaskTree(n, file, seen) {
  const short = file.split("/tasks/")[1] || file;
  const kids = (n.internals.includes[file] || [])
    .filter((f) => !seen.has(f))
    .map((f) => { seen.add(f); return roleTaskTree(n, f, seen); }).join("");
  const label = `📄 <code>${esc(short)}</code>`;
  return kids
    ? `<details class="dep-node" open><summary>${label}</summary><div class="dep-kids">${kids}</div></details>`
    : `<div class="dep-leaf">${label}</div>`;
}

function roleInternalsHtml(n) {
  const it = n.internals;
  if (!it || !it.entry) return "";
  const seen = new Set([it.entry]);
  const chain = roleTaskTree(n, it.entry, seen);
  const orphans = (it.orphan_tasks || []).map((f) => `
    <div class="dep-leaf">📄 <code>${esc(f.split("/tasks/")[1] || f)}</code>
      <span class="chip chip-red" title="not reachable from this role's tasks/main.yml">not included</span></div>`).join("");
  return `<div class="dep-kids dep-role-tasks">${chain}${orphans}</div>`;
}

function callMetaHtml(parent, childId) {
  const c = (parent && parent.calls || []).find((x) => x.target === childId);
  if (!c) return "";
  const bits = [];
  if (c.env) bits.push(`<span class="chip ${c.env.toLowerCase() === "prd" ? "chip-green" : "chip-red"}" title="caller environment argument">env ${esc(c.env)}</span>`);
  if (c.inventory) bits.push(`<span class="chip" title="ansible inventory">inv ${esc(c.inventory)}</span>`);
  if (c.container) bits.push(`<span class="chip" title="container">ctr ${esc(c.container)}</span>`);
  if (c.args) bits.push(`<span class="ci-meta" title="${esc(c.args)}">+args</span>`);
  return bits.join(" ");
}

function depTree(map, id, seen, meta) {
  const n = map[id];
  if (!n) return "";
  if (seen.has(id))
    return `<div class="dep-leaf ci-meta">↻ ${esc(n.path)} (cycle)</div>`;
  const next = new Set(seen); next.add(id);
  const kids = (n.out || []).map((c) => depTree(map, c, next, callMetaHtml(n, c))).join("")
    + (n.type === "role" ? roleInternalsHtml(n) : "");
  const label = `${DEP_ICON(n)} <code>${esc(n.path)}</code> ${meta || ""}
    ${n.type === "role" && n.files ? `<span class="ci-meta">${n.files.length} file(s)</span>` : ""}
    ${n.used ? "" : '<span class="chip chip-red">unused</span>'}`;
  return kids
    ? `<details class="dep-node" open><summary>${label}</summary><div class="dep-kids">${kids}</div></details>`
    : `<div class="dep-leaf">${label}</div>`;
}

// the full-instance dependency matrix rows (used vs unused), filtered
function depMatrixRows(d, query) {
  const q = (query || "").toLowerCase();
  return d.nodes
    .filter((n) => !q || n.path.toLowerCase().includes(q) || n.type.includes(q)
                 || (q === "unused" && !n.used) || (q === "used" && n.used))
    .sort((a, b) => (a.used === b.used ? a.path.localeCompare(b.path) : a.used ? 1 : -1))
    .slice(0, 300)
    .map((n) => `
      <div class="ci-row">
        <span>${DEP_ICON(n)}</span>
        <code class="ci-job">${esc(n.path)}</code>
        <span class="chip">${esc(n.type)}</span>
        <span class="ci-meta">→ ${n.out.length} · ← ${n.in_count}</span>
        <span class="chip ${n.used ? "chip-green" : "chip-red"}">${n.used ? "used" : "unused"}</span>
      </div>`).join("") || `<div class="empty">no matches</div>`;
}

// Dependency analysis panel — embedded in the Repositories page (Engine only).
// Returns the inner HTML; call wireDepPanel(d) after inserting it.
function depPanelHtml(d) {
  const map = {};
  d.nodes.forEach((n) => { map[n.id] = n; });
  if (!d.roots.some((r) => r === state.depRoot)) state.depRoot = d.roots[0];

  const tiles = ["pipeline", "playbook", "role", "script", "caller"].map((t) => {
    const s = d.stats[t] || { total: 0, used: 0 };
    if (t === "pipeline" && d.jenkins && d.jenkins.available) {
      const nw = (d.jenkins.not_wired || []).length;
      return `<div class="stat-tile"><b class="${nw ? "pct-warn" : "pct-good"}">${d.jenkins.wired}/${s.total}</b>
        <span>pipelines wired to Jenkins</span>${nw ? `<small class="pct-bad">${nw} not in Jenkins</small>` : ""}</div>`;
    }
    const unusedN = s.total - s.used;
    return `<div class="stat-tile"><b class="${unusedN ? "pct-warn" : "pct-good"}">${s.used}/${s.total}</b>
      <span>${t}s used</span>${unusedN ? `<small class="pct-bad">${unusedN} unused</small>` : ""}</div>`;
  }).join("");

  const jenkinsChip = (n) => {
    if (!d.jenkins || !d.jenkins.available || n.type !== "pipeline") return "";
    return n.jenkins_jobs && n.jenkins_jobs.length
      ? `<span class="chip chip-green" title="${esc(n.jenkins_jobs.join(", "))}">⚙ ${n.jenkins_jobs.length} job(s)</span>`
      : `<span class="chip chip-red" title="no Jenkins job's scriptPath points at this file">not in Jenkins</span>`;
  };
  const rootList = d.roots.map((rid) => `
    <div class="hist-row ${rid === state.depRoot ? "open" : ""}" data-dep-root="${esc(rid)}">
      <span class="hist-subject">⚙ ${esc(map[rid].path.replace(/^pipelines\//, ""))}</span>
      ${jenkinsChip(map[rid])}
      <span class="ci-meta">${(map[rid].out || []).length} direct deps</span>
    </div>`).join("") || `<div class="empty">no pipelines found under pipelines/</div>`;

  const unusedList = ["script", "playbook", "role"].flatMap((t) =>
    (d.unused[t] || []).map((p) => `
      <div class="ci-row"><span class="chip chip-red">${t}</span>
        <code class="ci-job">${esc(p)}</code></div>`))
    .concat((d.orphan_task_files || []).map((o) => `
      <div class="ci-row"><span class="chip chip-red">role task</span>
        <code class="ci-job">${esc(o.file)}</code>
        <span class="ci-meta" title="${esc(o.role)}">not included from main.yml</span></div>`))
    .join("")
    || `<div class="empty">✅ everything is reachable from the pipelines</div>`;

  const envFlags = (d.env_flags || []).map((f) => `
    <div class="ci-row"><span class="chip chip-red">env ${esc(f.env)}</span>
      <code class="ci-job" title="${esc(f.raw)}">${esc(f.src.replace(/^role:/, ""))}</code>
      ${f.target ? `<span class="ci-meta">→ ${esc(f.target.replace(/^role:/, ""))}</span>` : ""}
    </div>`).join("");
  const envPanel = envFlags ? `
    <h2 class="panel-divider">🚩 non-prd caller invocations — should be prd</h2>
    ${envFlags}` : "";

  const jk = d.jenkins || { available: false };
  const jenkinsPanel = !jk.available
    ? `<div class="kpi-note">ℹ Jenkins cross-reference unavailable — configure Jenkins to see which pipeline files are wired to real jobs</div>`
    : `
      ${(jk.not_wired || []).length ? `
        <h2 class="panel-divider">⚠ pipeline files NO Jenkins job uses</h2>
        ${jk.not_wired.map((p) => `<div class="ci-row"><span class="chip chip-red">not wired</span><code class="ci-job">${esc(p)}</code></div>`).join("")}` : ""}
      ${(jk.missing || []).length ? `
        <h2 class="panel-divider">⚠ Jenkins jobs pointing at files MISSING from this repo</h2>
        ${jk.missing.map((m) => `<div class="ci-row"><span class="chip chip-amber">missing</span>
          <code class="ci-job">${esc(m.path)}</code>
          <span class="ci-meta" title="${esc(m.jobs.join(", "))}">${m.jobs.length} job(s)</span></div>`).join("")}` : ""}
      ${!(jk.not_wired || []).length && !(jk.missing || []).length
        ? `<div class="empty" style="padding:8px">✅ every pipeline file maps to a Jenkins job and vice versa</div>` : ""}`;

  const notes = [
    d.truncated ? `⚠ scanned the first ${d.files_scanned} files only` : "",
    d.ambiguous.length ? `⚠ ${d.ambiguous.length} ambiguous reference(s) — same filename in several places (all candidates linked)` : "",
    d.dynamic.length ? `ℹ ${d.dynamic.length} dynamic call(s) (variable arguments) could not be resolved statically` : "",
  ].filter(Boolean).map((n) => `<div class="kpi-note">${n}</div>`).join("");

  return `
    <div class="deps-embed-head">
      <h2 style="margin:0">⛓ dependencies <span class="ci-meta">pipelines → playbooks / roles / scripts · used vs unused</span></h2>
      <span class="spacer"></span>
      <button class="btn btn-sm" id="dep-refresh">↻ re-analyze</button></div>
    ${notes}
    <div class="stat-tiles">${tiles}</div>
    <div class="ci-grid">
      <div class="panel">
        <h2>⚙ pipelines — pick one to trace</h2>
        <div class="ci-scroll">${rootList}</div>
        <h2 class="panel-divider">⛓ dependency tree — <span id="dep-tree-title">${esc(state.depRoot ? map[state.depRoot].path : "")}</span></h2>
        <div class="dep-tree" id="dep-tree-box">${state.depRoot ? depTree(map, state.depRoot, new Set()) : ""}</div>
      </div>
      <div>
        <div class="panel" style="margin-bottom:18px">
          <h2>🗑 unused files — candidates for cleanup</h2>
          <div class="ci-scroll">${unusedList}</div>
          ${envPanel}
          ${jenkinsPanel}</div>
        <div class="panel"><h2>🔎 full matrix — ${d.nodes.length} nodes
          <span class="ov-more">${d.cached ? "cached · " : ""}${d.files_scanned} files scanned</span></h2>
          <div class="repo-bar" style="margin-bottom:8px">
            <input id="dep-search" placeholder="filter by path / type / used / unused" value="${esc(state.depQuery || "")}" style="flex:1">
          </div>
          <div class="ci-scroll" style="max-height:420px" id="dep-matrix-rows">${depMatrixRows(d, state.depQuery)}</div></div>
      </div>
    </div>`;
}

function wireDepPanel(d) {
  const map = {};
  d.nodes.forEach((n) => { map[n.id] = n; });
  // in-place interactions — no full re-render, no flash, no focus loss
  view().querySelectorAll("[data-dep-root]").forEach((el) => el.onclick = () => {
    state.depRoot = el.dataset.depRoot;
    view().querySelectorAll("[data-dep-root]").forEach((row) =>
      row.classList.toggle("open", row === el));
    const title = document.getElementById("dep-tree-title");
    const box = document.getElementById("dep-tree-box");
    if (title) title.textContent = map[state.depRoot].path;
    if (box) box.innerHTML = depTree(map, state.depRoot, new Set());
  });
  const s = document.getElementById("dep-search");
  if (s) s.oninput = () => {
    state.depQuery = s.value;
    clearTimeout(state._depT);
    state._depT = setTimeout(() => {
      const rows = document.getElementById("dep-matrix-rows");
      if (rows) rows.innerHTML = depMatrixRows(d, state.depQuery);
    }, 120);
  };
}

/* ================= ACCESS MANAGEMENT ================= */
const ACC_PERM_CLS = (p) => /Administer|Manage permissions|Force push|Delete|Configure/i.test(p)
  ? "chip-red" : /Contribute|Edit|Create|Build|Transition|Resolve/i.test(p)
  ? "chip-amber" : "chip-cyan";
const permChips = (list, cls) => (list || []).map((p) =>
  `<span class="chip ${cls || ACC_PERM_CLS(p)}">${esc(p)}</span>`).join(" ");
const srcLabel = (d) => `${esc(d.source)}${d.cached ? " · cached" : ""}`;

const ACC_WHAT = {
  summary: "tallying projects, repos, teams & cross-system overlap",
  ldap: "checking the login LDAP + the [TEAM] resolver",
  ado: "querying Azure DevOps for projects",
  jira: "reading Jira permission schemes & their project assignments",
  activity: "reading per-project dates & per-user last-login/activity (JQL per row)",
  jenkins: "scanning Jenkins global + job/folder configs for matrix RBAC",
};

function accTeamSourceHtml(ts) {
  if (!ts) return "";
  const chips = [
    ["Engine cloned", ts.engine_cloned],
    ["getTeamMembersCN.sh", ts.script_present],
    [".prd profile", ts.prd_present],
  ].map(([label, ok]) =>
    `<span class="chip ${ok ? "chip-green" : "chip-red"}">${ok ? "✓" : "✗"} ${esc(label)}</span>`
  ).join(" ");
  return `
    <div class="acc-subhead">[TEAM] member resolver</div>
    <div class="ci-row">
      <span class="ci-dot ${ts.healthy ? "dot-green" : "dot-red"}"></span>
      <code class="ci-job">${esc(ts.script || "getTeamMembersCN.sh")}</code>
      ${chips}
      <span class="ci-meta">${esc(ts.note || "")}</span>
    </div>`;
}

// live test of the [TEAM] resolver: type a team, run getTeamMembersCN.sh, see it
const accProbeHtml = () => `
  <div class="ldap-probe">
    <input id="ldap-probe-team" placeholder="team name, e.g. Digital_Innovation" />
    <button class="btn btn-sm" id="ldap-probe-run">▶ Test resolver</button>
    <div id="ldap-probe-out" class="ldap-probe-out"></div>
  </div>`;

function accProbeResultHtml(r) {
  const ok = r.ran && r.returncode === 0 && r.parsed_count > 0;
  const dot = r.ran && r.returncode === 0
    ? (r.parsed_count > 0 ? "chip-green" : "chip-amber") : "chip-red";
  const head = !r.ran
    ? `<span class="chip chip-red">✗ ${esc(r.note)}</span>`
    : `<span class="chip ${dot}">${ok ? "✓" : r.returncode === 0 ? "!" : "✗"} exit ${r.returncode}</span>
       <span class="chip">${r.parsed_count} member${r.parsed_count === 1 ? "" : "s"} parsed</span>
       ${r.duration_ms != null ? `<span class="ci-meta">${r.duration_ms} ms</span>` : ""}
       ${r.demo ? '<span class="chip chip-cyan">demo</span>' : ""}`;
  const note = r.note && r.ran ? `<div class="ci-meta">${esc(r.note)}</div>` : "";
  const members = (r.members || []).length
    ? `<div class="acc-members">${r.members.map((m) =>
        `<span class="chip" title="${esc(m.username)}">${esc(m.display_name || m.username)}</span>`).join(" ")}</div>`
    : "";
  const block = (label, txt) => txt
    ? `<div class="probe-io"><div class="ci-meta">${label}</div><pre>${esc(txt)}</pre></div>` : "";
  return `<div class="probe-head">${head}</div>${note}${members}
    ${block("raw stdout", r.stdout)}${block("raw stderr", r.stderr)}`;
}

function accLdapHtml(d) {
  const teamSrc = accTeamSourceHtml(d.team_source);
  const probe = accProbeHtml();
  if (!(d.servers || []).length)
    return `<div class="empty">${esc(d.note || "no login LDAP configured")}</div>${teamSrc}${probe}`;
  const servers = d.servers.map((s) => `
    <div class="ci-row">
      <span class="ci-dot ${s.healthy ? "dot-green" : "dot-red"}"></span>
      <code class="ci-job">${esc(s.url)}</code>
      <span class="chip chip-cyan">login</span>
      <span class="chip ${s.healthy ? "chip-green" : "chip-red"}">${s.healthy ? "✓ reachable" : "✗ " + esc(s.note)}</span>
      ${s.healthy ? `<span class="ci-meta">${esc(s.note)}</span>` : ""}
    </div>`).join("");
  return `<div class="acc-subhead">login directory</div>${servers}${teamSrc}${probe}`;
}

async function accLoad(section, url, renderFn) {
  const box = document.getElementById(`acc-${section}`);
  if (!box) return;
  const tok = navToken();
  const t0 = Date.now();
  box.innerHTML = `<div class="empty acc-loading">⏳ ${esc(ACC_WHAT[section])}…
    <span class="acc-elapsed"></span></div>`;
  // elapsed nudge so a slow source visibly explains itself, never a dead spinner
  const timer = setInterval(() => {
    const el = box.querySelector(".acc-elapsed");
    if (!el) return clearInterval(timer);
    const s = Math.round((Date.now() - t0) / 1000);
    el.textContent = s >= 3 ? `(${s}s — large instances can take a moment)` : "";
  }, 1000);
  try {
    const d = await api(url);
    clearInterval(timer);
    if (navStale(tok)) return;  // navigated away — don't paint a detached box
    box.innerHTML = renderFn(d);
  } catch (e) {
    clearInterval(timer);
    if (navStale(tok)) return;
    box.innerHTML = `<div class="empty">⚠ couldn't load: ${esc(e.message)}
      <button class="btn btn-sm" data-acc-retry="${section}">↻ retry</button></div>`;
  }
  wireAccess(section);
  const rb = box.querySelector(`[data-acc-retry="${section}"]`);
  if (rb) rb.onclick = () => accLoad(section, url, renderFn);
}

// Jira: permission schemes + activity/last-seen in one unified panel
async function loadJira(refresh) {
  const box = document.getElementById("acc-jira");
  if (!box) return;
  const tok = navToken();
  box.innerHTML = `<div class="empty acc-loading">⏳ ${esc(ACC_WHAT.jira)}…</div>`;
  const s = refresh ? "?refresh=true" : "";
  try {
    const [schemes, activity] = await Promise.all([
      api(`/api/access/jira${s}`),
      api(`/api/access/jira/activity${s}`).catch(() => null),  // activity is optional
    ]);
    if (navStale(tok)) return;
    box.innerHTML = accJiraHtml(schemes, activity);
  } catch (e) {
    if (navStale(tok)) return;
    box.innerHTML = `<div class="empty">⚠ couldn't load: ${esc(e.message)}
      <button class="btn btn-sm" id="acc-jira-retry">↻ retry</button></div>`;
    const rb = document.getElementById("acc-jira-retry");
    if (rb) rb.onclick = () => loadJira(refresh);
  }
}

const extLink = (url) => url && !url.startsWith("#")
  ? `<a class="acc-ext" href="${esc(url)}" target="_blank" rel="noopener" title="open">↗</a>` : "";

const miniBar = (pct, cls) => `<span class="mini-bar"><span class="${cls || ""}" style="width:${Math.min(pct, 100)}%"></span></span>`;

// high-level governance stats shown when a collection is expanded
function collStatsPanel(s) {
  const pct = (n, d) => d ? Math.round(n / d * 100) : 0;
  const scored = (s.uniform_projects || 0) + (s.repo_specific_projects || 0);
  const uniPct = pct(s.uniform_projects || 0, scored);
  const teamDef = s.team_defined_projects || 0;
  const wholePct = pct(s.whole_team_projects || 0, teamDef);
  const unassigned = s.unassigned_projects || 0;
  const healthyPct = pct(s.unassigned_healthy || 0, unassigned);
  const prScored = s.pr_scored_projects || 0;
  const prDefinedPct = pct(s.pr_defined_projects || 0, prScored);
  const bar = (label, pctVal, sub, goodCls) => `
    <div class="cstat">
      <div class="cstat-top"><span>${label}</span><b class="${goodCls}">${pctVal}%</b></div>
      <div class="cstat-bar"><div class="${goodCls}" style="width:${pctVal}%"></div></div>
      <div class="ci-meta">${sub}</div>
    </div>`;
  const cls = (p) => p >= 80 ? "pct-good" : p >= 50 ? "pct-warn" : "pct-bad";
  return `
    <div class="coll-stats">
      ${scored ? bar("uniform vs repo-level access", uniPct,
          `${s.uniform_projects} uniform · ${s.repo_specific_projects} repo-specific`, cls(uniPct)) : ""}
      ${teamDef ? bar("whole-team granted vs per-member", wholePct,
          `${s.whole_team_projects} whole-team · ${s.per_member_projects} per-member (of ${teamDef} team-defined)`, cls(wholePct)) : ""}
      ${unassigned ? bar("healthy unassigned vs unhealthy", healthyPct,
          `${s.unassigned_healthy} healthy · ${s.unassigned_unhealthy} unhealthy (of ${unassigned} unassigned)`, cls(healthyPct)) : ""}
      ${prScored ? bar("PR reviewers defined", prDefinedPct,
          `${s.pr_project_level || 0} project-level · ${s.pr_repo_level || 0} repo-level · ${s.pr_missing_projects || 0} missing (of ${prScored})`, cls(prDefinedPct)) : ""}
      <div class="cstat">
        <div class="cstat-top"><span>projects with out-of-team members</span>
          <b class="${(s.extra_member_projects || 0) ? "pct-bad" : "pct-good"}">${s.extra_member_projects || 0}</b></div>
        <span class="ci-meta">granted members not in the [TEAM] group${(s.ldap_failed_projects || 0) ? ` · ${s.ldap_failed_projects} team(s) failed LDAP` : ""}</span>
      </div>
      <div class="cstat">
        <div class="cstat-top"><span>projects with duplicate access</span>
          <b class="${(s.duplicate_grant_projects || 0) ? "pct-warn" : "pct-good"}">${s.duplicate_grant_projects || 0}</b></div>
        <span class="ci-meta">whole team granted + members also granted individually (redundant)</span>
      </div>
    </div>`;
}
const gradeCls = (g) => ({ A: "grade-a", B: "grade-a", C: "grade-c", D: "grade-f", F: "grade-f" }[g] || "grade-x");
const scoreBadge = (score, grade) => score == null
  ? `<span class="score-badge grade-x" title="not scored (repo cap reached — refresh or expand)">?</span>`
  : `<span class="score-badge ${gradeCls(grade)}" title="access-hygiene score">${grade} · ${score}</span>`;

// filter predicate for one project against the ADO smart-filter state
function adoMatch(p, f, dupNames) {
  if (f.q && !(p.name || "").toLowerCase().includes(f.q.toLowerCase())) return false;
  if (f.grade && f.grade !== "all") {
    if (f.grade === "unscored") { if (p.grade && p.grade !== "?") return false; }
    else if ((p.grade || "?") !== f.grade) return false;
  }
  if (f.pr === "with" && !p.pr_present) return false;
  if (f.pr === "without" && p.pr_present) return false;
  const assigned = p.team && !p.team_unassigned;
  if (f.team === "whole" && !(assigned && p.team_group_granted)) return false;
  if (f.team === "notwhole" && !(assigned && p.team_group_granted === false)) return false;
  if (f.team === "ldapfail" && !(assigned && p.team_ldap_resolved === false)) return false;
  if (f.team === "dupaccess" && !((p.team_duplicate_count || 0) > 0)) return false;
  if (f.outteam === "yes" && !((p.team_non_member_count || 0) > 0)) return false;
  if (f.outteam === "no" && ((p.team_non_member_count || 0) > 0)) return false;
  if (f.unassigned === "correct" && !(p.team_unassigned && p.team_ok)) return false;
  if (f.unassigned === "incorrect" && !(p.team_unassigned && !p.team_ok)) return false;
  if (f.unassigned === "assigned" && p.team_unassigned) return false;
  const isDup = !!dupNames[(p.name || "").toLowerCase()];
  if (f.dup === "yes" && !isDup) return false;
  if (f.dup === "no" && isDup) return false;
  if (f.minrepos && (p.repos || 0) < Number(f.minrepos)) return false;
  return true;
}
const ADO_FILTER_ACTIVE = (f) => f && (f.q || (f.grade && f.grade !== "all")
  || (f.pr && f.pr !== "all") || (f.team && f.team !== "all") || (f.outteam && f.outteam !== "all")
  || (f.unassigned && f.unassigned !== "all") || (f.dup && f.dup !== "all")
  || f.minrepos || f.minprojects || (f.sort && f.sort !== "name"));

function accAdoHtml(d) {
  if (!d.projects.length) return `<div class="empty">no projects (${srcLabel(d)})</div>`;
  state.adoData = d;                       // kept so filters re-render without refetch
  const f = state.adoFilter = state.adoFilter || {};
  const stats = {};
  (d.collection_stats || []).forEach((s) => { stats[s.name] = s; });
  // project NAMES that appear in more than one collection
  const nameColls = {};
  d.projects.forEach((p) => {
    const k = (p.name || "").toLowerCase();
    (nameColls[k] = nameColls[k] || new Set()).add(p.coll);
  });
  const dupNames = {};
  Object.entries(nameColls).forEach(([k, set]) => { if (set.size > 1) dupNames[k] = [...set].sort(); });
  const dupCount = Object.keys(dupNames).length;
  const dupChip = (p) => {
    const others = (dupNames[(p.name || "").toLowerCase()] || []).filter((c) => c !== p.coll);
    return others.length
      ? `<span class="chip chip-violet acc-dup" title="same project name also in: ${others.map(esc).join(", ")}">⧉ also in ${others.length} other collection${others.length > 1 ? "s" : ""}</span>`
      : "";
  };
  // apply the smart filters, then group + sort the surviving projects
  const filtering = ADO_FILTER_ACTIVE(f);
  const shown = d.projects.filter((p) => adoMatch(p, f, dupNames));
  const SORT = {
    "score-asc": (a, b) => (a.score ?? 999) - (b.score ?? 999),
    "score-desc": (a, b) => (b.score ?? -1) - (a.score ?? -1),
    "repos-desc": (a, b) => (b.repos || 0) - (a.repos || 0),
    "name": (a, b) => a.name.localeCompare(b.name),
  };
  const byColl = {};
  shown.forEach((p) => { (byColl[p.coll] = byColl[p.coll] || []).push(p); });
  Object.values(byColl).forEach((arr) => arr.sort(SORT[f.sort] || SORT.name));
  let colls = Object.keys(byColl).sort();
  if (f.minprojects) colls = colls.filter((c) => byColl[c].length >= Number(f.minprojects));
  const shownCount = colls.reduce((n, c) => n + byColl[c].length, 0);
  const totRepos = d.projects.reduce((n, p) => n + (p.repos || 0), 0);
  const capNote = (d.total_repos && d.scored_repos < d.total_repos)
    ? ` · scored ${d.scored_repos}/${d.total_repos} repos (cap)` : "";
  const failed = d.ldap_failed_teams || [];
  const failProjects = failed.reduce((n, x) => n + (x.count || 0), 0);
  const failBanner = failed.length ? `
    <div class="remote-banner remote-new" style="margin-bottom:10px">
      <b>⚠ ${failed.length} LDAP group(s) not found — ${failProjects} project(s) affected (team-not-set, −15 each)</b>
      ${failed.map((x) => `<div class="ci-meta" style="margin-top:3px">• group <b>[${esc(x.team)}]</b> not in LDAP — used by ${x.count} project(s):
        ${x.projects.map((p) => `${esc(p.project)} <span class="ci-meta">(${esc(p.coll)})</span>`).join(", ")}</div>`).join("")}
    </div>` : "";

  // duplicated REPOSITORY names across the whole instance
  const dupRepos = d.duplicate_repos || [];
  const dupRepoPanel = dupRepos.length ? `
    <details class="filebox acc-duprepo" ${filtering ? "" : ""}>
      <summary>⧉ <b>${d.duplicate_repo_count || dupRepos.length}</b> repository name(s) shared across projects/collections</summary>
      <div style="padding:6px 12px">
        ${dupRepos.map((r) => `<div class="acc-duprepo-row">
          <code>${esc(r.name)}</code> <span class="chip chip-violet">×${r.count}</span>
          <span class="ci-meta">${r.locations.map((l) => `${esc(l.project)} <span class="acc-dup-note">(${esc(l.coll)})</span>`).join(" · ")}</span>
        </div>`).join("")}
      </div>
    </details>` : "";

  // smart filter bar
  const sel = (id, cur, opts) => `<select data-ado-filter="${id}">${opts.map(([v, label]) =>
    `<option value="${v}" ${(cur || "all") === v ? "selected" : ""}>${label}</option>`).join("")}</select>`;
  const filterBar = `
    <div class="acc-filters">
      <input id="ado-q" placeholder="🔎 project name…" value="${esc(f.q || "")}">
      ${sel("grade", f.grade, [["all", "any grade"], ["A", "A"], ["B", "B"], ["C", "C"], ["D", "D"], ["F", "F"], ["unscored", "unscored"]])}
      ${sel("pr", f.pr, [["all", "PR: any"], ["with", "with PR"], ["without", "without PR"]])}
      ${sel("team", f.team, [["all", "team: any"], ["whole", "whole-team granted"], ["notwhole", "not whole-team"], ["dupaccess", "duplicate access"], ["ldapfail", "LDAP failed"]])}
      ${sel("outteam", f.outteam, [["all", "out-of-team: any"], ["yes", "has out-of-team"], ["no", "none out-of-team"]])}
      ${sel("unassigned", f.unassigned, [["all", "assign: any"], ["assigned", "assigned"], ["correct", "unassigned ✓"], ["incorrect", "unassigned ✗"]])}
      ${sel("dup", f.dup, [["all", "name: any"], ["yes", "shared name"], ["no", "unique name"]])}
      <input class="acc-filter-num" type="number" min="0" data-ado-filter="minrepos" placeholder="min repos" value="${esc(f.minrepos || "")}">
      <input class="acc-filter-num" type="number" min="0" data-ado-filter="minprojects" placeholder="min proj/coll" value="${esc(f.minprojects || "")}">
      ${sel("sort", f.sort || "name", [["name", "sort: name"], ["score-asc", "score ↑"], ["score-desc", "score ↓"], ["repos-desc", "repos ↓"]])}
      ${filtering ? `<button class="btn btn-sm" id="ado-filter-clear">✕ clear</button>` : ""}
    </div>`;

  const summaryLine = `<div class="ci-meta" style="margin-bottom:8px">${srcLabel(d)} · ${filtering ? `<b>${shownCount}</b> of ` : ""}${d.projects.length} project(s) · ${totRepos} repo(s) across ${colls.length}${filtering ? "" : ""} collection(s)${capNote}${dupCount ? ` · <span class="acc-dup-note">⧉ ${dupCount} name(s) shared across collections</span>` : ""} — score = access hygiene (A best)${filtering ? " · filtered" : "; collections collapsed, click to expand"}</div>`;

  const body = colls.length ? colls.map((c) => {
      const s = stats[c] || { projects: byColl[c].length, teams: 0, repos: 0 };
      return `
      <details class="filebox acc-coll-det" ${filtering ? "open" : ""}>
        <summary>🗄 <b>${esc(c)}</b> ${scoreBadge(s.score, s.grade)}
          <span class="acc-coll-stats">
            <span class="chip chip-cyan">${filtering ? `${byColl[c].length} of ${s.projects}` : s.projects} projects</span>
            <span class="chip chip-green" title="all repos share one ACL set">${s.uniform_projects || 0} uniform</span>
            <span class="chip chip-amber" title="repos have their own ACLs">${s.repo_specific_projects || 0} repo-specific</span>
            <span class="chip" title="distinct members across the collection">${s.members ?? 0} members</span>
            ${(s.pr_defined_projects || 0) || (s.pr_scored_projects || 0)
              ? `<span class="chip ${s.pr_missing_projects ? "chip-amber" : "chip-green"}" title="projects defining a PR-reviewer group">🔀 ${s.pr_defined_projects || 0}/${s.pr_scored_projects || 0} w/ PR</span>` : ""}
            <span class="chip">${s.teams} teams</span>
            <span class="chip">${s.repos} repos</span>
            ${(() => { const n = byColl[c].filter((p) => dupNames[(p.name || "").toLowerCase()]).length;
              return n ? `<span class="chip chip-violet" title="project names in this collection that also exist in another collection">⧉ ${n} shared name(s)</span>` : ""; })()}
          </span></summary>
        <div class="acc-coll-body">
          ${collStatsPanel(s)}
          ${byColl[c].map((p) => `
            <details class="filebox acc-proj${dupNames[(p.name || "").toLowerCase()] ? " acc-proj-dup" : ""}" data-acc-coll="${esc(p.coll)}" data-acc-proj="${esc(p.id)}">
              <summary>📁 <b>${esc(p.name)}</b> ${dupChip(p)} ${scoreBadge(p.score, p.grade)} ${extLink(p.url)}
                <span class="acc-proj-stats">
                  ${p.uniform === true ? '<span class="chip chip-green">uniform</span>'
                    : p.uniform === false ? `<span class="chip chip-amber">repo-specific ${p.pct_repo_specific}%</span>` : ""}
                  ${p.team ? (p.team_unassigned
                    ? (p.team_ok
                        ? `<span class="chip chip-green" title="unassigned project with no access — healthy">✓ unassigned (no access)</span>`
                        : `<span class="chip chip-red" title="unassigned project but ${p.team_non_member_count} identit(y/ies) have access">⚠ unassigned — ${p.team_non_member_count} with access</span>`)
                    : p.team_ok
                    ? `<span class="chip chip-green" title="[${esc(p.team)}] fully granted, no out-of-team access">✓ team [${esc(p.team)}]</span>`
                    : p.team_ldap_resolved === false
                      ? `<span class="chip chip-red" title="LDAP group [${esc(p.team)}] not found — team not set (-15)">⚠ team [${esc(p.team)}] LDAP?</span>`
                      : `<span class="chip chip-red" title="${p.team_group_granted === false ? "team group not granted" : ""}${p.team_non_member_count ? p.team_non_member_count + " out-of-team grant(s)" : ""}">⚠ team [${esc(p.team)}]${p.team_non_member_count ? " +" + p.team_non_member_count : ""}</span>`) : ""}
                  ${(p.team_duplicate_count || 0) > 0
                    ? `<span class="chip chip-amber" title="whole team granted, yet ${p.team_duplicate_count} member(s) also hold an individual grant — redundant">♻ ${p.team_duplicate_count} duplicate</span>`
                    : ""}
                  ${p.pr_present
                    ? `<span class="chip ${p.pr_scope === "project" ? "chip-cyan" : "chip-amber"}" title="PR reviewers (${p.pr_scope}-level)${(p.pr_groups || []).length ? ": " + p.pr_groups.map((g) => g.name + (g.members != null ? " (" + g.members + ")" : "")).join(", ") : ""}">🔀 PR ${p.pr_member_count ?? 0} · ${p.pr_scope === "project" ? "project" : "repo"}</span>`
                    : ""}
                  ${(p.members ?? 0) === 0
                    ? (p.team_unassigned
                        ? '<span class="chip chip-green" title="no members — expected for an unassigned project">0 members</span>'
                        : '<span class="chip chip-red" title="no members have access to this project">⚠ 0 members</span>')
                    : `<span class="chip" title="distinct members with access">${p.members} members</span>`}
                  <span class="chip">${p.teams || 0} teams</span>
                  <span class="chip">${p.repos || 0} repos</span>
                </span></summary>
              <div class="acc-proj-body" id="acc-proj-${esc(p.coll)}-${esc(p.id)}"><div class="empty">loading…</div></div>
            </details>`).join("")}
        </div>
      </details>`;
    }).join("")
    : `<div class="empty">no projects match the filters — <a href="javascript:void 0" id="ado-filter-clear2">clear filters</a></div>`;

  return failBanner + dupRepoPanel + filterBar + summaryLine + body;
}

const TIER_CLS = { admin: "chip-red", write: "chip-amber", read: "chip-cyan", other: "" };

function accAdoProjectHtml(d) {
  const an = d.analysis || {};
  // ---- access summary + the many percentages ----
  const tp = an.tier_pct || {};
  const uniformBadge = an.total_repos
    ? (an.uniform
        ? '<span class="chip chip-green">✓ uniform access</span>'
        : `<span class="chip chip-amber">repo-specific access</span>`)
    : "";
  const tv = an.team_validation;
  const teamHealthy = tv && (tv.unassigned ? tv.non_team_count === 0
    : tv.ldap_resolved && tv.group_granted && !tv.non_team_count);
  const teamPanel = tv ? (tv.unassigned ? `
    <div class="acc-team ${teamHealthy ? "team-ok" : "team-bad"}">
      <b>🚫 [UnAssigned] project</b>
      <span class="chip ${teamHealthy ? "chip-green" : "chip-red"}">${teamHealthy ? "✓ healthy — no identities have access" : "✗ " + tv.non_team_count + " identit(y/ies) have access (should be none)"}</span>
      ${(tv.non_team_grants || []).length ? `<div class="ci-meta" style="flex-basis:100%;margin-top:4px">with access: ${tv.non_team_grants.map(esc).join(", ")}</div>` : ""}
    </div>` : `
    <details class="acc-team ${teamHealthy ? "team-ok" : "team-bad"}">
      <summary><b>👥 team [${esc(tv.team)}]</b>
      ${!tv.ldap_resolved ? '<span class="chip chip-red">✗ LDAP group not found — team not set (−15)</span>' : `
        <span class="chip">${tv.member_count} LDAP member(s)</span>
        <span class="chip ${tv.group_granted ? "chip-green" : "chip-red"}">${tv.group_granted ? "✓ whole team granted" : "✗ team group NOT granted"}</span>
        <span class="chip ${tv.non_team_count ? "chip-red" : "chip-green"}">${tv.non_team_count ? tv.non_team_count + " granted but NOT in team" : "✓ all " + (tv.granted_people || 0) + " grantee(s) in team"}</span>
        ${(tv.duplicate_count || 0) > 0 ? `<span class="chip chip-amber" title="already covered by the whole-team grant">♻ ${tv.duplicate_count} redundant individual grant(s)</span>` : ""}`}
      <span class="ci-meta"> · click to see members</span></summary>
      <div style="padding:6px 4px">
        ${(tv.duplicate_count || 0) > 0 ? `<div class="acc-h" style="color:var(--amber)">♻ duplicate — whole team granted, yet these members ALSO have an individual grant (${tv.duplicate_count})</div>
          <div class="acc-members">${(tv.duplicate_grants || []).map((m) => `<span class="chip chip-amber">${esc(m)}</span>`).join(" ")}</div>` : ""}
        ${(tv.non_team_grants || []).length ? `<div class="acc-h" style="color:var(--red);margin-top:8px">⚠ granted but NOT in [${esc(tv.team)}] (${tv.non_team_count})</div>
          <div class="acc-members">${tv.non_team_grants.map((m) => `<span class="chip chip-red">${esc(m)}</span>`).join(" ")}</div>` : ""}
        <div class="acc-h" style="margin-top:8px">LDAP members of [${esc(tv.team)}] (${tv.member_count})</div>
        ${(tv.ldap_members || []).length
          ? `<div class="acc-members">${tv.ldap_members.map((m) => `<span class="chip">${esc(m)}</span>`).join(" ")}</div>`
          : '<div class="ci-meta">none / LDAP not resolved</div>'}
      </div>
    </details>`) : "";
  const pr = an.pr_groups || [];
  const prPanel = an.total_repos ? (pr.length ? `
    <div class="acc-pr">
      <b>🔀 PR reviewers</b>
      ${pr.map((g) => `<span class="chip ${g.scope === "project" ? "chip-cyan" : "chip-amber"}" title="${g.scope === "project" ? "granted project-wide (team or project-level ACL)" : "granted on specific repositories"}">${esc(g.name)} · ${g.members != null ? g.members + " member(s)" : "size ?"} · ${g.scope}-level</span>`).join(" ")}
    </div>` : `
    <div class="acc-pr acc-pr-none"><b>🔀 PR reviewers</b>
      <span class="chip">none detected (no PR / PR Approvers group)</span></div>`) : "";
  const analysisPanel = an.total_repos ? `
    <div class="acc-score-line">${scoreBadge(an.score, an.grade)}
      <span class="ci-meta">access-hygiene score — uniform access, low repo-specific sprawl, low admin concentration &amp; valid [TEAM] access score higher</span></div>
    ${teamPanel}${prPanel}` : "";
  const restPanel = an.total_repos ? `
    <div class="acc-analysis">
      <div class="stat-tile"><b>${an.members}</b><span>members</span></div>
      <div class="stat-tile"><b>${an.teams}</b><span>teams</span></div>
      <div class="stat-tile"><b>${an.total_repos}</b><span>repos</span></div>
      <div class="stat-tile"><b class="${an.pct_repo_specific ? "pct-warn" : "pct-good"}">${an.pct_repo_specific}%</b>
        <span>repos with their OWN ACLs</span><small>${an.repos_with_explicit}/${an.total_repos} · ${an.distinct_acl_sets} distinct set(s)</small></div>
      <div class="stat-tile"><b class="${an.pct_admin ? "pct-bad" : "pct-good"}">${an.pct_admin}%</b>
        <span>identities with admin</span><small>${(an.tier_counts||{}).admin||0}/${an.distinct_identities}</small></div>
    </div>
    <div class="acc-tiers">
      ${uniformBadge}
      <span class="acc-tier-bar">
        <span class="chip-red" style="width:${tp.admin||0}%" title="admin ${tp.admin||0}%"></span>
        <span class="chip-amber" style="width:${tp.write||0}%" title="write ${tp.write||0}%"></span>
        <span class="chip-cyan" style="width:${tp.read||0}%" title="read ${tp.read||0}%"></span>
        <span class="tier-other" style="width:${tp.other||0}%" title="other ${tp.other||0}%"></span>
      </span>
      <span class="ci-meta">of ${an.distinct_identities} identities: ${tp.admin||0}% admin · ${tp.write||0}% write · ${tp.read||0}% read</span>
    </div>` : "";

  const teams = (d.teams || []).map((t) => `
    <div class="ci-row"><span class="ci-job">👥 ${esc(t.name)}</span>
      <span class="ci-meta">${t.members.length} member(s)</span>
      <span class="acc-members">${t.members.slice(0, 8).map((m) => `<span class="chip">${esc(m)}</span>`).join(" ")}
      ${t.members.length > 8 ? `<span class="ci-meta">+${t.members.length - 8} more</span>` : ""}</span>
    </div>`).join("") || `<div class="empty">no teams</div>`;
  const repos = (d.repos || []).map((r) => `
    <div class="acc-repo"><div class="ci-job" style="margin-bottom:4px">⛁ ${esc(r.name)}
      ${(r.acls || []).length ? `<span class="chip chip-amber">${r.acls.length} explicit</span>` : '<span class="chip chip-green">inherited</span>'} ${extLink(r.url)}</div>
      ${(r.acls || []).map((a) => `
        <div class="acc-acl"><span class="acc-ident"><span class="chip ${TIER_CLS[a.tier] || ""}" title="privilege tier">${esc(a.tier)}</span> ${esc(a.identity)}</span>
          ${permChips(a.allow)}
          ${(a.deny || []).map((p) => `<span class="chip chip-red" style="text-decoration:line-through" title="denied">${esc(p)}</span>`).join(" ")}
        </div>`).join("") || `<div class="ci-meta" style="padding:2px 8px">no explicit ACLs (inherited from project defaults)</div>`}
    </div>`).join("") || `<div class="empty">no repositories</div>`;
  const errs = (d.errors || []).length
    ? `<div class="kpi-note" style="color:var(--red)">⚠ some ADO calls failed: ${d.errors.map(esc).join(" · ")}</div>` : "";
  return `${errs}${analysisPanel}${restPanel}
    <h4 class="acc-h">teams &amp; members</h4>${teams}
    <h4 class="acc-h">repository permissions <span class="ci-meta">(service-account &amp; excluded grants hidden)${d.repo_cap_note ? " · first 200 repos" : ""}</span></h4>${repos}`;
}

function accJiraHtml(d, act) {
  if (!d.schemes.length) return `<div class="empty">no permission schemes (${srcLabel(d)})</div>`;
  // activity lookups (last-seen per user, last-opened/interaction per project)
  const projByKey = {};
  (act && act.projects || []).forEach((p) => { projByKey[p.key] = p; });
  const userByName = {};
  (act && act.users || []).forEach((u) => {
    [u.name, u.key, u.display_name].forEach((k) => { if (k) userByName[k.toLowerCase()] = u; });
  });
  // a scheme/JIRAUSER project chip enriched with its last-opened date
  const projChip = (p) => {
    const a = projByKey[p.key];
    const lo = a && a.last_opened, li = a && a.last_interaction;
    const title = a
      ? `last opened: ${lo ? lo.key + " on " + isoDay(lo.date) : "—"} · last interaction: ${li && li.date ? isoDay(li.date) : "—"}${p.scheme ? " · via scheme: " + p.scheme : ""}`
      : (p.scheme ? "via scheme: " + p.scheme : "");
    return `<a class="chip chip-green" href="${esc(p.url)}" target="_blank" rel="noopener" title="${esc(title)}">${esc(p.key)}${lo && lo.date ? ` <span class="acc-chip-date">${esc(ago(lo.date))}</span>` : ""}</a>`;
  };
  // a user's last login / last activity, if we have it
  const userSeen = (...keys) => {
    const u = keys.map((k) => userByName[(k || "").toLowerCase()]).find(Boolean);
    if (!u) return "";
    return `<span class="ci-meta acc-seen" title="last login · last activity">🕑 ${u.last_login ? "login " + ago(u.last_login) : "login N/A"} · ${(u.last_activity || {}).date ? "active " + ago(u.last_activity.date) : "no activity"}</span>`;
  };
  const g = d.groups || {};
  // jira-users who are in NONE of the granted LDAP groups (from getTeamMembersCN.sh)
  const noLdap = new Set((d.users_no_ldap_group || []).map((n) => (n || "").toLowerCase()));
  const grantedGroups = d.granted_groups || [];
  const groupsOutside = d.groups_outside_ldap || [];
  // instance group membership, each member enriched with last-seen + LDAP flag
  const memRow = (name, cls) => {
    const flag = noLdap.has((name || "").toLowerCase())
      ? '<span class="chip chip-red" title="not a member of any granted LDAP group">no LDAP group</span>' : "";
    return `<div class="jira-mem-row"><span class="chip ${cls || ""}">${esc(name)}</span>${userSeen(name)}${flag}</div>`;
  };
  const memList = (arr, cls) => (arr || []).length
    ? `<div class="jira-mem-list">${arr.map((n) => memRow(n, cls)).join("")}</div>`
    : '<div class="ci-meta">none / not readable</div>';
  // the granted permission-scheme groups resolved to their LDAP members
  const ldapGroupList = grantedGroups.length ? `
    <div class="acc-h" style="margin-top:10px">granted groups → LDAP membership <span class="ci-meta">(via getTeamMembersCN.sh)</span></div>
    <div class="jira-mem-list">${grantedGroups.map((gg) => `
      <div class="jira-mem-row">
        <span class="chip ${gg.ldap_resolved ? "chip-cyan" : "chip-red"}" title="${gg.ldap_resolved ? "resolves to an LDAP group" : "not an LDAP group — outside scope"}">${gg.ldap_resolved ? "👥" : "⚠"} ${esc(gg.name)}</span>
        ${gg.ldap_resolved
          ? `<span class="ci-meta">${gg.member_count} member(s)${(gg.members || []).length ? ": " + gg.members.slice(0, 8).map(esc).join(", ") + (gg.members.length > 8 ? ` +${gg.members.length - 8}` : "") : ""}</span>`
          : '<span class="ci-meta">⚠ outside LDAP scope — cannot verify membership</span>'}
      </div>`).join("")}</div>` : "";
  const groupsPanel = (g.admin_group || g.users_group) ? `
    <div class="stat-tiles" style="margin-bottom:8px">
      <div class="stat-tile"><b class="${g.admins_count ? "pct-bad" : ""}">${g.admins_readable ? (g.admins_count ?? (g.admins||[]).length) : "?"}</b>
        <span>${esc(g.admin_group || "administrators")}</span></div>
      <div class="stat-tile"><b>${g.users_readable ? g.users_count : "?"}</b>
        <span>${esc(g.users_group || "jira-users")}</span></div>
    </div>
    <details class="filebox" style="margin-bottom:8px">
      <summary>👑 instance group membership &amp; last-seen</summary>
      <div style="padding:8px 12px">
        <div class="acc-h">${esc(g.admin_group || "administrators")} — ${(g.admins||[]).length} shown</div>
        ${memList(g.admins, "chip-red")}
        <div class="acc-h" style="margin-top:10px">${esc(g.users_group || "jira-users")} — ${g.users_readable ? (g.users_count + " total, " + (g.users||[]).length + " shown") : "not readable"}</div>
        ${g.users_readable ? memList(g.users, "") : `<div class="kpi-note">⚠ couldn't read ${esc(g.users_group || "jira-users")} membership — the non-member cross-check is skipped (the account needs permission to browse the group)</div>`}
        ${ldapGroupList}
      </div>
    </details>` : "";

  // warnings SUMMARY — counts only; the offenders are highlighted inline in the
  // permission schemes below, so projects/schemes are never re-listed here
  const nonMembers = d.non_member_grants || [];
  const juUsers = d.jirauser_grants || [];
  const anyWarn = nonMembers.length || juUsers.length || groupsOutside.length || noLdap.size;
  const warn = anyWarn ? `
    <div class="jira-warn">
      ${juUsers.length ? `<span class="chip chip-red">🚩 ${juUsers.length} direct JIRAUSER grantee(s)</span>` : ""}
      ${nonMembers.length ? `<span class="chip chip-red">⚠ ${nonMembers.length} assigned but NOT ${esc(g.users_group || "jira-users")}</span>` : ""}
      ${groupsOutside.length ? `<span class="chip chip-red" title="${esc(groupsOutside.join(", "))}">⚠ ${groupsOutside.length} granted group(s) OUTSIDE LDAP scope</span>` : ""}
      ${noLdap.size ? `<span class="chip chip-red">⚠ ${noLdap.size} jira-user(s) in NO LDAP group</span>` : ""}
      <span class="ci-meta">🔻 flagged inline below</span>
    </div>` : "";

  return `<div class="ci-meta" style="margin-bottom:8px">${srcLabel(d)} · ${d.schemes.length} scheme(s)${d.project_count != null ? ` · ${d.project_count} project(s) checked` : ""}${d.projects_truncated ? " (truncated)" : ""}${act ? " · 🕑 dates &amp; last-seen from Jira activity" : ""}</div>`
    + groupsPanel + warn
    + d.schemes.map((s) => `
      <details class="filebox">
        <summary>🎫 <b>${esc(s.name)}</b> ${extLink(s.url)}
          ${(s.projects || []).length
            ? s.projects.slice(0, 12).map(projChip).join(" ")
              + (s.projects.length > 12 ? `<span class="ci-meta">+${s.projects.length - 12} more</span>` : "")
            : '<span class="chip">unassigned</span>'}
          <span class="ci-meta">${esc(s.description || "")}</span></summary>
        <div style="padding:8px 12px">
          ${s.holders.map((h) => {
            const groupOutside = h.type === "group" && h.ldap_resolved === false;
            const warnRow = h.flag || h.not_member || groupOutside;
            return `<div class="acc-acl ${warnRow ? "acc-acl-warn" : ""}"><span class="acc-ident ${warnRow ? "acc-flag" : ""}">${h.type === "group" ? "👥" : h.type === "user" ? "👤" : "🎭"} ${esc(h.holder)}
              ${h.key && h.display_name ? `<code class="acc-userkey" title="internal Jira user key">${esc(h.key)}</code>` : ""}
              ${h.flag ? '<span class="chip chip-red" title="direct grant to a JIRAUSER-keyed user">🚩 direct grantee</span>' : ""}
              ${h.not_member ? `<span class="chip chip-red" title="granted scheme access but not a ${esc(g.users_group || "jira-users")} member">⚠ not ${esc(g.users_group || "jira-users")}</span>` : ""}
              ${h.type === "group" ? (h.ldap_resolved
                ? `<span class="chip chip-cyan" title="resolved via getTeamMembersCN.sh">👥 ${h.ldap_member_count} LDAP member(s)</span>`
                : (h.ldap_resolved === false ? '<span class="chip chip-red" title="granted group does not resolve to any LDAP group">⚠ outside LDAP scope</span>' : "")) : ""}</span>
              ${h.type === "user" ? userSeen(h.key, (h.holder || "").replace(/^user /, "")) : ""}
              ${permChips(h.permissions)}</div>`;
          }).join("")}
        </div>
      </details>`).join("");
}

// ---- Jira activity & last-seen ----
const daysSince = (iso) => iso ? Math.floor((Date.now() - new Date(iso).getTime()) / 86400e3) : null;
const isoDay = (iso) => { try { return new Date(iso).toISOString().slice(0, 10); } catch { return iso || ""; } };
const staleChip = (iso, warnDays = 90) => {
  const d = daysSince(iso);
  return (d != null && d >= warnDays)
    ? `<span class="chip chip-amber" title="${d} days ago">stale · ${d >= 365 ? (d / 365).toFixed(1) + "y" : Math.round(d / 30) + "mo"}</span>` : "";
};
const jDate = (iso) => iso
  ? `<span title="${esc(isoDay(iso))}">${esc(ago(iso))}</span>` : '<span class="ci-meta">—</span>';
const jIssue = (o) => (o && o.date)
  ? `<a class="ci-job" style="flex:none" title="${esc((o.summary || "") + " · " + isoDay(o.date))}">${esc(o.key)}</a> · ${jDate(o.date)}`
  : '<span class="ci-meta">— none —</span>';

function accActivityHtml(d) {
  if (d.source === "not configured")
    return `<div class="empty">Jira not configured</div>`;
  const projects = (d.projects || []).map((p) => `
    <div class="actv-row actv-proj">
      <span class="actv-name"><a class="ci-job" href="${esc(p.url)}" target="_blank" rel="noopener" style="flex:none">${esc(p.key)}</a>
        <span class="ci-meta">${esc(p.name)}</span></span>
      <span class="actv-cell"><span class="actv-lbl">last opened</span> ${jIssue(p.last_opened)}</span>
      <span class="actv-cell"><span class="actv-lbl">last interaction</span> ${jDate((p.last_interaction || {}).date)}
        ${staleChip((p.last_interaction || {}).date)}</span>
    </div>`).join("") || `<div class="empty">no projects</div>`;
  const users = (d.users || []).map((u) => `
    <div class="actv-row actv-user">
      <span class="actv-name">${esc(u.display_name)} <small class="ci-meta">@${esc(u.name)}</small>
        ${u.active === false ? '<span class="chip">inactive</span>' : ""}</span>
      <span class="actv-cell"><span class="actv-lbl">last login</span>
        ${u.last_login ? jDate(u.last_login) : '<span class="chip" title="Jira REST does not expose this user\'s last-login">N/A</span>'}</span>
      <span class="actv-cell"><span class="actv-lbl">last activity</span> ${jIssue(u.last_activity)}
        ${staleChip((u.last_activity || {}).date)}</span>
    </div>`).join("") || `<div class="empty">no users / jira-users not readable</div>`;
  const loginNote = (d.source === "live" && !d.any_login)
    ? `<div class="kpi-note">ℹ your Jira's REST API doesn't expose per-user last-login (Cloud removed it; DC keeps it in admin/Crowd internals) — the login column shows <b>N/A</b>. “last activity” (most recent issue reported/assigned) is always available.</div>`
    : "";
  const pTrunc = d.projects_truncated
    ? `<span class="ci-meta">· showing ${(d.projects || []).length} of ${d.project_total} (cap)</span>` : "";
  const uTrunc = d.users_truncated
    ? `<span class="ci-meta">· showing ${(d.users || []).length} of ${d.user_total} (cap)</span>` : "";
  return `
    <div class="ci-meta" style="margin-bottom:6px">${srcLabel(d)} — dates via JQL; stale = no interaction in 90d+</div>
    ${loginNote}
    <div class="acc-subhead">projects — last opened &amp; last interaction ${pTrunc}</div>
    <div class="actv-list">${projects}</div>
    <div class="acc-subhead">users — last login &amp; last activity ${uTrunc}</div>
    <div class="actv-list">${users}</div>`;
}

function accJenkinsHtml(d) {
  if (!d.items.length)
    return `<div class="empty">no matrix-based entries found (${srcLabel(d)})${d.note ? "<br><small>" + esc(d.note) + "</small>" : ""}</div>`;
  return `<div class="ci-meta" style="margin-bottom:8px">${srcLabel(d)} · ${d.items.length} scope(s) with matrix entries${d.note ? " · " + esc(d.note) : ""}</div>`
    + d.items.map((it) => `
      <details class="filebox" ${it.path.startsWith("★") ? "open" : ""}>
        <summary>${it.path.startsWith("★") ? "" : "⚙ "}<b>${esc(it.path)}</b> <span class="ci-meta">${it.entries.length} principal(s)</span></summary>
        <div style="padding:8px 12px">
          ${it.entries.map((e) => `
            <div class="acc-acl"><span class="acc-ident">${e.type === "group" ? "👥" : e.type === "user" ? "👤" : "❔"} ${esc(e.sid)}</span>
              ${permChips(e.permissions)}</div>`).join("")}
        </div>
      </details>`).join("");
}

function wireAccess(section) {
  if (section === "ldap") {
    const inp = document.getElementById("ldap-probe-team");
    const btn = document.getElementById("ldap-probe-run");
    const out = document.getElementById("ldap-probe-out");
    if (btn && inp && out) {
      const run = async () => {
        const team = inp.value.trim();
        if (!team) { inp.focus(); return; }
        btn.disabled = true;
        out.innerHTML = `<div class="ci-meta">⏳ running getTeamMembersCN.sh ${esc(team)}…</div>`;
        try {
          const r = await api(`/api/access/ldap/test?team=${encodeURIComponent(team)}`);
          out.innerHTML = accProbeResultHtml(r);
        } catch (e) {
          out.innerHTML = `<div class="empty">⚠ ${esc(e.message)}</div>`;
        } finally { btn.disabled = false; }
      };
      btn.onclick = run;
      inp.onkeydown = (e) => { if (e.key === "Enter") run(); };
    }
  }
  if (section === "ado") {
    view().querySelectorAll("[data-acc-proj]").forEach((det) => {
      det.ontoggle = async () => {
        if (!det.open || det.dataset.loaded) return;
        det.dataset.loaded = "1";
        const coll = det.dataset.accColl, pid = det.dataset.accProj;
        const box = document.getElementById(`acc-proj-${coll}-${pid}`);
        try {
          const d = await api(`/api/access/ado/${encodeURIComponent(coll)}/${encodeURIComponent(pid)}`);
          if (box) box.innerHTML = accAdoProjectHtml(d);
        } catch (e) { if (box) box.innerHTML = `<div class="empty">⚠ ${esc(e.message)}</div>`; }
      };
    });
    wireAdoFilters();
  }
}

// re-render the ADO section from the cached payload (filters change client-side)
function rerenderAdo() {
  const box = document.getElementById("acc-ado");
  if (box && state.adoData) { box.innerHTML = accAdoHtml(state.adoData); wireAccess("ado"); }
}

function wireAdoFilters() {
  const f = state.adoFilter = state.adoFilter || {};
  view().querySelectorAll("[data-ado-filter]").forEach((el) => {
    if (el.tagName === "SELECT") {
      el.onchange = () => { f[el.dataset.adoFilter] = el.value; rerenderAdo(); };
    } else {  // numeric inputs
      el.onchange = () => { f[el.dataset.adoFilter] = el.value; rerenderAdo(); };
    }
  });
  const q = document.getElementById("ado-q");
  if (q) q.oninput = () => {
    f.q = q.value;
    clearTimeout(state._adoQT);
    state._adoQT = setTimeout(() => {
      rerenderAdo();
      const nq = document.getElementById("ado-q");
      if (nq) { nq.focus(); nq.setSelectionRange(nq.value.length, nq.value.length); }
    }, 200);
  };
  const clear = () => { state.adoFilter = {}; rerenderAdo(); };
  const cb = document.getElementById("ado-filter-clear");
  if (cb) cb.onclick = clear;
  const cb2 = document.getElementById("ado-filter-clear2");
  if (cb2) cb2.onclick = clear;
}

function accSummaryHtml(d) {
  const a = d.ado, j = d.jira, o = d.overlap;
  const tile = (v, label, cls) => `<div class="stat-tile"><b class="${cls || ""}">${v}</b><span>${label}</span></div>`;
  const tiles = [
    tile(a.collections, "ADO collections"),
    tile(a.projects, "ADO projects"),
    tile(a.repos, "ADO repos"),
    tile(a.teams, "ADO teams"),
    tile(a.named_users + (a.approx_users ? "+" : ""), "ADO named users"),
    tile(j.schemes, "Jira permission schemes"),
    tile(j.projects, "Jira projects"),
    tile(j.jirauser_grants, "JIRAUSER users", j.jirauser_grants ? "pct-bad" : ""),
  ].join("");
  const ov = o.comparable ? `
    <div class="acc-overlap">
      <div class="stat-tile"><b class="pct-good">${o.both_count}</b><span>in BOTH ADO &amp; Jira (same name)</span></div>
      <div class="stat-tile"><b>${o.ado_only_count}</b><span>ADO only</span></div>
      <div class="stat-tile"><b>${o.jira_only_count}</b><span>Jira only</span></div>
    </div>
    ${o.both_count ? `<details class="filebox"><summary>🔗 ${o.both_count} project(s) in both systems</summary>
      <div style="padding:8px 12px">${o.both.map((b) => `<div class="ci-row"><span class="ci-job">${esc(b.ado)}</span>
        <span class="ci-meta">ADO ↔ Jira ${esc(b.jira || "")}</span></div>`).join("")}</div></details>` : ""}`
    : `<div class="kpi-note">ADO/Jira name comparison needs both sources configured</div>`;
  return `<div class="stat-tiles">${tiles}</div>${ov}`;
}

// ---- ADO -> Gitea migration ----
const MIG_CHIP = {
  create: '<span class="chip chip-amber">＋ create</span>',
  exists: '<span class="chip chip-green">✓ exists</span>',
  migrate: '<span class="chip chip-cyan">⇪ migrate</span>',
  grant: '<span class="chip chip-amber">＋ grant</span>',
};
const migChip = (a) => MIG_CHIP[a] || `<span class="chip">${esc(a || "")}</span>`;
const PERM_CHIP = { admin: "chip-red", write: "chip-amber", read: "chip-cyan" };

function migOrgCard(o) {
  const repos = o.repos.map((r) => `<div class="mig-line">${migChip(r.action)}
    <code>${esc(r.name)}</code> → <code>${esc(r.gitea_repo)}</code></div>`).join("")
    || '<div class="ci-meta">no repos</div>';
  const teams = o.teams.map((t) => `<div class="mig-line">${migChip(t.action)}
    <code>${esc(t.gitea_team)}</code>
    <span class="chip ${PERM_CHIP[t.permission] || ""}">${esc(t.permission)}</span>
    <span class="chip">${esc(t.source)}</span>
    <span class="ci-meta">${t.members && t.members.length
      ? t.members.slice(0, 6).map((m) => m.gitea_user + (m.verify ? "⚠" : "")).join(", ")
        + (t.members.length > 6 ? ` +${t.members.length - 6}` : "")
      : (t.member_count != null ? t.member_count + " member(s)" : "—")}</span></div>`).join("")
    || '<div class="ci-meta">no teams</div>';
  const access = [
    ...o.collaborators.map((c) => `<div class="mig-line">${migChip("grant")}
      <span title="repo-level access">👤 ${esc(c.gitea_user)}${c.verify ? " ⚠" : ""}</span>
      <span class="chip ${PERM_CHIP[c.permission] || ""}">${esc(c.permission)}</span>
      <span class="ci-meta">on ${esc(c.repo)}</span></div>`),
    ...o.protections.map((p) => `<div class="mig-line"><span class="chip chip-amber">＋ protect</span>
      🔀 <code>${esc(p.repo)}</code>@${esc(p.branch)}
      <span class="ci-meta">≥${p.required_approvals} approval · team ${esc(p.team)} · ${esc(p.scope || "")}</span></div>`),
  ].join("") || '<div class="ci-meta">no repo-level access or PR protections</div>';
  return `<div class="mig-org">
    <div class="mig-org-head">🏛 <b>${esc(o.org)}</b> ${migChip(o.org_action)}
      <span class="ci-meta">${o.repos.length} repos · ${o.teams.length} teams · ${o.collaborators.length} collaborators · ${o.protections.length} protections</span></div>
    <div class="mig-cols">
      <div><div class="mig-col-h">repositories</div>${repos}</div>
      <div><div class="mig-col-h">teams (access)</div>${teams}</div>
      <div><div class="mig-col-h">repo-level + PR reviewers</div>${access}</div>
    </div></div>`;
}

function accMigrationHtml(d, tconf) {
  const s = d.summary || {};
  const colls = tconf.collections || [];
  const byColl = {}; (tconf.targets || []).forEach((t) => { byColl[t.collection] = t; });
  const targetRow = colls.map((c) => {
    const t = byColl[c];
    return t
      ? `<div class="mig-target"><span class="chip chip-cyan">${esc(c)}</span> →
          <code>${esc(t.url)}</code> <span class="chip">${t.org_strategy === "collection_project" ? "org=coll-project" : "org=project"}</span>
          ${t.has_token ? "" : '<span class="chip chip-red">no token</span>'}
          <button class="btn btn-sm" data-mig-edit="${esc(c)}" data-mig-url="${esc(t.url)}" data-mig-strat="${esc(t.org_strategy)}">edit</button>
          <button class="btn btn-sm btn-danger" data-mig-del="${t.id}">✕</button></div>`
      : `<div class="mig-target mig-unconf"><span class="chip">${esc(c)}</span>
          <span class="ci-meta">no Gitea target — this collection won't migrate</span>
          <button class="btn btn-sm" data-mig-edit="${esc(c)}">＋ configure</button></div>`;
  }).join("");
  const tile = (n, label, cls) => `<div class="stat-tile"><b class="${cls || ""}">${n}</b><span>${label}</span></div>`;
  const tiles = `<div class="stat-tiles" style="margin:12px 0">
    ${tile(s.orgs_create || 0, "orgs to create", (s.orgs_create ? "pct-warn" : "pct-good"))}
    ${tile(s.repos_migrate || 0, "repos to migrate", "pct-good")}
    ${tile(s.teams_create || 0, "teams to create")}
    ${tile(s.collaborators || 0, "repo-level grants")}
    ${tile(s.protections || 0, "PR protections")}
    ${tile(s.verify_users || 0, "users to verify", (s.verify_users ? "pct-bad" : "pct-good"))}</div>`;
  const targetCards = (d.targets || []).map((t) => {
    const st = t.state || {};
    const health = st.reachable
      ? `<span class="chip chip-green">✓ gitea ${esc(st.version || "?")}</span>`
      : `<span class="chip chip-red">✗ ${esc(st.error || "unreachable")}</span>`;
    return `<details class="filebox mig-target-card" open>
      <summary>🎯 <b>${esc(t.collection)}</b> → <code>${esc(t.gitea_url)}</code> ${health}
        <span class="ci-meta">current Gitea: ${st.org_count || 0} orgs · ${st.repo_count || 0} repos · ${st.team_count || 0} teams · ${t.projects} project(s) to map</span></summary>
      <div class="mig-body">${t.orgs.map(migOrgCard).join("")}</div>
    </details>`;
  }).join("") || '<div class="empty">no configured collections to plan — add a Gitea target above</div>';
  const unconf = (d.unconfigured || []).length
    ? `<div class="kpi-note">⚠ ${d.unconfigured.length} collection(s) have no Gitea target and will be skipped: ${d.unconfigured.map((u) => `${esc(u.collection)} (${u.projects} proj)`).join(", ")}</div>` : "";
  return `
    <div class="ci-meta" style="margin-bottom:6px">Gitea targets (one per collection) · source: ${esc(d.source)}${d.cached ? " · cached" : ""}</div>
    <div class="mig-targets">${targetRow}
      <details class="filebox" id="mig-form-box"><summary>＋ add / update a Gitea target (one per collection)</summary>
        <div class="mig-form">
          <select id="mig-coll">${colls.map((c) => `<option value="${esc(c)}">${esc(c)}</option>`).join("")}</select>
          <input id="mig-url" placeholder="https://gitea.host">
          <input id="mig-token" type="password" placeholder="Gitea API token (repo+org+admin)">
          <select id="mig-strategy"><option value="project">org = project name</option><option value="collection_project">org = collection-project</option></select>
          <button class="btn btn-sm btn-primary" id="mig-save">save target</button>
          <span id="mig-form-msg" class="ci-meta"></span>
        </div></details></div>
    ${tiles}${unconf}
    <div class="mig-actions">
      <button class="btn btn-sm" id="mig-replan">↻ re-plan</button>
      <button class="btn btn-sm btn-primary" id="mig-dry">▶ dry-run (preview)</button>
      <button class="btn btn-sm btn-danger" id="mig-run">🚀 migrate for real</button>
      <span class="ci-meta">dry run shows every action without writing; migrate for real needs the approver role + confirm</span>
    </div>
    <div id="mig-result">${state.migResult ? migResultHtml(state.migResult) : ""}</div>
    ${targetCards}`;
}

function migResultHtml(r) {
  const badge = r.dry_run ? '<span class="chip chip-cyan">🧪 dry run — preview only</span>'
    : '<span class="chip chip-amber">🚀 live migration</span>';
  const demoNote = r.demo ? ' <span class="chip">demo — not executed</span>' : "";
  const head = `<div class="mig-run-head">${badge}${demoNote}
    <b class="pct-good">${r.ok} action(s)</b>${(r.skip || 0) ? ` · <span class="ci-meta">${r.skip} already present / manual</span>` : ""}${(r.error || 0) ? ` · <b class="pct-bad">${r.error} error(s)</b>` : ""}
    <span class="ci-meta">across ${r.targets_run || 0} target(s)${r.dry_run ? " — nothing written" : ""}</span></div>`;
  if (r.note && !r.total)
    return `<div class="mig-run">${head}<div class="kpi-note">ℹ ${esc(r.note)}</div></div>`;
  // group by target → org so it reads like a migration transcript
  const groups = {};
  (r.steps || []).forEach((s) => {
    const k = (s.target || "") + " " + (s.org || "");
    (groups[k] = groups[k] || { target: s.target, org: s.org, steps: [] }).steps.push(s);
  });
  const STAT = { ok: "chip-green", skip: "chip", error: "chip-red" };
  const ICON = { ok: "✓", skip: "•", error: "✗" };
  const orgsHtml = Object.values(groups).map((gp) => `
    <div class="mig-run-org">
      <div class="mig-run-org-h">${gp.org ? `🏛 <b>${esc(gp.org)}</b>` : "—"}
        <span class="ci-meta">${esc(gp.target || "")}</span></div>
      ${gp.steps.map((s) => `<div class="mig-line">
        <span class="chip ${STAT[s.status] || ""}">${ICON[s.status] || "?"}</span>
        <span class="chip">${esc(s.action)}</span> <code>${esc(s.ref)}</code>
        <span class="ci-meta">${esc(s.note || "")}</span></div>`).join("")}
    </div>`).join("") || '<div class="empty">no steps</div>';
  const hint = (!r.dry_run && !r.demo && r.ok)
    ? '<div class="kpi-note">✅ done — click ↻ re-plan to refresh the current-Gitea-state view below</div>' : "";
  const note = r.note ? `<div class="kpi-note">ℹ ${esc(r.note)}</div>` : "";
  return `<div class="mig-run">${head}${note}${hint}<div class="mig-run-log">${orgsHtml}</div></div>`;
}

async function renderMigration() {
  view().innerHTML = `
    <div class="view-head"><h1>ADO → GITEA MIGRATION</h1>
      <span class="sub">clone code, structure &amp; access to self-hosted Gitea</span>
      <span class="spacer"></span>
      <button class="btn btn-sm" id="mig-replan-top">↻ re-plan</button></div>
    <div class="kpi-note" style="margin-bottom:12px">maps ADO <b>collection → Gitea instance</b>,
      <b>project → org</b>, <b>repo → repo</b>; teams, repo-level access &amp; PR reviewers replicated.
      Dry run is read-only; a real migration writes to Gitea and needs the approver role.</div>
    <div id="mig-root"><div class="empty acc-loading">⏳ planning…</div></div>`;
  const tb = document.getElementById("mig-replan-top");
  if (tb) tb.onclick = () => loadMigration(true);
  loadMigration(false);
}

async function loadMigration(refresh) {
  const box = document.getElementById("mig-root");
  if (!box) return;
  box.innerHTML = `<div class="empty acc-loading">⏳ planning the ADO → Gitea migration (reads each Gitea instance)…</div>`;
  try {
    const [plan, tconf] = await Promise.all([
      api(`/api/access/migration/plan${refresh ? "?refresh=true" : ""}`),
      api("/api/access/migration/targets"),
    ]);
    box.innerHTML = accMigrationHtml(plan, tconf);
    wireMigration(tconf);
  } catch (e) {
    box.innerHTML = `<div class="empty">⚠ couldn't plan: ${esc(e.message)}
      <button class="btn btn-sm" id="mig-retry">↻ retry</button></div>`;
    const rb = document.getElementById("mig-retry");
    if (rb) rb.onclick = () => loadMigration(refresh);
  }
}

function wireMigration(tconf) {
  const $$ = (id) => document.getElementById(id);
  view().querySelectorAll("[data-mig-edit]").forEach((b) => b.onclick = () => {
    $$("mig-form-box").open = true;
    $$("mig-coll").value = b.dataset.migEdit;
    if (b.dataset.migUrl) $$("mig-url").value = b.dataset.migUrl;
    if (b.dataset.migStrat) $$("mig-strategy").value = b.dataset.migStrat;
    $$("mig-token").focus();
  });
  view().querySelectorAll("[data-mig-del]").forEach((b) => b.onclick = async () => {
    if (!confirm("Remove this Gitea target? (does not touch Gitea itself)")) return;
    try { await api(`/api/access/migration/targets/${b.dataset.migDel}`, { method: "DELETE" }); loadMigration(true); }
    catch (e) { toast("⚠ " + e.message, "toast-err"); }
  });
  const save = $$("mig-save");
  if (save) save.onclick = async () => {
    const body = { collection: $$("mig-coll").value, url: $$("mig-url").value.trim(),
      token: $$("mig-token").value, org_strategy: $$("mig-strategy").value };
    if (!body.url) { $$("mig-form-msg").textContent = "Gitea URL is required"; return; }
    save.disabled = true; $$("mig-form-msg").textContent = "saving…";
    try { await api("/api/access/migration/targets", { method: "POST", body }); loadMigration(true); }
    catch (e) { $$("mig-form-msg").textContent = "⚠ " + e.message; save.disabled = false; }
  };
  const replan = $$("mig-replan"); if (replan) replan.onclick = () => loadMigration(true);
  const runExec = async (dry) => {
    const btn = dry ? $$("mig-dry") : $$("mig-run");
    if (!dry && !confirm("Run the REAL migration now?\n\nThis creates orgs/repos/teams and pushes source code into Gitea for every configured collection.")) return;
    btn.disabled = true;
    const res = $$("mig-result");
    res.innerHTML = `<div class="empty acc-loading">⏳ ${dry ? "simulating the migration (read-only)" : "migrating — creating orgs/repos/teams in Gitea"}…</div>`;
    res.scrollIntoView({ behavior: "smooth", block: "nearest" });
    try {
      const r = await api("/api/access/migration/execute", { method: "POST",
        body: { dry_run: dry, confirm: !dry } });
      state.migResult = r;                       // persists across re-plans
      res.innerHTML = migResultHtml(r);
      if (!dry) toast(`🚀 migration: ${r.ok} action(s)${r.error ? `, ${r.error} error(s)` : ""}`, r.error ? "toast-err" : "toast-quest");
    } catch (e) { res.innerHTML = `<div class="empty">⚠ migration failed: ${esc(e.message)}</div>`; }
    finally { btn.disabled = false; }
  };
  const dry = $$("mig-dry"); if (dry) dry.onclick = () => runExec(true);
  const run = $$("mig-run"); if (run) run.onclick = () => runExec(false);
}

async function renderAccess() {
  view().innerHTML = `
    <div class="view-head"><h1>ACCESS MANAGEMENT</h1>
      <span class="sub">who can do what — ADO · Jira · Jenkins</span>
      <span class="spacer"></span>
      <button class="btn btn-sm" id="acc-refresh">↻ refresh all (bypasses caches)</button></div>
    <div class="kpi-note" style="margin-bottom:12px">source systems are protected: results cache for 15 minutes,
      ADO project details load only when expanded, and fetches are bounded-parallel</div>
    <div class="panel" style="margin-bottom:18px"><h2>📊 at a glance</h2>
      <div id="acc-summary"></div></div>
    <div class="panel" style="margin-bottom:18px"><h2>⛁ Azure DevOps — projects &amp; repository permissions</h2>
      <div id="acc-ado"></div></div>
    <div class="panel"><h2>🎫 Jira — permission schemes, assignments &amp; activity</h2>
      <div id="acc-jira"></div></div>`;

  const load = (refresh) => {
    const s = refresh ? "?refresh=true" : "";
    accLoad("summary", `/api/access/summary${s}`, accSummaryHtml);
    accLoad("ado", `/api/access/ado${s}`, accAdoHtml);
    loadJira(refresh);
  };
  load(false);
  document.getElementById("acc-refresh").onclick = () => load(true);
}

/* ================= UPGRADES ================= */
const UPG_STATUS = {
  eol:     { label: "END OF LIFE", cls: "chip-red" },
  upgrade: { label: "upgrade available", cls: "chip-red" },
  patch:   { label: "patch available", cls: "chip-amber" },
  ok:      { label: "✓ up to date", cls: "chip-green" },
  unknown: { label: "unknown", cls: "" },
};

async function renderUpgrades(refresh) {
  const data = await api(`/api/upgrades${refresh === true ? "?refresh=true" : ""}`);
  const rows = data.rows.map((r) => {
    const st = UPG_STATUS[r.status] || UPG_STATUS.unknown;
    const needs = ["eol", "upgrade", "patch"].includes(r.status);
    return `
    <div class="panel upg-row ${needs ? "upg-needs" : ""}">
      <div class="upg-name">${r.icon} <b>${esc(r.name)}</b></div>
      <div class="upg-vers">
        <div class="upg-ver"><span class="ci-meta">current</span>
          <b>${esc(r.current || "—")}</b>
          ${r.detect_error ? `<small class="pct-warn">${esc(r.detect_error)}</small>` : ""}</div>
        <div class="upg-arrow">${needs ? "→" : "·"}</div>
        <div class="upg-ver"><span class="ci-meta">${r.lts ? "latest LTS" : "latest supported"}</span>
          <b>${esc(r.latest || r.recommended || "—")}</b>
          ${r.eol_date ? `<small>supported until ${esc(r.eol_date)}</small>` : ""}</div>
      </div>
      <span class="chip ${st.cls}">${st.label}</span>
      ${needs && r.current ? `<button class="btn btn-sm btn-primary"
          data-upg="${esc(r.name)}" data-cur="${esc(r.current)}"
          data-to="${esc(r.latest || r.recommended)}" data-status="${r.status}">＋ upgrade ticket</button>` : ""}
      <a class="btn btn-sm btn-ghost" href="${esc(r.page)}" target="_blank" rel="noopener">versions ↗</a>
      <div class="upg-src ci-meta">source: ${esc(r.source)}${r.lookup_error ? ` · lookup failed: ${esc(r.lookup_error)}` : ""}</div>
    </div>`;
  }).join("");

  view().innerHTML = `
    <div class="view-head"><h1>UPGRADES</h1>
      <span class="sub">running version vs latest LTS per integration ·
        checked ${ago(data.checked_at)}${data.cached ? " (cached)" : ""}${data.demo_versions ? " · demo versions" : ""}</span>
      <span class="spacer"></span>
      <button class="btn btn-sm" id="upg-refresh">↻ re-check now</button></div>
    ${data.degraded ? `<div class="remote-banner remote-new" style="margin-bottom:14px">
      <b>⚠ online version lookups are failing</b>
      <div class="ci-meta">${esc(data.hint || "")} — bundled versions below may be stale; lookups retry automatically every 10 minutes</div>
      ${data.lookup_config ? `<div class="ci-meta" style="margin-top:4px">server lookup config —
        proxy: <b>${esc(data.lookup_config.proxy || "none")}</b>${data.lookup_config.env_proxy ? ` · container env proxy: <b>${esc(data.lookup_config.env_proxy)}</b>` : ""}
        · verify_ssl: <b>${data.lookup_config.verify_ssl}</b>
        · sources: <b>${esc(data.lookup_config.sources)}</b>
        &nbsp;(if the proxy you set isn't shown here, the env var never reached the container — check QO_UPGRADES_PROXY and restart)</div>` : ""}
    </div>` : ""}
    ${rows || `<div class="empty">no tools to check</div>`}
    <div class="kpi-note" style="margin-top:12px">outdated tools feed the task pool:
      “＋ upgrade ticket” creates a prioritized Jira ticket (EOL → Highest, major → High, patch → Medium)</div>`;

  $("#upg-refresh").onclick = () => {
    view().innerHTML = `<div class="empty">re-checking versions…</div>`;
    renderUpgrades(true).catch((e) => { view().innerHTML = `<div class="empty">⚠ ${esc(e.message)}</div>`; });
  };
  view().querySelectorAll("[data-upg]").forEach((b) => b.onclick = async () => {
    const prio = b.dataset.status === "eol" ? "Highest"
      : b.dataset.status === "upgrade" ? "High" : "Medium";
    b.disabled = true;
    try {
      const d = await api("/api/issues", { method: "POST", body: {
        summary: `Upgrade ${b.dataset.upg} ${b.dataset.cur} → ${b.dataset.to}`,
        type: "Task", priority: prio,
        description: `Created by the QuestOps upgrade checker.\n` +
          `${b.dataset.upg} is running ${b.dataset.cur}; latest ${b.dataset.status === "patch" ? "patch" : "LTS/supported"} is ${b.dataset.to}.\n` +
          `Status: ${b.dataset.status}.`,
      }});
      handleGame(d.game);
      toast(`🎫 <b>${esc(d.issue.key)}</b> added to the pool · ${esc(prio)}`, "toast-xp", 5000);
      b.textContent = `✓ ${d.issue.key}`;
    } catch (e) { oops(e); b.disabled = false; }
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
