"""Orchestrator state endpoints.

GET /api/orchestrator/state — read-only view of all multi-Claude session states.
Returns staleness, lock status, brief excerpt, and latest artifact path for each role.

GET /api/orchestrator/research_proposals — reads the researcher session's brief
and surfaces proposed strategies (with implementation status) plus the current
watchlist + trigger conditions. Lets the operator see what the parallel
researcher session is recommending without context-switching to a worktree.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel
from src.config import PROJECT_ROOT

log = logging.getLogger(__name__)

router = APIRouter()

ORCHESTRATOR_DIR = PROJECT_ROOT / "live" / "orchestrator"

# Seconds between expected writes for each role.
_CADENCE: dict[str, int] = {
    "watcher": 15 * 60,
    "researcher": 4 * 60 * 60,
    "backtester": 24 * 60 * 60,
    "improver": 7 * 24 * 60 * 60,
    "operator": 4 * 60 * 60,
}

# Role → subdirectory where that session writes its primary output.
_ROLE_DIR: dict[str, str] = {
    "watcher": "watcher",
    "researcher": "research",
    "backtester": "backtests",
    "improver": "improver",
    "operator": "handoff/operator",
}

ROLES = list(_CADENCE)


class RoleState(BaseModel):
    last_update_iso: str | None
    lock_held: bool
    lock_pid: int | None
    latest_verdict_path: str | None
    brief_excerpt: str | None
    staleness: str  # "fresh" | "warn" | "stale"


class OrchestratorStateResponse(BaseModel):
    roles: dict[str, RoleState]
    as_of: str


def _dir_latest_mtime(directory: Path) -> datetime | None:
    if not directory.exists():
        return None
    mtimes = [
        datetime.fromtimestamp(f.stat().st_mtime, tz=UTC)
        for f in directory.iterdir()
        if f.is_file()
    ]
    return max(mtimes, default=None)


def _staleness(last: datetime | None, cadence: int, now: datetime) -> str:
    if last is None:
        return "stale"
    age = (now - last).total_seconds()
    if age <= cadence:
        return "fresh"
    if age <= cadence * 2:
        return "warn"
    return "stale"


def _latest_file(directory: Path) -> str | None:
    if not directory.exists():
        return None
    files = sorted(
        (f for f in directory.iterdir() if f.is_file()),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )
    return str(files[0]) if files else None


def _brief_excerpt(role: str) -> str | None:
    """Read the role's handoff brief, with a top-level fallback.

    Canonical path: ``live/orchestrator/handoff/<role>/brief.md``.
    Fallback path: ``live/orchestrator/<role>_brief.{md,json}`` —
    permissive on read because some session implementations don't use
    the ``src.orchestrator.handoff`` primitives and write to the top
    level instead. Excerpt format adapts: markdown shows the first 200
    characters; JSON is summarized to a one-line key:value digest.
    """
    md = ORCHESTRATOR_DIR / "handoff" / role / "brief.md"
    if md.exists():
        try:
            text = md.read_text()
            excerpt = text[:200].strip()
            return excerpt or None
        except OSError:
            pass

    fallback_md = ORCHESTRATOR_DIR / f"{role}_brief.md"
    if fallback_md.exists():
        try:
            text = fallback_md.read_text()
            excerpt = text[:200].strip()
            return excerpt or None
        except OSError:
            pass

    fallback_json = ORCHESTRATOR_DIR / f"{role}_brief.json"
    if fallback_json.exists():
        try:
            data = json.loads(fallback_json.read_text())
        except (json.JSONDecodeError, OSError):
            return None
        if not isinstance(data, dict):
            return None
        # Summarize a few well-known top-level keys; fall through to the
        # raw notes / first-string-value for unknown shapes.
        for key in ("notes", "summary", "tldr", "headline"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:200]
        # No known summary key — show last_run + a short shape preview.
        last_run = data.get("last_run_utc") or data.get("ts") or ""
        keys = ", ".join(list(data.keys())[:6])
        excerpt = f"{last_run} | keys: {keys}".strip(" |")
        return excerpt[:200] or None
    return None


def _lock_info(role: str) -> tuple[bool, int | None]:
    """Check both canonical and top-level lock paths.

    Canonical:  ``live/orchestrator/locks/<role>.lock``
    Fallback:   ``live/orchestrator/lock_<role>.json``

    Both shapes contain JSON ``{pid, acquired_at OR started_at, role, ...}``.
    Returns held=True only when the JSON parses AND the recorded pid is
    still alive; otherwise False (so a stale lock file from a crashed
    session doesn't leave us reporting "held" forever).
    """
    candidates = [
        ORCHESTRATOR_DIR / "locks" / f"{role}.lock",
        ORCHESTRATOR_DIR / f"lock_{role}.json",
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            data: dict = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        pid = data.get("pid")
        if not isinstance(pid, int):
            continue
        # Verify pid is still alive — stale locks (process died, file
        # not cleaned up) shouldn't report as held.
        try:
            import os  # noqa: PLC0415

            os.kill(pid, 0)
        except (ProcessLookupError, PermissionError, OSError):
            continue
        return True, pid
    return False, None


class ProposalEntry(BaseModel):
    rank: int
    filename: str
    slug: str  # filename without .md
    title: str  # human-friendly title (slug humanised)
    rationale: str  # the researcher's one-line justification
    status: str  # "implemented" | "proposed"
    impl_path: str | None  # path to src/strategies/<slug>.py if implemented


class WatchlistEntry(BaseModel):
    symbol: str
    confluence: float | None
    direction: str | None
    rsi: float | None
    adx: float | None
    bb_pct_b: float | None
    trigger: str | None  # human-readable trigger condition for next session


class ResearchProposalsResponse(BaseModel):
    last_run_iso: str | None
    next_run_iso: str | None
    regime: str | None
    threshold: float | None
    notes: str | None
    proposals: list[ProposalEntry]
    watchlist: list[WatchlistEntry]
    top_confluence: list[WatchlistEntry]
    data_source: str | None
    funding_status: str | None


# Common suffixes the researcher tags onto proposal filenames that don't end
# up in the implemented module name (e.g. "ema_ribbon_compression_breakout.md"
# → src/strategies/ema_ribbon_compression.py).
_PROPOSAL_SUFFIX_STRIP = ("_breakout", "_strategy", "_signal")


def _proposal_slug_to_strategy_file(slug: str) -> Path | None:
    """Return src/strategies/<slug>.py if the proposal is implemented.

    Tries the exact slug first, then progressively strips known suffixes —
    proposals like 'ema_ribbon_compression_breakout.md' map to
    'src/strategies/ema_ribbon_compression.py'.
    """
    candidates = [slug]
    for suffix in _PROPOSAL_SUFFIX_STRIP:
        if slug.endswith(suffix):
            candidates.append(slug[: -len(suffix)])
    for cand in candidates:
        candidate_path = PROJECT_ROOT / "src" / "strategies" / f"{cand}.py"
        if candidate_path.exists():
            return candidate_path
    return None


def _humanise_slug(slug: str) -> str:
    return slug.replace("_", " ").strip().title()


def _read_researcher_brief() -> dict | None:
    """Read whichever researcher brief shape exists (canonical or top-level).

    Returns the parsed JSON dict, or None if neither path is present or
    parseable. Permissive on read by design — the researcher session
    sometimes writes top-level for quick iteration.
    """
    candidates = [
        ORCHESTRATOR_DIR / "handoff" / "researcher" / "brief.json",
        ORCHESTRATOR_DIR / "researcher_brief.json",
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("could not parse researcher brief at %s: %s", path, exc)
            continue
        if isinstance(data, dict):
            return data
    return None


@router.get(
    "/api/orchestrator/research_proposals",
    response_model=ResearchProposalsResponse,
)
async def orchestrator_research_proposals() -> ResearchProposalsResponse:
    """Surface researcher session's proposals + current watchlist.

    Reads the researcher_brief.json the parallel session writes and
    annotates each proposed strategy with its implementation status (does
    src/strategies/<slug>.py exist?). Watchlist comes from
    market_observations.trigger_conditions joined with the per-symbol
    indicators in market_observations.key_levels. Top confluence comes
    from scan_results_ranked.
    """
    brief = _read_researcher_brief()
    if brief is None:
        return ResearchProposalsResponse(
            last_run_iso=None,
            next_run_iso=None,
            regime=None,
            threshold=None,
            notes=None,
            proposals=[],
            watchlist=[],
            top_confluence=[],
            data_source=None,
            funding_status=None,
        )

    proposals = _parse_proposals(brief.get("new_strategy_proposals") or {})
    market_obs = brief.get("market_observations") or {}
    watchlist = _parse_watchlist(market_obs)
    top_confluence = _parse_top_confluence(brief.get("scan_results_ranked") or [])

    return ResearchProposalsResponse(
        last_run_iso=brief.get("last_run_utc") or brief.get("ts"),
        next_run_iso=brief.get("next_run_scheduled_utc"),
        regime=_str_or_none(market_obs.get("regime")),
        threshold=_as_float(brief.get("threshold")),
        notes=_str_or_none(brief.get("notes")),
        proposals=proposals,
        watchlist=watchlist,
        top_confluence=top_confluence,
        data_source=_str_or_none(brief.get("data_source")),
        funding_status=_str_or_none(brief.get("funding_data")),
    )


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _parse_proposals(proposals_raw: dict) -> list[ProposalEntry]:
    """Parse priority_order strings into ProposalEntry list.

    Researcher writes lines like:
      "1. ema_ribbon_compression_breakout - directly backtestable"
    The dashes can be ASCII '-' or em/en dashes; strip the rank prefix
    first, then split at the first separator we find.
    """
    priority_order = proposals_raw.get("priority_order") or []
    proposals: list[ProposalEntry] = []
    for rank, line in enumerate(priority_order, start=1):
        if not isinstance(line, str):
            continue
        cleaned = line.lstrip()
        for sep in (". ", ") "):
            if cleaned[:3].rstrip().endswith(sep.strip()):
                cleaned = cleaned.split(sep, 1)[-1]
                break
        slug, rationale = _split_slug_rationale(cleaned)
        if not slug:
            continue
        impl_file = _proposal_slug_to_strategy_file(slug)
        proposals.append(
            ProposalEntry(
                rank=rank,
                filename=f"{slug}.md",
                slug=slug,
                title=_humanise_slug(slug),
                rationale=rationale or "(no rationale)",
                status="implemented" if impl_file else "proposed",
                impl_path=str(impl_file) if impl_file else None,
            )
        )
    return proposals


def _split_slug_rationale(cleaned: str) -> tuple[str, str]:
    """Split a researcher proposal line into (slug, rationale) at the first
    em/en-dash or hyphen-with-spaces. Returns ('', '') if cleaned is empty."""
    # Note: separators include ASCII hyphen, EN DASH, and EM DASH — all valid
    # in researcher prose. ruff RUF001 flags non-ASCII so we noqa explicitly.
    separators = (" — ", " – ", " - ")  # em-dash, en-dash, hyphen  # noqa: RUF001
    for sep in separators:
        if sep in cleaned:
            slug, rationale = cleaned.split(sep, 1)
            return slug.strip(), rationale.strip()
    return cleaned.strip(), ""


def _parse_watchlist(market_obs: dict) -> list[WatchlistEntry]:
    key_levels = market_obs.get("key_levels") or {}
    trigger_conditions = market_obs.get("trigger_conditions") or {}
    watchlist_symbols = market_obs.get("watchlist_for_next_session") or []
    watchlist: list[WatchlistEntry] = []
    for sym in watchlist_symbols:
        if not isinstance(sym, str):
            continue
        levels = key_levels.get(sym) or {}
        watchlist.append(
            WatchlistEntry(
                symbol=sym,
                confluence=None,
                direction=None,
                rsi=_as_float(levels.get("rsi")),
                adx=_as_float(levels.get("adx")),
                bb_pct_b=_as_float(levels.get("bb_pct_b")),
                trigger=trigger_conditions.get(sym),
            )
        )
    return watchlist


def _parse_top_confluence(scan_results: list) -> list[WatchlistEntry]:
    out: list[WatchlistEntry] = []
    for entry in scan_results[:5]:
        if not isinstance(entry, dict):
            continue
        sym = entry.get("symbol")
        if not isinstance(sym, str):
            continue
        out.append(
            WatchlistEntry(
                symbol=sym,
                confluence=_as_float(entry.get("confluence")),
                direction=_str_or_none(entry.get("direction")),
                rsi=_as_float(entry.get("rsi")),
                adx=_as_float(entry.get("adx")),
                bb_pct_b=_as_float(entry.get("bb_pct_b")),
                trigger=None,
            )
        )
    return out


def _as_float(value: object) -> float | None:
    """Coerce a JSON value to float; return None on missing or unparseable."""
    if value is None:
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


@router.get("/api/orchestrator/state", response_model=OrchestratorStateResponse)
async def orchestrator_state() -> OrchestratorStateResponse:
    """Read-only snapshot of all orchestrator session states."""
    now = datetime.now(UTC)
    roles: dict[str, RoleState] = {}

    for role in ROLES:
        output_dir = ORCHESTRATOR_DIR / _ROLE_DIR[role]
        handoff_dir = ORCHESTRATOR_DIR / "handoff" / role

        output_mtime = _dir_latest_mtime(output_dir)
        handoff_mtime = _dir_latest_mtime(handoff_dir)
        # Top-level fallbacks (out-of-spec session outputs).
        fallback_mtime: datetime | None = None
        for tail in (f"{role}_brief.md", f"{role}_brief.json", f"lock_{role}.json"):
            f = ORCHESTRATOR_DIR / tail
            if f.exists():
                ts = datetime.fromtimestamp(f.stat().st_mtime, tz=UTC)
                fallback_mtime = max(fallback_mtime, ts) if fallback_mtime else ts
        candidates = [
            t
            for t in (output_mtime, handoff_mtime, fallback_mtime)
            if t is not None
        ]
        last_update = max(candidates) if candidates else None

        lock_held, lock_pid = _lock_info(role)

        verdict_path = _latest_file(output_dir)
        if verdict_path is None:
            # Surface the top-level fallback file path when no canonical
            # output exists, so the dashboard can still link to it.
            for tail in (f"{role}_brief.md", f"{role}_brief.json"):
                f = ORCHESTRATOR_DIR / tail
                if f.exists():
                    verdict_path = str(f)
                    break

        roles[role] = RoleState(
            last_update_iso=last_update.isoformat() if last_update else None,
            lock_held=lock_held,
            lock_pid=lock_pid,
            latest_verdict_path=verdict_path,
            brief_excerpt=_brief_excerpt(role),
            staleness=_staleness(last_update, _CADENCE[role], now),
        )

    return OrchestratorStateResponse(roles=roles, as_of=now.isoformat())
