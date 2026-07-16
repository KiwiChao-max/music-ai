"""LLM service for music commentary.

Two backends:

* **MockLlm** --- runs locally, no network, deterministic-ish output. Used
  in development and in any deployment that hasn't configured a
  provider yet. The mock still produces a *plausible* commentary string
  so the UI is never broken by an empty value.

* **OpenAICompatibleLlm** --- talks to any OpenAI-compatible chat
  completions endpoint (OpenAI, Together, DeepSeek, OpenRouter,
  local llama.cpp, etc.). Only HTTP POST + JSON, no SDK, so the
  dependency surface stays small.

The `get_service()` factory picks the right backend at startup based
on the `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` settings.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = (
    "You are an expert music production assistant. Given a short, "
    "structured summary of a piece of music (BPM, key, time signature, "
    "estimated length, the top detected instruments and a chord "
    "progression), write a 3-5 sentence commentary aimed at a hobbyist "
    "musician who just uploaded the track. Be specific, use plain "
    "language, and call out anything interesting (e.g. unusual key, "
    "syncopation, an unexpected instrument). Don't fabricate details "
    "that aren't in the input. Reply in the same language as the user's "
    "task filename hint; default to English when ambiguous."
)


def build_user_prompt(analysis: Mapping[str, Any], *, filename: str = "") -> str:
    """Turn the analysis JSON into a compact, prompt-friendly string."""
    # Keep it under ~1 kB so even tiny models (3B-7B) can chew it.
    bits: list[str] = []
    if filename:
        bits.append(f"File: {filename}")
    for key in (
        "bpm",
        "key",
        "scale",
        "time_signature",
        "duration_seconds",
        "loudness_db",
    ):
        if key in analysis and analysis[key] is not None:
            bits.append(f"{key}: {analysis[key]}")
    chords = analysis.get("chords") or analysis.get("chord_progression")
    if chords:
        # The chord list is a flat array of {time, label, confidence}
        # in some pipelines; normalise to a short progression string.
        if isinstance(chords, list):
            labels = []
            for c in chords[:16]:
                if isinstance(c, dict):
                    label = c.get("label") or c.get("chord")
                else:
                    label = str(c)
                if label:
                    labels.append(str(label))
            if labels:
                bits.append("Chord progression: " + " - ".join(labels))
        elif isinstance(chords, str):
            bits.append(f"Chord progression: {chords}")
    instruments = analysis.get("detected_instruments") or analysis.get("instruments")
    if isinstance(instruments, list) and instruments:
        top = sorted(
            (i for i in instruments if isinstance(i, (list, tuple)) and len(i) == 2),
            key=lambda pair: -float(pair[1]),
        )[:5]
        if top:
            bits.append(
                "Top instruments: " + ", ".join(f"{n} ({p:.0%})" for n, p in top)
            )
    return "\n".join(bits) if bits else "(no analysis data)"


class LlmBackend(Protocol):
    name: str

    def complete(self, system: str, user: str) -> str: ...


@dataclass
class CommentaryResult:
    text: str
    model: str


# ---- mock backend --------------------------------------------------------
class MockLlm:
    """No-network fallback. Produces a deterministic commentary built
    directly from the analysis fields, so the UI can be exercised
    without any API key."""

    name = "mock"

    def complete(self, system: str, user: str) -> str:  # noqa: ARG002
        # Parse the prompt we built above and reflect it back. Anything
        # missing from `user` is replaced with a "looks like" hedge so
        # the output is always natural.
        lines = [ln.split(": ", 1) for ln in user.splitlines() if ": " in ln]
        info = {k.strip(): v.strip() for k, v in lines}

        bpm = info.get("bpm")
        key = info.get("key")
        scale = info.get("scale")
        ts = info.get("time_signature")
        duration = info.get("duration_seconds")
        instruments = info.get("Top instruments") or info.get("detected_instruments")
        chords = info.get("Chord progression")

        sentences: list[str] = []
        if bpm:
            bpm_val = float(bpm)
            pace = (
                "laid-back" if bpm_val < 80
                else "mid-tempo" if bpm_val < 120
                else "driving" if bpm_val < 150
                else "fast"
            )
            sentences.append(
                f"The track sits at {bpm} BPM, which is a {pace} groove."
            )
        if key and scale:
            sentences.append(
                f"It is in {key} {scale}, a { 'major' if scale.lower().startswith('maj') else 'minor' } tonality."
            )
        if ts and ts not in ("4/4",):
            sentences.append(f"The {ts} time signature gives the rhythm a slightly unusual feel.")
        if instruments:
            sentences.append(
                f"The mix leans on {instruments.lower()}."
            )
        if chords:
            short = chords if len(chords) <= 80 else chords[:77] + "..."
            sentences.append(
                f"Chord-wise it walks through {short}."
            )
        if duration:
            try:
                secs = float(duration)
                mins = int(secs // 60)
                rest = int(secs % 60)
                sentences.append(
                    f"The clip is about {mins}m{rest:02d}s, so it works as a { 'short' if secs < 60 else 'full' } sketch."
                )
            except ValueError:
                pass

        if not sentences:
            return (
                "The track's analysis came back with very little signal, so there's "
                "not much specific to say --- re-run with a cleaner input and we can "
                "be more useful."
            )
        return " ".join(sentences)


# ---- OpenAI-compatible backend -------------------------------------------
class OpenAICompatibleLlm:
    """Thin HTTP client for any OpenAI-compatible chat completions API.

    Set `LLM_BASE_URL` to the provider's root (defaults to OpenAI's
    public endpoint). The request payload is the standard
    `{"model": ..., "messages": [...]}` body, so the same code drives
    OpenAI, Together, DeepSeek, OpenRouter, and any local llama.cpp
    server that speaks the same schema.
    """

    name = "openai-compatible"

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    def complete(self, system: str, user: str) -> str:
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.7,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(url, headers=headers, content=json.dumps(payload))
            resp.raise_for_status()
            data = resp.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(
                f"unexpected LLM response shape: {data!r}"
            ) from exc


# ---- factory -------------------------------------------------------------
def get_backend() -> LlmBackend:
    """Return the configured backend, falling back to the mock when no
    key is set. The factory reads `app.config.settings` so the
    production code can swap backends at runtime by changing env vars
    and restarting the worker.
    """
    if not settings.llm_api_key:
        logger.info("LLM: no LLM_API_KEY set, using mock backend")
        return MockLlm()
    return OpenAICompatibleLlm(
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        timeout=settings.llm_timeout_seconds,
    )


# ---- public surface ------------------------------------------------------
def generate_commentary(
    analysis: Mapping[str, Any],
    *,
    filename: str = "",
    backend: LlmBackend | None = None,
) -> CommentaryResult:
    """End-to-end helper: build the prompt, call the backend, return
    the result with the model name so the worker can persist it on
    the task row for auditing.
    """
    backend = backend or get_backend()
    user = build_user_prompt(analysis, filename=filename)
    text = backend.complete(SYSTEM_PROMPT, user).strip()
    return CommentaryResult(text=text, model=backend.name)


# Convenience for tests.
__all__ = [
    "CommentaryResult",
    "LlmBackend",
    "MockLlm",
    "OpenAICompatibleLlm",
    "SYSTEM_PROMPT",
    "build_user_prompt",
    "generate_commentary",
    "get_backend",
]
