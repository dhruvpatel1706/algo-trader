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
    reset_cooldowns,
)


@pytest.fixture(autouse=True)
def _isolate_cooldowns():
    """Clean cooldown state at the start AND end of every test so cooldown
    behavior set by one test never leaks into the next."""
    reset_cooldowns()
    yield
    reset_cooldowns()


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


def test_default_chain_is_gemini_family_then_haiku_then_openai():
    """Pinning the order — accidentally reordering this changes cost + latency.

    The Gemini family leads because each model has its OWN free-tier RPD
    bucket (2.5-flash ~20 RPD, 2.0-flash ~1500 RPD, 1.5-flash-8b ~4000
    RPD), so stacking multiple Gemini models multiplies our zero-cost
    capacity. Best quality (2.5) first, deepest quota (8b) last in the
    Gemini family. Anthropic Haiku and OpenAI mini are paid fallbacks
    for cross-vendor resilience.
    """
    providers = [s.provider for s in DEFAULT_CHAIN]
    # Every Gemini entry comes before any Anthropic entry, and Anthropic
    # before OpenAI.
    gemini_idxs = [i for i, p in enumerate(providers) if p == "gemini"]
    anthropic_idxs = [i for i, p in enumerate(providers) if p == "anthropic"]
    openai_idxs = [i for i, p in enumerate(providers) if p == "openai"]
    assert gemini_idxs, "expected Gemini entries leading the chain"
    assert anthropic_idxs, "expected Anthropic entries"
    assert openai_idxs, "expected OpenAI entries"
    assert max(gemini_idxs) < min(anthropic_idxs), (
        "Gemini family must precede Anthropic"
    )
    assert max(anthropic_idxs) < min(openai_idxs), (
        "Anthropic must precede OpenAI"
    )
    # First entry is the highest-quality Gemini we list (2.5-flash).
    assert DEFAULT_CHAIN[0].provider == "gemini"
    assert DEFAULT_CHAIN[0].model == "gemini-2.5-flash"
    # Anthropic + OpenAI legs are exactly one each (paid resilience legs).
    assert len(anthropic_idxs) == 1
    assert DEFAULT_CHAIN[anthropic_idxs[0]].model.startswith("claude-haiku")
    assert len(openai_idxs) == 1
    assert DEFAULT_CHAIN[openai_idxs[0]].model.startswith("gpt-")
    # All Gemini entries share the GEMINI_API_KEY env var (single key
    # per AI Studio account covers every model on the free tier).
    for spec in DEFAULT_CHAIN:
        if spec.provider == "gemini":
            assert spec.api_key_env == "GEMINI_API_KEY"
    # Each entry must be uniquely identified by (provider, model) so the
    # per-model cooldown registry doesn't collide.
    keys = [(s.provider, s.model) for s in DEFAULT_CHAIN]
    assert len(set(keys)) == len(keys), f"duplicate (provider, model) in chain: {keys}"


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


# ---------------------------------------------------------------------------
# Per-provider cooldown circuit-breaker
# ---------------------------------------------------------------------------


def test_router_marks_provider_cooldown_after_failure(monkeypatch):
    """After a retryable failure, subsequent calls within cooldown skip the
    provider entirely without dispatching. Saves ~1s round-trip per dead
    provider per signal eval; matters at 60 evals/hour across 3 providers."""
    from src.llm.router import _is_cooling_down, _RetryableError

    chain = (_spec("anthropic", "K_A"), _spec("gemini", "K_G"))
    monkeypatch.setenv("K_A", "x")
    monkeypatch.setenv("K_G", "x")

    call_log: list[str] = []

    def fake_dispatch(spec, *, api_key, system, user, max_tokens, temperature):
        call_log.append(spec.provider)
        if spec.provider == "anthropic":
            raise _RetryableError("rate limited")
        return ("gemini ok", 1, 1)

    with patch("src.llm.router._dispatch", fake_dispatch):
        # First call: anthropic fails, gemini succeeds.
        r1 = Router(chain=chain).call(system="s", user="u")
        assert r1.provider == "gemini"
        assert call_log == ["anthropic", "gemini"]
        # Anthropic should be on cooldown now.
        assert _is_cooling_down("anthropic")
        assert not _is_cooling_down("gemini")

        # Second call within cooldown: anthropic must be skipped, gemini wins
        # without dispatching anthropic again.
        r2 = Router(chain=chain).call(system="s", user="u")
        assert r2.provider == "gemini"
        assert call_log == ["anthropic", "gemini", "gemini"], (
            "anthropic should have been skipped on call 2 (cooldown)"
        )


def test_router_clears_cooldown_after_success(monkeypatch):
    """A provider that recovers (e.g. Gemini quota window resets) should be
    marked healthy again on its next successful call so we promote it back
    to primary instead of staying on the fallback indefinitely."""
    from src.llm.router import (
        _is_cooling_down,
        _mark_failed,
    )

    chain = (_spec("anthropic", "K_A"),)
    monkeypatch.setenv("K_A", "x")

    # Pre-mark anthropic as failed (simulating prior failure).
    _mark_failed("anthropic")
    assert _is_cooling_down("anthropic")

    # ...but reset the cooldown so this test isn't blocked by the 5-min wait.
    reset_cooldowns()

    with patch("src.llm.router._dispatch", return_value=("ok", 1, 1)):
        r = Router(chain=chain).call(system="s", user="u")
    assert r.provider == "anthropic"
    # Cooldown should be cleared after success.
    assert not _is_cooling_down("anthropic")


def test_router_raises_when_all_providers_in_cooldown(monkeypatch):
    """If every provider in the chain is on cooldown, the router raises
    immediately — no wasted dispatch attempts. Reasoner sees this as an
    LLMUnavailableError and falls open (multiplier=1.0)."""
    from src.llm.router import _mark_failed

    chain = (_spec("anthropic", "K_A"), _spec("gemini", "K_G"))
    monkeypatch.setenv("K_A", "x")
    monkeypatch.setenv("K_G", "x")

    _mark_failed("anthropic")
    _mark_failed("gemini")

    with patch("src.llm.router._dispatch") as dispatch:
        with pytest.raises(LLMUnavailableError) as exc_info:
            Router(chain=chain).call(system="s", user="u")
    assert dispatch.call_count == 0, "should NOT dispatch when all in cooldown"
    assert "cooldown" in str(exc_info.value).lower()


def test_reset_cooldowns_clears_all_circuits():
    """Operator-grade tool to skip the wait after a payment top-up."""
    from src.llm.router import _is_cooling_down, _mark_failed

    _mark_failed("anthropic")
    _mark_failed("gemini")
    _mark_failed("openai")
    assert _is_cooling_down("anthropic")
    assert _is_cooling_down("gemini")
    assert _is_cooling_down("openai")

    reset_cooldowns()

    assert not _is_cooling_down("anthropic")
    assert not _is_cooling_down("gemini")
    assert not _is_cooling_down("openai")


# ---------------------------------------------------------------------------
# Per-model cooldown isolation — the whole reason the chain has 5 Gemini
# entries. One model exhausting its free-tier RPD must NOT cool the
# others; otherwise we lose the deepest free-tier quota in the chain
# (gemini-1.5-flash-8b at ~4000 RPD/day) the moment the lightest one
# (gemini-2.5-flash at ~20 RPD/day) hits its limit.
# ---------------------------------------------------------------------------


def test_per_model_cooldown_does_not_kill_sibling_model_on_same_provider(monkeypatch):
    """Gemini 2.5-flash hits 20 RPD; gemini-1.5-flash-8b should still serve."""
    from src.llm.router import _RetryableError

    # Two distinct Gemini models on the same API key — different RPD buckets.
    primary = ModelSpec(
        provider="gemini",
        model="gemini-2.5-flash",
        api_key_env="K_G",
        max_retries=0,
    )
    fallback_same_provider = ModelSpec(
        provider="gemini",
        model="gemini-1.5-flash-8b",
        api_key_env="K_G",
        max_retries=0,
    )
    chain = (primary, fallback_same_provider)
    monkeypatch.setenv("K_G", "x")

    call_log: list[tuple[str, str]] = []

    def fake_dispatch(spec, *, api_key, system, user, max_tokens, temperature):
        call_log.append((spec.provider, spec.model))
        if spec.model == "gemini-2.5-flash":
            # Free-tier per-model RPD exhaustion (this is the actual
            # message Gemini returns).
            raise _RetryableError(
                "429 RESOURCE_EXHAUSTED. Quota exceeded for metric: "
                "generativelanguage.googleapis.com/generate_content_free_tier_requests, "
                "limit: 20, model: gemini-2.5-flash"
            )
        return ("8b ok", 1, 1)

    with patch("src.llm.router._dispatch", fake_dispatch):
        # First call: 2.5 fails its RPD, 8b succeeds.
        r1 = Router(chain=chain).call(system="s", user="u")
        assert r1.model == "gemini-1.5-flash-8b"
        assert call_log == [
            ("gemini", "gemini-2.5-flash"),
            ("gemini", "gemini-1.5-flash-8b"),
        ]
        # Second call: 2.5 must be skipped (its model-level cooldown is
        # set), but 8b should be the FIRST dispatch call. Critically,
        # the provider-wide cooldown for gemini must NOT have been set —
        # otherwise we'd skip 8b too even though it has 4000 RPD left.
        call_log.clear()
        r2 = Router(chain=chain).call(system="s", user="u")
        assert r2.model == "gemini-1.5-flash-8b"
        assert call_log == [("gemini", "gemini-1.5-flash-8b")], (
            "2.5-flash should be skipped (cooldown), 8b should serve directly"
        )


def test_account_billing_error_cools_whole_provider(monkeypatch):
    """Anthropic 'credit balance is too low' kills every Anthropic model."""
    from src.llm.router import _is_cooling_down, _ProviderUnavailable

    primary = ModelSpec(
        provider="anthropic",
        model="claude-haiku-4-5",
        api_key_env="K_A",
        max_retries=0,
    )
    sibling = ModelSpec(
        provider="anthropic",
        model="claude-sonnet-4-5",
        api_key_env="K_A",
        max_retries=0,
    )
    fallback = _spec("openai", "K_O")
    chain = (primary, sibling, fallback)
    monkeypatch.setenv("K_A", "x")
    monkeypatch.setenv("K_O", "x")

    call_log: list[tuple[str, str]] = []

    def fake_dispatch(spec, *, api_key, system, user, max_tokens, temperature):
        call_log.append((spec.provider, spec.model))
        if spec.provider == "anthropic":
            raise _ProviderUnavailable("Your credit balance is too low")
        return ("openai ok", 1, 1)

    with patch("src.llm.router._dispatch", fake_dispatch):
        r1 = Router(chain=chain).call(system="s", user="u")
        assert r1.provider == "openai"
        # First call hit haiku (failed → provider-wide cool), then
        # sibling sonnet was SKIPPED because the whole provider is cool,
        # then openai succeeded.
        assert call_log == [
            ("anthropic", "claude-haiku-4-5"),
            ("openai", "openai-test"),
        ]
        # The provider cooldown is set; diagnostic check confirms.
        assert _is_cooling_down("anthropic")


def test_per_model_quota_error_classification():
    """The router must NOT classify Gemini's free-tier RPD message as
    account billing — that misclassification was the bug this commit fixes."""
    from src.llm.router import (
        _is_account_billing_error,
        _is_per_model_quota_error,
        _is_retryable,
    )

    free_tier_msg = (
        "429 RESOURCE_EXHAUSTED. Quota exceeded for metric: "
        "generativelanguage.googleapis.com/generate_content_free_tier_requests, "
        "limit: 20, model: gemini-2.5-flash"
    )
    e = Exception(free_tier_msg)
    assert _is_per_model_quota_error(e), "must detect free-tier RPD exhaustion"
    assert not _is_account_billing_error(e), (
        "free-tier RPD must NOT count as account billing — sibling models still work"
    )
    assert _is_retryable(e), "free-tier RPD must be retryable (chain fall-through)"

    # True account-level billing must still be flagged provider-wide.
    e2 = Exception("Your credit balance is too low; please top up at https://...")
    assert _is_account_billing_error(e2)
    assert not _is_per_model_quota_error(e2)
    assert not _is_retryable(e2)
