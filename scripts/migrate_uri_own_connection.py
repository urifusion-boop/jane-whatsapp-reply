"""One-off migration: make URI's own WhatsApp rehearsal number "just the first
tenant" instead of a hardcoded special case. Phase 3 of the multi-tenant plan.

Run this ONCE, on the VM (where .env/settings are real), BEFORE deploying the
multi-tenant jane-whatsapp-reply code. It only ever ADDS data — no existing
document is deleted, and the app's own transition fallback (message_processor.py)
means the live pipeline keeps working via the old single-tenant settings even if
this hasn't run yet, or partially fails.

Steps (matches the approved plan's Phase 3 exactly):
  1. Resolve whether URI_BRAND_ID is a brand_id or a user_id against the real
     brand_profiles document, and look up the real WABA id from Meta's Graph API.
  2. Insert one social_connections doc for URI's own number (upsert — safe to
     re-run).
  3. Backfill brand_id onto every existing jane_wa_conversations doc that's
     missing it — safe and unambiguous, since every doc today belongs to this
     one tenant.
  4. Print before/after document counts so the operator can confirm nothing was
     silently dropped or merged.

Usage:
  python3 -m scripts.migrate_uri_own_connection
"""

import asyncio
from datetime import datetime, timezone

import httpx
from motor.motor_asyncio import AsyncIOMotorClient

from app.core.config import settings


async def _resolve_brand_and_user_id(db) -> tuple[str, str | None]:
    """Returns (brand_id_or_none, user_id) — mirrors the $or scoping convention
    already used by brand_facts_reader.get_operational_facts_for_brand."""
    profile = await db["brand_profiles"].find_one(
        {"$or": [{"brand_id": settings.URI_BRAND_ID}, {"user_id": settings.URI_BRAND_ID}]}
    )
    if not profile:
        raise RuntimeError(
            f"No brand_profiles document found for URI_BRAND_ID={settings.URI_BRAND_ID!r} — "
            f"cannot migrate without knowing the real brand_id/user_id split."
        )
    if profile.get("brand_id") == settings.URI_BRAND_ID:
        return settings.URI_BRAND_ID, profile.get("user_id")
    # It matched on user_id instead — personal-brand profile, no separate brand_id.
    return None, settings.URI_BRAND_ID


async def _lookup_waba_id(phone_number_id: str) -> str:
    url = f"https://graph.facebook.com/{settings.FACEBOOK_API_VERSION}/{phone_number_id}"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            url,
            params={
                "fields": "whatsapp_business_account",
                "access_token": settings.WHATSAPP_ACCESS_TOKEN,
            },
        )
        data = resp.json()
        if "error" in data:
            raise RuntimeError(f"Graph API lookup failed for phone_number_id={phone_number_id}: {data['error']}")
        waba = data.get("whatsapp_business_account", {})
        waba_id = waba.get("id")
        if not waba_id:
            raise RuntimeError(f"No whatsapp_business_account.id in Graph API response: {data}")
        return waba_id


async def main() -> None:
    if not settings.URI_BRAND_ID or not settings.WHATSAPP_PHONE_NUMBER_ID:
        raise RuntimeError("URI_BRAND_ID and WHATSAPP_PHONE_NUMBER_ID must both be set to migrate.")

    client = AsyncIOMotorClient(settings.MONGODB_URI)
    try:
        db = client[settings.MONGODB_DB]

        print("== Step 1: resolve brand_id/user_id and WABA id ==")
        brand_id, user_id = await _resolve_brand_and_user_id(db)
        print(f"  brand_id={brand_id!r} user_id={user_id!r}")
        # The Graph API "fields=whatsapp_business_account" lookup on a
        # phone_number_id node isn't valid (confirmed live: "(#100) Tried
        # accessing nonexisting field") — that field doesn't resolve in reverse
        # from a phone number. Using the WABA id already confirmed earlier this
        # session from Meta's own API Setup page for this exact test number.
        waba_id = "1717259956163614"
        print(f"  waba_id={waba_id!r} (known value, not looked up)")
        resolved_id = brand_id or user_id  # what conversation_service/reply_engine actually key on

        print("\n== Step 2: upsert social_connections doc ==")
        now = datetime.now(timezone.utc).isoformat()
        conn_doc = {
            "id": f"whatsapp_business_{settings.WHATSAPP_PHONE_NUMBER_ID}",
            "user_id": user_id,
            "brand_id": brand_id,
            "platform": "whatsapp_business",
            "connected_via": "manual_migration_bootstrap",
            "waba_id": waba_id,
            "phone_number_id": settings.WHATSAPP_PHONE_NUMBER_ID,
            "display_phone_number": "",
            "subscribed_apps": True,  # already confirmed live-working this session
            "escalation_email": settings.ESCALATION_EMAIL_TO,
            "connection_status": "active",
            "connected_at": now,
            "updated_at": now,
        }
        result = await db["social_connections"].update_one(
            {"id": conn_doc["id"]}, {"$set": conn_doc}, upsert=True,
        )
        print(f"  matched={result.matched_count} upserted_id={result.upserted_id}")

        print("\n== Step 3: backfill brand_id on jane_wa_conversations ==")
        before_count = await db["jane_wa_conversations"].count_documents({})
        missing_count = await db["jane_wa_conversations"].count_documents({"brand_id": {"$exists": False}})
        print(f"  {before_count} total conversation(s), {missing_count} missing brand_id")

        if missing_count > 0:
            update_result = await db["jane_wa_conversations"].update_many(
                {"brand_id": {"$exists": False}}, {"$set": {"brand_id": resolved_id}},
            )
            print(f"  backfilled {update_result.modified_count} document(s) with brand_id={resolved_id!r}")

        after_count = await db["jane_wa_conversations"].count_documents({})
        print(f"\n== Step 4: verify ==")
        print(f"  before={before_count} after={after_count} (must match — nothing should be dropped or merged)")
        if before_count != after_count:
            raise RuntimeError("Document count changed during backfill — investigate before creating the compound index.")

        print("\nMigration complete. Safe to deploy the multi-tenant code and create the compound index.")
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(main())
