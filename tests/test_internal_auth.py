from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient

from app.core.config import settings
from app.middleware.internal_auth import get_internal_agent_context

# A tiny standalone app, not app.main.app — isolates this test from routing/DB
# concerns in main.py, tests only the auth dependency itself.
_test_app = FastAPI()


@_test_app.get("/protected")
async def protected(agent: dict = Depends(get_internal_agent_context)):
    return agent


client = TestClient(_test_app)


def test_fails_closed_when_secret_unconfigured():
    settings.JANE_WA_INTERNAL_SECRET = ""
    resp = client.get(
        "/protected",
        headers={"x-internal-service": "", "x-agent-email": "a@x.com", "x-agent-id": "1"},
    )
    assert resp.status_code == 401


def test_rejects_wrong_secret():
    settings.JANE_WA_INTERNAL_SECRET = "the-real-secret"
    resp = client.get(
        "/protected",
        headers={"x-internal-service": "wrong-secret", "x-agent-email": "a@x.com", "x-agent-id": "1"},
    )
    assert resp.status_code == 401


def test_rejects_missing_header_entirely():
    settings.JANE_WA_INTERNAL_SECRET = "the-real-secret"
    resp = client.get("/protected", headers={"x-agent-email": "a@x.com", "x-agent-id": "1"})
    assert resp.status_code == 401


def test_rejects_correct_secret_but_missing_agent_context():
    settings.JANE_WA_INTERNAL_SECRET = "the-real-secret"
    resp = client.get("/protected", headers={"x-internal-service": "the-real-secret"})
    assert resp.status_code == 401


def test_accepts_correct_secret_and_agent_context():
    settings.JANE_WA_INTERNAL_SECRET = "the-real-secret"
    resp = client.get(
        "/protected",
        headers={"x-internal-service": "the-real-secret", "x-agent-email": "agent@urisocial.com", "x-agent-id": "42"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"agent_email": "agent@urisocial.com", "agent_id": "42"}
