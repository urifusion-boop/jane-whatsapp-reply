"""Escalation notification — email is the v1 channel for every client (see plan's
"Decisions made": a real client escalates into their own WhatsApp Business app;
URI's own rehearsal has no such app, so email is the actual v1 default here, not a
shortcut around the design doc's "no second channel" stance for real clients).

The recipient is per-client (escalation_email on that client's social_connections
doc, resolved by client_registry), not a single global setting — SMTP_* stays
global, that's our own outbound relay, not client data.

Never includes the raw phone number — only the conversation's hashed id, so a
compromised inbox can't leak a real customer's number.
"""

from email.message import EmailMessage
from typing import Optional

import aiosmtplib

from app.core.config import settings


async def notify_escalation(conversation_id: str, question: str, reason: str, escalation_email: Optional[str]) -> None:
    if not escalation_email or not settings.SMTP_HOST:
        # No notification channel configured — log loudly rather than silently
        # dropping the escalation. The conversation is still marked `escalated` in
        # Mongo regardless, so nothing is lost, just not actively pushed anywhere.
        print(
            f"[ESCALATION] no email channel configured — conversation={conversation_id} "
            f"question={question!r} reason={reason!r}"
        )
        return

    message = EmailMessage()
    message["From"] = settings.SMTP_FROM or settings.SMTP_USER
    message["To"] = escalation_email
    message["Subject"] = "Jane on WhatsApp — a customer question needs your input"
    message.set_content(
        f"A customer asked something Jane couldn't answer from the current "
        f"operational facts:\n\n"
        f'  "{question}"\n\n'
        f"Reason: {reason}\n"
        f"Conversation: {conversation_id}\n\n"
        f"Jane has sent a holding reply and will stay quiet on this conversation "
        f"until it's resolved."
    )

    await aiosmtplib.send(
        message,
        hostname=settings.SMTP_HOST,
        port=settings.SMTP_PORT,
        username=settings.SMTP_USER or None,
        password=settings.SMTP_PASSWORD or None,
        start_tls=True,
    )
