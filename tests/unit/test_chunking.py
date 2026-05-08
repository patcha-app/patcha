import pytest
from patcha.utils.chunking import chunk_text


@pytest.mark.unit
def test_short_text_returns_single_chunk():
    result = chunk_text("hello world", max_tokens=100, overlap=10)
    assert result == ["hello world"]


@pytest.mark.unit
def test_long_text_splits():
    word = "token " * 10
    long_text = word * 200
    chunks = chunk_text(long_text, max_tokens=100, overlap=10)
    assert len(chunks) > 1


@pytest.mark.unit
def test_each_chunk_within_max_tokens():
    import tiktoken

    enc = tiktoken.get_encoding("cl100k_base")
    long_text = "hello world " * 500
    chunks = chunk_text(long_text, max_tokens=50, overlap=5)
    for chunk in chunks:
        assert len(enc.encode(chunk)) <= 50


@pytest.mark.unit
def test_overlap_preserved():
    import tiktoken

    enc = tiktoken.get_encoding("cl100k_base")
    long_text = "word " * 300
    chunks = chunk_text(long_text, max_tokens=50, overlap=10)
    if len(chunks) >= 2:
        tail = enc.encode(chunks[0])[-10:]
        head = enc.encode(chunks[1])[:10]
        assert tail == head


@pytest.mark.unit
def test_empty_string_returns_single_chunk():
    result = chunk_text("", max_tokens=100, overlap=10)
    assert result == [""]


@pytest.mark.unit
def test_overlap_gte_max_tokens_raises():
    with pytest.raises(ValueError):
        chunk_text("text", max_tokens=10, overlap=10)
