"""Run the dashboard CI locally (or in GitHub Actions) and report to Discord.

Mirrors the jira/ci convention: each step runs, results are consolidated into
report.json + report.md, and a summary embed (+ screenshots / report.md
attached) is posted to a Discord webhook when DISCORD_WEBHOOK_URL is set.
Notifications never fail the build.

    python localdev/run_ci.py                 # run all steps + post to Discord
    python localdev/run_ci.py --no-screens     # skip the browser screenshots
    DISCORD_WEBHOOK_URL=... python localdev/run_ci.py
    python localdev/run_ci.py --dry-run        # print the Discord payload, don't send

Exit code is non-zero if a REQUIRED step (compile / smoke test) fails;
screenshots are best-effort.
"""

from __future__ import annotations

import argparse
import glob
import html as _html
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(HERE, "ci_report")
SHOTS = os.path.join(HERE, "screenshots")
PY = sys.executable

PASS, FAIL, WARN, SKIP = "✅", "❌", "⚠️", "⏭️"
GREEN, RED = 0x2ECC71, 0xE74C3C
# Discord 403-Forbids the default Python-urllib User-Agent — send a real one.
UA = "DashboardCI/1.0 (+https://github.com/AdhamWagih93/streamlit-playground)"


def _run(title: str, cmd: list, required: bool) -> dict:
    print(f"\n=== {title} ===")
    try:
        p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=900)
        out = (p.stdout + p.stderr).strip()
        tail = "\n".join(out.splitlines()[-12:])
        print(tail)
        ok = p.returncode == 0
    except Exception as exc:
        out, tail, ok = str(exc), str(exc), False
    headline = (tail.splitlines()[-1] if tail else "") or ("ok" if ok else "failed")
    return {"title": title, "required": required,
            "status": "passed" if ok else "failed",
            "headline": headline[:300], "output": out[-4000:]}


def _git(*args) -> str:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                              text=True).stdout.strip()
    except Exception:
        return ""


def build_report(jobs: list) -> dict:
    required_failed = any(j["status"] == "failed" and j["required"] for j in jobs)
    return {
        "project": "CI/CD Dashboard",
        "overall_status": "failed" if required_failed else "passed",
        "commit": _git("rev-parse", "--short", "HEAD") or "?",
        "branch": _git("rev-parse", "--abbrev-ref", "HEAD") or "?",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ"),
        "jobs": jobs,
    }


def write_report(report: dict) -> None:
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "report.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    lines = [f"# CI/CD Dashboard CI — "
             f"{'✅ Passed' if report['overall_status'] == 'passed' else '❌ Failed'}",
             "",
             f"commit `{report['commit']}` · branch `{report['branch']}` · "
             f"{report['generated_at']}", ""]
    for j in report["jobs"]:
        icon = PASS if j["status"] == "passed" else FAIL
        req = "" if j["required"] else " _(best-effort)_"
        lines.append(f"- {icon} **{j['title']}**{req} — {j['headline']}")
    n_shots = len(glob.glob(os.path.join(SHOTS, "*.png")))
    lines += ["", f"Screenshots captured: **{n_shots}**"]
    with open(os.path.join(OUT, "report.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def changelog(n: int = 8) -> list:
    """Recent commits for the report's change log."""
    fmt = "%h\x1f%ad\x1f%an\x1f%s"
    out = subprocess.run(
        ["git", "log", f"-{n}", f"--pretty=format:{fmt}", "--date=short"],
        cwd=ROOT, capture_output=True, text=True).stdout
    rows = []
    for line in out.splitlines():
        p = line.split("\x1f")
        if len(p) == 4:
            rows.append({"sha": p[0], "date": p[1], "author": p[2], "subject": p[3]})
    return rows


def write_html_report(report: dict, log: list) -> str:
    """Render a professional, self-contained HTML CI report card."""
    passed = report["overall_status"] == "passed"
    n_pass = sum(1 for j in report["jobs"] if j["status"] == "passed")
    n_tot = len(report["jobs"])
    n_shots = len(glob.glob(os.path.join(SHOTS, "*.png")))
    job_rows = "".join(
        f'<div class="row {"ok" if j["status"] == "passed" else "bad"}">'
        f'<span class="ic">{"✓" if j["status"] == "passed" else "✗"}</span>'
        f'<span class="nm">{_html.escape(j["title"])}'
        + ('' if j["required"] else '<span class="opt">best-effort</span>')
        + f'</span>'
        f'<span class="hl">{_html.escape(j["headline"])}</span>'
        f'</div>'
        for j in report["jobs"])
    log_rows = "".join(
        f'<div class="cl"><span class="sha">{_html.escape(c["sha"])}</span>'
        f'<span class="cdate">{_html.escape(c["date"])}</span>'
        f'<span class="csub">{_html.escape(c["subject"])}</span>'
        f'<span class="cauth">{_html.escape(c["author"])}</span></div>'
        for c in log)
    accent = "#22c55e" if passed else "#ef4444"
    html_doc = f"""<!doctype html><html><head><meta charset="utf-8"><style>
*{{margin:0;box-sizing:border-box;font-family:'Segoe UI',system-ui,sans-serif}}
body{{background:#0b1220;padding:28px;width:960px}}
.card{{background:linear-gradient(180deg,#111a2e,#0d1526);border:1px solid #1e2b45;
  border-radius:18px;overflow:hidden;box-shadow:0 18px 50px rgba(0,0,0,.5)}}
.hdr{{display:flex;align-items:center;gap:18px;padding:22px 26px;
  border-bottom:1px solid #1e2b45;background:
  radial-gradient(900px 120px at 0% 0%,{accent}22,transparent)}}
.badge{{font-size:1.05rem;font-weight:800;letter-spacing:.04em;color:#fff;
  background:{accent};border-radius:999px;padding:7px 16px;white-space:nowrap}}
.htxt h1{{font-size:1.25rem;color:#e8eefc;font-weight:800}}
.htxt .sub{{font-size:.82rem;color:#8aa0c6;font-family:ui-monospace,monospace;margin-top:3px}}
.stats{{margin-left:auto;display:flex;gap:22px;text-align:right}}
.stat b{{display:block;font-size:1.5rem;color:#e8eefc;font-family:ui-monospace,monospace}}
.stat span{{font-size:.62rem;letter-spacing:.1em;text-transform:uppercase;color:#7e93b8}}
.sec{{padding:16px 26px}}
.sec h2{{font-size:.66rem;letter-spacing:.14em;text-transform:uppercase;color:#7e93b8;
  margin-bottom:10px;font-weight:800}}
.row{{display:flex;align-items:center;gap:12px;padding:9px 12px;border-radius:10px;
  margin-bottom:6px;background:#0e1830;border:1px solid #1a2742}}
.row .ic{{font-weight:900;width:18px;text-align:center}}
.row.ok .ic{{color:#22c55e}} .row.bad .ic{{color:#ef4444}}
.row.bad{{background:#241019;border-color:#5b1d24}}
.row .nm{{font-weight:700;color:#dce6fb;min-width:280px}}
.row .opt{{font-size:.6rem;color:#7e93b8;margin-left:8px;font-weight:600;
  border:1px solid #2a3a5c;border-radius:4px;padding:1px 6px}}
.row .hl{{margin-left:auto;font-family:ui-monospace,monospace;font-size:.78rem;
  color:#9fb3d6;text-align:right}}
.cl{{display:grid;grid-template-columns:64px 86px 1fr auto;gap:12px;align-items:baseline;
  padding:6px 12px;border-bottom:1px dashed #182542;font-size:.8rem}}
.cl .sha{{font-family:ui-monospace,monospace;color:{accent}}}
.cl .cdate{{font-family:ui-monospace,monospace;color:#7e93b8;font-size:.72rem}}
.cl .csub{{color:#cdd9f2;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.cl .cauth{{color:#7e93b8;font-size:.72rem}}
.ft{{padding:12px 26px;border-top:1px solid #1e2b45;color:#6f86ad;font-size:.72rem;
  font-family:ui-monospace,monospace;display:flex;justify-content:space-between}}
</style></head><body><div class="card">
<div class="hdr"><div class="badge">{'PASSED' if passed else 'FAILED'}</div>
<div class="htxt"><h1>CI/CD Dashboard — CI report</h1>
<div class="sub">commit {report['commit']} · {report['branch']} · {report['generated_at']}</div></div>
<div class="stats">
<div class="stat"><b>{n_pass}/{n_tot}</b><span>steps</span></div>
<div class="stat"><b>{n_shots}</b><span>screens</span></div></div></div>
<div class="sec"><h2>Test results</h2>{job_rows}</div>
<div class="sec"><h2>Change log</h2>{log_rows or '<div class="cl"><span class="csub">no commits</span></div>'}</div>
<div class="ft"><span>localdev fake seam · no VPN / Docker / live services</span>
<span>Dashboard CI</span></div>
</div></body></html>"""
    path = os.path.join(OUT, "report.html")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html_doc)
    return path


def render_png(html_path: str, png_path: str) -> bool:
    """HTML → PNG via Playwright/Chromium. Best-effort: returns False if no
    browser is available (e.g. a bare sandbox)."""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": 960, "height": 700},
                                    device_scale_factor=2)
            page.goto("file://" + os.path.abspath(html_path),
                      wait_until="networkidle")
            page.locator(".card").screenshot(path=png_path)
            browser.close()
        return True
    except Exception as exc:
        print(f"{WARN} report PNG render skipped: {type(exc).__name__}: {exc}")
        return False


def discord_payload(report: dict) -> dict:
    fields = [{"name": j["title"],
               "value": f"{PASS if j['status'] == 'passed' else FAIL} {j['headline']}"[:1024],
               "inline": False}
              for j in report["jobs"]]
    embed = {
        "title": f"CI/CD Dashboard CI — "
                 f"{'✅ Passed' if report['overall_status'] == 'passed' else '❌ Failed'}",
        "color": GREEN if report["overall_status"] == "passed" else RED,
        "fields": fields,
        "footer": {"text": f"commit {report['commit']} · {report['branch']} · "
                           f"{report['generated_at']}"},
    }
    return {"username": "Dashboard CI", "embeds": [embed]}


def post_discord(webhook: str, payload: dict, files: list, dry_run: bool) -> None:
    if dry_run or not webhook:
        why = "dry-run" if dry_run else "no DISCORD_WEBHOOK_URL set"
        print(f"\n[discord {why}] would post:")
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        if files:
            print("attachments:", ", ".join(os.path.basename(f) for f in files))
        return
    files = [f for f in files if os.path.exists(f) and os.path.getsize(f) <= 8 * 1024 * 1024][:10]
    try:
        if files:
            b = "----dashCIb0undary7f3a2b"
            parts = [f"--{b}\r\n".encode(),
                     b'Content-Disposition: form-data; name="payload_json"\r\n',
                     b"Content-Type: application/json\r\n\r\n",
                     json.dumps(payload).encode() + b"\r\n"]
            for i, path in enumerate(files):
                parts += [f"--{b}\r\n".encode(),
                          f'Content-Disposition: form-data; name="files[{i}]"; '
                          f'filename="{os.path.basename(path)}"\r\n'.encode(),
                          b"Content-Type: application/octet-stream\r\n\r\n",
                          open(path, "rb").read(), b"\r\n"]
            parts.append(f"--{b}--\r\n".encode())
            req = urllib.request.Request(
                webhook, data=b"".join(parts),
                headers={"Content-Type": f"multipart/form-data; boundary={b}",
                         "User-Agent": UA})
        else:
            req = urllib.request.Request(
                webhook, data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json", "User-Agent": UA})
        with urllib.request.urlopen(req, timeout=20) as r:
            r.read()
        print("Posted CI report to Discord.")
    except Exception as exc:  # never fail the build on a notification error
        print(f"{WARN} Discord post failed (non-fatal): {exc}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-screens", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--webhook", default=os.environ.get("DISCORD_WEBHOOK_URL", ""))
    args = ap.parse_args()

    os.makedirs(OUT, exist_ok=True)
    jobs = [
        _run("Compile", [PY, "-m", "py_compile", "cicd_dashboard.py", "cc_docchat.py"], True),
        _run("Seed git", [PY, "localdev/seed_git.py"], True),
        _run("Seed ES fixtures", [PY, "localdev/seed_es_fixtures.py"], True),
        _run("Smoke test (all tabs render)",
             [PY, "-m", "pytest", "localdev/test_smoke.py", "-q",
              f"--junitxml={os.path.join(OUT, 'junit.xml')}"], True),
    ]
    if not args.no_screens:
        jobs.append(_run("Screenshots (every tab)", [PY, "localdev/screenshot.py"], False))

    report = build_report(jobs)
    write_report(report)
    # Visual report card (test results + change log) → PNG, attached first.
    html_path = write_html_report(report, changelog())
    png = os.path.join(OUT, "report.png")
    files: list = []
    if render_png(html_path, png):
        files.append(png)
    files += [os.path.join(OUT, "report.md")]
    files += sorted(glob.glob(os.path.join(SHOTS, "*.png")))
    post_discord(args.webhook, discord_payload(report), files, args.dry_run)

    print(f"\nOverall: {report['overall_status'].upper()}  "
          f"(report → {os.path.relpath(OUT, ROOT)}/report.json + report.md)")
    return 0 if report["overall_status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
