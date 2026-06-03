"""Local text embeddings via FastEmbed (ONNX, on-device)."""

import logging
from functools import lru_cache
from typing import List

from fastembed import TextEmbedding

from patcha.config import config

log = logging.getLogger(__name__)

# Conservative native input limits (model tokens) for supported embedders.
_MODEL_MAX_TOKENS = {
    "BAAI/bge-base-en-v1.5": 512,
    "BAAI/bge-small-en-v1.5": 512,
    "text-embedding-3-small": 8191,
    "text-embedding-3-large": 8191,
}
_DEFAULT_MODEL_MAX_TOKENS = 512  # safe floor for unknown local models

# The chunker counts cl100k tokens; bge counts WordPiece tokens, which usually
# run higher for the same text. Leave headroom so a chunk sized in cl100k tokens
# can't overflow the model's native limit.
_TOKEN_SAFETY = 0.8


def effective_max_tokens() -> int:
    limit = _MODEL_MAX_TOKENS.get(
        config.embedding_model_name, _DEFAULT_MODEL_MAX_TOKENS
    )
    budget = min(config.max_embedding_tokens, int(limit * _TOKEN_SAFETY))
    return max(budget, config.embedding_chunk_overlap + 1)


@lru_cache(maxsize=1)
def _model() -> TextEmbedding:
    log.info(
        "loading fastembed model=%s cache_dir=%s",
        config.embedding_model_name,
        config.embedding_cache_dir,
    )
    config.embedding_cache_dir.mkdir(parents=True, exist_ok=True)
    return TextEmbedding(
        model_name=config.embedding_model_name,
        cache_dir=str(config.embedding_cache_dir),
    )


def embed_one(text: str) -> List[float]:
    return next(iter(_model().embed([text]))).tolist()


def embed_many(texts: List[str]) -> List[List[float]]:
    return [vector.tolist() for vector in _model().embed(texts)]
