"""Retention job for customer phone numbers — see crypto_utils.py's module
docstring on why phone_encrypted exists at all (agent replies need the real
number) and why it shouldn't stay decryptable forever (data-minimization intent
of the original one-way-hash-only design).

Clears phone_encrypted (keeping phone_hash, which is one-way and fine to retain
indefinitely for conversation lookup) once a conversation has been inactive past
the retention window — EXCEPT conversations currently `escalated`, which are
never touched regardless of age, since an escalated conversation is explicitly
still awaiting an agent reply and purging its phone number would make that reply
impossible to ever send.

Note: conversation_service.close() exists but nothing in this codebase currently
calls it — conversations never reach the `closed` state today. Basing this job on
`last_message_at` age instead of `state == closed` avoids depending on that dead
code path; if a real close() trigger gets added later, `closed` conversations
naturally also satisfy the inactivity check and get purged the same way.

Usage (matches scripts/migrate_uri_own_connection.py's invocation convention):
  python3 -m scripts.purge_stale_phone_numbers [--days N] [--dry-run]

Intended to run on a schedule via host crontab (not Celery Beat — this is a single
daily maintenance job, doesn't justify a new always-on deployable component):
  0 3 * * * cd /home/jane-reply/jane-whatsapp-reply && \\
    .venv/bin/python3 -m scripts.purge_stale_phone_numbers >> /var/log/jane-wa-purge.log 2>&1
"""

import argparse
import asyncio
from datetime import datetime, timedelta, timezone

from motor.motor_asyncio import AsyncIOMotorClient

from app.core.config import settings
from app.services.conversation_service import COLLECTION, ConversationState

DEFAULT_RETENTION_DAYS = 30


async def purge(days: int, dry_run: bool) -> int:
    client = AsyncIOMotorClient(settings.MONGODB_URI)
    try:
        db = client[settings.MONGODB_DB]
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).timestamp()

        query = {
            "state": {"$ne": ConversationState.ESCALATED.value},
            "last_message_at": {"$lt": cutoff},
            "phone_encrypted": {"$exists": True},
        }

        if dry_run:
            count = await db[COLLECTION].count_documents(query)
            print(f"[purge_stale_phone_numbers] DRY RUN — would clear phone_encrypted on {count} conversation(s)")
            return count

        result = await db[COLLECTION].update_many(query, {"$unset": {"phone_encrypted": ""}})
        print(f"[purge_stale_phone_numbers] cleared phone_encrypted on {result.modified_count} conversation(s) "
              f"(inactive > {days}d, not currently escalated)")
        return result.modified_count
    finally:
        client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=DEFAULT_RETENTION_DAYS, help="Retention window in days")
    parser.add_argument("--dry-run", action="store_true", help="Report what would be purged without changing anything")
    args = parser.parse_args()
    asyncio.run(purge(args.days, args.dry_run))


if __name__ == "__main__":
    main()
