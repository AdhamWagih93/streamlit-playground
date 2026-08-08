"""Per-project logging-health HTML REPORT (email-ready) + SMTP delivery.

The report is built from the same payload the Logging page shows (logstats
.analyze()), rendered as self-contained, inline-styled HTML that renders in
mail clients (tables, no external assets, light background). Sending uses the
QO_SMTP_* knobs; in demo mode with no SMTP configured the send is simulated.
"""

import datetime as dt
import re
import smtplib
from email.message import EmailMessage

from ..config import settings
from . import logstats

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# email-safe palette (light background — mail clients ignore dark schemes)
_C = {"text": "#1c2733", "dim": "#5b6b7c", "line": "#d8e0e8", "bg": "#f5f7fa",
      "green": "#1e8e5a", "cyan": "#0f7f9c", "amber": "#b26a00", "red": "#c2333c",
      "violet": "#6d4fc4"}


def _esc(s) -> str:
    return (str(s if s is not None else "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _grade_color(score) -> str:
    if score is None:
        return _C["dim"]
    return _C["green"] if score >= 90 else _C["cyan"] if score >= 75 \
        else _C["amber"] if score >= 50 else _C["red"]


def _score_pill(score, label="") -> str:
    v = "n/a" if score is None else score
    return (f'<span style="display:inline-block;padding:2px 10px;border-radius:12px;'
            f'font-weight:700;color:#fff;background:{_grade_color(score)}"'
            f' title="{_esc(label)}">{v}</span>')


def _rates(size, docs, first, last):
    if not size or not first or not last:
        return None
    try:
        f = dt.datetime.fromisoformat(str(first).replace("Z", "+00:00"))
        l = dt.datetime.fromisoformat(str(last).replace("Z", "+00:00"))
    except ValueError:
        return None
    days = max((l - f).total_seconds() / 86400.0, 1 / 24)
    return {"size_day": logstats._hsize(int(size / days)),
            "docs_day": int(docs / days) if docs else 0}


_ISSUE_LABEL = {"no_logs": "no logs", "stale": "stale", "timestamp": "@timestamp not a date",
                "bad_week": "bad year in index name", "future_week": "future-dated index",
                "over_retained": "over-retained", "over_sized": "over-sized storage",
                "clash": "deploy_platform clash", "team_clash": "owner clash",
                "unsupported": "unsupported platform"}


def build_report(project: str) -> dict:
    d = logstats.analyze()
    p = next((x for x in (d.get("projects") or []) if x["name"] == project), None)
    if p is None:
        raise ValueError(f"unknown project {project!r}")
    apps = p.get("apps") or []
    t = p.get("totals") or {}
    now = logstats._now().replace(microsecond=0).isoformat() + "Z"
    company = p.get("company")
    order = (d.get("env_order") or {})
    envs_order = [*(order.get("main") or []), *(order.get("extra") or [])]

    # ---- per-env aggregate across the project's apps -------------------
    agg: dict = {}
    for a in apps:
        for e in (a.get("env_stats") or []):
            m = agg.setdefault(e["env"], {"size": 0, "docs": 0, "idx": 0, "apps": 0,
                                          "scores": [], "issues": 0, "owner": None,
                                          "first": None, "last": None})
            m["size"] += e.get("size_bytes") or 0
            m["docs"] += e.get("docs") or 0
            m["idx"] += e.get("indices") or 0
            m["apps"] += 1
            if e.get("score") is not None:
                m["scores"].append(e["score"])
            m["issues"] += len(e.get("issues") or [])
            m["owner"] = m["owner"] or e.get("owner_project") or e.get("owner")
            if e.get("first_logged") and (not m["first"] or e["first_logged"] < m["first"]):
                m["first"] = e["first_logged"]
            if e.get("last_logged") and (not m["last"] or e["last_logged"] > m["last"]):
                m["last"] = e["last_logged"]
    env_names = [en for en in envs_order if en in agg] \
        + [en for en in sorted(agg) if en not in envs_order]

    th = (f'padding:6px 10px;border-bottom:2px solid {_C["line"]};text-align:left;'
          f'font-size:12px;color:{_C["dim"]};text-transform:uppercase;letter-spacing:.04em')
    td = f'padding:6px 10px;border-bottom:1px solid {_C["line"]};font-size:13px;vertical-align:top'

    env_rows = ""
    for en in env_names:
        m = agg[en]
        score = round(sum(m["scores"]) / len(m["scores"])) if m["scores"] else None
        r = _rates(m["size"], m["docs"], m["first"], m["last"])
        env_rows += (f'<tr><td style="{td}"><b>{_esc(en.upper())}</b></td>'
                     f'<td style="{td}">{_esc(m["owner"] or "—")}</td>'
                     f'<td style="{td}">{_score_pill(score)}</td>'
                     f'<td style="{td}">{m["apps"]}</td>'
                     f'<td style="{td}">{m["idx"]}</td>'
                     f'<td style="{td}"><b>{_esc(logstats._hsize(m["size"]))}</b></td>'
                     f'<td style="{td}">{_esc(r["size_day"]) + "/day" if r else "—"}</td>'
                     f'<td style="{td};color:{_C["red"] if m["issues"] else _C["green"]}">'
                     f'{m["issues"] or "none"}</td></tr>')

    app_rows = ""
    issue_blocks = ""
    for a in apps:
        issues = a.get("issues") or []
        r = _rates(a.get("size_bytes"), a.get("docs"), a.get("first_logged"), a.get("last_logged"))
        ratio = a.get("size_ratio")
        env_cells = []
        by_env = {e["env"]: e for e in (a.get("env_stats") or [])}
        for en in env_names:
            e = by_env.get(en)
            if not e:
                env_cells.append("—")
            elif not e.get("deployed"):
                env_cells.append(f'<span style="color:{_C["dim"]}">not deployed</span>')
            elif e.get("no_logs"):
                env_cells.append(f'<span style="color:{_C["red"]};font-weight:700">NO LOGS</span>')
            else:
                env_cells.append(f'{_esc(logstats._hsize(e.get("size_bytes") or 0))}'
                                 f' <span style="color:{_C["dim"]}">({e.get("indices") or 0} idx)</span>')
        oversize_tag = (f'<br><span style="color:{_C["amber"]};font-size:11px">{ratio}&times; avg</span>'
                        if a.get("over_sized") else "")
        app_rows += (f'<tr><td style="{td}"><b>{_esc(a["app"])}</b><br>'
                     f'<span style="color:{_C["dim"]};font-size:11px">'
                     f'{_esc(a.get("deploy_platform") or "—")}'
                     f'{" · " + _esc(a["deploy_technology"]) if a.get("deploy_technology") else ""}</span></td>'
                     f'<td style="{td}">{_score_pill(a.get("score"))}</td>'
                     + "".join(f'<td style="{td}">{c}</td>' for c in env_cells)
                     + f'<td style="{td}"><b style="color:{_C["red"] if a.get("over_sized") else _C["text"]}">'
                     f'{_esc(a.get("size_h") or "0 B")}</b>{oversize_tag}</td>'
                     f'<td style="{td}">{_esc(r["size_day"]) + "/day" if r else "—"}</td>'
                     f'<td style="{td};color:{_C["red"] if issues else _C["green"]};font-size:12px">'
                     f'{_esc(", ".join(_ISSUE_LABEL.get(k, k) for k in issues)) or "ok ✓"}</td></tr>')
        if issues:
            det = []
            if (a.get("ts_bad_indices") or []):
                det.append(f'@timestamp is not a <b>date</b> in {len(a["ts_bad_indices"])} '
                           f'index(es): {_esc(", ".join(a["ts_bad_indices"][:5]))}'
                           f'{"…" if len(a["ts_bad_indices"]) > 5 else ""}')
            if (a.get("bad_week_indices") or []):
                det.append(f'illogical YEAR in: {_esc(", ".join(a["bad_week_indices"][:5]))}')
            if (a.get("future_week_indices") or []):
                det.append(f'future-dated: {_esc(", ".join(a["future_week_indices"][:5]))}')
            if (a.get("over_retained_envs") or []):
                det.append(f'logs kept beyond retention in: {_esc(", ".join(a["over_retained_envs"]))}')
            if a.get("discrepancy"):
                det.append(f'deploy_platform clash: app {_esc(a.get("app_platform"))} vs '
                           f'project {_esc(a.get("project_platform"))}')
            if (a.get("owner_clash_envs") or []):
                det.append(f'owner clash in: {_esc(", ".join(a["owner_clash_envs"]))}')
            if a.get("over_sized"):
                det.append(f'stores {_esc(a.get("size_h"))} — {ratio}× the fleet average app')
            if a.get("no_logs"):
                det.append("deployed but produced NO logs")
            if det:
                issue_blocks += (f'<p style="margin:6px 0 2px;font-size:13px"><b>🧩 {_esc(a["app"])}</b></p>'
                                 f'<ul style="margin:2px 0 8px 18px;padding:0;font-size:12.5px;color:{_C["text"]}">'
                                 + "".join(f"<li>{x}</li>" for x in det) + "</ul>")

    pr = _rates(t.get("size_bytes"),
                t.get("docs"),
                min((a.get("first_logged") for a in apps if a.get("first_logged")), default=None),
                max((a.get("last_logged") for a in apps if a.get("last_logged")), default=None))
    with_logs = [a for a in apps if (a.get("size_bytes") or 0) > 0]
    avg_app = logstats._hsize(int(t.get("size_bytes", 0) / len(with_logs))) if with_logs else "—"
    stat = lambda label, val: (f'<td style="padding:8px 14px;border:1px solid {_C["line"]};'  # noqa: E731
                               f'border-radius:6px;background:#fff"><div style="font-size:17px;'
                               f'font-weight:700;color:{_C["text"]}">{val}</div>'
                               f'<div style="font-size:11px;color:{_C["dim"]}">{label}</div></td>')

    env_head = "".join(f'<th style="{th}">{_esc(en)}</th>' for en in env_names)
    html = f"""<!doctype html><html><body style="margin:0;padding:0;background:{_C['bg']}">
<div style="max-width:860px;margin:0 auto;padding:18px;font-family:Arial,Helvetica,sans-serif;color:{_C['text']}">
  <h1 style="font-size:20px;margin:0 0 2px">📊 Logging health — {_esc(project)}</h1>
  <p style="margin:0 0 12px;color:{_C['dim']};font-size:12px">
    {f"🏢 {_esc(company)} · " if company else ""}{_esc(p.get('deploy_platform') or '—')} → {_esc(p.get('prefix') or '—')}
    · generated {_esc(now)} · source: {_esc(d.get('source') or '?')}</p>
  <p style="margin:0 0 14px;font-size:14px">Project health score: {_score_pill(p.get('score'), 'project score')}</p>
  <table cellspacing="6" cellpadding="0" style="border-collapse:separate;margin:0 0 16px"><tr>
    {stat("apps", t.get("apps", len(apps)))}
    {stat("indices", t.get("indices", 0))}
    {stat("total size", f'<span style="color:{_C["red"]}">{_esc(t.get("size_h") or "0 B")}</span>' if p.get("over_sized") else _esc(t.get("size_h") or "0 B"))}
    {stat("documents", f"{t.get('docs', 0):,}")}
    {stat("avg app size", _esc(avg_app))}
    {stat("ingest / day", _esc(pr["size_day"]) if pr else "—")}
  </tr></table>

  <h2 style="font-size:15px;margin:16px 0 6px">Environments</h2>
  <table width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;background:#fff;border:1px solid {_C['line']};border-radius:6px">
    <tr><th style="{th}">env</th><th style="{th}">owner team</th><th style="{th}">score</th>
        <th style="{th}">apps</th><th style="{th}">indices</th><th style="{th}">size</th>
        <th style="{th}">rate</th><th style="{th}">issues</th></tr>
    {env_rows}
  </table>

  <h2 style="font-size:15px;margin:18px 0 6px">Applications</h2>
  <table width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;background:#fff;border:1px solid {_C['line']};border-radius:6px">
    <tr><th style="{th}">app</th><th style="{th}">score</th>{env_head}
        <th style="{th}">total</th><th style="{th}">rate</th><th style="{th}">issues</th></tr>
    {app_rows}
  </table>

  {f'<h2 style="font-size:15px;margin:18px 0 6px">Issue detail</h2><div style="background:#fff;border:1px solid {_C["line"]};border-radius:6px;padding:8px 12px">{issue_blocks}</div>' if issue_blocks else ''}

  <p style="margin:16px 0 0;color:{_C['dim']};font-size:11px">Generated by QuestOps · Logging Health
    · retention prd {int((d.get('retention') or {}).get('prd_days') or 0)}d / non-prd {int((d.get('retention') or {}).get('nonprd_days') or 0)}d
    · stale &gt; {_esc(d.get('stale_hours'))}h</p>
</div></body></html>"""
    score = p.get("score")
    return {"project": project,
            "subject": f"[QuestOps] Logging health — {project}"
                       f" (score {score if score is not None else 'n/a'}/100)",
            "html": html}


def send_report(project: str, recipients: list[str], subject: str | None = None) -> dict:
    rep = build_report(project)
    to = [r.strip() for r in (recipients or []) if r and r.strip()]
    if not to:
        raise ValueError("no recipients given")
    bad = [r for r in to if not _EMAIL_RE.match(r)]
    if bad:
        raise ValueError("invalid recipient(s): " + ", ".join(bad))
    subj = (subject or "").strip() or rep["subject"]
    if not settings.smtp_host:
        if settings.demo_mode:
            return {"ok": True, "sent": len(to), "recipients": to,
                    "note": "demo mode — SMTP not configured, email NOT actually sent"}
        raise RuntimeError("SMTP is not configured — set QO_SMTP_HOST (and the other "
                           "QO_SMTP_* knobs) in .env")
    msg = EmailMessage()
    msg["Subject"] = subj
    msg["From"] = settings.smtp_from
    msg["To"] = ", ".join(to)
    msg.set_content("This report is HTML — please open it in an HTML-capable mail client.")
    msg.add_alternative(rep["html"], subtype="html")
    if settings.smtp_ssl:
        server = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=30)
    else:
        server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30)
    try:
        if settings.smtp_starttls and not settings.smtp_ssl:
            server.starttls()
        if settings.smtp_user:
            server.login(settings.smtp_user, settings.smtp_password)
        server.send_message(msg)
    finally:
        try:
            server.quit()
        except Exception:  # noqa: BLE001 — best-effort close
            pass
    return {"ok": True, "sent": len(to), "recipients": to}
