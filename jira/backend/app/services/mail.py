"""Outbound SMTP mail sender driven by the admin-configured MailConfig.

This module is intentionally dependency-free: it relies only on the Python
standard library (``smtplib`` + ``email.mime``) so it can be used anywhere in
the app without pulling in extra packages. The SMTP credentials live in the
``mail_config`` row (id 1) and the password is stored encrypted; we decrypt it
lazily only at send time.
"""
from __future__ import annotations

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

from sqlalchemy.orm import Session

from app.core.crypto import decrypt
from app.models import MailConfig

_TIMEOUT = 10  # seconds


def get_mail_config(db: Session) -> MailConfig | None:
    """Return the singleton mail config row (id 1) or None if unset."""
    return db.get(MailConfig, 1)


def build_message(
    from_name: str,
    from_address: str,
    to: list[str],
    subject: str,
    body_text: str,
    body_html: str | None = None,
) -> MIMEMultipart:
    """Build a MIME multipart/alternative message (stdlib only)."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = formataddr((from_name or "Trackly", from_address or ""))
    msg["To"] = ", ".join(to)
    msg.attach(MIMEText(body_text or "", "plain", "utf-8"))
    if body_html:
        msg.attach(MIMEText(body_html, "html", "utf-8"))
    return msg


def send_email(
    db: Session,
    to: str | list[str],
    subject: str,
    body_text: str,
    body_html: str | None = None,
) -> tuple[bool, str]:
    """Send an email using the configured SMTP server.

    Returns ``(True, "sent")`` on success, or ``(False, message)`` on failure
    or when mail is not configured. Never raises.
    """
    cfg = get_mail_config(db)
    if cfg is None or not cfg.enabled or not cfg.host:
        return (False, "Mail not configured")

    recipients = [to] if isinstance(to, str) else list(to)
    recipients = [r for r in recipients if r]
    if not recipients:
        return (False, "No recipients")

    from_address = cfg.from_address or cfg.username or ""
    msg = build_message(
        from_name=cfg.from_name,
        from_address=from_address,
        to=recipients,
        subject=subject,
        body_text=body_text,
        body_html=body_html,
    )

    try:
        if cfg.use_ssl:
            server: smtplib.SMTP = smtplib.SMTP_SSL(cfg.host, cfg.port, timeout=_TIMEOUT)
        else:
            server = smtplib.SMTP(cfg.host, cfg.port, timeout=_TIMEOUT)
        try:
            server.ehlo()
            if cfg.use_tls and not cfg.use_ssl:
                server.starttls()
                server.ehlo()
            if cfg.username:
                password = decrypt(cfg.password_enc) or ""
                server.login(cfg.username, password)
            server.sendmail(from_address, recipients, msg.as_string())
        finally:
            try:
                server.quit()
            except Exception:
                pass
        return (True, "sent")
    except Exception as exc:  # noqa: BLE001 - surface any SMTP/network error to caller
        return (False, str(exc))


def send_test_email(db: Session, to: str | list[str]) -> tuple[bool, str]:
    """Send a canned test email so admins can validate SMTP settings."""
    subject = "Trackly test email"
    body_text = (
        "This is a test email from Trackly.\n\n"
        "If you received this message, your outbound mail configuration is "
        "working correctly."
    )
    body_html = (
        "<p>This is a test email from <strong>Trackly</strong>.</p>"
        "<p>If you received this message, your outbound mail configuration is "
        "working correctly.</p>"
    )
    return send_email(db, to, subject, body_text, body_html)
