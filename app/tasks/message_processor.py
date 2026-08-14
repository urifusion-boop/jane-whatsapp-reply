"""Celery task — mirrors whatsapp-agent's app/tasks/message_processor.py shape:
idempotency claim on the inbound message id, retry-with-backoff on failure (and the
claim is released on failure so the retry actually re-processes rather than being
permanently skipped as a false duplicate), then the actual reply/escalate flow.
"""

import asyncio
from datetime import datetime
from typing import Any, Dict

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import DuplicateKeyError

from app.celery_app import celery_app
from app.core.config import settings
from app.services import client_registry, conversation_service, reply_engine
from app.services.cloud_api_client import extract_inbound_message, send_message
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


async def _process_message_async(payload: Dict[str, Any]) -> None:
    message = extract_inbound_message(payload)
    if message is None:
        return  # not a text message we handle (status update, media, etc.)

    client_doc = await client_registry.get_client_by_phone_number_id(message["phone_number_id"])
    if client_doc is not None:
        brand_id = client_doc.get("brand_id") or client_doc.get("user_id")
        escalation_email = client_doc.get("escalation_email")
    elif message["phone_number_id"] == settings.WHATSAPP_PHONE_NUMBER_ID and settings.URI_BRAND_ID:
        # Transition fallback (Phase 3 migration): URI's own rehearsal number
        # hasn't been backfilled into social_connections yet — fall back to the
        # old single-tenant settings rather than dropping the message. Once the
        # migration doc exists, get_client_by_phone_number_id resolves it and
        # this branch stops firing (self-verifying: check the logs use the
        # registry path, not this one).
        brand_id = settings.URI_BRAND_ID
        escalation_email = settings.ESCALATION_EMAIL_TO
        print(f"[message_processor] using transition fallback for phone_number_id={message['phone_number_id']} — social_connections doc not found yet")
    else:
        print(f"[message_processor] no active whatsapp_business connection for phone_number_id={message['phone_number_id']} — dropping")
        return

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

        conversation = await conversation_service.get_or_create_conversation(db, message["from"], brand_id)
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
            await send_message(message["from"], result.reply_text, message["phone_number_id"])
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
            await send_message(message["from"], reply_engine.HOLDING_REPLY, message["phone_number_id"])
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
