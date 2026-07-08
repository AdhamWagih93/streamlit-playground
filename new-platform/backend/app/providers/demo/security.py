"""Security posture — demo provider.

Aggregates the world's scan results (prismacloud / invicti / zap / trufflehog)
per application, per version, and renders self-contained HTML scan reports whose
counts always match `world.scans` so the UI stays consistent.
"""
from __future__ import annotations

import html as html_mod
import math
import random
import zlib

from fastapi import HTTPException

from ...auth.rbac import User
from . import scope
from .world import STAGES, get_world

SCANNERS = ["prismacloud", "invicti", "zap", "trufflehog"]
SEVERITIES = ["critical", "high", "medium", "low"]
# "Latest deployed" = the version sitting in the highest stage present.
STAGE_PRIORITY = ["prd", "uat", "qc", "dev", "release", "build"]

SCANNER_LABELS = {
    "prismacloud": "Prisma Cloud — Container Image Scan",
    "invicti": "Invicti — Dynamic Application Security Test",
    "zap": "OWASP ZAP — Dynamic Application Security Test",
    "trufflehog": "TruffleHog — Secrets Scan",
}


def _kept_severities(floor: str) -> list[str]:
    if floor not in SEVERITIES:
        floor = "low"
    return SEVERITIES[: SEVERITIES.index(floor) + 1]


def _latest_version(app) -> tuple[str | None, str | None]:
    for stage in STAGE_PRIORITY:
        st = app.stages.get(stage) or {}
        if st.get("version"):
            return st["version"], stage
    return None, None


def _ver_key(v: str):
    try:
        return tuple(int(x) for x in v.split("-")[0].split("."))
    except ValueError:
        return (0, 0, 0)


# --------------------------------------------------------------------- summary
def summary(user: User, scanner: str = "all", q: str = "", project: str = "",
            only_findings: bool = False, severity_floor: str = "low",
            page: int = 1, size: int = 50) -> dict:
    w = get_world()
    wanted = SCANNERS if scanner in ("", "all") else [scanner]
    if any(s not in SCANNERS for s in wanted):
        raise HTTPException(status_code=404, detail=f"Unknown scanner: {scanner!r}")
    kept = _kept_severities(severity_floor)
    ql = (q or "").strip().lower()

    rows: list[dict] = []
    apps_total = 0
    apps_scanned = 0
    totals = {s: 0 for s in SEVERITIES}

    for app in scope.visible_apps(user):
        if project and app.project != project:
            continue
        if ql and ql not in app.application.lower() and ql not in app.project.lower():
            continue
        apps_total += 1
        ver, env = _latest_version(app)
        if not ver:
            continue
        cells: dict[str, dict] = {}
        for sc in wanted:
            c = w.scans.get((sc, app.application, ver))
            if not c:
                continue
            cells[sc] = {
                **{s: (c[s] if s in kept else 0) for s in SEVERITIES},
                "when": c["when"],
            }
        if cells:
            apps_scanned += 1
        row_tot = {s: sum(c[s] for c in cells.values()) for s in SEVERITIES}
        if only_findings and sum(row_tot.values()) == 0:
            continue
        for s in SEVERITIES:
            totals[s] += row_tot[s]
        rows.append({
            "application": app.application,
            "project": app.project,
            "version": ver,
            "env_of_version": env,
            "scanners": cells,
            "total_critical": row_tot["critical"],
            "total_high": row_tot["high"],
        })

    rows.sort(key=lambda r: (-r["total_critical"], -r["total_high"], r["application"]))
    total = len(rows)
    size = max(1, min(int(size or 50), 200))
    pages = max(1, math.ceil(total / size))
    page = max(1, min(int(page or 1), pages))
    return {
        "rows": rows[(page - 1) * size: page * size],
        "totals": {**totals, "apps_scanned": apps_scanned, "apps_total": apps_total},
        "page": page,
        "pages": pages,
        "total": total,
    }


# ----------------------------------------------------------------- drill-down
def _visible_app(user: User, project: str, application: str):
    app = scope.app_by_name(project, application)
    if app is None:
        raise HTTPException(status_code=404, detail="Application not found")
    if not user.can_see_row(app.teams):
        raise HTTPException(status_code=403, detail="Application is outside your team scope")
    return app


def app_detail(user: User, project: str, application: str) -> dict:
    app = _visible_app(user, project, application)
    w = get_world()
    prd_ver = (app.stages.get("prd") or {}).get("version")

    vers: dict[str, list[str]] = {}
    for stage in STAGES:  # build → release → dev → qc → uat → prd
        v = (app.stages.get(stage) or {}).get("version")
        if v:
            vers.setdefault(v, []).append(stage)

    out = []
    for v in sorted(vers, key=_ver_key, reverse=True):
        cells: dict[str, dict] = {}
        for sc in SCANNERS:
            c = w.scans.get((sc, application, v))
            if not c:
                continue
            delta = None
            if prd_ver:
                p = w.scans.get((sc, application, prd_ver))
                if p:
                    delta = {s: c[s] - p[s] for s in SEVERITIES}
            cells[sc] = {**{s: c[s] for s in SEVERITIES}, "when": c["when"], "delta": delta}
        out.append({"version": v, "envs": vers[v], "scanners": cells})
    return {
        "application": application,
        "project": project,
        "prd_version": prd_ver,
        "versions": out,
    }


# --------------------------------------------------------------------- report
_SEV_COLOR = {"critical": "#F06A6A", "high": "#F2B14C", "medium": "#7A9BFF", "low": "#98A2C0"}

_PKGS = ["openssl", "log4j-core", "spring-web", "jackson-databind", "glibc", "zlib",
         "busybox", "libcurl", "netty-codec-http", "commons-text", "protobuf-java",
         "xmlsec", "snakeyaml", "libexpat", "krb5-libs"]
_DAST_PATHS = ["/api/v1/login", "/api/v1/accounts", "/api/v1/transfer", "/admin",
               "/health", "/swagger-ui/index.html", "/api/v1/customers/42", "/callback",
               "/api/v1/statements", "/actuator/env"]
_DAST_ISSUES = {
    "critical": ["SQL Injection", "Remote Code Execution", "Authentication Bypass"],
    "high": ["Stored Cross-Site Scripting", "Server-Side Request Forgery", "XXE Injection",
             "Insecure Direct Object Reference"],
    "medium": ["Missing Content-Security-Policy", "Reflected XSS (filtered)", "Open Redirect",
               "Verbose Error Messages", "Weak TLS Cipher Suites"],
    "low": ["Missing X-Content-Type-Options", "Cookie Without SameSite Flag",
            "Server Version Disclosure", "Autocomplete Enabled on Password Field"],
}
_DETECTORS = ["AWS Access Key", "GitHub Personal Access Token", "Generic API Key",
              "Private RSA Key", "Slack Incoming Webhook", "JDBC Connection Password",
              "Azure Storage Account Key"]
_FILES = ["src/main/resources/application.yml", "helm/values-uat.yaml", "scripts/deploy.sh",
          ".env.sample", "src/config/datasource.ts", "Jenkinsfile", "docker-compose.override.yml",
          "src/test/resources/it-config.properties"]


def _fake_findings(r: random.Random, scanner: str, application: str, project: str,
                   counts: dict, limit: int = 15) -> list[dict]:
    seq: list[str] = []
    for sev in SEVERITIES:
        seq.extend([sev] * int(counts.get(sev, 0)))
    seq = seq[:limit]
    out = []
    for sev in seq:
        if scanner == "prismacloud":
            pkg = r.choice(_PKGS)
            year = r.choice([2023, 2024, 2025, 2025, 2025])
            out.append(dict(
                sev=sev,
                id=f"CVE-{year}-{r.randint(10000, 99999)}",
                title=f"{pkg} {r.randint(1, 9)}.{r.randint(0, 19)}.{r.randint(0, 9)}",
                detail=f"Vulnerable package in image layer sha256:{r.getrandbits(48):012x}… "
                       f"— fixed in {r.randint(1, 9)}.{r.randint(0, 19)}.{r.randint(1, 19)}",
            ))
        elif scanner in ("invicti", "zap"):
            issue = r.choice(_DAST_ISSUES[sev])
            path = r.choice(_DAST_PATHS)
            out.append(dict(
                sev=sev,
                id=f"{'INV' if scanner == 'invicti' else 'ZAP'}-{r.randint(1000, 9999)}",
                title=issue,
                detail=f"https://{application}.{project.lower()}.corp{path} — risk: {sev.upper()}, "
                       f"confidence {r.choice(['high', 'medium', 'firm'])}",
            ))
        else:  # trufflehog
            out.append(dict(
                sev=sev,
                id=f"TH-{r.randint(100, 999)}",
                title=r.choice(_DETECTORS),
                detail=f"{r.choice(_FILES)}:{r.randint(3, 240)} — verified={r.choice(['true', 'false'])}, "
                       f"entropy {r.uniform(3.5, 5.9):.2f}",
            ))
    return out


def report(user: User, scanner: str, project: str, application: str, version: str) -> str:
    if scanner not in SCANNERS:
        raise HTTPException(status_code=404, detail=f"Unknown scanner: {scanner!r}")
    _visible_app(user, project, application)
    counts = get_world().scans.get((scanner, application, version))
    if counts is None:
        raise HTTPException(status_code=404,
                            detail=f"No {scanner} scan recorded for {application} {version}")

    r = random.Random(zlib.crc32(f"{scanner}|{application}|{version}".encode()))
    total = sum(counts[s] for s in SEVERITIES)
    findings = _fake_findings(r, scanner, application, project, counts)
    esc = html_mod.escape

    sev_cells = "".join(
        f"<td style='text-align:center;padding:10px 18px;border:1px solid #22305c;'>"
        f"<div style='font-size:24px;font-weight:800;color:{_SEV_COLOR[s]}'>{counts[s]}</div>"
        f"<div style='font-size:10px;letter-spacing:.14em;color:#8b96c2'>{s.upper()}</div></td>"
        for s in SEVERITIES
    )
    rows = "".join(
        f"<tr>"
        f"<td style='padding:8px 10px;border-bottom:1px solid #1a2547;white-space:nowrap'>"
        f"<span style='color:{_SEV_COLOR[f['sev']]};font-weight:700;font-size:11px'>"
        f"{f['sev'].upper()}</span></td>"
        f"<td style='padding:8px 10px;border-bottom:1px solid #1a2547;color:#c9d4f6;"
        f"white-space:nowrap'>{esc(f['id'])}</td>"
        f"<td style='padding:8px 10px;border-bottom:1px solid #1a2547;color:#e8ecfa'>{esc(f['title'])}</td>"
        f"<td style='padding:8px 10px;border-bottom:1px solid #1a2547;color:#8b96c2'>{esc(f['detail'])}</td>"
        f"</tr>"
        for f in findings
    )
    more = ""
    if total > len(findings):
        more = (f"<p style='color:#8b96c2;font-size:12px'>Showing {len(findings)} of {total} "
                f"findings — full listing available in the scanner console.</p>")
    body_findings = (
        f"<table style='border-collapse:collapse;width:100%;font-size:12.5px'>"
        f"<thead><tr>"
        + "".join(f"<th style='text-align:left;padding:8px 10px;font-size:10px;letter-spacing:.12em;"
                  f"color:#8b96c2;border-bottom:1px solid #22305c'>{h}</th>"
                  for h in ("SEVERITY", "ID", "FINDING", "DETAIL"))
        + f"</tr></thead><tbody>{rows}</tbody></table>{more}"
        if findings else
        "<p style='color:#3dd68c;font-weight:600'>No findings — scan came back clean.</p>"
    )

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{esc(scanner)} — {esc(application)} {esc(version)}</title></head>
<body style="margin:0;background:#0B1020;color:#E8ECFA;font-family:'JetBrains Mono',ui-monospace,Menlo,monospace;padding:28px">
  <div style="max-width:900px;margin:0 auto">
    <div style="font-size:10px;letter-spacing:.2em;color:#8b96c2">MERIDIAN · SECURITY SCAN REPORT</div>
    <h1 style="font-size:20px;margin:8px 0 2px;color:#E8B44A">{esc(SCANNER_LABELS[scanner])}</h1>
    <div style="color:#c9d4f6;font-size:13px;margin-bottom:4px">
      {esc(application)} <span style="color:#8b96c2">·</span> project {esc(project)}
      <span style="color:#8b96c2">·</span> version
      <span style="border:1px solid #22305c;border-radius:6px;padding:1px 7px">{esc(version)}</span>
    </div>
    <div style="color:#8b96c2;font-size:11.5px;margin-bottom:22px">
      scanned {esc(str(counts.get('when', '')))} · status {esc(str(counts.get('status', 'ok')))} · {total} findings
    </div>
    <table style="border-collapse:collapse;margin-bottom:26px"><tr>{sev_cells}</tr></table>
    {body_findings}
    <div style="margin-top:30px;color:#5a648c;font-size:10.5px;letter-spacing:.12em">
      GENERATED BY MERIDIAN DEMO PROVIDER — COUNTS MATCH PLATFORM INVENTORY
    </div>
  </div>
</body></html>"""
