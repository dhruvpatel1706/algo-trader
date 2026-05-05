"""Unit tests for src.research.strategy_scout.

No real network calls. We monkeypatch the single ``_http_get_json`` seam for
GitHub and install a fake ``anthropic`` module on sys.modules for the LLM
evaluator path.
"""

from __future__ import annotations

import json
import subprocess
import sys
import types
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, ClassVar

import pytest
from src.research import strategy_scout as scout_mod
from src.research.strategy_scout import (
    StrategyCandidate,
    StrategyEvaluation,
    StrategyScout,
    _anonymize_tickers,
    _build_evaluation_prompt,
    _clamp01,
    _classify_complexity,
    _format_section,
    _neutral_evaluation,
    _parse_response_text,
)

# -- Helpers ----------------------------------------------------------------------------


def _candidate(
    title: str = "donchian-failed-breakout",
    url: str = "https://github.com/example/donchian-failed-breakout",
    summary: str = "Donchian breakout fade strategy with RSI confirmation",
    asset_class: list[str] | None = None,
    indicators: list[str] | None = None,
    raw_excerpt: str = "Donchian breakout RSI confirmation",
) -> StrategyCandidate:
    return StrategyCandidate(
        title=title,
        source="github",
        url=url,
        summary=summary,
        asset_class=asset_class if asset_class is not None else ["equity"],
        timeframes=["1d"],
        indicators_used=indicators if indicators is not None else ["RSI", "Donchian"],
        rule_complexity="simple",
        discovered_at=datetime(2026, 5, 1, tzinfo=UTC),
        raw_excerpt=raw_excerpt,
    )


def _gh_payload(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {"total_count": len(items), "incomplete_results": False, "items": items}


# -- Dataclass round-trips --------------------------------------------------------------


def test_strategy_candidate_dataclass_holds_fields():
    c = _candidate()
    assert c.title == "donchian-failed-breakout"
    assert c.source == "github"
    assert "RSI" in c.indicators_used
    assert c.rule_complexity == "simple"


def test_strategy_candidate_is_frozen():
    c = _candidate()
    with pytest.raises((AttributeError, Exception)):
        c.title = "different"  # type: ignore[misc]


def test_strategy_evaluation_dataclass_holds_fields():
    c = _candidate()
    ev = StrategyEvaluation(
        candidate=c,
        implementability_score=0.8,
        overfit_risk_score=0.3,
        lookahead_risk_score=0.1,
        novelty_score=0.6,
        expected_sharpe_range=(0.4, 1.0),
        pros=["clear rules"],
        cons=["small sample"],
        recommended_next_step="port_and_backtest",
        rationale="simple, clear, low-risk",
        evaluated_at=datetime(2026, 5, 1, tzinfo=UTC),
        model="claude-haiku-4-5-20251001",
    )
    assert ev.implementability_score == 0.8
    assert ev.recommended_next_step == "port_and_backtest"
    # priority_score = impl - overfit - lookahead = 0.8 - 0.3 - 0.1 = 0.4
    assert ev.priority_score == pytest.approx(0.4)


def test_priority_score_orders_easy_high_low_risk_first():
    c = _candidate()
    high = StrategyEvaluation(
        candidate=c,
        implementability_score=0.9,
        overfit_risk_score=0.1,
        lookahead_risk_score=0.1,
        novelty_score=0.5,
        expected_sharpe_range=(0.0, 0.0),
        pros=[],
        cons=[],
        recommended_next_step="port_and_backtest",
        rationale="",
        evaluated_at=datetime(2026, 5, 1, tzinfo=UTC),
        model="stub",
    )
    low = StrategyEvaluation(
        candidate=c,
        implementability_score=0.3,
        overfit_risk_score=0.8,
        lookahead_risk_score=0.5,
        novelty_score=0.5,
        expected_sharpe_range=(0.0, 0.0),
        pros=[],
        cons=[],
        recommended_next_step="reject",
        rationale="",
        evaluated_at=datetime(2026, 5, 1, tzinfo=UTC),
        model="stub",
    )
    assert high.priority_score > low.priority_score


# -- Anonymization ----------------------------------------------------------------------


def test_anonymize_tickers_replaces_known_ticker():
    out = _anonymize_tickers("AAPL momentum strategy backtest")
    assert "AAPL" not in out
    assert "[ASSET_" in out


def test_anonymize_tickers_does_not_corrupt_substrings():
    out = _anonymize_tickers("PINEAPPLE harvest")
    assert "PINEAPPLE" in out


def test_anonymize_tickers_is_case_insensitive():
    out = _anonymize_tickers("btc-usd perp basis")
    assert "btc" not in out.lower() or "[ASSET_" in out


# -- Heuristic classifiers --------------------------------------------------------------


def test_classify_complexity_simple_for_short_text():
    assert _classify_complexity("Buy when RSI < 30, sell when RSI > 70") == "simple"


def test_classify_complexity_complex_for_ml_keywords():
    assert _classify_complexity("LSTM neural net predicting returns") == "complex"


def test_clamp01_handles_garbage():
    assert _clamp01("nope") == 0.5
    assert _clamp01(None) == 0.5
    assert _clamp01(-3.0) == 0.0
    assert _clamp01(99.9) == 1.0
    assert _clamp01(0.4) == 0.4


def test_parse_response_text_handles_fenced_block():
    out = _parse_response_text('```json\n{"a": 1}\n```')
    assert out == {"a": 1}


def test_parse_response_text_handles_prose_around_json():
    out = _parse_response_text('Here you go: {"a": 1, "b": 2}. End.')
    assert out == {"a": 1, "b": 2}


def test_parse_response_text_returns_none_for_garbage():
    assert _parse_response_text("not json at all") is None
    assert _parse_response_text("[1, 2, 3]") is None


# -- search_github ----------------------------------------------------------------------


def test_search_github_no_token_uses_unauthenticated_path(monkeypatch):
    """Without GITHUB_TOKEN, no Authorization header is sent."""
    captured: dict[str, Any] = {}

    def _fake_get(url: str, headers: dict[str, str]):
        captured["url"] = url
        captured["headers"] = dict(headers)
        return _gh_payload(
            [
                {
                    "full_name": "alice/donchian-fade",
                    "html_url": "https://github.com/alice/donchian-fade",
                    "description": "Donchian fade with RSI confirmation",
                    "topics": ["trading", "donchian"],
                }
            ]
        )

    monkeypatch.setattr(scout_mod, "_http_get_json", _fake_get)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    s = StrategyScout(github_token=None)
    out = s.search_github(["donchian fade"])
    assert len(out) == 1
    assert out[0].source == "github"
    assert out[0].url == "https://github.com/alice/donchian-fade"
    assert "Authorization" not in captured["headers"]
    assert "User-Agent" in captured["headers"]


def test_search_github_with_token_sends_bearer_header(monkeypatch):
    captured: dict[str, Any] = {}

    def _fake_get(url: str, headers: dict[str, str]):
        captured["headers"] = dict(headers)
        return _gh_payload([])

    monkeypatch.setattr(scout_mod, "_http_get_json", _fake_get)
    s = StrategyScout(github_token="ghp_test_token_value")
    s.search_github(["foo"])
    assert captured["headers"].get("Authorization") == "Bearer ghp_test_token_value"


def test_search_github_dedupes_by_url(monkeypatch):
    """Same URL appearing in multiple queries must produce one candidate."""
    payload = _gh_payload(
        [
            {
                "full_name": "x/y",
                "html_url": "https://github.com/x/y",
                "description": "dup",
                "topics": [],
            }
        ]
    )
    monkeypatch.setattr(scout_mod, "_http_get_json", lambda u, h: payload)

    s = StrategyScout(github_token=None, max_candidates_per_query=10)
    out = s.search_github(["q1", "q2", "q3"])
    assert len(out) == 1
    assert out[0].url == "https://github.com/x/y"


def test_search_github_caps_at_max_candidates(monkeypatch):
    """Returns at most max_candidates_per_query across all queries."""
    items = [
        {
            "full_name": f"u/r{i}",
            "html_url": f"https://github.com/u/r{i}",
            "description": "x",
            "topics": [],
        }
        for i in range(50)
    ]
    monkeypatch.setattr(scout_mod, "_http_get_json", lambda u, h: _gh_payload(items))

    s = StrategyScout(github_token=None, max_candidates_per_query=5)
    out = s.search_github(["whatever"])
    assert len(out) == 5


def test_search_github_returns_empty_when_http_fails(monkeypatch):
    monkeypatch.setattr(scout_mod, "_http_get_json", lambda u, h: None)
    s = StrategyScout(github_token=None)
    assert s.search_github(["foo"]) == []


def test_search_github_returns_empty_for_empty_queries():
    s = StrategyScout(github_token=None)
    assert s.search_github([]) == []
    assert s.search_github(["", "  "]) == []


def test_search_github_skips_non_dict_items(monkeypatch):
    """Defensive: malformed items must not crash the scan."""
    payload = _gh_payload(
        [
            "not a dict",  # type: ignore[list-item]
            {"full_name": "ok/repo", "html_url": "https://github.com/ok/repo", "description": "ok"},
        ]
    )
    monkeypatch.setattr(scout_mod, "_http_get_json", lambda u, h: payload)
    s = StrategyScout(github_token=None)
    out = s.search_github(["x"])
    assert len(out) == 1
    assert out[0].url == "https://github.com/ok/repo"


def test_search_github_extracts_indicator_hints(monkeypatch):
    payload = _gh_payload(
        [
            {
                "full_name": "u/rsi-bb",
                "html_url": "https://github.com/u/rsi-bb",
                "description": "RSI + Bollinger Bands mean reversion ETF",
                "topics": ["python", "trading"],
            }
        ]
    )
    monkeypatch.setattr(scout_mod, "_http_get_json", lambda u, h: payload)
    s = StrategyScout(github_token=None)
    out = s.search_github(["mean reversion"])
    assert len(out) == 1
    assert "RSI" in out[0].indicators_used
    assert "Bollinger" in out[0].indicators_used
    assert "equity" in out[0].asset_class


# -- search_web -------------------------------------------------------------------------


def test_search_web_returns_empty_v1_stub():
    s = StrategyScout(github_token=None)
    assert s.search_web(["mean reversion", "momentum"]) == []


# -- evaluate ---------------------------------------------------------------------------


def test_evaluate_no_anthropic_key_returns_neutral(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    s = StrategyScout(github_token=None, anthropic_api_key="")
    ev = s.evaluate(_candidate(), today=date(2026, 5, 4))
    assert ev.model == "stub"
    assert ev.implementability_score == 0.5
    assert ev.overfit_risk_score == 0.5
    assert ev.lookahead_risk_score == 0.5
    assert ev.recommended_next_step == "research_more"


def test_evaluate_no_anthropic_sdk_returns_neutral(monkeypatch):
    """If `anthropic` import fails, fall back to neutral."""
    monkeypatch.setitem(sys.modules, "anthropic", None)
    s = StrategyScout(github_token=None, anthropic_api_key="fake")
    ev = s.evaluate(_candidate(), today=date(2026, 5, 4))
    assert ev.model == "stub"


# -- evaluate: fake anthropic client ---------------------------------------------------


_DEFAULT_LLM_RESPONSE = json.dumps(
    {
        "implementability": 0.8,
        "overfit_risk": 0.2,
        "lookahead_risk": 0.1,
        "novelty": 0.6,
        "sharpe_low": 0.4,
        "sharpe_high": 1.1,
        "pros": ["clear rules", "low param count"],
        "cons": ["small backtest"],
        "next_step": "port_and_backtest",
        "rationale": "low overfit risk, simple to port",
    }
)


class _FakeBlock:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeMessage:
    def __init__(self, text: str) -> None:
        self.content = [_FakeBlock(text)]


class _FakeMessages:
    def __init__(self, recorder: dict, response_text: str) -> None:
        self._recorder = recorder
        self._response_text = response_text

    def create(self, *, model, max_tokens, system, messages, **_kw):
        self._recorder["model"] = model
        self._recorder["max_tokens"] = max_tokens
        self._recorder["system"] = system
        self._recorder["messages"] = messages
        return _FakeMessage(self._response_text)


class _FakeAnthropic:
    last_recorder: ClassVar[dict] = {}
    response_text: ClassVar[str] = _DEFAULT_LLM_RESPONSE

    def __init__(self, api_key: str | None = None) -> None:
        _FakeAnthropic.last_recorder["api_key"] = api_key
        self.messages = _FakeMessages(_FakeAnthropic.last_recorder, _FakeAnthropic.response_text)


def _install_fake_anthropic(monkeypatch, response_text: str | None = None) -> None:
    fake_module = types.ModuleType("anthropic")
    if response_text is not None:
        _FakeAnthropic.response_text = response_text
    else:
        _FakeAnthropic.response_text = _DEFAULT_LLM_RESPONSE
    fake_module.Anthropic = _FakeAnthropic  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "anthropic", fake_module)
    _FakeAnthropic.last_recorder = {}


def test_evaluate_parses_well_formed_json(monkeypatch):
    _install_fake_anthropic(monkeypatch)
    s = StrategyScout(github_token=None, anthropic_api_key="fake-key")
    ev = s.evaluate(_candidate(), today=date(2026, 5, 4))

    assert ev.model != "stub"
    assert ev.implementability_score == pytest.approx(0.8)
    assert ev.overfit_risk_score == pytest.approx(0.2)
    assert ev.lookahead_risk_score == pytest.approx(0.1)
    assert ev.novelty_score == pytest.approx(0.6)
    assert ev.expected_sharpe_range == (0.4, 1.1)
    assert "clear rules" in ev.pros
    assert "small backtest" in ev.cons
    assert ev.recommended_next_step == "port_and_backtest"


def test_evaluate_clamps_out_of_range_scores(monkeypatch):
    _install_fake_anthropic(
        monkeypatch,
        response_text=json.dumps(
            {
                "implementability": 5.0,  # out of range
                "overfit_risk": -1.0,  # out of range
                "lookahead_risk": 0.5,
                "novelty": 0.5,
                "sharpe_low": 0.0,
                "sharpe_high": 0.0,
                "next_step": "research_more",
                "rationale": "",
            }
        ),
    )
    s = StrategyScout(github_token=None, anthropic_api_key="fake-key")
    ev = s.evaluate(_candidate(), today=date(2026, 5, 4))
    assert ev.implementability_score == 1.0
    assert ev.overfit_risk_score == 0.0


def test_evaluate_falls_back_on_malformed_json(monkeypatch):
    _install_fake_anthropic(monkeypatch, response_text="this is not JSON at all")
    s = StrategyScout(github_token=None, anthropic_api_key="fake-key")
    ev = s.evaluate(_candidate(), today=date(2026, 5, 4))
    assert ev.model == "stub"
    assert ev.implementability_score == 0.5


def test_evaluate_falls_back_on_api_exception(monkeypatch):
    class _RaisingMessages:
        def create(self, **_kw):
            raise RuntimeError("network down")

    class _RaisingAnthropic:
        def __init__(self, api_key=None) -> None:
            self.messages = _RaisingMessages()

    fake_module = types.ModuleType("anthropic")
    fake_module.Anthropic = _RaisingAnthropic  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "anthropic", fake_module)

    s = StrategyScout(github_token=None, anthropic_api_key="fake-key")
    ev = s.evaluate(_candidate(), today=date(2026, 5, 4))
    assert ev.model == "stub"
    assert ev.recommended_next_step == "research_more"


def test_evaluate_injects_today_date(monkeypatch):
    _install_fake_anthropic(monkeypatch)
    s = StrategyScout(github_token=None, anthropic_api_key="fake-key")
    s.evaluate(_candidate(), today=date(2026, 5, 4))
    sysprompt = _FakeAnthropic.last_recorder["system"]
    assert "2026-05-04" in sysprompt
    assert "do not know what happens after this date" in sysprompt


def test_evaluate_anonymizes_tickers_in_prompt(monkeypatch):
    _install_fake_anthropic(monkeypatch)
    s = StrategyScout(github_token=None, anthropic_api_key="fake-key")
    cand = _candidate(
        title="AAPL momentum",
        summary="Long AAPL on RSI breakout",
        raw_excerpt="AAPL fast moving average crosses slow",
    )
    s.evaluate(cand, today=date(2026, 5, 4))
    user_msg = _FakeAnthropic.last_recorder["messages"][0]["content"]
    assert "AAPL" not in user_msg
    assert "[ASSET_" in user_msg


def test_evaluate_invalid_next_step_falls_back(monkeypatch):
    _install_fake_anthropic(
        monkeypatch,
        response_text=json.dumps(
            {
                "implementability": 0.5,
                "overfit_risk": 0.5,
                "lookahead_risk": 0.5,
                "novelty": 0.5,
                "sharpe_low": 0.0,
                "sharpe_high": 0.0,
                "next_step": "yolo_send_it",  # not a valid value
                "rationale": "x",
            }
        ),
    )
    s = StrategyScout(github_token=None, anthropic_api_key="fake-key")
    ev = s.evaluate(_candidate(), today=date(2026, 5, 4))
    assert ev.recommended_next_step == "research_more"


def test_evaluate_swaps_inverted_sharpe_range(monkeypatch):
    _install_fake_anthropic(
        monkeypatch,
        response_text=json.dumps(
            {
                "implementability": 0.5,
                "overfit_risk": 0.5,
                "lookahead_risk": 0.5,
                "novelty": 0.5,
                "sharpe_low": 1.5,
                "sharpe_high": 0.2,  # inverted
                "next_step": "research_more",
                "rationale": "",
            }
        ),
    )
    s = StrategyScout(github_token=None, anthropic_api_key="fake-key")
    ev = s.evaluate(_candidate(), today=date(2026, 5, 4))
    sl, sh = ev.expected_sharpe_range
    assert sl <= sh


# -- write_backlog ----------------------------------------------------------------------


def _ev(c: StrategyCandidate, impl: float, overfit: float, lookahead: float) -> StrategyEvaluation:
    return StrategyEvaluation(
        candidate=c,
        implementability_score=impl,
        overfit_risk_score=overfit,
        lookahead_risk_score=lookahead,
        novelty_score=0.5,
        expected_sharpe_range=(0.5, 1.0),
        pros=["x"],
        cons=["y"],
        recommended_next_step="port_and_backtest",
        rationale="r",
        evaluated_at=datetime(2026, 5, 1, tzinfo=UTC),
        model="stub",
    )


def test_write_backlog_creates_file_with_header(tmp_path):
    s = StrategyScout(github_token=None, anthropic_api_key="")
    out = tmp_path / "backlog.md"
    cand = _candidate()
    s.write_backlog([_ev(cand, 0.8, 0.2, 0.1)], out)
    text = out.read_text(encoding="utf-8")
    assert "# Strategy research backlog" in text
    assert "## Scan" in text
    assert "donchian-failed-breakout" in text
    assert "| Score | Title | Source | Asset |" in text


def test_write_backlog_sorts_by_priority_desc(tmp_path):
    """Higher (impl - overfit - lookahead) appears before lower."""
    s = StrategyScout(github_token=None, anthropic_api_key="")
    high = _ev(_candidate(title="HIGH", url="https://example.com/h"), 0.9, 0.1, 0.0)
    low = _ev(_candidate(title="LOW", url="https://example.com/l"), 0.2, 0.8, 0.5)
    out = tmp_path / "b.md"
    s.write_backlog([low, high], out)
    text = out.read_text(encoding="utf-8")
    high_pos = text.find("HIGH")
    low_pos = text.find("LOW")
    assert high_pos != -1 and low_pos != -1
    assert high_pos < low_pos


def test_write_backlog_appends_to_existing(tmp_path):
    """A second write must preserve the first scan rather than overwrite."""
    s = StrategyScout(github_token=None, anthropic_api_key="")
    out = tmp_path / "b.md"
    c1 = _candidate(title="FIRST_SCAN", url="https://example.com/1")
    s.write_backlog([_ev(c1, 0.7, 0.2, 0.1)], out)
    text1 = out.read_text(encoding="utf-8")
    assert "FIRST_SCAN" in text1

    c2 = _candidate(title="SECOND_SCAN", url="https://example.com/2")
    s.write_backlog([_ev(c2, 0.6, 0.3, 0.1)], out)
    text2 = out.read_text(encoding="utf-8")
    assert "FIRST_SCAN" in text2
    assert "SECOND_SCAN" in text2
    # Header must appear exactly once.
    assert text2.count("# Strategy research backlog") == 1
    # Two scan sections.
    assert text2.count("## Scan") >= 2


def test_write_backlog_atomic_rename_no_tmp_left(tmp_path):
    """After write, the .tmp sibling file must not linger."""
    s = StrategyScout(github_token=None, anthropic_api_key="")
    out = tmp_path / "b.md"
    s.write_backlog([_ev(_candidate(), 0.5, 0.5, 0.5)], out)
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == []


def test_write_backlog_handles_empty_evaluations(tmp_path):
    s = StrategyScout(github_token=None, anthropic_api_key="")
    out = tmp_path / "b.md"
    s.write_backlog([], out)
    text = out.read_text(encoding="utf-8")
    assert "## Scan" in text
    assert "_No candidates scored in this scan._" in text


def test_write_backlog_creates_parent_dir(tmp_path):
    s = StrategyScout(github_token=None, anthropic_api_key="")
    out = tmp_path / "nested" / "dir" / "backlog.md"
    s.write_backlog([_ev(_candidate(), 0.5, 0.5, 0.5)], out)
    assert out.exists()


def test_write_backlog_escapes_pipes_in_titles(tmp_path):
    """Ensure markdown-table integrity when titles contain '|'."""
    s = StrategyScout(github_token=None, anthropic_api_key="")
    out = tmp_path / "b.md"
    cand = _candidate(title="bad|title|with|pipes", url="https://example.com/p")
    s.write_backlog([_ev(cand, 0.6, 0.2, 0.1)], out)
    text = out.read_text(encoding="utf-8")
    # Escaped pipes must be present; raw unescaped column-breaking pipes must not split the row.
    assert "bad\\|title\\|with\\|pipes" in text


# -- _format_section --------------------------------------------------------------------


def test_format_section_includes_detail_for_each_candidate():
    cand = _candidate(title="A_TITLE", url="https://example.com/a")
    md = _format_section([_ev(cand, 0.7, 0.2, 0.1)])
    assert "### Detail: A_TITLE" in md
    assert "https://example.com/a" in md
    assert "impl=0.70" in md


def test_format_section_renders_unknown_sharpe():
    cand = _candidate()
    ev = StrategyEvaluation(
        candidate=cand,
        implementability_score=0.5,
        overfit_risk_score=0.5,
        lookahead_risk_score=0.5,
        novelty_score=0.5,
        expected_sharpe_range=(0.0, 0.0),  # unknown
        pros=[],
        cons=[],
        recommended_next_step="research_more",
        rationale="",
        evaluated_at=datetime(2026, 5, 1, tzinfo=UTC),
        model="stub",
    )
    md = _format_section([ev])
    assert "unknown" in md


# -- Build evaluation prompt sanity ----------------------------------------------------


def test_build_evaluation_prompt_anonymizes_and_dates():
    cand = _candidate(
        title="AAPL momentum",
        summary="momentum on AAPL",
        raw_excerpt="AAPL goes up when it goes up",
    )
    sys_p, user_p = _build_evaluation_prompt(cand, today=date(2026, 5, 4))
    assert "2026-05-04" in sys_p
    assert "AAPL" not in user_p
    assert "[ASSET_" in user_p


# -- Neutral evaluation -----------------------------------------------------------------


def test_neutral_evaluation_routes_to_research_more():
    ev = _neutral_evaluation(_candidate(), today=date(2026, 5, 4))
    assert ev.recommended_next_step == "research_more"
    assert ev.implementability_score == 0.5
    assert ev.model == "stub"


# -- CLI smoke test ---------------------------------------------------------------------


def test_cli_dry_run_prints_queries_and_exits_zero(tmp_path):
    """End-to-end: --dry-run should print queries, hit no network, exit 0.

    We pass --queries directly so the test does not depend on the YAML config.
    """
    repo_root = Path(__file__).resolve().parents[3]
    script = repo_root / "scripts" / "scout_strategies.py"
    output = tmp_path / "out.md"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--dry-run",
            "--queries",
            "mean reversion,RSI strategy",
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "DRY RUN" in result.stdout
    assert "mean reversion" in result.stdout
    assert "RSI strategy" in result.stdout
    # Dry run must not write the output file.
    assert not output.exists()


def test_cli_dry_run_with_default_config(tmp_path):
    """With --from-config pointing at the bundled YAML, dry run lists queries."""
    repo_root = Path(__file__).resolve().parents[3]
    script = repo_root / "scripts" / "scout_strategies.py"
    config = repo_root / "docs" / "scout_queries.yaml"
    if not config.exists():
        pytest.skip("default scout_queries.yaml not bundled")
    output = tmp_path / "out.md"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--dry-run",
            "--from-config",
            str(config),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "DRY RUN" in result.stdout


def test_cli_missing_config_returns_1(tmp_path):
    repo_root = Path(__file__).resolve().parents[3]
    script = repo_root / "scripts" / "scout_strategies.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--dry-run",
            "--from-config",
            str(tmp_path / "does_not_exist.yaml"),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 1


# -- max_candidates_per_query validation -----------------------------------------------


def test_scout_constructor_rejects_zero_max():
    with pytest.raises(ValueError):
        StrategyScout(max_candidates_per_query=0)
