---
name: chart-analysis
description: Renders a chart PNG from OHLCV and routes it to the chart-analyzer subagent. Use whenever chart patterns, support/resistance, trendlines, or "what does this chart look like" are mentioned.
---

# Chart-analysis pipeline

## Steps

1. **Render.** `uv run python -m src.signals.plot --symbol <SYM> --timeframe <1D|1H|...> --bars <N> --out /tmp/<SYM>_<TF>.png`.
2. **Hand off.** Invoke the `chart-analyzer` subagent with the PNG path, symbol, and timeframe.
3. **Capture output.** Persist the JSON it returns to the journal as `event:"chart_analysis"`.
4. **Do not act on the output here.** Strategy + risk decisions happen elsewhere.

## What the chart-analyzer returns

A JSON object with `trend`, `support[]`, `resistance[]`, `patterns[]`, `volume_note`, and `caveats`. Confidence is capped at `0.7` unless multiple signals align. Empty `patterns: []` is a valid answer — patterns are often absent.
