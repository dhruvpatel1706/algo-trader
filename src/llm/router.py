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


@dataclass(slots=True)
class Router:
    chain: tuple[ModelSpec, ...] = field(default_factory=lambda: DEFAULT_CHAIN)

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
        """
        last_error: Exception | None = None
        for spec in self.chain:
            api_key = os.environ.get(spec.api_key_env, "")
            if not api_key:
                logger.debug("router: skipping %s — %s not set", spec.provider, spec.api_key_env)
                continue
            for attempt in range(spec.max_retries + 1):
                started = time.monotonic()
                try:
                    text, in_tok, out_tok = _dispatch(
                        spec, api_key=api_key, system=system, user=user,
                        max_tokens=max_tokens, temperature=temperature,
                    )
                    return LLMResponse(
                        text=text,
                        provider=spec.provider,
                        model=spec.model,
                        input_tokens=in_tok,
                        output_tokens=out_tok,
                        elapsed_ms=int((time.monotonic() - started) * 1000),
                    )
                except _ProviderUnavailable as e:
                    # Permanent for this provider (no SDK / bad creds).
                    # Don't retry, fall through to the next in chain.
                    last_error = e
                    logger.warning("router: %s permanently unavailable: %s", spec.provider, e)
                    break
                except _RetryableError as e:
                    last_error = e
                    if attempt < spec.max_retries:
                        time.sleep(spec.backoff_sec * (2**attempt))
                        continue
                    logger.warning(
                        "router: %s retries exhausted: %s — falling through", spec.provider, e
                    )
                    break
        raise LLMUnavailableError(
            f"all providers failed or skipped (last error: {last_error!r})"
        )


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
        in_tok = int(getattr(usage, "input_tokens", 0)) if usage else 0
        out_tok = int(getattr(usage, "output_tokens", 0)) if usage else 0
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
        resp = client.models.generate_content(
            model=model,
            contents=user,
            config={
                "system_instruction": system,
                "max_output_tokens": max_tokens,
                "temperature": temperature,
            },
        )
        text = getattr(resp, "text", None) or ""
        meta = getattr(resp, "usage_metadata", None)
        in_tok = int(getattr(meta, "prompt_token_count", 0)) if meta else 0
        out_tok = int(getattr(meta, "candidates_token_count", 0)) if meta else 0
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
        in_tok = int(getattr(usage, "prompt_tokens", 0)) if usage else 0
        out_tok = int(getattr(usage, "completion_tokens", 0)) if usage else 0
        return text, in_tok, out_tok
    except Exception as e:
        if _is_retryable(e):
            raise _RetryableError(str(e)) from e
        raise _ProviderUnavailable(str(e)) from e


def _is_retryable(exc: Exception) -> bool:
    """Classify any provider exception. Retryable: rate limit, 5xx, timeout."""
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
