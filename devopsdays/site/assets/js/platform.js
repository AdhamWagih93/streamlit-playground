/* DevOps Platform demo — shared shell: brand, sidebar, topbar, helpers.
   Brand name + logo live in ONE place: BRAND below. */

const BRAND = {
  name: "DevOps Platform",
  logo: "assets/img/efdevopslogo.png",
  event: "DevOpsDays Cairo 2026",
};

const PAGES = [
  { id: "intro",        href: "intro.html",        ico: "▸", label: "Intro",                   key: "1", section: "DEVOPSDAYS" },
  { id: "lastyear",     href: "lastyear.html",     ico: "⟲", label: "Last Year",               key: "2" },
  { id: "overview",     href: "index.html",        ico: "◈", label: "Overview",               key: "3", section: "PLATFORM" },
  { id: "ai",           href: "ai.html",           ico: "✳", label: "Embedded AI",             key: "4", section: "AI SERVICES" },
  { id: "assistant",    href: "assistant.html",    ico: "✦", label: "Knowledge Assistant",     key: "5" },
  { id: "architecture", href: "architecture.html", ico: "⌬", label: "Architecture Discovery",  key: "6" },
  { id: "incident",     href: "incident.html",     ico: "⚠", label: "Incident Analysis",      key: "7" },
  { id: "value",        href: "value.html",        ico: "◎", label: "Today",                   key: "8", section: "OUTCOMES" },
  { id: "closing",      href: "closing.html",      ico: "✧", label: "Closing",                 key: "9" },
];

function renderShell(activeId, crumbTail) {
  // ── sidebar ──
  const nav = PAGES.map((p) => {
    const sec = p.section ? `<div class="nav-label">${p.section}</div>` : "";
    return `${sec}<a href="${p.href}" class="${p.id === activeId ? "active" : ""}">
      <span class="nav-ico">${p.ico}</span>${p.label}
      <span class="nav-key">${p.key}</span></a>`;
  }).join("");

  document.getElementById("sidebar").innerHTML = `
    <div class="brand">
      <div class="brand-logo"><img src="${BRAND.logo}" alt="${BRAND.name}"></div>
    </div>
    <div class="nav">
      ${nav}
    </div>
    <div class="sidebar-foot">
      <span class="gov-badge">GOVERNED · ON-PREM AI</span>
      <span>All AI runs inside the enterprise boundary.<br>Every interaction is audited.</span>
    </div>`;

  // ── topbar ──
  document.getElementById("topbar").innerHTML = `
    <div class="crumb"><b>${BRAND.name}</b><span class="sep">/</span>${crumbTail}</div>
    <div class="topbar-right">
      <span class="env-pill">tenant <b>enterprise-core</b></span>
      <span class="clock" id="clock"></span>
      <div class="avatar">AW</div>
    </div>`;

  // clock
  const tick = () => {
    const d = new Date();
    document.getElementById("clock").textContent =
      d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  };
  tick(); setInterval(tick, 1000);

  // ── sidebar / topbar visibility (persists across pages for clean recording) ──
  const sb = document.getElementById("sidebar");
  const applySb = () => {
    document.body.classList.toggle("nosb", localStorage.getItem("mer_sb") === "hidden");
    document.body.classList.toggle("notb", localStorage.getItem("mer_tb") === "hidden");
  };
  const toggleSb = () => {
    localStorage.setItem("mer_sb", localStorage.getItem("mer_sb") === "hidden" ? "shown" : "hidden");
    applySb();
  };
  const toggleTb = () => {
    localStorage.setItem("mer_tb", localStorage.getItem("mer_tb") === "hidden" ? "shown" : "hidden");
    applySb();
  };
  const tg = document.createElement("button");
  tg.className = "sb-collapse"; tg.title = "Hide sidebar (H)"; tg.innerHTML = "⟨";
  tg.addEventListener("click", toggleSb);
  sb.appendChild(tg);
  const restore = document.createElement("button");
  restore.className = "sb-restore"; restore.title = "Show sidebar (H)"; restore.innerHTML = "⟩";
  restore.addEventListener("click", toggleSb);
  document.body.appendChild(restore);
  const tbRestore = document.createElement("button");
  tbRestore.className = "tb-restore"; tbRestore.title = "Show top bar (T)"; tbRestore.innerHTML = "⌄";
  tbRestore.addEventListener("click", toggleTb);
  document.body.appendChild(tbRestore);
  // company logo stays on screen when the sidebar is hidden
  const floatLogo = document.createElement("div");
  floatLogo.className = "float-logo"; floatLogo.title = "Show sidebar (H)";
  floatLogo.innerHTML = `<img src="${BRAND.logo}" alt="${BRAND.name}">`;
  floatLogo.addEventListener("click", toggleSb);
  document.body.appendChild(floatLogo);
  applySb();

  // hotkeys (recording aid, nothing shown on screen):
  //   1–8  jump between scenes · H toggle sidebar · Enter run the page's main action
  document.addEventListener("keydown", (e) => {
    // Escape always restores both bars, even from inside an input
    if (e.key === "Escape") {
      localStorage.setItem("mer_sb", "shown");
      localStorage.setItem("mer_tb", "shown");
      applySb();
      if (document.activeElement && document.activeElement.blur) document.activeElement.blur();
      return;
    }
    const t = e.target.tagName;
    if (t === "INPUT" || t === "TEXTAREA") return;
    if (e.key === "h" || e.key === "H") { toggleSb(); return; }
    if (e.key === "t" || e.key === "T") { toggleTb(); return; }
    if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
      const i = PAGES.findIndex((x) => x.id === activeId);
      const next = PAGES[i + (e.key === "ArrowRight" ? 1 : -1)];
      if (next) window.location.href = next.href;
      return;
    }
    if (e.key === "Enter" && t !== "BUTTON") {
      const btn = document.querySelector("[data-main-action]:not([disabled])");
      if (btn) { e.preventDefault(); btn.click(); }
      return;
    }
    const p = PAGES.find((x) => x.key === e.key);
    if (p) window.location.href = p.href;
  });

  document.title = `${BRAND.name} — ${crumbTail}`;
}

/* type text into el, resolves when done */
function typeInto(el, html, speed = 14) {
  return new Promise((resolve) => {
    el.classList.add("caret");
    // tokenize so tags are inserted atomically
    const tokens = html.match(/<[^>]+>|./gs) || [];
    let i = 0, buf = "";
    (function step() {
      if (i >= tokens.length) { el.classList.remove("caret"); resolve(); return; }
      buf += tokens[i++];
      el.innerHTML = buf;
      const t = tokens[i - 1];
      setTimeout(step, t && t.startsWith("<") ? 0 : speed);
    })();
  });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/* count-up for KPI numbers: <span data-count="214" data-suffix="">…</span> */
function countUp(el, dur = 1100) {
  const target = parseFloat(el.dataset.count);
  const dec = (el.dataset.count.split(".")[1] || "").length;
  const suffix = el.dataset.suffix || "";
  const t0 = performance.now();
  (function frame(t) {
    const k = Math.min(1, (t - t0) / dur);
    const eased = 1 - Math.pow(1 - k, 3);
    el.textContent = (target * eased).toFixed(dec) + suffix;
    if (k < 1) requestAnimationFrame(frame);
  })(t0);
}
function initCounters() {
  document.querySelectorAll("[data-count]").forEach((el, i) =>
    setTimeout(() => countUp(el), 150 + i * 120));
}
