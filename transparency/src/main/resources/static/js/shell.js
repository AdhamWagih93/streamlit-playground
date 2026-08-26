/*
 * Transparency viewer shell.
 *
 * Everything here is about the frame we cannot see inside: an iframe gives no progress events and,
 * cross-document, no error events either. So the shell watches three signals instead — the frame's
 * load event, a wall-clock deadline, and a periodic upstream reachability probe — and turns them
 * into one honest state: loading, ready, or unreachable.
 */
(function () {
  "use strict";

  var body = document.body;
  var frame = document.getElementById("report");
  var config = {
    frameUrl: body.getAttribute("data-frame-url"),
    statusUrl: body.getAttribute("data-status-url"),
    loadTimeout: parseInt(body.getAttribute("data-load-timeout"), 10) || 45000
  };

  var STATE = { LOADING: "loading", READY: "ready", ERROR: "error" };
  var loadDeadline = null;

  /* ------------------------------------------------------------ report state */

  function setState(state) {
    body.setAttribute("data-report", state);
    document.getElementById("overlay-loading").hidden = state !== STATE.LOADING;
    document.getElementById("overlay-error").hidden = state !== STATE.ERROR;
  }

  function beginLoad() {
    setState(STATE.LOADING);
    window.clearTimeout(loadDeadline);
    loadDeadline = window.setTimeout(function () {
      failWith(
        "The report is taking too long",
        "The report server has not finished responding. It may be under load — try again in a moment."
      );
    }, config.loadTimeout);
  }

  function completeLoad() {
    window.clearTimeout(loadDeadline);
    setState(STATE.READY);
    stampFetchTime();
    probeStatus();
  }

  function failWith(title, body_) {
    window.clearTimeout(loadDeadline);
    document.getElementById("error-title").textContent = title;
    document.getElementById("error-body").textContent = body_;
    setState(STATE.ERROR);
  }

  function reload() {
    beginLoad();
    // Re-assigning src rather than calling location.reload() inside the frame: the frame is
    // same-origin through the proxy, but the report may have navigated within itself.
    frame.setAttribute("src", config.frameUrl);
  }

  frame.addEventListener("load", function () {
    // A cross-document error page also fires load; the status probe is what tells them apart.
    completeLoad();
  });

  frame.addEventListener("error", function () {
    failWith(
      "The report did not load",
      "The report server did not answer. Your access is unaffected — try again."
    );
  });

  /* ------------------------------------------------------------ provenance */

  function stampFetchTime() {
    var el = document.getElementById("fetched-at");
    var now = new Date();
    el.textContent = now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    el.setAttribute("title", now.toLocaleString());
  }

  /* ------------------------------------------------------------ upstream status */

  var statusEl = document.getElementById("status");
  var statusText = document.getElementById("status-text");
  var statusLatency = document.getElementById("status-latency");

  function renderStatus(state, text, latencyMs) {
    statusEl.setAttribute("data-state", state);
    statusText.textContent = text;
    statusLatency.textContent = typeof latencyMs === "number" ? latencyMs + " ms" : "";
  }

  function probeStatus() {
    renderStatus("checking", "Checking", null);
    fetch(config.statusUrl, { credentials: "same-origin", headers: { Accept: "application/json" } })
      .then(function (response) {
        if (!response.ok) throw new Error("status " + response.status);
        return response.json();
      })
      .then(function (payload) {
        if (payload.reachable) {
          renderStatus("live", "Live", payload.latencyMs);
        } else {
          renderStatus("down", "Unreachable", null);
          if (body.getAttribute("data-report") === STATE.READY) {
            // The frame rendered, but the source has since gone away: say so rather than let a
            // stale canvas imply the figures are current.
            failWith("Lost contact with the report server", payload.detail || "The connection dropped.");
          }
        }
      })
      .catch(function () {
        renderStatus("down", "Unreachable", null);
      });
  }

  window.setInterval(probeStatus, 30000);

  /* ------------------------------------------------------------ panels */

  function panel(toggleId, panelId) {
    var toggle = document.getElementById(toggleId);
    var target = document.getElementById(panelId);

    function open(next) {
      target.hidden = !next;
      toggle.setAttribute("aria-expanded", String(next));
    }

    toggle.addEventListener("click", function () {
      var next = target.hidden;
      closeAll();
      open(next);
    });

    return { close: function () { open(false); }, toggle: function () { var n = target.hidden; closeAll(); open(n); } };
  }

  var panels = [];
  function closeAll() {
    panels.forEach(function (p) { p.close(); });
  }

  var detailsPanel = panel("details-toggle", "details");
  var shortcutsPanel = panel("shortcuts-toggle", "shortcuts");
  panels.push(detailsPanel, shortcutsPanel);

  document.addEventListener("click", function (event) {
    var origin = event.target instanceof Element ? event.target : null;
    if (!origin || !origin.closest(".details, .shortcuts, #details-toggle, #shortcuts-toggle")) {
      closeAll();
    }
  });

  /* ------------------------------------------------------------ theme */

  var THEME_KEY = "transparency.theme";

  function currentTheme() {
    var explicit = document.documentElement.getAttribute("data-theme");
    if (explicit) return explicit;
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }

  function toggleTheme() {
    var next = currentTheme() === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    try {
      localStorage.setItem(THEME_KEY, next);
    } catch (e) { /* private mode: the choice simply does not persist */ }
    document.getElementById("theme").setAttribute("title", "Switch to " + (next === "dark" ? "light" : "dark") + " theme (T)");
  }

  /* ------------------------------------------------------------ controls */

  var refreshButton = document.getElementById("refresh");

  refreshButton.addEventListener("click", function () {
    refreshButton.classList.add("is-spinning");
    window.setTimeout(function () { refreshButton.classList.remove("is-spinning"); }, 700);
    reload();
  });

  document.getElementById("retry").addEventListener("click", reload);
  document.getElementById("theme").addEventListener("click", toggleTheme);

  document.getElementById("fullscreen").addEventListener("click", function () {
    var canvas = document.getElementById("canvas");
    if (document.fullscreenElement) {
      document.exitFullscreen();
    } else if (canvas.requestFullscreen) {
      canvas.requestFullscreen().catch(function () { /* denied by policy: nothing to recover */ });
    }
  });

  document.addEventListener("keydown", function (event) {
    if (event.metaKey || event.ctrlKey || event.altKey) return;
    var target = event.target;
    if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable)) return;

    switch (event.key.toLowerCase()) {
      case "r": reload(); break;
      case "f": document.getElementById("fullscreen").click(); break;
      case "t": toggleTheme(); break;
      case "d": detailsPanel.toggle(); break;
      case "?": shortcutsPanel.toggle(); break;
      case "escape": closeAll(); break;
      default: return;
    }
    event.preventDefault();
  });

  /* ------------------------------------------------------------ start */

  beginLoad();
  probeStatus();

  // The frame may have finished before this deferred script ran; its load event is long gone.
  try {
    if (frame.contentDocument && frame.contentDocument.readyState === "complete") {
      completeLoad();
    }
  } catch (e) { /* not same-origin yet: the load listener will handle it */ }
})();
