import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from app.services import reply_engine

SAMPLE_FACTS = {
    "catalogue": [{"name": "Lavender candle", "price": "₦8,500", "description": "hand-poured"}],
    "delivery_areas": [{"area": "Lekki", "fee": "₦1,500", "timeline": "1-2 days"}],
    "hours": "Mon-Sat 9am-6pm",
    "payment_methods": ["Bank transfer"],
}


def _run(coro):
    return asyncio.run(coro)


def _mock_openai_response(text: str):
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=text))]
    return resp


@patch("app.services.reply_engine.get_uri_operational_facts", new_callable=AsyncMock)
@patch("app.services.reply_engine._openai_client")
def test_catalogue_exact_match_answers_directly(mock_client, mock_facts):
    mock_facts.return_value = SAMPLE_FACTS
    mock_client.return_value.chat.completions.create.return_value = _mock_openai_response(
        "The Lavender candle is ₦8,500."
    )

    result = _run(reply_engine.handle("how much is the lavender candle?"))

    assert result.matched is True
    assert result.matched_fact == "catalogue"
    assert "8,500" in result.reply_text


@patch("app.services.reply_engine.get_uri_operational_facts", new_callable=AsyncMock)
def test_hours_match_answers_directly(mock_facts):
    mock_facts.return_value = SAMPLE_FACTS
    with patch("app.services.reply_engine._openai_client") as mock_client:
        mock_client.return_value.chat.completions.create.return_value = _mock_openai_response(
            "We're open Mon-Sat 9am-6pm."
        )
        result = _run(reply_engine.handle("what time do you open?"))

    assert result.matched is True
    assert result.matched_fact == "hours"


@patch("app.services.reply_engine.get_uri_operational_facts", new_callable=AsyncMock)
def test_no_match_escalates(mock_facts):
    mock_facts.return_value = SAMPLE_FACTS
    result = _run(reply_engine.handle("do you deliver to Ajah?"))

    assert result.matched is False
    assert result.reply_text == ""


@patch("app.services.reply_engine.get_uri_operational_facts", new_callable=AsyncMock)
def test_never_invents_a_price_not_in_catalogue(mock_facts):
    mock_facts.return_value = SAMPLE_FACTS
    # A question about a product NOT in the catalogue must never be answered as if
    # it matched — this is Rule 1 from the design doc, the single worst failure
    # available to this system.
    result = _run(reply_engine.handle("how much is the rose candle?"))

    assert result.matched is False


@patch("app.services.reply_engine.get_uri_operational_facts", new_callable=AsyncMock)
@patch("app.services.reply_engine._openai_client")
def test_phrasing_failure_falls_back_to_raw_fact(mock_client, mock_facts):
    mock_facts.return_value = SAMPLE_FACTS
    mock_client.return_value.chat.completions.create.side_effect = RuntimeError("openai down")

    result = _run(reply_engine.handle("how much is the lavender candle?"))

    assert result.matched is True
    assert "8,500" in result.reply_text  # fell back to the raw fact line, not empty
