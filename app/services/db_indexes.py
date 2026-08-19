"""Shared index-creation logic, called from BOTH Celery's worker startup
(celery_app.py) and the FastAPI app's own startup (main.py) — not just one or the
other. jane_wa_processed_messages/jane_wa_conversations are written exclusively by
Celery tasks (message_processor.py), but jane_wa_agent_reply_claims and the new
message-history index on jane_wa_messages are written by the FastAPI process
directly (internal_router.py -> agent_reply_service.py, no Celery task involved).
Given webhook and worker replicas are separate containers (see
docker-compose.enterprise.yml), relying on only the Celery hook would leave those
indexes never created in a deployment where the API starts without a worker having
run first.
"""

from motor.motor_asyncio import AsyncIOMotorDatabase


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    # Idempotency claim on inbound Meta webhook message ids (Celery-written).
    await db["jane_wa_processed_messages"].create_index("message_id", unique=True)
    await db["jane_wa_processed_messages"].create_index("created_at", expireAfterSeconds=86400)

    # Multi-tenant conversation scoping (Celery-written, also read by
    # internal_router.py's FastAPI endpoints).
    await db["jane_wa_conversations"].create_index([("phone_hash", 1), ("brand_id", 1)], unique=True)

    # Idempotency claim on agent-reply idempotency_key (FastAPI-written, via
    # agent_reply_service.send_agent_reply). TTL cleans up old claims the same way
    # jane_wa_processed_messages' does — 24h is generous for a support reply's
    # retry window.
    await db["jane_wa_agent_reply_claims"].create_index("idempotency_key", unique=True)
    await db["jane_wa_agent_reply_claims"].create_index("created_at", expireAfterSeconds=86400)

    # Per-conversation message history query — the dashboard now does a real
    # "load all messages for this conversation" query per open
    # (internal_router.get_conversation_messages), which didn't exist as an access
    # pattern before this feature.
    await db["jane_wa_messages"].create_index([("conversation_id", 1), ("created_at", 1)])
