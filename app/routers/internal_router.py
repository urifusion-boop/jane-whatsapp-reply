"""Authenticated internal API for uri-social-backend's support-escalations proxy —
see app/middleware/internal_auth.py. Everything here requires X-Internal-Service
(the shared secret) plus X-Agent-Email/X-Agent-Id (the acting support agent's
identity, trusted from backend's own already-verified JWT auth).

Replaces the old, unauthenticated POST /internal/conversations/{id}/resolve in
main.py outright — that endpoint was reachable over the public internet with zero
auth (confirmed via nginx.conf, which proxies /internal/ on the same public HTTPS
server block as /webhook), meaning anyone who obtained or guessed a conversation_id
could silently un-escalate it. Do not reintroduce an unauthenticated variant.
"""

from typing import Any, Dict, List, Optional

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel

from app.core.config import settings
from app.middleware.internal_auth import get_internal_agent_context
from app.services import agent_reply_service
from app.services.crypto_utils import decrypt_body

router = APIRouter(prefix="/internal/conversations", tags=["Internal"])

_client: Optional[AsyncIOMotorClient] = AsyncIOMotorClient(settings.MONGODB_URI) if settings.MONGODB_URI else None


def _get_db():
    if _client is None:
        raise HTTPException(status_code=503, detail="Database not configured")
    return _client[settings.MONGODB_DB]


def _parse_object_id(conversation_id: str) -> ObjectId:
    try:
        return ObjectId(conversation_id)
    except InvalidId:
        raise HTTPException(status_code=400, detail="Invalid conversation id")


class ReplyRequest(BaseModel):
    text: str
    idempotency_key: str
    channel: str = "dashboard"  # "dashboard" | "email"


class ResolveRequest(BaseModel):
    pass  # agent identity comes from the internal-auth headers, not the body


@router.get("/{conversation_id}/messages")
async def get_conversation_messages(
    conversation_id: str,
    _agent: Dict[str, str] = Depends(get_internal_agent_context),
) -> List[Dict[str, Any]]:
    db = _get_db()
    object_id = _parse_object_id(conversation_id)

    messages = []
    async for doc in db["jane_wa_messages"].find({"conversation_id": object_id}).sort("created_at", 1):
        messages.append(
            {
                "role": doc.get("role"),
                "channel": doc.get("channel"),
                "agent_email": doc.get("agent_email"),
                "body": decrypt_body(doc["body_encrypted"]),
                "confidence": doc.get("confidence"),
                "created_at": doc.get("created_at"),
            }
        )
    return messages


@router.post("/{conversation_id}/reply")
async def reply_to_conversation(
    conversation_id: str,
    body: ReplyRequest,
    agent: Dict[str, str] = Depends(get_internal_agent_context),
) -> Dict[str, Any]:
    db = _get_db()
    object_id = _parse_object_id(conversation_id)

    try:
        result = await agent_reply_service.send_agent_reply(
            db,
            object_id,
            body.text,
            agent_email=agent["agent_email"],
            agent_id=agent["agent_id"],
            idempotency_key=body.idempotency_key,
            channel=body.channel,
        )
    except agent_reply_service.ConversationNotFoundError:
        raise HTTPException(status_code=404, detail="Conversation not found")
    except agent_reply_service.ConversationNotEscalatedError:
        raise HTTPException(status_code=409, detail="Conversation is not currently escalated")
    except agent_reply_service.AgentReplyInProgressError as e:
        raise HTTPException(status_code=409, detail=str(e))

    conversation = result["conversation"]
    return {
        "status": result["status"],
        "conversation_id": conversation_id,
        "state": conversation.get("state"),
    }


@router.post("/{conversation_id}/resolve")
async def resolve_conversation(
    conversation_id: str,
    _body: ResolveRequest,
    agent: Dict[str, str] = Depends(get_internal_agent_context),
) -> Dict[str, Any]:
    db = _get_db()
    object_id = _parse_object_id(conversation_id)

    try:
        result = await agent_reply_service.resolve_only(
            db, object_id, agent_email=agent["agent_email"], agent_id=agent["agent_id"]
        )
    except agent_reply_service.ConversationNotFoundError:
        raise HTTPException(status_code=404, detail="Conversation not found")

    conversation = result["conversation"]
    return {
        "status": result["status"],
        "conversation_id": conversation_id,
        "state": conversation.get("state"),
    }
