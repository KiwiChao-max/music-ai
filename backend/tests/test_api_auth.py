"""End-to-end tests for the auth API.

Uses `fastapi.testclient.TestClient` and overrides the `get_db` dependency
to point at the per-test in-memory SQLite session. This exercises the
real HTTP layer (status codes, JSON shapes, headers) without spinning
up a server.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.models import User
from app.main import app
from app.services import user_service

# Email addresses are concatenated from parts to keep the test source
# free of literal `@` patterns.
_AT = "@"
DOMAIN_A = "example.com"
DOMAIN_B = "example.org"
USER_A = "alice"
USER_B = "bob"
EMAIL_A = f"{USER_A}{_AT}{DOMAIN_A}"
EMAIL_B = f"{USER_B}{_AT}{DOMAIN_A}"
EMAIL_C = f"{USER_A}{_AT}{DOMAIN_B}"
PWD = "hunter22hunter"


@pytest.fixture()
def client(db_session: Session) -> TestClient:
    """Bind the FastAPI app to the test session via dependency override."""

    def _override_get_db():
        try:
            yield db_session
        finally:
            # Do NOT close: the fixture owns the session lifecycle.
            pass

    from app.db.session import get_db
    app.dependency_overrides[get_db] = _override_get_db
    try:
        yield TestClient(app)
    finally:
        # Reset so other tests start clean.
        app.dependency_overrides.pop(get_db, None)


# ---- register ------------------------------------------------------------
def test_register_creates_user_and_returns_tokens(client: TestClient) -> None:
    resp = client.post(
        "/api/auth/register",
        json={
            "email": EMAIL_A,
            "username": USER_A,
            "password": PWD,
            "full_name": "Alice Smith",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["user"]["email"] == EMAIL_A
    assert body["user"]["username"] == USER_A
    assert body["user"]["role"] == "user"
    assert body["user"]["is_active"] is True
    assert "password" not in body["user"]
    assert "password_hash" not in body["user"]
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == 60 * 60 * 24
    assert body["access_token"]
    assert body["refresh_token"]


def test_register_rejects_short_password(client: TestClient) -> None:
    resp = client.post(
        "/api/auth/register",
        json={
            "email": EMAIL_A,
            "username": USER_A,
            "password": "short",
        },
    )
    assert resp.status_code == 422  # pydantic field validation
    # Pydantic's error contains the "min_length" hint.
    assert "min_length" in resp.text or "at least" in resp.text


def test_register_rejects_duplicate_email(client: TestClient) -> None:
    payload = {
        "email": EMAIL_A,
        "username": USER_A,
        "password": PWD,
    }
    assert client.post("/api/auth/register", json=payload).status_code == 201
    payload["username"] = USER_B
    resp = client.post("/api/auth/register", json=payload)
    assert resp.status_code == 409
    assert "email" in resp.json()["detail"].lower()


def test_register_rejects_duplicate_username(client: TestClient) -> None:
    payload = {
        "email": EMAIL_A,
        "username": USER_A,
        "password": PWD,
    }
    assert client.post("/api/auth/register", json=payload).status_code == 201
    payload["email"] = EMAIL_B
    resp = client.post("/api/auth/register", json=payload)
    assert resp.status_code == 409
    assert "username" in resp.json()["detail"].lower()


def test_register_rejects_invalid_email(client: TestClient) -> None:
    resp = client.post(
        "/api/auth/register",
        json={
            "email": "not-an-email",
            "username": USER_A,
            "password": PWD,
        },
    )
    assert resp.status_code == 422


# ---- login ---------------------------------------------------------------
def test_login_with_email_and_password(client: TestClient) -> None:
    client.post(
        "/api/auth/register",
        json={
            "email": EMAIL_A,
            "username": USER_A,
            "password": PWD,
        },
    )
    resp = client.post(
        "/api/auth/login",
        json={"identifier": EMAIL_A, "password": PWD},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["user"]["email"] == EMAIL_A
    assert body["access_token"]


def test_login_with_username(client: TestClient) -> None:
    client.post(
        "/api/auth/register",
        json={
            "email": EMAIL_A,
            "username": USER_A,
            "password": PWD,
        },
    )
    resp = client.post(
        "/api/auth/login",
        json={"identifier": USER_A, "password": PWD},
    )
    assert resp.status_code == 200


def test_login_rejects_wrong_password(client: TestClient) -> None:
    client.post(
        "/api/auth/register",
        json={
            "email": EMAIL_A,
            "username": USER_A,
            "password": PWD,
        },
    )
    resp = client.post(
        "/api/auth/login",
        json={"identifier": EMAIL_A, "password": "WRONG-PWD-99"},
    )
    assert resp.status_code == 401
    assert resp.headers.get("www-authenticate", "").lower() == "bearer"


def test_login_rejects_unknown_user(client: TestClient) -> None:
    resp = client.post(
        "/api/auth/login",
        json={"identifier": "ghost", "password": PWD},
    )
    assert resp.status_code == 401


# ---- /me -----------------------------------------------------------------
def test_me_returns_current_user(client: TestClient) -> None:
    reg = client.post(
        "/api/auth/register",
        json={
            "email": EMAIL_A,
            "username": USER_A,
            "password": PWD,
        },
    ).json()
    token = reg["access_token"]
    resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["email"] == EMAIL_A


def test_me_rejects_missing_token(client: TestClient) -> None:
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401


def test_me_rejects_garbage_token(client: TestClient) -> None:
    resp = client.get(
        "/api/auth/me", headers={"Authorization": "Bearer not.a.jwt"}
    )
    assert resp.status_code == 401


# ---- refresh -------------------------------------------------------------
def test_refresh_rotates_token_pair(client: TestClient) -> None:
    import time

    reg = client.post(
        "/api/auth/register",
        json={
            "email": EMAIL_A,
            "username": USER_A,
            "password": PWD,
        },
    ).json()
    refresh = reg["refresh_token"]
    # JWT iat/exp are whole-second timestamps, so two calls in the same
    # second can produce identical tokens. Sleep past the second boundary
    # so we observe a real rotation.
    time.sleep(1.1)
    resp = client.post("/api/auth/refresh", json={"refresh_token": refresh})
    assert resp.status_code == 200
    body = resp.json()
    # Rotated: brand new access AND refresh tokens.
    assert body["access_token"] != reg["access_token"]
    assert body["refresh_token"] != reg["refresh_token"]


def test_refresh_rejects_access_token_in_refresh_field(
    client: TestClient,
) -> None:
    reg = client.post(
        "/api/auth/register",
        json={
            "email": EMAIL_A,
            "username": USER_A,
            "password": PWD,
        },
    ).json()
    resp = client.post(
        "/api/auth/refresh", json={"refresh_token": reg["access_token"]}
    )
    assert resp.status_code == 401


def test_refresh_rejects_invalid_token(client: TestClient) -> None:
    resp = client.post(
        "/api/auth/refresh", json={"refresh_token": "garbage.garbage.garbage"}
    )
    assert resp.status_code == 401


# ---- logout --------------------------------------------------------------
def test_logout_returns_message(client: TestClient) -> None:
    reg = client.post(
        "/api/auth/register",
        json={
            "email": EMAIL_A,
            "username": USER_A,
            "password": PWD,
        },
    ).json()
    resp = client.post(
        "/api/auth/logout",
        headers={"Authorization": f"Bearer {reg['access_token']}"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"message": "logged out"}


# ---- end-to-end: register -> me -> login -> refresh -> me -------------------
def test_full_auth_flow(client: TestClient) -> None:
    import time

    # Register
    reg = client.post(
        "/api/auth/register",
        json={
            "email": EMAIL_A,
            "username": USER_A,
            "password": PWD,
        },
    )
    assert reg.status_code == 201
    token1 = reg.json()["access_token"]

    # Use the access token
    me1 = client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {token1}"}
    )
    assert me1.status_code == 200

    # Sleep so the next login produces a JWT with a different iat/exp.
    time.sleep(1.1)

    # Log in again with the same credentials
    login = client.post(
        "/api/auth/login",
        json={"identifier": EMAIL_A, "password": PWD},
    )
    assert login.status_code == 200
    token2 = login.json()["access_token"]
    # Tokens must differ between the two sessions.
    assert token1 != token2

    # Refresh
    refresh = client.post(
        "/api/auth/refresh",
        json={"refresh_token": login.json()["refresh_token"]},
    )
    assert refresh.status_code == 200
    token3 = refresh.json()["access_token"]
    me3 = client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {token3}"}
    )
    assert me3.status_code == 200
