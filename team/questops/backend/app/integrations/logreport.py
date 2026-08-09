"""Per-project logging-health HTML REPORT (email-ready) + SMTP delivery.

Built from the same payload the Logging page shows (logstats.analyze()),
rendered as self-contained inline-styled HTML that renders in mail clients
(tables, no external assets, light background).

Scoping: EXTRA envs (LOG_EXTRA_ENVS) are EXCLUDED by default (include_extra
toggles them in), and `team` narrows the report to just the environments that
team owns. Every table, stat and score is recomputed from the included envs.
Sending uses the QO_SMTP_* knobs; demo mode with no SMTP simulates the send.
"""

import datetime as dt
import re
import smtplib
import time
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
    """Ingest per day over the logged span. The span is clamped to >= 1 DAY so
    a tiny observation window (one fresh 500 KB index spanning an hour) is
    never extrapolated into a fantasy daily rate — for sub-day spans the
    "per-day" rate is simply what was actually logged."""
    if not size or not first or not last:
        return None
    try:
        f = dt.datetime.fromisoformat(str(first).replace("Z", "+00:00"))
        l = dt.datetime.fromisoformat(str(last).replace("Z", "+00:00"))
    except ValueError:
        return None
    days = (l - f).total_seconds() / 86400.0
    if days > 365 * 15:          # poisoned span (junk @timestamp docs)
        return None
    days = max(days, 1.0)
    return {"size_day": logstats._hsize(int(size / days)),
            "docs_day": int(docs / days) if docs else 0}


_ISSUE_LABEL = {"no_logs": "no logs", "stale": "stale", "timestamp": "@timestamp not a date",
                "bad_week": "bad year in index name", "future_week": "future-dated index",
                "over_retained": "over-retained", "over_sized": "over-sized storage",
                "grok": "grok parse failures",
                "clash": "deploy_platform clash", "team_clash": "owner clash",
                "unsupported": "unsupported platform"}


def _env_issue_lines(a: dict, e: dict, stale_hours) -> list[str]:
    """Human explanations for every issue an app carries IN one environment."""
    out = []
    for k in (e.get("issues") or []):
        if k == "no_logs":
            out.append("deployed here but produced NO logs")
        elif k == "stale":
            age = e.get("last_logged_age_h")
            out.append(f"stale — newest log is {age:.0f}h old (threshold {stale_hours}h)"
                       if age is not None else f"stale — no log newer than {stale_hours}h")
        elif k == "timestamp":
            n = len(e.get("ts_bad_indices") or [])
            out.append(f"@timestamp is not a date in {n} index(es) — time filters "
                       f"silently return nothing there")
        elif k == "bad_week":
            names = e.get("bad_week_indices") or []
            out.append(f"illogical YEAR in the index name (mis-templated shipper): "
                       f"{_esc(', '.join(names[:3]))}{'…' if len(names) > 3 else ''}")
        elif k == "future_week":
            names = e.get("future_week_indices") or []
            out.append(f"future-dated index (clock skew / mis-template): "
                       f"{_esc(', '.join(names[:3]))}{'…' if len(names) > 3 else ''}")
        elif k == "grok":
            n = len(e.get("grok_indices") or [])
            out.append(f"{n} index(es) contain docs tagged _grokparsefailure — "
                       f"grok patterns not matching, fields never extracted")
        elif k == "over_retained":
            out.append(f"logs kept beyond the retention policy "
                       f"({e.get('retention_days', '?')} days)")
        elif k == "team_clash":
            out.append(f"env owner clash — app says {_esc(e.get('owner_app') or '?')}, "
                       f"project says {_esc(e.get('owner_project') or '?')}")
        else:
            out.append(_ISSUE_LABEL.get(k, k))
    return out


# the issue kinds that have doc SAMPLES + how to fetch them
_SAMPLED_ISSUES = (("timestamp", "", "ts_bad_indices"),
                   ("bad_week", "badweek", "bad_week_indices"),
                   ("future_week", "future", "future_week_indices"),
                   ("grok", "grok", "grok_indices"))


def _issue_samples(indices: list, mode: str, k: int = 3) -> list:
    """Up to k sampled docs from the suspect indices (never raises)."""
    if not indices:
        return []
    try:
        res = logstats.ts_samples(",".join(indices[:10]), size=k + 5, mode=mode)
    except Exception:  # noqa: BLE001 — sampling must never break the report
        return []
    docs = (res.get("index") or {}).get("docs") or []
    if mode == "":                       # @timestamp case: show the NON-dates
        docs = [d for d in docs if not d.get("is_date")] or docs
    elif mode == "future":
        docs = [d for d in docs if d.get("is_future")] or docs
    return docs[:k]


def _samples_html(label: str, docs: list) -> str:
    if not docs:
        return ""
    rows = (f'<div style="font-size:11px;color:{_C["dim"]};margin:4px 0 0 8px">'
            f'samples — {_esc(label)} (up to 3):</div>')
    for d in docs:
        orig = (d.get("original") or "")
        flags = []
        if not d.get("is_date"):
            flags.append("not a date")
        elif d.get("is_future"):
            flags.append("future date")
        if "_grokparsefailure" in (d.get("tags") or []):
            flags.append("_grokparsefailure")
        rows += (f'<div style="margin:2px 0 4px 8px;padding:4px 8px;background:{_C["bg"]};'
                 f'border-left:3px solid {_C["red"]};font-family:monospace;font-size:11px;'
                 f'word-break:break-all">'
                 f'<b>@timestamp:</b> {_esc(d.get("value"))}'
                 + (f' <span style="color:{_C["red"]}">[{_esc(", ".join(flags))}]</span>' if flags else "")
                 + (f' · <b>type:</b> {_esc(d["logtype"])}' if d.get("logtype") else "")
                 + (f' · <b>file:</b> {_esc(d["path"])}' if d.get("path") else "")
                 + (f'<br><span style="color:{_C["dim"]}">{_esc(orig[:200])}'
                    f'{"…" if len(orig) > 200 else ""}</span>' if orig else "")
                 + '</div>')
    return rows


def build_report(project: str, include_extra: bool = False, team: str | None = None,
                 skip_healthy: bool = False, skip_undeployed: bool = False,
                 skip_unmonitored: bool = False) -> dict:
    d = logstats.analyze()
    p = next((x for x in (d.get("projects") or []) if x["name"] == project), None)
    if p is None:
        raise ValueError(f"unknown project {project!r}")
    apps_all = p.get("apps") or []
    now = logstats._now().replace(microsecond=0).isoformat() + "Z"
    company = p.get("company")
    stale_hours = d.get("stale_hours")
    order = (d.get("env_order") or {})
    main_order = order.get("main") or []
    extra_set = set(order.get("extra") or [])

    # ---- per-env aggregate over ALL envs (to know owners before filtering) --
    agg: dict = {}
    for a in apps_all:
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
    present = [en for en in main_order if en in agg] \
        + [en for en in sorted(agg) if en not in main_order]

    # ---- scope: extra envs off by default; optional env-owner team filter --
    env_names = [en for en in present if include_extra or en not in extra_set]
    if team:
        tl = team.strip().lower()
        env_names = [en for en in env_names
                     if (agg[en]["owner"] or "").strip().lower() == tl]
    scope_bits = []
    if extra_set:
        scope_bits.append("extra envs included" if include_extra else "extra envs excluded")
    if team:
        scope_bits.append(f"envs owned by {team}")
    scope_note = " · ".join(scope_bits)

    th = (f'padding:6px 10px;border-bottom:2px solid {_C["line"]};text-align:left;'
          f'font-size:12px;color:{_C["dim"]};text-transform:uppercase;letter-spacing:.04em')
    td = f'padding:6px 10px;border-bottom:1px solid {_C["line"]};font-size:13px;vertical-align:top'

    # ---- scope every app to the included envs; drop apps with none ---------
    scoped = []
    for a in apps_all:
        es = [e for e in (a.get("env_stats") or []) if e["env"] in env_names]
        if not es and (a.get("env_stats") or []):
            continue                      # nothing of this app is in scope
        size = sum(e.get("size_bytes") or 0 for e in es)
        docs = sum(e.get("docs") or 0 for e in es)
        idx = sum(e.get("indices") or 0 for e in es)
        firsts = [e["first_logged"] for e in es if e.get("first_logged")]
        lasts = [e["last_logged"] for e in es if e.get("last_logged")]
        escores = [e["score"] for e in es if e.get("score") is not None]
        score = None
        if a.get("monitored") and escores:
            base = sum(escores) / len(escores)
            if a.get("discrepancy"):
                base -= 10
            if any(e.get("owner_clash") for e in es):
                base -= 10
            if a.get("over_sized"):
                base -= 10
            score = max(int(round(base)), 0)
        scoped.append({**a, "_es": es, "_size": size, "_docs": docs, "_idx": idx,
                       "_first": min(firsts) if firsts else None,
                       "_last": max(lasts) if lasts else None, "_score": score})

    # optional app filters — every hidden count is stated in the scope note
    hidden_unmonitored = 0
    if skip_unmonitored:                      # platform not checked at all
        hidden_unmonitored = sum(1 for x in scoped if not x.get("monitored"))
        scoped = [x for x in scoped if x.get("monitored")]
    hidden_undeployed = 0
    if skip_undeployed:                       # never deployed in the in-scope envs
        dep = lambda x: any(e.get("deployed") for e in x["_es"])  # noqa: E731
        hidden_undeployed = sum(1 for x in scoped if not dep(x))
        scoped = [x for x in scoped if dep(x)]
    hidden_healthy = 0
    if skip_healthy:                          # perfect score (in scope)
        hidden_healthy = sum(1 for x in scoped if x["_score"] == 100)
        scoped = [x for x in scoped if x["_score"] != 100]

    t_size = sum(x["_size"] for x in scoped)
    t_docs = sum(x["_docs"] for x in scoped)
    t_idx = sum(x["_idx"] for x in scoped)
    pscores = [x["_score"] for x in scoped if x["_score"] is not None]
    pscore = int(round(sum(pscores) / len(pscores))) if pscores else None
    if p.get("over_sized") and pscore is not None:
        pscore = max(pscore - 10, 0)

    env_rows = ""
    env_issue_blocks = ""
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
        # ---- the per-env issue EXPLANATIONS (why each highlight is red) ----
        lines = []
        for a in scoped:
            e = next((x for x in a["_es"] if x["env"] == en), None)
            if not e or not (e.get("issues") or []):
                continue
            expl = _env_issue_lines(a, e, stale_hours)
            if expl:
                lines.append(f'<li style="margin:2px 0"><b>{_esc(a["app"])}</b>: '
                             + "; ".join(expl) + "</li>")
        if lines:
            owner_tag = f" · {_esc(m['owner'])}" if m["owner"] else ""
            # up to 3 doc SAMPLES per sampled issue kind, for THIS env's
            # suspect indices (aggregated across the env's apps)
            samples = ""
            for key, mode, fld in _SAMPLED_ISSUES:
                idxs = sorted({n for a in scoped for e2 in a["_es"]
                               if e2["env"] == en and key in (e2.get("issues") or [])
                               for n in (e2.get(fld) or [])})
                if idxs:
                    samples += _samples_html(_ISSUE_LABEL.get(key, key),
                                             _issue_samples(idxs, mode))
            env_issue_blocks += (f'<p style="margin:8px 0 2px;font-size:13px">'
                                 f'<b>{_esc(en.upper())}</b>{owner_tag}</p>'
                                 f'<ul style="margin:2px 0 8px 18px;padding:0;font-size:12.5px;'
                                 f'color:{_C["text"]}">{"".join(lines)}</ul>{samples}')

    # app-level (env-independent) issues explained separately
    app_level = ""
    for a in scoped:
        det = []
        if a.get("discrepancy"):
            det.append(f'deploy_platform clash — app says {_esc(a.get("app_platform"))}, '
                       f'project says {_esc(a.get("project_platform"))}')
        if a.get("over_sized"):
            det.append(f'over-sized storage — {_esc(a.get("size_h"))}, '
                       f'{a.get("size_ratio")}× the fleet-average app')
        if a.get("platform_status") == "unsupported":
            det.append(f'platform {_esc(a.get("deploy_platform"))} is not monitored — '
                       f'logs are not checked')
        if a.get("logging_required") is False:
            det.append(f'technology {_esc(a.get("deploy_technology"))} does not require '
                       f'logging (Engine Deploy_Technologies)')
        if det:
            app_level += (f'<li style="margin:2px 0"><b>{_esc(a["app"])}</b>: '
                          + "; ".join(det) + "</li>")

    app_rows = ""
    for a in scoped:
        issues = sorted({k for e in a["_es"] for k in (e.get("issues") or [])}
                        | ({"clash"} if a.get("discrepancy") else set())
                        | ({"over_sized"} if a.get("over_sized") else set()))
        r = _rates(a["_size"], a["_docs"], a["_first"], a["_last"])
        by_env = {e["env"]: e for e in a["_es"]}
        cells = []
        for en in env_names:
            e = by_env.get(en)
            if not e:
                cells.append("—")
            elif not e.get("deployed"):
                cells.append(f'<span style="color:{_C["dim"]}">not deployed</span>')
            elif e.get("no_logs"):
                cells.append(f'<span style="color:{_C["red"]};font-weight:700">NO LOGS</span>')
            else:
                cells.append(f'{_esc(logstats._hsize(e.get("size_bytes") or 0))}'
                             f' <span style="color:{_C["dim"]}">({e.get("indices") or 0} idx)</span>')
        oversize_tag = (f'<br><span style="color:{_C["amber"]};font-size:11px">'
                        f'{a.get("size_ratio")}&times; avg</span>' if a.get("over_sized") else "")
        app_rows += (f'<tr><td style="{td}"><b>{_esc(a["app"])}</b><br>'
                     f'<span style="color:{_C["dim"]};font-size:11px">'
                     f'{_esc(a.get("deploy_platform") or "—")}'
                     f'{" · " + _esc(a["deploy_technology"]) if a.get("deploy_technology") else ""}</span></td>'
                     f'<td style="{td}">{_score_pill(a["_score"])}</td>'
                     + "".join(f'<td style="{td}">{c}</td>' for c in cells)
                     + f'<td style="{td}"><b style="color:'
                     f'{_C["red"] if a.get("over_sized") else _C["text"]}">'
                     f'{_esc(logstats._hsize(a["_size"]))}</b>{oversize_tag}</td>'
                     f'<td style="{td}">{_esc(r["size_day"]) + "/day" if r else "—"}</td>'
                     f'<td style="{td};color:{_C["red"] if issues else _C["green"]};font-size:12px">'
                     f'{_esc(", ".join(_ISSUE_LABEL.get(k, k) for k in issues)) or "ok ✓"}</td></tr>')

    pr = _rates(t_size, t_docs,
                min((x["_first"] for x in scoped if x["_first"]), default=None),
                max((x["_last"] for x in scoped if x["_last"]), default=None))
    with_logs = [x for x in scoped if x["_size"] > 0]
    avg_app = logstats._hsize(int(t_size / len(with_logs))) if with_logs else "—"
    stat = lambda label, val: (f'<td style="padding:8px 14px;border:1px solid {_C["line"]};'  # noqa: E731
                               f'border-radius:6px;background:#fff"><div style="font-size:17px;'
                               f'font-weight:700;color:{_C["text"]}">{val}</div>'
                               f'<div style="font-size:11px;color:{_C["dim"]}">{label}</div></td>')
    total_stat = (f'<span style="color:{_C["red"]}">{_esc(logstats._hsize(t_size))}</span>'
                  if p.get("over_sized") else _esc(logstats._hsize(t_size)))

    for n, what in ((hidden_unmonitored, "unmonitored"), (hidden_undeployed, "un-deployed"),
                    (hidden_healthy, "healthy (score 100)")):
        if n:
            scope_note = (scope_note + " · " if scope_note else "") + f"{n} {what} app(s) hidden"
    if skip_healthy and not hidden_healthy:
        scope_note = (scope_note + " · " if scope_note else "") + "healthy apps hidden"
    env_head = "".join(f'<th style="{th}">{_esc(en)}</th>' for en in env_names)
    issues_section = ""
    if env_issue_blocks or app_level:
        none_note = (f'<p style="font-size:12.5px;color:{_C["green"]}">'
                     'no environment issues in scope</p>')
        app_block = ""
        if app_level:
            app_block = (f'<p style="margin:10px 0 2px;font-size:13px"><b>App-level</b></p>'
                         f'<ul style="margin:2px 0 6px 18px;padding:0;font-size:12.5px;'
                         f'color:{_C["text"]}">{app_level}</ul>')
        issues_section = (f'<h2 style="font-size:15px;margin:18px 0 6px">Issue summary — per environment</h2>'
                          f'<div style="background:#fff;border:1px solid {_C["line"]};border-radius:6px;padding:8px 12px">'
                          f'{env_issue_blocks or none_note}{app_block}</div>')

    html = f"""<!doctype html><html><body style="margin:0;padding:0;background:{_C['bg']}">
<div style="max-width:860px;margin:0 auto;padding:18px;font-family:Arial,Helvetica,sans-serif;color:{_C['text']}">
  <h1 style="font-size:20px;margin:0 0 2px">📊 Logging health — {_esc(project)}</h1>
  <p style="margin:0 0 12px;color:{_C['dim']};font-size:12px">
    {f"🏢 {_esc(company)} · " if company else ""}{_esc(p.get('deploy_platform') or '—')} → {_esc(p.get('prefix') or '—')}
    · generated {_esc(now)}{f" · {_esc(scope_note)}" if scope_note else ""}
    · environments: {_esc(", ".join(env_names) or "none in scope")}</p>
  <p style="margin:0 0 14px;font-size:14px">Project health score: {_score_pill(pscore, 'project score (in scope)')}</p>
  <table cellspacing="6" cellpadding="0" style="border-collapse:separate;margin:0 0 16px"><tr>
    {stat("apps", len(scoped))}
    {stat("indices", t_idx)}
    {stat("total size", total_stat)}
    {stat("documents", f"{t_docs:,}")}
    {stat("avg app size", _esc(avg_app))}
    {stat("ingest / day", _esc(pr["size_day"]) if pr else "—")}
  </tr></table>

  <h2 style="font-size:15px;margin:16px 0 6px">Environments</h2>
  <table width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;background:#fff;border:1px solid {_C['line']};border-radius:6px">
    <tr><th style="{th}">env</th><th style="{th}">owner team</th><th style="{th}">score</th>
        <th style="{th}">apps</th><th style="{th}">indices</th><th style="{th}">size</th>
        <th style="{th}">rate</th><th style="{th}">issues</th></tr>
    {env_rows or f'<tr><td style="{td}" colspan="8">no environments in scope</td></tr>'}
  </table>

  <h2 style="font-size:15px;margin:18px 0 6px">Applications</h2>
  <table width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;background:#fff;border:1px solid {_C['line']};border-radius:6px">
    <tr><th style="{th}">app</th><th style="{th}">score</th>{env_head}
        <th style="{th}">total</th><th style="{th}">rate</th><th style="{th}">issues</th></tr>
    {app_rows or f'<tr><td style="{td}" colspan="6">no applications in scope</td></tr>'}
  </table>

  {issues_section}

  <p style="margin:16px 0 0;color:{_C['dim']};font-size:11px">Logging health report
    · retention prd {int((d.get('retention') or {}).get('prd_days') or 0)}d / non-prd {int((d.get('retention') or {}).get('nonprd_days') or 0)}d
    · stale &gt; {_esc(stale_hours)}h</p>
</div></body></html>"""
    return {"project": project,
            "subject": f"Logging health — {project}"
                       f" (score {pscore if pscore is not None else 'n/a'}/100)"
                       + (f" · {team} envs" if team else ""),
            "html": html, "envs": env_names, "hidden_healthy": hidden_healthy,
            "hidden_undeployed": hidden_undeployed,
            "hidden_unmonitored": hidden_unmonitored,
            "include_extra": include_extra, "team": team or None,
            "skip_healthy": skip_healthy}


def _complete_recipients(recipients: list[str]) -> list[str]:
    """Mirror send_mail.py's filter_words + domain completion: drop empties,
    the "@default.domain" placeholder and case-insensitive duplicates; append
    MAIL_DEFAULT_DOMAIN to bare (no-@) names."""
    dd = (settings.mail_default_domain or "").strip().lstrip("@")
    out: list[str] = []
    seen: set = set()
    for r in recipients or []:
        r = (r or "").strip()
        if not r or r.lower() == "@default.domain":
            continue
        if "@" not in r and dd:
            r = f"{r}@{dd}"
        if r.lower() in seen:
            continue
        seen.add(r.lower())
        out.append(r)
    return out


def _send_ews(subject: str, html: str, to: list[str]) -> None:
    """Exchange Web Services, exactly like the send_mail.py utility:
    Credentials → Configuration(server=SMTP_HOST) → Account(SMTP_FROM,
    autodiscover=False) → Message(HTMLBody, folder=Sent).send()."""
    try:
        from exchangelib import (Account, Configuration, Credentials,
                                 HTMLBody, Message)
    except ImportError as exc:
        raise RuntimeError("MAIL_TRANSPORT=ews but the exchangelib package is "
                           "not installed in the backend image — add "
                           "`exchangelib` (see requirements.txt) or switch "
                           "QO_MAIL_TRANSPORT=smtp") from exc
    credentials = Credentials(username=settings.smtp_user,
                              password=settings.smtp_password)
    config = Configuration(server=settings.smtp_host, credentials=credentials)
    account = Account(primary_smtp_address=settings.smtp_from,
                      credentials=credentials, autodiscover=False, config=config)
    message = Message(account=account, folder=account.sent, subject=subject,
                      body=HTMLBody(html), to_recipients=to)
    message.send()


def _send_smtp(subject: str, html: str, to: list[str]) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from
    msg["To"] = ", ".join(to)
    msg.set_content("This report is HTML — please open it in an HTML-capable mail client.")
    msg.add_alternative(html, subtype="html")
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


def send_report(project: str, recipients: list[str], subject: str | None = None,
                include_extra: bool = False, team: str | None = None,
                skip_healthy: bool = False, skip_undeployed: bool = False,
                skip_unmonitored: bool = False) -> dict:
    rep = build_report(project, include_extra=include_extra, team=team,
                       skip_healthy=skip_healthy, skip_undeployed=skip_undeployed,
                       skip_unmonitored=skip_unmonitored)
    # ADMIN_EMAIL is looped into every report send (deduped like the rest)
    admin = [settings.admin_email] if (settings.admin_email or "").strip() else []
    to = _complete_recipients(list(recipients or []) + admin)
    if not to:
        raise ValueError("no recipients given")
    bad = [r for r in to if not _EMAIL_RE.match(r)]
    if bad:
        hint = "" if settings.mail_default_domain else             " (bare names need QO_MAIL_DEFAULT_DOMAIN to be completed)"
        raise ValueError("invalid recipient(s): " + ", ".join(bad) + hint)
    subj = (subject or "").strip() or rep["subject"]
    if not settings.smtp_host:
        if settings.demo_mode:
            return {"ok": True, "sent": len(to), "recipients": to, "transport": "demo",
                    "note": "demo mode — mail server not configured, email NOT actually sent"}
        raise RuntimeError("mail is not configured — set QO_SMTP_HOST (and the other "
                           "QO_SMTP_*/QO_MAIL_* knobs) in .env")
    transport = (settings.mail_transport or "ews").strip().lower()
    sender = _send_smtp if transport == "smtp" else _send_ews
    retries = max(int(settings.mail_retries or 1), 1)
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            sender(subj, rep["html"], to)
            return {"ok": True, "sent": len(to), "recipients": to,
                    "transport": transport, "attempts": attempt}
        except Exception as exc:  # noqa: BLE001 — retried, then surfaced verbatim
            last_err = exc
            if attempt < retries:
                time.sleep(max(float(settings.mail_retry_wait or 0), 0))
    raise RuntimeError(
        f"{transport.upper()} send failed after {retries} attempt(s) — "
        f"server {settings.smtp_host!r}, user {settings.smtp_user!r}, "
        f"from {settings.smtp_from!r}, to {', '.join(to)} — "
        f"{type(last_err).__name__}: {last_err}")
