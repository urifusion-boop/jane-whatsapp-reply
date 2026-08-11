"""Meta WhatsApp Cloud API — direct integration, not through Twilio or any BSP.

Confirmed via exploration that this genuinely does not exist anywhere in this
organization's codebase yet (uri-social-backend's WhatsApp integration is Twilio;
jane_ads/adapters/meta.py's own comments flag Cloud API as unbuilt "work-split item
2.4"). Not live-verified — no WhatsApp product has been added to the Meta App yet, no
phone number, no access token exist (see plan's "Explicitly not in this slice").
"""

import hashlib
import hmac
import json
from typing import Any, Dict, Optional

import httpx

from app.core.config import settings

GRAPH_BASE = f"https://graph.facebook.com/{settings.FACEBOOK_API_VERSION}"


def verify_meta_signature(raw_body: bytes, signature_header: str, app_secret: str) -> bool:
    """Verify Meta's X-Hub-Signature-256 header. Adapted from the HMAC skeleton in
    uri-social-backend's PaymentService._verify_webhook_signature (SQUAD payments),
    with two deliberate differences: SHA256 not SHA512, and the HMAC is computed over
    the raw request BYTES exactly as received, never a re-serialized JSON string —
    Meta signs the literal bytes it sent, and re-serializing (even with the same data)
    can silently produce a different byte sequence and break verification."""
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    provided = signature_header.removeprefix("sha256=")
    expected = hmac.new(app_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, provided)


async def send_message(to: str, body: str) -> Dict[str, Any]:
    """Send a plain text WhatsApp message via Cloud API. `to` is the customer's raw
    phone number in international format, no '+' — Cloud API's own convention."""
    url = f"{GRAPH_BASE}/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {settings.WHATSAPP_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to.lstrip("+"),
        "type": "text",
        "text": {"body": body, "preview_url": False},
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, headers=headers, content=json.dumps(payload))
        if resp.status_code >= 400:
            raise RuntimeError(f"WhatsApp Cloud API send failed {resp.status_code}: {resp.text}")
        return resp.json()


def extract_inbound_message(payload: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """Meta nests the actual message several levels deep and sends other event types
    (statuses, etc.) through the same webhook — return None for anything that isn't
    an actual inbound text message. Shape per Meta's documented webhook payload:
    entry[0].changes[0].value.messages[0]."""
    try:
        entry = payload.get("entry", [])[0]
        change = entry.get("changes", [])[0]
        value = change.get("value", {})
        messages = value.get("messages")
        if not messages:
            return None
        message = messages[0]
        if message.get("type") != "text":
            return None
        return {
            "message_id": message.get("id", ""),
            "from": message.get("from", ""),
            "text": message.get("text", {}).get("body", ""),
        }
    except (IndexError, AttributeError, TypeError):
        return None
