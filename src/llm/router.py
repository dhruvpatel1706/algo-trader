"""Multi-provider LLM router with deterministic fallback order.

Goals:
  - One callable for every LLM request in the codebase.
  - If the primary provider fails (rate limit, 5xx, transient network), fall
    through to the next provider rather than crash the agent loop.
  - Skip providers whose API key is unset rather than treating that as an error.
  - Keep imports lazy: a missing SDK on a provider we don't use shouldn't
    break the codepath at all.
  - Stay vendor-agnostic at the call site: the caller passes ``system`` and
    ``user`` strings; the router does the rest.

Provider order is deliberate:
  1. **Anthropic Haiku** — cheapest fast model with the strongest instruction
     following on quant-style classification tasks. Default for all routers.
  2. **Google Gemini Flash** — same latency tier, different vendor. Picks up
     when Anthropic is rate-limited or returns 5xx.
  3. **OpenAI gpt-4.1-mini** — third leg. Different infra again, so a regional
     outage hitting Anthropic + Google together is unlikely to take this out.

We do NOT include long-context / reasoning-tier models (Sonnet, GPT-4o, Gemini
Pro) by default — they're 10-30× the cost and no faster. Caller can override
via `ModelSpec` if they need them for a specific task.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Literal

logger = logging.getLogger(__name__)


Provider = Literal["anthropic", "gemini", "openai"]


@dataclass(frozen=True, slots=True)
class ModelSpec:
    """One row in the fallback chain. Ordered by preference."""

    provider: Provider
    model: str
    api_key_env: str
    # Whether this provider's failure should be retried with backoff before
    # falling through to the next. Keep small — fallback is the strategy.
    # Default 0: the fallback CHAIN is the retry strategy. A retry inside the
    # trade-decision loop blocks the agent for backoff_sec * 2^attempt seconds
    # per signal during a provider outage; with 20 candidate signals that
    # compounds. Callers who genuinely want in-provider retries (e.g. an
    # offline backtest pass) can override per-spec.
    max_retries: int = 0
    backoff_sec: float = 0.5


@dataclass(frozen=True, slots=True)
class LLMResponse:
    """Result of a successful router call."""

    text: str
    provider: Provider
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    elapsed_ms: int = 0


class LLMUnavailableError(RuntimeError):
    """Raised when every configured provider failed or was skipped."""


# Default fallback chain — all "fast & cheap" tier.
#
# Order is Gemini → Anthropic → OpenAI:
#   1. Gemini Flash 2.5 leads because the AI Studio free tier (15 RPM,
#      1M tokens/day) is enough to cover our paper-trading volume at
#      $0/month. Operators without paid LLM credits should still get a
#      working autonomous reasoner.
#   2. Anthropic Haiku 4.5 is the paid fallback — picks up if Gemini
#      hits the rate limit or has a regional outage.
#   3. OpenAI gpt-4.1-mini is the third leg for resilience across
#      vendors. Different infra again so a coincident multi-vendor
#      outage is unlikely to take this leg out too.
DEFAULT_CHAIN: tuple[ModelSpec, ...] = (
    ModelSpec(
        provider="gemini",
        model="gemini-2.5-flash",
        api_key_env="GEMINI_API_KEY",
    ),
    ModelSpec(
        provider="anthropic",
        model="claude-haiku-4-5-20251001",
        api_key_env="ANTHROPIC_API_KEY",
    ),
    ModelSpec(
        provider="openai",
        model="gpt-4.1-mini",
        api_key_env="OPENAI_API_KEY",
    ),
)


# When a provider quota / billing / 5xx fires, skip it for this many seconds
# before trying again. Without this the router was burning ~3s per signal eval
# attempting 3 dead providers (network round-trip per attempt) at 60 evals/hour
# across 3 providers = 180 wasted API attempts/hour. With the cooldown the
# second attempt within a 5-min window short-circuits to an immediate fail-open.
_PROVIDER_COOLDOWN_SEC = 300


@dataclass(slots=True)
class Router:
    chain: tuple[ModelSpec, ...] = field(default_factory=lambda: DEFAULT_CHAIN)
    # Per-provider cooldown: provider -> monotonic timestamp at which it
    # becomes eligible to try again. Mutated only via _mark_failed() and
    # checked via _is_cooling_down(). Module-level (not per-instance) is a
    # deliberate choice — circuits shared across Router instances so the
    # autonomous reasoner and the sentiment classifier don't each maintain
    # their own view of "is Gemini up right now?".

    def call(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 512,
        temperature: float = 0.0,
    ) -> LLMResponse:
        """Invoke the chain in order; return the first successful response.

        ``temperature=0.0`` is the default because every place we use this in
        the bot wants deterministic output (sentiment classification, regime
        labels, strategy-scout grading). Override per-call if needed.

        Per-provider cooldown: a provider that fails (any reason) is skipped
        for ``_PROVIDER_COOLDOWN_SEC`` seconds on subsequent calls. Resets
        automatically when the cooldown window elapses. Without this the
        router wastes ~3s per signal eval calling dead providers — when all
        three providers are quota-exhausted (Gemini free tier daily reset,
        Anthropic billing, OpenAI quota) we'd otherwise burn 180 wasted
        round-trips per hour.
        """
        last_error: Exception | None = None
        for spec in self.chain:
            api_key = os.environ.get(spec.api_key_env, "")
            if not api_key:
                logger.debug("router: skipping %s — %s not set", spec.provider, spec.api_key_env)
                continue
            cooldown_remaining = _cooldown_remaining(spec.provider)
            if cooldown_remaining > 0:
                logger.debug(
                    "router: skipping %s — cooldown for %.0fs more",
                    spec.provider,
                    cooldown_remaining,
                )
                last_error = LLMUnavailableError(
                    f"{spec.provider} in cooldown ({cooldown_remaining:.0f}s remaining)"
                )
                continue
            for attempt in range(spec.max_retries + 1):
                started = time.monotonic()
                try:
                    text, in_tok, out_tok = _dispatch(
                        spec, api_key=api_key, system=system, user=user,
                        max_tokens=max_tokens, temperature=temperature,
                    )
                    # Success — clear any leftover cooldown so the next call
                    # uses this provider as the primary again.
                    _clear_cooldown(spec.provider)
                    return LLMResponse(
                        text=text,
                        provider=spec.provider,
                        model=spec.model,
                        input_tokens=in_tok,
                        output_tokens=out_tok,
                        elapsed_ms=int((time.monotonic() - started) * 1000),
                    )
                except _ProviderUnavailable as e:
                    last_error = e
                    if _is_billing_error(e):
                        logger.critical(
                            "router: %s BILLING/CREDIT ERROR — top up to restore AI judgment: %s",
                            spec.provider,
                            e,
                        )
                    else:
                        logger.warning(
                            "router: %s permanently unavailable: %s", spec.provider, e
                        )
                    _mark_failed(spec.provider)
                    break
                except _RetryableError as e:
                    last_error = e
                    if attempt < spec.max_retries:
                        time.sleep(spec.backoff_sec * (2**attempt))
                        continue
                    logger.warning(
                        "router: %s retries exhausted: %s — falling through", spec.provider, e
                    )
                    _mark_failed(spec.provider)
                    break
        logger.critical(
            "router: ALL providers failed — bot is running fail_open (no AI judgment). "
            "Check billing/quota for anthropic, gemini, openai."
        )
        raise LLMUnavailableError(
            f"all providers failed or skipped (last error: {last_error!r})"
        )


# Module-level cooldown registry. Keyed by provider string; value is the
# monotonic time at which the provider becomes eligible again. Anything
# missing from the dict means "no cooldown active".
_provider_cooldowns: dict[str, float] = {}


def _mark_failed(provider: str) -> None:
    """Put a provider on cooldown so subsequent calls skip it briefly."""
    _provider_cooldowns[provider] = time.monotonic() + _PROVIDER_COOLDOWN_SEC


def _clear_cooldown(provider: str) -> None:
    """Reset cooldown after a successful call — provider is healthy again."""
    _provider_cooldowns.pop(provider, None)


def _cooldown_remaining(provider: str) -> float:
    """Seconds left in the cooldown, or 0 if none / expired."""
    eligible_at = _provider_cooldowns.get(provider)
    if eligible_at is None:
        return 0.0
    remaining = eligible_at - time.monotonic()
    if remaining <= 0:
        # Auto-clear so the next call retries the provider fresh.
        _provider_cooldowns.pop(provider, None)
        return 0.0
    return remaining


def _is_cooling_down(provider: str) -> bool:
    """Public-ish helper for tests + diagnostics."""
    return _cooldown_remaining(provider) > 0


def reset_cooldowns() -> None:
    """Clear ALL provider cooldowns. Operator-grade tool — use after a
    payment top-up or quota reset to skip the wait. Tests also call this
    in fixtures to start each test with a clean slate."""
    _provider_cooldowns.clear()


_default_router: Router | None = None


def default_router() -> Router:
    """Module-level singleton router with the default chain."""
    global _default_router
    if _default_router is None:
        _default_router = Router()
    return _default_router


def call_llm(
    *,
    system: str,
    user: str,
    max_tokens: int = 512,
    temperature: float = 0.0,
) -> LLMResponse:
    """Top-level convenience wrapper around `default_router().call()`."""
    return default_router().call(
        system=system, user=user, max_tokens=max_tokens, temperature=temperature
    )


# -- internal dispatch + error taxonomy ------------------------------------


class _ProviderUnavailable(Exception):
    """SDK missing, bad creds, or any non-retryable provider-specific error."""


class _RetryableError(Exception):
    """Rate-limit / 5xx / transient network — retry then fall through."""


def _dispatch(
    spec: ModelSpec,
    *,
    api_key: str,
    system: str,
    user: str,
    max_tokens: int,
    temperature: float,
) -> tuple[str, int, int]:
    if spec.provider == "anthropic":
        return _call_anthropic(spec.model, api_key, system, user, max_tokens, temperature)
    if spec.provider == "gemini":
        return _call_gemini(spec.model, api_key, system, user, max_tokens, temperature)
    if spec.provider == "openai":
        return _call_openai(spec.model, api_key, system, user, max_tokens, temperature)
    raise _ProviderUnavailable(f"unknown provider: {spec.provider}")


def _safe_int(value: object, default: int = 0) -> int:
    """Coerce to int; return default on None or non-numeric input.

    SDK usage metadata fields are nominally integers but in practice can be
    None (response truncated mid-stream, model didn't report tokens, SDK
    minor-version skew). A bare ``int(None)`` crashes; this helper makes
    the conversion best-effort so a usage-counter mishap never aborts an
    LLM call that already returned text.
    """
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _call_anthropic(
    model: str, api_key: str, system: str, user: str, max_tokens: int, temperature: float
) -> tuple[str, int, int]:
    try:
        import anthropic  # type: ignore[import-not-found]
    except ImportError as e:
        raise _ProviderUnavailable(f"anthropic SDK not installed: {e}") from e
    try:
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(
            block.text for block in msg.content if getattr(block, "type", "") == "text"
        )
        usage = getattr(msg, "usage", None)
        in_tok = _safe_int(getattr(usage, "input_tokens", 0)) if usage else 0
        out_tok = _safe_int(getattr(usage, "output_tokens", 0)) if usage else 0
        return text, in_tok, out_tok
    except Exception as e:
        if _is_retryable(e):
            raise _RetryableError(str(e)) from e
        raise _ProviderUnavailable(str(e)) from e


def _call_gemini(
    model: str, api_key: str, system: str, user: str, max_tokens: int, temperature: float
) -> tuple[str, int, int]:
    try:
        from google import genai  # type: ignore[import-not-found]
    except ImportError as e:
        raise _ProviderUnavailable(f"google-genai SDK not installed: {e}") from e
    try:
        client = genai.Client(api_key=api_key)
        # google-genai accepts system_instruction in config.
        # thinking_budget=0 disables the gemini-2.5-flash internal-thinking
        # phase. Our use case is a structured JSON classifier (autonomous
        # reasoner returning a multiplier + halt vote); we want the full
        # max_output_tokens budget devoted to the answer, not to invisible
        # reasoning that we don't read. With thinking enabled, a small
        # max_tokens budget can be consumed entirely by internal thoughts
        # and produce zero user-facing output.
        resp = client.models.generate_content(
            model=model,
            contents=user,
            config={
                "system_instruction": system,
                "max_output_tokens": max_tokens,
                "temperature": temperature,
                "thinking_config": {"thinking_budget": 0},
            },
        )
        text = getattr(resp, "text", None) or ""
        meta = getattr(resp, "usage_metadata", None)
        in_tok = _safe_int(getattr(meta, "prompt_token_count", 0)) if meta else 0
        out_tok = _safe_int(getattr(meta, "candidates_token_count", 0)) if meta else 0
        return text, in_tok, out_tok
    except Exception as e:
        if _is_retryable(e):
            raise _RetryableError(str(e)) from e
        raise _ProviderUnavailable(str(e)) from e


def _call_openai(
    model: str, api_key: str, system: str, user: str, max_tokens: int, temperature: float
) -> tuple[str, int, int]:
    try:
        from openai import OpenAI  # type: ignore[import-not-found]
    except ImportError as e:
        raise _ProviderUnavailable(f"openai SDK not installed: {e}") from e
    try:
        client = OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        choice = resp.choices[0]
        text = choice.message.content or ""
        usage = getattr(resp, "usage", None)
        in_tok = _safe_int(getattr(usage, "prompt_tokens", 0)) if usage else 0
        out_tok = _safe_int(getattr(usage, "completion_tokens", 0)) if usage else 0
        return text, in_tok, out_tok
    except Exception as e:
        if _is_retryable(e):
            raise _RetryableError(str(e)) from e
        raise _ProviderUnavailable(str(e)) from e


_BILLING_TOKENS = (
    "credit balance",
    "billing",
    "payment",
    "insufficient_quota",
    "quota exceeded",
    "resource_exhausted",
)


def _is_billing_error(exc: Exception) -> bool:
    """True for hard billing/quota failures that won't resolve without manual action."""
    msg = str(exc).lower()
    return any(tok in msg for tok in _BILLING_TOKENS)


def _is_retryable(exc: Exception) -> bool:
    """Classify any provider exception. Retryable: transient rate limit, 5xx, timeout."""
    if _is_billing_error(exc):
        return False
    msg = str(exc).lower()
    cls = type(exc).__name__.lower()
    retryable_tokens = (
        "rate",
        "timeout",
        "timed out",
        "503",
        "502",
        "504",
        "overloaded",
        "unavailable",
        "connection",
        "internalserver",
        "apiconnectionerror",
        "apitimeouterror",
        "ratelimit",
    )
    return any(tok in msg or tok in cls for tok in retryable_tokens)
