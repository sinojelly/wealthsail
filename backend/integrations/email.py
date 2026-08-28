"""Minimal SMTP delivery adapter for security notifications."""

from __future__ import annotations

import asyncio
import smtplib
from email.message import EmailMessage
from urllib.parse import quote

from backend.config import Settings


async def send_password_reset_email(
    settings: Settings, *, recipient: str, token: str
) -> None:
    if not settings.smtp_host or not settings.smtp_from:
        if settings.is_production:
            raise RuntimeError("SMTP is not configured")
        return
    # A URL fragment is never sent in the HTTP request line or Referer. The
    # browser reads it once and immediately removes it from the address bar.
    reset_url = (
        f"{settings.web_base_url.rstrip('/')}/login"
        f"#reset-token={quote(token, safe='')}"
    )
    message = EmailMessage()
    message["Subject"] = "WealthSail 密码重置"
    message["From"] = settings.smtp_from
    message["To"] = recipient
    message.set_content(
        "你正在重置 WealthSail 密码。请在有效期内打开以下链接：\n\n"
        f"{reset_url}\n\n如果并非本人操作，请忽略本邮件。"
    )

    def _send() -> None:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as client:
            if settings.smtp_starttls:
                client.starttls()
            if settings.smtp_username:
                client.login(settings.smtp_username, settings.smtp_password or "")
            client.send_message(message)

    await asyncio.to_thread(_send)
