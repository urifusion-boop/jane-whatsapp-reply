from bson import ObjectId
from bson.errors import InvalidId
from fastapi import FastAPI, HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

from app.core.config import settings
from app.routers import webhook_router
from app.services import conversation_service

app = FastAPI(title="Jane on WhatsApp — reply engine (URI's own number)")

app.include_router(webhook_router.router)

_client = AsyncIOMotorClient(settings.MONGODB_URI) if settings.MONGODB_URI else None


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/internal/conversations/{conversation_id}/resolve")
async def resolve_conversation(conversation_id: str):
    """The only path back from `escalated` to `jane_handling` — whoever is running
    this rehearsal calls this once they've answered the customer's question
    themselves (matching the design doc's escalation flow). Not authenticated yet —
    this is an internal-only endpoint per docker-compose's nginx routing
    (/internal/ is not exposed publicly the way /webhook is); real auth is a Step 5
    concern once this moves beyond a single-operator rehearsal."""
    if _client is None:
        raise HTTPException(status_code=503, detail="Database not configured")

    try:
        object_id = ObjectId(conversation_id)
    except InvalidId:
        raise HTTPException(status_code=400, detail="Invalid conversation id")

    db = _client[settings.MONGODB_DB]
    updated = await conversation_service.resolve(db, object_id)
    if updated is None:
        raise HTTPException(status_code=404, detail="Conversation not found or not escalated")
    return {"conversation_id": conversation_id, "state": updated["state"]}
