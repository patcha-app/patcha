"""Text chunking with token-level overlap for embedding."""

from typing import List

import tiktoken

_ENCODING = tiktoken.get_encoding("cl100k_base")


def chunk_text(text: str, max_tokens: int, overlap: int = 100) -> List[str]:
    # overlap shared tokens keep adjacent chunks close in vector space
    if overlap >= max_tokens:
        raise ValueError(
            f"overlap ({overlap}) must be less than max_tokens ({max_tokens})"
        )

    tokens = _ENCODING.encode(text)
    if len(tokens) <= max_tokens:
        return [text]

    chunks = []
    step = max_tokens - overlap
    start = 0
    while start < len(tokens):
        end = min(start + max_tokens, len(tokens))
        chunks.append(_ENCODING.decode(tokens[start:end]))
        if end == len(tokens):
            break
        start += step

    return chunks
