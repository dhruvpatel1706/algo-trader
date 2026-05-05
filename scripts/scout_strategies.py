#!/usr/bin/env python3
"""Strategy research scout CLI.

Usage:
  uv run python scripts/scout_strategies.py --queries "mean reversion,momentum,RSI" --max 25
  uv run python scripts/scout_strategies.py --from-config docs/scout_queries.yaml
  uv run python scripts/scout_strategies.py --dry-run    # just list what would be scanned

Defaults to writing docs/research_backlog.md.

Exit codes:
  0  success (always — even with 0 candidates found, this is a research script not a gate)
  1  config error (bad arguments, missing yaml, etc.)

Safety
------
This script discovers and scores strategy candidates only. It NEVER places a
trade, NEVER touches the broker, and writes nothing besides the markdown
backlog. It is safe to run on a production machine.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

# Resolve the project root (scripts/ -> repo root).
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.research.strategy_scout import StrategyScout  # noqa: E402

_DEFAULT_OUTPUT = _PROJECT_ROOT / "docs" / "research_backlog.md"
_DEFAULT_CONFIG = _PROJECT_ROOT / "docs" / "scout_queries.yaml"


def _parse_simple_yaml(path: Path) -> dict[str, list[str]]:
    """Parse the trivial 'group: [items]' YAML shape used by scout_queries.yaml.

    Avoids adding a yaml dep — same approach as src.data.sentiment._load_aliases.
    Format:
        group_name:
          - "first query"
          - "second query"
        another_group:
          - "..."
    """
    out: dict[str, list[str]] = {}
    if not path.exists():
        raise FileNotFoundError(f"config file not found: {path}")
    text = path.read_text(encoding="utf-8")

    current: str | None = None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if not line.startswith((" ", "\t")) and line.rstrip().endswith(":"):
            current = line.strip().rstrip(":").strip()
            out.setdefault(current, [])
            continue
        stripped = line.strip()
        if stripped.startswith("- "):
            value = stripped[2:].strip().strip("\"'")
            if value and current is not None:
                out[current].append(value)
    return out


def _flatten_queries(grouped: dict[str, list[str]]) -> list[str]:
    flat: list[str] = []
    for items in grouped.values():
        flat.extend(items)
    return flat


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="scout_strategies",
        description=(
            "Scout GitHub + web for trading-strategy candidates and produce a "
            "research backlog. Never executes trades."
        ),
    )
    parser.add_argument(
        "--queries",
        type=str,
        default=None,
        help="Comma-separated query strings. Overrides --from-config.",
    )
    parser.add_argument(
        "--from-config",
        type=str,
        default=str(_DEFAULT_CONFIG),
        help=f"YAML file with grouped queries (default: {_DEFAULT_CONFIG.relative_to(_PROJECT_ROOT)})",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=str(_DEFAULT_OUTPUT),
        help=f"Output markdown path (default: {_DEFAULT_OUTPUT.relative_to(_PROJECT_ROOT)})",
    )
    parser.add_argument(
        "--max",
        type=int,
        default=25,
        help="Max candidates per query (cost cap). Default 25.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print queries and config; make no network calls; exit 0.",
    )
    return parser.parse_args(argv)


def _resolve_queries(args: argparse.Namespace) -> list[str]:
    if args.queries:
        return [q.strip() for q in args.queries.split(",") if q.strip()]
    config_path = Path(args.from_config)
    grouped = _parse_simple_yaml(config_path)
    return _flatten_queries(grouped)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    try:
        queries = _resolve_queries(args)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if not queries:
        print("error: no queries to run (empty --queries and empty config)", file=sys.stderr)
        return 1

    if args.dry_run:
        print("DRY RUN — no network calls, no LLM calls, no file writes.")
        print(f"Output target  : {args.output}")
        print(f"Max per query  : {args.max}")
        print(f"Query count    : {len(queries)}")
        for q in queries:
            print(f"  - {q}")
        return 0

    scout = StrategyScout(max_candidates_per_query=args.max)
    print(f"Scanning {len(queries)} queries via GitHub...")

    candidates = scout.search_github(queries)
    candidates += scout.search_web(queries)
    print(f"Found {len(candidates)} candidates (after dedup).")

    today = datetime.now(tz=UTC).date()
    evaluations = [scout.evaluate(c, today) for c in candidates]
    print(f"Evaluated {len(evaluations)} candidates.")

    out_path = Path(args.output)
    written = scout.write_backlog(evaluations, out_path)
    print(f"Wrote backlog: {written}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
