# Jane on WhatsApp — reply engine (URI's own number)

Step 2 of the "Jane on WhatsApp" design doc: a FastAPI + Celery + Redis microservice
that answers customer questions on URI's own WhatsApp number, using the
`operational_facts` a brand's Playbook carries (see `uri-social-backend`'s
`BrandProfileService`). Deterministic, exact-match-only in v1 — never invents a price
or policy; anything it can't answer from real facts gets escalated with a holding
reply sent to the customer.

When Jane escalates, a customer-care agent can close it out via three channels, all
landing back in the exact same WhatsApp chat thread (WhatsApp itself has no concept
of separate chats for the same two phone numbers — it's purely keyed by the number
pair, so it doesn't matter which channel replied):
- **Dashboard** — `uri-social-frontend`'s Escalations tab, authenticated via the
  same JWT/support-role login the rest of the admin area uses.
- **Email** — the escalation notification includes a click-through deep link to the
  dashboard (not raw inbound-email parsing).
- **WhatsApp directly** — via Meta's "Coexistence" mode, a human can reply from the
  actual WhatsApp Business phone app on the same number; Jane detects this via
  Meta's message-echo webhook and auto-resolves. **The exact webhook schema for
  this is unverified** — see `app/services/cloud_api_client.py`'s
  `extract_message_echo()` docstring; it's a stub until confirmed against Meta's
  live docs or a real captured payload.

A separate project from `whatsapp-agent` on purpose — see the design doc's own
Architecture section (§5) for why: a genuinely different numbering model, a genuinely
different conversation (a stranger asking about prices, not an owner creating
content), and a different blast radius (a wrong answer here damages a client's
reputation with their own customer).

## Architecture

Mirrors `whatsapp-agent`'s proven infrastructure shape: nginx load-balancing 3
webhook replicas, a Redis/Celery queue, a separate worker pool, all sharing the same
MongoDB Atlas cluster as `uri-social-backend`. The one real architectural difference:
this service talks to Meta's WhatsApp **Cloud API** directly (JSON, `hub.challenge`
verification, `X-Hub-Signature-256`), not Twilio.

```
Customer's WhatsApp → Meta Cloud API → nginx (3 webhook replicas)
  → fast-ack, verify signature, enqueue to Redis, return 200 immediately
  → Celery worker pool → reads operational_facts from shared MongoDB
  → exact fact match: reply directly · no match: escalate (holding reply + notify)

uri-social-backend (support-team JWT auth) → /internal/* (shared-secret auth,
see app/middleware/internal_auth.py) → agent reply sent via Cloud API, logged,
conversation resolved — same chat thread the customer already sees.
```

**Customer phone numbers**: originally stored only as a one-way hash (never
recoverable) — but replying to an escalated conversation later, from a different
process, requires the real number (Meta's send API always needs it; there's no
session/thread-id shortcut). Now stored reversibly, once, per conversation, on its
own encryption key (`PHONE_ENCRYPTION_KEY`, deliberately separate from
`FIELD_ENCRYPTION_KEY`) — decrypt access is narrowed in code to exactly two call
sites, never returned by any list/read API. `scripts/purge_stale_phone_numbers.py`
(run via host crontab, not Celery Beat) clears it once a conversation's been
inactive past a retention window, so it isn't kept decryptable indefinitely. See
`app/services/crypto_utils.py`'s module docstring for the full reasoning.

## Local development

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in real values — see comments in that file

# Run the API alone (webhook receiver + /health + internal endpoints)
uvicorn app.main:app --reload --port 8080

# Run a worker (separate terminal, needs Redis running locally)
celery -A app.celery_app worker --loglevel=info -Q jane_wa_messages
```

## Tests

```bash
pytest tests/ -v
```

43 tests, all against mocks (OpenAI, Mongo, Meta's HTTP API) — no live Meta
credentials are required to run them.

**Also verified against real data, not just mocks**: URI Social's actual
`operational_facts` (real Starter/Growth/Pro/Agency pricing, Squad as the payment
gateway, the real 7-day/10-credit trial — all cross-checked against
`SubscriptionService.py`/`TrialService.py`/`PaymentService.py` in `uri-social-backend`,
not marketing copy, which was found to disagree with the code in two places and has
since been corrected) is live in the dev MongoDB at `URI_BRAND_ID=
brnd_personal_uri-social-own-profile`. Running `reply_engine.handle()` against it for
9 real customer-style questions ("how much is the growth plan?", "do you offer a free
trial?", "what payment methods do you accept?", "can I get a discount?", "what are
your delivery areas?", "refund policy?", "how much for pro?", "agency plan?", "can I
top up credits?") answered 8 of 9 correctly from real facts and correctly escalated
the one with no matching data (URI has no delivery areas — nothing to invent). This
run is also what surfaced and got a real fix: the matcher originally had no logic at
all for payment_methods/negotiation_policy/returns_policy (only catalogue/hours/
delivery), and a naive single-word matcher would have let two catalogue items
sharing a generic word cross-match each other's price — both are now covered by
tests (`test_similarly_named_items_never_cross_match`,
`test_payment_methods_match_answers_directly`, etc.).

## Deploying

Push to `main` triggers `.github/workflows/deploy-production.yml` — builds
`uriteam/urisvc:jane-whatsapp-reply-prod`, pushes it, then deploys the enterprise
compose stack to the production VM. Requires these GitHub Actions secrets (repo
Settings → Secrets and variables → Actions), none of which exist yet for this repo:

| Secret | Purpose |
|---|---|
| `DOCKER_USERNAME` / `DOCKER_PASSWORD` | Docker Hub push, same account as `whatsapp-agent`'s |
| `JANE_WA_VM_IP` | Production VM address — a new VM, or a new deploy path on an existing one; not yet provisioned |
| `JANE_WA_VM_USERNAME` | SSH user on that VM |
| `JANE_WA_VM_SSH_KEY` | SSH private key for that user |

`.github/workflows/test.yml` runs on every push/PR that isn't `main` — compile check,
full test suite, and `docker compose config` validation. No secrets required.

## Status

Live in production on a Meta **test number** (up on `20.81.41.135`, full enterprise
stack: nginx + 3 webhook replicas + 3 workers + Redis + Flower) — confirmed handling
real messages end-to-end. Moving to the real business number is a pure config swap
(`WHATSAPP_PHONE_NUMBER_ID`, and `WHATSAPP_ACCESS_TOKEN` only if Meta issues a
different one for it) — `send_message()`/`message_processor.py` already resolve the
sending number dynamically per-message, no code change needed.

**Still needed before the real number goes live for real customers:**
- **Real business number registered on the Meta App**, either newly verified or
  migrated from wherever it's currently live (Twilio, another BSP) — a Meta-dashboard
  action, not engineering.
- **WhatsApp Coexistence enabled** on that number, so a human can also reply from
  the WhatsApp Business phone app directly (see the three-channel escalation-reply
  section above) — also a Meta-dashboard action.
- **`PHONE_ENCRYPTION_KEY` and `JANE_WA_INTERNAL_SECRET`** — generate with the
  commands in `.env.example`; must be set on both this service AND (the secret only)
  `uri-social-backend`'s own config, exactly matching.
- **`FRONTEND_BASE_URL`** — so the escalation email's dashboard link resolves.
- **`scripts/purge_stale_phone_numbers.py` on a crontab** — see the script's own
  docstring for the suggested schedule.
- **Meta's message-echo webhook schema confirmed** — `extract_message_echo()` is a
  stub until this happens; the WhatsApp-phone-app reply channel won't auto-resolve
  conversations until it's implemented for real against a verified payload shape.
