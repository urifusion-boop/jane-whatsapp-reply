"""The reply engine — deterministic fact matching only in v1, per the design doc's
own Decisions §Q1 recommendation: launch conservative, only auto-answer EXACT matches
against operational_facts (a listed catalogue item, hours, a listed delivery area);
everything even slightly interpretive escalates. AI confidence scoring is a
fast-follow once real escalation-rate data exists — not built here.

Rule 1 from the design doc is the whole point of this file: "If the playbook doesn't
state a price, Jane doesn't estimate one... Missing information is an escalation
trigger, not a gap to fill creatively." The AI call below is used ONLY to phrase an
already-matched fact naturally — it is never asked to answer from anything else.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from openai import OpenAI

from app.core.config import settings
from app.services.brand_facts_reader import get_operational_facts_for_brand

_client: Optional[OpenAI] = None


def _openai_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=settings.OPENAI_API_KEY)
    return _client


@dataclass
class ReplyResult:
    matched: bool
    reply_text: str
    matched_fact: str = ""


_STOPWORDS = {"a", "an", "the", "and", "or", "of", "for", "to", "plan", "package"}


def _significant_words(name: str) -> List[str]:
    """Words worth matching on — drops short/common words like "plan" or "the",
    and drops any word containing a digit (e.g. "7-day"): those read as technical/
    date-like details a customer's casual phrasing rarely repeats verbatim, so
    requiring them would make an otherwise-answerable question (e.g. "do you offer
    a free trial?" against a "7-day free trial" item) escalate for no good reason."""
    return [w for w in name.lower().split() if len(w) >= 3 and w not in _STOPWORDS and not any(c.isdigit() for c in w)]


def _catalogue_item_matches(name: str, q: str) -> bool:
    """True if the full item name appears verbatim, OR every one of its
    significant words appears somewhere in the question (AND, not OR). AND is the
    safety-critical choice: a single shared category noun like "candle" must never
    be enough on its own to match "Lavender candle" against a question about some
    other, non-catalogue "candle" — confirmed as a real regression by
    test_never_invents_a_price_not_in_catalogue when this was OR-based. An item
    whose name reduces to exactly one significant word (e.g. "Pro" once "plan" is
    filtered) still matches on that single word — there's nothing else to combine
    it with, and it's the item's whole identity, not a shared category label."""
    if name.lower() in q:
        return True
    words = _significant_words(name)
    return bool(words) and all(w in q for w in words)


def _find_match(question: str, facts: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """Case-insensitive matching against every operational_facts category —
    deliberately simple and conservative, not fuzzy/semantic: see
    _catalogue_item_matches for the catalogue rule; hours/payment/negotiation/
    returns match on a small fixed keyword set each. Returns the single best
    match, or None — never guesses when nothing lines up."""
    q = question.lower()

    for item in facts.get("catalogue") or []:
        name = str(item.get("name", ""))
        if name and _catalogue_item_matches(name, q):
            return {"kind": "catalogue", "name": name, "price": item.get("price", ""), "description": item.get("description", ""), "availability": item.get("availability", "")}

    if facts.get("hours") and any(kw in q for kw in ("hour", "open", "close", "time")):
        return {"kind": "hours", "hours": facts["hours"]}

    for area in facts.get("delivery_areas") or []:
        area_name = str(area.get("area", ""))
        if area_name and area_name.lower() in q:
            return {"kind": "delivery", "area": area_name, "fee": area.get("fee", ""), "timeline": area.get("timeline", "")}

    if facts.get("payment_methods") and any(
        kw in q for kw in ("payment", "pay ", "how do i pay", "how can i pay", "accept")
    ):
        return {"kind": "payment_methods", "payment_methods": ", ".join(facts["payment_methods"])}

    if facts.get("negotiation_policy") and any(
        kw in q for kw in ("discount", "negotiat", "lower price", "reduce price", "cheaper", "best price")
    ):
        return {"kind": "negotiation_policy", "negotiation_policy": facts["negotiation_policy"]}

    if facts.get("returns_policy") and any(
        kw in q for kw in ("refund", "return", "money back", "cancel")
    ):
        return {"kind": "returns_policy", "returns_policy": facts["returns_policy"]}

    return None


def _phrase_fact(question: str, match: Dict[str, str]) -> str:
    """Ask the model to phrase the already-matched fact naturally — it is given
    ONLY this fact, never the full operational_facts, so it cannot answer from
    anything beyond what was already deterministically matched."""
    if match["kind"] == "catalogue":
        fact_line = f"{match['name']}: price {match.get('price') or 'not listed'}"
        if match.get("description"):
            fact_line += f", {match['description']}"
        if match.get("availability"):
            fact_line += f" ({match['availability']})"
    elif match["kind"] == "hours":
        fact_line = f"Business hours: {match['hours']}"
    elif match["kind"] == "delivery":
        fact_line = f"Delivery to {match['area']}: fee {match.get('fee') or 'not listed'}, timeline {match.get('timeline') or 'not listed'}"
    elif match["kind"] == "payment_methods":
        fact_line = f"Accepted payment methods: {match['payment_methods']}"
    elif match["kind"] == "negotiation_policy":
        fact_line = f"Pricing/negotiation policy: {match['negotiation_policy']}"
    else:
        fact_line = f"Returns/refund policy: {match['returns_policy']}"

    try:
        result = _openai_client().chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.4,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are Jane, a WhatsApp assistant. Answer the customer's question "
                        "using ONLY the single fact given below — never add, estimate, or "
                        "imply anything beyond it. Keep it to 1-2 short sentences, friendly, "
                        "no preamble."
                    ),
                },
                {"role": "user", "content": f"Customer asked: \"{question}\"\n\nFact: {fact_line}"},
            ],
        )
        return result.choices[0].message.content.strip()
    except Exception:
        # If phrasing fails for any reason, fall back to the raw fact rather than
        # silently dropping the reply — never worse than escalating unnecessarily.
        return fact_line


async def handle(question: str, brand_id: str) -> ReplyResult:
    facts = await get_operational_facts_for_brand(brand_id)
    match = _find_match(question, facts)
    if match is None:
        return ReplyResult(matched=False, reply_text="")

    reply_text = _phrase_fact(question, match)
    return ReplyResult(matched=True, reply_text=reply_text, matched_fact=match["kind"])


HOLDING_REPLY = "Let me check that and get back to you shortly!"
