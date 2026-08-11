"""Field-level PII protection for customer WhatsApp data — see design doc §11
(Data protection): the customer's phone number is a real person's contact info and
must be hashed at rest, never stored in reversible form. Message bodies must be
encrypted, not stored as plain readable text, but DO need to be readable again when
building reply context (a follow-up question needs the prior exchange) — so phone
numbers use a one-way hash, message bodies use reversible symmetric encryption.

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


def encrypt_body(body: str) -> bytes:
    return _fernet().encrypt(body.encode("utf-8"))


def decrypt_body(token: bytes) -> str:
    return _fernet().decrypt(token).decode("utf-8")
