from contextlib import asynccontextmanager

from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorClient

from app.core.config import settings
from app.routers import internal_router, webhook_router
from app.services.db_indexes import ensure_indexes


@asynccontextmanager
async def lifespan(app: FastAPI):
    # jane_wa_agent_reply_claims and jane_wa_messages' history index are written/
    # read by THIS process (internal_router.py), not by a Celery task — relying
    # solely on celery_app.py's worker-startup hook would leave them uncreated in
    # a deployment where a webhook replica starts without a worker having run
    # first (they're separate containers — see docker-compose.enterprise.yml).
    if settings.MONGODB_URI:
        client = AsyncIOMotorClient(settings.MONGODB_URI)
        try:
            await ensure_indexes(client[settings.MONGODB_DB])
        except Exception as e:
            print(f"[main] failed to ensure indexes: {e}")
        finally:
            client.close()
    yield


app = FastAPI(title="Jane on WhatsApp — reply engine (URI's own number)", lifespan=lifespan)

app.include_router(webhook_router.router)
# All /internal/* endpoints require X-Internal-Service (shared secret) +
# X-Agent-Email/X-Agent-Id — see app/middleware/internal_auth.py. This replaces
# the old unauthenticated POST /internal/conversations/{id}/resolve outright: that
# endpoint was reachable over the public internet with zero auth (nginx.conf
# proxies /internal/ on the same public HTTPS server block as /webhook), so anyone
# who obtained/guessed a conversation_id could silently un-escalate it.
app.include_router(internal_router.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
