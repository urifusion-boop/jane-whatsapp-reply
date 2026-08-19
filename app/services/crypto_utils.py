"""Field-level PII protection for customer WhatsApp data — see design doc §11
(Data protection). Message bodies are encrypted (reversible), not stored as plain
readable text — needed to build reply context (a follow-up question needs the prior
exchange). `phone_hash` is a one-way hash of the customer's raw phone number, used
only for conversation lookup by equality — never reversed.

Originally the raw phone number was NEVER stored in any reversible form at all
(hash only) — but replying to an escalated conversation later, from a different
process, requires the literal number (Meta's Cloud API send endpoint always needs
the real `to` number; there is no session/thread-id shortcut that avoids it). So the
raw number is now ALSO stored reversibly, once, on the conversation doc
(`phone_encrypted`) — deliberately under its own Fernet key (PHONE_ENCRYPTION_KEY),
separate from FIELD_ENCRYPTION_KEY, so a compromise of one key never exposes the
other (message content and customer identity are different sensitivity tiers).
Decrypt access to phone_encrypted is narrowed in code to exactly two call sites
(app/tasks/message_processor.py, app/services/agent_reply_service.py) — it must
never be returned by a list/read API or written to logs. A retention job
(app/scripts/purge_stale_phone_numbers.py) clears phone_encrypted once a
conversation is closed and past its reply window, preserving the original
minimize-PII intent within the window replies are actually possible.

No general-purpose PII hashing/encryption utility existed anywhere in the codebase
this service was modeled on (confirmed by exploration before writing this) — this is
genuinely new, small, and reused everywhere sensitive data is touched.
"""

import hashlib

from cryptography.fernet import Fernet

from app.core.config import settings


def hash_phone(phone_number: str) -> str:
    """One-way hash of a raw phone number. Only ever compared for equality — never
    reversed, never logged alongside the raw number."""
    normalized = phone_number.strip().lstrip("+")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _fernet() -> Fernet:
    if not settings.FIELD_ENCRYPTION_KEY:
        raise RuntimeError(
            "FIELD_ENCRYPTION_KEY is not set — message bodies must never be stored "
            "unencrypted. Generate one with: "
            "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    return Fernet(settings.FIELD_ENCRYPTION_KEY.encode("utf-8"))


def _phone_fernet() -> Fernet:
    if not settings.PHONE_ENCRYPTION_KEY:
        raise RuntimeError(
            "PHONE_ENCRYPTION_KEY is not set — customer phone numbers must never be "
            "stored unencrypted, and must use a key separate from "
            "FIELD_ENCRYPTION_KEY. Generate one with: "
            "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    return Fernet(settings.PHONE_ENCRYPTION_KEY.encode("utf-8"))


def encrypt_body(body: str) -> bytes:
    return _fernet().encrypt(body.encode("utf-8"))


def decrypt_body(token: bytes) -> str:
    return _fernet().decrypt(token).decode("utf-8")


def encrypt_phone(phone_number: str) -> bytes:
    """Reversible encryption of a raw phone number, on its own key — see module
    docstring. Only call this when persisting a NEW conversation's phone number
    (once, at creation); never re-encrypt/overwrite on every message."""
    normalized = phone_number.strip().lstrip("+")
    return _phone_fernet().encrypt(normalized.encode("utf-8"))


def decrypt_phone(token: bytes) -> str:
    """Only ever call this from the two sanctioned call sites (see module
    docstring) — never from a list/read API, never log the result."""
    return _phone_fernet().decrypt(token).decode("utf-8")
