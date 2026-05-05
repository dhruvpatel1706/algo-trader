"""Unit tests for src.data.macro_deal_extractor.

The Anthropic SDK is mocked via a fake module on ``sys.modules``. No real
network calls are made.
"""

from __future__ import annotations

import sys
import types
from datetime import UTC, date, datetime
from typing import ClassVar

import pytest
from src.data.macro_deal_extractor import (
    MacroDeal,
    _parse_amount,
    extract_macro_deals,
)
from src.data.news import NewsArticle, _hash_body, _make_id


def _make_article(
    headline: str = "NVIDIA invests $1B in Nokia for AI infra",
    body: str = "NVIDIA announced a strategic $1 billion investment in Nokia.",
    ticker: str = "NVDA",
) -> NewsArticle:
    body_hash = _hash_body(body)
    return NewsArticle(
        id=_make_id("finnhub", ticker, "https://x.test/deal", body_hash),
        source="finnhub",
        ticker=ticker,
        headline=headline,
        body=body,
        url="https://x.test/deal",
        published_at=datetime(2024, 1, 15, tzinfo=UTC),
        body_hash=body_hash,
    )


# ---------------------------------------------------------------------------
# Defensive paths
# ---------------------------------------------------------------------------


def test_extract_macro_deals_empty_list_returns_empty():
    assert extract_macro_deals([], today=date(2024, 1, 1)) == []


def test_extract_macro_deals_no_anthropic_key_returns_empty(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    out = extract_macro_deals([_make_article()], today=date(2024, 1, 1))
    assert out == []


def test_extract_macro_deals_anthropic_not_installed_returns_empty(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    monkeypatch.setitem(sys.modules, "anthropic", None)
    out = extract_macro_deals([_make_article()], today=date(2024, 1, 1))
    assert out == []


# ---------------------------------------------------------------------------
# Amount parsing
# ---------------------------------------------------------------------------


def test_parse_amount_dollar_billion():
    assert _parse_amount("$1B") == 1_000_000_000.0


def test_parse_amount_dollar_million():
    assert _parse_amount("$500M") == 500_000_000.0


def test_parse_amount_billion_word_with_decimal():
    assert _parse_amount("1.5 billion") == 1_500_000_000.0


def test_parse_amount_million_word_with_decimal():
    assert _parse_amount("750.5 million") == 750_500_000.0


def test_parse_amount_bn_short():
    assert _parse_amount("$2.0bn") == 2_000_000_000.0


def test_parse_amount_thousand_k():
    assert _parse_amount("100k") == 100_000.0


def test_parse_amount_plain_number_treated_as_usd():
    assert _parse_amount(1_000_000_000) == 1_000_000_000.0
    assert _parse_amount(500_000_000.0) == 500_000_000.0


def test_parse_amount_none_returns_none():
    assert _parse_amount(None) is None


def test_parse_amount_unparseable_string_returns_none():
    assert _parse_amount("a couple million-ish, maybe") in (
        None,
        # The regex will find a bare number-less hit if any digits exist; this
        # phrase has no digits so it must be None.
    )
    assert _parse_amount("loads of money") is None


def test_parse_amount_zero_or_negative_returns_none():
    assert _parse_amount(0) is None
    assert _parse_amount(-100) is None
    assert _parse_amount("$0") is None


def test_parse_amount_rejects_bool():
    # bool is a subclass of int -- excluded explicitly so True != "$1".
    assert _parse_amount(True) is None
    assert _parse_amount(False) is None


# ---------------------------------------------------------------------------
# Mocked Anthropic SDK -- happy path
# ---------------------------------------------------------------------------


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


_GOOD_DEAL_RESPONSE = (
    '{"is_deal": true, "deal_type": "investment", "actor": "NVIDIA", '
    '"target": "Nokia", "amount_usd_billions": 1.0, '
    '"purpose": "AI infrastructure for 6G", "confidence": 0.85, '
    '"actor_tickers": ["NVDA"], "target_tickers": ["NOK"]}'
)


class _FakeAnthropic:
    last_recorder: ClassVar[dict] = {}
    response_text: ClassVar[str] = _GOOD_DEAL_RESPONSE

    def __init__(self, api_key=None):
        _FakeAnthropic.last_recorder["api_key"] = api_key
        self.messages = _FakeMessages(_FakeAnthropic.last_recorder, _FakeAnthropic.response_text)


def _install_fake_anthropic(monkeypatch, response_text: str | None = None):
    fake_module = types.ModuleType("anthropic")
    if response_text is not None:
        _FakeAnthropic.response_text = response_text
    else:
        _FakeAnthropic.response_text = _GOOD_DEAL_RESPONSE
    fake_module.Anthropic = _FakeAnthropic
    monkeypatch.setitem(sys.modules, "anthropic", fake_module)
    _FakeAnthropic.last_recorder = {}


def test_extract_macro_deals_parses_known_json(monkeypatch):
    _install_fake_anthropic(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")

    article = _make_article()
    out = extract_macro_deals([article], today=date(2024, 1, 15))
    assert len(out) == 1
    deal = out[0]
    assert isinstance(deal, MacroDeal)
    assert deal.deal_type == "investment"
    assert deal.actor == "NVIDIA"
    assert deal.target == "Nokia"
    assert deal.amount_usd == pytest.approx(1_000_000_000.0)
    assert deal.purpose == "AI infrastructure for 6G"
    assert deal.confidence == pytest.approx(0.85)
    assert deal.actor_tickers == ["NVDA"]
    assert deal.target_tickers == ["NOK"]
    assert deal.article_id == article.id


def test_extract_macro_deals_includes_today_in_system_prompt(monkeypatch):
    _install_fake_anthropic(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")

    extract_macro_deals([_make_article()], today=date(2024, 6, 1))
    assert "2024-06-01" in _FakeAnthropic.last_recorder["system"]
    assert "CONSERVATIVE" in _FakeAnthropic.last_recorder["system"]
    assert "is_deal" in _FakeAnthropic.last_recorder["system"]


# ---------------------------------------------------------------------------
# Filters: confidence, is_deal, amount
# ---------------------------------------------------------------------------


def test_extract_macro_deals_filters_low_confidence(monkeypatch):
    _install_fake_anthropic(
        monkeypatch,
        response_text=(
            '{"is_deal": true, "deal_type": "investment", "actor": "X", '
            '"target": "Y", "amount_usd_billions": 1.0, "purpose": "p", '
            '"confidence": 0.4}'
        ),
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    out = extract_macro_deals([_make_article()], today=date(2024, 1, 1))
    assert out == []


def test_extract_macro_deals_filters_is_deal_false(monkeypatch):
    _install_fake_anthropic(
        monkeypatch,
        response_text=(
            '{"is_deal": false, "deal_type": "investment", "actor": "X", '
            '"target": "Y", "amount_usd_billions": null, "purpose": "p", '
            '"confidence": 0.95}'
        ),
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    out = extract_macro_deals([_make_article()], today=date(2024, 1, 1))
    assert out == []


def test_extract_macro_deals_filters_amount_below_threshold(monkeypatch):
    # $50M deal vs $100M default minimum -> filtered.
    _install_fake_anthropic(
        monkeypatch,
        response_text=(
            '{"is_deal": true, "deal_type": "investment", "actor": "X", '
            '"target": "Y", "amount_usd_billions": 0.05, "purpose": "p", '
            '"confidence": 0.95}'
        ),
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    out = extract_macro_deals([_make_article()], today=date(2024, 1, 1))
    assert out == []


def test_extract_macro_deals_keeps_amount_at_threshold(monkeypatch):
    _install_fake_anthropic(
        monkeypatch,
        response_text=(
            '{"is_deal": true, "deal_type": "investment", "actor": "X", '
            '"target": "Y", "amount_usd_billions": 0.1, "purpose": "p", '
            '"confidence": 0.7}'
        ),
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    out = extract_macro_deals([_make_article()], today=date(2024, 1, 1))
    assert len(out) == 1
    assert out[0].amount_usd == pytest.approx(100_000_000.0)


def test_extract_macro_deals_filters_missing_amount_when_min_set(monkeypatch):
    # No amount but min_amount_usd > 0 -> drop.
    _install_fake_anthropic(
        monkeypatch,
        response_text=(
            '{"is_deal": true, "deal_type": "partnership", "actor": "X", '
            '"target": "Y", "amount_usd_billions": null, "purpose": "p", '
            '"confidence": 0.95}'
        ),
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    out = extract_macro_deals([_make_article()], today=date(2024, 1, 1))
    assert out == []


def test_extract_macro_deals_keeps_missing_amount_when_min_zero(monkeypatch):
    _install_fake_anthropic(
        monkeypatch,
        response_text=(
            '{"is_deal": true, "deal_type": "partnership", "actor": "X", '
            '"target": "Y", "amount_usd_billions": null, "purpose": "p", '
            '"confidence": 0.95}'
        ),
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    out = extract_macro_deals([_make_article()], today=date(2024, 1, 1), min_amount_usd=0)
    assert len(out) == 1
    assert out[0].amount_usd is None


def test_extract_macro_deals_filters_invalid_deal_type(monkeypatch):
    _install_fake_anthropic(
        monkeypatch,
        response_text=(
            '{"is_deal": true, "deal_type": "rumor", "actor": "X", '
            '"target": "Y", "amount_usd_billions": 1.0, "purpose": "p", '
            '"confidence": 0.95}'
        ),
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    out = extract_macro_deals([_make_article()], today=date(2024, 1, 1))
    assert out == []


def test_extract_macro_deals_handles_unparseable_response(monkeypatch):
    _install_fake_anthropic(monkeypatch, response_text="not json at all")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    out = extract_macro_deals([_make_article()], today=date(2024, 1, 1))
    assert out == []


def test_extract_macro_deals_handles_fenced_json(monkeypatch):
    _install_fake_anthropic(
        monkeypatch,
        response_text="```json\n" + _GOOD_DEAL_RESPONSE + "\n```",
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    out = extract_macro_deals([_make_article()], today=date(2024, 1, 1))
    assert len(out) == 1


def test_extract_macro_deals_handles_api_exception(monkeypatch):
    class _RaisingMessages:
        def create(self, **_):
            raise RuntimeError("network down")

    class _RaisingAnthropic:
        def __init__(self, api_key=None):
            self.messages = _RaisingMessages()

    fake_module = types.ModuleType("anthropic")
    fake_module.Anthropic = _RaisingAnthropic
    monkeypatch.setitem(sys.modules, "anthropic", fake_module)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")

    out = extract_macro_deals([_make_article()], today=date(2024, 1, 1))
    assert out == []


def test_extract_macro_deals_uses_default_haiku_model(monkeypatch):
    _install_fake_anthropic(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    extract_macro_deals([_make_article()], today=date(2024, 1, 1))
    assert _FakeAnthropic.last_recorder["model"] == "claude-haiku-4-5-20251001"


def test_extract_macro_deals_clamps_confidence_into_unit_interval(monkeypatch):
    _install_fake_anthropic(
        monkeypatch,
        response_text=(
            '{"is_deal": true, "deal_type": "investment", "actor": "X", '
            '"target": "Y", "amount_usd_billions": 1.0, "purpose": "p", '
            '"confidence": 1.5}'
        ),
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    out = extract_macro_deals([_make_article()], today=date(2024, 1, 1))
    assert len(out) == 1
    assert out[0].confidence == 1.0


def test_macro_deal_dataclass_holds_expected_fields():
    deal = MacroDeal(
        article_id="x" * 32,
        deal_type="investment",
        actor="NVIDIA",
        target="Nokia",
        amount_usd=1_000_000_000.0,
        purpose="AI infra",
        confidence=0.85,
        extracted_at=datetime(2024, 1, 1, tzinfo=UTC),
        actor_tickers=["NVDA"],
        target_tickers=["NOK"],
    )
    assert deal.actor == "NVIDIA"
    assert deal.target == "Nokia"
    assert deal.amount_usd == 1_000_000_000.0
    assert deal.confidence == 0.85
