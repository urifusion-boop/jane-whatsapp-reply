"""Tests the DISPATCH branch added to message_processor.py's _process_message_async
for message-echo events — deliberately does NOT assert Meta's real webhook schema
(extract_message_echo is a stub that always returns None until that's verified, see
its own docstring). Mocks extract_message_echo to return a hand-built fake dict, so
these tests only prove the routing is wired correctly: a non-text-message payload
that extract_inbound_message rejects gets a second chance via extract_message_echo,
and a genuine echo gets handed to _process_message_echo_async. Real schema
verification and end-to-end echo-processing correctness (log_agent_echo never
double-sending, etc.) are covered separately in test_agent_reply_service.py.
"""

import asyncio
from unittest.mock import AsyncMock, patch

from app.tasks import message_processor


def _run(coro):
    return asyncio.run(coro)


def test_neither_message_nor_echo_is_a_silent_noop():
    """A payload that's neither a text message nor a recognized echo (e.g. a
    status update) must not call anything — existing, unchanged behavior."""
    with patch("app.tasks.message_processor.extract_inbound_message", return_value=None), patch(
        "app.tasks.message_processor.extract_message_echo", return_value=None
    ), patch("app.tasks.message_processor._process_message_echo_async", new_callable=AsyncMock) as mock_echo:
        _run(message_processor._process_message_async({"entry": []}))
    mock_echo.assert_not_awaited()


def test_echo_event_dispatches_to_echo_handler():
    fake_echo = {"from": "2348012345678", "text": "Sure, it's ₦40,000/month", "phone_number_id": "test-id"}

    with patch("app.tasks.message_processor.extract_inbound_message", return_value=None), patch(
        "app.tasks.message_processor.extract_message_echo", return_value=fake_echo
    ), patch("app.tasks.message_processor._process_message_echo_async", new_callable=AsyncMock) as mock_echo:
        _run(message_processor._process_message_async({"entry": ["fake-echo-payload"]}))

    mock_echo.assert_awaited_once_with(fake_echo)


def test_text_message_never_reaches_echo_extraction():
    """A normal customer text message must be handled entirely by the existing
    path — extract_message_echo should never even be called."""
    fake_message = {"message_id": "wamid.1", "from": "2348012345678", "text": "hi", "phone_number_id": "test-id"}

    with patch("app.tasks.message_processor.extract_inbound_message", return_value=fake_message), patch(
        "app.tasks.message_processor.extract_message_echo"
    ) as mock_extract_echo, patch(
        "app.tasks.message_processor._resolve_client_context", new_callable=AsyncMock, return_value=None
    ):
        # _resolve_client_context returning None makes the function return early
        # right after the client-context check — enough to prove extract_message_echo
        # was never reached, without needing to mock the full DB pipeline here.
        _run(message_processor._process_message_async({"entry": ["fake-message-payload"]}))

    mock_extract_echo.assert_not_called()
