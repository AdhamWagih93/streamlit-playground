/* Shared helpers for all Mission Control pages. Vanilla, no deps. */

// Live mission clock in the top bar (UTC-ish, just for vibe)
function startClock(sel) {
  const el = document.querySelector(sel);
  if (!el) return;
  const base = new Date(2026, 5, 22, 9, 14, 3); // fixed "demo day" morning
  let t = base.getTime();
  const tick = () => {
    t += 1000;
    const d = new Date(t);
    const p = n => String(n).padStart(2, '0');
    el.textContent = `T+ ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())} · MISSION DAY`;
  };
  tick();
  setInterval(tick, 1000);
}

// Append a line to a .term log with a small delay-driven reveal.
function logLine(term, who, whoClass, msg, time) {
  const ln = document.createElement('div');
  ln.className = 'ln';
  ln.innerHTML =
    `<span class="t">${time || nowStamp()}</span>` +
    `<span class="who ${whoClass}">${who}</span>` +
    `<span class="msg">${msg}</span>`;
  term.appendChild(ln);
  term.scrollTop = term.scrollHeight;
  return ln;
}

function nowStamp() {
  const d = new Date();
  const p = n => String(n).padStart(2, '0');
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

// Animate a number from a→b inside an element.
function countTo(el, to, opts = {}) {
  const dur = opts.dur || 900, dec = opts.dec || 0, suffix = opts.suffix || '', prefix = opts.prefix || '';
  const from = parseFloat(el.dataset.from || '0');
  const start = performance.now();
  function frame(now) {
    const p = Math.min(1, (now - start) / dur);
    const e = 1 - Math.pow(1 - p, 3);
    const val = (from + (to - from) * e).toFixed(dec);
    el.textContent = prefix + Number(val).toLocaleString() + suffix;
    if (p < 1) requestAnimationFrame(frame);
    else el.dataset.from = to;
  }
  requestAnimationFrame(frame);
}

// Promise-based delay for orchestrating demo sequences.
const wait = ms => new Promise(r => setTimeout(r, ms));

// Stagger reveal of elements matching selector.
function stagger(sel, step = 90) {
  document.querySelectorAll(sel).forEach((el, i) => {
    el.style.animationDelay = (i * step) + 'ms';
    el.classList.add('reveal');
  });
}

// Inject the shared top bar. backHref null on hub.
function mountTopbar({ title, code, backHref }) {
  const bar = document.createElement('div');
  bar.className = 'topbar';
  bar.innerHTML = `
    <div class="brand"><span class="dot"></span> DEVOPS&nbsp;//&nbsp;2026
      <small>${code || ''}</small>
    </div>
    <div class="spacer"></div>
    <div class="clock" id="clock"></div>
    ${backHref ? `<a class="backlink" href="${backHref}">◂ MISSION CONTROL</a>` : ''}
  `;
  document.body.prepend(bar);
  startClock('#clock');
}
