"""Episodic trade memory for the autonomous reasoner.

This package gives the LLM a small persistent memory of past trades so
it can answer "have we seen a setup like this before, and how did those
work out?" before deciding on a new entry. Components:

* :class:`TradeMemory` — frozen dataclass for one row.
* :mod:`embeddings` — pluggable backends:
  :class:`DeterministicHashProvider` (no network, dim 64) and
  :class:`OpenAIEmbeddingProvider` (``text-embedding-3-small``, dim 1536).
* :class:`MemoryStore` — SQLite persistence (default
  ``live/memory.db``), float32 BLOB embeddings.
* :func:`recall_similar` — top-k cosine recall.
* :func:`format_memories_for_prompt` — markdown renderer for prompt context.

Nothing in this package mutates broker state, journal, or the agents/
package; it is a pure new feature consumed by the reasoner via the
public functions above.
"""

from __future__ import annotations

from src.memory.embeddings import (
    DeterministicHashProvider,
    EmbeddingProvider,
    EmbeddingProviderError,
    OpenAIEmbeddingProvider,
    get_default_provider,
)
from src.memory.format import format_memories_for_prompt
from src.memory.models import TradeMemory
from src.memory.recall import DimensionMismatchError, cosine, recall_similar
from src.memory.store import DEFAULT_DB_PATH, MemoryStore, MemoryStoreError

__all__ = [
    "DEFAULT_DB_PATH",
    "DeterministicHashProvider",
    "DimensionMismatchError",
    "EmbeddingProvider",
    "EmbeddingProviderError",
    "MemoryStore",
    "MemoryStoreError",
    "OpenAIEmbeddingProvider",
    "TradeMemory",
    "cosine",
    "format_memories_for_prompt",
    "get_default_provider",
    "recall_similar",
]
