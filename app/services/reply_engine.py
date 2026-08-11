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
from app.services.brand_facts_reader import get_uri_operational_facts

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


def _find_match(question: str, facts: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """Case-insensitive substring match against catalogue item names, hours, and
    delivery area names — deliberately simple and conservative, not fuzzy/semantic.
    Returns the single best match, or None."""
    q = question.lower()

    for item in facts.get("catalogue") or []:
        name = str(item.get("name", ""))
        if name and name.lower() in q:
            return {"kind": "catalogue", "name": name, "price": item.get("price", ""), "description": item.get("description", ""), "availability": item.get("availability", "")}

    if facts.get("hours") and any(kw in q for kw in ("hour", "open", "close", "time")):
        return {"kind": "hours", "hours": facts["hours"]}

    for area in facts.get("delivery_areas") or []:
        area_name = str(area.get("area", ""))
        if area_name and area_name.lower() in q:
            return {"kind": "delivery", "area": area_name, "fee": area.get("fee", ""), "timeline": area.get("timeline", "")}

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
    else:
        fact_line = f"Delivery to {match['area']}: fee {match.get('fee') or 'not listed'}, timeline {match.get('timeline') or 'not listed'}"

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


async def handle(question: str) -> ReplyResult:
    facts = await get_uri_operational_facts()
    match = _find_match(question, facts)
    if match is None:
        return ReplyResult(matched=False, reply_text="")

    reply_text = _phrase_fact(question, match)
    return ReplyResult(matched=True, reply_text=reply_text, matched_fact=match["kind"])


HOLDING_REPLY = "Let me check that and get back to you shortly!"
