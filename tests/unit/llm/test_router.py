"""Tests for src.llm.router.

The router is the only LLM call site in the bot. It must:
  - Skip providers whose API key is unset (don't crash, just move on).
  - Fall through to the next provider on retryable errors after retries are exhausted.
  - Return immediately on success without calling later providers.
  - Raise LLMUnavailableError when every provider fails or is skipped.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from src.llm.router import (
    DEFAULT_CHAIN,
    LLMResponse,
    LLMUnavailableError,
    ModelSpec,
    Router,
    _is_retryable,
)


def _spec(provider: str, env: str = "TEST_KEY") -> ModelSpec:
    return ModelSpec(
        provider=provider,  # type: ignore[arg-type]
        model=f"{provider}-test",
        api_key_env=env,
        max_retries=0,
        backoff_sec=0.0,
    )


def test_router_uses_first_provider_when_it_succeeds(monkeypatch):
    chain = (_spec("anthropic", "K_A"), _spec("gemini", "K_G"))
    monkeypatch.setenv("K_A", "anthropic-key")
    monkeypatch.setenv("K_G", "gemini-key")

    with patch("src.llm.router._dispatch") as dispatch:
        dispatch.return_value = ("hello world", 10, 5)
        out = Router(chain=chain).call(system="sys", user="msg")
    assert out.text == "hello world"
    assert out.provider == "anthropic"
    assert out.input_tokens == 10
    assert out.output_tokens == 5
    assert dispatch.call_count == 1


def test_router_skips_providers_with_missing_keys(monkeypatch):
    chain = (_spec("anthropic", "K_A"), _spec("gemini", "K_G"))
    # Only the second provider has a key set.
    monkeypatch.delenv("K_A", raising=False)
    monkeypatch.setenv("K_G", "gemini-key")

    with patch("src.llm.router._dispatch") as dispatch:
        dispatch.return_value = ("from gemini", 0, 0)
        out = Router(chain=chain).call(system="s", user="u")
    assert out.provider == "gemini"
    assert dispatch.call_count == 1


def test_router_falls_through_on_retryable_error(monkeypatch):
    from src.llm.router import _RetryableError

    chain = (_spec("anthropic", "K_A"), _spec("gemini", "K_G"))
    monkeypatch.setenv("K_A", "anthropic-key")
    monkeypatch.setenv("K_G", "gemini-key")

    calls = {"n": 0}

    def fake_dispatch(spec, *, api_key, system, user, max_tokens, temperature):
        calls["n"] += 1
        if spec.provider == "anthropic":
            raise _RetryableError("rate limited")
        return ("fallback worked", 1, 1)

    with patch("src.llm.router._dispatch", fake_dispatch):
        out = Router(chain=chain).call(system="s", user="u")
    assert out.provider == "gemini"
    assert calls["n"] == 2


def test_router_falls_through_on_provider_unavailable(monkeypatch):
    from src.llm.router import _ProviderUnavailable

    chain = (_spec("anthropic", "K_A"), _spec("openai", "K_O"))
    monkeypatch.setenv("K_A", "x")
    monkeypatch.setenv("K_O", "x")

    def fake_dispatch(spec, **_):
        if spec.provider == "anthropic":
            raise _ProviderUnavailable("SDK missing")
        return ("ok", 0, 0)

    with patch("src.llm.router._dispatch", fake_dispatch):
        out = Router(chain=chain).call(system="s", user="u")
    assert out.provider == "openai"


def test_router_raises_when_no_provider_has_key(monkeypatch):
    chain = (_spec("anthropic", "K_A"), _spec("gemini", "K_G"))
    monkeypatch.delenv("K_A", raising=False)
    monkeypatch.delenv("K_G", raising=False)
    with pytest.raises(LLMUnavailableError):
        Router(chain=chain).call(system="s", user="u")


def test_router_raises_when_all_providers_fail(monkeypatch):
    from src.llm.router import _RetryableError

    chain = (_spec("anthropic", "K_A"), _spec("gemini", "K_G"))
    monkeypatch.setenv("K_A", "x")
    monkeypatch.setenv("K_G", "x")
    with patch(
        "src.llm.router._dispatch", side_effect=_RetryableError("503 service unavailable")
    ):
        with pytest.raises(LLMUnavailableError):
            Router(chain=chain).call(system="s", user="u")


def test_default_chain_is_gemini_then_haiku_then_openai():
    """Pinning the order — accidentally reordering this changes cost + latency.

    Gemini leads because the AI Studio free tier covers our paper-trading
    volume at $0/month; Anthropic Haiku and OpenAI mini are paid fallbacks
    for cross-vendor resilience.
    """
    assert [s.provider for s in DEFAULT_CHAIN] == ["gemini", "anthropic", "openai"]
    assert "gemini" in DEFAULT_CHAIN[0].model
    assert DEFAULT_CHAIN[1].model.startswith("claude-haiku")
    assert DEFAULT_CHAIN[2].model.startswith("gpt-")


@pytest.mark.parametrize(
    "msg",
    ["rate limit hit", "503", "Service unavailable", "Connection reset", "timed out"],
)
def test_is_retryable_matches_expected_errors(msg):
    assert _is_retryable(Exception(msg))


@pytest.mark.parametrize(
    "msg",
    ["invalid api key", "model not found", "permission denied", "bad request"],
)
def test_is_retryable_rejects_permanent_errors(msg):
    assert not _is_retryable(Exception(msg))


def test_call_llm_returns_llmresponse_shape():
    """LLMResponse is the public type — pin its shape."""
    r = LLMResponse(text="x", provider="anthropic", model="m")
    assert r.text == "x"
    assert r.provider == "anthropic"
    assert r.model == "m"
    assert r.input_tokens == 0
    assert r.output_tokens == 0
