"""Unit tests for src.data.sentiment.

The Anthropic API is mocked: we install a fake `anthropic` module on sys.modules so
importing inside score_article picks it up without ever hitting the network.
"""

from __future__ import annotations

import sys
import types
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


# --- score_article: mocked API path ---------------------------------------------------


class _FakeBlock:
    def __init__(self, text: str):
        self.text = text


class _FakeMessage:
    def __init__(self, text: str):
        self.content = [_FakeBlock(text)]


class _FakeMessages:
    def __init__(self, recorder: dict, response_text: str):
        self._recorder = recorder
        self._response_text = response_text

    def create(self, *, model, max_tokens, system, messages, **_):
        self._recorder["model"] = model
        self._recorder["max_tokens"] = max_tokens
        self._recorder["system"] = system
        self._recorder["messages"] = messages
        return _FakeMessage(self._response_text)


_DEFAULT_RESPONSE = (
    '{"score": 0.7, "label": "bullish", "confidence": 0.8, "reasoning": "good"}'
)


class _FakeAnthropic:
    last_recorder: ClassVar[dict] = {}
    response_text: ClassVar[str] = _DEFAULT_RESPONSE

    def __init__(self, api_key=None):
        _FakeAnthropic.last_recorder["api_key"] = api_key
        self.messages = _FakeMessages(_FakeAnthropic.last_recorder, _FakeAnthropic.response_text)


def _install_fake_anthropic(monkeypatch, response_text: str | None = None):
    """Install a fake `anthropic` module on sys.modules so the lazy import resolves."""
    fake_module = types.ModuleType("anthropic")
    if response_text is not None:
        _FakeAnthropic.response_text = response_text
    fake_module.Anthropic = _FakeAnthropic
    monkeypatch.setitem(sys.modules, "anthropic", fake_module)
    _FakeAnthropic.last_recorder = {}


def test_score_article_uses_anthropic_and_parses_response(monkeypatch):
    _install_fake_anthropic(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")

    article = _make_article()
    out = score_article(article, today=date(2024, 1, 15))

    assert out.model != "stub"
    assert out.score == pytest.approx(0.7)
    assert out.label == "bullish"
    assert out.confidence == pytest.approx(0.8)
    assert out.article_id == article.id
    assert _FakeAnthropic.last_recorder["api_key"] == "fake-key"


def test_score_article_includes_today_date_in_prompt(monkeypatch):
    _install_fake_anthropic(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")

    article = _make_article()
    today = date(2024, 1, 15)
    score_article(article, today=today)

    system_prompt = _FakeAnthropic.last_recorder["system"]
    assert "2024-01-15" in system_prompt
    assert "do not know what happens after this date" in system_prompt


def test_score_article_anonymizes_user_prompt(monkeypatch):
    _install_fake_anthropic(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")

    article = _make_article(
        ticker="AAPL",
        headline="Apple (AAPL) crushes earnings",
        body="Apple reported record sales of iPhones today.",
    )
    score_article(article, today=date(2024, 1, 15))

    user_msg = _FakeAnthropic.last_recorder["messages"][0]["content"]
    # The ticker and company name must not appear in the prompt sent to the model.
    assert "AAPL" not in user_msg
    assert "Apple" not in user_msg
    assert "[ASSET_" in user_msg


def test_score_article_uses_default_haiku_model(monkeypatch):
    _install_fake_anthropic(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")

    score_article(_make_article(), today=date(2024, 1, 15))
    assert _FakeAnthropic.last_recorder["model"] == "claude-haiku-4-5-20251001"


def test_score_article_clamps_out_of_range_score(monkeypatch):
    _install_fake_anthropic(
        monkeypatch,
        response_text='{"score": 2.5, "label": "bullish", "confidence": 1.5}',
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")

    out = score_article(_make_article(), today=date(2024, 1, 15))
    assert out.score == 1.0
    assert out.confidence == 1.0


def test_score_article_falls_back_to_stub_on_unparseable_response(monkeypatch):
    _install_fake_anthropic(monkeypatch, response_text="not json at all")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")

    out = score_article(_make_article(), today=date(2024, 1, 15))
    assert out.model == "stub"
    assert out.score == 0.0


def test_score_article_falls_back_to_stub_on_api_exception(monkeypatch):
    """If the Anthropic client raises, return the neutral stub."""

    class _RaisingMessages:
        def create(self, **_):
            raise RuntimeError("network down")

    class _RaisingAnthropic:
        def __init__(self, api_key=None):
            self.messages = _RaisingMessages()

    fake_module = types.ModuleType("anthropic")
    fake_module.Anthropic = _RaisingAnthropic
    monkeypatch.setitem(sys.modules, "anthropic", fake_module)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")

    out = score_article(_make_article(), today=date(2024, 1, 15))
    assert out.model == "stub"
    assert out.score == 0.0


def test_score_article_handles_fenced_json_response(monkeypatch):
    _install_fake_anthropic(
        monkeypatch,
        response_text='```json\n{"score": -0.5, "label": "bearish", "confidence": 0.6}\n```',
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")

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
