"""Regression test for a real bug caught during live infrastructure verification:
the Celery worker started successfully and reported healthy, but its [tasks]
registry was empty — process_whatsapp_message was never registered, because the
Celery app had no `include=` pointing at the module that defines it. A real
enqueued webhook message would have been silently rejected as an "unregistered
task" by every worker replica. webhook_router.py's lazy import only registers the
task in the WEBHOOK process (enough to call .delay(), which just needs the task
name), never in the WORKER process that actually has to execute it.
"""

from app.celery_app import celery_app


def test_message_processor_task_is_registered():
    celery_app.loader.import_default_modules()
    tasks = set(celery_app.tasks.keys())
    assert "app.tasks.message_processor.process_whatsapp_message" in tasks
