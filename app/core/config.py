from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    ENVIRONMENT: str = "development"

    # MongoDB — same shared Atlas cluster as uri-social-backend/whatsapp-agent.
    # This service only ever reads brand_profiles; it never writes to it.
    MONGODB_URI: str = ""
    MONGODB_DB: str = "urisocial"

    # Which brand_profiles document is "URI's own" — the specific brand_id (or
    # user_id, for a personal-brand profile) to read operational_facts from. Left
    # unset by default deliberately rather than guessing a brand_name string; must
    # be set to the real value once URI's own profile has operational_facts filled
    # in via the Playbook (Slice 1).
    URI_BRAND_ID: str = ""

    # Redis — Celery broker/backend
    REDIS_URL: str = "redis://localhost:6379/0"

    # OpenAI — phrasing only, never fact invention (see reply_engine.py)
    OPENAI_API_KEY: str = ""

    # Meta App — reused from uri-social-backend's existing credentials (same App,
    # WhatsApp Business Platform added as a product in Meta's dashboard)
    META_APP_ID: str = ""
    META_APP_SECRET: str = ""
    FACEBOOK_API_VERSION: str = "v21.0"

    # WhatsApp Cloud API — URI's own number for this rehearsal
    WHATSAPP_PHONE_NUMBER_ID: str = ""
    WHATSAPP_ACCESS_TOKEN: str = ""
    WHATSAPP_VERIFY_TOKEN: str = ""

    # Field-level encryption (Fernet) for message bodies at rest
    FIELD_ENCRYPTION_KEY: str = ""

    # Separate Fernet key for the customer's raw phone number specifically —
    # deliberately NOT the same key as FIELD_ENCRYPTION_KEY. Phone numbers were
    # originally stored only as a one-way hash (see crypto_utils.py); replying to
    # an escalated conversation later requires the real number (Meta's send API
    # always needs the literal `to` number), so it's now stored reversibly, once,
    # on the conversation doc — but on its own key so compromising the message-body
    # key never exposes customer identities, and vice versa. Decrypt access is
    # narrowed in code to exactly two call sites (message_processor.py,
    # agent_reply_service.py) — never returned by a list/read API, never logged.
    # Paired with a retention job (app/scripts/purge_stale_phone_numbers.py) that
    # clears this field once a conversation is closed and past its reply window,
    # so it isn't kept decryptable indefinitely.
    PHONE_ENCRYPTION_KEY: str = ""

    # Escalation notification (email — see plan's "Decisions made" for why email,
    # not WhatsApp, is the v1 channel for URI's own rehearsal specifically)
    ESCALATION_EMAIL_TO: str = ""
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = ""

    # uri-social-frontend's own origin, for the escalation email's click-through
    # deep link — jane can't reach uri-social-backend's WEB_APP_URL setting, needs
    # its own copy.
    FRONTEND_BASE_URL: str = ""

    # Shared secret gating /internal/* — presented by uri-social-backend as
    # X-Internal-Service (see app/middleware/internal_auth.py). Mirrors
    # uri-social-backend's own SDK_GATEWAY_INTERNAL_SECRET pattern: left empty by
    # default so an unconfigured deployment fails closed (an empty header value
    # can never match an empty expected secret), rather than trusting a guessable
    # default. /internal/ is reachable over the public internet (confirmed via
    # nginx.conf — it shares the same public HTTPS server block as /webhook, just
    # a different rate-limit zone), so this secret is the real access gate, not a
    # network boundary.
    JANE_WA_INTERNAL_SECRET: str = ""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
