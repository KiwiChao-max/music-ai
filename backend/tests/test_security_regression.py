"""Security regression tests.

Validates that critical security invariants hold:
  1. Unauthorized static-file access is blocked.
  2. Multi-user privilege escalation on sample libraries is prevented.
  3. Spoofed X-Forwarded-For headers are ignored from untrusted peers.
  4. Refresh-token reuse (replay) is detected and revokes the family.
  5. Broker failure gracefully recovers task state.
"""
from __future__ import annotations

import io
import sys
from unittest.mock import patch, MagicMock

import pytest

# ---------------------------------------------------------------------------
# email-validator may not be installed in every environment.  Provide a
# synthetic stub so the Pydantic EmailStr schema can be generated without
# triggering an ImportError at module-load time.
# ---------------------------------------------------------------------------
if "email_validator" not in sys.modules:
    import types

    _ev = types.ModuleType("email_validator")
    _ev.__version__ = "2.0.0"

    def _validate_email(
        email: str,
        *,
        check_deliverability: bool = True,
        test_environment: bool = False,
        globally_deliverable: bool = True,
        timeout: int = 0,
    ):
        info = MagicMock()
        info.normalized = email.lower()
        info.local_part = email.split("@")[0] if "@" in email else email
        info.domain = email.split("@")[1] if "@" in email else "example.com"
        info.ascii_email = email.lower()
        return info

    _ev.validate_email = _validate_email
    _ev.ValidatedEmail = type("ValidatedEmail", (), {})

    sys.modules["email_validator"] = _ev

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import AudioTask, AudioTaskStatus, RefreshToken, RefreshTokenStatus
from app.main import app
from app.services import auth_service, sample_library_service, task_service
from app.services.auth_service import (
    TokenNotFoundError,
    TokenReuseError,
    TokenRevokedError,
    issue_token_pair,
    rotate_refresh_token,
)
from app.utils.network import get_client_ip, is_trusted_proxy


# ---------------------------------------------------------------------------
# Test client factory
# ---------------------------------------------------------------------------

@pytest.fixture
def client(db_session: Session) -> TestClient:
    """Bind the FastAPI app to the test session via dependency override."""

    def _override_get_db():
        try:
            yield db_session
        finally:
            pass

    from app.db.session import get_db

    app.dependency_overrides[get_db] = _override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


# ---------------------------------------------------------------------------
# 1. Unauthorized static-file access
# ---------------------------------------------------------------------------

class TestUnauthorizedArtifactAccess:
    """Artifact download endpoints must enforce task ownership.

    GET /api/tasks/{id}/files/{scope}/{filename} is the only file-serving
    route.  It must reject:
      - requests without any auth (401 when auth_required=True)
      - requests from a user who does not own the task (403)
    """

    @pytest.fixture(autouse=True)
    def _setup(self, db_session: Session):
        self.db = db_session
        self.user_a = _get_or_create_user(db_session, "a_security@test.com", "user_a")
        self.task_a = _create_task(db_session, self.user_a.id, "track_a.wav")
        db_session.commit()

    def test_anonymous_access_is_blocked(self, client: TestClient, monkeypatch):
        monkeypatch.setattr(settings, "auth_required", True)
        resp = client.get(f"/api/tasks/{self.task_a.id}/files/upload/test.wav")
        assert resp.status_code == 401

    def test_wrong_user_is_blocked(self, client: TestClient):
        user_b = _get_or_create_user(self.db, "b_security@test.com", "user_b")
        self.db.commit()
        token = _access_token(user_b.id)
        resp = client.get(
            f"/api/tasks/{self.task_a.id}/files/upload/test.wav",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403

    def test_path_traversal_is_blocked(self, client: TestClient):
        token = _access_token(self.user_a.id)
        resp = client.get(
            f"/api/tasks/{self.task_a.id}/files/upload/../../../etc/passwd",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 2. Multi-user privilege escalation (sample library)
# ---------------------------------------------------------------------------

class TestSampleLibraryPrivilegeEscalation:
    """A non-admin user must not be able to modify another user's library.

    The `check_resource_owner` guard in `common.py` enforces this for
    activate, deactivate, update, and delete.
    """

    @pytest.fixture(autouse=True)
    def _setup(self, db_session: Session, storage_dir):
        self.db = db_session
        self.user_a = _get_or_create_user(db_session, "lib_a@test.com", "lib_owner_a")
        self.user_b = _get_or_create_user(db_session, "lib_b@test.com", "lib_owner_b")
        db_session.commit()

        # Create a minimal WAV file so the library creation succeeds.
        dummy_wav = _generate_minimal_wav()
        svc = sample_library_service.SampleLibraryService()
        info = svc.create_library(
            db_session,
            name="Drum Kit A",
            files=[("kick.wav", dummy_wav)],
            owner_id=self.user_a.id,
        )
        self.lib_id = info.id
        db_session.commit()

    def _auth(self, user) -> dict:
        return {"Authorization": f"Bearer {_access_token(user.id)}"}

    def test_activate_library_denied(self, client: TestClient):
        resp = client.post(
            f"/api/instruments/libraries/{self.lib_id}/activate",
            headers=self._auth(self.user_b),
        )
        assert resp.status_code == 403

    def test_deactivate_library_denied(self, client: TestClient):
        resp = client.post(
            f"/api/instruments/libraries/{self.lib_id}/deactivate",
            headers=self._auth(self.user_b),
        )
        assert resp.status_code == 403

    def test_update_library_denied(self, client: TestClient):
        resp = client.patch(
            f"/api/instruments/libraries/{self.lib_id}",
            json={"name": "Hacked Name"},
            headers=self._auth(self.user_b),
        )
        assert resp.status_code == 403

    def test_delete_library_denied(self, client: TestClient):
        resp = client.delete(
            f"/api/instruments/libraries/{self.lib_id}",
            headers=self._auth(self.user_b),
        )
        assert resp.status_code == 403

    def test_anonymous_access_is_blocked(self, client: TestClient, monkeypatch):
        monkeypatch.setattr(settings, "auth_required", True)
        resp = client.post(
            f"/api/instruments/libraries/{self.lib_id}/activate",
        )
        # CSRF middleware (403) may fire before auth middleware (401);
        # both are valid rejection outcomes for an unauthenticated request.
        assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# 3. Spoofed X-Forwarded-For
# ---------------------------------------------------------------------------

class TestSpoofedXForwardedFor:
    """X-Forwarded-For must only be honoured when the direct peer is trusted.

    Default trusted proxies: 127.0.0.1, ::1.  Any other IP's X-Forwarded-For
    header must be silently ignored.
    """

    def test_trusted_proxy(self) -> None:
        assert is_trusted_proxy("127.0.0.1", ["127.0.0.1", "::1"]) is True
        assert is_trusted_proxy("::1", ["127.0.0.1", "::1"]) is True

    def test_spoofed_xff_from_untrusted_peer(self) -> None:
        ip = get_client_ip(
            remote_addr="10.0.0.99",
            x_forwarded_for="192.168.1.1",
            trusted_proxies=["127.0.0.1", "::1"],
        )
        assert ip == "10.0.0.99"

    def test_legitimate_xff_from_trusted_proxy(self) -> None:
        ip = get_client_ip(
            remote_addr="127.0.0.1",
            x_forwarded_for="203.0.113.42, 10.0.0.1",
            trusted_proxies=["127.0.0.1", "::1"],
        )
        assert ip == "203.0.113.42"

    def test_empty_xff_from_trusted_peer(self) -> None:
        ip = get_client_ip(
            remote_addr="127.0.0.1",
            x_forwarded_for="",
            trusted_proxies=["127.0.0.1"],
        )
        assert ip == "127.0.0.1"

    def test_cidr_trusted_proxy(self) -> None:
        assert is_trusted_proxy("10.0.0.5", ["10.0.0.0/8"]) is True
        assert is_trusted_proxy("172.16.0.1", ["10.0.0.0/8"]) is False

    def test_invalid_remote_addr(self) -> None:
        assert is_trusted_proxy("", ["127.0.0.1"]) is False
        assert is_trusted_proxy("not_an_ip", ["127.0.0.1"]) is False

    def test_xff_with_multiple_hops(self) -> None:
        ip = get_client_ip(
            remote_addr="127.0.0.1",
            x_forwarded_for="1.2.3.4, 10.0.0.1, 10.0.0.2",
            trusted_proxies=["127.0.0.1"],
        )
        assert ip == "1.2.3.4"


# ---------------------------------------------------------------------------
# 4. Refresh-token replay detection
# ---------------------------------------------------------------------------

class TestRefreshTokenReplay:
    """Reusing a consumed refresh token must revoke the entire family.

    Rotation flow:
      1. issue_token_pair() -> active refresh token
      2. rotate_refresh_token() -> marks old as USED, issues new ACTIVE
      3. rotate_refresh_token() with the OLD token -> TokenReuseError
         and entire family is REVOKED
    """

    @pytest.fixture(autouse=True)
    def _setup(self, db_session: Session):
        self.db = db_session
        self.user = _get_or_create_user(db_session, "replay@test.com", "replay_user")
        db_session.commit()

    def test_rotation_consumes_old_token(self) -> None:
        tokens = issue_token_pair(
            self.db, self.user.id, email=self.user.email, role="user",
        )
        self.db.commit()
        original_refresh = tokens["refresh_token"]

        new_tokens = rotate_refresh_token(self.db, original_refresh)
        self.db.commit()
        assert new_tokens["refresh_token"] != original_refresh

        from app.services.auth_service import _hash_token

        old_hash = _hash_token(original_refresh)
        record = self.db.query(RefreshToken).filter(
            RefreshToken.token_hash == old_hash,
        ).first()
        assert record is not None
        assert record.status == RefreshTokenStatus.USED

    def test_replay_detected_and_family_revoked(self) -> None:
        tokens = issue_token_pair(
            self.db, self.user.id, email=self.user.email, role="user",
        )
        self.db.commit()
        original_refresh = tokens["refresh_token"]

        new_tokens = rotate_refresh_token(self.db, original_refresh)
        self.db.commit()
        new_refresh = new_tokens["refresh_token"]

        with pytest.raises(TokenReuseError) as exc_info:
            rotate_refresh_token(self.db, original_refresh)
        assert "already been used" in str(exc_info.value)
        self.db.commit()

        with pytest.raises(TokenRevokedError):
            rotate_refresh_token(self.db, new_refresh)

    def test_not_found_token_raises(self) -> None:
        with pytest.raises(TokenNotFoundError):
            rotate_refresh_token(self.db, "not.a.real.token.xxx")

    def test_revoked_token_raises(self) -> None:
        tokens = issue_token_pair(
            self.db, self.user.id, email=self.user.email, role="user",
        )
        self.db.commit()
        auth_service.revoke_all_user_tokens(self.db, self.user.id)
        self.db.commit()
        with pytest.raises(TokenRevokedError):
            rotate_refresh_token(self.db, tokens["refresh_token"])

    def test_logout_revokes_all_families(self) -> None:
        tokens1 = issue_token_pair(
            self.db, self.user.id, email=self.user.email, role="user",
        )
        self.db.commit()
        tokens2 = issue_token_pair(
            self.db, self.user.id, email=self.user.email, role="user",
        )
        self.db.commit()

        count = auth_service.revoke_all_user_tokens(self.db, self.user.id)
        self.db.commit()
        assert count >= 2

        with pytest.raises(TokenRevokedError):
            rotate_refresh_token(self.db, tokens1["refresh_token"])
        with pytest.raises(TokenRevokedError):
            rotate_refresh_token(self.db, tokens2["refresh_token"])


# ---------------------------------------------------------------------------
# 5. Broker failure recovery
# ---------------------------------------------------------------------------

class TestBrokerFailureRecovery:
    """When the Celery broker is unreachable, the task state must be rolled back.

    The `start_processing` endpoint in `tasks.py` has a try/except around
    `process_audio_task.delay()`.  If the broker is down:
      - The task is rolled back to UPLOADED (or FAILED if already claimed).
      - A 503 is returned to the client.
      - The task is NOT left in PROCESSING indefinitely.
    """

    @pytest.fixture(autouse=True)
    def _setup(self, db_session: Session):
        self.db = db_session
        self.user = _get_or_create_user(db_session, "broker@test.com", "broker_user")
        self.task = _create_task(db_session, self.user.id, "fail_test.wav")
        db_session.commit()

    def test_broker_unavailable_rolls_back_task(self, client: TestClient):
        token = _access_token(self.user.id)

        with patch(
            "app.api.tasks.process_audio_task.delay",
            side_effect=ConnectionError("broker unreachable"),
        ):
            resp = client.post(
                f"/api/tasks/{self.task.id}/process",
                headers={"Authorization": f"Bearer {token}"},
            )

        assert resp.status_code == 503
        assert "could not be dispatched" in resp.json()["detail"]

        self.db.expire_all()
        task = self.db.query(AudioTask).filter(AudioTask.id == self.task.id).first()
        assert task.status != AudioTaskStatus.PROCESSING
        assert task.status in (AudioTaskStatus.UPLOADED, AudioTaskStatus.FAILED)

    def test_broker_timeout_rolls_back_task(self, client: TestClient):
        token = _access_token(self.user.id)

        with patch(
            "app.api.tasks.process_audio_task.delay",
            side_effect=TimeoutError("broker timeout"),
        ):
            resp = client.post(
                f"/api/tasks/{self.task.id}/process",
                headers={"Authorization": f"Bearer {token}"},
            )

        assert resp.status_code == 503
        self.db.expire_all()
        task = self.db.query(AudioTask).filter(AudioTask.id == self.task.id).first()
        assert task.status != AudioTaskStatus.PROCESSING

    def test_ownership_checked_before_dispatch(self, client: TestClient):
        user_b = _get_or_create_user(self.db, "broker_b@test.com", "broker_b_user")
        self.db.commit()
        token_b = _access_token(user_b.id)

        with patch(
            "app.api.tasks.process_audio_task.delay",
            side_effect=ConnectionError("broker unreachable"),
        ):
            resp = client.post(
                f"/api/tasks/{self.task.id}/process",
                headers={"Authorization": f"Bearer {token_b}"},
            )

        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_or_create_user(db: Session, email: str, username: str):
    from app.services import user_service

    user = user_service.get_user_by_email(db, email)
    if user is not None:
        return user
    user = user_service.create_user(
        db, email=email, username=username, password="secure123",
    )
    db.flush()
    return user


def _create_task(db: Session, user_id: int, filename: str) -> AudioTask:
    task = AudioTask(
        filename=filename,
        status=AudioTaskStatus.UPLOADED,
        user_id=user_id,
    )
    db.add(task)
    db.flush()
    return task


def _access_token(user_id: int) -> str:
    return auth_service.create_token(str(user_id), token_type="access")


def _generate_minimal_wav() -> bytes:
    """Generate a minimal valid WAV file (44 bytes header + silence)."""
    import struct

    sample_rate = 44100
    num_channels = 1
    bits_per_sample = 16
    num_samples = 100
    data_size = num_samples * num_channels * (bits_per_sample // 8)

    buf = io.BytesIO()
    # RIFF header
    buf.write(b"RIFF")
    buf.write(struct.pack("<I", 36 + data_size))
    buf.write(b"WAVE")
    # fmt chunk
    buf.write(b"fmt ")
    buf.write(struct.pack("<I", 16))
    buf.write(struct.pack("<H", 1))  # PCM
    buf.write(struct.pack("<H", num_channels))
    buf.write(struct.pack("<I", sample_rate))
    buf.write(struct.pack("<I", sample_rate * num_channels * bits_per_sample // 8))
    buf.write(struct.pack("<H", num_channels * bits_per_sample // 8))
    buf.write(struct.pack("<H", bits_per_sample))
    # data chunk
    buf.write(b"data")
    buf.write(struct.pack("<I", data_size))
    buf.write(b"\x00" * data_size)

    return buf.getvalue()