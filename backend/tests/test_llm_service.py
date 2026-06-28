"""Tests for `app.services.llm_service`.

Covers the prompt builder, the mock backend's deterministic output,
and the OpenAI-compatible HTTP backend via a stubbed `httpx.Client`.
"""
from __future__ import annotations

import json
from typing import Any, Mapping
from unittest.mock import MagicMock, patch

import pytest

from app.services import llm_service
from app.services.llm_service import (
    MockLlm,
    OpenAICompatibleLlm,
    build_user_prompt,
    generate_commentary,
)


# ---- prompt builder ------------------------------------------------------
def test_build_user_prompt_handles_empty_analysis() -> None:
    assert build_user_prompt({}) == "(no analysis data)"


def test_build_user_prompt_includes_basic_fields() -> None:
    analysis = {
        "bpm": 120,
        "key": "C",
        "scale": "major",
        "time_signature": "4/4",
        "duration_seconds": 145.0,
        "loudness_db": -8.0,
    }
    prompt = build_user_prompt(analysis, filename="track.wav")
    assert "File: track.wav" in prompt
    assert "bpm: 120" in prompt
    assert "key: C" in prompt
    assert "scale: major" in prompt
    assert "time_signature: 4/4" in prompt
    assert "duration_seconds: 145.0" in prompt


def test_build_user_prompt_truncates_chord_list() -> None:
    analysis = {
        "chords": [
            {"time": 0.0, "label": "C", "confidence": 0.9},
            {"time": 1.0, "label": "Am", "confidence": 0.8},
            {"time": 2.0, "label": "F", "confidence": 0.7},
        ]
    }
    prompt = build_user_prompt(analysis)
    assert "C – Am – F" in prompt


def test_build_user_prompt_accepts_string_chords() -> None:
    analysis = {"chord_progression": "C – G – Am – F"}
    prompt = build_user_prompt(analysis)
    assert "Chord progression: C – G – Am – F" in prompt


def test_build_user_prompt_ranks_top_instruments() -> None:
    analysis = {
        "detected_instruments": [
            ["piano", 0.4],
            ["bass", 0.7],
            ["drums", 0.6],
        ]
    }
    prompt = build_user_prompt(analysis)
    # bass (0.7) should be first
    assert "bass" in prompt
    assert prompt.index("bass") < prompt.index("drums")


# ---- mock backend --------------------------------------------------------
def test_mock_llm_uses_bpm_for_pace() -> None:
    mock = MockLlm()
    out = mock.complete(
        llm_service.SYSTEM_PROMPT,
        "bpm: 70\nkey: C\nscale: major",
    )
    assert "70 BPM" in out
    assert "laid-back" in out


def test_mock_llm_handles_missing_fields() -> None:
    out = MockLlm().complete(llm_service.SYSTEM_PROMPT, "")
    assert "very little signal" in out


def test_generate_commentary_returns_model_name() -> None:
    result = generate_commentary(
        {"bpm": 100, "key": "A", "scale": "minor"},
        filename="song.wav",
    )
    assert result.model == "mock"
    assert "100 BPM" in result.text
    assert "A minor" in result.text


# ---- OpenAI-compatible backend ------------------------------------------
class _FakeResponse:
    def __init__(self, payload: Mapping[str, Any], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self) -> Mapping[str, Any]:
        return self._payload


def test_openai_compatible_llm_posts_and_extracts_content() -> None:
    backend = OpenAICompatibleLlm(
        base_url="https://api.example.com/v1",
        model="test-model",
        api_key="sk-test",
    )
    fake = _FakeResponse(
        {
            "choices": [
                {"message": {"role": "assistant", "content": "a bouncy 4/4 groove."}}
            ]
        }
    )
    with patch("httpx.Client") as client_cls:
        client = MagicMock()
        client.__enter__.return_value = client
        client.post.return_value = fake
        client_cls.return_value = client
        out = backend.complete("sys", "user")
    assert out == "a bouncy 4/4 groove."
    client.post.assert_called_once()
    args, kwargs = client.post.call_args
    assert args[0] == "https://api.example.com/v1/chat/completions"
    body = json.loads(kwargs["content"])
    assert body["model"] == "test-model"
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][1] == {"role": "user", "content": "user"}


def test_openai_compatible_llm_raises_on_unexpected_shape() -> None:
    backend = OpenAICompatibleLlm(
        base_url="https://api.example.com/v1",
        model="test-model",
        api_key="sk-test",
    )
    fake = _FakeResponse({"oops": True})
    with patch("httpx.Client") as client_cls:
        client = MagicMock()
        client.__enter__.return_value = client
        client.post.return_value = fake
        client_cls.return_value = client
        with pytest.raises(RuntimeError, match="unexpected LLM response"):
            backend.complete("sys", "user")


# ---- factory -------------------------------------------------------------
def test_get_backend_returns_mock_when_no_key() -> None:
    from app.config import settings

    with patch.object(settings, "llm_api_key", ""):
        backend = llm_service.get_backend()
    assert isinstance(backend, MockLlm)


def test_get_backend_returns_openai_when_key_set() -> None:
    from app.config import settings

    with patch.object(settings, "llm_api_key", "sk-test"), patch.object(
        settings, "llm_base_url", "https://api.example.com/v1"
    ), patch.object(settings, "llm_model", "gpt-x"):
        backend = llm_service.get_backend()
    assert isinstance(backend, OpenAICompatibleLlm)
    assert backend.model == "gpt-x"
