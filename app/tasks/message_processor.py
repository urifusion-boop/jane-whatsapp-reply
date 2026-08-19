"""Celery task — mirrors whatsapp-agent's app/tasks/message_processor.py shape:
idempotency claim on the inbound message id, retry-with-backoff on failure (and the
claim is released on failure so the retry actually re-processes rather than being
permanently skipped as a false duplicate), then the actual reply/escalate flow.
"""

import asyncio
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import DuplicateKeyError

from app.celery_app import celery_app
from app.core.config import settings
from app.services import agent_reply_service, client_registry, conversation_service, reply_engine
from app.services.cloud_api_client import extract_inbound_message, extract_message_echo, send_message
from app.services.crypto_utils import encrypt_body
from app.services.escalation_notifier import notify_escalation

PROCESSED_COLLECTION = "jane_wa_processed_messages"
MESSAGES_COLLECTION = "jane_wa_messages"


@celery_app.task(name="app.tasks.message_processor.process_whatsapp_message", bind=True)
def process_whatsapp_message(self, payload: Dict[str, Any]):
    try:
        return asyncio.run(_process_message_async(payload))
    except Exception as exc:
        raise self.retry(exc=exc, countdown=2 ** self.request.retries, max_retries=3)


async def _resolve_client_context(phone_number_id: str) -> Optional[Tuple[str, Optional[str], Optional[str]]]:
    """Which brand/escalation-inbox/access-token a given inbound phone_number_id
    belongs to — shared by both the customer-message path and the message-echo
    path, since both need to resolve a conversation the same way. Returns None if
    the number is genuinely unrecognized (caller must drop, never guess a
    fallback brand)."""
    client_doc = await client_registry.get_client_by_phone_number_id(phone_number_id)
    if client_doc is not None:
        brand_id = client_doc.get("brand_id") or client_doc.get("user_id")
        escalation_email = client_doc.get("escalation_email")
        # None for URI's own manually-configured rehearsal connection (no
        # per-client token stored — it uses the shared System User grant) or any
        # connection predating this field; real clients onboarded via Embedded
        # Signup have their own 60-day token here, kept alive by
        # uri-social-backend's run_whatsapp_token_refresh cron job.
        # send_message() falls back to the global WHATSAPP_ACCESS_TOKEN when this
        # is None.
        access_token = client_doc.get("access_token")
        return brand_id, escalation_email, access_token

    if phone_number_id == settings.WHATSAPP_PHONE_NUMBER_ID and settings.URI_BRAND_ID:
        # Transition fallback (Phase 3 migration): URI's own rehearsal number
        # hasn't been backfilled into social_connections yet — fall back to the
        # old single-tenant settings rather than dropping the message. Once the
        # migration doc exists, get_client_by_phone_number_id resolves it and
        # this branch stops firing (self-verifying: check the logs use the
        # registry path, not this one).
        print(f"[message_processor] using transition fallback for phone_number_id={phone_number_id} — social_connections doc not found yet")
        return settings.URI_BRAND_ID, settings.ESCALATION_EMAIL_TO, None

    print(f"[message_processor] no active whatsapp_business connection for phone_number_id={phone_number_id} — dropping")
    return None


async def _process_message_async(payload: Dict[str, Any]) -> None:
    message = extract_inbound_message(payload)
    if message is None:
        # Not a customer text message — could be a status update, media, or a
        # WhatsApp/Coexistence message-echo event (a human replied from the phone
        # app). extract_message_echo is a stub (returns None unconditionally)
        # until Meta's real echo schema is verified — see its own docstring.
        echo = extract_message_echo(payload)
        if echo is not None:
            await _process_message_echo_async(echo)
        return

    context = await _resolve_client_context(message["phone_number_id"])
    if context is None:
        return
    brand_id, escalation_email, access_token = context

    client = AsyncIOMotorClient(settings.MONGODB_URI)
    db = client[settings.MONGODB_DB]

    claimed = False
    try:
        try:
            await db[PROCESSED_COLLECTION].insert_one(
                {"message_id": message["message_id"], "created_at": datetime.utcnow()}
            )
            claimed = True
        except DuplicateKeyError:
            return  # already processed — Meta redelivered the same webhook event

        conversation = await conversation_service.get_or_create_conversation(
            db, message["from"], brand_id, phone_number_id=message["phone_number_id"]
        )
        await conversation_service.touch(db, conversation["_id"])

        await db[MESSAGES_COLLECTION].insert_one(
            {
                "conversation_id": conversation["_id"],
                "role": "customer",
                "body_encrypted": encrypt_body(message["text"]),
                "confidence": None,
                "created_at": datetime.utcnow(),
            }
        )

        if conversation["state"] == conversation_service.ConversationState.ESCALATED.value:
            # Jane stays quiet on an escalated conversation until it's explicitly
            # resolved — never resumes mid-exchange without the customer noticing.
            return

        result = await reply_engine.handle(message["text"], brand_id)

        if result.matched:
            await send_message(message["from"], result.reply_text, message["phone_number_id"], access_token)
            await db[MESSAGES_COLLECTION].insert_one(
                {
                    "conversation_id": conversation["_id"],
                    "role": "jane",
                    "body_encrypted": encrypt_body(result.reply_text),
                    "confidence": 1.0,
                    "created_at": datetime.utcnow(),
                }
            )
        else:
            await send_message(message["from"], reply_engine.HOLDING_REPLY, message["phone_number_id"], access_token)
            await db[MESSAGES_COLLECTION].insert_one(
                {
                    "conversation_id": conversation["_id"],
                    "role": "jane",
                    "body_encrypted": encrypt_body(reply_engine.HOLDING_REPLY),
                    "confidence": 0.0,
                    "created_at": datetime.utcnow(),
                }
            )
            reason = "No exact match in operational_facts for this question."
            await conversation_service.escalate(db, conversation["_id"], reason)
            await notify_escalation(str(conversation["_id"]), message["text"], reason, escalation_email)
    except Exception:
        if claimed:
            # Release the claim so a retry actually re-processes this message
            # instead of being silently skipped as a false duplicate — the exact
            # bug this pattern fixes, per whatsapp-agent's own history.
            await db[PROCESSED_COLLECTION].delete_one({"message_id": message["message_id"]})
        raise
    finally:
        client.close()


async def _process_message_echo_async(echo: Dict[str, Any]) -> None:
    """A human sent a message directly from the WhatsApp Business phone app on a
    Coexistence-enabled number — resolve the same conversation the customer-message
    path would, log it, and auto-resolve. Never calls send_message() — the message
    already went out over WhatsApp directly.

    echo's expected shape is PROVISIONAL, matching extract_inbound_message's shape
    for consistency (from/text/phone_number_id) — extract_message_echo() is a stub
    returning None unconditionally until Meta's real Coexistence echo schema is
    confirmed (see its docstring), so this function has not been exercised against
    a real payload. Revisit both together.

    TODO: once the real schema is confirmed, check whether Meta's echo event
    carries its own unique event/message id — if so, add the same idempotency-claim
    pattern _process_message_async uses (PROCESSED_COLLECTION), since Meta webhooks
    can redeliver the same event. Omitted for now because the field to claim on
    isn't known yet; log_agent_echo() calling conversation_service.resolve() twice
    for a genuine redelivery is a harmless no-op on the second call, but it would
    still insert a duplicate message row, which isn't correct long-term."""
    context = await _resolve_client_context(echo["phone_number_id"])
    if context is None:
        return
    brand_id, _escalation_email, _access_token = context

    client = AsyncIOMotorClient(settings.MONGODB_URI)
    try:
        db = client[settings.MONGODB_DB]
        conversation = await conversation_service.get_or_create_conversation(
            db, echo["from"], brand_id, phone_number_id=echo["phone_number_id"]
        )
        await conversation_service.touch(db, conversation["_id"])
        await agent_reply_service.log_agent_echo(db, conversation["_id"], echo["text"], echo_meta=echo)
    finally:
        client.close()
