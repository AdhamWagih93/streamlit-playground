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
                repos: renderRepos, deps: renderDeps, access: renderAccess,
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

async function renderCI() {
  if (state.dive) return renderDive();
  const kpiHours = state.kpiHours || 168;  // default: the past week
  const [data, kpi, errs] = await Promise.all([
    api("/api/ci"), api(`/api/kpi?hours=${kpiHours}`), api("/api/errors")]);
  const failures = data.failures.map((f) => `
    <div class="ci-row">
      <span class="ci-dot dot-red"></span>
      <span class="ci-job">${esc(f.job)} <small>#${f.number}</small></span>
      <span class="ci-meta">${esc(f.result)} · ${f.ago_min}m ago${f.claimed_by ? ` · 🛠 @${esc(f.claimed_by)}` : ""}</span>
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
      <span class="ci-meta">running ${l.running_min}m${l.avg_min ? ` · avg ${l.avg_min}m` : ""}${l.claimed_by ? ` · 👀 @${esc(l.claimed_by)}` : ""}</span>
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
      <span class="ci-meta">${j.recent_builds ? `${j.recent_builds} builds · ` : ""}${j.building ? "building…" : esc(j.result || "—")}${j.duration_min ? ` · ${j.duration_min}m` : ""}</span>
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
    : !kpi.window_applied
      ? `<div class="kpi-note">⚠ no parseable dates in '${esc(kpi.index)}' — the ${kpi.hours}h window could not be applied; showing the newest records</div>`
      : kpi.window_source === "client"
        ? `<div class="kpi-note">ℹ the index's date fields aren't date-mapped — the ${kpi.hours}h window is enforced on parsed builddate values instead</div>`
        : "";
  const pctCls = (p) => p >= 90 ? "pct-good" : p >= 70 ? "pct-warn" : "pct-bad";
  const st = kpi.stats || { total: 0, pipelines: [] };
  // failing pipelines are front and centre WITH their links; fully-green ones
  // collapse behind a stat box, viewable on demand
  const failing = st.pipelines.filter((p) => p.success < p.total);
  const green = st.pipelines.filter((p) => p.total > 0 && p.success === p.total);
  const greenPct = st.pipelines.length ? Math.round((green.length / st.pipelines.length) * 100) : 0;
  const pipeName = (p) => p.url && !p.url.startsWith("#")
    ? `<a class="ci-job" href="${esc(p.url)}" target="_blank" rel="noopener" title="open ${esc(p.job)} in Jenkins">${esc(p.job)} ↗</a>`
    : `<span class="ci-job" title="${esc(p.job)}">${esc(p.job)}</span>`;
  const pipeRow = (p) => `
    <div class="kpi-pipe">
      ${pipeName(p)}
      <span class="lb-bar"><div class="${pctCls(p.pct)}" style="width:${p.pct}%"></div></span>
      <span class="kpi-pct ${pctCls(p.pct)}">${p.pct}%</span>
      <span class="ci-meta">${p.success}/${p.total}</span>
    </div>`;
  const kpiStats = st.total ? `
    <div class="kpi-stats">
      <div class="kpi-overall">
        <b class="${pctCls(st.overall_pct)}">${st.overall_pct}%</b>
        <span>overall success<br>${st.success}/${st.total} builds</span>
      </div>
      <div class="kpi-pipes">
        ${failing.map(pipeRow).join("") || `<div class="empty">no failing pipelines in this window 🎉</div>`}
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
      ${kpiWarn}
      ${kpi.truncated ? `<div class="kpi-note">⚠ the window holds ${kpi.loaded_total} builds — stats are computed on the newest ${kpi.fetched} (raise KPI_MAX_DOCS to widen)</div>` : ""}
      ${!kpi.loaded_total && kpi.diagnostics ? `
        <details class="filebox"><summary>🔎 why 0 builds? — query diagnostics</summary>
          <div style="padding:8px 12px">
            ${(kpi.diagnostics.attempts || []).map((a) => `<div class="ci-meta">• ${esc(a)}</div>`).join("")}
            ${(kpi.diagnostics.sample || []).length ? `<div class="kpi-note" style="margin-top:6px">sample raw dates from the index:</div>` : ""}
            ${(kpi.diagnostics.sample || []).map((s) => `<div class="ci-meta">• builddate=${esc(JSON.stringify(s.builddate))} · @timestamp=${esc(JSON.stringify(s["@timestamp"]))} · parseable: ${s.parsed ? "yes" : "NO"}</div>`).join("")}
          </div>
        </details>` : ""}
      ${kpi.ignored ? `<div class="kpi-note">🚫 ${kpi.ignored} build(s) excluded by KPI_IGNORE (${esc((kpi.ignore_tokens || []).join(", "))})</div>` : ""}
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
    </div>`;

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
        <button class="btn btn-sm ${state.historyOpen ? "btn-primary" : ""}" id="repo-history">🕘 History</button>
        <button class="btn btn-sm" id="repo-pull" title="fetch the server copy and move your workspace to it">⟳ Sync</button>
        <button class="btn btn-sm btn-danger" id="repo-discard">Discard my edits</button>
        <button class="btn btn-sm btn-danger" id="repo-remove"
          title="remove from QuestOps (all members' workspaces deleted; the remote repo is untouched)">🗑</button>
      </div>
      <div id="remote-banner">${remoteBannerHtml(remoteData)}</div>
      ${scanHtml}
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

async function renderDeps(refresh) {
  const repoData = await api("/api/repos").catch(() => ({ repos: [] }));
  if (!repoData.repos.length) {
    view().innerHTML = `<div class="view-head"><h1>DEPENDENCIES</h1></div>
      <div class="empty">no repositories defined — add your Engine repo on the
      <a href="#/repos">Repositories page</a> first</div>`;
    return;
  }
  if (!repoData.repos.some((r) => r.slot === state.depSlot)) {
    const engine = repoData.repos.find((r) => r.name.toLowerCase() === "engine");
    state.depSlot = (engine || repoData.repos[0]).slot;
  }
  const cur = repoData.repos.find((r) => r.slot === state.depSlot);
  const chips = repoData.repos.map((r) => `
    <button class="btn btn-sm ${r.slot === cur.slot ? "btn-primary" : ""}" data-dep-repo="${r.slot}">⛁ ${esc(r.name)}</button>`).join(" ");
  const head = `
    <div class="view-head"><h1>DEPENDENCIES</h1>
      <span class="sub">pipelines → playbooks / roles / scripts · used vs unused</span>
      <span class="spacer"></span>
      <div class="filter-row">${chips}</div>
      <button class="btn btn-sm" id="dep-refresh">↻ re-analyze</button></div>`;

  if (!cur.cloned) {
    view().innerHTML = head + `
      <div class="empty">'${esc(cur.name)}' is not cloned yet —
        clone it on the <a href="#/repos">Repositories page</a> to analyze it</div>`;
    wireDeps(cur);
    return;
  }

  view().innerHTML = head + `<div class="empty">analyzing ${esc(cur.name)}…</div>`;
  let d;
  try {
    d = await api(`/api/deps?slot=${cur.slot}${refresh === true ? "&refresh=true" : ""}`);
  } catch (e) {
    view().innerHTML = head + `<div class="empty">⚠ ${esc(e.message)}</div>`;
    wireDeps(cur);
    return;
  }
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

  const matrixRowsHtml = (query) => {
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
  };

  view().innerHTML = head + `
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
          <div class="ci-scroll" style="max-height:420px" id="dep-matrix-rows">${matrixRowsHtml(state.depQuery)}</div></div>
      </div>
    </div>`;
  wireDeps(cur);

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
      if (rows) rows.innerHTML = matrixRowsHtml(state.depQuery);
    }, 120);
  };
}

function wireDeps(cur) {
  // only repo switch and re-analyze do a full render (they change the data)
  view().querySelectorAll("[data-dep-repo]").forEach((b) => b.onclick = () => {
    state.depSlot = parseInt(b.dataset.depRepo, 10);
    state.depRoot = null;
    renderDeps();
  });
  const r = document.getElementById("dep-refresh");
  if (r) r.onclick = () => renderDeps(true);
}

/* ================= ACCESS MANAGEMENT ================= */
const ACC_PERM_CLS = (p) => /Administer|Manage permissions|Force push|Delete|Configure/i.test(p)
  ? "chip-red" : /Contribute|Edit|Create|Build|Transition|Resolve/i.test(p)
  ? "chip-amber" : "chip-cyan";
const permChips = (list, cls) => (list || []).map((p) =>
  `<span class="chip ${cls || ACC_PERM_CLS(p)}">${esc(p)}</span>`).join(" ");
const srcLabel = (d) => `${esc(d.source)}${d.cached ? " · cached" : ""}`;

const ACC_WHAT = {
  ado: "querying Azure DevOps for projects",
  jira: "reading Jira permission schemes & their project assignments",
  jenkins: "scanning Jenkins job/folder configs for matrix RBAC",
};

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

const extLink = (url) => url && !url.startsWith("#")
  ? `<a class="acc-ext" href="${esc(url)}" target="_blank" rel="noopener" title="open">↗</a>` : "";

function accAdoHtml(d) {
  if (!d.projects.length) return `<div class="empty">no projects (${srcLabel(d)})</div>`;
  // group projects by collection — the whole instance, not one collection
  const byColl = {};
  d.projects.forEach((p) => { (byColl[p.coll] = byColl[p.coll] || []).push(p); });
  const colls = Object.keys(byColl).sort();
  return `<div class="ci-meta" style="margin-bottom:8px">${srcLabel(d)} · ${d.projects.length} project(s) across ${colls.length} collection(s) — expand a project for its teams &amp; repo permissions</div>`
    + colls.map((c) => `
      <div class="acc-coll"><div class="acc-coll-head">🗄 ${esc(c)}</div>
        ${byColl[c].map((p) => `
          <details class="filebox acc-proj" data-acc-coll="${esc(p.coll)}" data-acc-proj="${esc(p.id)}">
            <summary>📁 <b>${esc(p.name)}</b> ${extLink(p.url)} <span class="ci-meta">${esc(p.description || "")}</span></summary>
            <div class="acc-proj-body" id="acc-proj-${esc(p.coll)}-${esc(p.id)}"><div class="empty">loading…</div></div>
          </details>`).join("")}
      </div>`).join("");
}

function accAdoProjectHtml(d) {
  const teams = (d.teams || []).map((t) => `
    <div class="ci-row"><span class="ci-job">👥 ${esc(t.name)}</span>
      <span class="acc-members">${t.members.slice(0, 8).map((m) => `<span class="chip">${esc(m)}</span>`).join(" ")}
      ${t.members.length > 8 ? `<span class="ci-meta">+${t.members.length - 8} more</span>` : ""}</span>
    </div>`).join("") || `<div class="empty">no teams</div>`;
  const repos = (d.repos || []).map((r) => `
    <div class="acc-repo"><div class="ci-job" style="margin-bottom:4px">⛁ ${esc(r.name)} ${extLink(r.url)}</div>
      ${(r.acls || []).map((a) => `
        <div class="acc-acl"><span class="acc-ident">${esc(a.identity)}</span>
          ${permChips(a.allow)}
          ${(a.deny || []).map((p) => `<span class="chip chip-red" style="text-decoration:line-through" title="denied">${esc(p)}</span>`).join(" ")}
        </div>`).join("") || `<div class="ci-meta" style="padding:2px 8px">no explicit ACLs (inherited only)</div>`}
    </div>`).join("") || `<div class="empty">no repositories</div>`;
  return `<h4 class="acc-h">teams &amp; members</h4>${teams}
    <h4 class="acc-h">repository permissions <span class="ci-meta">(grants to the QuestOps service account are hidden)${d.repo_cap_note ? " · first 60 repos" : ""}</span></h4>${repos}`;
}

function accJiraHtml(d) {
  if (!d.schemes.length) return `<div class="empty">no permission schemes (${srcLabel(d)})</div>`;
  const juBanner = (d.jirauser_grants || []).length ? `
    <div class="remote-banner remote-new" style="margin-bottom:10px">
      <b>🚩 ${d.jirauser_grants.length} grant(s) to JIRAUSER-keyed users</b>
      ${d.jirauser_grants.map((g) => `<div class="ci-meta">• ${esc(g.holder)} in <b>${esc(g.scheme)}</b></div>`).join("")}
    </div>` : "";
  return `<div class="ci-meta" style="margin-bottom:8px">${srcLabel(d)} · ${d.schemes.length} scheme(s)${d.project_count != null ? ` · ${d.project_count} project(s) checked` : ""}${d.projects_truncated ? " (truncated)" : ""}</div>`
    + juBanner
    + d.schemes.map((s) => `
      <details class="filebox">
        <summary>🎫 <b>${esc(s.name)}</b> ${extLink(s.url)}
          ${(s.projects || []).length
            ? s.projects.slice(0, 12).map((p) => `<a class="chip chip-green" href="${esc(p.url)}" target="_blank" rel="noopener">${esc(p.key)}</a>`).join(" ")
              + (s.projects.length > 12 ? `<span class="ci-meta">+${s.projects.length - 12} more</span>` : "")
            : '<span class="chip">unassigned</span>'}
          <span class="ci-meta">${esc(s.description || "")}</span></summary>
        <div style="padding:8px 12px">
          ${s.holders.map((h) => `
            <div class="acc-acl"><span class="acc-ident ${h.flag ? "acc-flag" : ""}">${h.type === "group" ? "👥" : h.type === "user" ? "👤" : "🎭"} ${esc(h.holder)}
              ${h.flag ? '<span class="chip chip-red" title="JIRAUSER-keyed user grant">🚩</span>' : ""}</span>
              ${permChips(h.permissions)}</div>`).join("")}
        </div>
      </details>`).join("");
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
  }
}

async function renderAccess() {
  view().innerHTML = `
    <div class="view-head"><h1>ACCESS MANAGEMENT</h1>
      <span class="sub">who can do what — ADO projects &amp; repos · Jira permission schemes · Jenkins matrix RBAC</span>
      <span class="spacer"></span>
      <button class="btn btn-sm" id="acc-refresh">↻ refresh all (bypasses caches)</button></div>
    <div class="kpi-note" style="margin-bottom:12px">source systems are protected: results cache for 15 minutes,
      ADO project details load only when expanded, and Jenkins configs come from a shared cache</div>
    <div class="panel" style="margin-bottom:18px"><h2>⛁ Azure DevOps — projects &amp; repository permissions</h2>
      <div id="acc-ado"></div></div>
    <div class="ci-grid">
      <div class="panel"><h2>🎫 Jira — permission schemes &amp; assignments</h2>
        <div id="acc-jira"></div></div>
      <div class="panel"><h2>⚙ Jenkins — matrix-based RBAC</h2>
        <div id="acc-jenkins"></div></div>
    </div>`;

  const load = (refresh) => {
    const s = refresh ? "?refresh=true" : "";
    accLoad("ado", `/api/access/ado${s}`, accAdoHtml);
    accLoad("jira", `/api/access/jira${s}`, accJiraHtml);
    accLoad("jenkins", `/api/access/jenkins${s}`, accJenkinsHtml);
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
        · base: <b>${esc(data.lookup_config.eol_api_base)}</b>
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
