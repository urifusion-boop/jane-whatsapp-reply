"""Read-only access to URI's own brand_profiles document on the shared MongoDB
Atlas cluster. Deliberately a LOCAL, small duplicate of the operational_facts shape
that uri-social-backend's BrandProfileService already defines — not a cross-repo
Python import. This matches the established convention in this organization:
whatsapp-agent already keeps its own local copies of brand_profile_service.py,
image_content_service.py, etc. rather than importing uri-social-backend as a package,
since these are genuinely separate deployable services.

This service NEVER writes to brand_profiles — Slice 1 (the Playbook extension in
uri-social-backend) owns writes; this only ever reads.
"""

import time
from typing import Any, Dict, Optional

from motor.motor_asyncio import AsyncIOMotorClient

from app.core.config import settings

_CACHE_TTL_SECONDS = 60
_cache: Dict[str, Dict[str, Any]] = {}  # brand_id -> {"facts": ..., "brand_name": ..., "fetched_at": ...}


async def get_operational_facts_for_brand(brand_id: str) -> Dict[str, Any]:
    """A given brand's operational_facts — cached briefly per brand_id since this
    data changes rarely and there's no reason to hit Mongo on every inbound message.

    On a cache miss, the Motor client is created fresh and closed within this same
    call rather than cached at module level — each Celery task runs its own
    `asyncio.run()` with a brand-new event loop, and a Motor client created in one
    task's loop raises "Event loop is closed" the moment a later task (running in
    a different loop) tries to use it. message_processor.py already follows this
    same create-fresh-per-task pattern for its own Mongo client."""
    now = time.monotonic()
    cached = _cache.get(brand_id)
    if cached is not None and (now - cached["fetched_at"]) < _CACHE_TTL_SECONDS:
        return cached["facts"]

    if not brand_id:
        raise RuntimeError(
            "brand_id is not set — this must be the brand_id (or user_id, for a "
            "personal-brand profile) resolved from the inbound WhatsApp number's "
            "client_registry connection, before this service can answer anything."
        )

    client = AsyncIOMotorClient(settings.MONGODB_URI)
    try:
        db = client[settings.MONGODB_DB]
        profile = await db["brand_profiles"].find_one(
            {"$or": [{"brand_id": brand_id}, {"user_id": brand_id}]}
        )
    finally:
        client.close()

    facts = (profile or {}).get("operational_facts") or {}
    brand_name = (profile or {}).get("brand_name", "URI Social")

    _cache[brand_id] = {"facts": facts, "brand_name": brand_name, "fetched_at": now}
    return facts


def format_operational_facts_for_prompt(operational_facts: Optional[Dict[str, Any]]) -> str:
    """Same formatting as uri-social-backend's
    BrandProfileService.format_operational_facts_for_prompt — kept identical on
    purpose (deliberately duplicated, see module docstring) so a fact reads the same
    way whether it reaches a customer through this service or through captions/blog
    posts/manual WhatsApp replies in the main backend."""
    facts = operational_facts or {}
    parts = []

    if facts.get("hours"):
        parts.append(f"Business hours: {facts['hours']}")

    catalogue = facts.get("catalogue") or []
    if catalogue:
        items = [
            f"{c.get('name', '')} - {c.get('price', '')}".strip(" -")
            for c in catalogue[:8] if c.get("name")
        ]
        if items:
            parts.append(f"Catalogue: {'; '.join(items)}")

    delivery_areas = facts.get("delivery_areas") or []
    if delivery_areas:
        areas = [
            f"{d.get('area', '')} (fee: {d.get('fee', 'n/a')}, timeline: {d.get('timeline', 'n/a')})"
            for d in delivery_areas[:5] if d.get("area")
        ]
        if areas:
            parts.append(f"Delivery areas: {'; '.join(areas)}")

    payment_methods = facts.get("payment_methods") or []
    if payment_methods:
        parts.append(f"Payment methods accepted: {', '.join(payment_methods)}")

    if facts.get("negotiation_policy"):
        parts.append(f"Negotiation policy: {facts['negotiation_policy']}")

    if facts.get("returns_policy"):
        parts.append(f"Returns/guarantees: {facts['returns_policy']}")

    return " | ".join(parts)
