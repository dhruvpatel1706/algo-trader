"""LLM router with multi-provider fallback.

Public surface:

    from src.llm import call_llm, LLMUnavailableError

    text = call_llm(
        system="You are a market regime classifier...",
        user="What regime are we in given VIX=18, ...",
        max_tokens=200,
    )

The router tries providers in order (default: Anthropic Haiku → Gemini Flash →
OpenAI mini), and on rate-limit / 5xx / network failure falls through to the
next. Each provider only activates when its API key is present, so a missing
secret is "skipped" not "error". When ALL providers fail, the router raises
`LLMUnavailableError` and the caller decides whether to fail open or closed.

This module is import-safe: it doesn't import any provider SDK at module
scope, so adding a new provider here costs nothing for callers that don't
use it.
"""

from src.llm.router import (
    LLMResponse,
    LLMUnavailableError,
    ModelSpec,
    Provider,
    call_llm,
    default_router,
)

__all__ = [
    "LLMResponse",
    "LLMUnavailableError",
    "ModelSpec",
    "Provider",
    "call_llm",
    "default_router",
]
