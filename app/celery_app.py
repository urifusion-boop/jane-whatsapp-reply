"""Celery app config — mirrors whatsapp-agent's app/celery_app.py shape (same
serialization, time limits, and at-least-once delivery guarantees), one dedicated
queue for this service."""

from celery import Celery
from celery.signals import worker_process_init

from app.core.config import settings

celery_app = Celery("jane_whatsapp_reply", broker=settings.REDIS_URL, backend=settings.REDIS_URL)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_time_limit=300,
    task_soft_time_limit=240,
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=1000,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    broker_connection_retry_on_startup=True,
    task_routes={
        "app.tasks.message_processor.process_whatsapp_message": {"queue": "jane_wa_messages"},
    },
)


@worker_process_init.connect
def _init_worker_process(**kwargs):
    """Ensure the idempotency-claim collection has a TTL index, once per worker
    process — same discipline as whatsapp-agent's own startup hook."""
    import asyncio

    from motor.motor_asyncio import AsyncIOMotorClient

    async def _ensure_index():
        client = AsyncIOMotorClient(settings.MONGODB_URI)
        db = client[settings.MONGODB_DB]
        # unique on message_id is what actually makes the idempotency claim work —
        # insert_one() only raises DuplicateKeyError because of this index; the TTL
        # index just cleans up old claims so the collection doesn't grow forever.
        await db["jane_wa_processed_messages"].create_index("message_id", unique=True)
        await db["jane_wa_processed_messages"].create_index("created_at", expireAfterSeconds=86400)
        client.close()

    try:
        asyncio.run(_ensure_index())
    except Exception as e:
        print(f"[celery_app] failed to ensure TTL index: {e}")
