"""Macro deal extractor.

Detects high-impact business deals like "NVIDIA invests $1B in Nokia for AI infra".

The extractor is intentionally CONSERVATIVE: in a trading context, false
positives (acting on a deal that did not actually happen at the size we think)
are far worse than false negatives (missing a deal). Defenses:

  1. The LLM prompt explicitly requires a CONCRETE financial commitment with a
     specific dollar amount or a clearly-named strategic transaction. Vague
     language ("could", "may", "is reportedly considering") must yield
     ``is_deal=false``.
  2. The model returns its own ``confidence`` in [0, 1]; we filter to
     ``confidence >= 0.6``.
  3. Amounts below ``min_amount_usd`` (default $100M) are dropped.
  4. The amount parser only accepts a small set of well-formed shapes ("$1B",
     "$500M", "1.5 billion"). Anything ambiguous returns ``None`` and the
     candidate is dropped.

If ``ANTHROPIC_API_KEY`` is unset (or the SDK is not installed),
``extract_macro_deals`` returns ``[]`` -- never raises.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Literal

from src.data.news import NewsArticle

_DEFAULT_MODEL = "claude-haiku-4-5-20251001"
_BODY_TRUNCATE_CHARS = 1200
_MIN_CONFIDENCE = 0.6
_DEFAULT_MIN_AMOUNT_USD = 100_000_000.0

DealType = Literal["investment", "acquisition", "partnership", "spinoff", "joint_venture"]
_VALID_DEAL_TYPES: frozenset[str] = frozenset(
    ("investment", "acquisition", "partnership", "spinoff", "joint_venture")
)


@dataclass(frozen=True, slots=True)
class MacroDeal:
    """Structured representation of a high-impact business event."""

    article_id: str
    deal_type: DealType
    actor: str
    target: str
    amount_usd: float | None
    purpose: str
    confidence: float
    extracted_at: datetime
    actor_tickers: list[str]
    target_tickers: list[str]


# ---------------------------------------------------------------------------
# Amount parsing
# ---------------------------------------------------------------------------


_NUM_PATTERN = re.compile(
    r"""
    \$?\s*                          # optional currency
    (?P<num>\d+(?:\.\d+)?)          # 1 or 1.5
    \s*
    (?P<unit>billion|bn|b|million|mn|m|thousand|k)?
    """,
    re.IGNORECASE | re.VERBOSE,
)

_UNIT_MULT: dict[str, float] = {
    "billion": 1_000_000_000.0,
    "bn": 1_000_000_000.0,
    "b": 1_000_000_000.0,
    "million": 1_000_000.0,
    "mn": 1_000_000.0,
    "m": 1_000_000.0,
    "thousand": 1_000.0,
    "k": 1_000.0,
}


def _parse_amount(raw: object) -> float | None:
    """Parse a deal amount into USD.

    Accepts:
      * ``int`` / ``float``: interpreted as USD (already normalized).
      * ``"$1B"``, ``"$500M"``, ``"1.5 billion"``, ``"2.0bn"`` etc.

    Returns ``None`` for anything that cannot be parsed unambiguously.
    """
    if raw is None:
        return None
    if isinstance(raw, bool):
        # bool is a subclass of int -- exclude explicitly.
        return None
    if isinstance(raw, (int, float)):
        return float(raw) if raw > 0 else None
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    m = _NUM_PATTERN.search(text)
    if not m:
        return None
    try:
        n = float(m.group("num"))
    except ValueError:
        return None
    unit = (m.group("unit") or "").lower()
    mult = _UNIT_MULT.get(unit, 1.0)
    out = n * mult
    return out if out > 0 else None


# ---------------------------------------------------------------------------
# Prompting
# ---------------------------------------------------------------------------


_SYSTEM_PROMPT_TEMPLATE = """You are a conservative financial-deal extractor.

Today's date is {today}. You do not know what happens after this date.

Read a news article and decide whether it describes a CONCRETE financial commitment \
or strategic transaction between two named entities. Examples that QUALIFY:
  - "NVIDIA invests $1 billion in Nokia for AI infrastructure" (investment, $1B)
  - "Microsoft agrees to acquire Activision for $68.7B" (acquisition, $68.7B)
  - "Pfizer and BioNTech form joint venture to develop mRNA cancer vaccines" \
(joint_venture, amount may be unspecified)
  - "Amazon to spin off AWS into separate publicly-traded entity" (spinoff)

Examples that DO NOT qualify (return is_deal=false):
  - "Tesla shares rise on speculation about potential Mexico plant"
  - "Analyst raises price target on Apple"
  - "CEO discusses possible future expansion into Europe"
  - "Company reports strong quarterly earnings" (operations, not a deal)
  - "Rumors swirl about possible takeover" (no concrete commitment)
  - "Shareholders vote on previously announced merger" (no NEW deal)

Be CONSERVATIVE. False positives are far worse than false negatives. Only return \
is_deal=true if the article describes a CONCRETE financial commitment with a \
specific dollar amount or clear strategic transaction.

Output strict JSON with these keys (no prose, no fences):
  is_deal: bool
  deal_type: one of "investment" | "acquisition" | "partnership" | "spinoff" | \
"joint_venture" (use the closest fit; required when is_deal=true)
  actor: string (the entity initiating the action)
  target: string (the entity receiving the investment / being acquired)
  amount_usd_billions: float or null (deal size in BILLIONS of USD; null if unspecified)
  purpose: short phrase describing the strategic purpose
  confidence: float in [0, 1] (your confidence the article describes a real concrete deal)
  actor_tickers: list of likely ticker symbols for the actor (best-effort, may be empty)
  target_tickers: list of likely ticker symbols for the target (best-effort, may be empty)
"""


def _build_prompt(article: NewsArticle, today: date) -> tuple[str, str]:
    system = _SYSTEM_PROMPT_TEMPLATE.format(today=today.isoformat())
    truncated_body = article.body[:_BODY_TRUNCATE_CHARS]
    user = f"Headline: {article.headline}\n\nBody: {truncated_body}"
    return system, user


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


def _parse_response_text(text: str) -> dict[str, Any] | None:
    """Permissive JSON parse: tolerates fenced code blocks and prose around JSON."""
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


def _coerce_str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(x).strip().upper() for x in value if isinstance(x, str) and x.strip()]


def _build_deal(
    article_id: str,
    parsed: dict[str, Any],
    *,
    extracted_at: datetime,
) -> MacroDeal | None:
    """Validate + build a ``MacroDeal``. Returns ``None`` if validation fails."""
    if not bool(parsed.get("is_deal")):
        return None

    deal_type_raw = str(parsed.get("deal_type") or "").strip().lower().replace(" ", "_")
    if deal_type_raw not in _VALID_DEAL_TYPES:
        return None

    actor = str(parsed.get("actor") or "").strip()
    target = str(parsed.get("target") or "").strip()
    if not actor or not target:
        return None

    # Amount: prefer "amount_usd_billions" (the prompt-mandated field), fall
    # back to "amount_usd" if a model returns absolute dollars by mistake.
    amount: float | None = None
    if "amount_usd_billions" in parsed:
        billions = parsed.get("amount_usd_billions")
        if billions is not None:
            try:
                bv = float(billions)
            except (TypeError, ValueError):
                bv = 0.0
            if bv > 0:
                amount = bv * 1_000_000_000.0
    if amount is None and "amount_usd" in parsed:
        amount = _parse_amount(parsed.get("amount_usd"))

    purpose = str(parsed.get("purpose") or "").strip()

    try:
        confidence = float(parsed.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    actor_tickers = _coerce_str_list(parsed.get("actor_tickers"))
    target_tickers = _coerce_str_list(parsed.get("target_tickers"))

    return MacroDeal(
        article_id=article_id,
        deal_type=deal_type_raw,  # type: ignore[arg-type]
        actor=actor,
        target=target,
        amount_usd=amount,
        purpose=purpose,
        confidence=confidence,
        extracted_at=extracted_at,
        actor_tickers=actor_tickers,
        target_tickers=target_tickers,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _extract_text_from_message(message: object) -> str:
    """Pull the text content out of an anthropic ``Message`` defensively."""
    content = getattr(message, "content", None)
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            text = getattr(block, "text", None)
            if isinstance(text, str):
                parts.append(text)
        return "".join(parts)
    if isinstance(content, str):
        return content
    return ""


def _passes_filters(deal: MacroDeal, *, min_amount_usd: float) -> bool:
    if deal.confidence < _MIN_CONFIDENCE:
        return False
    if min_amount_usd > 0:
        if deal.amount_usd is None or deal.amount_usd < min_amount_usd:
            return False
    return True


def _extract_one(
    client: object,
    article: NewsArticle,
    *,
    today: date,
    model: str,
    extracted_at: datetime,
) -> MacroDeal | None:
    """Process a single article. Returns the validated deal or ``None``."""
    system_prompt, user_prompt = _build_prompt(article, today)
    try:
        message = client.messages.create(  # type: ignore[attr-defined]
            model=model,
            max_tokens=600,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
    except Exception:
        # Network / rate-limit / SDK glitch. We deliberately swallow and skip
        # this article rather than fail the whole batch -- false negatives
        # are acceptable in the conservative-by-design extractor.
        return None

    raw_text = _extract_text_from_message(message)
    parsed = _parse_response_text(raw_text)
    if parsed is None:
        return None

    return _build_deal(article.id, parsed, extracted_at=extracted_at)


def extract_macro_deals(
    articles: list[NewsArticle],
    today: date,
    model: str = _DEFAULT_MODEL,
    min_amount_usd: float = _DEFAULT_MIN_AMOUNT_USD,
) -> list[MacroDeal]:
    """Run each article through Claude Haiku with a structured-output prompt.

    Filters to deals where the model says ``is_deal=true``, ``confidence >= 0.6``,
    and ``amount_usd >= min_amount_usd`` (deals with no parsed amount are dropped
    when ``min_amount_usd > 0``).

    If ``ANTHROPIC_API_KEY`` is unset, returns ``[]``.
    """
    if not articles:
        return []

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return []

    try:
        import anthropic  # type: ignore[import-not-found]  # noqa: PLC0415
    except ImportError:
        return []

    try:
        client = anthropic.Anthropic(api_key=api_key)
    except Exception:
        return []

    out: list[MacroDeal] = []
    extracted_at = datetime.now(tz=UTC)

    for article in articles:
        deal = _extract_one(
            client,
            article,
            today=today,
            model=model,
            extracted_at=extracted_at,
        )
        if deal is None:
            continue
        if not _passes_filters(deal, min_amount_usd=min_amount_usd):
            continue
        out.append(deal)

    return out
