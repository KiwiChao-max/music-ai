"""Tests for `app.services.auth_service`.

Covers the two halves of the auth layer:
  * password hashing (bcrypt round-trip + malformed input)
  * JWT signing/verification (access/refresh, type mismatch, expiry)
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest

from app.services import auth_service


# ---- password hashing -----------------------------------------------------
def test_hash_and_verify_round_trip() -> None:
    hashed = auth_service.hash_password("correct horse battery staple")
    assert hashed.startswith("$2")  # bcrypt marker
    assert auth_service.verify_password(
        "correct horse battery staple", hashed
    )
    assert not auth_service.verify_password("wrong", hashed)


def test_hash_password_rejects_empty_string() -> None:
    with pytest.raises(ValueError):
        auth_service.hash_password("")


def test_verify_password_rejects_empty_input() -> None:
    hashed = auth_service.hash_password("hunter22")
    assert auth_service.verify_password("", hashed) is False
    assert auth_service.verify_password("hunter22", "") is False


def test_verify_password_handles_malformed_hash() -> None:
    # A garbage hash must not raise --- it just returns False so login
    # surfaces a clean "invalid credentials" error.
    assert auth_service.verify_password("anything", "not-a-bcrypt-hash") is False
    assert auth_service.verify_password("anything", "$2b$12$" + "x" * 60) is False


def test_same_password_produces_different_hashes() -> None:
    """Bcrypt salts the input so two hashes of the same password don't match."""
    a = auth_service.hash_password("hunter22hunter22")
    b = auth_service.hash_password("hunter22hunter22")
    assert a != b
    assert auth_service.verify_password("hunter22hunter22", a)
    assert auth_service.verify_password("hunter22hunter22", b)


# ---- JWT helpers ----------------------------------------------------------
def test_create_and_decode_access_token() -> None:
    token = auth_service.create_token(
        "42", token_type="access", extra_claims={"email": "[email protected]"}
    )
    payload = auth_service.decode_token(token, expected_type="access")
    assert payload["sub"] == "42"
    assert payload["type"] == "access"
    assert payload["email"] == "[email protected]"
    # iat / exp are present and ordered correctly
    assert payload["exp"] > payload["iat"]


def test_decode_rejects_wrong_token_type() -> None:
    refresh = auth_service.create_token("1", token_type="refresh")
    with pytest.raises(ValueError, match="wrong token type"):
        auth_service.decode_token(refresh, expected_type="access")


def test_decode_rejects_tampered_signature() -> None:
    token = auth_service.create_token("1", token_type="access")
    # Replace a section of the signature with a clearly different blob.
    # Flipping the last char happens to be a valid base64url swap ~1/64
    # of the time, so nuke the whole signature segment.
    head, _, sig = token.rpartition(".")
    tampered = f"{head}.{sig[:-8]}AAAAAAAA"
    assert tampered != token
    with pytest.raises(ValueError):
        auth_service.decode_token(tampered, expected_type="access")


def test_decode_rejects_expired_token() -> None:
    """An expired token must raise so the API can return 401."""
    from jose import jwt as _jose

    from app.config import settings

    expired = _jose.encode(
        {
            "sub": "1",
            "type": "access",
            "iat": int(time.time()) - 7200,
            "exp": int(time.time()) - 3600,
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    with pytest.raises(ValueError):
        auth_service.decode_token(expired, expected_type="access")


def test_issue_token_pair_returns_both_tokens() -> None:
    pair = auth_service.issue_token_pair(
        7, email="[email protected]", role="user"
    )
    assert pair["token_type"] == "Bearer"
    assert pair["expires_in"] == 60 * 60 * 24
    # Each token must round-trip with the right type.
    access = auth_service.decode_token(pair["access_token"], expected_type="access")
    refresh = auth_service.decode_token(pair["refresh_token"], expected_type="refresh")
    assert access["sub"] == "7"
    assert access["role"] == "user"
    assert refresh["sub"] == "7"
    # Cross-type usage must fail.
    with pytest.raises(ValueError):
        auth_service.decode_token(pair["access_token"], expected_type="refresh")
    with pytest.raises(ValueError):
        auth_service.decode_token(pair["refresh_token"], expected_type="access")
