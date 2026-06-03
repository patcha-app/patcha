"""Chunk sizing must track the active embedding model's real token limit."""

import pytest

from patcha import embedding
from patcha.config import config
from patcha.utils.chunking import _ENCODING, chunk_text


def test_short_text_returns_single_chunk_unchanged():
    text = "a short reconstructed screen"
    assert chunk_text(text, embedding.effective_max_tokens(), 100) == [text]


def test_long_text_splits_within_budget_with_overlap():
    budget = embedding.effective_max_tokens()
    overlap = config.embedding_chunk_overlap
    text = " ".join(f"line{i} content here" for i in range(2000))

    chunks = chunk_text(text, budget, overlap)

    assert len(chunks) > 1
    for chunk in chunks:
        assert len(_ENCODING.encode(chunk)) <= budget

    first, second = _ENCODING.encode(chunks[0]), _ENCODING.encode(chunks[1])
    assert first[-overlap:] == second[:overlap]


def test_effective_max_tokens_tracks_model(monkeypatch):
    monkeypatch.setattr(config, "embedding_model_name", "BAAI/bge-base-en-v1.5")
    assert embedding.effective_max_tokens() == int(512 * embedding._TOKEN_SAFETY)

    monkeypatch.setattr(config, "embedding_model_name", "text-embedding-3-small")
    assert embedding.effective_max_tokens() == min(
        config.max_embedding_tokens, int(8191 * embedding._TOKEN_SAFETY)
    )


def test_unknown_model_falls_back_to_safe_floor(monkeypatch):
    monkeypatch.setattr(config, "embedding_model_name", "some/unknown-model")
    assert embedding.effective_max_tokens() == int(
        embedding._DEFAULT_MODEL_MAX_TOKENS * embedding._TOKEN_SAFETY
    )


def test_overlap_not_less_than_max_raises():
    with pytest.raises(ValueError):
        chunk_text("some text", max_tokens=100, overlap=100)
