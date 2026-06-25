"""알림 메일 발송. SMTP 미설정(dev)이면 발송 대신 로그로 남긴다(외부 의존 없음).

발송 실패는 절대 요청을 깨지 않도록 호출부에서 best-effort 로 사용한다(send_safe).
"""
from __future__ import annotations

import asyncio
import logging
import smtplib
import ssl
from email.message import EmailMessage
from typing import Any

from fastapi import Depends

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)


class EmailSender:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def dev_mode(self) -> bool:
        return not self._settings.smtp_host

    async def send(self, *, to: str | None, subject: str, body: str) -> None:
        if not to:
            return
        if self.dev_mode:
            logger.info("[email:dev] to=%s subject=%s\n%s", to, subject, body)
            return
        message = EmailMessage()
        message["From"] = self._settings.email_from
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)
        await asyncio.to_thread(self._send_smtp, message)

    async def send_safe(self, *, to: str | None, subject: str, body: str) -> None:
        """발송 실패가 본 요청을 깨지 않도록 예외를 삼킨다."""
        try:
            await self.send(to=to, subject=subject, body=body)
        except Exception:  # noqa: BLE001 - 알림은 부가기능, 본 흐름 보호 우선
            logger.warning("알림 메일 발송 실패: to=%s subject=%s", to, subject, exc_info=True)

    def _send_smtp(self, message: EmailMessage) -> None:
        context = ssl.create_default_context()
        with smtplib.SMTP(self._settings.smtp_host, self._settings.smtp_port) as server:
            server.starttls(context=context)
            if self._settings.smtp_user:
                server.login(self._settings.smtp_user, self._settings.smtp_password or "")
            server.send_message(message)


def get_email_sender(settings: Settings = Depends(get_settings)) -> EmailSender:
    return EmailSender(settings)


def _fmt_date(value: Any) -> str:
    iso = getattr(value, "isoformat", None)
    text = iso() if callable(iso) else str(value or "")
    return text[:10]
