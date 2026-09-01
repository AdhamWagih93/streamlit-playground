"""E-mail page — READ the service account's mailbox over EWS (exchangelib).

Scope is deliberately narrow:
  - the SERVICE ACCOUNT mailbox only (settings.smtp_from) — no impersonation
  - a HARD two-week lookback: whatever the filters say, nothing older than
    MAX_DAYS is ever requested from Exchange (server-side date filter)
  - read-only: no send / delete / move
Bodies are sanitised server-side and additionally rendered inside a sandboxed
iframe by the UI; credentials never leave the config.
"""

import datetime as dt
import html as html_mod
import re
import time

from ..config import settings

MAX_DAYS = 14            # the hard lookback ceiling (user rule: 2 weeks)
MAX_LIMIT = 100
_LIST_TTL = 60           # seconds — one EWS round trip per filter set per minute
_LIST_CACHE: dict = {}

FOLDERS = ("inbox", "sent")


def _require_ews():
    if settings.mail_transport != "ews":
        raise RuntimeError("the E-mail page reads over EWS — set QO_MAIL_TRANSPORT=ews "
                           "(SMTP is a send-only protocol and cannot read a mailbox)")
    if not (settings.smtp_host and settings.smtp_user and settings.smtp_from):
        raise RuntimeError("mail is not configured (QO_SMTP_HOST / QO_SMTP_USER / QO_SMTP_FROM)")


def _account():
    from exchangelib import Account, Configuration, Credentials
    credentials = Credentials(username=settings.smtp_user, password=settings.smtp_password)
    config = Configuration(server=settings.smtp_host, credentials=credentials)
    return Account(primary_smtp_address=settings.smtp_from, credentials=credentials,
                   autodiscover=False, config=config)


# paired tags disappear WITH their content (a script's source must not leak
# into the visible text); link/meta are void; stray closers are swept after
_TAG_PAIRED = re.compile(r"(?is)<(script|style|iframe|object|embed|form|noscript)\b[^>]*>.*?</\1\s*>")
_TAG_VOID = re.compile(r"(?is)<(?:link|meta)\b[^>]*/?>")
_TAG_STRAY = re.compile(r"(?is)</?(?:script|style|iframe|object|embed|form|noscript)\b[^>]*>")
_ON_ATTR = re.compile(r"(?i)\son\w+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)")
_JS_URL = re.compile(r"(?i)(href|src)\s*=\s*([\"']?)\s*javascript:[^\"'>\s]*")
_IMG_SRC = re.compile(r"(?i)(<img\b[^>]*?)\ssrc\s*=")


def sanitize_html(body: str) -> str:
    """Server-side pass: scripts/styles/frames/forms gone, on* handlers gone,
    javascript: URLs gone, remote images blocked (src -> data-blocked-src; the
    UI offers a 'load images' toggle). The UI also renders the result inside
    a sandboxed iframe, so this is defence in depth, not the only wall."""
    body = _TAG_PAIRED.sub("", body or "")
    body = _TAG_VOID.sub("", body)
    body = _TAG_STRAY.sub("", body)
    body = _ON_ATTR.sub("", body)
    body = _JS_URL.sub(r"\1=\2#blocked", body)
    body = _IMG_SRC.sub(r"\1 data-blocked-src=", body)
    return body


def _clamp(days, limit):
    try:
        days = min(int(days or MAX_DAYS), MAX_DAYS)
    except (TypeError, ValueError):
        days = MAX_DAYS
    try:
        limit = max(1, min(int(limit or 50), MAX_LIMIT))
    except (TypeError, ValueError):
        limit = 50
    return max(1, days), limit


def _row(m) -> dict:
    sender = getattr(m, "sender", None)
    return {"id": getattr(m, "id", "") or "",
            "subject": getattr(m, "subject", "") or "(no subject)",
            "from_name": getattr(sender, "name", "") or "",
            "from_email": getattr(sender, "email_address", "") or "",
            "when": (m.datetime_received.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
                     if getattr(m, "datetime_received", None) else ""),
            "unread": not bool(getattr(m, "is_read", True)),
            "attachments": bool(getattr(m, "has_attachments", False)),
            "importance": str(getattr(m, "importance", "") or "").lower()}


def list_messages(folder: str = "inbox", q: str = "", sender: str = "", unread: bool = False,
                  attachments: bool = False, days: int = MAX_DAYS, limit: int = 50,
                  offset: int = 0, refresh: bool = False) -> dict:
    days, limit = _clamp(days, limit)
    offset = max(0, min(int(offset or 0), 1000))
    folder = folder if folder in FOLDERS else "inbox"
    key = (folder, q.lower(), sender.lower(), unread, attachments, days, limit, offset)
    hit = _LIST_CACHE.get(key)
    if hit and not refresh and time.time() - hit["at"] < _LIST_TTL:
        return {**hit["value"], "cached": True}
    if settings.demo_mode:
        value = _demo_list(folder, q, sender, unread, attachments, days, limit, offset)
    else:
        _require_ews()
        acct = _account()
        f = acct.inbox if folder == "inbox" else acct.sent
        since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
        from exchangelib import EWSDateTime, EWSTimeZone
        qs = f.filter(datetime_received__gte=EWSDateTime.from_datetime(since.astimezone(EWSTimeZone("UTC"))))
        if sender:
            qs = qs.filter(sender__icontains=sender)
        if q:
            qs = qs.filter(subject__icontains=q)
        if unread:
            qs = qs.filter(is_read=False)
        if attachments:
            qs = qs.filter(has_attachments=True)
        qs = qs.order_by("-datetime_received").only(
            "id", "subject", "sender", "datetime_received", "is_read",
            "has_attachments", "importance")
        total = qs.count()
        rows = [_row(m) for m in qs[offset:offset + limit]]
        value = {"folder": folder, "days": days, "total": total, "offset": offset,
                 "messages": rows, "mailbox": settings.smtp_from,
                 "note": f"service-account mailbox · last {days} day(s) only"}
    _LIST_CACHE[key] = {"at": time.time(), "value": value}
    return {**value, "cached": False}


def get_message(msg_id: str) -> dict:
    if settings.demo_mode:
        return _demo_message(msg_id)
    _require_ews()
    acct = _account()
    m = acct.inbox.get(id=msg_id) if True else None
    body = str(getattr(m, "body", "") or "")
    is_html = "<" in body and ">" in body
    atts = []
    for a in getattr(m, "attachments", None) or []:
        atts.append({"name": getattr(a, "name", "") or "attachment",
                     "size": getattr(a, "size", 0) or 0,
                     "content_type": getattr(a, "content_type", "") or ""})
    to = [getattr(x, "email_address", "") or getattr(x, "name", "") for x in (getattr(m, "to_recipients", None) or [])]
    cc = [getattr(x, "email_address", "") or getattr(x, "name", "") for x in (getattr(m, "cc_recipients", None) or [])]
    return {**_row(m), "to": to, "cc": cc,
            "html": sanitize_html(body) if is_html else "",
            "text": "" if is_html else body}


# ---------------------------------------------------------------- demo inbox
def _demo_rows() -> list[dict]:
    now = dt.datetime.now(dt.timezone.utc)
    mk = lambda i, h, frm, mail, subj, unread=False, att=False, imp="normal": {  # noqa: E731
        "id": f"demo-{i}", "subject": subj, "from_name": frm, "from_email": mail,
        "when": (now - dt.timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%S"),
        "unread": unread, "attachments": att, "importance": imp}
    return [
        mk(1, 2, "RLM System", "rlm@corp.local", "RLM-2214 release ticket approved", unread=True, imp="high"),
        mk(2, 5, "Alice Nasr", "alice.nasr@corp.local", "Re: prd deployment window tonight", unread=True),
        mk(3, 11, "Jenkins", "ci@corp.local", "payments-service build 1.20.1 FAILED", att=True, imp="high"),
        mk(4, 26, "ITSM", "itsm@corp.local", "CHG0041244 waiting for approver_team", unread=True),
        mk(5, 50, "Prisma Cloud", "prisma@corp.local", "Weekly image scan digest", att=True),
        mk(6, 96, "Bob Builder", "bob@corp.local", "Standard change vars.yml question"),
        mk(7, 170, "Grace Ops", "grace@corp.local", "UAT data refresh done", att=True),
        mk(8, 300, "Exchange", "postmaster@corp.local", "Undeliverable: report to old-team@corp.local"),
    ]


def _demo_list(folder, q, sender, unread, attachments, days, limit, offset):
    rows = _demo_rows() if folder == "inbox" else _demo_rows()[1:3]
    cut = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    rows = [r for r in rows if r["when"] >= cut.strftime("%Y-%m-%dT%H:%M:%S")]
    if q:
        rows = [r for r in rows if q.lower() in r["subject"].lower()]
    if sender:
        rows = [r for r in rows if sender.lower() in (r["from_name"] + r["from_email"]).lower()]
    if unread:
        rows = [r for r in rows if r["unread"]]
    if attachments:
        rows = [r for r in rows if r["attachments"]]
    return {"folder": folder, "days": days, "total": len(rows), "offset": offset,
            "messages": rows[offset:offset + limit], "mailbox": "questops@corp.local",
            "note": f"service-account mailbox · last {days} day(s) only"}


def _demo_message(msg_id: str) -> dict:
    row = next((r for r in _demo_rows() if r["id"] == msg_id), None) or _demo_rows()[0]
    raw = ("<div><p>Hello team,</p><p>The <b>release ticket</b> was approved. "
           "<script>alert('xss')</script><img src='https://tracker.evil/px.gif'>"
           "Details in the portal: <a href='javascript:steal()'>link</a> "
           "<a href='https://itsm.corp.local/CHG0041244'>CHG0041244</a>.</p>"
           "<p>— automated notification</p></div>")
    return {**row, "to": ["questops@corp.local"], "cc": [],
            "html": sanitize_html(raw), "text": ""}
