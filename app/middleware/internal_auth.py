"""
Internal service authentication — gates /internal/* against uri-social-backend.

Mirrors uri-social-backend's own app/middleware/sdk_gateway_auth.py exactly: a
shared secret presented as a header, compared with hmac.compare_digest (constant-
time, avoids leaking secret length/prefix via timing), fails closed if the expected
secret is unconfigured (an empty string never matches, even an empty header value).

/internal/ is reachable over the public internet (confirmed via nginx.conf — it
shares the same public HTTPS server block as /webhook, just a different rate-limit
zone, not a private-network-only route), so this secret is the real access control,
not a defense-in-depth layer on top of network isolation. A static, guessable value
here would let anyone on the internet decrypt customer message history and send
arbitrary WhatsApp messages as URI's business number.

Trusts X-Agent-Email/X-Agent-Id as the acting support agent's identity, taken on
faith from uri-social-backend — the same one-hop trust delegation
SDKGatewayAuth.extract_developer_context already establishes for X-Developer-ID
(backend has already verified the agent's own JWT before forwarding here).
"""

import hmac
from typing import Optional

from fastapi import HTTPException, Request, status

from app.core.config import settings


class InternalServiceAuth:
    @classmethod
    def verify(cls, request: Request) -> dict:
        internal_service = request.headers.get("x-internal-service") or ""
        expected = settings.JANE_WA_INTERNAL_SECRET
        if not expected or not hmac.compare_digest(internal_service, expected):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authorized")

        agent_email = request.headers.get("x-agent-email")
        agent_id = request.headers.get("x-agent-id")
        if not agent_email or not agent_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid internal request: missing agent context",
            )

        return {"agent_email": agent_email, "agent_id": agent_id}


async def get_internal_agent_context(request: Request) -> dict:
    """FastAPI dependency — raises 401 if not a valid internal request from
    uri-social-backend, otherwise returns {"agent_email": ..., "agent_id": ...}."""
    return InternalServiceAuth.verify(request)
