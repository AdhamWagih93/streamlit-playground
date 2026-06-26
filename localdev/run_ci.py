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
    files = [os.path.join(OUT, "report.md")] + sorted(glob.glob(os.path.join(SHOTS, "*.png")))
    post_discord(args.webhook, discord_payload(report), files, args.dry_run)

    print(f"\nOverall: {report['overall_status'].upper()}  "
          f"(report → {os.path.relpath(OUT, ROOT)}/report.json + report.md)")
    return 0 if report["overall_status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
