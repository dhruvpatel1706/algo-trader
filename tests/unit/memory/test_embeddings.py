"""Unit tests for the embedding providers and the default-provider factory."""

from __future__ import annotations

import json
import math

import httpx
import pytest
import respx
from src.memory.embeddings import (
    DeterministicHashProvider,
    EmbeddingProviderError,
    OpenAIEmbeddingProvider,
    get_default_provider,
)

# ---------------------------------------------------------------------------
# DeterministicHashProvider
# ---------------------------------------------------------------------------


def test_deterministic_hash_dim_is_64():
    p = DeterministicHashProvider()
    assert p.dim == 64
    vec = p.embed("hello")
    assert len(vec) == 64


def test_deterministic_hash_is_repeatable():
    p = DeterministicHashProvider()
    a = p.embed("the cat sat on the mat")
    b = p.embed("the cat sat on the mat")
    assert a == b


def test_deterministic_hash_distinct_inputs_produce_distinct_vectors():
    p = DeterministicHashProvider()
    a = p.embed("alpha")
    b = p.embed("beta")
    assert a != b
    # And not a near-duplicate either.
    assert sum(abs(x - y) for x, y in zip(a, b, strict=True)) > 1e-3


def test_deterministic_hash_is_l2_normalized():
    p = DeterministicHashProvider()
    for text in ("", "hello", "the quick brown fox", "x" * 5000):
        vec = p.embed(text)
        norm = math.sqrt(sum(x * x for x in vec))
        assert math.isclose(norm, 1.0, rel_tol=1e-6)


def test_deterministic_hash_embed_batch_matches_single_calls():
    p = DeterministicHashProvider()
    inputs = ["one", "two", "three", "four"]
    batch = p.embed_batch(inputs)
    singles = [p.embed(t) for t in inputs]
    assert batch == singles


def test_deterministic_hash_embed_batch_empty():
    p = DeterministicHashProvider()
    assert p.embed_batch([]) == []


# ---------------------------------------------------------------------------
# OpenAIEmbeddingProvider
# ---------------------------------------------------------------------------


def test_openai_provider_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(EmbeddingProviderError, match="OPENAI_API_KEY"):
        OpenAIEmbeddingProvider()


def test_openai_provider_dim_is_1536():
    p = OpenAIEmbeddingProvider(api_key="sk-test-key")
    assert p.dim == 1536


@respx.mock
def test_openai_embed_request_shape_and_normalization():
    p = OpenAIEmbeddingProvider(api_key="sk-test-key")
    # Mock returns a small but well-formed embedding; the provider must
    # L2-normalize before returning.
    fake_embedding = [3.0, 4.0]  # norm = 5
    route = respx.post("https://api.openai.com/v1/embeddings").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [{"embedding": fake_embedding, "index": 0}],
                "model": "text-embedding-3-small",
            },
        )
    )
    out = p.embed("foo")
    assert route.called
    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer sk-test-key"
    assert request.headers["User-Agent"] == "algo-trader-memory/0.1"
    body = json.loads(request.content)
    assert body == {"model": "text-embedding-3-small", "input": "foo"}
    # 3,4,5 triangle -> normalized to 0.6, 0.8.
    assert out == pytest.approx([0.6, 0.8])


@respx.mock
def test_openai_embed_batch_preserves_order():
    p = OpenAIEmbeddingProvider(api_key="sk-test-key")
    # Server returns items in scrambled order; provider must restore by index.
    respx.post("https://api.openai.com/v1/embeddings").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {"embedding": [0.0, 1.0], "index": 1},
                    {"embedding": [1.0, 0.0], "index": 0},
                ]
            },
        )
    )
    out = p.embed_batch(["first", "second"])
    assert out[0] == pytest.approx([1.0, 0.0])
    assert out[1] == pytest.approx([0.0, 1.0])


@respx.mock
def test_openai_http_error_raises_embedding_provider_error():
    p = OpenAIEmbeddingProvider(api_key="sk-test-key")
    respx.post("https://api.openai.com/v1/embeddings").mock(
        return_value=httpx.Response(429, text="rate limited"),
    )
    with pytest.raises(EmbeddingProviderError, match="429"):
        p.embed("foo")


@respx.mock
def test_openai_unexpected_response_shape_raises():
    p = OpenAIEmbeddingProvider(api_key="sk-test-key")
    respx.post("https://api.openai.com/v1/embeddings").mock(
        return_value=httpx.Response(200, json={"unexpected": True}),
    )
    with pytest.raises(EmbeddingProviderError):
        p.embed("foo")


def test_openai_embed_batch_empty_list_skips_call():
    p = OpenAIEmbeddingProvider(api_key="sk-test-key")
    # No respx mock — would explode on a real call. Empty input must
    # short-circuit before the network.
    assert p.embed_batch([]) == []


# ---------------------------------------------------------------------------
# get_default_provider
# ---------------------------------------------------------------------------


def test_default_provider_unset_returns_deterministic(monkeypatch):
    monkeypatch.delenv("MEMORY_EMBEDDING_PROVIDER", raising=False)
    p = get_default_provider()
    assert isinstance(p, DeterministicHashProvider)


def test_default_provider_explicit_deterministic(monkeypatch):
    monkeypatch.setenv("MEMORY_EMBEDDING_PROVIDER", "deterministic")
    p = get_default_provider()
    assert isinstance(p, DeterministicHashProvider)


def test_default_provider_openai_with_key(monkeypatch):
    monkeypatch.setenv("MEMORY_EMBEDDING_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")
    p = get_default_provider()
    assert isinstance(p, OpenAIEmbeddingProvider)


def test_default_provider_openai_without_key_raises(monkeypatch):
    monkeypatch.setenv("MEMORY_EMBEDDING_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(EmbeddingProviderError):
        get_default_provider()


def test_default_provider_unknown_value_raises(monkeypatch):
    monkeypatch.setenv("MEMORY_EMBEDDING_PROVIDER", "magic-cloud")
    with pytest.raises(EmbeddingProviderError, match="unknown"):
        get_default_provider()
