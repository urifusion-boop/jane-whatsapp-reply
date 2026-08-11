import hashlib
import hmac

from app.services.cloud_api_client import verify_meta_signature

SECRET = "test_app_secret"


def _sign(body: bytes, secret: str = SECRET) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def test_valid_signature_passes():
    body = b'{"entry": []}'
    signature = _sign(body)
    assert verify_meta_signature(body, signature, SECRET) is True


def test_tampered_body_fails():
    body = b'{"entry": []}'
    signature = _sign(body)
    tampered = b'{"entry": [1]}'
    assert verify_meta_signature(tampered, signature, SECRET) is False


def test_wrong_secret_fails():
    body = b'{"entry": []}'
    signature = _sign(body, secret="wrong_secret")
    assert verify_meta_signature(body, signature, SECRET) is False


def test_missing_header_fails():
    body = b'{"entry": []}'
    assert verify_meta_signature(body, "", SECRET) is False


def test_missing_prefix_fails():
    body = b'{"entry": []}'
    digest = hmac.new(SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()
    assert verify_meta_signature(body, digest, SECRET) is False  # no "sha256=" prefix
