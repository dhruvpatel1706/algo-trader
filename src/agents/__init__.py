"""Per-asset-class agent containers.

An Agent bundles strategies + universe + heat allocation for one asset class.
Concrete agents live in equity_agent.py, gold_agent.py, bonds_agent.py,
crypto_agent.py. The governance_agent.py is a non-trading meta-agent that
emits recommendations (kill/promote/halt) over the trading agents.
"""

from __future__ import annotations

from src.agents.base import Agent, AgentStatus, AssetClass

__all__ = ["Agent", "AgentStatus", "AssetClass"]
