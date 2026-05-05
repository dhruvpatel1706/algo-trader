"""LLM-scored news sentiment with anti-bias hardening.

Anti-bias hardening (Reddit Deep90 pattern):
  1. Anonymize the ticker and the issuer's common name -> [ASSET_<id>] placeholder.
     The model cannot use prior knowledge of the specific company to bias the score.
  2. Inject the article's date into the system prompt and instruct the model that
     it knows nothing after that date. Prevents look-ahead leakage from training data.

When ANTHROPIC_API_KEY is unset (or the SDK isn't installed), score_article returns a
neutral stub so unit tests / dry runs never hit the network.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from src.data.news import NewsArticle

# Project-relative path for the company-aliases file (optional).
_ALIASES_PATH = Path(__file__).resolve().parents[2] / "data" / "company_aliases.yaml"

# Built-in fallback for the most common large-cap tickers. Kept tiny on purpose;
# callers can override via the YAML file.
_BUILTIN_ALIASES: dict[str, list[str]] = {
    "AAPL": ["Apple", "Apple Inc", "Apple Inc."],
    "MSFT": ["Microsoft", "Microsoft Corp", "Microsoft Corporation"],
    "GOOGL": ["Google", "Alphabet", "Alphabet Inc"],
    "GOOG": ["Google", "Alphabet", "Alphabet Inc"],
    "AMZN": ["Amazon", "Amazon.com", "Amazon.com Inc"],
    "META": ["Meta", "Meta Platforms", "Facebook"],
    "TSLA": ["Tesla", "Tesla Inc", "Tesla Motors"],
    "NVDA": ["Nvidia", "NVIDIA", "Nvidia Corp"],
    "NFLX": ["Netflix", "Netflix Inc"],
    "SPY": ["SPDR S&P 500", "S&P 500 ETF"],
}

_DEFAULT_MODEL = "claude-haiku-4-5-20251001"
_BODY_TRUNCATE_CHARS = 500


@dataclass(frozen=True, slots=True)
class SentimentScore:
    article_id: str
    score: float  # in [-1, 1]
    label: str  # "bearish" | "neutral" | "bullish"
    confidence: float
    model: str
    scored_at: datetime


def _load_aliases() -> dict[str, list[str]]:
    """Load company aliases from data/company_aliases.yaml if present, else builtins.

    YAML file is optional. We do not introduce a yaml dep — we parse the simple
    'TICKER: [name1, name2]' shape ourselves. Falls back to built-ins.
    """
    aliases = dict(_BUILTIN_ALIASES)
    if not _ALIASES_PATH.exists():
        return aliases
    try:
        text = _ALIASES_PATH.read_text(encoding="utf-8")
    except OSError:
        return aliases

    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        ticker, rhs = line.split(":", 1)
        ticker = ticker.strip().upper()
        rhs = rhs.strip()
        if rhs.startswith("[") and rhs.endswith("]"):
            rhs = rhs[1:-1]
        names = [n.strip().strip("\"'") for n in rhs.split(",")]
        names = [n for n in names if n]
        if ticker and names:
            aliases[ticker] = names
    return aliases


def _placeholder_for(ticker: str) -> str:
    """Stable per-ticker placeholder. Same ticker -> same placeholder across calls."""
    digest = hashlib.sha256(ticker.upper().encode("utf-8")).hexdigest()[:8]
    return f"[ASSET_{digest}]"


def anonymize_headline(headline: str, ticker: str) -> str:
    """Replace ticker symbol with [ASSET_<id>] placeholder.

    Also replaces common company names if known (read company_aliases.yaml -- small
    dict). Anti-bias hardening per Reddit Deep90 pattern.
    """
    placeholder = _placeholder_for(ticker)
    out = headline

    # Replace ticker as a whole word (avoid mangling e.g. "AAPL" inside "AAPLE").
    # Case-insensitive whole-word substitution without regex import gymnastics.
    for variant in {ticker, ticker.upper(), ticker.lower()}:
        # Strip standalone occurrences first using surrounding-char check.
        i = 0
        while True:
            idx = out.lower().find(variant.lower(), i)
            if idx < 0:
                break
            left_ok = idx == 0 or not out[idx - 1].isalnum()
            right_idx = idx + len(variant)
            right_ok = right_idx == len(out) or not out[right_idx].isalnum()
            if left_ok and right_ok:
                out = out[:idx] + placeholder + out[right_idx:]
                i = idx + len(placeholder)
            else:
                i = idx + len(variant)

    aliases = _load_aliases().get(ticker.upper(), [])
    # Replace longer aliases first so "Apple Inc." wins over "Apple".
    for name in sorted(aliases, key=len, reverse=True):
        if not name:
            continue
        i = 0
        while True:
            idx = out.lower().find(name.lower(), i)
            if idx < 0:
                break
            left_ok = idx == 0 or not out[idx - 1].isalnum()
            right_idx = idx + len(name)
            right_ok = right_idx == len(out) or not out[right_idx].isalnum()
            if left_ok and right_ok:
                out = out[:idx] + placeholder + out[right_idx:]
                i = idx + len(placeholder)
            else:
                i = idx + len(name)

    return out


def _label_from_score(score: float) -> str:
    if score >= 0.2:
        return "bullish"
    if score <= -0.2:
        return "bearish"
    return "neutral"


def _stub_score(article_id: str) -> SentimentScore:
    return SentimentScore(
        article_id=article_id,
        score=0.0,
        label="neutral",
        confidence=0.0,
        model="stub",
        scored_at=datetime.now(tz=UTC),
    )


def _build_prompt(article: NewsArticle, today: date) -> tuple[str, str]:
    """Return (system_prompt, user_prompt). Both anonymized; date injected."""
    system = (
        "You are a financial news sentiment scorer. "
        f"Today's date is {today.isoformat()}. "
        "You do not know what happens after this date. "
        "Output JSON: {score: -1..1, label: bullish|neutral|bearish, confidence: 0..1, "
        "reasoning: short}."
    )
    anon_headline = anonymize_headline(article.headline, article.ticker)
    anon_body = anonymize_headline(article.body[:_BODY_TRUNCATE_CHARS], article.ticker)
    user = f"Headline: {anon_headline}\n\nBody: {anon_body}"
    return system, user


def _parse_response_text(text: str) -> dict[str, Any] | None:
    """Permissive JSON parse: tolerates fenced code blocks and prose around JSON."""
    candidate = text.strip()
    # Strip optional fenced block.
    if candidate.startswith("```"):
        candidate = candidate.strip("`")
        if candidate.lower().startswith("json"):
            candidate = candidate[4:]
        candidate = candidate.strip()
    # Find the first '{' and last '}' if surrounded by prose.
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


def score_article(
    article: NewsArticle,
    today: date,
    model: str = _DEFAULT_MODEL,
) -> SentimentScore:
    """Score a single article using the Anthropic API (Haiku 4.5 by default).

    Build the prompt:
      - System: "You are a financial news sentiment scorer. Today's date is {today}.
                You do not know what happens after this date. Output JSON: ..."
      - User: Anonymized headline + first 500 chars of body

    Cache strategy: caller is responsible (Redis). This function just calls.

    If ANTHROPIC_API_KEY env var is unset, return a neutral score=0 with model="stub"
    (graceful no-op for testing).
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return _stub_score(article.id)

    try:
        import anthropic  # type: ignore[import-not-found]  # noqa: PLC0415
    except ImportError:
        return _stub_score(article.id)

    system_prompt, user_prompt = _build_prompt(article, today)

    try:
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model=model,
            max_tokens=400,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
    except Exception:
        return _stub_score(article.id)

    # Extract text content from the response.
    raw_text = ""
    content = getattr(message, "content", None)
    if isinstance(content, list):
        for block in content:
            text = getattr(block, "text", None)
            if isinstance(text, str):
                raw_text += text
    elif isinstance(content, str):
        raw_text = content

    parsed = _parse_response_text(raw_text)
    if parsed is None:
        return _stub_score(article.id)

    try:
        score = float(parsed.get("score", 0.0))
    except (TypeError, ValueError):
        score = 0.0
    score = max(-1.0, min(1.0, score))

    try:
        confidence = float(parsed.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    label = str(parsed.get("label", "")).strip().lower()
    if label not in {"bullish", "neutral", "bearish"}:
        label = _label_from_score(score)

    return SentimentScore(
        article_id=article.id,
        score=score,
        label=label,
        confidence=confidence,
        model=model,
        scored_at=datetime.now(tz=UTC),
    )
