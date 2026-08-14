"""Conversation state machine — mirrors the design doc's §8 diagram exactly: there is
no path from `escalated` straight to `closed`, and no path that resumes Jane's
replies silently. Every return to `jane_handling` passes through an explicit
"resolved" action or a configured quiet period — never silent, a customer noticing a
voice change without explanation is worse than a slower reply.
"""

import time
from enum import Enum
from typing import Any, Dict, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.services.crypto_utils import hash_phone

COLLECTION = "jane_wa_conversations"


class ConversationState(str, Enum):
    JANE_HANDLING = "jane_handling"
    ESCALATED = "escalated"
    CLOSED = "closed"


async def get_or_create_conversation(db: AsyncIOMotorDatabase, raw_phone: str, brand_id: str) -> Dict[str, Any]:
    """Scoped by (phone_hash, brand_id), not phone_hash alone — without brand_id,
    two different clients' customers could collide onto the same conversation
    record now that this service handles more than one client's WhatsApp number."""
    phone_hash = hash_phone(raw_phone)
    existing = await db[COLLECTION].find_one({"phone_hash": phone_hash, "brand_id": brand_id})
    if existing:
        return existing

    now = time.time()
    doc = {
        "phone_hash": phone_hash,
        "brand_id": brand_id,
        "state": ConversationState.JANE_HANDLING.value,
        "escalated_reason": None,
        "escalated_at": None,
        "last_message_at": now,
        "created_at": now,
    }
    result = await db[COLLECTION].insert_one(doc)
    doc["_id"] = result.inserted_id
    return doc


async def escalate(db: AsyncIOMotorDatabase, conversation_id: Any, reason: str) -> None:
    await db[COLLECTION].update_one(
        {"_id": conversation_id},
        {
            "$set": {
                "state": ConversationState.ESCALATED.value,
                "escalated_reason": reason,
                "escalated_at": time.time(),
            }
        },
    )


async def resolve(db: AsyncIOMotorDatabase, conversation_id: Any) -> Optional[Dict[str, Any]]:
    """The only path back from `escalated` to `jane_handling` — an explicit action,
    never automatic. Returns the updated conversation, or None if it wasn't actually
    in `escalated` (resolving a conversation that isn't escalated is a no-op, not an
    error — avoids a caller racing itself into an inconsistent state)."""
    conversation = await db[COLLECTION].find_one({"_id": conversation_id})
    if not conversation or conversation.get("state") != ConversationState.ESCALATED.value:
        return None

    await db[COLLECTION].update_one(
        {"_id": conversation_id},
        {"$set": {"state": ConversationState.JANE_HANDLING.value, "escalated_reason": None}},
    )
    conversation["state"] = ConversationState.JANE_HANDLING.value
    conversation["escalated_reason"] = None
    return conversation


async def touch(db: AsyncIOMotorDatabase, conversation_id: Any) -> None:
    await db[COLLECTION].update_one({"_id": conversation_id}, {"$set": {"last_message_at": time.time()}})


async def close(db: AsyncIOMotorDatabase, conversation_id: Any) -> None:
    """Only reachable from jane_handling (per the design doc's diagram — there's no
    escalated→closed edge; an escalated conversation must be resolved first)."""
    await db[COLLECTION].update_one(
        {"_id": conversation_id, "state": ConversationState.JANE_HANDLING.value},
        {"$set": {"state": ConversationState.CLOSED.value}},
    )
