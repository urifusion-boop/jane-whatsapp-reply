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
_cache: Dict[str, Any] = {"facts": None, "brand_name": "", "fetched_at": 0.0}

_client: Optional[AsyncIOMotorClient] = None


def _get_client() -> AsyncIOMotorClient:
    global _client
    if _client is None:
        _client = AsyncIOMotorClient(settings.MONGODB_URI)
    return _client


async def get_uri_operational_facts() -> Dict[str, Any]:
    """URI's own operational_facts, keyed by brand_name — cached briefly since this
    data changes rarely and there's no reason to hit Mongo on every inbound message."""
    now = time.monotonic()
    if _cache["facts"] is not None and (now - _cache["fetched_at"]) < _CACHE_TTL_SECONDS:
        return _cache["facts"]

    if not settings.URI_BRAND_ID:
        raise RuntimeError(
            "URI_BRAND_ID is not set — this must point at the brand_id (or user_id, "
            "for a personal-brand profile) of URI's own brand_profiles document, "
            "filled in via the Playbook (Slice 1), before this service can answer "
            "anything."
        )

    db = _get_client()[settings.MONGODB_DB]
    profile = await db["brand_profiles"].find_one(
        {"$or": [{"brand_id": settings.URI_BRAND_ID}, {"user_id": settings.URI_BRAND_ID}]}
    )
    facts = (profile or {}).get("operational_facts") or {}
    brand_name = (profile or {}).get("brand_name", "URI Social")

    _cache["facts"] = facts
    _cache["brand_name"] = brand_name
    _cache["fetched_at"] = now
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
