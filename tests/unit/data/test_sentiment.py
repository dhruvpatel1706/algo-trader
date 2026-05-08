"""Unit tests for src.data.sentiment.

The Anthropic API is mocked: we install a fake `anthropic` module on sys.modules so
importing inside score_article picks it up without ever hitting the network.
"""

from __future__ import annotations

import sys
from datetime import UTC, date, datetime
from typing import ClassVar

import pytest
from src.data import sentiment as sentiment_mod
from src.data.news import NewsArticle, _hash_body, _make_id
from src.data.sentiment import (
    SentimentScore,
    _build_prompt,
    _parse_response_text,
    anonymize_headline,
    score_article,
)


def _make_article(
    ticker: str = "AAPL",
    headline: str = "AAPL beats expectations on strong iPhone sales",
    body: str = "Apple posted record revenue this quarter.",
) -> NewsArticle:
    body_hash = _hash_body(body)
    return NewsArticle(
        id=_make_id("finnhub", ticker, "https://x.test/a", body_hash),
        source="finnhub",
        ticker=ticker,
        headline=headline,
        body=body,
        url="https://x.test/a",
        published_at=datetime(2024, 1, 15, tzinfo=UTC),
        body_hash=body_hash,
    )


# --- anonymize_headline ----------------------------------------------------------------


def test_anonymize_replaces_ticker_with_placeholder():
    out = anonymize_headline("AAPL beats expectations", "AAPL")
    assert "AAPL" not in out
    assert out.startswith("[ASSET_") or "[ASSET_" in out


def test_anonymize_placeholder_includes_asset_prefix_and_id():
    out = anonymize_headline("AAPL up 5%", "AAPL")
    # Placeholder format: [ASSET_xxxx]
    assert "[ASSET_" in out
    # 8-char hex id portion.
    placeholder = sentiment_mod._placeholder_for("AAPL")
    assert placeholder.startswith("[ASSET_")
    assert placeholder.endswith("]")
    assert len(placeholder) == len("[ASSET_") + 8 + 1


def test_anonymize_replaces_company_name_apple():
    out = anonymize_headline("Apple unveils new iPhone", "AAPL")
    assert "Apple" not in out
    assert "[ASSET_" in out


def test_anonymize_replaces_both_ticker_and_company_name():
    out = anonymize_headline("Apple (AAPL) hits all-time high", "AAPL")
    assert "Apple" not in out
    assert "AAPL" not in out


def test_anonymize_does_not_clobber_substring_within_word():
    # "AAPLE" should not be touched (whole-word match only).
    out = anonymize_headline("PINEAPPLE harvest grows", "AAPL")
    assert "PINEAPPLE" in out


def test_anonymize_is_case_insensitive_for_ticker():
    out = anonymize_headline("aapl rallies", "AAPL")
    assert "aapl" not in out
    assert "[ASSET_" in out


def test_anonymize_placeholder_is_stable_per_ticker():
    a = sentiment_mod._placeholder_for("AAPL")
    b = sentiment_mod._placeholder_for("AAPL")
    c = sentiment_mod._placeholder_for("MSFT")
    assert a == b
    assert a != c


# --- score_article: stub paths ---------------------------------------------------------


def test_score_article_no_api_key_returns_stub(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    article = _make_article()
    out = score_article(article, today=date(2024, 1, 15))
    assert out.score == 0.0
    assert out.label == "neutral"
    assert out.model == "stub"
    assert out.article_id == article.id


def test_score_article_returns_score_in_unit_interval(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    out = score_article(_make_article(), today=date(2024, 1, 15))
    assert -1.0 <= out.score <= 1.0


def test_score_article_returns_stub_when_anthropic_not_installed(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy")
    # Ensure import of `anthropic` fails inside score_article.
    monkeypatch.setitem(sys.modules, "anthropic", None)
    out = score_article(_make_article(), today=date(2024, 1, 15))
    assert out.model == "stub"
    assert out.score == 0.0


# --- score_article: mocked router path -----------------------------------------------


_DEFAULT_RESPONSE = (
    '{"score": 0.7, "label": "bullish", "confidence": 0.8, "reasoning": "good"}'
)


class _FakeRouter:
    """Fake router that records every call() invocation. The sentiment
    module was refactored to route through ``src.llm.router.default_router()``
    instead of importing the Anthropic SDK directly, so tests now patch
    the router rather than the SDK."""

    last_recorder: ClassVar[dict] = {}
    response_text: ClassVar[str] = _DEFAULT_RESPONSE
    response_provider: ClassVar[str] = "gemini"
    response_model: ClassVar[str] = "gemini-2.5-flash"
    raise_on_call: ClassVar[Exception | None] = None

    def call(self, *, system, user, max_tokens, temperature):
        from src.llm.router import LLMResponse

        _FakeRouter.last_recorder["system"] = system
        _FakeRouter.last_recorder["user"] = user
        _FakeRouter.last_recorder["max_tokens"] = max_tokens
        _FakeRouter.last_recorder["temperature"] = temperature
        if _FakeRouter.raise_on_call is not None:
            raise _FakeRouter.raise_on_call
        return LLMResponse(
            text=_FakeRouter.response_text,
            provider=_FakeRouter.response_provider,  # type: ignore[arg-type]
            model=_FakeRouter.response_model,
        )


def _install_fake_router(
    monkeypatch,
    response_text: str | None = None,
    raise_on_call: Exception | None = None,
):
    """Patch the router singleton so score_article goes through the fake."""
    if response_text is not None:
        _FakeRouter.response_text = response_text
    else:
        _FakeRouter.response_text = _DEFAULT_RESPONSE
    _FakeRouter.raise_on_call = raise_on_call
    _FakeRouter.last_recorder = {}
    fake_router = _FakeRouter()
    # Patch where ``score_article`` imports it from (lazy import inside the
    # function — patching the module attribute is what gets picked up).
    import src.llm.router as router_mod

    monkeypatch.setattr(router_mod, "default_router", lambda: fake_router)


def test_score_article_uses_router_and_parses_response(monkeypatch):
    _install_fake_router(monkeypatch)
    article = _make_article()
    out = score_article(article, today=date(2024, 1, 15))

    assert out.model != "stub"
    assert out.score == pytest.approx(0.7)
    assert out.label == "bullish"
    assert out.confidence == pytest.approx(0.8)
    assert out.article_id == article.id
    # Model field now records "provider/model" because the router
    # decides which model served the request.
    assert "/" in out.model


def test_score_article_includes_today_date_in_prompt(monkeypatch):
    _install_fake_router(monkeypatch)
    article = _make_article()
    today = date(2024, 1, 15)
    score_article(article, today=today)

    system_prompt = _FakeRouter.last_recorder["system"]
    assert "2024-01-15" in system_prompt
    assert "do not know what happens after this date" in system_prompt


def test_score_article_anonymizes_user_prompt(monkeypatch):
    _install_fake_router(monkeypatch)
    article = _make_article(
        ticker="AAPL",
        headline="Apple (AAPL) crushes earnings",
        body="Apple reported record sales of iPhones today.",
    )
    score_article(article, today=date(2024, 1, 15))

    user_msg = _FakeRouter.last_recorder["user"]
    # The ticker and company name must not appear in the prompt sent to the model.
    assert "AAPL" not in user_msg
    assert "Apple" not in user_msg
    assert "[ASSET_" in user_msg


def test_score_article_records_provider_and_model_in_result(monkeypatch):
    """The model field on SentimentScore should now reflect WHICH provider
    actually served the request, since the chain may fall through."""
    _FakeRouter.response_provider = "gemini"
    _FakeRouter.response_model = "gemini-2.0-flash"
    _install_fake_router(monkeypatch)

    out = score_article(_make_article(), today=date(2024, 1, 15))
    assert out.model == "gemini/gemini-2.0-flash"


def test_score_article_clamps_out_of_range_score(monkeypatch):
    _install_fake_router(
        monkeypatch,
        response_text='{"score": 2.5, "label": "bullish", "confidence": 1.5}',
    )
    out = score_article(_make_article(), today=date(2024, 1, 15))
    assert out.score == 1.0
    assert out.confidence == 1.0


def test_score_article_falls_back_to_stub_on_unparseable_response(monkeypatch):
    _install_fake_router(monkeypatch, response_text="not json at all")
    out = score_article(_make_article(), today=date(2024, 1, 15))
    assert out.model == "stub"
    assert out.score == 0.0


def test_score_article_falls_back_to_stub_on_router_exception(monkeypatch):
    """If every provider in the router chain fails, return the neutral stub."""
    from src.llm.router import LLMUnavailableError

    _install_fake_router(
        monkeypatch,
        raise_on_call=LLMUnavailableError("all providers down"),
    )
    out = score_article(_make_article(), today=date(2024, 1, 15))
    assert out.model == "stub"
    assert out.score == 0.0


def test_score_article_handles_fenced_json_response(monkeypatch):
    _install_fake_router(
        monkeypatch,
        response_text='```json\n{"score": -0.5, "label": "bearish", "confidence": 0.6}\n```',
    )
    out = score_article(_make_article(), today=date(2024, 1, 15))
    assert out.score == pytest.approx(-0.5)
    assert out.label == "bearish"


# --- helpers (sanity) -----------------------------------------------------------------


def test_build_prompt_returns_anonymized_user_with_dated_system():
    article = _make_article(headline="Apple AAPL shines", body="Apple did well today.")
    sys_prompt, user_prompt = _build_prompt(article, today=date(2024, 6, 1))
    assert "2024-06-01" in sys_prompt
    assert "AAPL" not in user_prompt
    assert "Apple" not in user_prompt


def test_parse_response_text_returns_none_for_non_object():
    assert _parse_response_text("[1, 2, 3]") is None
    assert _parse_response_text("") is None


def test_sentiment_score_dataclass_holds_expected_fields():
    s = SentimentScore(
        article_id="a",
        score=0.5,
        label="bullish",
        confidence=0.9,
        model="stub",
        scored_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    assert -1.0 <= s.score <= 1.0
    assert s.label in {"bullish", "neutral", "bearish"}
