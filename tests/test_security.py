import json

from sqlalchemy import select

from notice.config import Settings, settings
from notice.crypto import unseal
from notice.db import session_scope
from notice.models import Job, LoginSession, Token
from conftest import signup


def test_csrf_and_origin_protection(client, account):
    csrf = client.headers.pop("x-csrf-token")
    assert client.post("/api/auth/logout").status_code == 403
    client.headers["x-csrf-token"] = csrf
    assert client.post("/api/auth/logout", headers={"origin": "https://attacker.example"}).status_code == 403
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/me").status_code == 401


def test_login_rate_limit_counts_failures(client):
    for _ in range(10):
        response = client.post(
            "/api/auth/login", json={"email": "unknown@example.com", "password": "wrong-password-value"}
        )
        assert response.status_code == 401
    assert (
        client.post(
            "/api/auth/login", json={"email": "unknown@example.com", "password": "wrong-password-value"}
        ).status_code
        == 429
    )


def test_reset_token_single_use_revokes_sessions_and_is_encrypted(client, account):
    response = client.post("/api/auth/recover", json={"email": account["email"]})
    assert response.status_code == 202
    with session_scope() as db:
        job = db.scalar(select(Job).where(Job.kind == "mail"))
        assert "reset=" not in json.dumps(job.payload)
        payload = unseal(job.payload["sealed"])
        raw = payload["body"].split("#reset=")[1]
        token = db.scalar(select(Token))
        assert raw != token.id
    result = client.post("/api/auth/reset", json={"token": raw, "password": "new-strong-password-456"})
    assert result.status_code == 200
    assert client.get("/api/me").status_code == 401
    assert (
        client.post("/api/auth/reset", json={"token": raw, "password": "new-strong-password-456"}).status_code
        == 400
    )
    assert (
        client.post(
            "/api/auth/login", json={"email": account["email"], "password": "new-strong-password-456"}
        ).status_code
        == 200
    )


def test_email_verification_gate(client, monkeypatch):
    monkeypatch.setattr(settings(), "require_email_verification", True)
    user = signup(client)
    assert not user["verified"]
    assert client.get("/api/cases").status_code == 403
    with session_scope() as db:
        job = db.scalar(select(Job).where(Job.kind == "mail"))
        raw = unseal(job.payload["sealed"])["body"].split("#verify=")[1]
    assert client.post("/api/auth/verify", json={"token": raw}).status_code == 200
    assert client.get("/api/cases").status_code == 200


def test_existing_unverified_account_can_use_local_workspace_when_verification_is_disabled(client, monkeypatch):
    monkeypatch.setattr(settings(), "require_email_verification", True)
    signup(client)
    assert client.get("/api/cases").status_code == 403
    monkeypatch.setattr(settings(), "require_email_verification", False)
    profile = client.get("/api/me").json()
    assert profile["verified"] is True
    assert profile["email_verified"] is False
    assert profile["email_verification_required"] is False
    assert client.get("/api/cases").status_code == 200
    monkeypatch.setattr(settings(), "require_email_verification", True)
    assert client.get("/api/cases").status_code == 403


def test_session_revoke_and_no_plaintext_cookie_in_database(client, account):
    raw = client.cookies.get("ns_session")
    with session_scope() as db:
        assert db.get(LoginSession, raw) is None
    session = client.get("/api/auth/sessions").json()[0]
    assert client.delete("/api/auth/sessions/" + session["id"]).status_code == 200
    assert client.get("/api/me").status_code == 401


def test_production_configuration_fails_closed():
    import pytest

    with pytest.raises(ValueError, match="strong secret"):
        Settings(environment="production")


def test_storage_path_traversal_rejected():
    import pytest
    from notice.storage import local_path

    with pytest.raises(ValueError):
        local_path("../../secret")
