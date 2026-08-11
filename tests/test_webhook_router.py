import hashlib
import hmac
import json
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app

client = TestClient(app)


def test_get_webhook_verification_success():
    settings.WHATSAPP_VERIFY_TOKEN = "my-verify-token"
    resp = client.get(
        "/webhook",
        params={"hub.mode": "subscribe", "hub.verify_token": "my-verify-token", "hub.challenge": "12345"},
    )
    assert resp.status_code == 200
    assert resp.text == "12345"


def test_get_webhook_verification_wrong_token():
    settings.WHATSAPP_VERIFY_TOKEN = "my-verify-token"
    resp = client.get(
        "/webhook",
        params={"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "12345"},
    )
    assert resp.status_code == 403


def test_post_webhook_enqueues_and_acks_fast():
    settings.META_APP_SECRET = "test_secret"
    body = json.dumps({"entry": []}).encode()
    signature = "sha256=" + hmac.new(b"test_secret", body, hashlib.sha256).hexdigest()

    with patch("app.tasks.message_processor.process_whatsapp_message.delay") as mock_delay:
        resp = client.post("/webhook", content=body, headers={"x-hub-signature-256": signature})

    assert resp.status_code == 200
    mock_delay.assert_called_once()


def test_post_webhook_rejects_bad_signature():
    settings.META_APP_SECRET = "test_secret"
    body = json.dumps({"entry": []}).encode()

    with patch("app.tasks.message_processor.process_whatsapp_message.delay") as mock_delay:
        resp = client.post("/webhook", content=body, headers={"x-hub-signature-256": "sha256=wrong"})

    assert resp.status_code == 403
    mock_delay.assert_not_called()
