"""Human customer-care agent actions on an escalated conversation — the actual
missing capability this feature adds. Three entry points, one per reply channel:

- send_agent_reply(): dashboard or email-deep-link channel — agent typed a reply,
  it must be sent to the customer via Meta's Cloud API, logged, and the
  conversation resolved.
- resolve_only(): "I handled this out-of-band" (a phone call, in person) — no
  message to send or log, just close out the escalation.
- log_agent_echo(): WhatsApp/Coexistence channel — a human already replied
  directly from the phone app; Meta's message-echo webhook tells us about it after
  the fact. Must NOT call send_message() here — the message already went out over
  WhatsApp directly, sending again would double-send to the customer.

Idempotency note (send_agent_reply only — the other two are naturally idempotent,
Mongo writes with no external side effect): talking to Meta's API can't be rolled
into the same commit as the Mongo writes that log the message and resolve the
conversation, so this is idempotent-on-retry, not atomically transactional. The
claim record's `sent` field distinguishes "fully done, safe no-op" from "ambiguous —
might have reached Meta, might not have" on retry. The ambiguous case is
deliberately NOT auto-retried (see AgentReplyInProgressError) — a false automatic
retry risks a real double-send to the customer, which is worse than surfacing the
ambiguity and letting a human check the conversation's actual message history
before deciding to send again with a fresh idempotency_key.
"""

from datetime import datetime
from typing import Any, Dict, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError

from app.services import client_registry, conversation_service
from app.services.cloud_api_client import send_message
from app.services.crypto_utils import decrypt_phone, encrypt_body

CONVERSATIONS_COLLECTION = conversation_service.COLLECTION
MESSAGES_COLLECTION = "jane_wa_messages"
CLAIMS_COLLECTION = "jane_wa_agent_reply_claims"


class ConversationNotFoundError(Exception):
    def __init__(self, conversation_id: Any):
        self.conversation_id = conversation_id
        super().__init__(f"Conversation not found: {conversation_id}")


class ConversationNotEscalatedError(Exception):
    def __init__(self, conversation_id: Any):
        self.conversation_id = conversation_id
        super().__init__(f"Conversation is not currently escalated: {conversation_id}")


class AgentReplyInProgressError(Exception):
    """A reply attempt under this exact idempotency_key already exists and isn't
    cleanly finished — see module docstring on why this isn't auto-retried."""
    pass


async def _resolve_send_target(db: AsyncIOMotorDatabase, conversation: Dict[str, Any]) -> tuple:
    """Returns (customer_phone, phone_number_id, access_token) for sending a reply
    on this conversation — mirrors message_processor.py's own resolution: prefer a
    multi-tenant client_registry connection's access_token if one exists, else the
    global default (send_message() itself falls back to settings.WHATSAPP_ACCESS_TOKEN)."""
    customer_phone = decrypt_phone(conversation["phone_encrypted"])
    phone_number_id = conversation.get("phone_number_id") or ""

    client_doc = await client_registry.get_client_by_phone_number_id(phone_number_id)
    access_token = client_doc.get("access_token") if client_doc else None

    return customer_phone, phone_number_id, access_token


async def send_agent_reply(
    db: AsyncIOMotorDatabase,
    conversation_id: Any,
    body: str,
    agent_email: str,
    agent_id: str,
    idempotency_key: str,
    channel: str = "dashboard",
) -> Dict[str, Any]:
    claims = db[CLAIMS_COLLECTION]

    existing_claim = await claims.find_one({"idempotency_key": idempotency_key})
    if existing_claim is not None:
        if existing_claim.get("sent"):
            conversation = await db[CONVERSATIONS_COLLECTION].find_one({"_id": conversation_id})
            return {"status": "already_sent", "conversation": conversation}
        raise AgentReplyInProgressError(
            f"A reply attempt for idempotency_key={idempotency_key!r} is already in "
            "flight or ended ambiguously — check the conversation's message history "
            "before retrying with a new idempotency_key."
        )

    conversation = await db[CONVERSATIONS_COLLECTION].find_one({"_id": conversation_id})
    if conversation is None:
        raise ConversationNotFoundError(conversation_id)
    if conversation.get("state") != conversation_service.ConversationState.ESCALATED.value:
        raise ConversationNotEscalatedError(conversation_id)

    try:
        await claims.insert_one(
            {
                "idempotency_key": idempotency_key,
                "conversation_id": conversation_id,
                "sent": False,
                "created_at": datetime.utcnow(),
            }
        )
    except DuplicateKeyError:
        # Lost a race against a concurrent request using the same idempotency_key.
        raise AgentReplyInProgressError(
            f"A reply attempt for idempotency_key={idempotency_key!r} is already in "
            "flight — check the conversation's message history before retrying with "
            "a new idempotency_key."
        )

    try:
        customer_phone, phone_number_id, access_token = await _resolve_send_target(db, conversation)
        await send_message(customer_phone, body, phone_number_id, access_token)
        # Set sent=True immediately after the send succeeds, before any further
        # Mongo writes — narrows (does not eliminate) the window in which a crash
        # could leave us unable to tell whether Meta actually received the message.
        await claims.update_one({"idempotency_key": idempotency_key}, {"$set": {"sent": True}})
    except Exception:
        # Never reached a confirmed-sent state — safe to fully release the claim,
        # a retry with the SAME idempotency_key can start clean.
        await claims.delete_one({"idempotency_key": idempotency_key})
        raise

    await db[MESSAGES_COLLECTION].insert_one(
        {
            "conversation_id": conversation_id,
            "role": "agent",
            "channel": channel,
            "agent_email": agent_email,
            "agent_id": agent_id,
            "body_encrypted": encrypt_body(body),
            "confidence": None,
            "created_at": datetime.utcnow(),
        }
    )

    updated = await conversation_service.resolve(db, conversation_id, resolved_via=channel, resolved_by=agent_email)
    return {"status": "sent", "conversation": updated}


async def resolve_only(
    db: AsyncIOMotorDatabase, conversation_id: Any, agent_email: str, agent_id: str
) -> Dict[str, Any]:
    """Out-of-band resolution (phone call, in person) — no message sent or logged.
    Not the way to resolve a WhatsApp-echo reply, which self-resolves via
    log_agent_echo() below."""
    conversation = await db[CONVERSATIONS_COLLECTION].find_one({"_id": conversation_id})
    if conversation is None:
        raise ConversationNotFoundError(conversation_id)

    updated = await conversation_service.resolve(db, conversation_id, resolved_via="manual", resolved_by=agent_email)
    if updated is None:
        return {"status": "not_escalated", "conversation": conversation}
    return {"status": "resolved", "conversation": updated}


async def log_agent_echo(
    db: AsyncIOMotorDatabase,
    conversation_id: Any,
    echo_text: str,
    echo_meta: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """A human already replied directly from the WhatsApp Business phone app
    (Coexistence) — Meta's message-echo webhook is telling us after the fact.
    NEVER calls send_message() — the message already went out over WhatsApp
    directly; sending again would double-send to the customer.

    agent_email is deliberately None here — Meta's echo payload may not identify
    which team member sent it if multiple people share the same device (see
    cloud_api_client.extract_message_echo's TODO); this is a possible platform
    limitation to confirm once the real schema is verified, not something this
    function can work around."""
    conversation = await db[CONVERSATIONS_COLLECTION].find_one({"_id": conversation_id})
    if conversation is None:
        return None  # unknown conversation — nothing to log or resolve

    await db[MESSAGES_COLLECTION].insert_one(
        {
            "conversation_id": conversation_id,
            "role": "agent",
            "channel": "whatsapp_echo",
            "agent_email": None,
            "agent_id": None,
            "echo_meta": echo_meta,
            "body_encrypted": encrypt_body(echo_text),
            "confidence": None,
            "created_at": datetime.utcnow(),
        }
    )

    return await conversation_service.resolve(db, conversation_id, resolved_via="whatsapp_echo")
