"""Cross-service, read-only lookup into uri-social-backend's social_connections
collection on the shared MongoDB Atlas cluster — the multi-tenant analog of
brand_facts_reader.py's cross-service read of brand_profiles. Resolves which client
a given inbound WhatsApp number belongs to, so message_processor.py can route to
the right brand's operational_facts, conversation records, and escalation inbox.

Same discipline as brand_facts_reader.py: the Motor client is NEVER cached at
module level. Each Celery task runs its own asyncio.run() with a fresh event loop,
and a Motor client created in one task's loop raises "Event loop is closed" the
moment a later task (running in a different loop) tries to use it. Only the small
per-phone_number_id result dict is cached (keyed correctly this time — unlike
brand_facts_reader's original bug, this cache was built as a dict-of-dicts from
the start, one entry per phone_number_id, not a single global slot)."""

import time
from typing import Any, Dict, Optional

from motor.motor_asyncio import AsyncIOMotorClient

from app.core.config import settings

_CACHE_TTL_SECONDS = 60
_cache: Dict[str, Dict[str, Any]] = {}  # phone_number_id -> {"doc": ..., "fetched_at": ...}


async def get_client_by_phone_number_id(phone_number_id: str) -> Optional[Dict[str, Any]]:
    """The active whatsapp_business connection for this number, or None if it's
    unrecognized or not currently active (disconnected, mid-onboarding, etc.) —
    callers must treat None as "drop this message", never guess a fallback brand."""
    now = time.monotonic()
    cached = _cache.get(phone_number_id)
    if cached is not None and (now - cached["fetched_at"]) < _CACHE_TTL_SECONDS:
        return cached["doc"]

    client = AsyncIOMotorClient(settings.MONGODB_URI)
    try:
        db = client[settings.MONGODB_DB]
        doc = await db["social_connections"].find_one({
            "platform": "whatsapp_business",
            "phone_number_id": phone_number_id,
            "connection_status": "active",
        })
    finally:
        client.close()

    _cache[phone_number_id] = {"doc": doc, "fetched_at": now}
    return doc
