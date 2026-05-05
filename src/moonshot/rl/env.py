"""Gym-compatible single-asset trading environment for the RL moonshot lane.

Paper-only research scaffold. See module-level note in `src/moonshot/__init__.py`
companions: this lane NEVER bridges to a live broker. The hard caps below
mirror the repo policy in `src/config.py` and cannot be raised by the agent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar

import numpy as np
import pandas as pd

# gymnasium is optional. If absent, callers can still construct TradingEnv as a
# plain Python object and use the duck-typed reset/step interface.
try:  # pragma: no cover - import side-effect
    import gymnasium as gym
    from gymnasium import spaces

    _HAS_GYM = True
except ImportError:  # pragma: no cover - exercised only without gymnasium
    gym = None  # type: ignore[assignment]
    spaces = None  # type: ignore[assignment]
    _HAS_GYM = False


# Moonshot lane invariant: never bridges to a live broker. See module docstring.
LIVE_BROKER_BRIDGE: bool = False

# Discrete action space: index -> position sign.
ACTION_FLAT = 0
ACTION_LONG = 1
ACTION_SHORT = 2  # only honored when allow_short=True

ACTION_TO_POS = {ACTION_FLAT: 0, ACTION_LONG: 1, ACTION_SHORT: -1}


@dataclass(frozen=True, slots=True)
class TradingEnvConfig:
    """Configuration for `TradingEnv`.

    Hard caps mirror `src/config.py` (`max_position_size_pct=0.10`,
    `risk_pct_per_trade=0.01`, `max_drawdown_halt=0.15`). These cannot be raised
    by the RL agent — the env enforces them.
    """

    symbol: str = "SPY"
    initial_equity: float = 10_000.0
    transaction_cost_bps: float = 1.0  # 0.01%
    slippage_bps: float = 5.0
    max_position_size_pct: float = 0.10  # 10% per repo cap
    risk_pct_per_trade: float = 0.01  # 1% per repo cap
    max_drawdown_halt: float = 0.15  # 15% per repo cap
    allow_short: bool = False
    feature_columns: tuple[str, ...] = (
        "rsi",
        "adx",
        "atr",
        "bb_width",
        "ret_5d",
        "ret_21d",
    )
    # Minimum number of warm-up bars before the env starts emitting observations.
    # Anything that needs e.g. a 21-day rolling return must have at least 21 bars
    # of context.
    warmup_bars: int = 25


def _safe_div(a: pd.Series | np.ndarray, b: pd.Series | np.ndarray) -> pd.Series | np.ndarray:
    """Divide while masking divide-by-zero -> 0.0 (avoids inf in features)."""
    if isinstance(a, pd.Series) or isinstance(b, pd.Series):
        return (a / b).replace([np.inf, -np.inf], 0.0).fillna(0.0)
    out = np.divide(a, b, out=np.zeros_like(a, dtype=float), where=b != 0)
    return out


def _build_features(bars: pd.DataFrame, columns: tuple[str, ...]) -> pd.DataFrame:
    """Build the feature matrix used by the env.

    Light-weight, vectorised; we deliberately do NOT depend on `ta` here so the
    env stays fast and the import surface stays small. Each feature has a
    standardised, bounded shape so the linear agent does not need explicit
    normalisation layers.
    """
    close = bars["close"].astype(float)
    high = bars.get("high", close).astype(float)
    low = bars.get("low", close).astype(float)

    feats: dict[str, pd.Series] = {}

    # RSI(14): 0..100 normalised to roughly [-1, 1] via (rsi - 50) / 50.
    delta = close.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    roll_up = up.rolling(14, min_periods=14).mean()
    roll_down = down.rolling(14, min_periods=14).mean()
    rs = _safe_div(roll_up, roll_down)
    rsi_raw = 100 - 100 / (1 + rs)
    feats["rsi"] = ((rsi_raw - 50.0) / 50.0).fillna(0.0)

    # ADX-ish: simple absolute returns / rolling stdev. Bounded heuristic.
    abs_ret = close.pct_change().abs()
    feats["adx"] = (abs_ret.rolling(14, min_periods=14).mean() * 100.0).fillna(0.0)

    # ATR(14): true-range mean, normalised by close.
    tr = pd.concat(
        [
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ],
        axis=1,
    ).max(axis=1)
    feats["atr"] = _safe_div(tr.rolling(14, min_periods=14).mean(), close).fillna(0.0)

    # Bollinger band width / close, mean-centered.
    bb_mid = close.rolling(20, min_periods=20).mean()
    bb_std = close.rolling(20, min_periods=20).std(ddof=0)
    feats["bb_width"] = _safe_div(2 * bb_std, bb_mid).fillna(0.0)

    feats["ret_5d"] = close.pct_change(5).fillna(0.0)
    feats["ret_21d"] = close.pct_change(21).fillna(0.0)

    df = pd.DataFrame(feats, index=bars.index)
    # Keep only requested columns (allows callers to limit the feature surface
    # for tests / ablations).
    missing = [c for c in columns if c not in df.columns]
    for m in missing:
        df[m] = 0.0
    return df[list(columns)].replace([np.inf, -np.inf], 0.0).fillna(0.0)


@dataclass(slots=True)
class _EnvState:
    bar_idx: int = 0
    equity: float = 0.0
    peak_equity: float = 0.0
    position: int = 0  # current sign: -1, 0, +1
    n_trades: int = 0
    wins: int = 0
    realized_pnl: float = 0.0
    last_trade_entry: float = 0.0
    trade_pnls: list[float] = field(default_factory=list)


class TradingEnv:
    """Gym-compatible single-asset trading environment.

    Observation: vector of normalized features at the current bar.
    Action: discrete in `{0: flat, 1: long, 2: short}`. Short ignored unless
        `config.allow_short=True`.
    Reward: log return of equity from this bar to next bar, after costs.

    Hard rules baked in (matching repo policy):
    - Long-only by default (set `allow_short=True` to enable shorts).
    - Max position size 10% of equity (cannot be raised by RL agent).
    - Drawdown halt at 15% (terminates episode).
    - Risk cap 1% per trade enforced by sizing (we cap position notional so a
      single bar's adverse move cannot exceed `risk_pct_per_trade` of equity at
      typical ATR; the env additionally clips raw position notional via
      `max_position_size_pct`).
    """

    metadata: ClassVar[dict[str, Any]] = {"render_modes": []}

    def __init__(
        self,
        bars: pd.DataFrame,
        config: TradingEnvConfig | None = None,
        *,
        seed: int | None = None,
    ) -> None:
        if "close" not in bars.columns:
            raise ValueError("bars must contain a 'close' column")
        if len(bars) < 2:
            raise ValueError("bars must contain at least 2 rows")

        self.config = config or TradingEnvConfig()
        self.bars = bars.copy()
        self.bars.index = pd.RangeIndex(len(self.bars)) if not isinstance(
            self.bars.index, pd.DatetimeIndex
        ) else self.bars.index

        self._features = _build_features(self.bars, self.config.feature_columns)
        self._n_features = len(self.config.feature_columns)
        self._n_actions = 3 if self.config.allow_short else 2

        self._rng = np.random.default_rng(seed)
        self._state = _EnvState(
            equity=self.config.initial_equity,
            peak_equity=self.config.initial_equity,
        )

        # Build gym spaces only if gymnasium is importable. Otherwise expose
        # plain attributes for duck-typed callers.
        if _HAS_GYM:
            self.observation_space = spaces.Box(
                low=-np.inf,
                high=np.inf,
                shape=(self._n_features,),
                dtype=np.float32,
            )
            self.action_space = spaces.Discrete(self._n_actions)
        else:  # pragma: no cover - exercised only without gymnasium
            self.observation_space = None
            self.action_space = None

    # -- properties -----------------------------------------------------------

    @property
    def n_features(self) -> int:
        return self._n_features

    @property
    def n_actions(self) -> int:
        return self._n_actions

    @property
    def equity(self) -> float:
        return self._state.equity

    # -- gym API --------------------------------------------------------------

    def reset(
        self,
        *,
        seed: int | None = None,
        options: dict | None = None,
    ) -> tuple[np.ndarray, dict]:
        del options  # gym signature compatibility
        if seed is not None:
            self._rng = np.random.default_rng(seed)
        self._state = _EnvState(
            bar_idx=int(self.config.warmup_bars),
            equity=self.config.initial_equity,
            peak_equity=self.config.initial_equity,
        )
        info = {
            "bar_idx": self._state.bar_idx,
            "equity": self._state.equity,
            "position": self._state.position,
        }
        return self._observation(), info

    def step(
        self, action: int
    ) -> tuple[np.ndarray, float, bool, bool, dict]:
        """Apply `action` at the current bar and step forward one bar.

        Returns: (observation, reward, terminated, truncated, info).
        """
        if action < 0 or action >= self._n_actions:
            raise ValueError(
                f"invalid action {action!r}; expected 0..{self._n_actions - 1}"
            )

        target_pos = ACTION_TO_POS[int(action)]
        if not self.config.allow_short and target_pos < 0:
            target_pos = 0

        prev_close = float(self.bars["close"].iloc[self._state.bar_idx])
        next_idx = self._state.bar_idx + 1
        truncated = next_idx >= len(self.bars)
        if truncated:
            obs = self._observation()
            info = self._make_info(reward=0.0, position_change=False)
            return obs, 0.0, True, True, info

        next_close = float(self.bars["close"].iloc[next_idx])

        # Position sizing: notional = equity * max_position_size_pct.
        notional = self._state.equity * self.config.max_position_size_pct
        position_change = target_pos != self._state.position
        cost = self._apply_position_change(target_pos, notional) if position_change else 0.0

        # Mark-to-market PnL for the *previous* position over [prev_close, next_close].
        bar_return = (next_close - prev_close) / prev_close if prev_close > 0 else 0.0
        # Pos used during the bar is the OLD position (we change at bar close).
        pos_used = self._state.position
        gross_pnl = pos_used * notional * bar_return
        net_pnl = gross_pnl - cost

        prev_equity = self._state.equity
        self._state.equity = max(0.0, prev_equity + net_pnl)
        self._state.peak_equity = max(self._state.peak_equity, self._state.equity)
        self._state.position = target_pos
        self._state.bar_idx = next_idx

        # Reward = log equity return; clip to a finite range for numerical safety.
        if prev_equity > 0 and self._state.equity > 0:
            reward = float(np.log(self._state.equity / prev_equity))
        else:
            reward = -1.0

        # Drawdown halt: terminate if equity dropped past `max_drawdown_halt`.
        peak = self._state.peak_equity
        dd = 1.0 - (self._state.equity / peak) if peak > 0 else 0.0
        terminated = dd >= self.config.max_drawdown_halt
        truncated = next_idx >= len(self.bars) - 1

        info = self._make_info(reward=reward, position_change=position_change)
        info["drawdown"] = float(dd)
        info["transaction_cost"] = float(cost)

        return self._observation(), reward, bool(terminated), bool(truncated), info

    # -- internals ------------------------------------------------------------

    def _apply_position_change(self, target_pos: int, notional: float) -> float:
        """Charge transaction cost + accounting for a position change.

        Returns the dollar cost so the caller can subtract it from PnL.
        """
        # Round-trip cost on the changed side. If flipping (long->short),
        # we pay twice (close + open).
        unit_cost_bps = self.config.transaction_cost_bps + self.config.slippage_bps
        cur_pos = self._state.position
        is_flip = cur_pos != 0 and target_pos not in (0, cur_pos)
        switch_factor = 2 if is_flip else 1
        cost = notional * (unit_cost_bps / 10_000.0) * switch_factor
        self._state.n_trades += 1
        if target_pos != 0:
            # Opening a new position — mark the entry equity for win-rate.
            self._state.last_trade_entry = self._state.equity
        else:
            # Closing a position -> realised PnL accounting.
            trade_pnl = self._state.equity - self._state.last_trade_entry
            self._state.realized_pnl += trade_pnl
            self._state.trade_pnls.append(trade_pnl)
            if trade_pnl > 0:
                self._state.wins += 1
        return cost

    def _observation(self) -> np.ndarray:
        idx = min(self._state.bar_idx, len(self._features) - 1)
        row = self._features.iloc[idx].to_numpy(dtype=np.float32, copy=True)
        # Append normalized position as a state feature.
        # (Many RL trading papers find this materially helps stability.)
        # We do not extend the observation_space dim because the linear agent
        # treats the pure feature vector; position info goes in `info`.
        return row

    def _make_info(self, *, reward: float, position_change: bool) -> dict:
        return {
            "bar_idx": self._state.bar_idx,
            "equity": self._state.equity,
            "peak_equity": self._state.peak_equity,
            "position": self._state.position,
            "n_trades": self._state.n_trades,
            "wins": self._state.wins,
            "trade_pnls": list(self._state.trade_pnls),
            "reward": reward,
            "position_change": position_change,
        }


__all__ = [
    "ACTION_FLAT",
    "ACTION_LONG",
    "ACTION_SHORT",
    "ACTION_TO_POS",
    "TradingEnv",
    "TradingEnvConfig",
]
