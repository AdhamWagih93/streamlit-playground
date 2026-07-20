"""Capture full-page screenshots of every dashboard tab with a real browser.

Boots `streamlit run localdev/front_local.py` headless against the local fake
seam, then drives Chromium (Playwright) to open each tab — clicking the
"▶ Load …" button for lazy tabs — and saves a PNG per tab under
localdev/screenshots/. Best-effort: a tab that fails to capture is logged and
skipped so the rest still produce artifacts.

Local:  python -m pip install playwright && playwright install chromium
        python localdev/seed_git.py && python localdev/seed_es_fixtures.py
        python localdev/screenshot.py
CI:     see .github/workflows/ci.yml (uploads screenshots/ as an artifact).
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SHOTS = os.path.join(HERE, "screenshots")
APP = os.path.join(HERE, "front_local.py")

# Tab substrings to capture, in strip order. Each becomes one screenshot.
TABS = [
    ("01_inventory", "INVENTORY"),
    ("02_teams", "TEAMS"),
    ("03_event_log", "EVENT LOG"),
    ("04_actions", "ACTIONS"),
    ("05_sync_check", "SYNC CHECK"),
    ("06_history", "HISTORY"),
    ("07_ado_coverage", "ADO COVERAGE"),
    ("08_architecture", "ARCHITECTURE"),
    ("09_tech_platforms", "TECH & PLATFORMS"),
]


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_health(port: int, timeout: float = 90) -> bool:
    url = f"http://127.0.0.1:{port}/_stcore/health"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(1)
    return False


def _settle(page, ms: int = 1500) -> None:
    """Wait for Streamlit's running indicator to clear, then a short beat."""
    try:
        page.wait_for_selector('[data-testid="stStatusWidget"]',
                               state="detached", timeout=20000)
    except Exception:
        pass
    try:
        page.wait_for_load_state("networkidle", timeout=20000)
    except Exception:
        pass
    page.wait_for_timeout(ms)


def main() -> int:
    os.makedirs(SHOTS, exist_ok=True)
    from playwright.sync_api import sync_playwright  # imported lazily

    port = _free_port()
    env = dict(os.environ)
    # Pre-open every lazy tab so switching tabs never triggers a Load-button
    # rerun (which would reset st.tabs to the first tab and ruin the capture).
    env["LOCALDEV_EAGER_TABS"] = "1"
    env.setdefault("LOCALDEV_ADO_FIXTURE",
                   os.path.join(HERE, "fixtures", "ado_snapshot.json"))
    proc = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", APP,
         "--server.headless", "true", "--server.port", str(port),
         "--server.address", "127.0.0.1",
         "--browser.gatherUsageStats", "false"],
        cwd=ROOT, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    captured = 0
    try:
        if not _wait_health(port, 120):
            print("Streamlit did not become healthy in time", file=sys.stderr)
            return 1
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": 1600, "height": 1000},
                                    device_scale_factor=1)
            page.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded")
            page.wait_for_selector('[data-testid="stAppViewContainer"]',
                                   timeout=60000)
            _settle(page, 2500)
            # Streamlit's tab DOM has drifted across versions (BaseWeb buttons
            # → plain elements). ARIA role="tab" is the stable contract, with
            # the old BaseWeb selector as a fallback for older versions.
            def _find_tab(needle: str):
                pat = re.compile(needle, re.I)
                for loc in (page.get_by_role("tab", name=pat),
                            page.locator('button[data-baseweb="tab"]',
                                         has_text=pat)):
                    el = loc.first
                    if el.count() > 0:
                        return el
                return None

            _n_tabs = page.get_by_role("tab").count()
            print(f"  (page exposes {_n_tabs} role=tab elements)")
            for fname, needle in TABS:
                try:
                    tab = _find_tab(needle)
                    if tab is None:
                        print(f"  (skip {fname}: tab '{needle}' not present)")
                        continue
                    tab.scroll_into_view_if_needed()
                    tab.click()
                    _settle(page, 1500)
                    # Tabs are pre-opened (LOCALDEV_EAGER_TABS) so no "Load …"
                    # button / rerun — the switch is pure client-side and sticks.
                    out = os.path.join(SHOTS, f"{fname}.png")
                    # Capture the TABS WIDGET (tab bar + the active tab's
                    # content) rather than the whole page — the dashboard has a
                    # tall filter rail / stat tiles above the tabs, so a
                    # full-page shot buries the tab content and every tab looks
                    # the same up top. Shooting the tabs container isolates what
                    # actually changes per tab. Falls back to full_page.
                    shot_el = None
                    for sel in ('.st-key-cc_surface_tabs',
                                '[data-testid="stTabs"]'):
                        el = page.locator(sel).first
                        if el.count() > 0:
                            shot_el = el
                            break
                    if shot_el is not None:
                        try:
                            shot_el.scroll_into_view_if_needed()
                        except Exception:
                            pass
                        page.wait_for_timeout(300)
                        shot_el.screenshot(path=out)
                    else:
                        page.screenshot(path=out, full_page=True)
                    captured += 1
                    print(f"  captured {fname}.png")
                except Exception as e:  # best effort per tab
                    print(f"  (failed {fname}: {type(e).__name__}: {e})")
            browser.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
    print(f"Captured {captured}/{len(TABS)} tabs into {SHOTS}")
    return 0 if captured else 1


if __name__ == "__main__":
    raise SystemExit(main())
