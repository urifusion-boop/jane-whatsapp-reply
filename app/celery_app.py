"""Celery app config — mirrors whatsapp-agent's app/celery_app.py shape (same
serialization, time limits, and at-least-once delivery guarantees), one dedicated
queue for this service."""

from celery import Celery
from celery.signals import worker_process_init

from app.core.config import settings

celery_app = Celery(
    "jane_whatsapp_reply",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    # Without this, `celery -A app.celery_app worker` never imports
    # app.tasks.message_processor, so @celery_app.task never runs and the
    # worker's task registry stays empty — confirmed live: worker startup
    # logs showed an empty [tasks] section, meaning a real enqueued message
    # would have been rejected as "unregistered task" and silently dropped.
    # webhook_router.py's lazy import of the task only registers it in the
    # webhook process (which just needs the task NAME to enqueue), not in
    # the worker process that actually has to execute it.
    include=["app.tasks.message_processor"],
)

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
    """Ensure all shared indexes exist, once per worker process — same discipline
    as whatsapp-agent's own startup hook. See app/services/db_indexes.py — this is
    ALSO called from main.py's FastAPI startup, since some of these indexes back
    collections only the FastAPI process itself writes to."""
    import asyncio

    from motor.motor_asyncio import AsyncIOMotorClient

    from app.services.db_indexes import ensure_indexes

    async def _ensure():
        client = AsyncIOMotorClient(settings.MONGODB_URI)
        db = client[settings.MONGODB_DB]
        await ensure_indexes(db)
        client.close()

    try:
        asyncio.run(_ensure())
    except Exception as e:
        print(f"[celery_app] failed to ensure indexes: {e}")
