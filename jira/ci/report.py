#!/usr/bin/env python3
"""Trackly CI report generator.

Turns raw CI outputs (pytest JSON/coverage, the frontend bundle, docker job
status) into two artifacts per the project's reporting goals:

* ``report.json`` — machine-readable, stable schema (for tooling / agents).
* ``report.md``   — a polished, human-readable visual report.

When ``$GITHUB_STEP_SUMMARY`` is set (GitHub Actions / act), the markdown is also
appended there so it renders in the run's Summary tab.

Subcommands:
    backend     --pytest pytest.json [--coverage coverage.json] --out-dir DIR
    frontend    --dist DIST_DIR [--build-log LOG] --out-dir DIR
    docker      --status passed|failed [--note TEXT] --out-dir DIR
    consolidate --in ARTIFACTS_DIR --out-dir DIR   # merges per-job result.json

Stdlib only. Never raises on missing inputs — it degrades to "unknown".
"""
from __future__ import annotations

import argparse
import datetime as _dt
import glob
import gzip
import json
import os
import sys

PASS = "✅"
FAIL = "❌"
WARN = "⚠️"
SKIP = "⏭️"


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _read_json(path: str | None) -> dict | None:
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (ValueError, OSError):
        return None


def _badge(status: str) -> str:
    return {"passed": f"{PASS} Passed", "failed": f"{FAIL} Failed"}.get(status, f"{WARN} {status.title()}")


def _human_bytes(n: int) -> str:
    val = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if val < 1024 or unit == "GB":
            return f"{val:.0f} {unit}" if unit == "B" else f"{val:.1f} {unit}"
        val /= 1024
    return f"{n} B"


# --------------------------------------------------------------------------- #
# Per-job result builders -> a normalised dict {job,status,headline,metrics,...}
# --------------------------------------------------------------------------- #
def build_backend(pytest_path: str, coverage_path: str | None) -> dict:
    data = _read_json(pytest_path)
    cov = _read_json(coverage_path)
    if not data:
        return {"job": "backend", "title": "Backend · tests", "status": "failed",
                "headline": "no pytest report produced", "metrics": {}, "tables": []}
    summary = data.get("summary", {})
    total = summary.get("total", 0)
    passed = summary.get("passed", 0)
    failed = summary.get("failed", 0) + summary.get("error", 0)
    skipped = summary.get("skipped", 0)
    duration = data.get("duration", 0.0)
    status = "passed" if failed == 0 and total > 0 else "failed"

    metrics = {
        "total": total, "passed": passed, "failed": failed, "skipped": skipped,
        "duration_s": round(duration, 2),
    }
    if cov:
        pct = cov.get("totals", {}).get("percent_covered")
        if pct is not None:
            metrics["coverage_pct"] = round(pct, 1)

    # Failing tests table (or a compact pass note).
    rows = []
    for t in data.get("tests", []):
        if t.get("outcome") not in ("passed",):
            rows.append([t.get("nodeid", "?"), t.get("outcome", "?"),
                         f"{t.get('call', {}).get('duration', t.get('duration', 0)):.2f}s"])
    tables = []
    if rows:
        tables.append({"title": "Failing / non-passing tests",
                       "headers": ["Test", "Outcome", "Time"], "rows": rows})

    cov_txt = f" · coverage {metrics['coverage_pct']}%" if "coverage_pct" in metrics else ""
    headline = f"{passed}/{total} passed in {metrics['duration_s']}s{cov_txt}"
    if failed:
        headline = f"{failed} failed, {passed} passed{cov_txt}"
    return {"job": "backend", "title": "Backend · tests", "status": status,
            "headline": headline, "metrics": metrics, "tables": tables}


def build_frontend(dist_dir: str, build_log: str | None) -> dict:
    assets = []
    total_raw = total_gz = 0
    pattern = os.path.join(dist_dir, "**", "*")
    for path in sorted(glob.glob(pattern, recursive=True)):
        if not os.path.isfile(path):
            continue
        ext = os.path.splitext(path)[1].lower()
        if ext not in (".js", ".css", ".html"):
            continue
        raw = os.path.getsize(path)
        with open(path, "rb") as fh:
            gz = len(gzip.compress(fh.read(), 6))
        total_raw += raw
        total_gz += gz
        assets.append([os.path.relpath(path, dist_dir), _human_bytes(raw), _human_bytes(gz)])

    built = len(assets) > 0
    status = "passed" if built else "failed"
    metrics = {"assets": len(assets), "bundle_raw_bytes": total_raw, "bundle_gzip_bytes": total_gz}
    headline = (f"{len(assets)} assets · {_human_bytes(total_raw)} raw / "
                f"{_human_bytes(total_gz)} gzip") if built else "build produced no assets"
    tables = []
    if assets:
        assets.append(["TOTAL", _human_bytes(total_raw), _human_bytes(total_gz)])
        tables.append({"title": "Bundle assets", "headers": ["Asset", "Raw", "Gzip"], "rows": assets})
    return {"job": "frontend", "title": "Frontend · build", "status": status,
            "headline": headline, "metrics": metrics, "tables": tables}


def build_docker(status: str, note: str | None) -> dict:
    status = "passed" if status == "passed" else "failed"
    headline = note or ("compose validated · images built" if status == "passed"
                        else "compose/image build failed")
    return {"job": "docker", "title": "Docker · compose & images", "status": status,
            "headline": headline, "metrics": {}, "tables": []}


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def render_job_md(result: dict) -> str:
    lines = [f"## {result['title']} — {_badge(result['status'])}", "", f"_{result['headline']}_", ""]
    if result.get("metrics"):
        lines.append("| Metric | Value |")
        lines.append("|--------|-------|")
        for k, v in result["metrics"].items():
            label = k.replace("_", " ").replace("bytes", "(bytes)").title()
            if k.endswith("_bytes"):
                v = _human_bytes(int(v))
            lines.append(f"| {label} | {v} |")
        lines.append("")
    for tbl in result.get("tables", []):
        lines.append(f"**{tbl['title']}**")
        lines.append("")
        lines.append("| " + " | ".join(tbl["headers"]) + " |")
        lines.append("|" + "|".join("---" for _ in tbl["headers"]) + "|")
        for row in tbl["rows"]:
            lines.append("| " + " | ".join(str(c) for c in row) + " |")
        lines.append("")
    return "\n".join(lines)


def render_report_md(report: dict) -> str:
    overall = report["overall_status"]
    icon = PASS if overall == "passed" else FAIL
    head = (f"# {icon} Trackly CI Report\n\n"
            f"**Status:** {_badge(overall)}  ·  **Commit:** `{report['commit']}`  ·  "
            f"**Branch:** `{report['branch']}`  ·  {report['generated_at']}\n")
    summary = ["## Summary", "", "| Check | Status | Details |", "|-------|--------|---------|"]
    for job in report["jobs"]:
        summary.append(f"| {job['title']} | {_badge(job['status'])} | {job['headline']} |")
    parts = [head, "\n".join(summary), ""]
    for job in report["jobs"]:
        parts.append(render_job_md(job))
    parts.append(f"\n---\n_Generated by `ci/report.py` · {report['generated_at']}_")
    return "\n".join(parts)


def _write_outputs(out_dir: str, report: dict) -> None:
    os.makedirs(out_dir, exist_ok=True)
    md = render_report_md(report)
    with open(os.path.join(out_dir, "report.md"), "w", encoding="utf-8") as fh:
        fh.write(md)
    with open(os.path.join(out_dir, "report.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as fh:
            fh.write(md + "\n")
    # Always echo to stdout so local/act runs are visible in the log.
    print(md)


def _meta() -> dict:
    sha = os.environ.get("GITHUB_SHA", "")[:7] or "local"
    ref = os.environ.get("GITHUB_REF_NAME") or os.environ.get("GITHUB_HEAD_REF") or "local"
    return {"commit": sha, "branch": ref, "generated_at": _now()}


def _single_job_report(result: dict) -> dict:
    meta = _meta()
    return {**meta, "overall_status": result["status"], "jobs": [result]}


def cmd_backend(args) -> int:
    result = build_backend(args.pytest, args.coverage)
    _save_result(args.out_dir, result)
    _write_outputs(args.out_dir, _single_job_report(result))
    return 0


def cmd_frontend(args) -> int:
    result = build_frontend(args.dist, args.build_log)
    _save_result(args.out_dir, result)
    _write_outputs(args.out_dir, _single_job_report(result))
    return 0


def cmd_docker(args) -> int:
    result = build_docker(args.status, args.note)
    _save_result(args.out_dir, result)
    _write_outputs(args.out_dir, _single_job_report(result))
    return 0


def _save_result(out_dir: str, result: dict) -> None:
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "result.json"), "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2)


_JOB_ORDER = {"backend": 0, "frontend": 1, "docker": 2}

_DISCORD_GREEN = 0x2ECC71
_DISCORD_RED = 0xE74C3C


def build_discord_payload(report: dict) -> dict:
    """Build a Discord webhook embed summarising a consolidated report."""
    status = report.get("overall_status", "unknown")
    title = f"Trackly CI — {'✅ Passed' if status == 'passed' else '❌ Failed'}"
    fields = []
    for job in report.get("jobs", []):
        icon = PASS if job["status"] == "passed" else FAIL
        fields.append({"name": job["title"], "value": f"{icon} {job['headline']}"[:1024], "inline": False})
    embed = {
        "title": title,
        "color": _DISCORD_GREEN if status == "passed" else _DISCORD_RED,
        "fields": fields,
        "footer": {"text": f"commit {report.get('commit')} · {report.get('branch')} · {report.get('generated_at')}"},
    }
    return {"username": "Trackly CI", "embeds": [embed]}


def _post_discord(webhook: str, payload: dict, files: list[str], dry_run: bool) -> int:
    import urllib.request

    if dry_run:
        print("[discord dry-run] payload:")
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        if files:
            print("[discord dry-run] attachments:", ", ".join(os.path.basename(f) for f in files))
        return 0

    existing = [f for f in files if os.path.exists(f)]
    try:
        if existing:
            boundary = "----TracklyCIyboundary7f3a2b"
            parts: list[bytes] = []
            parts.append(f"--{boundary}\r\n".encode())
            parts.append(b'Content-Disposition: form-data; name="payload_json"\r\n')
            parts.append(b"Content-Type: application/json\r\n\r\n")
            parts.append(json.dumps(payload).encode("utf-8") + b"\r\n")
            for i, path in enumerate(existing):
                name = os.path.basename(path)
                parts.append(f"--{boundary}\r\n".encode())
                parts.append(
                    f'Content-Disposition: form-data; name="files[{i}]"; filename="{name}"\r\n'.encode()
                )
                parts.append(b"Content-Type: application/octet-stream\r\n\r\n")
                with open(path, "rb") as fh:
                    parts.append(fh.read())
                parts.append(b"\r\n")
            parts.append(f"--{boundary}--\r\n".encode())
            body = b"".join(parts)
            req = urllib.request.Request(
                webhook, data=body,
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            )
        else:
            req = urllib.request.Request(
                webhook, data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
        print("Posted CI report to Discord.")
        return 0
    except Exception as exc:  # never fail the build because of notifications
        print(f"{WARN} Discord post failed (non-fatal): {exc}")
        return 0


def cmd_discord(args) -> int:
    webhook = args.webhook or os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook:
        print(f"{WARN} No Discord webhook configured (set --webhook or DISCORD_WEBHOOK_URL); skipping.")
        return 0
    report = _read_json(os.path.join(args.report_dir, "report.json"))
    if not report:
        print(f"{WARN} No report.json in {args.report_dir}; nothing to post.")
        return 0
    payload = build_discord_payload(report)
    files = []
    if args.attach:
        for name in ("report.md", "pytest.html"):
            path = os.path.join(args.report_dir, name)
            if os.path.exists(path):
                files.append(path)
    return _post_discord(webhook, payload, files, args.dry_run)


def cmd_consolidate(args) -> int:
    results = []
    for path in sorted(glob.glob(os.path.join(args.in_dir, "**", "result.json"), recursive=True)):
        data = _read_json(path)
        if data:
            results.append(data)
    results.sort(key=lambda r: _JOB_ORDER.get(r.get("job"), 99))
    overall = "passed" if results and all(r.get("status") == "passed" for r in results) else "failed"
    report = {**_meta(), "overall_status": overall, "jobs": results}
    _write_outputs(args.out_dir, report)
    # Non-zero exit if anything failed, so the workflow reflects overall status.
    return 0 if overall == "passed" else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Trackly CI report generator")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("backend")
    b.add_argument("--pytest", required=True)
    b.add_argument("--coverage", default=None)
    b.add_argument("--out-dir", required=True)
    b.set_defaults(func=cmd_backend)

    f = sub.add_parser("frontend")
    f.add_argument("--dist", required=True)
    f.add_argument("--build-log", default=None)
    f.add_argument("--out-dir", required=True)
    f.set_defaults(func=cmd_frontend)

    d = sub.add_parser("docker")
    d.add_argument("--status", required=True)
    d.add_argument("--note", default=None)
    d.add_argument("--out-dir", required=True)
    d.set_defaults(func=cmd_docker)

    c = sub.add_parser("consolidate")
    c.add_argument("--in", dest="in_dir", required=True)
    c.add_argument("--out-dir", required=True)
    c.set_defaults(func=cmd_consolidate)

    dc = sub.add_parser("discord", help="Post a consolidated report to a Discord webhook")
    dc.add_argument("--report-dir", required=True)
    dc.add_argument("--webhook", default=None, help="Webhook URL (or set DISCORD_WEBHOOK_URL)")
    dc.add_argument("--attach", action="store_true", help="Attach report.md and pytest.html")
    dc.add_argument("--dry-run", action="store_true", help="Print payload instead of posting")
    dc.set_defaults(func=cmd_discord)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
