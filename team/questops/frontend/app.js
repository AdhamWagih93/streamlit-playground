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
                logging: renderLogging, migration: renderMigration,
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
// discovered ADO repos grouped 🗄 collection → 📁 project, each project with a
// one-shot "➕ Add project" that defines all its not-yet-added repos at once
function discoverGroupsHtml(d, existing, collFilter) {
  const repos = d.repos || [];
  if (!repos.length)
    return `<div class="empty">no repositories found${collFilter ? " in " + esc(collFilter) : " on the ADO instance"}</div>`;
  const colls = {};
  repos.forEach((r) => {
    const c = r.collection || "";
    const p = r.project || "(ungrouped)";
    ((colls[c] = colls[c] || {})[p] = colls[c][p] || []).push(r);
  });
  const anyColl = Object.keys(colls).some((c) => c);
  const repoRow = (r) => {
    const added = existing.has(r.url);
    return `<div class="disc-row ${added ? "added" : ""}">
      <span class="ci-job">⛁ ${esc(r.name)}</span>
      ${added ? '<span class="chip chip-green">✓ added</span>'
        : `<button class="btn btn-sm" data-adourl="${esc(r.url)}" data-adoname="${esc(r.name)}">+ add</button>`}
    </div>`;
  };
  const projBlock = (collName, projName, arr) => {
    const news = arr.filter((r) => !existing.has(r.url)).length;
    const rows = arr.slice().sort((a, b) => a.name.localeCompare(b.name)).map(repoRow).join("");
    return `<div class="disc-proj">
      <div class="disc-proj-head">
        <span class="repo-proj-name">📁 ${esc(projName)}</span>
        <span class="repo-proj-count ${news ? "" : "all"}">${arr.length - news}/${arr.length} added</span>
        ${news ? `<button class="btn btn-sm btn-primary disc-add-all" data-add-coll="${esc(collName)}" data-add-proj="${esc(projName)}"
            title="define all ${news} new repo(s) of this project in one action — DB only, no clone; then use ⬇ Clone all below">➕ Add project (${news})</button>`
          : '<span class="chip chip-green">✓ all added</span>'}
      </div>
      <div class="disc-proj-rows">${rows}</div>
    </div>`;
  };
  const sortedColls = Object.keys(colls).sort((a, b) => (a || "￿").localeCompare(b || "￿"));
  return sortedColls.map((c) => {
    const projs = colls[c];
    const projNames = Object.keys(projs).sort();
    const inner = projNames.map((p) => projBlock(c, p, projs[p])).join("");
    if (!anyColl) return inner;
    const collNew = projNames.reduce((n, p) => n + projs[p].filter((r) => !existing.has(r.url)).length, 0);
    return `<details class="repo-coll" open>
      <summary>🗄 <b>${esc(c || "(no collection)")}</b>
        <span class="ci-meta">${projNames.length} project${projNames.length === 1 ? "" : "s"}</span>
        ${c && collNew ? `<button class="btn btn-sm btn-primary disc-add-coll" data-addc-coll="${esc(c)}"
            title="define all ${collNew} new repo(s) across this whole collection in one action — DB only, no clone">➕ Add collection (${collNew})</button>` : ""}</summary>
      <div class="repo-coll-body">${inner}</div></details>`;
  }).join("");
}

async function addCollection(collection, btn) {
  if (btn) { btn.disabled = true; btn.textContent = "⏳ adding…"; }
  try {
    const { result } = await api("/api/repos/add-collection",
      { method: "POST", body: { collection } });
    const bits = [`${result.added_count} added`];
    if (result.skipped_count) bits.push(`${result.skipped_count} already`);
    if (result.error_count) bits.push(`${result.error_count} failed`);
    toast(`🗄 <b>${esc(collection)}</b>: ${bits.join(" · ")} across ${result.projects.length} project(s)`,
      result.error_count ? "toast-err" : "toast-xp");
    (result.errors || []).forEach((e) => oops(`${e.name}: ${e.error}`));
    renderRepos();
  } catch (e) { oops(e); if (btn) { btn.disabled = false; btn.textContent = "➕ Add collection"; } }
}

async function addProject(collection, project, btn) {
  if (btn) { btn.disabled = true; btn.textContent = "⏳ adding…"; }
  try {
    const { result } = await api("/api/repos/add-project",
      { method: "POST", body: { collection, project } });
    const bits = [`${result.added_count} added`];
    if (result.skipped_count) bits.push(`${result.skipped_count} already`);
    if (result.error_count) bits.push(`${result.error_count} failed`);
    toast(`📁 <b>${esc(project)}</b>: ${bits.join(" · ")} — ⬇ Clone all to fetch them`,
      result.error_count ? "toast-err" : "toast-xp");
    (result.errors || []).forEach((e) => oops(`${e.name}: ${e.error}`));
    renderRepos();
  } catch (e) { oops(e); if (btn) { btn.disabled = false; btn.textContent = "➕ Add project"; } }
}

function repoAddHtml() {
  const d = state.repoDiscover;
  const collFilter = state.repoDiscoverColl || "";
  const collChips = d && !d.error && (d.collections || []).length > 1 ? `
    <div class="filter-row" style="margin:8px 0;flex-wrap:wrap">
      <button class="btn btn-sm ${!collFilter ? "btn-primary" : ""}" data-disc-coll="">all collections</button>
      ${d.collections.map((c) => `<button class="btn btn-sm ${collFilter === c ? "btn-primary" : ""}" data-disc-coll="${esc(c)}">🗄 ${esc(c)}</button>`).join(" ")}
    </div>` : "";
  const existing = new Set((state.reposData || []).map((r) => r.url));
  const list = !d ? `<div class="empty">browsing the ADO instance…</div>`
    : d.error ? `<div class="empty">⚠ ${esc(d.error)}<br>
        <button class="btn btn-sm" id="repo-discover-retry" style="margin-top:8px">↻ retry</button></div>`
    : discoverGroupsHtml(d, existing, collFilter);
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
  (slot || view()).querySelectorAll("[data-add-proj]").forEach((b) => b.onclick = () =>
    addProject(b.dataset.addColl, b.dataset.addProj, b));
  (slot || view()).querySelectorAll("[data-addc-coll]").forEach((b) => b.onclick = (e) => {
    e.preventDefault(); e.stopPropagation();
    addCollection(b.dataset.addcColl, b);
  });
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

/* ---- cloned repos grouped by collection → project, with bulk clone ---- */
function repoGroupsHtml(repos, cur) {
  const colls = {};
  repos.forEach((r) => {
    const c = r.collection || "";
    const p = r.project || "(ungrouped)";
    ((colls[c] = colls[c] || {})[p] = colls[c][p] || []).push(r);
  });
  const anyColl = Object.keys(colls).some((c) => c);
  const chip = (r) => `
    <button class="btn btn-sm repo-chip ${r.slot === cur.slot ? "btn-primary" : ""} ${r.cloned ? "" : "not-cloned"} ${r.mine ? "mine" : ""}"
      data-repo="${r.slot}" title="${esc(r.url)}${r.cloned && r.size_h ? " · " + esc(r.size_h) : ""}${r.mine ? " · in your workspace" : ""}">
      ⛁ ${esc(r.name)}${r.dirty ? ` <span class="dirty-badge">${r.dirty}</span>` : ""}${r.cloned ? "" : " ⬇"}
    </button>`;
  // member-scoped "remove mine" + separate destructive "un-define" (delete from catalog)
  const removeMineBtn = (coll, proj, n) => n
    ? `<button class="btn btn-sm repo-remove-mine" data-rm-coll="${esc(coll)}" data-rm-proj="${esc(proj)}"
        title="remove ONLY your workspace for the ${n} repo(s) you have open here — the shared clone, catalog, other members &amp; ADO are kept">✖ Remove mine (${n})</button>` : "";
  const undefBtn = (coll, proj, scope) =>
    `<button class="btn btn-sm repo-undefine-grp" data-ud-coll="${esc(coll)}" data-ud-proj="${esc(proj)}"
        title="delete this whole ${scope} from the shared catalog (definitions + clones, for ALL members) — ADO untouched">🗑</button>`;
  const projBlock = (collName, projName, arr) => {
    const total = arr.length;
    const clonedN = arr.filter((r) => r.cloned).length;
    const mineN = arr.filter((r) => r.mine).length;
    const uncloned = total - clonedN;
    const chips = arr.slice().sort((a, b) => a.name.localeCompare(b.name)).map(chip).join("");
    return `
      <div class="repo-proj">
        <div class="repo-proj-head">
          <span class="repo-proj-name">📁 ${esc(projName)}</span>
          <span class="repo-proj-count ${clonedN === total ? "all" : ""}" title="cloned repositories in this project">${clonedN}/${total} cloned</span>
          ${uncloned ? `<button class="btn btn-sm repo-clone-all" data-clone-coll="${esc(collName)}" data-clone-proj="${esc(projName)}"
              title="clone the ${uncloned} un-cloned repo(s) in this project — one at a time, easy on ADO">⬇ Clone all (${uncloned})</button>` : ""}
          ${removeMineBtn(collName, projName, mineN)}
          ${undefBtn(collName, projName, "project")}
        </div>
        <div class="repo-proj-chips">${chips}</div>
      </div>`;
  };
  const sortedColls = Object.keys(colls).sort((a, b) => (a || "￿").localeCompare(b || "￿"));
  return sortedColls.map((c) => {
    const projs = colls[c];
    const projNames = Object.keys(projs).sort();
    const inner = projNames.map((p) => projBlock(c, p, projs[p])).join("");
    if (!anyColl) return inner;                      // no collections → just project groups
    const totalRepos = projNames.reduce((n, p) => n + projs[p].length, 0);
    const clonedRepos = projNames.reduce((n, p) => n + projs[p].filter((r) => r.cloned).length, 0);
    const mineC = projNames.reduce((n, p) => n + projs[p].filter((r) => r.mine).length, 0);
    return `
      <details class="repo-coll" open>
        <summary>🗄 <b>${esc(c || "(no collection)")}</b>
          <span class="ci-meta">${projNames.length} project${projNames.length === 1 ? "" : "s"} · ${clonedRepos}/${totalRepos} repos cloned</span>
          <span class="repo-coll-actions">${removeMineBtn(c, "", mineC)}${undefBtn(c, "", "collection")}</span></summary>
        <div class="repo-coll-body">${inner}</div>
      </details>`;
  }).join("");
}

/* ---- branches + delta viewer ---- */
function branchOption(b, selected) {
  const badge = b.current ? " ✱" : "";
  return `<option value="${esc(b.name)}" ${b.name === selected ? "selected" : ""}>${esc(b.name)}${badge}</option>`;
}

function branchSelect(id, bdata, selected) {
  const grp = (label, arr) => arr.length
    ? `<optgroup label="${label}">${arr.map((b) => branchOption(b, selected)).join("")}</optgroup>` : "";
  return `<select id="${id}" class="branch-sel">
      ${grp("local", bdata.local || [])}${grp("remote", bdata.remote || [])}</select>`;
}

function branchesPanelHtml(bdata) {
  if (!bdata || bdata.error)
    return `<div class="panel branch-panel"><div class="empty">⚠ ${esc((bdata && bdata.error) || "could not read branches")}</div></div>`;
  const list = (bdata.branches || []).map((b) => `
    <div class="branch-item ${b.current ? "current" : ""}">
      <span class="branch-name">${b.current ? "✱ " : ""}${esc(b.name)}</span>
      ${b.remote ? '<span class="chip">remote</span>' : '<span class="chip chip-cyan">local</span>'}
      <code class="branch-sha">${esc(b.sha)}</code>
      <span class="branch-sub">${esc(b.subject)}</span>
      <span class="ci-meta">${esc(b.author)} · ${esc(b.rel)}</span>
    </div>`).join("") || '<div class="empty">no branches</div>';
  return `
    <div class="panel branch-panel">
      <div class="branch-head">🌿 <b>${bdata.count}</b> branch${bdata.count === 1 ? "" : "es"}
        <span class="ci-meta">${bdata.local_count} local · ${bdata.remote_count} remote · on <b>${esc(bdata.current)}</b></span></div>
      <div class="branch-compare-bar">
        <span class="branch-cap">base</span>
        ${branchSelect("branch-base", bdata, state.branchBase)}
        <button class="btn btn-sm branch-swap" id="branch-swap" title="swap base and compare">⇄</button>
        <span class="branch-cap">compare</span>
        ${branchSelect("branch-compare", bdata, state.branchCompare)}
      </div>
      <div id="branch-delta"><div class="rsearch-status">comparing…</div></div>
      <details class="branch-list"><summary>all branches (${bdata.count})</summary>${list}</details>
    </div>`;
}

function branchDeltaHtml(d) {
  if (!d) return "";
  if (d.error) return `<div class="rsearch-status rsearch-err">⚠ ${esc(d.error)}</div>`;
  if (d.base === d.compare)
    return `<div class="branch-identical">select two different branches to compare</div>`;
  if (d.identical)
    return `<div class="branch-identical">✓ <b>${esc(d.base)}</b> and <b>${esc(d.compare)}</b> are identical</div>`;

  const diverge = `
    <div class="bd-diverge">
      <span class="bd-branch">${esc(d.base)}</span>
      <span class="bd-arrows">
        <span class="bd-behind ${d.behind ? "" : "zero"}" title="commits on ${esc(d.base)} not in ${esc(d.compare)}">← ${d.behind} behind</span>
        <span class="bd-ahead ${d.ahead ? "" : "zero"}" title="commits on ${esc(d.compare)} not in ${esc(d.base)}">${d.ahead} ahead →</span>
      </span>
      <span class="bd-branch">${esc(d.compare)}</span>
    </div>`;

  const totChg = (d.additions + d.deletions) || 1;
  const summary = `
    <div class="bd-summary">
      <span><b>${d.file_count}</b> file${d.file_count === 1 ? "" : "s"} changed</span>
      <span class="bd-add">+${d.additions}</span>
      <span class="bd-del">−${d.deletions}</span>
      ${d.binary_files ? `<span class="ci-meta">${d.binary_files} binary</span>` : ""}
      <span class="bd-propbar">
        <span class="bd-prop-add" style="width:${(d.additions / totChg * 100).toFixed(1)}%"></span>
        <span class="bd-prop-del" style="width:${(d.deletions / totChg * 100).toFixed(1)}%"></span>
      </span>
    </div>`;

  const maxTot = Math.max(1, ...d.files.map((f) => f.total));
  const files = d.files.map((f) => `
    <div class="bd-file">
      <code class="bd-path">${esc(f.path)}</code>
      ${f.binary ? '<span class="chip">binary</span>'
        : `<span class="bd-nums"><span class="bd-add">+${f.added}</span> <span class="bd-del">−${f.deleted}</span></span>
           <span class="bd-bar">
             <span class="bd-bar-add" style="width:${(f.added / maxTot * 100).toFixed(1)}%"></span>
             <span class="bd-bar-del" style="width:${(f.deleted / maxTot * 100).toFixed(1)}%"></span>
           </span>`}
    </div>`).join("") || '<div class="ci-meta" style="padding:8px 2px">no file changes (commits only)</div>';

  const commits = (d.commits || []).map((c) => `
    <div class="bd-commit">
      <code>${esc(c.short)}</code>
      <span class="bd-csub">${esc(c.subject)}</span>
      <span class="ci-meta">${esc(c.author)} · ${esc(c.rel)}</span>
    </div>`).join("");
  const commitBlock = d.commit_count ? `
    <details class="bd-commits" open>
      <summary>${d.commit_count} commit${d.commit_count === 1 ? "" : "s"} on <b>${esc(d.compare)}</b> not in <b>${esc(d.base)}</b>${d.commits_shown < d.commit_count ? ` (showing ${d.commits_shown})` : ""}</summary>
      ${commits}
    </details>` : "";

  return `${diverge}${summary}
    <div class="bd-files">${files}${d.truncated ? '<div class="rs-capped">…more files changed (capped at 400)</div>' : ""}</div>
    ${commitBlock}`;
}

async function loadBranchDelta(slot) {
  const base = state.branchBase, comp = state.branchCompare;
  const box = document.getElementById("branch-delta");
  if (!base || !comp) { if (box) box.innerHTML = '<div class="empty">pick two branches</div>'; return; }
  if (box) box.innerHTML = '<div class="rsearch-status">comparing… <span class="rsearch-spin"></span></div>';
  try {
    const d = await api(`/api/repos/${slot}/branch-delta?base=${encodeURIComponent(base)}&compare=${encodeURIComponent(comp)}`);
    state.branchDelta = d;
    const box2 = document.getElementById("branch-delta");
    if (box2) box2.innerHTML = branchDeltaHtml(d);
  } catch (e) {
    state.branchDelta = null;
    const box2 = document.getElementById("branch-delta");
    if (box2) box2.innerHTML = `<div class="rsearch-status rsearch-err">⚠ ${esc(e.message)}</div>`;
  }
}

function wireBranchPanel(slot) {
  const b = document.getElementById("branch-base");
  const c = document.getElementById("branch-compare");
  if (b) b.onchange = () => { state.branchBase = b.value; loadBranchDelta(slot); };
  if (c) c.onchange = () => { state.branchCompare = c.value; loadBranchDelta(slot); };
  const sw = document.getElementById("branch-swap");
  if (sw) sw.onclick = () => {
    const t = state.branchBase; state.branchBase = state.branchCompare; state.branchCompare = t;
    state.branchDelta = null; renderRepos();
  };
}

/* ---- global code search across all cloned repos ---- */
function rsState() {
  return (state.repoSearch = state.repoSearch || {
    q: "", regex: false, caseSensitive: false, wholeWord: false,
    glob: "", scope: "all", collection: "", project: "",
    data: null, loading: false, open: false });
}

// build a highlighter for the current query; returns escaped HTML with <mark>
function rsHighlighter(rs) {
  const q = rs.q || "";
  let re = null;
  try {
    const src = rs.regex ? q : q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const body = rs.wholeWord ? `\\b(?:${src})\\b` : src;
    re = new RegExp(body, rs.caseSensitive ? "g" : "gi");
  } catch { re = null; }
  return (text) => {
    if (!re) return esc(text);
    let out = "", last = 0, m; re.lastIndex = 0;
    while ((m = re.exec(text)) !== null) {
      if (m.index >= last) {
        out += esc(text.slice(last, m.index)) + `<mark>${esc(m[0])}</mark>`;
        last = m.index + m[0].length;
      }
      if (m.index === re.lastIndex) re.lastIndex++;   // zero-width guard
    }
    return out + esc(text.slice(last));
  };
}

function repoSearchPanelHtml(repos, cur) {
  const rs = rsState();
  const opt = (key, label, title) =>
    `<button type="button" class="rsopt ${rs[key] ? "on" : ""}" data-rsopt="${key}" title="${title}">${label}</button>`;
  // collection / project / repository scoping — options derived from the catalog,
  // project narrowed to the chosen collection, repo narrowed to both
  const colls = [...new Set(repos.map((r) => r.collection).filter(Boolean))].sort();
  const projs = [...new Set(repos.filter((r) => !rs.collection || r.collection === rs.collection)
    .map((r) => r.project).filter(Boolean))].sort();
  const scopeRepos = repos.filter((r) => r.cloned
    && (!rs.collection || r.collection === rs.collection)
    && (!rs.project || r.project === rs.project));
  const collSel = colls.length ? `<select id="rsearch-coll" title="filter by collection">
      <option value="">any collection</option>
      ${colls.map((c) => `<option value="${esc(c)}" ${rs.collection === c ? "selected" : ""}>🗄 ${esc(c)}</option>`).join("")}
    </select>` : "";
  const projSel = projs.length ? `<select id="rsearch-proj" title="filter by project">
      <option value="">any project</option>
      ${projs.map((p) => `<option value="${esc(p)}" ${rs.project === p ? "selected" : ""}>📁 ${esc(p)}</option>`).join("")}
    </select>` : "";
  const scopeSel = `<select id="rsearch-scope" title="limit the search to one repository">
      <option value="all" ${rs.scope === "all" ? "selected" : ""}>any repository</option>
      ${scopeRepos.map((r) =>
        `<option value="${r.slot}" ${String(rs.scope) === String(r.slot) ? "selected" : ""}>${esc(r.name)} only</option>`).join("")}
    </select>`;
  return `
    <div class="panel repo-search" id="repo-search-panel">
      <form id="repo-search-form" class="rsearch-bar">
        <span class="rsearch-icon">🔎</span>
        <input id="rsearch-q" placeholder="search across cloned repositories… (Enter to run)"
          value="${esc(rs.q)}" spellcheck="false" autocomplete="off">
        <div class="rsearch-opts">
          ${opt("caseSensitive", "Aa", "Match case")}
          ${opt("wholeWord", "\\b", "Whole word")}
          ${opt("regex", ".*", "Regular expression (POSIX/extended)")}
        </div>
        <input id="rsearch-glob" class="rsearch-glob" placeholder="path filter · *.py"
          value="${esc(rs.glob)}" spellcheck="false" autocomplete="off">
        ${collSel}${projSel}${scopeSel}
        <button class="btn btn-sm btn-primary" type="submit">Search</button>
        ${rs.q || rs.data ? `<button class="btn btn-sm" type="button" id="rsearch-clear">✕ clear</button>` : ""}
      </form>
      <div id="repo-search-results">${repoSearchResultsHtml()}</div>
    </div>`;
}

function repoSearchResultsHtml() {
  const rs = rsState();
  if (rs.loading) return `<div class="rsearch-status">🔎 searching… <span class="rsearch-spin"></span></div>`;
  const d = rs.data;
  if (!d) return "";
  if (d.error) return `<div class="rsearch-status rsearch-err">⚠ ${esc(d.error)}</div>`;
  const hl = rsHighlighter(rs);
  const errs = (d.errors || []).length
    ? `<div class="rsearch-status rsearch-err">⚠ ${d.errors.map((e) => `${esc(e.name)}: ${esc(e.error)}`).join(" · ")}</div>` : "";
  const summary = `<div class="rsearch-summary">
      ${d.total_matches
        ? `<b>${d.total_matches}</b> match${d.total_matches === 1 ? "" : "es"} in <b>${d.total_files}</b> file${d.total_files === 1 ? "" : "s"} across <b>${d.matched_repos}</b> repo${d.matched_repos === 1 ? "" : "s"}`
        : `<b>no matches</b> for “${esc(d.query)}”`}
      <span class="ci-meta"> · searched ${d.repos_searched} cloned repo${d.repos_searched === 1 ? "" : "s"}${d.repos_not_cloned ? ` · ${d.repos_not_cloned} not cloned` : ""} · ${d.elapsed_ms}ms</span>
    </div>`;
  const repos = (d.repos || []).filter((r) => r.match_count);
  const body = repos.map((r, ri) => {
    const files = (r.files || []).map((f) => {
      const rows = f.hits.map((h) =>
        `<div class="rs-hit" data-rs-slot="${r.slot}" data-rs-path="${esc(f.path)}" data-rs-line="${h.line}" title="open ${esc(f.path)}:${h.line}">
           <span class="rs-ln">${h.line}</span><code class="rs-code">${hl(h.text)}</code></div>`).join("");
      const capped = f.hit_count >= 60 ? `<div class="rs-capped">…more matches in this file</div>` : "";
      return `<details class="rs-file" open>
          <summary><span class="rs-fpath">${esc(f.path)}</span><span class="rs-fcount">${f.hit_count}</span></summary>
          ${rows}${capped}</details>`;
    }).join("");
    return `<details class="rs-repo" ${ri < 4 ? "open" : ""}>
        <summary>⛁ <b>${esc(r.name)}</b>
          <span class="chip chip-cyan">${r.match_count} match${r.match_count === 1 ? "" : "es"}</span>
          <span class="chip">${r.file_count} file${r.file_count === 1 ? "" : "s"}</span>
          ${r.truncated ? `<span class="chip chip-amber" title="results capped for this repo">truncated</span>` : ""}
        </summary>
        <div class="rs-files">${files}</div>
      </details>`;
  }).join("");
  return errs + summary + (repos.length ? body : "");
}

async function runRepoSearch() {
  const rs = rsState();
  const box = document.getElementById("repo-search-results");
  if (!rs.q || rs.q.trim().length < 2) {
    rs.data = { error: "enter at least 2 characters to search" };
    if (box) box.innerHTML = repoSearchResultsHtml();
    return;
  }
  rs.loading = true;
  if (box) box.innerHTML = repoSearchResultsHtml();
  const qs = new URLSearchParams({ q: rs.q.trim() });
  if (rs.regex) qs.set("regex", "true");
  if (rs.caseSensitive) qs.set("case_sensitive", "true");
  if (rs.wholeWord) qs.set("whole_word", "true");
  if (rs.glob.trim()) qs.set("path_glob", rs.glob.trim());
  if (rs.scope !== "all") qs.set("slot", rs.scope);
  if (rs.collection) qs.set("collection", rs.collection);
  if (rs.project) qs.set("project", rs.project);
  try {
    rs.data = await api(`/api/repos/search?${qs.toString()}`);
  } catch (e) {
    rs.data = { error: e.message };
  }
  rs.loading = false;
  const box2 = document.getElementById("repo-search-results");
  if (box2) { box2.innerHTML = repoSearchResultsHtml(); wireRepoSearchHits(); }
}

function wireRepoSearchHits() {
  view().querySelectorAll(".rs-hit").forEach((el) => el.onclick = () => {
    const slot = parseInt(el.dataset.rsSlot, 10);
    const path = el.dataset.rsPath;
    state.repoSlot = slot;
    state.repoPath = path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "";
    state.repoFile = path;
    state.repoJumpLine = parseInt(el.dataset.rsLine, 10) || null;
    renderRepos();
  });
}

function wireRepoSearch() {
  const rs = rsState();
  const form = document.getElementById("repo-search-form");
  if (!form) return;
  const sync = () => {
    const q = document.getElementById("rsearch-q"); if (q) rs.q = q.value;
    const g = document.getElementById("rsearch-glob"); if (g) rs.glob = g.value;
    const sc = document.getElementById("rsearch-scope"); if (sc) rs.scope = sc.value;
  };
  form.onsubmit = (e) => { e.preventDefault(); sync(); runRepoSearch(); };
  view().querySelectorAll("[data-rsopt]").forEach((b) => b.onclick = () => {
    rs[b.dataset.rsopt] = !rs[b.dataset.rsopt];
    b.classList.toggle("on");
    sync();
    if (rs.q && rs.q.trim().length >= 2 && !rs.loading) runRepoSearch();  // live re-run
  });
  // collection / project scoping — rebuild the panel so the dependent selects
  // (project narrows to collection, repo narrows to both) refresh, then re-run
  const cc = document.getElementById("rsearch-coll");
  if (cc) cc.onchange = () => {
    sync(); rs.collection = cc.value;
    const stillValid = (state.reposData || []).some((r) => r.project === rs.project
      && (!rs.collection || r.collection === rs.collection));
    if (!stillValid) rs.project = "";
    if (rs.scope !== "all") rs.scope = "all";
    updateSearchPanel();
  };
  const pc = document.getElementById("rsearch-proj");
  if (pc) pc.onchange = () => {
    sync(); rs.project = pc.value;
    if (rs.scope !== "all") rs.scope = "all";
    updateSearchPanel();
  };
  const sc2 = document.getElementById("rsearch-scope");
  if (sc2) sc2.onchange = () => { rs.scope = sc2.value; if (rs.q && rs.q.trim().length >= 2) runRepoSearch(); };
  const clear = document.getElementById("rsearch-clear");
  if (clear) clear.onclick = () => {
    rs.q = ""; rs.glob = ""; rs.data = null;
    const q = document.getElementById("rsearch-q"); if (q) q.value = "";
    const g = document.getElementById("rsearch-glob"); if (g) g.value = "";
    const box = document.getElementById("repo-search-results"); if (box) box.innerHTML = "";
    const cb = document.getElementById("rsearch-clear"); if (cb) cb.remove();
    if (q) q.focus();
  };
  wireRepoSearchHits();
}

// re-render just the search panel in place (used when collection/project
// scoping changes the dependent selects), preserving the query + re-running it
function updateSearchPanel() {
  const el = document.getElementById("repo-search-panel");
  if (!el) return;
  const repos = state.reposData || [];
  const cur = repos.find((r) => r.slot === state.repoSlot) || {};
  el.outerHTML = repoSearchPanelHtml(repos, cur);
  wireRepoSearch();
  const rs = rsState();
  if (rs.q && rs.q.trim().length >= 2) runRepoSearch();
}

// move a textarea caret to a 1-based line and scroll it into view
function jumpEditorToLine(line) {
  const ta = document.getElementById("repo-editor");
  if (!ta || !line) return;
  const lines = ta.value.split("\n");
  const start = lines.slice(0, line - 1).reduce((n, l) => n + l.length + 1, 0);
  const end = start + (lines[line - 1] || "").length;
  ta.focus();
  ta.setSelectionRange(start, end);
  const lh = parseFloat(getComputedStyle(ta).lineHeight) || 18;
  ta.scrollTop = Math.max(0, (line - 3) * lh);
}

async function renderRepos() {
  const data = await api("/api/repos");
  state.reposData = data.repos;   // lets the add panel mark already-defined repos
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
  // per-repo analysis: Engine → dependency matrix; inventories → config view
  const isEngine = (cur.name || "").toLowerCase() === "engine";
  const isInventories = (cur.name || "").toLowerCase() === "inventories";

  const groupsHtml = repoGroupsHtml(data.repos, cur);

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
        <button class="btn btn-danger" id="repo-undefine"
          title="delete from the shared catalog (definition) — nothing is cloned yet; the remote repo in ADO is untouched">🗑 Un-define</button>
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
    // inventories: parsed per-project config view (teams, apps, envs, hosts, vars)
    let invHtml = "";
    if (isInventories && state.invOpen) {
      if (!state.invData || state.invRefresh) {
        try {
          state.invData = await api(`/api/inventory${state.invRefresh ? "?refresh=true" : ""}`);
        } catch (e) { state.invData = { error: e.message }; }
        state.invRefresh = false;
      }
      invHtml = `<div class="deps-embed inv-embed" id="inv-embed">${state.invData.error
        ? `<div class="panel"><div class="empty">⚠ ${esc(state.invData.error)}</div></div>`
        : invPanelHtml(state.invData)}</div>`;
    }
    const histPath = state.historyScope === "file" && state.repoFile ? state.repoFile : "";
    const [treeData, fileData, diffData, agentLogData, remoteData, histData, branchesData] = await Promise.all([
      api(`/api/repos/${cur.slot}/tree?path=${encodeURIComponent(state.repoPath || "")}`),
      state.repoFile ? api(`/api/repos/${cur.slot}/file?path=${encodeURIComponent(state.repoFile)}`).catch((e) => ({ error: e.message })) : null,
      state.repoFile ? api(`/api/repos/${cur.slot}/diff?path=${encodeURIComponent(state.repoFile)}`).catch(() => ({ diff: "" })) : null,
      api(`/api/repos/${cur.slot}/agent/log`).catch(() => ({ log: [] })),
      api(`/api/repos/${cur.slot}/remote`).catch(() => null),
      state.historyOpen ? api(`/api/repos/${cur.slot}/history?path=${encodeURIComponent(histPath)}`)
        .catch((e) => ({ commits: [], error: e.message })) : null,
      state.branchesOpen ? api(`/api/repos/${cur.slot}/branches`)
        .catch((e) => ({ error: e.message, branches: [] })) : null,
    ]);
    state.agentLog = agentLogData;
    // when the branches view is open, pick sane default base/compare per repo
    if (state.branchesOpen && branchesData && !branchesData.error) {
      if (state.branchSlot !== cur.slot) {
        state.branchSlot = cur.slot;
        state.branchBase = branchesData.current || (branchesData.branches[0] || {}).name || "";
        state.branchCompare = (branchesData.branches.find((b) => b.name !== state.branchBase) || {}).name || "";
        state.branchDelta = null;
      }
    }

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
          ${cur.size_h ? ` · <span title="on-disk size (working tree + .git)">💾 ${esc(cur.size_h)}</span>` : ""}
          ${cur.branch_count ? ` · 🌿 ${cur.branch_count} branch${cur.branch_count === 1 ? "" : "es"}` : ""}
          ${cur.dirty ? ` · <span class="pct-warn">${cur.dirty} locally modified</span>` : ""}</span>
        <button class="btn btn-sm ${state.scanOpen ? "btn-primary" : ""}" id="repo-scan">🔬 Tech scan</button>
        ${isEngine ? `<button class="btn btn-sm ${state.depsOpen ? "btn-primary" : ""}" id="repo-deps" title="pipelines → playbooks / roles / scripts">⛓ Dependencies</button>` : ""}
        ${isInventories ? `<button class="btn btn-sm ${state.invOpen ? "btn-primary" : ""}" id="repo-inv" title="per-project apps, teams, envs, hosts &amp; vars">🧭 Configurations</button>` : ""}
        <button class="btn btn-sm ${state.branchesOpen ? "btn-primary" : ""}" id="repo-branches" title="list branches and compare deltas">🌿 Branches</button>
        <button class="btn btn-sm ${state.historyOpen ? "btn-primary" : ""}" id="repo-history">🕘 History</button>
        <button class="btn btn-sm" id="repo-pull" title="fetch the server copy and move your workspace to it">⟳ Sync</button>
        <button class="btn btn-sm btn-danger" id="repo-discard">Discard my edits</button>
        <button class="btn btn-sm btn-danger" id="repo-remove"
          title="remove ONLY your workspace (worktree + local edits) — the shared clone, catalog, other members &amp; ADO are kept">✖ Remove mine</button>
        <button class="btn btn-sm" id="repo-undefine"
          title="delete from the shared catalog for ALL members (definition + clone) — the remote repo in ADO is untouched">🗑 Un-define</button>
      </div>
      <div id="remote-banner">${remoteBannerHtml(remoteData)}</div>
      ${scanHtml}
      ${depsHtml}
      ${invHtml}
      ${state.branchesOpen ? branchesPanelHtml(branchesData) : ""}
      ${state.historyOpen ? historyPanelHtml(histData) : ""}
      <div class="repo-grid">
        <div class="panel tree-panel">${up}${items}</div>
        <div class="panel editor-panel">${editor}</div>
      </div>`;
  }

  const anyCloned = data.repos.some((r) => r.cloned);
  view().innerHTML = `
    ${headHtml}
    ${addPanel}
    ${anyCloned ? repoSearchPanelHtml(data.repos, cur) : ""}
    <div class="repo-groups">${groupsHtml}</div>
    ${body}
    ${cur.cloned ? agentPanelHtml(cur, state.agentLog) : ""}`;
  wireRepoAdd();
  if (anyCloned) wireRepoSearch();
  if (state.repoJumpLine) { jumpEditorToLine(state.repoJumpLine); state.repoJumpLine = null; }

  view().querySelectorAll("[data-repo]").forEach((b) => b.onclick = () => {
    state.repoSlot = parseInt(b.dataset.repo, 10);
    state.repoPath = ""; state.repoFile = null;
    renderRepos();
  });
  view().querySelectorAll(".repo-clone-all").forEach((b) => b.onclick = async () => {
    const collection = b.dataset.cloneColl, project = b.dataset.cloneProj;
    b.disabled = true; b.textContent = "⏳ cloning…";
    try {
      const res = await api("/api/repos/clone-project", { method: "POST", body: { collection, project } });
      const bits = [`${res.cloned_count} cloned`];
      if (res.skipped_count) bits.push(`${res.skipped_count} already`);
      if (res.error_count) bits.push(`${res.error_count} failed`);
      toast(`⛁ ${esc(project)}: ${bits.join(" · ")}`, res.error_count ? "toast-err" : "toast-xp");
      (res.errors || []).forEach((e) => oops(`${e.name}: ${e.error}`));
      renderRepos();
    } catch (e) { oops(e); b.disabled = false; b.textContent = "⬇ Clone all"; }
  });
  view().querySelectorAll(".repo-remove-mine").forEach((b) => b.onclick = async (e) => {
    e.preventDefault(); e.stopPropagation();
    const collection = b.dataset.rmColl, project = b.dataset.rmProj;
    const label = project ? `project “${project}”` : `collection “${collection || "(no collection)"}”`;
    if (!confirm(`Remove ONLY your workspace across ${label}?\n\nYour worktrees + local edits there are deleted.\nThe shared clones, the catalog, other members and ADO are untouched — re-openable anytime.`)) return;
    b.disabled = true; b.textContent = "⏳ removing…";
    try {
      const { result } = await api("/api/repos/workspace/remove", { method: "POST", body: { collection, project } });
      toast(`✖ removed my workspace · ${result.removed_count} repo(s)${result.absent_count ? ` · ${result.absent_count} not open` : ""}`, "toast-xp");
      renderRepos();
    } catch (e2) { oops(e2); b.disabled = false; }
  });
  view().querySelectorAll(".repo-undefine-grp").forEach((b) => b.onclick = async (e) => {
    e.preventDefault(); e.stopPropagation();
    const collection = b.dataset.udColl, project = b.dataset.udProj;
    const label = project ? `project “${project}”` : `collection “${collection || "(no collection)"}”`;
    if (!confirm(`Delete ${label} from the SHARED catalog?\n\nThis removes the definitions and shared clones for ALL members.\nThe remote repositories in ADO are untouched.`)) return;
    b.disabled = true; b.textContent = "⏳…";
    try {
      const { result } = await api("/api/repos/undefine", { method: "POST", body: { collection, project } });
      toast(`🗑 removed ${result.removed_count} repo(s) from the catalog`, result.error_count ? "toast-err" : "toast-xp");
      (result.errors || []).forEach((er) => oops(`${er.name}: ${er.error}`));
      state.repoSlot = null; state.scanData = null;
      renderRepos();
    } catch (e2) { oops(e2); b.disabled = false; }
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
  on("repo-branches", () => {
    state.branchesOpen = !state.branchesOpen;
    renderRepos();
  });
  if (cur.cloned && state.branchesOpen) {
    wireBranchPanel(cur.slot);
    // auto-load the delta for the current selection if not already shown
    if (state.branchBase && state.branchCompare &&
        (!state.branchDelta || state.branchDelta.base !== state.branchBase
         || state.branchDelta.compare !== state.branchCompare)) {
      loadBranchDelta(cur.slot);
    } else if (state.branchDelta) {
      const box = document.getElementById("branch-delta");
      if (box) box.innerHTML = branchDeltaHtml(state.branchDelta);
    }
  }
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
  on("repo-inv", () => { state.invOpen = !state.invOpen; renderRepos(); });
  on("inv-refresh", () => { state.invRefresh = true; renderRepos(); });
  if (isInventories && state.invOpen && state.invData && !state.invData.error)
    wireInvPanel();
  on("repo-remove", async () => {
    if (!confirm(`Remove ONLY your workspace for ${cur.name}?\n\nYour worktree + local edits are deleted.\nThe shared clone, the catalog, other members and ADO are untouched — re-openable anytime.`)) return;
    try {
      const { result } = await api(`/api/repos/${cur.slot}/workspace`, { method: "DELETE" });
      toast(result.removed_count ? `✖ removed my workspace for ${esc(cur.name)}` : `nothing to remove — no workspace here`, "toast-xp");
      renderRepos();
    } catch (e) { oops(e); }
  });
  on("repo-undefine", async () => {
    if (!confirm(`Delete ${cur.name} from the SHARED catalog?\n\nThis removes the definition and the shared clone for ALL members.\nThe remote repository in ADO is untouched.`)) return;
    try {
      await api(`/api/repos/${cur.slot}`, { method: "DELETE" });
      toast(`🗑 ${esc(cur.name)} deleted from the catalog`);
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

/* ---- inventories: per-project configuration view ---- */
const INV_PRIMARY = ["dev", "qc", "prd"];               // dev_team / qc_team / prd_team(ops)
const INV_ROLE_LABEL = { dev: "dev", qc: "qc", prd: "prd/ops" };
function invMatch(p, f) {
  const allTeams = [...Object.values(p.teams || {}), ...Object.values(p.other_teams || {})];
  if (f.q) {
    const hay = (p.name + " " + (p.apps || []).join(" ") + " " + allTeams.join(" ")
      + " " + Object.keys(p.vars || {}).join(" ")).toLowerCase();
    if (!hay.includes(f.q.toLowerCase())) return false;
  }
  if (f.team && f.team !== "all" && !allTeams.includes(f.team)) return false;
  if (f.env && f.env !== "all" && !(p.envs || []).includes(f.env)) return false;
  if (f.missing === "yes" && INV_PRIMARY.every((r) => (p.teams || {})[r])) return false;
  return true;
}

/* ---- inventory config: value grids, four-layer view, project/app diff ---- */
const INV_SCOPE_LABEL = { project: "project", app: "app", env_app: "env+app", host: "host" };
const invScopeCls = (o) =>
  o === "env_app" ? "envapp" : o === "project" ? "proj" : o; // app/host keep their name

// a compact key→value grid (sorted); vault-encrypted scopes show a lock, never values
function invGrid(dict, opts) {
  opts = opts || {};
  const entries = Object.entries(dict || {}).filter(([k]) => k !== "__vault__");
  const lock = (dict || {}).__vault__
    ? '<span class="chip chip-amber" title="ansible-vault — encrypted, never decrypted">🔒 encrypted</span>' : "";
  if (!entries.length) return `<div class="inv-empty">${lock || (opts.empty || "no variables")}</div>`;
  entries.sort((a, b) => a[0].localeCompare(b[0]));
  const rows = entries.map(([k, v]) =>
    `<div class="inv-kv"><code class="inv-k">${esc(k)}</code>
      <span class="inv-v">${v === "" ? '<span class="inv-null">—</span>' : esc(v)}</span></div>`).join("");
  return `<div class="inv-grid">${rows}</div>${lock ? `<div class="inv-lock-note">${lock}</div>` : ""}`;
}

// env overlays (group_vars/<env>_<app>) belonging to one app → [[env, dict], …]
function invAppOverlays(c, app) {
  const suf = `_${app}`;
  return Object.entries(c.env_app_vars || {})
    .filter(([g]) => g.length > suf.length && g.slice(g.length - suf.length) === suf)
    .map(([g, d]) => [g.slice(0, g.length - suf.length), d])
    .sort((a, b) => a[0].localeCompare(b[0]));
}

// the four layers of one project, values shown, laid out by scope
function invLayersHtml(p) {
  const c = p.config || {};
  const proj = `
    <div class="inv-layer">
      <div class="inv-layer-head"><span class="inv-scope proj">project</span>
        <span class="ci-meta">group_vars/all · inherited by every app</span></div>
      ${invGrid(c.project_vars, { empty: "no project variables" })}
    </div>`;
  const apps = (p.apps || []).map((a) => {
    const overlays = invAppOverlays(c, a);
    const ov = overlays.map(([env, d]) => `
      <div class="inv-overlay">
        <div class="inv-overlay-head"><span class="inv-scope envapp">${esc(env)}</span>
          <span class="ci-meta">group_vars/${esc(env)}_${esc(a)}</span></div>
        ${invGrid(d)}
      </div>`).join("");
    return `
      <details class="inv-applayer">
        <summary><span class="inv-scope app">app</span> <b>${esc(a)}</b>
          <span class="ci-meta">group_vars/${esc(a)}${overlays.length ? ` · ${overlays.length} env overlay${overlays.length === 1 ? "" : "s"}` : ""}</span></summary>
        <div class="inv-applayer-body">
          ${invGrid((c.app_vars || {})[a], { empty: "no app-level variables" })}${ov}</div>
      </details>`;
  }).join("") || '<div class="inv-empty">no apps</div>';
  const hostEntries = Object.entries(c.host_vars || {});
  const hosts = hostEntries.length ? `
    <div class="inv-layer">
      <div class="inv-layer-head"><span class="inv-scope host">host</span>
        <span class="ci-meta">host_vars · environment-specific</span></div>
      ${hostEntries.map(([h, d]) => `
        <div class="inv-overlay">
          <div class="inv-overlay-head"><span class="chip">🖥 ${esc(h)}</span></div>
          ${invGrid(d, { empty: "no host variables" })}
        </div>`).join("")}
    </div>` : "";
  return `<div class="inv-layers">${proj}
    <div class="inv-applayers">
      <div class="inv-layer-head"><span class="inv-scope app">apps</span>
        <span class="ci-meta">group_vars/&lt;app&gt; + env overlays</span></div>${apps}</div>
    ${hosts}</div>`;
}

// merge layers low→high, remembering which layer set each key (its origin)
function invEffective(pairs) {
  const map = {};
  let vaulted = false;
  for (const [dict, origin] of pairs) {
    if (!dict) continue;
    for (const [k, v] of Object.entries(dict)) {
      if (k === "__vault__") { vaulted = true; continue; }
      map[k] = { value: v, origin };
    }
  }
  return { map, vaulted };
}

function invProjectEntity(p) {
  return { label: p.name, sub: "group_vars/all",
    ...invEffective([[(p.config || {}).project_vars, "project"]]) };
}

// Ansible-style effective config for one app at one env: project ⊕ app ⊕ env_app ⊕ host
function invAppEntity(p, app, env) {
  const c = p.config || {};
  const pairs = [[c.project_vars, "project"], [(c.app_vars || {})[app], "app"]];
  if (env && env !== "(base)") {
    pairs.push([(c.env_app_vars || {})[`${env}_${app}`], "env_app"]);
    Object.entries(c.host_vars || {}).forEach(([h, hv]) => {
      if (h.split("_")[0] === env) pairs.push([hv, "host"]);
    });
  }
  return { label: `${p.name} / ${app}`,
    sub: env && env !== "(base)" ? `effective @ ${env}` : "project + app", ...invEffective(pairs) };
}

function invDiff(A, B) {
  const keys = [...new Set([...Object.keys(A.map), ...Object.keys(B.map)])].sort();
  const rows = keys.map((k) => {
    const a = A.map[k], b = B.map[k];
    const status = a && b ? (a.value === b.value ? "same" : "diff") : (a ? "onlyA" : "onlyB");
    return { key: k, a, b, status };
  });
  const n = (st) => rows.filter((r) => r.status === st).length;
  return { rows, diff: n("diff"), onlyA: n("onlyA"), onlyB: n("onlyB"), same: n("same") };
}

function invCompareHtml(d) {
  const projects = d.projects || [];
  const cmp = state.invCmp = state.invCmp
    || { kind: "project", a: "", b: "", env: "(base)", diffOnly: true };

  const appList = projects.flatMap((p) =>
    (p.apps || []).map((a) => ({ project: p.name, app: a, label: `${p.name} / ${a}` })));
  const opts = cmp.kind === "project"
    ? projects.map((p) => [p.name, p.name])
    : appList.map((x, i) => [String(i), x.label]);
  if (!opts.find(([v]) => v === cmp.a)) cmp.a = (opts[0] || [""])[0];
  if (!opts.find(([v]) => v === cmp.b)) cmp.b = (opts[1] || opts[0] || [""])[0];

  const projOf = (id) => cmp.kind === "app"
    ? projects.find((p) => p.name === (appList[+id] || {}).project) : null;
  const resolve = (id) => {
    if (cmp.kind === "project") {
      const p = projects.find((x) => x.name === id);
      return p ? invProjectEntity(p) : null;
    }
    const x = appList[+id];
    const p = x && projects.find((pp) => pp.name === x.project);
    return p ? invAppEntity(p, x.app, cmp.env) : null;
  };

  const kindTabs = `<div class="inv-cmp-kind">
    <button class="btn btn-sm ${cmp.kind === "project" ? "btn-primary" : ""}" data-inv-kind="project">📁 projects</button>
    <button class="btn btn-sm ${cmp.kind === "app" ? "btn-primary" : ""}" data-inv-kind="app">🧩 apps</button></div>`;

  if (opts.length < 2)
    return `<div class="inv-cmp">${kindTabs}<div class="empty">need at least two ${cmp.kind}s to compare</div></div>`;

  let envBar = "";
  if (cmp.kind === "app") {
    const envs = [...new Set([...(projOf(cmp.a) || {}).envs || [], ...(projOf(cmp.b) || {}).envs || []])].sort();
    if (!["(base)", ...envs].includes(cmp.env)) cmp.env = "(base)";
    envBar = `<div class="inv-cmp-envbar"><span class="ci-meta">environment</span>
      ${["(base)", ...envs].map((e) => `<button class="btn btn-sm ${cmp.env === e ? "btn-primary" : ""}" data-inv-env="${esc(e)}">${e === "(base)" ? "base" : esc(e)}</button>`).join("")}
      <span class="ci-meta inv-cmp-envhint">${cmp.env === "(base)" ? "project + app layers" : "project ⊕ app ⊕ env-app ⊕ host (effective)"}</span></div>`;
  }

  const sel = (id, cur) => `<select id="${id}">${opts.map(([v, l]) =>
    `<option value="${esc(v)}" ${v === cur ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>`;
  const A = resolve(cmp.a), B = resolve(cmp.b);
  if (!A || !B) return `<div class="inv-cmp">${kindTabs}<div class="empty">pick two ${cmp.kind}s</div></div>`;

  const diff = invDiff(A, B);
  const summary = `
    <div class="inv-cmp-summary">
      <span class="inv-cmp-stat diff"><b>${diff.diff}</b> differ</span>
      <span class="inv-cmp-stat onlyA"><b>${diff.onlyA}</b> only in A</span>
      <span class="inv-cmp-stat onlyB"><b>${diff.onlyB}</b> only in B</span>
      <span class="inv-cmp-stat same"><b>${diff.same}</b> identical</span>
      <span class="spacer"></span>
      <label class="inv-cmp-diffonly"><input type="checkbox" id="inv-cmp-diffonly" ${cmp.diffOnly ? "checked" : ""}> only differences</label>
    </div>`;

  const badge = (o) => `<span class="inv-scope ${invScopeCls(o.origin)}" title="value comes from the ${INV_SCOPE_LABEL[o.origin]} layer">${INV_SCOPE_LABEL[o.origin]}</span>`;
  const cell = (o) => o
    ? `<div class="inv-cmp-cell"><span class="inv-v">${o.value === "" ? '<span class="inv-null">—</span>' : esc(o.value)}</span>${badge(o)}</div>`
    : '<div class="inv-cmp-cell missing"><span class="inv-null">absent</span></div>';
  const icon = { same: "=", diff: "≠", onlyA: "◑", onlyB: "◐" };
  const rows = cmp.diffOnly ? diff.rows.filter((r) => r.status !== "same") : diff.rows;
  const table = rows.length ? rows.map((r) => `
    <div class="inv-cmp-row ${r.status}">
      <span class="inv-cmp-status" title="${r.status}">${icon[r.status]}</span>
      <code class="inv-cmp-key">${esc(r.key)}</code>
      ${cell(r.a)}${cell(r.b)}
    </div>`).join("")
    : `<div class="inv-empty">${cmp.diffOnly ? "no differences — configured identically ✓" : "no variables"}</div>`;
  const heads = `
    <div class="inv-cmp-row inv-cmp-heads">
      <span class="inv-cmp-status"></span><span class="inv-cmp-key ci-meta">variable</span>
      <span class="inv-cmp-cell inv-cmp-head"><span class="inv-cmp-tag a">A</span> <b>${esc(A.label)}</b> <span class="ci-meta">${esc(A.sub)}</span>${A.vaulted ? " 🔒" : ""}</span>
      <span class="inv-cmp-cell inv-cmp-head"><span class="inv-cmp-tag b">B</span> <b>${esc(B.label)}</b> <span class="ci-meta">${esc(B.sub)}</span>${B.vaulted ? " 🔒" : ""}</span>
    </div>`;

  return `<div class="inv-cmp">
    <div class="inv-cmp-bar">${kindTabs}
      <span class="inv-cmp-pick"><span class="inv-cmp-tag a">A</span>${sel("inv-cmp-a", cmp.a)}
        <button class="btn btn-sm" id="inv-cmp-swap" title="swap A and B">⇄</button>
        <span class="inv-cmp-tag b">B</span>${sel("inv-cmp-b", cmp.b)}</span></div>
    ${envBar}${summary}
    <div class="inv-cmp-table">${heads}${table}</div></div>`;
}

function invPanelHtml(d) {
  const s = d.summary || {};
  const f = state.invFilter = state.invFilter || {};
  const projects = d.projects || [];
  const allTeams = [...new Set(projects.flatMap((p) =>
    [...Object.values(p.teams || {}), ...Object.values(p.other_teams || {})]))].sort();
  const allEnvs = [...new Set(projects.flatMap((p) => p.envs || []))].sort();
  const filtering = !!(f.q || (f.team && f.team !== "all") || (f.env && f.env !== "all") || f.missing === "yes");
  const shown = projects.filter((p) => invMatch(p, f));

  const tile = (n, label, cls) => `<div class="stat-tile"><b class="${cls || ""}">${n}</b><span>${label}</span></div>`;
  const tiles = `<div class="stat-tiles" style="margin:6px 0 12px">
    ${tile(s.projects || 0, "projects")}
    ${tile(s.apps || 0, "apps")}
    ${tile(s.hosts || 0, "hosts")}
    ${tile(s.vault_files || 0, "vault files")}
    ${tile(s.distinct_teams || 0, "distinct teams")}
    ${tile(s.missing_primary || 0, "missing a primary team", (s.missing_primary ? "pct-bad" : "pct-good"))}</div>`;
  const teamOverview = (s.teams || []).length ? `
    <details class="filebox" style="margin-bottom:8px"><summary>👥 teams across the inventory (${s.teams.length})</summary>
      <div class="inv-chips" style="padding:8px 12px">${s.teams.map((t) =>
        `<span class="chip chip-cyan" title="${t.usages} usage(s)">${esc(t.name)} · ${t.usages}</span>`).join(" ")}</div>
    </details>` : "";
  const sel = (id, cur, opts) => `<select data-inv-filter="${id}">${opts.map(([v, l]) =>
    `<option value="${esc(v)}" ${(cur || "all") === v ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>`;
  const filterBar = `<div class="acc-filters">
    <input id="inv-q" placeholder="🔎 project / app / team / var…" value="${esc(f.q || "")}">
    ${sel("team", f.team, [["all", "team: any"], ...allTeams.map((t) => [t, t])])}
    ${sel("env", f.env, [["all", "env: any"], ...allEnvs.map((e) => [e, e])])}
    ${sel("missing", f.missing, [["all", "primary teams: any"], ["yes", "missing a primary team"]])}
    ${filtering ? '<button class="btn btn-sm" id="inv-filter-clear">✕ clear</button>' : ""}</div>`;

  const teamCell = (p, role) => {
    const t = (p.teams || {})[role];
    const lbl = INV_ROLE_LABEL[role] || role;
    return t ? `<span class="chip chip-green" title="${role}_team">${lbl}: ${esc(t)}</span>`
      : `<span class="chip chip-red" title="${role}_team not defined">${lbl}: —</span>`;
  };
  const cards = shown.map((p) => `
    <details class="filebox inv-proj" ${filtering ? "open" : ""}>
      <summary>📁 <b>${esc(p.name)}</b>
        <span class="inv-chips">${INV_PRIMARY.map((r) => teamCell(p, r)).join(" ")}
          ${Object.entries(p.other_teams || {}).map(([r, t]) => `<span class="chip" title="${esc(r)}_team">${esc(r)}: ${esc(t)}</span>`).join(" ")}</span>
        <span class="ci-meta">${p.app_count} app(s) · ${(p.envs || []).length} env(s) · ${p.host_count} host(s)${p.vault_files ? ` · 🔒 ${p.vault_files} vault` : ""}</span></summary>
      <div style="padding:8px 12px">
        <div class="acc-h">apps</div>
        <div class="inv-chips">${(p.apps || []).map((a) => `<span class="chip">${esc(a)}</span>`).join(" ") || '<span class="ci-meta">none</span>'}</div>
        ${(p.envs || []).length ? `<div class="acc-h" style="margin-top:8px">environments</div>
          <div class="inv-chips">${p.envs.map((e) => `<span class="chip chip-amber">${esc(e)}</span>`).join(" ")}</div>` : ""}
        ${(p.hosts || []).length ? `<div class="acc-h" style="margin-top:8px">hosts</div>
          ${p.hosts.map((h) => `<div class="ci-row"><span class="ci-job">🖥 ${esc(h.host)}</span>
            ${h.vars ? '<span class="chip chip-green">vars</span>' : ""}
            ${h.vault ? '<span class="chip chip-amber" title="ansible-vault — encrypted, not decrypted">🔒 vault</span>' : ""}</div>`).join("")}` : ""}
        <details class="filebox inv-cfg-box" style="margin-top:10px">
          <summary>🧩 configuration layers — values by scope (project · app · env-app · host)</summary>
          <div style="padding:8px 12px">${invLayersHtml(p)}</div>
        </details>
      </div>
    </details>`).join("") || '<div class="empty">no projects match the filters</div>';

  const view_ = state.invView = state.invView || "browse";
  const head = `<div class="deps-embed-head"><h2 style="margin:0">🧭 inventory configurations
      <span class="ci-meta">${esc(d.source)}${d.cached ? " · cached" : ""}${view_ === "browse" && filtering ? ` · ${shown.length} of ${projects.length}` : ""}</span></h2>
      <span class="spacer"></span>
      <div class="inv-viewtabs">
        <button class="btn btn-sm ${view_ === "browse" ? "btn-primary" : ""}" id="inv-view-browse" title="browse every project's four config layers">🗂 Browse</button>
        <button class="btn btn-sm ${view_ === "compare" ? "btn-primary" : ""}" id="inv-view-compare" title="diff two projects or two apps to catch config mismatches">⇄ Compare</button>
      </div>
      <button class="btn btn-sm" id="inv-refresh">↻ re-analyze</button></div>
    ${d.note ? `<div class="kpi-note">${esc(d.note)}</div>` : ""}`;

  if (view_ === "compare")
    return `${head}${invCompareHtml(d)}`;
  return `${head}${tiles}${teamOverview}${filterBar}${cards}`;
}

function rerenderInv() {
  const box = document.getElementById("inv-embed");
  if (box && state.invData && !state.invData.error) { box.innerHTML = invPanelHtml(state.invData); wireInvPanel(); }
}

function wireInvPanel() {
  const f = state.invFilter = state.invFilter || {};
  view().querySelectorAll("[data-inv-filter]").forEach((el) =>
    el.onchange = () => { f[el.dataset.invFilter] = el.value; rerenderInv(); });
  const q = document.getElementById("inv-q");
  if (q) q.oninput = () => {
    f.q = q.value;
    clearTimeout(state._invT);
    state._invT = setTimeout(() => {
      rerenderInv();
      const nq = document.getElementById("inv-q");
      if (nq) { nq.focus(); nq.setSelectionRange(nq.value.length, nq.value.length); }
    }, 200);
  };
  const cb = document.getElementById("inv-filter-clear");
  if (cb) cb.onclick = () => { state.invFilter = {}; rerenderInv(); };
  const rb = document.getElementById("inv-refresh");
  if (rb) rb.onclick = () => { state.invRefresh = true; renderRepos(); };

  // view tabs (browse / compare)
  const vb = document.getElementById("inv-view-browse");
  if (vb) vb.onclick = () => { state.invView = "browse"; rerenderInv(); };
  const vc = document.getElementById("inv-view-compare");
  if (vc) vc.onclick = () => { state.invView = "compare"; rerenderInv(); };

  // compare controls
  view().querySelectorAll("[data-inv-kind]").forEach((b) => b.onclick = () => {
    if (state.invCmp) { state.invCmp.kind = b.dataset.invKind; state.invCmp.a = ""; state.invCmp.b = ""; }
    rerenderInv();
  });
  view().querySelectorAll("[data-inv-env]").forEach((b) => b.onclick = () => {
    if (state.invCmp) state.invCmp.env = b.dataset.invEnv; rerenderInv();
  });
  const ca = document.getElementById("inv-cmp-a");
  if (ca) ca.onchange = () => { state.invCmp.a = ca.value; rerenderInv(); };
  const cbx = document.getElementById("inv-cmp-b");
  if (cbx) cbx.onchange = () => { state.invCmp.b = cbx.value; rerenderInv(); };
  const sw = document.getElementById("inv-cmp-swap");
  if (sw) sw.onclick = () => {
    const t = state.invCmp.a; state.invCmp.a = state.invCmp.b; state.invCmp.b = t; rerenderInv();
  };
  const dof = document.getElementById("inv-cmp-diffonly");
  if (dof) dof.onchange = () => { state.invCmp.diffOnly = dof.checked; rerenderInv(); };
}

/* ================= LOGGING HEALTH ================= */
function logInt(n) { return (n || 0).toLocaleString(); }
function logHsize(n) {
  let f = n || 0;
  for (const u of ["B", "KB", "MB", "GB", "TB"]) {
    if (f < 1024 || u === "TB") return (u === "B" ? Math.round(f) : f.toFixed(1)) + " " + u;
    f /= 1024;
  }
}
function logAgo(h) {
  if (h == null) return "—";
  if (h < 1) return "just now";
  if (h < 48) return Math.round(h) + "h ago";
  return Math.round(h / 24) + "d ago";
}
const logSrcChip = (s) => `<span class="chip ${s === "prd" ? "chip-violet" : "chip-cyan"}" title="${esc(s)} Elasticsearch connection">${esc(s)}</span>`;

function logConnBar(conns) {
  const one = (kind, label) => {
    const c = (conns || {})[kind];
    const placeholder = kind === "nonprd";
    if (!c || !c.configured)
      return `<div class="log-conn off">
        <span class="chip ${placeholder ? "chip-amber" : "chip-red"}">${label}${placeholder ? " · not configured" : " · off"}</span>
        <span class="ci-meta">${esc((c && c.note) || (placeholder
          ? "set QO_ES_NONPRD_URL / QO_ES_NONPRD_API_KEY to include dev/qc/uat logs"
          : "set QO_ES_URL / QO_ES_API_KEY"))}</span></div>`;
    if (!c.reachable)
      return `<div class="log-conn bad"><span class="chip chip-red">${label} · unreachable</span>
        <span class="ci-meta">${esc(c.error || "")}</span></div>`;
    return `<div class="log-conn ok"><span class="chip chip-green">${label} ✓</span>
      <span class="ci-meta">${logInt(c.indices)} indices${c.url ? " · " + esc(c.url) : ""}${
        c.unexpected_envs ? ` · <span class="pct-warn">⚠ unexpected env here: ${esc(c.unexpected_envs.join(", "))}</span>` : ""}</span></div>`;
  };
  return `<div class="log-conns">${one("prd", "prd ES")}${one("nonprd", "non-prd ES")}</div>`;
}

// inventory-parse diagnostics — what QuestOps could extract from `inventories`
// (source, projects, apps, envs, per-project deploy_platform → prefix) + the
// logtypes actually seen in ES. Mirrors the Repositories inventory panel so a
// blank/partial page is debuggable.
function logDiagHtml(d) {
  const g = d.diagnostics;
  if (!g) return "";
  const chips = (arr, cls) => (arr || []).length
    ? arr.map((x) => `<span class="chip ${cls || ""}">${esc(x)}</span>`).join(" ")
    : '<span class="ci-meta">none detected</span>';
  const rows = (g.project_platforms || []).map((pp) => {
    const proj = pp.deploy_platform
      ? `<span class="chip chip-cyan" title="project-global deploy_platform (group_vars/all)">project: ${esc(pp.deploy_platform)} → ${esc(pp.prefix || "?")}</span>`
      : '<span class="chip" title="no project-wide deploy_platform (group_vars/all)">project: —</span>';
    const over = (pp.app_overrides || []).map((o) =>
      `<span class="chip ${o.discrepancy ? "chip-red" : "chip-cyan"}" title="app-specific deploy_platform (group_vars/${esc(o.app)})${o.discrepancy ? " — CLASHES with project global" : ""}">${esc(o.app)}: ${esc(o.platform)} → ${esc(o.prefix || "?")}${o.discrepancy ? " ⚠" : ""}</span>`).join(" ");
    const unres = (pp.unresolved_apps || []).length
      ? `<span class="chip chip-red" title="no deploy_platform resolved (app or project): ${esc((pp.unresolved_apps || []).join(", "))}">${pp.unresolved_apps.length} app(s) unresolved</span>` : "";
    return `<div class="log-diag-row">
      <span class="ci-job">📁 ${esc(pp.project)}</span>
      ${proj} ${over} ${unres}
      <span class="ci-meta">${pp.apps} app(s) · envs ${(pp.envs || []).join(", ") || "—"}</span>
    </div>`;
  }).join("") || '<div class="ci-meta">no projects parsed from inventories</div>';
  const logtypes = (d.summary || {}).logtypes || [];
  const issues = !(g.prefixes || []).length || !g.projects
    || Object.values(d.connections || {}).some((c) => c && (!c.configured || !c.reachable));
  const mapChips = Object.entries(g.platform_map || {})
    .map(([k, v]) => `<span class="chip">${esc(k)} → ${esc(v)}</span>`).join(" ");
  return `<details class="filebox log-diag" ${issues ? "open" : ""}>
    <summary>🔎 inventory parse &amp; detection${(g.prefixes || []).length ? "" : ' — <span class="pct-bad">⚠ no prefixes resolved</span>'}</summary>
    <div class="log-diag-body">
      <div class="log-diag-facts">
        <div><span class="acc-h">inventories source</span><div class="ci-meta">${esc(g.inventory_source || "—")}${g.inventory_note ? ` · ${esc(g.inventory_note)}` : ""}</div></div>
        <div><span class="acc-h">projects (${g.projects})</span><div class="inv-chips">${chips((g.project_platforms || []).map((p) => p.project))}</div></div>
        <div><span class="acc-h">apps (${(g.apps || []).length})</span><div class="inv-chips">${chips(g.apps)}</div></div>
        <div><span class="acc-h">environments</span><div class="inv-chips">${chips(g.envs, "chip-amber")}</div></div>
        <div><span class="acc-h">prefixes resolved</span><div class="inv-chips">${chips(g.prefixes, "chip-cyan")}</div></div>
        <div><span class="acc-h">logtypes (live from ES)</span><div class="inv-chips">${chips(logtypes, "chip-cyan")}</div></div>
      </div>
      <div class="acc-h" style="margin-top:8px">per-project deploy_platform → index prefix <span class="ci-meta">(map: ${mapChips})</span></div>
      <div class="log-diag-rows">${rows}</div>
    </div>
  </details>`;
}

// health score → grade class + badge (0–100; higher = healthier)
function logScoreClass(s) {
  if (s == null) return "na";
  if (s >= 90) return "a";
  if (s >= 70) return "b";
  if (s >= 40) return "c";
  return "f";
}
function logScoreBadge(s, label) {
  return `<span class="log-score ${logScoreClass(s)}" title="${esc(label || "health score")} — 0–100 from current issues">${s == null ? "n/a" : s}</span>`;
}
const LOG_ISSUE_LABEL = {
  no_logs: "no logs", stale: "stale", timestamp: "@timestamp not date",
  bad_week: "bad year", future_week: "future-dated", over_retained: "over-retained",
  over_sized: "over-sized storage",
  clash: "platform clash", team_clash: "owner clash", unsupported: "unsupported platform",
};

function logTsBadge(a) {
  if (!a.indices) return "";
  if (a.ts_ok) return `<span class="chip chip-green" title="@timestamp is a proper date in all ${a.indices} index(es)">🕓 date ✓</span>`;
  const kinds = Object.keys(a.ts_types || {}).filter((t) => t !== "date");
  const bad = (a.ts_bad_indices || []).length;
  return `<span class="chip chip-red" title="@timestamp is ${esc(kinds.join("/") || "not a date")} in ${bad} index(es) — range/time filters silently match nothing there">🕓 not date · ${bad}</span>`;
}

function logAppMatch(a, f) {
  if (f.q) {
    const hay = (a.app + " " + (a.project || "") + " " + (a.deploy_platform || "") + " "
      + (a.deploy_technology || "") + " " + (a.company || "") + " "
      + (a.envs || []).join(" ") + " " + (a.logtypes || []).join(" ")).toLowerCase();
    if (!hay.includes(f.q.toLowerCase())) return false;
  }
  if (f.project && f.project !== "all" && a.project !== f.project) return false;
  if (f.platform && f.platform !== "all" && (a.deploy_platform || "—") !== f.platform) return false;
  if (f.tech && f.tech !== "all" && (a.deploy_technology || "—") !== f.tech) return false;
  if (f.company && f.company !== "all" && (a.company || "—") !== f.company) return false;
  if (f.logreq === "required" && a.logging_required !== true) return false;
  if (f.logreq === "notrequired" && a.logging_required !== false) return false;
  if (f.logtype && f.logtype !== "all" && !(a.logtypes || []).includes(f.logtype)) return false;
  if (f.env && f.env !== "all"
    && !(a.envs || []).includes(f.env) && !(a.expected_envs || []).includes(f.env)) return false;
  if (f.team && f.team !== "all") {
    const ci = f.team.indexOf(":");
    if (ci > 0) {              // role filter "<env>:<team>" → match that env's owner
      const e = (a.env_stats || []).find((x) => x.env === f.team.slice(0, ci));
      if (!e || e.owner !== f.team.slice(ci + 1)) return false;
    } else if (!(a.owners || []).includes(f.team)) return false;
  }
  if (f.issue && f.issue !== "all") {
    if (f.issue === "any") { if (!(a.issues || []).length) return false; }
    else if (!(a.issues || []).includes(f.issue)) return false;
  }
  if (f.hideNoLogs && a.no_logs) return false;          // exclude apps with no logs
  if (f.hideUnmonitored && !a.monitored) return false;  // exclude unmonitored apps
  if (f.hideUndeployed && !a.deployed) return false;    // exclude never-deployed apps
  return true;
}

// filtering by env/team scopes the app's DATA to the matching environments —
// its env rows, index list, sizes, counts, issues and score are recomputed from
// just those envs (client-side, from data already loaded). No-op when neither
// env nor team is active.
function logScopeApp(a, f) {
  const envA = f.env && f.env !== "all";
  const teamA = f.team && f.team !== "all" && !f.team.includes(":");   // role mode never scopes
  if (!envA && !teamA) return a;
  const es = (a.env_stats || []).filter((e) =>
    (!envA || e.env === f.env) && (!teamA || e.owner === f.team));
  if (!es.length || es.length === (a.env_stats || []).length) return a;
  const uniq = (arr) => [...new Set(arr)];
  const sum = (fn) => es.reduce((n, e) => n + (fn(e) || 0), 0);
  const maxS = (arr) => arr.length ? arr.reduce((m, x) => x > m ? x : m) : null;
  const minS = (arr) => arr.length ? arr.reduce((m, x) => x < m ? x : m) : null;
  const escores = es.map((e) => e.score).filter((x) => x != null);
  const ownerClash = es.filter((e) => e.owner_clash).map((e) => e.env);
  const idxCount = sum((e) => e.indices);
  const bytes = sum((e) => e.size_bytes);
  const issues = uniq([...es.flatMap((e) => e.issues || []),
    ...(a.discrepancy ? ["clash"] : []),
    ...(a.over_sized ? ["over_sized"] : []),
    ...(a.platform_status === "unsupported" ? ["unsupported"] : [])]).sort();
  let score = null;
  if (a.monitored && escores.length) {
    let base = escores.reduce((x, y) => x + y, 0) / escores.length;
    if (a.discrepancy) base -= 10;
    if (ownerClash.length) base -= 10;
    if (a.over_sized) base -= 10;
    score = Math.max(Math.round(base), 0);
  }
  return { ...a, _scoped: true, env_stats: es,
    envs: uniq(es.filter((e) => e.indices).map((e) => e.env)).sort(),
    expected_envs: uniq(es.map((e) => e.env)).sort(),
    indices: idxCount, size_bytes: bytes, size_h: logHsize(bytes), docs: sum((e) => e.docs),
    first_logged: minS(es.map((e) => e.first_logged).filter(Boolean)),
    last_logged: maxS(es.map((e) => e.last_logged).filter(Boolean)),
    last_deploy: maxS(es.map((e) => e.last_deploy).filter(Boolean)),
    owners: uniq(es.map((e) => e.owner).filter(Boolean)).sort(),
    logtypes: uniq(es.flatMap((e) => e.logtypes || [])).sort(),
    sources: uniq(es.flatMap((e) => e.sources || [])).sort(),
    index_list: (a.index_list || []).filter((i) => es.some((e) => e.env === i.env)),
    ts_bad_indices: uniq(es.flatMap((e) => e.ts_bad_indices || [])),
    bad_week_indices: uniq(es.flatMap((e) => e.bad_week_indices || [])),
    future_week_indices: uniq(es.flatMap((e) => e.future_week_indices || [])),
    over_retained_envs: es.filter((e) => e.over_retained).map((e) => e.env),
    owner_clash_envs: ownerClash,
    envs_stale: es.filter((e) => e.stale).length,
    envs_no_logs: es.filter((e) => e.no_logs && e.deployed).length,
    deployed: es.some((e) => e.deployed),
    undeployed_envs: es.filter((e) => !e.deployed).map((e) => e.env),
    no_logs: a.monitored && idxCount === 0 && es.some((e) => e.deployed),
    ts_ok: !es.some((e) => !e.ts_ok && e.indices),
    stale: es.some((e) => e.stale), issues, score };
}

// sorting is CRITERIA + DIRECTION: the selects carry only the criteria, a
// separate ↑/↓ toggle carries the direction. Sensible defaults per criteria:
// score + name ascend (worst / A first), everything else descends (big first).
// No-score / no-logs apps always sink to the END regardless of direction.
const LOG_SORT_LABELS = [["score", "score"], ["size", "size"], ["updated", "last updated"],
  ["docs", "documents"], ["indices", "indices"], ["name", "name"]];
const logDefaultDir = (sort) => (sort === "score" || sort === "name") ? "asc" : "desc";
const LOG_SORT_VAL = {
  score: (a) => a.score, size: (a) => a.size_bytes, updated: (a) => a.last_logged || "",
  docs: (a) => a.docs, indices: (a) => a.indices, name: (a) => a.app.toLowerCase(),
};
const _cmpVals = (va, vb) => (typeof va === "string" || typeof vb === "string")
  ? String(va).localeCompare(String(vb)) : ((va || 0) - (vb || 0));
function logSortApps(apps, sort, dir) {
  const get = LOG_SORT_VAL[sort] || LOG_SORT_VAL.score;
  const mul = ((dir || logDefaultDir(sort)) === "desc") ? -1 : 1;
  const sink = (sort === "score" || !LOG_SORT_VAL[sort])
    ? (a) => (a.score == null ? 1 : 0) : (a) => (a.no_logs ? 1 : 0);
  return apps.slice().sort((a, b) => (sink(a) - sink(b))
    || (mul * _cmpVals(get(a), get(b))) || (b.size_bytes - a.size_bytes));
}

// projects are sorted too (by the same metric, aggregated) — otherwise a sort
// only reorders apps INSIDE collapsed cards and looks like it does nothing
const _plast = (apps) => (apps || []).reduce((m, a) => (a.last_logged || "") > m ? (a.last_logged || "") : m, "");
const LOG_PROJ_VAL = {
  score: (e) => e.score, size: (e) => e.t.size_bytes || 0, docs: (e) => e.t.docs || 0,
  indices: (e) => e.t.indices || 0, updated: (e) => _plast(e.apps),
  name: (e) => e.p.name.toLowerCase(),
};
function logSortProjects(entries, sort, dir) {
  const get = LOG_PROJ_VAL[sort] || LOG_PROJ_VAL.score;
  const mul = ((dir || logDefaultDir(sort)) === "desc") ? -1 : 1;
  return entries.slice().sort((a, b) => {
    const sk = (sort === "score" || !LOG_PROJ_VAL[sort])
      ? ((a.score == null ? 1 : 0) - (b.score == null ? 1 : 0)) : 0;
    return sk || (mul * _cmpVals(get(a), get(b)))
      || ((b.t.size_bytes || 0) - (a.t.size_bytes || 0));
  });
}
// shared ↑/↓ segmented toggle
function logDirBtn(cls, dir, title) {
  return `<button type="button" class="log-dir ${cls}" title="${esc(title || "toggle ascending / descending")}">
    <span class="seg asc ${dir === "asc" ? "active" : ""}">↑</span><span class="seg desc ${dir === "desc" ? "active" : ""}">↓</span></button>`;
}

const logDay = (iso) => iso ? esc(String(iso).slice(0, 10)) : "—";
const logWhen = (iso) => iso ? esc(String(iso).slice(0, 16).replace("T", " ")) : "—";

// per-environment health: owner ($env_team), logged SPAN (first→last), last
// environments are laid out SIDE BY SIDE in the configured order: MAIN_ENVS as
// the primary columns, EXTRA_ENVS shown separately (also side by side).
function logOrderedEnvs(present, d) {
  const eo = (d || state.logData || {}).env_order || {};
  const has = new Set(present);
  const main = (eo.main || []).filter((e) => has.has(e));
  const extra = (eo.extra || []).filter((e) => has.has(e));
  const listed = new Set([...(eo.main || []), ...(eo.extra || [])]);
  const rest = present.filter((e) => !listed.has(e)).sort();
  return { main: [...main, ...rest], extra };
}

// health meter bar (score 0–100 → width + colour)
const logMeter = (score) => `<span class="log-meter" title="health ${score == null ? "n/a" : score}"><span class="log-meter-fill ${logScoreClass(score)}" style="width:${score == null ? 0 : score}%"></span></span>`;

// logging-RATE stats from what's already on an app/env/aggregate: bytes+docs
// over the logged span (first→last). Span clamped to >= 1 DAY so a tiny
// observation window (one fresh 500 KB index) is never extrapolated into a
// fantasy daily rate — for sub-day spans "per day" = what was actually logged.
function logRates(size, docs, first, last) {
  if (!size || !first || !last) return null;
  let days = (new Date(last) - new Date(first)) / 86400000;
  if (!isFinite(days) || days > 365 * 15) return null;   // poisoned span (junk @timestamp docs) — no rate beats a wrong rate
  days = Math.max(days, 1);
  return {
    days,
    size_day_h: logHsize(Math.round(size / days)),
    docs_day: docs ? Math.round(docs / days) : 0,
    doc_avg_h: docs ? logHsize(Math.round(size / docs)) : null,
  };
}
// one compact "📈 rate" line used by the drawer env sections + env dive
function logRateLine(size, docs, first, last) {
  const r = logRates(size, docs, first, last);
  if (!r) return "";
  return `<span class="ci-meta" title="ingest rate over the logged span (${r.days < 1.5 ? "~" + Math.round(r.days * 24) + "h" : Math.round(r.days) + " days"})">📈 ≈<b>${esc(r.size_day_h)}</b>/day · ${logInt(r.docs_day)} docs/day${r.doc_avg_h ? ` · avg doc ${esc(r.doc_avg_h)}` : ""}</span>`;
}

// ---- integrated apps × environments MATRIX --------------------------------
// One grid: rows = apps, columns = environments (MAIN_ENVS then EXTRA_ENVS).
// The header row is the project's per-env overview; each app row is a compact
// health cell per env; clicking an app row dives into its detail (aligned under
// the same columns); clicking an env header focuses that environment.
let _logAppSeq = 0;   // per-render app id counter (HTML-safe keys for lazy detail)
let _logMxSeq = 0;    // per-render matrix id counter (env-dive lookups)
const LOG_ISSUE_SEV = { no_logs: "bad", stale: "warn", timestamp: "bad",
  bad_week: "bad", future_week: "bad", over_retained: "warn", over_sized: "warn", team_clash: "bad", clash: "bad" };
// labeled issue micro-chips (a reader shouldn't have to decode mystery dots);
// at most `cap` shown, the rest folded into a "+n" with a tooltip
function logIssueChips(issues, cap = 2) {
  const list = issues || [];
  if (!list.length) return "";
  const shown = list.slice(0, cap).map((k) =>
    `<span class="log-mxi ${LOG_ISSUE_SEV[k] || "bad"}">${esc(LOG_ISSUE_LABEL[k] || k)}</span>`).join("");
  const more = list.length > cap
    ? `<span class="log-mxi more" title="${esc(list.slice(cap).map((k) => LOG_ISSUE_LABEL[k] || k).join(", "))}">+${list.length - cap}</span>` : "";
  return `<div class="log-mx-cissues">${shown}${more}</div>`;
}

// app-level config flags shown on the app (row) cell
function logAppFlags(a) {
  const b = [];
  const unsupported = a.platform_status === "unsupported";
  if (a.deploy_platform) b.push(`<span class="chip ${unsupported ? "chip-amber" : "chip-cyan"}" title="deploy_platform (${esc(a.prefix_source || "?")})${a.prefix ? ` → ${esc(a.prefix)}` : ""}">${esc(a.deploy_platform)}${a.prefix ? `→${esc(a.prefix)}` : ""}</span>`);
  else if (a.platform_status !== "none") b.push('<span class="chip chip-red">no platform</span>');
  if (a.deploy_technology) b.push(`<span class="chip chip-violet" title="deploy_technology (${esc(a.tech_source || "?")})">🛠 ${esc(a.deploy_technology)}</span>`);
  if (a.logging_required === false) b.push(`<span class="chip chip-amber" title="the ${esc(a.deploy_technology || "?")} technology has logging: false in the Engine repo (vars/Deploy_Technologies) — logs are not expected from this app">🔇 logging not required</span>`);
  if (unsupported) b.push('<span class="chip chip-amber" title="platform not monitored — logs not checked">not monitored</span>');
  if (a.discrepancy) b.push(`<span class="chip chip-red" title="app deploy_platform (${esc(a.app_platform)}) overrides project (${esc(a.project_platform)})">⚠ clash</span>`);
  if ((a.owner_clash_envs || []).length) b.push(`<span class="chip chip-red" title="owner ($env_team) differs app vs project @ ${esc(a.owner_clash_envs.join(", "))}">⚠ owner</span>`);
  if (!a.deployed) b.push('<span class="chip chip-amber" title="never deployed — no logs expected">never deployed</span>');
  if (a.over_sized) b.push(`<span class="chip chip-amber" title="stores ${esc(a.size_h)} — ${a.size_ratio}× the fleet's average app (${esc(((state.logData || {}).storage_avg || {}).app_h || "?")}); flagged at ≥${((state.logData || {}).storage_avg || {}).factor || 2}×">🗄 ${a.size_ratio}× avg</span>`);
  if (a.not_in_inventory) b.push('<span class="chip chip-amber">drift</span>');
  return b.join(" ");
}

const _mxCls = (e) => !e ? "absent" : (!e.deployed ? "undeployed" : (e.no_logs ? "nolog"
  : (!e.ts_ok && e.indices ? "tsbad" : (e.stale ? "stale" : "ok"))));

// compact app×env cell — ONE text line: score · ⚠issue-count · size. The
// full story (meter, labeled issues, owner, spans) lives in the app drawer;
// the cell's tooltip carries it for a hover. Absent / never-deployed envs
// stay QUIET (a faint word, no box) so real data stands out.
function logMxCell(e, sep, over) {
  if (!e) return `<div class="log-mx-cell absent ${sep ? "sep" : ""}" title="app not present in this environment"><span class="log-mx-na">—</span></div>`;
  if (!e.deployed) return `<div class="log-mx-cell undeployed ${sep ? "sep" : ""}" title="${esc(e.env)} — never deployed, no logs expected"><span class="log-mx-na">not deployed</span></div>`;
  const issues = e.issues || [];
  const tip = `${e.env} · score ${e.score == null ? "n/a" : e.score} · ${logInt(e.indices)} idx · ${e.size_h} · ${logInt(e.docs)} docs`
    + (e.owner ? ` · 👤 ${e.owner}` : "")
    + (e.no_logs ? " · NO LOGS" : (e.last_logged_age_h != null ? ` · last log ${logAgo(e.last_logged_age_h)}` : ""))
    + (issues.length ? ` · ⚠ ${issues.map((k) => LOG_ISSUE_LABEL[k] || k).join(" · ")}` : "");
  const sev = issues.some((k) => (LOG_ISSUE_SEV[k] || "bad") === "bad") ? "bad" : "warn";
  const warn = issues.length
    ? `<span class="log-mxi ${sev}" title="${esc(issues.map((k) => LOG_ISSUE_LABEL[k] || k).join(" · "))}">⚠${issues.length}</span>` : "";
  const right = e.no_logs ? '<span class="log-mx-nolog">no logs</span>'
    : `<span class="log-mx-csize${over ? " over" : ""}"${over ? ` title="app stores over the fleet average"` : ""}>${esc(e.size_h)}</span>${e.indices ? `<span class="log-mx-cidx" title="${logInt(e.indices)} log indices in ${esc(e.env)}">${logInt(e.indices)} idx</span>` : ""}`;
  return `<div class="log-mx-cell ${_mxCls(e)} ${sep ? "sep" : ""}" title="${esc(tip)}">
    <span class="log-mx-cscore ${logScoreClass(e.score)}">${e.score == null ? "–" : e.score}</span>${warn}${right}
  </div>`;
}

// env column header — the project's aggregate for that env (score, meter,
// size, apps, issue count). Click opens the env DIVE for THIS project (the
// global env filter lives in the filter bar, not here).
function logMxHead(env, m, sep, total) {
  const base = `class="log-mx-cell head ${sep ? "sep" : ""}" data-env="${esc(env)}" role="button" tabindex="0"`;
  if (!m) return `<div ${base} title="click to open the ${esc(env)} dive for this project"><div class="log-mx-cline"><span class="log-mx-envname">${esc(env)}</span></div></div>`;
  const score = m.scores.length ? Math.round(m.scores.reduce((x, y) => x + y, 0) / m.scores.length) : null;
  const issueN = m.no_logs + m.stale + m.ts_bad + m.over;
  const parts = [m.no_logs && `${m.no_logs} no-logs`, m.stale && `${m.stale} stale`,
    m.ts_bad && `${m.ts_bad} @timestamp`, m.over && `${m.over} over-retained`].filter(Boolean).join(" · ");
  return `<div ${base} title="${esc(env)} — ${m.apps}/${total} apps · ${esc(logHsize(m.size_bytes))} · ${logInt(m.indices)} idx${parts ? " · " + esc(parts) : ""} · click to open this environment's dive (all apps' details)">
    <div class="log-mx-cline"><span class="log-mx-envname">${esc(env)}</span>${(() => {
      const owners = [...(m.owners || [])];
      const lead = m.powner || owners[0];
      if (!lead) return "";
      const extra = owners.filter((o) => o !== lead);
      return `<span class="log-mx-envteam" title="${esc(env)}_team: ${esc(lead)}${extra.length ? ` · app overrides: ${esc(extra.join(", "))}` : ""}">👤 ${esc(lead)}${extra.length ? ` +${extra.length}` : ""}</span>`;
    })()}${logScoreBadge(score, "env score across this project's apps")}</div>
    ${logMeter(score)}
    <div class="log-mx-cmeta"><b>${esc(logHsize(m.size_bytes))}</b> · ${logInt(m.indices)} idx · ${m.apps}/${total} apps${issueN ? ` <span class="log-mxi ${(m.no_logs || m.ts_bad) ? "bad" : "warn"}">${issueN} issue${issueN === 1 ? "" : "s"}</span>` : ""}</div>
    ${(() => { const r = logRates(m.size_bytes, m.docs, m.first, m.last);
      return r ? `<div class="log-mx-cmeta" title="ingest rate across this env's apps">📈 ≈${esc(r.size_day_h)}/day · ${logInt(r.docs_day)} docs/day</div>` : ""; })()}
  </div>`;
}

// shared index-row list (app dive + env dive)
function logIdxRows(list) {
  return (list || []).map((i) => `
    <div class="log-idx ${i.ts_type !== "date" || i.bad_week || i.future_week ? "bad" : ""}">
      <code class="log-idx-name">${esc(i.index)}</code>
      <span class="chip chip-amber">${esc(i.env || "?")}</span><span class="chip chip-cyan">${esc(i.logtype || "—")}</span>
      <span class="ci-meta log-idx-week">${esc(i.week || "")}${i.bad_week ? ' <span class="pct-bad">⚠ year</span>' : ""}${i.future_week ? ' <span class="pct-bad">⚠ future</span>' : ""}</span>
      <span class="log-idx-size">${logHsize(i.size_bytes)}</span><span class="ci-meta">${logInt(i.docs)} docs</span>${logSrcChip(i.source)}
      ${i.ts_type !== "date" ? `<span class="chip chip-red" title="@timestamp mapping">🕓 ${esc(i.ts_type || "unmapped")}</span>` : ""}
    </div>`).join("") || '<div class="empty">no indices</div>';
}

// ENVIRONMENT dive for ONE project — opened by clicking an env column header.
// Shows EVERY app's full detail for that environment: stats, owner, logged
// span, deploy, issues, the @timestamp/future sample inspectors, and that
// env's index list. Project-local by design — the global env filter lives in
// the always-visible filter bar, not here.
function logEnvDiveHtml(mx, env) {
  const close = '<button class="btn btn-sm btn-ghost log-envdive-close" title="close this environment dive">✕</button>';
  const inEnv = mx.apps.map((a) => ({ a, e: (a.env_stats || []).find((x) => x.env === env) })).filter((x) => x.e);
  let title = `<span class="log-envdive-title">🔎 ${esc(env)} <span class="ci-meta">·</span> ${esc(mx.p.name)}</span>`;
  if (!inEnv.length) return `<div class="log-envdive-head">${title}<span class="ci-meta">no apps in this environment</span><span class="spacer"></span>${close}</div>`;
  const size = inEnv.reduce((n, x) => n + (x.e.size_bytes || 0), 0);
  const docsT = inEnv.reduce((n, x) => n + (x.e.docs || 0), 0);
  const eFirst = inEnv.map((x) => x.e.first_logged).filter(Boolean).sort()[0];
  const eLast = inEnv.map((x) => x.e.last_logged).filter(Boolean).sort().slice(-1)[0];
  const eRate = logRates(size, docsT, eFirst, eLast);
  const powner = (inEnv.find((x) => x.e.owner_project) || {}).e?.owner_project
    || (inEnv.find((x) => x.e.owner) || {}).e?.owner;
  const issueN = inEnv.reduce((n, x) => n + (x.e.issues || []).length, 0);
  if (powner) title += ` <span class="chip chip-cyan" title="${esc(env)}_team">👤 ${esc(powner)}</span>`;
  const rows = inEnv.map(({ a, e }) => {
    const id = ("tssd-" + a.project + "-" + a.app + "-" + env).replace(/[^A-Za-z0-9_-]/g, "_");
    const goodI = (a.index_list || []).find((i) => i.ts_type === "date" && !i.bad_week && !i.future_week);
    const goodAttr = goodI ? `data-ts-good="${esc(goodI.index)}"` : "";
    const badNames = (e.ts_bad_indices || []).slice(0, 15).join(",");
    const futNames = (e.future_week_indices || []).slice(0, 15).join(",");
    const insp = [];
    if (badNames) insp.push(`<div class="log-tsbad-note">⚠ <b>@timestamp</b> is not a <b>date</b> in ${(e.ts_bad_indices || []).length} ${esc(env)} index(es) — time filters silently return nothing there.
      <button class="btn btn-sm log-ts-sample" data-tss-target="${id}-ts" data-ts-index="${esc(badNames)}" ${goodAttr}>🔍 sample bad docs</button>
      <div id="${id}-ts" class="log-tss"></div></div>`);
    if (futNames) insp.push(`<div class="log-tsbad-note">⚠ ${(e.future_week_indices || []).length} ${esc(env)} index(es) dated in the <b>FUTURE</b> vs the current week (${esc((state.logData || {}).current_week || "?")}).
      <button class="btn btn-sm log-ts-sample" data-tss-target="${id}-fut" data-ts-index="${esc(futNames)}" data-ts-mode="future" data-tss-label="⏩ docs in future-dated indices" ${goodAttr}>🔍 sample docs</button>
      <div id="${id}-fut" class="log-tss"></div></div>`);
    const bwNames = (e.bad_week_indices || []).slice(0, 15).join(",");
    if (bwNames) insp.push(`<div class="log-tsbad-note">⚠ ${(e.bad_week_indices || []).length} ${esc(env)} index(es) with an illogical <b>YEAR</b> in the name (malformed yyyy.ww).
      <button class="btn btn-sm log-ts-sample" data-tss-target="${id}-bw" data-ts-index="${esc(bwNames)}" data-ts-mode="badweek" data-tss-label="🗓 docs in bad-year indices" ${goodAttr}>🔍 sample docs</button>
      <div id="${id}-bw" class="log-tss"></div></div>`);
    const idxs = (a.index_list || []).filter((i) => i.env === env);
    const owner = e.owner ? `<span class="chip ${e.owner_clash ? "chip-red" : "chip-cyan"}" title="${e.owner_clash ? `app ${esc(e.owner_app)} vs project ${esc(e.owner_project)}` : esc(env) + "_team"}">👤 ${esc(e.owner)}${e.owner_clash ? " ⚠" : ""}</span>` : '<span class="ci-meta">👤 —</span>';
    const logged = !e.deployed ? '<span class="ci-meta">never deployed — no logs expected</span>'
      : (e.no_logs ? '<span class="pct-bad">deployed but NO LOGS</span>'
        : `<span class="ci-meta">🕓 ${logWhen(e.first_logged)} → ${logWhen(e.last_logged)} (${logAgo(e.last_logged_age_h)})</span>`);
    return `<div class="log-envdive-app">
      <div class="log-envdive-line">${logScoreBadge(e.score, env + " health score")}
        <span class="log-app-name">🧩 <b>${esc(a.app)}</b></span>
        ${e.no_logs || !e.deployed ? "" : `<span class="ci-meta">${logInt(e.indices)} idx · <b>${esc(e.size_h)}</b> · ${logInt(e.docs)} docs${(e.logtypes || []).length ? " · " + esc(e.logtypes.join(", ")) : ""}</span>`}
        ${logIssueChips(e.issues, 8) || (e.deployed && e.indices ? '<span class="chip chip-green">ok ✓</span>' : "")}</div>
      <div class="log-envdive-line">${owner} ${logged} <span class="ci-meta">📦 last deploy ${e.last_deploy ? logWhen(e.last_deploy) : (e.deployed ? "—" : "never")}</span> ${!e.no_logs && e.deployed ? logRateLine(e.size_bytes, e.docs, e.first_logged, e.last_logged) : ""}</div>
      ${insp.join("")}
      ${idxs.length ? `<details class="filebox log-idx-box"><summary>📑 ${idxs.length} ${esc(env)} index${idxs.length === 1 ? "" : "es"}</summary><div class="log-idx-list">${logIdxRows(idxs)}</div></details>` : ""}
    </div>`;
  }).join("");
  return `<div class="log-envdive-head">${title}
      <span class="ci-meta">${inEnv.length}/${mx.apps.length} apps · <b>${esc(logHsize(size))}</b>${eRate ? ` · ≈<b>${esc(eRate.size_day_h)}</b>/day` : ""} · ${issueN ? `<span class="pct-warn">${issueN} issue${issueN === 1 ? "" : "s"}</span>` : '<span class="pct-good">no issues</span>'}</span>
      <span class="spacer"></span>${close}</div>${rows}`;
}

// the app DRAWER (slide-over panel): everything about one app without pushing
// the table apart — vertical env sections, facts, inspectors, index list
function logAppDrawerHtml(a) {
  const flags = logAppFlags(a);
  const head = `<div class="log-drawer-head">
      ${logScoreBadge(a.score, "app health score")}
      <div class="log-drawer-title"><span class="log-drawer-app">🧩 ${esc(a.app)}</span>
        <span class="ci-meta">📁 ${esc(a.project)}${a.monitored && a.indices ? ` · ${logInt(a.indices)} idx · <b class="${a.over_sized ? "pct-bad" : ""}">${esc(a.size_h)}</b> · ${logInt(a.docs)} docs${(() => { const r = logRates(a.size_bytes, a.docs, a.first_logged, a.last_logged); return r ? ` · ≈<b>${esc(r.size_day_h)}</b>/day` : ""; })()}` : ""}</span></div>
      <span class="spacer"></span>
      <button class="btn btn-sm btn-ghost log-drawer-close" title="close (Esc)">✕</button></div>
    ${flags ? `<div class="log-mx-appflags">${flags}</div>` : ""}`;
  if (a.platform_status === "unsupported")
    return head + `<div class="empty">This app's <code>deploy_platform</code> is <b>${esc(a.deploy_platform)}</b>, not one QuestOps monitors (OCP / LinuxVM / WindowsVM / K8s) — logs are <b>not checked</b>.${a.discrepancy ? ` <span class="pct-bad">It overrides the project global <b>${esc(a.project_platform)}</b>.</span>` : ""}</div>`;
  if (a.platform_status === "none")
    return head + `<div class="empty">No index prefix — no <code>deploy_platform</code> on this app or the project.</div>`;
  const byEnv = {}; (a.env_stats || []).forEach((e) => { byEnv[e.env] = e; });
  const { main, extra } = logOrderedEnvs(Object.keys(byEnv), state.logData);
  const envSec = (en, isExtra) => {
    const e = byEnv[en]; if (!e) return "";
    const owner = e.owner ? `<span class="chip ${e.owner_clash ? "chip-red" : "chip-cyan"}" title="${e.owner_clash ? `app ${esc(e.owner_app)} vs project ${esc(e.owner_project)}` : esc(en) + "_team"}">👤 ${esc(e.owner)}${e.owner_clash ? " ⚠" : ""}</span>` : '<span class="ci-meta">👤 —</span>';
    const logged = !e.deployed ? '<span class="ci-meta">never deployed — no logs expected</span>'
      : (e.no_logs ? '<span class="pct-bad">deployed but NO LOGS</span>'
        : `<span class="ci-meta">🕓 ${logWhen(e.first_logged)} → ${logWhen(e.last_logged)} (${logAgo(e.last_logged_age_h)})</span>`);
    return `<div class="log-drawer-env ${_mxCls(e)}">
      <div class="log-drawer-envhead"><span class="log-mx-envname">${esc(en)}</span>${isExtra ? '<span class="chip chip-amber">extra</span>' : ""}${logScoreBadge(e.score, en + " health score")}
        <span class="spacer"></span>${!e.deployed || e.no_logs ? "" : `<span class="ci-meta">${logInt(e.indices)} idx · <b>${esc(e.size_h)}</b> · ${logInt(e.docs)} docs</span>`}</div>
      ${e.deployed && !e.no_logs ? logMeter(e.score) : ""}
      <div class="log-envdive-line">${owner} ${logged}</div>
      <div class="log-envdive-line ci-meta">📦 last deploy ${e.last_deploy ? logWhen(e.last_deploy) : (e.deployed ? "—" : "never")}</div>
      ${!e.no_logs && e.deployed ? `<div class="log-envdive-line">${logRateLine(e.size_bytes, e.docs, e.first_logged, e.last_logged)}</div>` : ""}
      ${logIssueChips(e.issues, 8) || (e.deployed && e.indices ? '<div><span class="chip chip-green">ok ✓</span></div>' : "")}
    </div>`;
  };
  const envsHtml = [...main.map((en) => envSec(en, false)), ...extra.map((en) => envSec(en, true))].join("")
    || '<div class="ci-meta">no environment data</div>';
  const chips = (arr, cls) => (arr || []).map((x) => `<span class="chip ${cls}">${esc(x)}</span>`).join(" ") || '<span class="ci-meta">none</span>';
  const tssId = "tss-" + (a.project + "-" + a.app).replace(/[^A-Za-z0-9_-]/g, "_");
  const badNames = (a.ts_bad_indices || []).slice(0, 15).join(",");
  const goodI = (a.index_list || []).find((i) => i.ts_type === "date" && !i.bad_week && !i.future_week);
  const tsInspect = (!a.ts_ok && badNames) ? `
    <div class="log-tsbad-note">⚠ <b>@timestamp</b> is not a <b>date</b> in ${(a.ts_bad_indices || []).length} index(es) — time filters silently return nothing there.
      <button class="btn btn-sm log-ts-sample" data-tss-target="${tssId}" data-ts-index="${esc(badNames)}" ${goodI ? `data-ts-good="${esc(goodI.index)}"` : ""}>🔍 sample bad docs</button>
      <div id="${tssId}" class="log-tss"></div></div>` : "";
  // same inspector for FUTURE-dated indices — what @timestamp are those docs
  // actually carrying? (clock skew vs a mis-templated loader)
  const futNames = (a.future_week_indices || []).slice(0, 15).join(",");
  const futInspect = futNames ? `
    <div class="log-tsbad-note">⚠ ${(a.future_week_indices || []).length} index(es) dated in the <b>FUTURE</b> vs the current week (${esc((state.logData || {}).current_week || "?")}) — likely clock skew or a mis-templated index.
      <button class="btn btn-sm log-ts-sample" data-tss-target="${tssId}-fut" data-ts-index="${esc(futNames)}" data-ts-mode="future" data-tss-label="⏩ docs in future-dated indices" ${goodI ? `data-ts-good="${esc(goodI.index)}"` : ""}>🔍 sample docs</button>
      <div id="${tssId}-fut" class="log-tss"></div></div>` : "";
  // and for BAD-YEAR indices — the index NAME is malformed (yyyy.ww), so the
  // docs' @timestamp + log.file.path point at the mis-templated shipper
  const bwNames = (a.bad_week_indices || []).slice(0, 15).join(",");
  const bwInspect = bwNames ? `
    <div class="log-tsbad-note">⚠ ${(a.bad_week_indices || []).length} index(es) with an illogical <b>YEAR</b> in the name (malformed yyyy.ww — a mis-templated loader).
      <button class="btn btn-sm log-ts-sample" data-tss-target="${tssId}-bw" data-ts-index="${esc(bwNames)}" data-ts-mode="badweek" data-tss-label="🗓 docs in bad-year indices" ${goodI ? `data-ts-good="${esc(goodI.index)}"` : ""}>🔍 sample docs</button>
      <div id="${tssId}-bw" class="log-tss"></div></div>` : "";
  return `${head}
    <div class="log-drawer-envs">${envsHtml}</div>
    <div class="log-app-facts">
      <div><span class="acc-h">owners ($env_team)</span><div class="inv-chips">${(a.owners || []).map((o) => `<span class="chip chip-cyan">👤 ${esc(o)}</span>`).join(" ") || '<span class="ci-meta">none</span>'}</div></div>
      <div><span class="acc-h">logtypes (live from ES)</span><div class="inv-chips">${chips(a.logtypes, "chip-cyan")}</div></div>
      <div><span class="acc-h">deploy_platform</span><div class="ci-meta">${a.deploy_platform ? `${esc(a.deploy_platform)} → ${esc(a.prefix || "?")} (${esc(a.prefix_source || "?")})` : "—"}</div></div>
      <div><span class="acc-h">deploy_technology</span><div class="ci-meta">${a.deploy_technology ? `${esc(a.deploy_technology)} (${esc(a.tech_source || "?")})` : "—"}${a.logging_required === false ? ' · <span class="pct-warn">🔇 logging not required</span>' : (a.logging_required === true ? ' · logging required' : "")}</div></div>
      <div><span class="acc-h">company</span><div class="ci-meta">${a.company ? `🏢 ${esc(a.company)}` : "—"}</div></div>
      <div><span class="acc-h">stored on</span><div class="inv-chips">${(a.sources || []).map(logSrcChip).join(" ") || '<span class="ci-meta">—</span>'}</div></div>
      <div><span class="acc-h">storage vs fleet</span><div class="ci-meta"><b class="${a.over_sized ? "pct-bad" : ""}">${esc(a.size_h)}</b>${a.size_ratio != null ? ` · ${a.size_ratio}× the average app (${esc(((state.logData || {}).storage_avg || {}).app_h || "?")})` : ""}${a.over_sized ? ' · <span class="pct-warn">over-sized</span>' : ""}</div></div>
    </div>
    ${tsInspect}
    ${futInspect}
    ${bwInspect}
    ${a.indices ? `<details class="filebox log-idx-box"><summary>📑 ${logInt(a.indices)} index${a.indices === 1 ? "" : "es"}</summary><div class="log-idx-list">${logIdxRows(a.index_list)}</div></details>` : ""}`;
}

function closeLogDrawer() {
  const d = document.getElementById("log-drawer");
  if (d) d.hidden = true;
}
function openLogDrawer(aid) {
  const a = (state.logAppMap || {})[aid];
  const d = document.getElementById("log-drawer");
  if (!a || !d) return;
  const panel = d.querySelector(".log-drawer-panel");
  panel.innerHTML = logAppDrawerHtml(a);
  panel.scrollTop = 0;
  d.hidden = false;
  panel.querySelectorAll(".log-ts-sample").forEach((b) => b.onclick = () => loadTsSamples(b));
  d.querySelector(".log-drawer-backdrop").onclick = closeLogDrawer;
  const cl = panel.querySelector(".log-drawer-close");
  if (cl) { cl.onclick = closeLogDrawer; cl.focus(); }
}

// the whole matrix for one project (header + app rows + lazy detail placeholders)
function logMatrixHtml(p, apps, f) {
  // per-project app sort override (the small selector in the header row)
  const psort = (state.logProjSort || {})[p.name] || "global";
  const skey = psort === "global" ? ((state.logFilter || {}).sort || "score") : psort;
  const sdir = (state.logProjDir || {})[p.name]
    || (psort === "global" ? (state.logFilter || {}).sortDir : null) || logDefaultDir(skey);
  apps = logSortApps(apps, skey, sdir);
  const present = [...new Set(apps.flatMap((a) => (a.env_stats || []).map((e) => e.env)))];
  const { main, extra } = logOrderedEnvs(present, state.logData);
  const cols = [...main, ...extra];
  if (!cols.length) return '<div class="ci-meta" style="padding:6px">no environments to show</div>';
  const sepAt = extra.length ? main.length : -1;   // first extra column index
  const agg = {};
  apps.forEach((a) => (a.env_stats || []).forEach((e) => {
    const m = agg[e.env] = agg[e.env] || { size_bytes: 0, indices: 0, docs: 0, scores: [], no_logs: 0, stale: 0, ts_bad: 0, over: 0, apps: 0, first: null, last: null };
    m.size_bytes += e.size_bytes; m.indices += e.indices; m.docs += e.docs; m.apps++;
    if (e.first_logged && (!m.first || e.first_logged < m.first)) m.first = e.first_logged;
    if (e.last_logged && (!m.last || e.last_logged > m.last)) m.last = e.last_logged;
    if (e.owner) (m.owners = m.owners || new Set()).add(e.owner);
    if (e.owner_project && !m.powner) m.powner = e.owner_project;   // THE project ${env}_team
    if (e.score != null) m.scores.push(e.score);
    if (e.no_logs && e.deployed) m.no_logs++;
    if (e.stale) m.stale++;
    if (!e.ts_ok && e.indices) m.ts_bad++;
    if (e.over_retained) m.over++;
  }));
  const _mid = "m" + (++_logMxSeq);
  (state.logMxMap = state.logMxMap || {})[_mid] = { p, apps, pscore: f._projScore };
  const head = `<div class="log-mx-row head">
    <div class="log-mx-appcell head">
      <span class="log-mx-appline">${logScoreBadge(f._projScore, "project score")} <span class="log-mx-projname">📁 ${esc(p.name)}</span></span>
      <span class="log-mx-axis">apps ↓ · environments → (click an env for its dive)
        <select class="log-mx-sortsel" title="sort this project's apps">
          <option value="global" ${psort === "global" ? "selected" : ""}>sort: global</option>
          ${LOG_SORT_LABELS.map(([v, l]) =>
            `<option value="${v}" ${psort === v ? "selected" : ""}>${l}</option>`).join("")}
        </select>
        ${logDirBtn("log-mx-dir", sdir, "sort direction for this project's apps")}
        <button class="btn btn-sm log-mx-report" title="prepare, preview and email this project's logging report">📧 report</button></span>
    </div>
    ${cols.map((en, i) => logMxHead(en, agg[en], i === sepAt, apps.length)).join("")}</div>
  <div class="log-mx-envdive" hidden></div>`;
  // each app is ONE slim clickable row; clicking opens the app DRAWER (a
  // slide-over panel) instead of pushing detail into the table. Apps with
  // nothing to chart (unsupported / never deployed / no logs anywhere) get a
  // single spanning note instead of a row of empty cells.
  const rows = apps.map((a) => {
    const _aid = "a" + (++_logAppSeq);
    (state.logAppMap = state.logAppMap || {})[_aid] = a;
    const byEnv = {}; (a.env_stats || []).forEach((e) => { byEnv[e.env] = e; });
    const rowCls = a.no_logs ? "nolog" : (!a.ts_ok && a.indices ? "tsbad" : (a.stale ? "stale" : (!a.deployed ? "undeployed" : "")));
    const flags = logAppFlags(a);
    const note = a.platform_status === "unsupported" ? "platform not monitored — logs not checked"
      : (!a.deployed ? "never deployed — no logs expected"
        : (a.platform_status === "none" ? "no deploy_platform — can't build an index prefix"
          : (a.monitored && a.no_logs
            ? `deployed but NO LOGS — expected in: ${(a.env_stats || []).filter((e) => e.deployed).map((e) => e.env).join(", ") || "?"}` : null)));
    const cells = note
      ? `<div class="log-mx-cell note ${a.no_logs && a.deployed ? "nolog" : ""}">${esc(note)}</div>`
      : cols.map((en, i) => logMxCell(byEnv[en], i === sepAt, a.over_sized)).join("");
    return `<div class="log-mx-row approw ${rowCls}" data-app-id="${_aid}" role="button" tabindex="0" title="click for the full app breakdown">
        <div class="log-mx-appcell">${logScoreBadge(a.score, "app health score")}
          <span class="log-app-name">🧩 <b>${esc(a.app)}</b></span>
          ${a.indices ? `<span class="log-mx-cidx" title="${logInt(a.indices)} log indices in total">${logInt(a.indices)} idx</span>` : ""}
          ${flags ? `<span class="log-mx-appflags">${flags}</span>` : ""}
          <span class="log-mx-caret">›</span>
        </div>
        ${cells}</div>`;
  }).join("");
  return `<div class="log-mx-scroll"><div class="log-matrix" data-mx-id="${_mid}" style="--envn:${cols.length}">${head}${rows}</div></div>`;
}

function logProjectCardHtml(p, apps, f) {
  // totals + score recomputed from the (filtered + scoped) apps so the project
  // header stays consistent with the active env/team filters
  const has = (k) => apps.filter((a) => (a.issues || []).includes(k)).length;
  const bytes = apps.reduce((n, a) => n + a.size_bytes, 0);
  const scores = apps.map((a) => a.score).filter((x) => x != null);
  let pscore = scores.length ? Math.round(scores.reduce((x, y) => x + y, 0) / scores.length) : null;
  if (p.over_sized && pscore != null) pscore = Math.max(pscore - 10, 0);   // project-level storage-hog deduction
  const t = {
    apps: (p.totals || {}).apps || (p.apps || []).length,
    indices: apps.reduce((n, a) => n + a.indices, 0), size_h: logHsize(bytes),
    docs: apps.reduce((n, a) => n + a.docs, 0),
    no_logs: has("no_logs"), stale: has("stale"), ts_bad: has("timestamp"),
    bad_week: has("bad_week"), future_week: has("future_week"), over_retained: has("over_retained"),
    over_sized: has("over_sized"),
    discrepancies: has("clash"), team_clash: has("team_clash"), unsupported: has("unsupported"),
    undeployed: apps.filter((a) => !a.deployed).length,
  };
  const flag = (n, label, cls) => n ? ` · <span class="${cls}">${n} ${label}</span>` : "";
  const plat = p.deploy_platform
    ? `<span class="chip chip-cyan" title="project-global deploy_platform → index prefix">${esc(p.deploy_platform)} → ${esc(p.prefix || "?")}</span>`
    : (p.no_prefix ? '<span class="chip chip-red" title="no deploy_platform on any app or group_vars/all">no deploy_platform</span>' : "");
  const warn = p.no_prefix
    ? `<div class="log-tsbad-note">⚠ project <b>${esc(p.name)}</b> resolves no <code>deploy_platform</code> on any app (group_vars/&lt;app&gt;) or project-wide (group_vars/all) — can't build a log index prefix (OCP→oc · LinuxVM→vmlin · WindowsVM→vmwin · K8s→k8s), so its apps can't be located.</div>`
    : "";
  return `<details class="filebox log-proj" data-proj="${esc(p.name)}">
    <summary>${logScoreBadge(pscore, "project health score")}
      <span class="log-proj-name">📁 <b>${esc(p.name)}</b></span> ${plat}${p.company ? `<span class="chip chip-violet" title="company (group_vars/all)">🏢 ${esc(p.company)}</span>` : ""}${p.over_sized ? `<span class="chip chip-amber" title="project stores ${esc(logHsize(bytes))} — ${p.size_ratio}× the average project (${esc(((state.logData || {}).storage_avg || {}).project_h || "?")})">🗄 ${p.size_ratio}× avg</span>` : ""}${p.not_in_inventory ? ' <span class="chip chip-amber">not in inventory</span>' : ""}
      <span class="ci-meta">${apps.length}/${t.apps} app(s) · ${logInt(t.indices)} idx · <b class="${p.over_sized ? "pct-bad" : ""}">${esc(t.size_h)}</b> · ${logInt(t.docs)} docs${(() => {
        const wl = apps.filter((x) => x.size_bytes > 0);
        const r = logRates(bytes, t.docs, wl.map((x) => x.first_logged).filter(Boolean).sort()[0],
          wl.map((x) => x.last_logged).filter(Boolean).sort().slice(-1)[0]);
        return (wl.length ? ` · avg app ${esc(logHsize(Math.round(bytes / wl.length)))}` : "")
          + (r ? ` · ≈<b>${esc(r.size_day_h)}</b>/day` : "");
      })()}${
        flag(t.no_logs, "no-logs", "pct-bad")}${flag(t.stale, "stale", "pct-warn")}${flag(t.ts_bad, "@ts", "pct-bad")}${flag(t.bad_week, "bad-year", "pct-bad")}${flag(t.future_week, "future", "pct-bad")}${flag(t.over_retained, "over-retained", "pct-warn")}${flag(t.over_sized, "over-sized", "pct-warn")}${flag(t.discrepancies, "clash", "pct-bad")}${flag(t.team_clash, "owner-clash", "pct-bad")}${flag(t.unsupported, "unmonitored", "pct-warn")}${flag(t.undeployed, "un-deployed", "pct-warn")}</span></summary>
    <div class="log-proj-body">${warn}
      ${(f._projScore = pscore, logMatrixHtml(p, apps, f))}</div></details>`;
}

// stat tiles are computed from the FILTERED apps so the numbers track the filters
function logTilesHtml(apps) {
  const has = (k) => apps.filter((a) => (a.issues || []).includes(k)).length;
  const sum = (fn) => apps.reduce((n, a) => n + fn(a), 0);
  const scores = apps.map((a) => a.score).filter((x) => x != null);
  const overall = scores.length ? Math.round(scores.reduce((x, y) => x + y, 0) / scores.length) : null;
  const projects = new Set(apps.map((a) => a.project)).size;
  const tile = (n, label, cls) => `<div class="stat-tile"><b class="${cls || ""}">${n}</b><span>${label}</span></div>`;
  return `<div class="stat-tiles" style="margin:8px 0 12px">
    <div class="stat-tile log-score-tile"><b class="log-score ${logScoreClass(overall)}">${overall == null ? "—" : overall}</b><span>health score</span></div>
    ${tile(projects, "projects")}
    ${tile(apps.length, "apps")}
    ${tile(logInt(sum((a) => a.indices)), "log indices")}
    ${tile(logHsize(sum((a) => a.size_bytes)), "total size")}
    ${tile(logInt(sum((a) => a.docs)), "documents")}
    ${(() => {
      const withLogs = apps.filter((a) => a.size_bytes > 0);
      const avgApp = withLogs.length ? Math.round(sum((a) => a.size_bytes) / withLogs.length) : 0;
      const first = withLogs.map((a) => a.first_logged).filter(Boolean).sort()[0];
      const last = withLogs.map((a) => a.last_logged).filter(Boolean).sort().slice(-1)[0];
      const r = logRates(sum((a) => a.size_bytes), sum((a) => a.docs), first, last);
      return tile(logHsize(avgApp), "avg app size")
        + (r ? tile(r.size_day_h + "/d", "ingest / day") + tile(logInt(r.docs_day), "docs / day")
             + (r.doc_avg_h ? tile(r.doc_avg_h, "avg doc size") : "") : "");
    })()}
    ${tile(has("no_logs"), "apps no-logs", has("no_logs") ? "pct-bad" : "pct-good")}
    ${tile(has("stale"), "apps stale", has("stale") ? "pct-warn" : "pct-good")}
    ${tile(has("timestamp"), "@timestamp", has("timestamp") ? "pct-bad" : "pct-good")}
    ${tile(has("bad_week"), "bad year", has("bad_week") ? "pct-bad" : "pct-good")}
    ${tile(has("future_week"), "future-dated", has("future_week") ? "pct-bad" : "pct-good")}
    ${tile(has("over_retained"), "over-retained", has("over_retained") ? "pct-warn" : "pct-good")}
    ${tile(has("over_sized"), "over-sized", has("over_sized") ? "pct-warn" : "pct-good")}
    ${tile(has("clash"), "platform clashes", has("clash") ? "pct-bad" : "pct-good")}
    ${tile(has("team_clash"), "owner clashes", has("team_clash") ? "pct-bad" : "pct-good")}
    ${tile(has("unsupported"), "unmonitored", has("unsupported") ? "pct-warn" : "pct-good")}
    ${tile(sum((a) => a.deployed ? 0 : 1), "un-deployed", sum((a) => a.deployed ? 0 : 1) ? "pct-warn" : "pct-good")}</div>`;
}

function logContentHtml() {
  const d = state.logData;
  const f = state.logFilter;
  const on = (k) => f[k] && f[k] !== "all";
  f._any = !!(f.q || on("env") || on("project") || on("platform") || on("tech") || on("company") || on("logtype") || on("team") || on("issue") || on("logreq"));
  state.logAppMap = {}; _logAppSeq = 0;   // rebuilt as rows render → lazy bodies look apps up here
  state.logMxMap = {}; _logMxSeq = 0;     // per-project matrix registry (env dives)
  const filtered = [];
  // filter → SCOPE (env/team) → sort apps; then sort the projects the same way.
  // Scoping recomputes each app's aggregates from just the matching envs.
  const entries = (d.projects || []).map((p) => {
    const apps = logSortApps((p.apps || []).filter((a) => logAppMatch(a, f))
      .map((a) => logScopeApp(a, f)), f.sort, f.sortDir);
    const scores = apps.map((a) => a.score).filter((x) => x != null);
    let score = scores.length ? Math.round(scores.reduce((x, y) => x + y, 0) / scores.length) : null;
    if (p.over_sized && score != null) score = Math.max(score - 10, 0);
    return { p, apps,
      t: { size_bytes: apps.reduce((n, a) => n + a.size_bytes, 0),
           docs: apps.reduce((n, a) => n + a.docs, 0),
           indices: apps.reduce((n, a) => n + a.indices, 0) },
      score };
  }).filter((e) => e.apps.length);
  const cards = logSortProjects(entries, f.sort, f.sortDir).map((e) => {
    filtered.push(...e.apps);
    return logProjectCardHtml(e.p, e.apps, f);
  }).join("");
  const un = (d.unmatched || []).length ? `
    <details class="filebox log-unmatched">
      <summary>⚠ ${d.unmatched.length} unmatched index${d.unmatched.length === 1 ? "" : "es"} — didn't map to a known project/app (naming drift) <span class="ci-meta">· <b>${esc(logHsize(d.unmatched.reduce((n, u) => n + (u.size_bytes || 0), 0)))}</b> total</span></summary>
      <div class="log-idx-list">${d.unmatched.map((u) => `
        <div class="log-idx ${u.bad_week || u.future_week ? "bad" : ""}"><code class="log-idx-name">${esc(u.index)}</code>
          <span class="log-idx-size">${logHsize(u.size_bytes)}</span>
          <span class="ci-meta">${logInt(u.docs)} docs</span>${logSrcChip(u.source)}${u.bad_week ? '<span class="chip chip-red">⚠ bad year</span>' : ""}${u.future_week ? '<span class="chip chip-red">⚠ future</span>' : ""}</div>`).join("")}</div>
    </details>` : "";
  return logTilesHtml(filtered)
    + (cards || '<div class="empty">no apps match the filters</div>') + un
    + `<div id="log-drawer" hidden><div class="log-drawer-backdrop"></div>
       <aside class="log-drawer-panel" role="dialog" aria-modal="true" aria-label="app logging detail"></aside></div>`
    + `<div id="log-report" hidden><div class="log-drawer-backdrop"></div>
       <div class="log-report-panel" role="dialog" aria-modal="true" aria-label="project logging report">
         <div class="log-report-head">📧 <b id="log-report-title"></b>
           <span class="spacer"></span>
           <button class="btn btn-sm btn-ghost log-report-close" title="close (Esc)">✕</button></div>
         <div class="log-report-bar log-report-opts">
           <label class="log-issues" title="EXTRA_ENVS are excluded by default — tick to include them"><input type="checkbox" id="log-report-extra"> include extra envs</label>
           <label class="log-issues" title="hide apps with a perfect score (100) — a focused problem report"><input type="checkbox" id="log-report-healthy"> hide healthy apps</label>
           <label class="log-issues" title="hide apps never deployed in the in-scope environments (no logs expected)"><input type="checkbox" id="log-report-undep" checked> hide un-deployed apps</label>
           <label class="log-issues" title="hide apps whose platform isn't monitored (logs not checked)"><input type="checkbox" id="log-report-unmon"> hide unmonitored apps</label>
           <select id="log-report-team" title="narrow the report to the environments owned by one team"></select></div>
         <div class="log-report-bar">
           <input id="log-report-subj" title="email subject">
           <input id="log-report-to" placeholder="recipients — comma-separated emails">
           <button class="btn btn-sm btn-primary" id="log-report-send">📤 send</button>
           <button class="btn btn-sm" id="log-report-dl" title="download the previewed report as an HTML file">⬇ HTML</button>
           <button class="btn btn-sm" id="log-report-pdf" title="save the previewed report as PDF (opens the print dialog — choose 'Save as PDF')">🖨 PDF</button>
           <span id="log-report-status" class="ci-meta"></span></div>
         <iframe id="log-report-frame" title="report preview"></iframe>
       </div></div>`;
}

function rerenderLog() {
  const box = document.getElementById("log-body");
  if (!box) return;
  // keep the user's place: which projects are expanded, which env dives are
  // open, and the scroll offset — a filter change must not reset the view
  const scroller = view();
  const top = scroller ? scroller.scrollTop : 0;
  const openProj = new Set([...box.querySelectorAll("details.log-proj[open]")]
    .map((el) => el.dataset.proj));
  const openDives = {};
  box.querySelectorAll(".log-matrix").forEach((mxEl) => {
    const dive = mxEl.querySelector(".log-mx-envdive");
    const mx = (state.logMxMap || {})[mxEl.dataset.mxId];
    if (dive && mx && !dive.hidden && dive.dataset.env) openDives[mx.p.name] = dive.dataset.env;
  });
  box.innerHTML = logContentHtml();
  wireLogContent();
  box.querySelectorAll("details.log-proj").forEach((el) => {
    if (openProj.has(el.dataset.proj)) el.setAttribute("open", "");
  });
  box.querySelectorAll(".log-matrix").forEach((mxEl) => {
    const mx = (state.logMxMap || {})[mxEl.dataset.mxId];
    const env = mx && openDives[mx.p.name];
    if (!env) return;
    const cell = mxEl.querySelector(`.log-mx-cell.head[data-env="${env}"]`);
    if (cell) cell.dispatchEvent(new Event("click"));
  });
  if (scroller) scroller.scrollTop = top;
}

// @timestamp sample inspector — the offending docs (across all suspect
// indices) with their @timestamp value + event.original, next to a healthy
// index. Non-dates are red; valid-but-FUTURE dates are flagged too.
function logTsSamplesHtml(data, label) {
  const col = (title, blk, badCol) => {
    if (!blk) return "";
    if (blk.error) return `<div class="tss-col"><div class="acc-h">${title}</div><div class="rsearch-status rsearch-err">⚠ ${esc(blk.error)}</div></div>`;
    const idxLabel = (blk.indices || []).length > 1
      ? `${blk.indices.length} indices` : esc((blk.indices || [])[0] || "—");
    const rows = (blk.docs || []).map((dd) => {
      const orig = dd.original == null ? "" : String(dd.original);
      const origBlock = orig
        ? `<details class="tss-orig"><summary>event.original <span class="ci-meta">(${orig.length}${dd.original_truncated ? "+" : ""} chars)</span></summary><pre>${esc(orig)}${dd.original_truncated ? "\n…(truncated)" : ""}</pre></details>`
        : '<div class="ci-meta tss-noorig">no event.original</div>';
      const verdict = !dd.is_date ? '<span class="chip chip-red">not a date</span>'
        : (dd.is_future ? '<span class="chip chip-red">⏩ future date</span>' : '<span class="chip chip-green">date ✓</span>');
      const path = (dd.path || dd.logtype) ? `<div class="tss-path">${dd.logtype ? `<span class="chip chip-cyan" title="fields.type — the shipper's log-type tag">${esc(dd.logtype)}</span> ` : ""}${dd.path ? `<span title="log.file.path — the log file this doc was shipped from">📄 <code>${esc(dd.path)}</code></span>` : ""}</div>` : "";
      return `<div class="tss-doc ${dd.is_date && !dd.is_future ? "ok" : "bad"}">
        <div class="tss-doc-head">
          <code class="tss-val">${esc(String(dd.value))}</code>
          ${verdict}
          <span class="ci-meta tss-src">${badCol ? esc(String(dd.index || "").split("-").slice(1, 3).join("-") || dd.index || "") : ""}</span>
        </div>
        ${path}
        ${origBlock}</div>`;
    }).join("") || '<div class="ci-meta">no docs sampled</div>';
    const futBit = blk.future ? ` · <b>${blk.future}</b> future-dated` : "";
    return `<div class="tss-col"><div class="acc-h">${title}</div>
      <div class="ci-meta tss-idx">${idxLabel} · mapping <b>${esc(Object.values(blk.ts_types || {})[0] || "?")}</b> · <b>${blk.non_date}</b>/${blk.sampled} not dates${futBit}</div>${rows}</div>`;
  };
  return `<div class="tss">${col(label || "⚠ offending docs", data.index, true)}${col("✓ healthy index", data.good, false)}</div>`;
}
async function loadTsSamples(btn) {
  const c = document.getElementById(btn.dataset.tssTarget);
  if (!c) return;
  // already fetched → the button just toggles the panel (no refetch)
  if (c.dataset.loaded) {
    const hide = !c.hasAttribute("hidden");
    c.toggleAttribute("hidden", hide);
    btn.textContent = hide ? btn.dataset.showLabel : "🙈 hide samples";
    return;
  }
  btn.dataset.showLabel = btn.dataset.showLabel || btn.textContent;
  c.removeAttribute("hidden");
  c.innerHTML = '<div class="rsearch-status">sampling… <span class="rsearch-spin"></span></div>';
  const qs = new URLSearchParams({ index: btn.dataset.tsIndex });
  if (btn.dataset.tsGood) qs.set("good", btn.dataset.tsGood);
  if (btn.dataset.tsMode) qs.set("mode", btn.dataset.tsMode);
  try {
    c.innerHTML = logTsSamplesHtml(await api(`/api/logging/ts-samples?${qs.toString()}`), btn.dataset.tssLabel);
    c.dataset.loaded = "1";               // success → future clicks toggle
    btn.textContent = "🙈 hide samples";
  } catch (e) { c.innerHTML = `<div class="rsearch-status rsearch-err">⚠ ${esc(e.message)}</div>`; }
}
function closeLogReport() {
  const m = document.getElementById("log-report");
  if (m) m.hidden = true;
}
// prepare + preview the per-project report; recipients are chosen here and
// the send goes through the backend's SMTP (QO_SMTP_* in .env)
async function openLogReport(projName) {
  const m = document.getElementById("log-report");
  if (!m) return;
  m.hidden = false;
  m.querySelector("#log-report-title").textContent = `logging report — ${projName}`;
  const frame = m.querySelector("#log-report-frame");
  const status = m.querySelector("#log-report-status");
  const subj = m.querySelector("#log-report-subj");
  m.querySelector(".log-drawer-backdrop").onclick = closeLogReport;
  m.querySelector(".log-report-close").onclick = closeLogReport;
  // scope controls: extra envs OFF by default; env-owner team filter from
  // this project's actual env owners
  const extraCb = m.querySelector("#log-report-extra");
  const healthyCb = m.querySelector("#log-report-healthy");
  const undepCb = m.querySelector("#log-report-undep");
  const unmonCb = m.querySelector("#log-report-unmon");
  const teamSel = m.querySelector("#log-report-team");
  extraCb.checked = false;
  healthyCb.checked = false;
  undepCb.checked = true;    // like the page: never-deployed apps hidden by default
  unmonCb.checked = false;
  const proj = ((state.logData || {}).projects || []).find((x) => x.name === projName);
  const owners = [...new Set((proj ? proj.apps || [] : [])
    .flatMap((a) => (a.env_stats || []).map((e) => e.owner_project || e.owner))
    .filter(Boolean))].sort();
  teamSel.innerHTML = `<option value="">env team: all</option>`
    + owners.map((o) => `<option value="${esc(o)}">${esc(o)} envs only</option>`).join("");
  teamSel.value = "";
  let lastRep = null;
  const load = async () => {
    status.textContent = "building report…";
    const qs = new URLSearchParams({ project: projName });
    if (extraCb.checked) qs.set("extra", "true");
    if (healthyCb.checked) qs.set("skip_healthy", "true");
    if (undepCb.checked) qs.set("skip_undeployed", "true");
    if (unmonCb.checked) qs.set("skip_unmonitored", "true");
    if (teamSel.value) qs.set("team", teamSel.value);
    try {
      const rep = await api(`/api/logging/report?${qs.toString()}`);
      lastRep = rep;
      const toInp = m.querySelector("#log-report-to");
      if (rep.admin_email) {
        toInp.placeholder = `recipients — comma-separated (${rep.admin_email} is always looped in)`;
        toInp.title = `${rep.admin_email} is added to every report send (QO_ADMIN_EMAIL)`;
      }
      subj.value = rep.subject;
      frame.setAttribute("srcdoc", rep.html);
      status.textContent = `preview ready (${(rep.envs || []).join(", ") || "no envs in scope"}) — add recipients and send`;
    } catch (e) { status.textContent = `⚠ ${e.message}`; }
  };
  // download EXACTLY what's previewed (same scope) as a standalone HTML file
  const dlBtn = m.querySelector("#log-report-dl");
  if (dlBtn) dlBtn.onclick = () => {
    if (!lastRep) { status.textContent = "⚠ no report built yet"; return; }
    const scope = [teamSel.value && teamSel.value.replace(/[^A-Za-z0-9_-]+/g, "_"),
      extraCb.checked ? "with-extra" : "", healthyCb.checked ? "problems-only" : ""]
      .filter(Boolean).join("_");
    const name = `logging-report_${projName.replace(/[^A-Za-z0-9_-]+/g, "_")}`
      + (scope ? `_${scope}` : "") + `_${new Date().toISOString().slice(0, 10)}.html`;
    const url = URL.createObjectURL(new Blob([lastRep.html], { type: "text/html" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
    status.textContent = `⬇ downloaded ${name}`;
  };
  // PDF = the browser's print-to-PDF of exactly the previewed document
  const pdfBtn = m.querySelector("#log-report-pdf");
  if (pdfBtn) pdfBtn.onclick = () => {
    if (!lastRep) { status.textContent = "⚠ no report built yet"; return; }
    try {
      frame.contentWindow.focus();
      frame.contentWindow.print();
      status.textContent = "🖨 print dialog opened — choose “Save as PDF”";
    } catch (e) { status.textContent = `⚠ ${e.message}`; }
  };
  extraCb.onchange = load;
  healthyCb.onchange = load;
  undepCb.onchange = load;
  unmonCb.onchange = load;
  teamSel.onchange = load;
  await load();
  const sendBtn = m.querySelector("#log-report-send");
  sendBtn.onclick = async () => {
    const to = m.querySelector("#log-report-to").value.split(",").map((x) => x.trim()).filter(Boolean);
    if (!to.length) { status.textContent = "⚠ add at least one recipient"; return; }
    sendBtn.disabled = true;
    status.textContent = "sending…";
    try {
      const res = await api("/api/logging/report/send", { method: "POST",
        body: { project: projName, recipients: to, subject: subj.value,
                extra: extraCb.checked, team: teamSel.value || null,
                skip_healthy: healthyCb.checked, skip_undeployed: undepCb.checked,
                skip_unmonitored: unmonCb.checked } });
      status.classList.remove("log-report-err");
      status.textContent = `✓ sent to ${res.sent} recipient(s) (${res.recipients.join(", ")})`
        + (res.transport && res.transport !== "demo" ? ` via ${res.transport.toUpperCase()}${res.attempts > 1 ? ` after ${res.attempts} attempts` : ""}` : "")
        + (res.note ? ` — ${res.note}` : "");
    } catch (e) {
      status.classList.add("log-report-err");
      status.textContent = `⚠ ${e.message}`;
      status.title = e.message;
    }
    sendBtn.disabled = false;
  };
}

// wire ONE matrix element (rows, env headers, per-project sort) — scoped so a
// single project's matrix can be rebuilt in place without re-binding the rest
function wireLogMatrix(mxEl) {
  // app rows open the slide-over drawer (content built on demand per click)
  mxEl.querySelectorAll(".log-mx-row.approw[data-app-id]").forEach((row) => {
    const open = () => openLogDrawer(row.dataset.appId);
    row.addEventListener("click", open);
    row.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); open(); } });
  });
  // click an ENV column header → open/close that environment's dive for THIS
  // project only (all apps' details for the env). Does NOT touch the global
  // env filter — that lives in the always-visible filter bar.
  mxEl.querySelectorAll(".log-mx-cell.head[data-env]").forEach((cell) => {
    const toggle = (ev) => {
      ev.stopPropagation();
      const dive = mxEl.querySelector(".log-mx-envdive");
      const mx = (state.logMxMap || {})[mxEl.dataset.mxId];
      if (!dive || !mx) return;
      const env = cell.dataset.env;
      const wasOpen = !dive.hidden && dive.dataset.env === env;
      mxEl.querySelectorAll(".log-mx-cell.head.active").forEach((h) => h.classList.remove("active"));
      if (wasOpen) { dive.hidden = true; dive.dataset.env = ""; return; }
      dive.dataset.env = env;
      dive.innerHTML = logEnvDiveHtml(mx, env);
      dive.hidden = false;
      cell.classList.add("active");
      dive.querySelectorAll(".log-ts-sample").forEach((b) => b.onclick = () => loadTsSamples(b));
      const cl = dive.querySelector(".log-envdive-close");
      if (cl) cl.onclick = () => { dive.hidden = true; dive.dataset.env = ""; cell.classList.remove("active"); };
    };
    cell.addEventListener("click", toggle);
    cell.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); toggle(ev); } });
  });
  const repBtn = mxEl.querySelector(".log-mx-report");
  if (repBtn) repBtn.onclick = (ev) => {
    ev.stopPropagation();
    const mx = (state.logMxMap || {})[mxEl.dataset.mxId];
    if (mx) openLogReport(mx.p.name);
  };
  // small per-project app-sort (criteria select + ↑/↓ toggle) — rebuilds ONLY
  // this matrix, in place, so the project expander stays open and other
  // projects keep their listeners
  const rebuild = () => {
    const mx = (state.logMxMap || {})[mxEl.dataset.mxId];
    const scroll = mxEl.closest(".log-mx-scroll");
    if (!mx || !scroll) return;
    const f2 = Object.assign({}, state.logFilter, { _projScore: mx.pscore });
    const tmp = document.createElement("div");
    tmp.innerHTML = logMatrixHtml(mx.p, mx.apps, f2);   // re-applies the stored sort+dir
    const fresh = tmp.firstElementChild;
    scroll.replaceWith(fresh);
    const freshMx = fresh.querySelector(".log-matrix");
    if (freshMx) wireLogMatrix(freshMx);
  };
  const sel = mxEl.querySelector(".log-mx-sortsel");
  if (sel) sel.onchange = () => {
    const mx = (state.logMxMap || {})[mxEl.dataset.mxId];
    if (!mx) return;
    (state.logProjSort = state.logProjSort || {})[mx.p.name] = sel.value;
    delete (state.logProjDir || {})[mx.p.name];   // direction resets to the criteria's default
    rebuild();
  };
  const mxDir = mxEl.querySelector(".log-mx-dir");
  if (mxDir) mxDir.onclick = (ev) => {
    ev.stopPropagation();
    const mx = (state.logMxMap || {})[mxEl.dataset.mxId];
    if (!mx) return;
    const psort = (state.logProjSort || {})[mx.p.name] || "global";
    const skey = psort === "global" ? ((state.logFilter || {}).sort || "score") : psort;
    const cur = (state.logProjDir || {})[mx.p.name]
      || (psort === "global" ? (state.logFilter || {}).sortDir : null) || logDefaultDir(skey);
    (state.logProjDir = state.logProjDir || {})[mx.p.name] = cur === "asc" ? "desc" : "asc";
    rebuild();
  };
}
function wireLogContent() {
  view().querySelectorAll(".log-matrix").forEach(wireLogMatrix);
  // Esc closes the drawer (wired once — closeLogDrawer is a no-op when shut)
  if (!state._logEscWired) {
    state._logEscWired = true;
    document.addEventListener("keydown", (ev) => { if (ev.key === "Escape") { closeLogDrawer(); closeLogReport(); } });
  }
}

async function renderLogging() {
  const tok = navToken();
  let d;
  try {
    d = await api(`/api/logging${state.logRefresh ? "?refresh=true" : ""}`);
  } catch (e) {
    if (!navStale(tok)) view().innerHTML = `<div class="empty">⚠ ${esc(e.message)}</div>`;
    return;
  }
  state.logRefresh = false;
  if (navStale(tok)) return;
  state.logData = d;
  const f = state.logFilter = state.logFilter
    || { q: "", project: "all", platform: "all", tech: "all", company: "all", logreq: "all", logtype: "all", env: "all", team: "all",
      issue: "all", sort: "score", hideNoLogs: false, hideUnmonitored: false, hideUndeployed: true };
  const s = d.summary || {};
  const anyActive = !!(f.q || ["project", "platform", "tech", "company", "logreq", "logtype", "env", "team", "issue"]
    .some((k) => f[k] && f[k] !== "all") || f.hideNoLogs || f.hideUnmonitored || f.hideUndeployed);

  const legend = (d.platform_legend || []).map((x) =>
    `<span class="chip chip-cyan" title="deploy_platform ${esc(x.platform)} → index prefix ${esc(x.prefix)}">${esc(x.platform)} → <b>${esc(x.prefix)}</b></span>`).join(" ");
  // compact at-a-glance connection dots on the (collapsed) setup summary
  const connDot = (kind, label) => {
    const c = (d.connections || {})[kind];
    if (!c || !c.configured) return `<span class="chip ${kind === "nonprd" ? "chip-amber" : "chip-red"}" title="${esc((c && c.note) || "not configured")}">${label} ○</span>`;
    return `<span class="chip ${c.reachable ? "chip-green" : "chip-red"}" title="${c.reachable ? c.indices + " indices" : esc(c.error || "unreachable")}">${label} ${c.reachable ? "✓" : "✗"}</span>`;
  };
  const connProblem = ["prd", "nonprd"].some((k) => { const c = (d.connections || {})[k]; return c && c.configured && !c.reachable; });
  const head = `<div class="view-head"><h1>LOGGING HEALTH</h1>
      <span class="sub">ELK index health across your projects &amp; apps</span>
      <span class="spacer"></span>
      <button class="btn btn-sm" id="log-refresh">↻ re-analyze</button></div>`;
  // pattern logic + ES health + inventory detection — collapsed, on demand
  // (auto-opens only when there's a page-level note or a connection problem)
  const setup = `<details class="filebox log-setup" ${(d.note || connProblem) ? "open" : ""}>
      <summary>ℹ index pattern · Elasticsearch health · inventory detection
        <span class="log-setup-dots">${connDot("prd", "prd")} ${connDot("nonprd", "non-prd")}
          <span class="ci-meta">${(d.prefixes || []).length} prefix(es)</span></span></summary>
      <div class="log-setup-body">
        <div class="ci-meta" style="margin-bottom:8px">index pattern <code>\${prefix}-\${project}-\${env}-\${app}-\${logtype}-yyyy.ww</code> — prefix per app from <code>deploy_platform</code>; retention prd ${Math.round((d.retention || {}).prd_days / 30) || "?"}mo · non-prd ${(d.retention || {}).nonprd_days || "?"}d</div>
        ${legend ? `<div class="log-legend"><span class="ci-meta">platform → prefix:</span> ${legend}</div>` : ""}
        ${logConnBar(d.connections)}
        ${logDiagHtml(d)}
      </div></details>`;

  if (!(d.projects || []).length) {
    view().innerHTML = head + (d.note ? `<div class="panel"><div class="kpi-note">${esc(d.note)}</div></div>` : "") + setup;
    document.getElementById("log-refresh").onclick = () => { state.logRefresh = true; renderLogging(); };
    return;
  }

  const sel = (id, cur, opts) => `<select id="${id}">${opts.map(([v, l]) =>
    `<option value="${esc(v)}" ${String(cur) === String(v) ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>`;
  const projNames = (d.projects || []).map((p) => p.name);
  const platforms = [...new Set((d.projects || []).flatMap((p) => (p.apps || [])
    .map((a) => a.deploy_platform).filter(Boolean)))].sort();
  const issueOpts = [["all", "issue: any"], ["any", "any issue"],
    ...["no_logs", "stale", "timestamp", "bad_week", "future_week", "over_retained", "over_sized", "clash", "team_clash", "unsupported"]
      .map((k) => [k, LOG_ISSUE_LABEL[k]])];
  // filters PRECEDE the stat tiles so the numbers respond to them; the bar is
  // STICKY (always visible while scrolling) and visually compact
  const filterBar = `<div class="acc-filters log-filters">
    <input id="log-q" placeholder="🔎 app / project / platform / env / logtype…" value="${esc(f.q || "")}">
    ${(s.companies || []).length ? sel("log-company", f.company || "all", [["all", "company: any"], ...(s.companies || []).map((c) => [c, c])]) : ""}
    ${sel("log-project", f.project || "all", [["all", "project: any"], ...projNames.map((p) => [p, p])])}
    ${sel("log-platform", f.platform || "all", [["all", "platform: any"], ...platforms.map((p) => [p, p])])}
    ${(s.technologies || []).length ? sel("log-tech", f.tech || "all", [["all", "tech: any"], ...(s.technologies || []).map((t) => [t, t])]) : ""}
    ${(d.projects || []).some((p) => (p.apps || []).some((a) => a.logging_required != null))
      ? sel("log-logreq", f.logreq || "all", [["all", "logging: any"], ["required", "logging required"], ["notrequired", "logging not required"]]) : ""}
    ${sel("log-logtype", f.logtype || "all", [["all", "type: any"], ...(s.logtypes || []).map((l) => [l, l])])}
    ${sel("log-env", f.env || "all", [["all", "env: any"], ...(s.envs || []).map((e) => [e, e])])}
    ${(() => {
      const byEnv = {};
      (d.projects || []).forEach((p) => (p.apps || []).forEach((a) => (a.env_stats || []).forEach((e) => {
        if (e.owner) (byEnv[e.env] = byEnv[e.env] || new Set()).add(e.owner);
      })));
      const order = [...((d.env_order || {}).main || []), ...((d.env_order || {}).extra || [])];
      const roleEnvs = [...order.filter((en) => byEnv[en]),
        ...Object.keys(byEnv).sort().filter((en) => !order.includes(en))];
      const opt = (v, l) => `<option value="${esc(v)}" ${String(f.team) === String(v) ? "selected" : ""}>${esc(l)}</option>`;
      return `<select id="log-team">${opt("all", "owner: any")}
        <optgroup label="env owner (scopes to their envs)">${(s.teams || []).map((tm) => opt(tm, tm)).join("")}</optgroup>
        ${roleEnvs.map((en) => `<optgroup label="${esc(en)}_team (all envs shown)">${[...byEnv[en]].sort()
          .map((tm) => opt(en + ":" + tm, en + "_team: " + tm)).join("")}</optgroup>`).join("")}</select>`;
    })()}
    ${sel("log-issue", f.issue || "all", issueOpts)}
    ${sel("log-sort", f.sort || "score", [...LOG_SORT_LABELS.map(([v, l]) => [v, "sort: " + l])])}
    ${logDirBtn("", f.sortDir || logDefaultDir(f.sort || "score"), "toggle ascending / descending")}
    <label class="log-issues" title="hide apps never deployed (per ${esc(d.deploy_index || "the deployments index")}) — no logs expected"><input type="checkbox" id="log-hide-undep" ${f.hideUndeployed ? "checked" : ""}> hide un-deployed</label>
    <label class="log-issues" title="hide apps that have no log indices"><input type="checkbox" id="log-hide-nolog" ${f.hideNoLogs ? "checked" : ""}> hide no-logs</label>
    <label class="log-issues" title="hide apps whose platform isn't monitored"><input type="checkbox" id="log-hide-unmon" ${f.hideUnmonitored ? "checked" : ""}> hide unmonitored</label>
    ${anyActive ? '<button class="btn btn-sm" id="log-clear">✕ clear</button>' : ""}
    <span class="spacer"></span><span class="ci-meta">${esc(d.source)}${d.cached ? " · cached" : ""} · stale &gt;${d.stale_hours}h${d.current_week ? ` · current week <b>${esc(d.current_week)}</b>` : ""}</span></div>`;

  const noteHtml = d.note ? `<div class="panel" style="margin-bottom:10px"><div class="kpi-note">${esc(d.note)}</div></div>` : "";
  // compact: title · collapsed setup/health · always-visible filters + stats + results
  view().innerHTML = head + noteHtml + setup + filterBar
    + `<div id="log-body">${logContentHtml()}</div>`;
  wireLogging();
}

function wireLogging() {
  const rb = document.getElementById("log-refresh");
  if (rb) rb.onclick = () => { state.logRefresh = true; renderLogging(); };
  const f = state.logFilter;
  const q = document.getElementById("log-q");
  if (q) q.oninput = () => {
    f.q = q.value;
    clearTimeout(state._logT);
    state._logT = setTimeout(() => {
      rerenderLog();
      const nq = document.getElementById("log-q");
      if (nq) { nq.focus(); nq.setSelectionRange(nq.value.length, nq.value.length); }
    }, 200);
  };
  [["log-project", "project"], ["log-platform", "platform"], ["log-tech", "tech"], ["log-company", "company"], ["log-logreq", "logreq"], ["log-logtype", "logtype"],
   ["log-env", "env"], ["log-team", "team"], ["log-issue", "issue"]].forEach(([id, key]) => {
    const el = document.getElementById(id);
    if (el) el.onchange = () => { f[key] = el.value; rerenderLog(); };
  });
  // sort criteria + a separate ↑/↓ direction toggle (direction resets to the
  // criteria's natural default when the criteria changes)
  const dirBtn = view().querySelector(".log-filters .log-dir");
  const syncDir = () => {
    if (!dirBtn) return;
    const dd = f.sortDir || logDefaultDir(f.sort || "score");
    dirBtn.querySelector(".seg.asc").classList.toggle("active", dd === "asc");
    dirBtn.querySelector(".seg.desc").classList.toggle("active", dd === "desc");
  };
  const so = document.getElementById("log-sort");
  if (so) so.onchange = () => { f.sort = so.value; f.sortDir = logDefaultDir(so.value); syncDir(); rerenderLog(); };
  if (dirBtn) dirBtn.onclick = () => {
    f.sortDir = (f.sortDir || logDefaultDir(f.sort || "score")) === "asc" ? "desc" : "asc";
    syncDir(); rerenderLog();
  };
  [["log-hide-undep", "hideUndeployed"], ["log-hide-nolog", "hideNoLogs"],
   ["log-hide-unmon", "hideUnmonitored"]].forEach(([id, key]) => {
    const el = document.getElementById(id);
    // re-render the whole view so the "✕ clear" button appears/disappears
    if (el) el.onchange = () => { f[key] = el.checked; renderLogging(); };
  });
  const cl = document.getElementById("log-clear");
  if (cl) cl.onclick = () => {
    state.logFilter = { q: "", project: "all", platform: "all", tech: "all", company: "all", logreq: "all", logtype: "all",
      env: "all", team: "all", issue: "all", sort: f.sort || "score", sortDir: f.sortDir,
      hideNoLogs: false, hideUnmonitored: false, hideUndeployed: true };
    renderLogging();
  };
  wireLogContent();
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
      ${(s.inventory_pipelines || 0) ? bar("inventory pipelines tied to an ADO repo", pct(s.inventory_pipelines_matched || 0, s.inventory_pipelines || 0),
          `${s.inventory_pipelines_matched || 0} matched · ${(s.inventory_pipelines || 0) - (s.inventory_pipelines_matched || 0)} unmatched (of ${s.inventory_pipelines} defined) · ${s.inventory_projects || 0} project(s) in inventory`, cls(pct(s.inventory_pipelines_matched || 0, s.inventory_pipelines || 0))) : ""}
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
  if (f.inv === "in" && !p.in_inventory) return false;
  if (f.inv === "out" && p.in_inventory) return false;
  if (f.inv === "pipes" && !((p.inv_pipelines_matched || 0) > 0)) return false;
  if (f.inv === "mismatch" && p.inv_team_match !== false) return false;
  if (f.minrepos && (p.repos || 0) < Number(f.minrepos)) return false;
  return true;
}
const ADO_FILTER_ACTIVE = (f) => f && (f.q || (f.grade && f.grade !== "all")
  || (f.pr && f.pr !== "all") || (f.team && f.team !== "all") || (f.outteam && f.outteam !== "all")
  || (f.unassigned && f.unassigned !== "all") || (f.dup && f.dup !== "all")
  || (f.inv && f.inv !== "all")
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
  // inventory chips for one project: presence, dev_team↔owner match, pipelines
  const invChips = (p) => {
    if (!p.in_inventory)
      return '<span class="chip chip-amber acc-inv-chip" title="no matching project in the inventories repo">🧭 not in inventory</span>';
    const parts = ['<span class="chip chip-green acc-inv-chip" title="found in the inventories repo">🧭 in inventory</span>'];
    if (p.inv_team_match === true)
      parts.push(`<span class="chip chip-green" title="inventory dev_team [${esc(p.inv_dev_team || "")}] matches the ADO owner">✓ dev_team</span>`);
    else if (p.inv_team_match === false)
      parts.push(`<span class="chip chip-red" title="inventory dev_team [${esc(p.inv_dev_team || "—")}] ≠ ADO owner [${esc(p.team || "—")}]">⚠ dev_team ≠ owner</span>`);
    if ((p.inv_pipelines || 0) > 0) {
      const full = p.inv_pipelines_matched === p.inv_pipelines;
      parts.push(`<span class="chip ${full ? "chip-cyan" : "chip-amber"}" title="inventory apps (repository_name) resolving to a real ADO repo${(p.inv_pipeline_repos || []).length ? ": " + p.inv_pipeline_repos.map(esc).join(", ") : ""}">🔧 ${p.inv_pipelines_matched || 0}/${p.inv_pipelines} pipelines</span>`);
    }
    return parts.join(" ");
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

  // inventory join — presence, dev_team match & pipeline coverage
  const inv = d.inventory || {};
  const invSum = inv.summary || {};
  const invCloned = inv.source && inv.source !== "not cloned";
  const invBanner = !invCloned
    ? `<div class="kpi-note" style="margin-bottom:10px">🧭 clone the <code>inventories</code> repo on the Repositories page to tie each app's pipeline (<code>repository_name</code>) to its ADO repo${inv.note ? ` — ${esc(inv.note)}` : ""}</div>`
    : `<div class="acc-inv-bar">
        <span class="chip chip-green" title="ADO projects that exist in the inventories repo">🧭 ${invSum.in_inventory || 0}/${invSum.ado_projects || 0} projects in inventory</span>
        <span class="chip ${invSum.pipelines_matched ? "chip-cyan" : ""}" title="inventory apps whose repository_name resolves to a real ADO repo">🔧 ${invSum.pipelines_matched || 0}/${invSum.pipelines_total || 0} pipelines tied to a repo</span>
        ${invSum.team_mismatch ? `<span class="chip chip-red" title="inventory dev_team ≠ the ADO project owner">⚠ ${invSum.team_mismatch} dev_team mismatch</span>` : `<span class="chip chip-green">✓ dev_team matches owner</span>`}
        ${(inv.inventory_only || []).length ? `<span class="chip chip-amber" title="inventory projects with no matching ADO project: ${inv.inventory_only.map(esc).join(", ")}">🧭 ${inv.inventory_only.length} inventory-only</span>` : ""}
      </div>`;

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
      ${sel("inv", f.inv, [["all", "inventory: any"], ["in", "in inventory"], ["out", "not in inventory"], ["pipes", "has pipelines"], ["mismatch", "dev_team ≠ owner"]])}
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
            ${invCloned ? `<span class="chip chip-cyan" title="inventory apps (repository_name) tied to a real ADO repo in this collection">🔧 ${s.inventory_pipelines_matched || 0}/${s.inventory_pipelines || 0} pipelines</span>` : ""}
            ${invCloned && (s.inventory_projects != null) ? `<span class="chip" title="projects in this collection found in the inventories repo">🧭 ${s.inventory_projects || 0} in inventory</span>` : ""}
            ${(s.inventory_team_mismatch || 0) ? `<span class="chip chip-red" title="projects where inventory dev_team ≠ ADO owner">⚠ ${s.inventory_team_mismatch} dev_team ≠</span>` : ""}
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
                  ${invChips(p)}
                </span></summary>
              <div class="acc-proj-body" id="acc-proj-${esc(p.coll)}-${esc(p.id)}"><div class="empty">loading…</div></div>
            </details>`).join("")}
        </div>
      </details>`;
    }).join("")
    : `<div class="empty">no projects match the filters — <a href="javascript:void 0" id="ado-filter-clear2">clear filters</a></div>`;

  return failBanner + invBanner + dupRepoPanel + filterBar + summaryLine + body;
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
  const a = d.ado, j = d.jira, o = d.overlap, inv = d.inventory || {};
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
  // inventory tiles — only meaningful once the inventories repo is cloned
  const invCloned = inv.source && inv.source !== "not cloned";
  const invTiles = invCloned ? `
    <div class="acc-glance-inv">
      <div class="acc-glance-h">🧭 inventories repo</div>
      <div class="stat-tiles">
        ${tile(inv.projects || 0, "inventory projects")}
        ${tile(inv.apps || 0, "apps")}
        ${tile(inv.pipelines || 0, "pipelines (repository_name)")}
        ${tile(inv.pipelines_matched || 0, "pipelines tied to an ADO repo", (inv.pipelines_matched ? "pct-good" : ""))}
        ${tile(inv.in_inventory || 0, "ADO projects in inventory")}
        ${tile(inv.not_in_inventory || 0, "ADO projects NOT in inventory", (inv.not_in_inventory ? "pct-warn" : "pct-good"))}
        ${tile(inv.inventory_only || 0, "inventory-only projects", (inv.inventory_only ? "pct-warn" : ""))}
        ${tile(inv.team_mismatch || 0, "dev_team ≠ ADO owner", (inv.team_mismatch ? "pct-bad" : "pct-good"))}
      </div>
    </div>`
    : `<div class="kpi-note" style="margin-top:8px">🧭 inventory stats appear once the <code>inventories</code> repo is cloned on the Repositories page${inv.note ? ` — ${esc(inv.note)}` : ""}</div>`;
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
  return `<div class="stat-tiles">${tiles}</div>${invTiles}${ov}`;
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
    <div class="panel" style="margin-bottom:18px"><h2>⛁ Azure DevOps — projects, repository permissions &amp; inventory pipelines</h2>
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
