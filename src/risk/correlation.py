"""Correlation-aware sizing penalty.

Why: when several positions are highly correlated, sizing each at the same
risk-of-equity overstates portfolio diversification. A "fresh" symbol that
moves nearly 1:1 with what is already open is effectively a clone — adding
it concentrates risk rather than spreading it. This module provides a
multiplicative scale factor in [0.0, 1.0] to attenuate (or block) entries
into instruments that are highly correlated with the current book.

The penalty is a simple piecewise-linear map of the mean pairwise correlation
between the candidate symbol and each of the already-open symbols:

    mean_corr <= 0.30           -> 1.0   (no penalty)
    0.30 < mean_corr <= 0.70    -> linearly scaled from 1.0 down to 0.5
    mean_corr > 0.70            -> 0.0   (block; nearly a clone)

The function is defensive: if there are no open positions, if the candidate
is already in the book, or if any correlation cannot be computed (too few
overlapping observations, all-NaN), the symbol is treated as uncorrelated
and the function returns 1.0.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def correlation_penalty(  # noqa: PLR0912 — guard cascade is the design; flat is auditable
    symbol: str,
    open_positions: list[str],
    returns_lookback: pd.DataFrame,
    lookback: int = 63,
) -> float:
    """Compute a [0, 1] sizing scalar based on correlation with the open book.

    Parameters
    ----------
    symbol:
        The candidate ticker being sized.
    open_positions:
        Tickers currently held in the portfolio.
    returns_lookback:
        Wide DataFrame of per-symbol returns (one column per ticker). Should
        contain enough recent rows to compute a rolling correlation; only the
        last `lookback` rows are used.
    lookback:
        Number of trailing observations to use when computing pairwise
        correlations. Default 63 ~ a calendar quarter of trading days.

    Returns
    -------
    float in [0.0, 1.0]. 1.0 means "no correlation penalty"; 0.0 means
    "block — the candidate is essentially a duplicate of the open book".
    """
    # Empty book or already-held symbol: no additional concentration risk.
    if not open_positions:
        return 1.0
    if symbol in open_positions:
        return 1.0

    # Defensive: missing data in the inputs is treated as uncorrelated.
    if returns_lookback is None or returns_lookback.empty:
        return 1.0
    if symbol not in returns_lookback.columns:
        return 1.0

    window = returns_lookback.tail(lookback)
    if len(window) < 2:
        return 1.0

    candidate_series = window[symbol]
    if candidate_series.isna().all():
        return 1.0

    correlations: list[float] = []
    for other in open_positions:
        if other not in window.columns:
            continue
        other_series = window[other]
        # Need at least two overlapping non-NaN observations to estimate corr.
        joined = pd.concat([candidate_series, other_series], axis=1).dropna()
        if len(joined) < 2:
            continue
        if joined.iloc[:, 0].std(ddof=1) == 0 or joined.iloc[:, 1].std(ddof=1) == 0:
            # Degenerate (constant) series — correlation is undefined.
            continue
        corr = float(joined.iloc[:, 0].corr(joined.iloc[:, 1]))
        if np.isnan(corr):
            continue
        correlations.append(corr)

    if not correlations:
        # No usable pairs: no evidence of correlation, no penalty.
        return 1.0

    mean_corr = float(np.mean(correlations))

    if mean_corr <= 0.30:
        scale = 1.0
    elif mean_corr >= 0.70:
        scale = 0.0
    else:
        # Linear ramp: 0.30 -> 1.0, 0.70 -> 0.5.
        # slope = (0.5 - 1.0) / (0.70 - 0.30) = -1.25
        scale = 1.0 + (mean_corr - 0.30) * (-1.25)

    # Clamp defensively.
    return max(0.0, min(1.0, float(scale)))
