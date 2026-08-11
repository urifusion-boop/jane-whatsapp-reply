"""Meta WhatsApp Cloud API webhook — mirrors whatsapp-agent's fast-ack shape (verify,
enqueue, return immediately — no DB reads, no OpenAI calls, no sends happen
synchronously in the request path) adapted for Meta's JSON payload and
X-Hub-Signature-256 scheme instead of Twilio's form-encoded + X-Twilio-Signature.
"""

import json

from fastapi import APIRouter, Query, Request, Response

from app.core.config import settings
from app.services.cloud_api_client import verify_meta_signature

router = APIRouter()


@router.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query(default="", alias="hub.mode"),
    hub_verify_token: str = Query(default="", alias="hub.verify_token"),
    hub_challenge: str = Query(default="", alias="hub.challenge"),
):
    """Meta's one-time handshake when the webhook URL is registered in their
    dashboard — echo back hub.challenge only if the verify token matches."""
    if hub_mode == "subscribe" and hub_verify_token == settings.WHATSAPP_VERIFY_TOKEN:
        return Response(content=hub_challenge, media_type="text/plain")
    return Response(status_code=403)


@router.post("/webhook")
async def receive_webhook(request: Request):
    raw_body = await request.body()
    signature = request.headers.get("x-hub-signature-256", "")

    if not verify_meta_signature(raw_body, signature, settings.META_APP_SECRET):
        return Response(status_code=403)

    payload = json.loads(raw_body)

    from app.tasks.message_processor import process_whatsapp_message

    process_whatsapp_message.delay(payload)
    return Response(status_code=200)
