"""Unit tests for `LinearQAgent`."""

from __future__ import annotations

import numpy as np
import pytest
from src.moonshot.rl.agent import LinearQAgent


def _agent(**kw) -> LinearQAgent:
    defaults = {
        "n_features": 4,
        "n_actions": 3,
        "lr": 0.05,
        "gamma": 0.95,
        "lam": 0.7,
        "epsilon_start": 0.5,
        "epsilon_end": 0.05,
        "epsilon_decay": 0.9,
        "seed": 1,
    }
    defaults.update(kw)
    return LinearQAgent(**defaults)


# -------- select_action ------------------------------------------------------


def test_select_action_returns_int_in_range():
    a = _agent()
    s = np.zeros(a.n_features)
    for _ in range(50):
        action = a.select_action(s)
        assert isinstance(action, int)
        assert 0 <= action < a.n_actions


def test_deterministic_action_skips_exploration():
    a = _agent(epsilon_start=1.0)  # always explore in non-deterministic mode
    s = np.array([1.0, -1.0, 0.5, 0.25])
    # Force a clear argmax via weights.
    a.W[1] = np.array([2.0, 0.0, 0.0, 0.0])  # action 1 wins for the given state
    action = a.select_action(s, deterministic=True)
    assert action == 1


def test_select_action_uses_q_argmax_when_epsilon_zero():
    a = _agent(epsilon_start=0.0, epsilon_end=0.0)
    a.W[2] = np.array([5.0, 0.0, 0.0, 0.0])
    s = np.array([1.0, 0.0, 0.0, 0.0])
    assert a.select_action(s) == 2


def test_invalid_state_shape_raises():
    a = _agent()
    with pytest.raises(ValueError):
        a.q_values(np.array([1.0, 2.0]))  # wrong dim


# -------- update -------------------------------------------------------------


def test_update_does_not_crash_and_changes_weights():
    a = _agent()
    weights_before = a.W.copy()
    s = np.array([1.0, 0.0, 0.0, 0.0])
    sp = np.array([0.5, 0.5, 0.0, 0.0])
    a.update(s, action=1, reward=1.0, next_state=sp, next_action=0, done=False)
    assert not np.allclose(weights_before, a.W), "weights should move on a non-zero TD error"


def test_update_with_done_clears_traces():
    a = _agent()
    s = np.array([1.0, 0.0, 0.0, 0.0])
    sp = np.array([0.0, 1.0, 0.0, 0.0])
    a.update(s, action=0, reward=1.0, next_state=sp, next_action=1, done=False)
    assert np.any(a._e_W != 0)
    a.update(s, action=0, reward=0.0, next_state=sp, next_action=0, done=True)
    assert np.all(a._e_W == 0)


def test_update_invalid_action_raises():
    a = _agent()
    s = np.zeros(a.n_features)
    with pytest.raises(ValueError):
        a.update(s, action=99, reward=0.0, next_state=s, next_action=0, done=False)
    with pytest.raises(ValueError):
        a.update(s, action=0, reward=0.0, next_state=s, next_action=99, done=False)


# -------- save / load --------------------------------------------------------


def test_save_load_round_trip(tmp_path):
    a = _agent()
    s = np.array([0.1, -0.2, 0.3, 0.4])
    # Train one step so the weights are non-trivial.
    a.update(s, action=1, reward=0.7, next_state=s, next_action=0, done=False)
    a.decay_epsilon()
    path = tmp_path / "agent.bundle"
    a.save(path)

    b = LinearQAgent(n_features=a.n_features, n_actions=a.n_actions, seed=42)
    b.load(path)

    np.testing.assert_array_equal(a.W, b.W)
    np.testing.assert_array_equal(a.b, b.b)
    np.testing.assert_array_equal(a._e_W, b._e_W)
    np.testing.assert_array_equal(a._e_b, b._e_b)
    assert b.epsilon == pytest.approx(a.epsilon)
    assert b.lr == pytest.approx(a.lr)
    assert b.gamma == pytest.approx(a.gamma)
    assert b.lam == pytest.approx(a.lam)


def test_load_dim_mismatch_raises(tmp_path):
    a = _agent(n_features=4, n_actions=3)
    a.save(tmp_path / "x")
    other = LinearQAgent(n_features=5, n_actions=3)
    with pytest.raises(ValueError):
        other.load(tmp_path / "x")


# -------- epsilon decay ------------------------------------------------------


def test_epsilon_decays_and_clips_to_floor():
    a = _agent(epsilon_start=1.0, epsilon_end=0.1, epsilon_decay=0.5)
    assert a.epsilon == 1.0
    a.decay_epsilon()
    assert a.epsilon == pytest.approx(0.5)
    a.decay_epsilon()
    assert a.epsilon == pytest.approx(0.25)
    # Many more decays -> clip at 0.1.
    for _ in range(20):
        a.decay_epsilon()
    assert a.epsilon == pytest.approx(0.1)


def test_constructor_validates_args():
    with pytest.raises(ValueError):
        LinearQAgent(n_features=0, n_actions=2)
    with pytest.raises(ValueError):
        LinearQAgent(n_features=2, n_actions=0)
    with pytest.raises(ValueError):
        LinearQAgent(n_features=2, n_actions=2, gamma=2.0)
    with pytest.raises(ValueError):
        LinearQAgent(n_features=2, n_actions=2, lam=-0.1)
