# Jane on WhatsApp — reply engine (URI's own number)

Step 2 of the "Jane on WhatsApp" design doc: a FastAPI + Celery + Redis microservice
that answers customer questions on URI's own WhatsApp number, using the
`operational_facts` a brand's Playbook carries (see `uri-social-backend`'s
`BrandProfileService`). Deterministic, exact-match-only in v1 — never invents a price
or policy; anything it can't answer from real facts gets escalated by email with a
holding reply sent to the customer.

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
  → exact fact match: reply directly · no match: escalate (email + holding reply)
```

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

18 tests, all against mocks (OpenAI, Mongo, Meta's HTTP API) — no live Meta
credentials are required to run them. See "Not yet live-tested" below for what
those credentials would additionally unlock.

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

## Not yet live-tested — blocked on Day-1 setup, not engineering

This service's own logic (signature verification, fact matching, conversation state
machine, encryption) is fully built and unit-tested. What it cannot do yet, because
none of the following exist:

- **URI's own `operational_facts`** — nothing has been filled in for URI itself via
  the Playbook yet, so there's nothing real to answer from.
- **`URI_BRAND_ID`** — needs to point at wherever that profile ends up living.
- **WhatsApp product on the Meta App** — `META_APP_ID`/`META_APP_SECRET` are reused
  from `uri-social-backend`'s existing Meta App; the WhatsApp Business Platform
  product itself hasn't been added to it yet.
- **A real phone number, access token, and a chosen verify token.**
- **`FIELD_ENCRYPTION_KEY`** — generate with the command in `.env.example`.
- **SMTP credentials + `ESCALATION_EMAIL_TO`** — for the escalation email.
- **Production VM + the four GitHub secrets above.**

None of these are engineering work — they're dashboard/account setup, one command,
or a config value someone needs to decide. Once they exist, this deploys and runs
exactly as built.
