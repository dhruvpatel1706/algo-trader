"""Linear function approximator Q-learning agent (pure NumPy).

Why a linear agent rather than DQN/PPO/SAC?

- The RL lane is research scaffolding for a *retail-scale* setup. A deep RL
  policy on a small features space and a few thousand bars overfits aggressively;
  the deterministic linear baseline gives a clean signal of whether RL has any
  alpha at all before adding architectural complexity.
- Keeping the dep surface to NumPy keeps the moonshot lane truly self-contained
  — no torch / tf / sb3 install needed to run training locally or in CI.
- A linear policy with eligibility traces is fast enough to run hundreds of
  episodes per second on synthetic bars, which is what the harness needs for
  reproducible regression tests.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

# Moonshot lane invariant: this module MUST NOT reach a real broker. Static
# audits grep for `LIVE_BROKER_BRIDGE` in `src/moonshot/**`; missing it
# signals a contributor accidentally wired moonshot logic into the trade path.
LIVE_BROKER_BRIDGE: bool = False


class LinearQAgent:
    """Linear function-approximator Q-agent.

    Q(s, a) = s @ W[a] + b[a]
    Update rule: SARSA(λ) with eligibility traces (default λ = 0.9).
    Exploration: epsilon-greedy with multiplicative decay.

    Pure NumPy. No torch / tf / sb3 dep.
    """

    def __init__(
        self,
        n_features: int,
        n_actions: int,
        *,
        lr: float = 0.01,
        gamma: float = 0.99,
        lam: float = 0.9,
        epsilon_start: float = 1.0,
        epsilon_end: float = 0.05,
        epsilon_decay: float = 0.995,
        seed: int | None = None,
    ) -> None:
        if n_features <= 0:
            raise ValueError("n_features must be positive")
        if n_actions <= 0:
            raise ValueError("n_actions must be positive")
        if not 0.0 <= gamma <= 1.0:
            raise ValueError("gamma must be in [0, 1]")
        if not 0.0 <= lam <= 1.0:
            raise ValueError("lam must be in [0, 1]")

        self.n_features = int(n_features)
        self.n_actions = int(n_actions)
        self.lr = float(lr)
        self.gamma = float(gamma)
        self.lam = float(lam)
        self.epsilon_start = float(epsilon_start)
        self.epsilon = float(epsilon_start)
        self.epsilon_end = float(epsilon_end)
        self.epsilon_decay = float(epsilon_decay)

        self._rng = np.random.default_rng(seed)
        # Small-magnitude init keeps initial Q ~0; helps stable epsilon-greedy.
        self.W = self._rng.normal(0.0, 0.01, size=(self.n_actions, self.n_features))
        self.b = np.zeros(self.n_actions, dtype=np.float64)
        # Eligibility traces, same shape as W and b.
        self._e_W = np.zeros_like(self.W)
        self._e_b = np.zeros_like(self.b)

    # -- core API -------------------------------------------------------------

    def q_values(self, state: np.ndarray) -> np.ndarray:
        """Return Q-values for every action at `state`."""
        s = np.asarray(state, dtype=np.float64).reshape(-1)
        if s.shape[0] != self.n_features:
            raise ValueError(
                f"state has {s.shape[0]} features; expected {self.n_features}"
            )
        return self.W @ s + self.b

    def select_action(self, state: np.ndarray, *, deterministic: bool = False) -> int:
        """Epsilon-greedy action selection.

        With prob `epsilon` (or 0 when `deterministic=True`) we sample uniformly.
        Otherwise pick `argmax_a Q(s, a)`. Ties broken by first index.
        """
        if not deterministic and self._rng.random() < self.epsilon:
            return int(self._rng.integers(0, self.n_actions))
        q = self.q_values(state)
        return int(np.argmax(q))

    def update(
        self,
        state: np.ndarray,
        action: int,
        reward: float,
        next_state: np.ndarray,
        next_action: int,
        done: bool,
    ) -> None:
        """Apply one SARSA(λ) update with eligibility traces."""
        if not 0 <= action < self.n_actions:
            raise ValueError(f"action {action!r} out of range")
        if not 0 <= next_action < self.n_actions:
            raise ValueError(f"next_action {next_action!r} out of range")

        s = np.asarray(state, dtype=np.float64).reshape(-1)
        sp = np.asarray(next_state, dtype=np.float64).reshape(-1)

        q_sa = float(self.W[action] @ s + self.b[action])
        q_spap = 0.0 if done else float(self.W[next_action] @ sp + self.b[next_action])
        td_error = float(reward) + self.gamma * q_spap - q_sa

        # Decay traces; bump the trace for the action just taken.
        self._e_W *= self.gamma * self.lam
        self._e_b *= self.gamma * self.lam
        self._e_W[action] += s
        self._e_b[action] += 1.0

        self.W += self.lr * td_error * self._e_W
        self.b += self.lr * td_error * self._e_b

        if done:
            self._e_W.fill(0.0)
            self._e_b.fill(0.0)

    def decay_epsilon(self) -> None:
        """Apply one multiplicative epsilon decay step."""
        self.epsilon = max(self.epsilon_end, self.epsilon * self.epsilon_decay)

    def reset_traces(self) -> None:
        self._e_W.fill(0.0)
        self._e_b.fill(0.0)

    # -- serialisation --------------------------------------------------------

    def save(self, path: Path | str) -> None:
        """Save weights + hyperparameters to a single .npz/.json bundle."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez(
            path.with_suffix(".npz"),
            W=self.W,
            b=self.b,
            e_W=self._e_W,
            e_b=self._e_b,
        )
        meta = {
            "n_features": self.n_features,
            "n_actions": self.n_actions,
            "lr": self.lr,
            "gamma": self.gamma,
            "lam": self.lam,
            "epsilon": self.epsilon,
            "epsilon_start": self.epsilon_start,
            "epsilon_end": self.epsilon_end,
            "epsilon_decay": self.epsilon_decay,
        }
        path.with_suffix(".json").write_text(json.dumps(meta, indent=2))

    def load(self, path: Path | str) -> None:
        path = Path(path)
        meta = json.loads(path.with_suffix(".json").read_text())
        if int(meta["n_features"]) != self.n_features:
            raise ValueError(
                f"saved n_features={meta['n_features']} != self.n_features={self.n_features}"
            )
        if int(meta["n_actions"]) != self.n_actions:
            raise ValueError(
                f"saved n_actions={meta['n_actions']} != self.n_actions={self.n_actions}"
            )
        bundle = np.load(path.with_suffix(".npz"))
        self.W = bundle["W"]
        self.b = bundle["b"]
        self._e_W = bundle["e_W"]
        self._e_b = bundle["e_b"]
        self.lr = float(meta["lr"])
        self.gamma = float(meta["gamma"])
        self.lam = float(meta["lam"])
        self.epsilon = float(meta["epsilon"])
        self.epsilon_start = float(meta["epsilon_start"])
        self.epsilon_end = float(meta["epsilon_end"])
        self.epsilon_decay = float(meta["epsilon_decay"])


__all__ = ["LinearQAgent"]
