import os

os.environ.setdefault("FIELD_ENCRYPTION_KEY", "")

from cryptography.fernet import Fernet

from app.core.config import settings
from app.services import crypto_utils


def test_hash_phone_deterministic():
    a = crypto_utils.hash_phone("+2348012345678")
    b = crypto_utils.hash_phone("2348012345678")
    assert a == b  # '+' stripped consistently
    assert len(a) == 64  # sha256 hex digest length


def test_hash_phone_one_way_and_distinct():
    h1 = crypto_utils.hash_phone("+2348012345678")
    h2 = crypto_utils.hash_phone("+2348099999999")
    assert h1 != h2
    assert "2348012345678" not in h1


def test_encrypt_decrypt_round_trip():
    settings.FIELD_ENCRYPTION_KEY = Fernet.generate_key().decode()
    original = "How much for the black one?"
    token = crypto_utils.encrypt_body(original)
    assert token != original.encode()
    assert crypto_utils.decrypt_body(token) == original


def test_encrypt_raises_without_key():
    settings.FIELD_ENCRYPTION_KEY = ""
    try:
        crypto_utils.encrypt_body("hello")
        assert False, "expected RuntimeError"
    except RuntimeError:
        pass
