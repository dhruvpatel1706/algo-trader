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


# Default fallback chain — Gemini's family of free-tier models stacked
# first to maximize zero-cost uptime, then paid Anthropic/OpenAI as the
# resilience legs.
#
# Each Gemini model in the AI Studio free tier has its OWN RPD quota:
#   - gemini-2.5-flash       ~ 20 RPD  (newest, highest quality, tightest quota)
#   - gemini-2.5-flash-lite  ~ 200 RPD (lighter / faster)
#   - gemini-2.0-flash       ~ 1500 RPD (workhorse)
#   - gemini-2.0-flash-lite  ~ 1500 RPD (fast workhorse)
#   - gemini-1.5-flash-8b    ~ 4000 RPD (highest volume, oldest)
#
# Quota numbers move; treat them as a guide, not a contract. The router
# never relies on the numbers — it just falls through on rate-limit
# response. Listing them all means one model exhausting its RPD does
# NOT take the whole "gemini" leg down (per-model cooldown isolates
# them — see _model_cooldowns below).
#
# Order = best quality first, deepest quota last. Ordering by
# best-quality-first means the autonomous reasoner gets the strongest
# judgment available while it has free quota. By the time we reach the
# 8b model, we still have 4000 calls/day to burn — far more than the
# bot needs at full agent cadence (~100 calls/day).
#
# Anthropic Haiku 4.5 + OpenAI gpt-4.1-mini are the paid resilience
# legs at the bottom: different infra each, so a coincident outage
# across all three vendors is unlikely.
DEFAULT_CHAIN: tuple[ModelSpec, ...] = (
    ModelSpec(
        provider="gemini",
        model="gemini-2.5-flash",
        api_key_env="GEMINI_API_KEY",
    ),
    ModelSpec(
        provider="gemini",
        model="gemini-2.5-flash-lite",
        api_key_env="GEMINI_API_KEY",
    ),
    ModelSpec(
        provider="gemini",
        model="gemini-2.0-flash",
        api_key_env="GEMINI_API_KEY",
    ),
    ModelSpec(
        provider="gemini",
        model="gemini-2.0-flash-lite",
        api_key_env="GEMINI_API_KEY",
    ),
    ModelSpec(
        provider="gemini",
        model="gemini-1.5-flash-8b",
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

# Per-MODEL cooldown — applied to rate-limit responses (429s, retryable
# 5xx, transient timeouts). Different scope from the provider cooldown:
# when gemini-2.5-flash hits its 20 RPD daily limit, the rate limit is
# specific to THAT model; gemini-2.0-flash has a separate 1500 RPD bucket.
# Cooling the whole "gemini" provider in that case wastes the deepest
# quota in the chain.
#
# Window is shorter than the provider cooldown because rate-limit
# responses often clear within a minute (per-minute RPM bucket). Daily
# RPD exhaustion stays cool for the full window, then the chain
# naturally retries — at worst we waste one round-trip per ~10 minutes.
_MODEL_COOLDOWN_SEC = 600


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

        Cooldown semantics (two layers):
          - Per-MODEL cooldown for transient / per-model-quota failures.
            One Gemini model exhausting its free-tier RPD does NOT cool
            its sibling models in the same chain.
          - Per-PROVIDER cooldown for account-level billing / SDK / auth
            failures. The whole provider is dead until a human fixes it.

        Without this the router would burn ~3s per signal eval calling
        dead providers — at 60 evals/hour across 7 chain entries =
        420 wasted API attempts/hour. With cooldowns the second attempt
        within the cool-down window short-circuits to immediate
        fail-open.
        """
        last_error: Exception | None = None
        for spec in self.chain:
            api_key = os.environ.get(spec.api_key_env, "")
            if not api_key:
                logger.debug("router: skipping %s — %s not set", spec.provider, spec.api_key_env)
                continue
            cooldown_remaining = _cooldown_remaining(spec.provider, spec.model)
            if cooldown_remaining > 0:
                logger.debug(
                    "router: skipping %s/%s — cooldown for %.0fs more",
                    spec.provider, spec.model,
                    cooldown_remaining,
                )
                last_error = LLMUnavailableError(
                    f"{spec.provider}/{spec.model} in cooldown "
                    f"({cooldown_remaining:.0f}s remaining)"
                )
                continue
            for attempt in range(spec.max_retries + 1):
                started = time.monotonic()
                try:
                    text, in_tok, out_tok = _dispatch(
                        spec, api_key=api_key, system=system, user=user,
                        max_tokens=max_tokens, temperature=temperature,
                    )
                    # Success — clear both layers of cooldown so the next
                    # call uses this model as the primary again.
                    _clear_cooldown(spec.provider, spec.model)
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
                    if _is_account_billing_error(e):
                        logger.critical(
                            "router: %s BILLING/CREDIT ERROR — top up to restore AI judgment: %s",
                            spec.provider,
                            e,
                        )
                        _mark_failed(spec.provider)  # whole provider dead
                    else:
                        # SDK missing, auth bad, or model-not-found.
                        # Cool only this model — siblings on the same
                        # provider may still be reachable. If the issue
                        # really is provider-wide, every model will fail
                        # in turn and accumulate its own cooldown.
                        logger.warning(
                            "router: %s/%s permanently unavailable: %s",
                            spec.provider, spec.model, e,
                        )
                        _mark_failed(spec.provider, spec.model)
                    break
                except _RetryableError as e:
                    last_error = e
                    if attempt < spec.max_retries:
                        time.sleep(spec.backoff_sec * (2**attempt))
                        continue
                    logger.warning(
                        "router: %s/%s retries exhausted: %s — falling through",
                        spec.provider, spec.model, e,
                    )
                    # Transient / per-model rate limit / per-model RPD —
                    # cool ONLY this model so the next chain entry on
                    # the same provider gets a clean shot.
                    _mark_failed(spec.provider, spec.model)
                    break
        logger.critical(
            "router: ALL providers failed — bot is running fail_open (no AI judgment). "
            "Check billing/quota for anthropic, gemini, openai."
        )
        raise LLMUnavailableError(
            f"all providers failed or skipped (last error: {last_error!r})"
        )


# Module-level cooldown registries. Provider-scoped catches billing /
# SDK / auth failures (the whole provider is dead until a human fixes
# it). Model-scoped catches rate-limit / 5xx / timeout (just THIS
# model is throttled; siblings on the same provider may still work).
#
# Keys:
#   _provider_cooldowns: provider name -> monotonic eligible time
#   _model_cooldowns:    (provider, model) tuple -> monotonic eligible time
#
# A model is cooling down if EITHER its provider OR (provider, model)
# entry is unexpired. Missing keys mean "no cooldown active".
_provider_cooldowns: dict[str, float] = {}
_model_cooldowns: dict[tuple[str, str], float] = {}


def _mark_failed(provider: str, model: str | None = None) -> None:
    """Put a provider OR a single model on cooldown.

    ``model=None`` cools the whole provider (the original semantics —
    used for billing / SDK / auth failures where every model on the
    provider is unreachable). ``model=<name>`` cools just that model
    (used for rate-limit / 5xx / timeout where sibling models on the
    same provider may still have free quota).
    """
    if model is None:
        _provider_cooldowns[provider] = time.monotonic() + _PROVIDER_COOLDOWN_SEC
    else:
        _model_cooldowns[(provider, model)] = time.monotonic() + _MODEL_COOLDOWN_SEC


def _clear_cooldown(provider: str, model: str | None = None) -> None:
    """Reset cooldown after a successful call.

    Clears BOTH provider-level and model-level entries — a successful
    call proves the provider is reachable AND this specific model is
    serving requests, so any pessimism we recorded earlier is stale.
    """
    _provider_cooldowns.pop(provider, None)
    if model is not None:
        _model_cooldowns.pop((provider, model), None)


def _cooldown_remaining(provider: str, model: str | None = None) -> float:  # noqa: PLR0912 - two-mode lookup over two cooldown registries
    """Seconds left in the longest applicable cooldown, or 0 if none.

    Two query modes:
      - ``model=<name>`` (router's per-call lookup): returns the larger
        of the provider-level cooldown and the (provider, model) cooldown.
        A different model on the same provider does NOT count — e.g. if
        gemini-2.5-flash is cooled, gemini-2.0-flash is still considered
        eligible.
      - ``model=None`` (diagnostic / "is anything wrong with this
        provider?"): returns the largest cooldown across the provider
        AND any model on that provider. Useful for tests and for
        operator-facing health checks.
    """
    now = time.monotonic()
    longest = 0.0
    eligible_at = _provider_cooldowns.get(provider)
    if eligible_at is not None:
        remaining = eligible_at - now
        if remaining <= 0:
            _provider_cooldowns.pop(provider, None)
        else:
            longest = remaining
    if model is not None:
        m_eligible_at = _model_cooldowns.get((provider, model))
        if m_eligible_at is not None:
            remaining = m_eligible_at - now
            if remaining <= 0:
                _model_cooldowns.pop((provider, model), None)
            elif remaining > longest:
                longest = remaining
    else:
        # Diagnostic mode: scan every model for this provider and pick
        # the longest live cooldown. Also opportunistically clean up
        # stale entries we walk past.
        stale: list[tuple[str, str]] = []
        for key, eligible_at_m in _model_cooldowns.items():
            if key[0] != provider:
                continue
            remaining = eligible_at_m - now
            if remaining <= 0:
                stale.append(key)
            elif remaining > longest:
                longest = remaining
        for key in stale:
            _model_cooldowns.pop(key, None)
    return longest


def _is_cooling_down(provider: str, model: str | None = None) -> bool:
    """Public-ish helper for tests + diagnostics."""
    return _cooldown_remaining(provider, model) > 0


def reset_cooldowns() -> None:
    """Clear ALL provider AND model cooldowns. Operator-grade tool — use
    after a payment top-up or quota reset to skip the wait. Tests also
    call this in fixtures to start each test with a clean slate."""
    _provider_cooldowns.clear()
    _model_cooldowns.clear()


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


# Tokens that signal an ACCOUNT-LEVEL billing failure — i.e. the
# operator owes money to the vendor or has exhausted a paid plan's
# allowance. These cool the WHOLE PROVIDER because every model on that
# vendor shares the same wallet. They will not self-resolve without
# operator action (top up the card, raise plan tier).
_ACCOUNT_BILLING_TOKENS = (
    "credit balance",     # Anthropic: "Your credit balance is too low"
    "billing",            # generic
    "payment",            # generic
    "insufficient_quota", # OpenAI
)


# Tokens that signal a PER-MODEL free-tier rate / RPD bucket has run
# out. These should cool only THIS MODEL — sibling models on the same
# provider have independent quotas. Gemini's free-tier limit messages
# look like:
#   "Quota exceeded for metric: generativelanguage.googleapis.com/
#    generate_content_free_tier_requests, limit: 20, model: gemini-2.5-flash"
# So "free_tier" + "free tier" reliably distinguish the per-model case
# from the account-level cases above.
_MODEL_QUOTA_TOKENS = (
    "free_tier",
    "free tier",
)


# Legacy tokens kept for backward compat — these match BOTH account-level
# and per-model issues, so the higher-resolution checks above run first.
_BILLING_TOKENS = (
    *_ACCOUNT_BILLING_TOKENS,
    "quota exceeded",
    "resource_exhausted",
)


def _is_billing_error(exc: Exception) -> bool:
    """True for hard billing/quota failures that won't resolve without manual action.

    NOTE: This is the historical / coarse predicate — kept stable for
    callers and existing tests. The router itself uses the finer
    :func:`_is_account_billing_error` and :func:`_is_per_model_quota_error`
    so that a per-model RPD exhaustion (e.g. Gemini free tier) cools
    only the specific model rather than the entire provider.
    """
    msg = str(exc).lower()
    return any(tok in msg for tok in _BILLING_TOKENS)


def _is_account_billing_error(exc: Exception) -> bool:
    """True for *account-level* billing failures (provider-wide, won't self-heal)."""
    msg = str(exc).lower()
    if any(tok in msg for tok in _MODEL_QUOTA_TOKENS):
        return False  # per-model RPD — siblings may still have quota
    return any(tok in msg for tok in _ACCOUNT_BILLING_TOKENS)


def _is_per_model_quota_error(exc: Exception) -> bool:
    """True when only the specific model's free-tier bucket is exhausted."""
    msg = str(exc).lower()
    return any(tok in msg for tok in _MODEL_QUOTA_TOKENS)


def _is_retryable(exc: Exception) -> bool:
    """Classify any provider exception.

    Retryable bucket (chain falls through and we cool ONLY this model):
      - Transient rate limit / 5xx / timeout
      - Per-model free-tier RPD exhaustion (sibling models on the same
        provider have independent buckets)

    Non-retryable bucket (chain falls through and we cool the WHOLE
    provider since every model shares the failure mode):
      - Account-level billing (Anthropic credit balance, OpenAI
        insufficient_quota)
      - SDK / auth / model-not-found errors
    """
    if _is_account_billing_error(exc):
        return False
    if _is_per_model_quota_error(exc):
        return True
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
