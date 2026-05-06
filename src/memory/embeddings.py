"""Embedding providers for trade-memory recall.

Two implementations:

* :class:`DeterministicHashProvider` — sha256-based pseudo-embedding,
  64-dim, deterministic, no network. Two distinct narratives almost
  certainly map to two distinct vectors, but the geometry of the space
  is meaningless: similar setups will NOT cluster. Use only for tests
  and as a safe local fallback when no API key is configured.

* :class:`OpenAIEmbeddingProvider` — calls
  ``https://api.openai.com/v1/embeddings`` with model
  ``text-embedding-3-small`` (1536-dim) using a synchronous httpx
  client. Reads the API key from the constructor argument or the
  ``OPENAI_API_KEY`` environment variable.

All vectors are L2-normalized before being returned, so cosine
similarity reduces to a plain dot product downstream.

Selection helper: :func:`get_default_provider` reads
``MEMORY_EMBEDDING_PROVIDER`` (``"openai"`` | ``"deterministic"``,
default ``"deterministic"``) and returns the corresponding instance.
"""

from __future__ import annotations

import hashlib
import math
import os
from typing import Protocol, runtime_checkable

import httpx


class EmbeddingProviderError(RuntimeError):
    """Raised when an embedding backend fails (HTTP error, missing key, etc.)."""


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Protocol implemented by every embedding backend."""

    @property
    def dim(self) -> int:
        """Dimensionality of the embedding vectors this provider returns."""
        ...

    def embed(self, text: str) -> list[float]:
        """Embed a single string. Returned vector is L2-normalized."""
        ...

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed many strings. Each returned vector is L2-normalized."""
        ...


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _l2_normalize(vec: list[float]) -> list[float]:
    """Return ``vec`` rescaled to unit L2 norm.

    Returns the all-zero vector unchanged (avoids division by zero); this
    cannot happen for any non-empty input under the deterministic hash
    scheme below, but the OpenAI backend has been observed to return
    zero vectors for empty strings — be defensive.
    """
    norm_sq = 0.0
    for x in vec:
        norm_sq += x * x
    if norm_sq <= 0.0:
        return list(vec)
    norm = math.sqrt(norm_sq)
    return [x / norm for x in vec]


# ---------------------------------------------------------------------------
# Deterministic hash provider
# ---------------------------------------------------------------------------


class DeterministicHashProvider:
    """64-dim deterministic pseudo-embedding derived from SHA-256.

    Construction:
        For ``i`` in ``0..7``, compute ``sha256(f"{i}|" + text)`` and read
        the resulting 32 bytes as 8 unsigned 32-bit integers. Each integer
        is mapped to ``[-1.0, 1.0]`` via ``(2*x / 2**32) - 1``. The 64
        floats are concatenated and L2-normalized.

    This is deterministic, collision-free in practice for any reasonable
    distinct-input set, and requires no network. It exists for tests and
    as a fallback when ``OPENAI_API_KEY`` is unset; it is NOT a real
    semantic embedding and should never be used to drive production
    recall decisions.
    """

    DIM: int = 64
    _ROUNDS: int = 8  # 8 rounds * 8 floats per round = 64 dims

    @property
    def dim(self) -> int:
        return self.DIM

    def embed(self, text: str) -> list[float]:
        """Return the deterministic 64-dim L2-normalized vector for ``text``.

        Empty strings are permitted; they yield a fixed vector that is
        derived from sha256("0|"), sha256("1|"), ... — still well-formed
        and L2-normalized.
        """
        floats: list[float] = []
        # Each round produces 8 floats from 32 bytes -> 8 uint32 values.
        for i in range(self._ROUNDS):
            payload = f"{i}|{text}".encode()
            digest = hashlib.sha256(payload).digest()
            # 32 bytes / 4 = 8 uint32s.
            for j in range(0, 32, 4):
                value = int.from_bytes(digest[j : j + 4], "big", signed=False)
                # Map [0, 2**32) into [-1.0, 1.0).
                floats.append((value / (1 << 31)) - 1.0)
        return _l2_normalize(floats)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Per-text dispatch to :meth:`embed`. Order matches input order."""
        return [self.embed(t) for t in texts]


# ---------------------------------------------------------------------------
# OpenAI provider
# ---------------------------------------------------------------------------


class OpenAIEmbeddingProvider:
    """OpenAI embeddings backend (model ``text-embedding-3-small``).

    Args:
        api_key: API key. If ``None``, reads ``OPENAI_API_KEY`` from the
            environment. Raises :class:`EmbeddingProviderError` if no
            key is available.
        model: Model name; default ``"text-embedding-3-small"`` (1536-dim).
        timeout_s: Per-request timeout in seconds.

    Failure modes:
        Any non-2xx HTTP response raises
        :class:`EmbeddingProviderError` with the response body text. No
        retry policy is implemented here — callers may wrap in
        ``tenacity`` or similar if they want one. Network errors from
        ``httpx`` propagate as-is (they subclass ``RuntimeError`` only
        loosely so we don't wrap).
    """

    URL: str = "https://api.openai.com/v1/embeddings"
    DEFAULT_MODEL: str = "text-embedding-3-small"
    DEFAULT_DIM: int = 1536
    USER_AGENT: str = "algo-trader-memory/0.1"

    def __init__(
        self,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        timeout_s: float = 10.0,
    ) -> None:
        key = api_key if api_key is not None else os.environ.get("OPENAI_API_KEY")
        if not key:
            raise EmbeddingProviderError(
                "OPENAI_API_KEY is not set and no api_key was provided"
            )
        self._api_key: str = key
        self._model: str = model
        self._timeout_s: float = timeout_s

    @property
    def dim(self) -> int:
        # text-embedding-3-small returns 1536 dims. We don't introspect the
        # response to learn this; if the user passes a different model with
        # a different native dim, callers are responsible for ensuring the
        # store dim matches. v1 keeps this static.
        return self.DEFAULT_DIM

    # Internal: shared post-and-parse routine.
    def _post(self, payload: dict[str, object]) -> list[list[float]]:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "User-Agent": self.USER_AGENT,
        }
        try:
            response = httpx.post(
                self.URL,
                json=payload,
                headers=headers,
                timeout=self._timeout_s,
            )
        except httpx.HTTPError as exc:  # pragma: no cover - exercised via respx
            raise EmbeddingProviderError(f"network error: {exc!s}") from exc

        if response.status_code >= 400:
            raise EmbeddingProviderError(
                f"OpenAI embeddings HTTP {response.status_code}: {response.text}"
            )
        body = response.json()
        # OpenAI returns: {"data": [{"embedding": [...], "index": 0}, ...], ...}
        data = body.get("data")
        if not isinstance(data, list):
            raise EmbeddingProviderError(
                f"unexpected response shape: {body!r}"
            )
        out: list[list[float]] = []
        # Sort by index so caller order is preserved even if API permutes.
        items = sorted(data, key=lambda d: int(d.get("index", 0)))
        for item in items:
            vec = item.get("embedding")
            if not isinstance(vec, list):
                raise EmbeddingProviderError(
                    f"unexpected item shape: {item!r}"
                )
            out.append(_l2_normalize([float(x) for x in vec]))
        return out

    def embed(self, text: str) -> list[float]:
        """Embed a single string via the OpenAI API."""
        result = self._post({"model": self._model, "input": text})
        if len(result) != 1:
            raise EmbeddingProviderError(
                f"expected 1 embedding, got {len(result)}"
            )
        return result[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed many strings in one API call. Preserves input order."""
        if not texts:
            return []
        result = self._post({"model": self._model, "input": texts})
        if len(result) != len(texts):
            raise EmbeddingProviderError(
                f"expected {len(texts)} embeddings, got {len(result)}"
            )
        return result


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_default_provider() -> EmbeddingProvider:
    """Return the embedding provider configured by environment.

    Reads ``MEMORY_EMBEDDING_PROVIDER``:

    * ``"openai"`` → :class:`OpenAIEmbeddingProvider` (requires
      ``OPENAI_API_KEY``).
    * ``"deterministic"`` (or unset) → :class:`DeterministicHashProvider`.

    Any other value raises :class:`EmbeddingProviderError` so a
    misconfigured env doesn't silently fall back to fake embeddings.
    """
    choice = os.environ.get("MEMORY_EMBEDDING_PROVIDER", "deterministic").strip().lower()
    if choice == "openai":
        return OpenAIEmbeddingProvider()
    if choice == "deterministic":
        return DeterministicHashProvider()
    raise EmbeddingProviderError(
        f"unknown MEMORY_EMBEDDING_PROVIDER={choice!r}; "
        "expected 'openai' or 'deterministic'"
    )
