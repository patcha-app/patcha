"""Local text embeddings via FastEmbed (ONNX, on-device)."""

import logging
from functools import lru_cache
from typing import List

from fastembed import TextEmbedding

from patcha.config import config

log = logging.getLogger(__name__)


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
