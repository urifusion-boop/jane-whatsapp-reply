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

    # Escalation notification (email — see plan's "Decisions made" for why email,
    # not WhatsApp, is the v1 channel for URI's own rehearsal specifically)
    ESCALATION_EMAIL_TO: str = ""
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = ""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
