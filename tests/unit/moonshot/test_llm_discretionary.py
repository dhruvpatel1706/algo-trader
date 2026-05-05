from __future__ import annotations

import inspect
import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from src.moonshot import llm_discretionary
from src.moonshot.llm_discretionary import LlmDecision, LlmDiscretionaryAgent


@pytest.fixture
def market_state() -> dict:
    return {
        "tickers": {
            "AAPL": {"close": 150.0, "rsi": 55.0},
            "MSFT": {"close": 320.0, "rsi": 62.0},
        }
    }


def test_decide_with_no_api_key_returns_abstain(monkeypatch, market_state: dict) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    agent = LlmDiscretionaryAgent()
    decision = agent.decide(market_state, today=date(2026, 5, 4))
    assert isinstance(decision, LlmDecision)
    assert decision.action == "abstain"
    assert decision.confidence == 0.0
    # Fallback ticker should be one of the inputs (first).
    assert decision.ticker == "AAPL"
    assert "no api client" in decision.reasoning.lower()


def test_decide_with_mock_client_returns_parsed_decision(market_state: dict) -> None:
    mock_response = SimpleNamespace(
        content=[SimpleNamespace(text=json.dumps(
            {"ticker": "T1", "action": "buy", "confidence": 0.72, "reasoning": "rsi rising"}
        ))]
    )
    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_response

    agent = LlmDiscretionaryAgent(client=mock_client)
    decision = agent.decide(market_state, today=date(2026, 5, 4))

    assert decision.action == "buy"
    assert decision.confidence == pytest.approx(0.72)
    assert decision.reasoning == "rsi rising"
    assert mock_client.messages.create.call_count == 1
    # Must use anonymized payload (no real ticker leaks into the prompt).
    call = mock_client.messages.create.call_args
    sent = call.kwargs.get("messages", [{}])[0]
    body = sent.get("content", "")
    assert "AAPL" not in body
    assert "MSFT" not in body
    assert "T1" in body


def test_decide_unparseable_response_abstains(market_state: dict) -> None:
    mock_response = SimpleNamespace(content=[SimpleNamespace(text="not json")])
    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_response
    agent = LlmDiscretionaryAgent(client=mock_client)
    decision = agent.decide(market_state, today=date(2026, 5, 4))
    assert decision.action == "abstain"


def test_decide_dict_response_supported(market_state: dict) -> None:
    """Some mocks return dict-shaped responses; agent should handle both."""
    response = {"content": [{"text": json.dumps({
        "ticker": "T2",
        "action": "sell",
        "confidence": 0.4,
        "reasoning": "overheated",
    })}]}
    client = MagicMock()
    client.messages.create.return_value = response
    agent = LlmDiscretionaryAgent(client=client)
    decision = agent.decide(market_state, today=date(2026, 5, 4))
    assert decision.action == "sell"
    assert decision.confidence == pytest.approx(0.4)


def test_decide_invalid_action_clamps_to_abstain(market_state: dict) -> None:
    response = SimpleNamespace(content=[SimpleNamespace(text=json.dumps({
        "ticker": "T1",
        "action": "moon",
        "confidence": 1.5,
    }))])
    client = MagicMock()
    client.messages.create.return_value = response
    agent = LlmDiscretionaryAgent(client=client)
    decision = agent.decide(market_state, today=date(2026, 5, 4))
    assert decision.action == "abstain"


def test_decide_api_error_abstains(market_state: dict) -> None:
    client = MagicMock()
    client.messages.create.side_effect = RuntimeError("boom")
    agent = LlmDiscretionaryAgent(client=client)
    decision = agent.decide(market_state, today=date(2026, 5, 4))
    assert decision.action == "abstain"
    assert "API error" in decision.reasoning


def test_paper_only_safety() -> None:
    assert LlmDiscretionaryAgent.LIVE_BROKER_BRIDGE is False
    src = inspect.getsource(llm_discretionary)
    for token in ["src.execution", "alpaca.trading", "TradingClient"]:
        assert token not in src

    # Even instantiating with a mocked client must not flip the safety flag.
    agent = LlmDiscretionaryAgent(client=MagicMock())
    assert agent.LIVE_BROKER_BRIDGE is False
