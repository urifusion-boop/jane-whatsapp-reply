import asyncio
import os

os.environ.setdefault("FIELD_ENCRYPTION_KEY", "")
os.environ.setdefault("PHONE_ENCRYPTION_KEY", "")

from unittest.mock import AsyncMock, patch

from cryptography.fernet import Fernet

from app.core.config import settings
from app.services import agent_reply_service
from app.services.conversation_service import ConversationState
from app.services.crypto_utils import encrypt_phone

from .fakes import FakeDatabase

settings.FIELD_ENCRYPTION_KEY = Fernet.generate_key().decode()
settings.PHONE_ENCRYPTION_KEY = Fernet.generate_key().decode()


def _run(coro):
    return asyncio.run(coro)


async def _collect(cursor):
    return [doc async for doc in cursor]


async def _seed_conversation(db, state=ConversationState.ESCALATED.value, phone="+2348012345678"):
    result = await db["jane_wa_conversations"].insert_one(
        {
            "phone_hash": "irrelevant-for-these-tests",
            "phone_encrypted": encrypt_phone(phone),
            "phone_number_id": "test-phone-number-id",
            "brand_id": "test-brand",
            "state": state,
            "escalated_reason": "test",
            "escalated_at": 0,
            "last_message_at": 0,
            "created_at": 0,
        }
    )
    return result.inserted_id


def _no_client_registry():
    return patch("app.services.agent_reply_service.client_registry.get_client_by_phone_number_id", new_callable=AsyncMock, return_value=None)


def _mock_send():
    return patch("app.services.agent_reply_service.send_message", new_callable=AsyncMock)


def test_send_agent_reply_happy_path():
    db = FakeDatabase()
    conversation_id = _run(_seed_conversation(db))

    with _mock_send() as mock_send, _no_client_registry():
        result = _run(
            agent_reply_service.send_agent_reply(
                db, conversation_id, "The Growth plan is ₦40,000/month.", "agent@urisocial.com", "agent-1", "idem-1"
            )
        )

    assert result["status"] == "sent"
    assert result["conversation"]["state"] == ConversationState.JANE_HANDLING.value
    mock_send.assert_awaited_once()
    called_to = mock_send.await_args.args[0]
    assert called_to == "2348012345678"  # decrypted, '+' stripped

    messages = _run(_collect(db["jane_wa_messages"].find({"conversation_id": conversation_id})))
    assert len(messages) == 1
    assert messages[0]["role"] == "agent"
    assert messages[0]["channel"] == "dashboard"
    assert messages[0]["agent_email"] == "agent@urisocial.com"

    claim = _run(db["jane_wa_agent_reply_claims"].find_one({"idempotency_key": "idem-1"}))
    assert claim["sent"] is True


def test_send_agent_reply_not_escalated_raises():
    db = FakeDatabase()
    conversation_id = _run(_seed_conversation(db, state=ConversationState.JANE_HANDLING.value))

    with _mock_send() as mock_send, _no_client_registry():
        try:
            _run(agent_reply_service.send_agent_reply(db, conversation_id, "hi", "a@x.com", "a1", "idem-2"))
            assert False, "expected ConversationNotEscalatedError"
        except agent_reply_service.ConversationNotEscalatedError:
            pass
    mock_send.assert_not_awaited()


def test_send_agent_reply_missing_conversation_raises():
    db = FakeDatabase()
    from bson import ObjectId

    with _mock_send(), _no_client_registry():
        try:
            _run(agent_reply_service.send_agent_reply(db, ObjectId(), "hi", "a@x.com", "a1", "idem-3"))
            assert False, "expected ConversationNotFoundError"
        except agent_reply_service.ConversationNotFoundError:
            pass


def test_send_agent_reply_retry_after_full_success_is_noop():
    db = FakeDatabase()
    conversation_id = _run(_seed_conversation(db))

    with _mock_send() as mock_send, _no_client_registry():
        _run(agent_reply_service.send_agent_reply(db, conversation_id, "first send", "a@x.com", "a1", "idem-4"))
        # Retry with the SAME idempotency_key — must be a true no-op, no second send.
        result = _run(agent_reply_service.send_agent_reply(db, conversation_id, "first send", "a@x.com", "a1", "idem-4"))

    assert result["status"] == "already_sent"
    mock_send.assert_awaited_once()  # still only ever called once


def test_send_agent_reply_ambiguous_claim_refuses_silent_retry():
    """Simulates a crash between send_message() succeeding and sent=True being
    set — the claim exists with sent=False. Must NOT auto-retry the send."""
    db = FakeDatabase()
    conversation_id = _run(_seed_conversation(db))
    _run(
        db["jane_wa_agent_reply_claims"].insert_one(
            {"idempotency_key": "idem-5", "conversation_id": conversation_id, "sent": False, "created_at": 0}
        )
    )

    with _mock_send() as mock_send, _no_client_registry():
        try:
            _run(agent_reply_service.send_agent_reply(db, conversation_id, "hi", "a@x.com", "a1", "idem-5"))
            assert False, "expected AgentReplyInProgressError"
        except agent_reply_service.AgentReplyInProgressError:
            pass
    mock_send.assert_not_awaited()  # the whole point — never silently re-send


def test_send_agent_reply_releases_claim_on_send_failure():
    db = FakeDatabase()
    conversation_id = _run(_seed_conversation(db))

    with patch(
        "app.services.agent_reply_service.send_message", new_callable=AsyncMock, side_effect=RuntimeError("Meta API down")
    ), _no_client_registry():
        try:
            _run(agent_reply_service.send_agent_reply(db, conversation_id, "hi", "a@x.com", "a1", "idem-6"))
            assert False, "expected RuntimeError to propagate"
        except RuntimeError:
            pass

    # Claim must be gone — a retry with the SAME key should be able to start clean.
    claim = _run(db["jane_wa_agent_reply_claims"].find_one({"idempotency_key": "idem-6"}))
    assert claim is None


def test_resolve_only_on_escalated_conversation():
    db = FakeDatabase()
    conversation_id = _run(_seed_conversation(db))

    result = _run(agent_reply_service.resolve_only(db, conversation_id, "a@x.com", "a1"))

    assert result["status"] == "resolved"
    assert result["conversation"]["state"] == ConversationState.JANE_HANDLING.value
    assert result["conversation"]["resolved_via"] == "manual"
    assert result["conversation"]["resolved_by"] == "a@x.com"


def test_resolve_only_on_non_escalated_conversation_is_noop_not_error():
    db = FakeDatabase()
    conversation_id = _run(_seed_conversation(db, state=ConversationState.JANE_HANDLING.value))

    result = _run(agent_reply_service.resolve_only(db, conversation_id, "a@x.com", "a1"))

    assert result["status"] == "not_escalated"


def test_resolve_only_missing_conversation_raises():
    from bson import ObjectId

    db = FakeDatabase()
    try:
        _run(agent_reply_service.resolve_only(db, ObjectId(), "a@x.com", "a1"))
        assert False, "expected ConversationNotFoundError"
    except agent_reply_service.ConversationNotFoundError:
        pass


def test_log_agent_echo_never_calls_send_message():
    db = FakeDatabase()
    conversation_id = _run(_seed_conversation(db))

    with _mock_send() as mock_send:
        updated = _run(agent_reply_service.log_agent_echo(db, conversation_id, "Sure, it's ₦40,000/month"))

    mock_send.assert_not_awaited()  # the message already went out over WhatsApp directly
    assert updated["state"] == ConversationState.JANE_HANDLING.value
    assert updated["resolved_via"] == "whatsapp_echo"

    messages = _run(_collect(db["jane_wa_messages"].find({"conversation_id": conversation_id})))
    assert len(messages) == 1
    assert messages[0]["channel"] == "whatsapp_echo"
    assert messages[0]["agent_email"] is None


def test_log_agent_echo_unknown_conversation_is_noop():
    from bson import ObjectId

    db = FakeDatabase()
    result = _run(agent_reply_service.log_agent_echo(db, ObjectId(), "hi"))
    assert result is None
