"""Strategy research scout.

Discovers trading-strategy candidates from public sources (GitHub, web) and
LLM-scores them for implementability and overfit risk. Output is a markdown
research backlog the operator can act on later.

Hard safety guarantee
---------------------
This module produces research artifacts, NEVER signals. There is no entry
point that returns ``Signal`` objects, no broker import, no order placement.
The only side effect on disk is appending markdown to a backlog file.

Design choices
--------------
* GitHub search uses ``urllib`` + the public REST API. With ``GITHUB_TOKEN``
  rate limit is 30 req/min for code search and 5,000 req/hr for the auth'd
  account; without a token we fall back to 60 req/hr unauthenticated.
* Anthropic client is OPTIONAL. ``evaluate`` returns a neutral rating when the
  key or SDK is missing so operators can dry-run end-to-end.
* All prompts use the same anti-bias hardening as ``src.data.sentiment``:
  today's date is injected and ticker symbols are anonymized. The evaluator
  is deliberately conservative — its scores are advisory inputs to a human
  review queue, never gates.
* Backlog writes are atomic (temp file + ``os.replace``) and append-only so
  history is preserved across scans.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

from src.net import safe_urlopen

logger = logging.getLogger(__name__)

# -- Constants --------------------------------------------------------------------------

_GITHUB_SEARCH_URL = "https://api.github.com/search/repositories"
_DEFAULT_USER_AGENT = "algo-trader strategy-scout (research only)"
_HTTP_TIMEOUT_SEC = 20.0
_DEFAULT_MODEL = "claude-haiku-4-5-20251001"
_RAW_EXCERPT_CHARS = 1000

# Tickers we want to anonymize in LLM prompts. Keep in sync with sentiment.py.
_KNOWN_TICKERS = (
    "AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "META", "TSLA", "NVDA", "NFLX",
    "SPY", "QQQ", "IWM", "GLD", "TLT", "IEF", "AGG", "BND", "BTC", "ETH",
)

_INDICATOR_KEYWORDS = (
    "RSI", "MACD", "EMA", "SMA", "Bollinger", "Donchian", "ATR", "ADX",
    "VWAP", "OBV", "Stochastic", "Williams", "Ichimoku", "Keltner",
    "Supertrend", "Heikin", "ML", "LSTM", "XGBoost", "GBM", "RandomForest",
)
_TIMEFRAME_KEYWORDS = ("1m", "5m", "15m", "30m", "1h", "4h", "1d", "daily", "weekly")
_ASSET_KEYWORDS: dict[str, tuple[str, ...]] = {
    "equity": ("equity", "stock", "etf", "spy", "nasdaq", "nyse"),
    "crypto": ("crypto", "btc", "eth", "bitcoin", "ethereum", "perpetual", "perp"),
    "futures": ("futures", "es=f", "gc=f", "cl=f", "comex", "cme"),
    "forex": ("forex", "fx", "eurusd", "usdjpy"),
    "options": ("options", "iron condor", "wheel", "csp", "covered call"),
    "gold": ("gold", "xauusd", "gld", "gc=f"),
}

# -- Dataclasses ------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StrategyCandidate:
    """A candidate trading strategy discovered by the scout."""

    title: str
    source: Literal["github", "web", "paper", "manual"]
    url: str
    summary: str
    asset_class: list[str] = field(default_factory=list)
    timeframes: list[str] = field(default_factory=list)
    indicators_used: list[str] = field(default_factory=list)
    rule_complexity: Literal["simple", "moderate", "complex"] = "moderate"
    discovered_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    raw_excerpt: str = ""


@dataclass(frozen=True, slots=True)
class StrategyEvaluation:
    """LLM-scored assessment of a candidate."""

    candidate: StrategyCandidate
    implementability_score: float
    overfit_risk_score: float
    lookahead_risk_score: float
    novelty_score: float
    expected_sharpe_range: tuple[float, float]
    pros: list[str]
    cons: list[str]
    recommended_next_step: Literal["port_and_backtest", "research_more", "reject"]
    rationale: str
    evaluated_at: datetime
    model: str

    @property
    def priority_score(self) -> float:
        """Sort key: easy-to-port, low fishy-flag candidates rank highest."""
        return (
            self.implementability_score
            - self.overfit_risk_score
            - self.lookahead_risk_score
        )


# -- Helpers ----------------------------------------------------------------------------


def _clamp01(value: Any, default: float = 0.5) -> float:
    """Coerce to float in [0, 1] with a fallback default."""
    import math  # noqa: PLC0415 — local import keeps this helper self-contained.

    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(v):
        return default
    return max(0.0, min(1.0, v))


def _detect_assets(text: str) -> list[str]:
    """Heuristic asset-class tagger."""
    low = text.lower()
    out: list[str] = []
    for cls, keys in _ASSET_KEYWORDS.items():
        if any(k in low for k in keys):
            out.append(cls)
    return out or ["equity"]


def _detect_timeframes(text: str) -> list[str]:
    low = text.lower()
    return [tf for tf in _TIMEFRAME_KEYWORDS if tf in low]


def _detect_indicators(text: str) -> list[str]:
    low = text.lower()
    return [ind for ind in _INDICATOR_KEYWORDS if ind.lower() in low]


def _classify_complexity(text: str) -> Literal["simple", "moderate", "complex"]:
    """Rule complexity heuristic from text length + indicator count.

    ML/neural-net signatures are evaluated first because a 5-word "LSTM
    predicting returns" excerpt should not be tagged 'simple' just because
    it is short.
    """
    low = text.lower()
    indicators = _detect_indicators(text)
    ml_keys = ("lstm", "neural", "transformer", "deep learning")
    if len(indicators) >= 5 or any(k in low for k in ml_keys):
        return "complex"
    word_count = len(text.split())
    if len(indicators) <= 2 and word_count < 200:
        return "simple"
    return "moderate"


def _anonymize_tickers(text: str) -> str:
    """Replace common ticker symbols with stable [ASSET_xxxx] placeholders.

    Same anti-bias pattern as src.data.sentiment.anonymize_headline. Whole-word
    only — does not corrupt substrings.
    """
    out = text
    for ticker in _KNOWN_TICKERS:
        digest = hashlib.sha256(ticker.encode("utf-8")).hexdigest()[:8]
        placeholder = f"[ASSET_{digest}]"
        # Whole-word, case-insensitive.
        pattern = re.compile(rf"\b{re.escape(ticker)}\b", re.IGNORECASE)
        out = pattern.sub(placeholder, out)
    return out


def _parse_response_text(text: str) -> dict[str, Any] | None:
    """Permissive JSON parser. Tolerates fenced code blocks and surrounding prose."""
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = candidate.strip("`")
        if candidate.lower().startswith("json"):
            candidate = candidate[4:]
        candidate = candidate.strip()
    if not candidate.startswith("{"):
        lo = candidate.find("{")
        hi = candidate.rfind("}")
        if lo == -1 or hi == -1 or hi <= lo:
            return None
        candidate = candidate[lo : hi + 1]
    try:
        out = json.loads(candidate)
    except (json.JSONDecodeError, ValueError):
        return None
    return out if isinstance(out, dict) else None


def _neutral_evaluation(
    candidate: StrategyCandidate,
    today: date,
    rationale: str = "no LLM available; defaulting to neutral, manual review required",
) -> StrategyEvaluation:
    """Return a deliberately conservative no-op evaluation.

    Used when ANTHROPIC_API_KEY is unset, the SDK isn't installed, or the
    model returns garbage. Routes the candidate to ``research_more`` so a
    human still reviews it — the scout never silently rejects.
    """
    return StrategyEvaluation(
        candidate=candidate,
        implementability_score=0.5,
        overfit_risk_score=0.5,
        lookahead_risk_score=0.5,
        novelty_score=0.5,
        expected_sharpe_range=(0.0, 0.0),
        pros=[],
        cons=[],
        recommended_next_step="research_more",
        rationale=rationale,
        evaluated_at=datetime.combine(today, datetime.min.time(), tzinfo=UTC),
        model="stub",
    )


def _extract_text_from_message(message: Any) -> str:
    """Pull plain text out of an Anthropic SDK message response."""
    content = getattr(message, "content", None)
    if isinstance(content, list):
        return "".join(
            block.text for block in content if isinstance(getattr(block, "text", None), str)
        )
    if isinstance(content, str):
        return content
    return ""


def _coerce_str_list(value: Any) -> list[str]:
    """Return a clean list of strings from a possibly-malformed list value."""
    if not isinstance(value, list):
        return []
    return [str(x) for x in value if isinstance(x, str)]


def _coerce_sharpe_range(parsed: dict[str, Any]) -> tuple[float, float]:
    """Pull (sharpe_low, sharpe_high) from parsed JSON, swapping if inverted."""
    try:
        sl = float(parsed.get("sharpe_low", 0.0))
        sh = float(parsed.get("sharpe_high", 0.0))
    except (TypeError, ValueError):
        return 0.0, 0.0
    if sl > sh:
        sl, sh = sh, sl
    return sl, sh


def _coerce_next_step(value: Any) -> Literal["port_and_backtest", "research_more", "reject"]:
    candidate = str(value or "").strip().lower()
    if candidate in {"port_and_backtest", "research_more", "reject"}:
        return candidate  # type: ignore[return-value]
    return "research_more"


def _build_evaluation_from_parsed(
    candidate: StrategyCandidate,
    parsed: dict[str, Any],
) -> StrategyEvaluation:
    """Map a parsed LLM JSON dict into a validated StrategyEvaluation."""
    return StrategyEvaluation(
        candidate=candidate,
        implementability_score=_clamp01(parsed.get("implementability")),
        overfit_risk_score=_clamp01(parsed.get("overfit_risk")),
        lookahead_risk_score=_clamp01(parsed.get("lookahead_risk")),
        novelty_score=_clamp01(parsed.get("novelty")),
        expected_sharpe_range=_coerce_sharpe_range(parsed),
        pros=_coerce_str_list(parsed.get("pros")),
        cons=_coerce_str_list(parsed.get("cons")),
        recommended_next_step=_coerce_next_step(parsed.get("next_step")),
        rationale=str(parsed.get("rationale", "")).strip(),
        evaluated_at=datetime.now(tz=UTC),
        model=_DEFAULT_MODEL,
    )


def _build_evaluation_prompt(candidate: StrategyCandidate, today: date) -> tuple[str, str]:
    """Build (system, user) prompts for LLM evaluation. Date-injected, ticker-anonymized."""
    system = (
        "You are a quantitative trading-strategy reviewer. "
        f"Today's date is {today.isoformat()}. "
        "You do not know what happens after this date. "
        "Score the proposed strategy across multiple risk dimensions. "
        "Be skeptical: most strategies on the public web are overfit, lookahead-leaked, "
        "or unimplementable as stated. Default toward conservative scores. "
        "Output strictly JSON: {\"implementability\":0..1, \"overfit_risk\":0..1, "
        "\"lookahead_risk\":0..1, \"novelty\":0..1, \"sharpe_low\":float, "
        "\"sharpe_high\":float, \"pros\":[str], \"cons\":[str], "
        "\"next_step\":\"port_and_backtest|research_more|reject\", \"rationale\":str}."
    )
    excerpt = _anonymize_tickers(candidate.raw_excerpt or candidate.summary)
    title = _anonymize_tickers(candidate.title)
    summary = _anonymize_tickers(candidate.summary)
    user = (
        f"Title: {title}\n"
        f"Source: {candidate.source}\n"
        f"Asset class hints: {','.join(candidate.asset_class)}\n"
        f"Timeframe hints: {','.join(candidate.timeframes)}\n"
        f"Indicators detected: {','.join(candidate.indicators_used)}\n"
        f"Rule complexity (heuristic): {candidate.rule_complexity}\n\n"
        f"Summary: {summary}\n\n"
        f"Excerpt:\n{excerpt[:_RAW_EXCERPT_CHARS]}"
    )
    return system, user


# -- Network seam (tests monkeypatch this) ---------------------------------------------


def _http_get_json(url: str, headers: dict[str, str]) -> dict[str, Any] | list[Any] | None:
    """Fetch a URL and decode JSON. Returns None on any failure.

    Tests monkeypatch this single seam to avoid real network calls.
    """
    req = urllib.request.Request(url, headers=headers)  # noqa: S310 — scheme guarded by safe_urlopen below
    try:
        with safe_urlopen(req, timeout=_HTTP_TIMEOUT_SEC) as resp:
            raw = resp.read()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None


# -- Scout ------------------------------------------------------------------------------


class StrategyScout:
    """Searches GitHub + web for trading strategy candidates and scores them.

    Hard rules:
      - Never executes a trade. Output is research only.
      - Respects all anti-bias rules from sentiment.py: ticker anonymization,
        date injection in prompts.
      - Reads at most ``max_candidates_per_query`` per scan to bound API costs.
      - All paths defensive: missing GITHUB_TOKEN / ANTHROPIC_API_KEY yields
        graceful fallbacks (empty results / neutral evaluations).
    """

    def __init__(
        self,
        github_token: str | None = None,
        anthropic_api_key: str | None = None,
        max_candidates_per_query: int = 25,
        user_agent: str = _DEFAULT_USER_AGENT,
    ) -> None:
        self._gh_token = (
            github_token if github_token is not None else os.environ.get("GITHUB_TOKEN", "")
        )
        self._anthropic_key = (
            anthropic_api_key
            if anthropic_api_key is not None
            else os.environ.get("ANTHROPIC_API_KEY", "")
        )
        if max_candidates_per_query <= 0:
            raise ValueError("max_candidates_per_query must be positive")
        self._max = max_candidates_per_query
        self._user_agent = user_agent

    # -- Discovery ----------------------------------------------------------------------

    def search_github(self, queries: list[str]) -> list[StrategyCandidate]:
        """Search GitHub repos for trading-strategy candidates.

        Endpoint: ``GET https://api.github.com/search/repositories``. Without a
        token we fall back to the unauthenticated rate limit (60 req/hr); with
        a token we get 5,000 req/hr. Repos are deduped by URL and the result
        is capped at ``self._max``.

        Returns [] on any HTTP failure rather than raising — this is a
        research scout, not a critical-path component.
        """
        if not queries:
            return []

        headers = {
            "User-Agent": self._user_agent,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self._gh_token:
            headers["Authorization"] = f"Bearer {self._gh_token}"

        seen: set[str] = set()
        out: list[StrategyCandidate] = []

        per_query_cap = max(1, self._max)
        for raw_query in queries:
            q = raw_query.strip()
            if not q:
                continue
            params = {
                "q": f"{q} language:python",
                "sort": "stars",
                "order": "desc",
                "per_page": str(min(100, per_query_cap)),
            }
            url = f"{_GITHUB_SEARCH_URL}?{urllib.parse.urlencode(params)}"
            payload = _http_get_json(url, headers)
            if payload is None or not isinstance(payload, dict):
                continue
            items = payload.get("items")
            if not isinstance(items, list):
                continue

            for item in items:
                if len(out) >= self._max:
                    return out
                if not isinstance(item, dict):
                    continue
                repo_url = str(item.get("html_url") or "").strip()
                if not repo_url or repo_url in seen:
                    continue
                seen.add(repo_url)

                title = str(item.get("full_name") or item.get("name") or "").strip()
                description = str(item.get("description") or "").strip()
                topics_raw = item.get("topics")
                topics: list[str] = (
                    [str(t) for t in topics_raw if isinstance(t, str)]
                    if isinstance(topics_raw, list)
                    else []
                )

                excerpt = "\n".join([title, description, " ".join(topics)])[:_RAW_EXCERPT_CHARS]
                summary = description or f"GitHub repo {title} matched query '{q}'."

                out.append(
                    StrategyCandidate(
                        title=title or repo_url,
                        source="github",
                        url=repo_url,
                        summary=summary[:500],
                        asset_class=_detect_assets(excerpt),
                        timeframes=_detect_timeframes(excerpt),
                        indicators_used=_detect_indicators(excerpt),
                        rule_complexity=_classify_complexity(excerpt),
                        discovered_at=datetime.now(tz=UTC),
                        raw_excerpt=excerpt,
                    )
                )
        return out

    def search_web(self, queries: list[str]) -> list[StrategyCandidate]:
        """Optional web-search-based candidate discovery.

        v1: stub. Returns []. A real implementation needs a search API
        (Brave, Serper, Bing) which is out of scope for the no-keys default
        path. Operators can extend this method without touching the rest of
        the pipeline.
        """
        _ = queries  # silence unused-arg linters
        return []

    # -- Evaluation ---------------------------------------------------------------------

    def evaluate(self, candidate: StrategyCandidate, today: date) -> StrategyEvaluation:
        """LLM-score a candidate using Claude Haiku.

        The prompt asks for structured JSON. ``today`` is injected so the
        model can't claim future-knowledge advantage, and ticker symbols in
        the candidate text are anonymized.

        Falls back to a neutral ``StrategyEvaluation`` (scores 0.5 across the
        board, ``research_more``) when:
          - ANTHROPIC_API_KEY env var is unset
          - the ``anthropic`` SDK isn't installed
          - the API call raises
          - the response can't be parsed as JSON
        """
        if not self._anthropic_key:
            return _neutral_evaluation(candidate, today, "ANTHROPIC_API_KEY unset")

        try:
            import anthropic  # type: ignore[import-not-found]  # noqa: PLC0415
        except ImportError:
            return _neutral_evaluation(candidate, today, "anthropic SDK not installed")

        system_prompt, user_prompt = _build_evaluation_prompt(candidate, today)

        try:
            client = anthropic.Anthropic(api_key=self._anthropic_key)
            message = client.messages.create(
                model=_DEFAULT_MODEL,
                max_tokens=800,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
        except Exception:
            return _neutral_evaluation(candidate, today, "Anthropic API error")

        raw_text = _extract_text_from_message(message)
        parsed = _parse_response_text(raw_text)
        if parsed is None:
            return _neutral_evaluation(candidate, today, "LLM response not valid JSON")

        return _build_evaluation_from_parsed(candidate, parsed)

    # -- Output -------------------------------------------------------------------------

    def write_backlog(
        self,
        evaluations: list[StrategyEvaluation],
        output_path: Path,
    ) -> Path:
        """Append a new dated section to the markdown backlog.

        Sorted by ``priority_score`` (implementability minus overfit minus
        lookahead) so easy-to-port, low-suspicion candidates rank first.

        Atomic write: builds the new full content in memory, writes to a
        sibling temp file, fsyncs, then ``os.replace`` to the target. Same
        pattern as ``src.journal.writer``. If the target already exists, the
        new section is appended to its existing content.
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        existing = ""
        if output_path.exists():
            try:
                existing = output_path.read_text(encoding="utf-8")
            except OSError:
                existing = ""
        if not existing:
            existing = (
                "# Strategy research backlog\n\n"
                "This file is appended-to by `scripts/scout_strategies.py`. "
                "Each scan adds a section dated by UTC timestamp. Top of the "
                "section = highest priority candidates (high implementability, "
                "low overfit risk).\n"
            )

        section = _format_section(evaluations)
        new_content = existing.rstrip() + "\n\n" + section + "\n"

        tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as f:
            f.write(new_content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, output_path)
        return output_path


# -- Markdown formatter -----------------------------------------------------------------


def _md_escape(text: str) -> str:
    """Escape characters that would break a markdown table cell."""
    return text.replace("|", "\\|").replace("\n", " ").strip()


def _format_section(evaluations: list[StrategyEvaluation]) -> str:
    """Render one dated scan section as markdown."""
    now = datetime.now(tz=UTC)
    header = f"## Scan {now.strftime('%Y-%m-%d %H:%M UTC')}\n\n"

    sorted_evals = sorted(evaluations, key=lambda e: e.priority_score, reverse=True)

    if not sorted_evals:
        return header + "_No candidates scored in this scan._\n"

    table_lines = [
        "| Score | Title | Source | Asset | Sharpe est | Next step |",
        "|---|---|---|---|---|---|",
    ]
    for ev in sorted_evals:
        c = ev.candidate
        title = _md_escape(c.title)[:80]
        if c.url:
            title_cell = f"[{title}]({c.url})"
        else:
            title_cell = title
        asset = ",".join(c.asset_class) or "?"
        sl, sh = ev.expected_sharpe_range
        sharpe = "unknown" if sl == 0.0 and sh == 0.0 else f"{sl:.2f}-{sh:.2f}"
        row = (
            f"| {ev.priority_score:.2f} | {title_cell} | {c.source} | "
            f"{_md_escape(asset)} | {sharpe} | {ev.recommended_next_step} |"
        )
        table_lines.append(row)

    detail_lines: list[str] = ["", ""]
    for ev in sorted_evals:
        c = ev.candidate
        detail_lines.append(f"### Detail: {c.title}")
        detail_lines.append(f"- url: {c.url}")
        detail_lines.append(f"- source: {c.source}")
        detail_lines.append(f"- asset_class: {','.join(c.asset_class) or '?'}")
        detail_lines.append(f"- timeframes: {','.join(c.timeframes) or '?'}")
        detail_lines.append(f"- indicators: {','.join(c.indicators_used) or '?'}")
        detail_lines.append(f"- rule_complexity: {c.rule_complexity}")
        detail_lines.append(
            f"- scores: impl={ev.implementability_score:.2f} "
            f"overfit={ev.overfit_risk_score:.2f} "
            f"lookahead={ev.lookahead_risk_score:.2f} "
            f"novelty={ev.novelty_score:.2f}"
        )
        detail_lines.append(f"- model: {ev.model}")
        if ev.pros:
            detail_lines.append(f"- pros: {'; '.join(ev.pros)}")
        if ev.cons:
            detail_lines.append(f"- cons: {'; '.join(ev.cons)}")
        if ev.rationale:
            detail_lines.append(f"- rationale: {ev.rationale}")
        detail_lines.append("")

    return header + "\n".join(table_lines) + "\n" + "\n".join(detail_lines)
