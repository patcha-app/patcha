import json
from datetime import datetime, timezone

import pytest

from patcha.db.models import Event, EventType
from patcha.process import EventPreprocessor, _build_embedding_text


def make_event(type_=EventType.BROWSER, raw="content", project=None):
    return Event(
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        type=type_,
        source="test",
        raw_content=raw,
        project=project,
        source_doc_id="doc::001",
    )


@pytest.fixture
def preprocessor(mocker):
    mock_client = mocker.MagicMock()
    mocker.patch("patcha.process.OpenAI", return_value=mock_client)
    mocker.patch("patcha.process.config", openai_api_key="test-key",
                 max_embedding_tokens=8191, embedding_chunk_overlap=100)
    p = EventPreprocessor()
    fake_embedding = [0.1] * 1536
    p.client.embeddings.create.return_value.data = [
        mocker.MagicMock(embedding=fake_embedding)
    ]
    return p


@pytest.mark.integration
def test_process_event_single_chunk(preprocessor):
    event = make_event(raw=json.dumps({"title": "GH", "domain": "github.com"}))
    results = preprocessor.process_event(event)
    assert len(results) == 1
    assert results[0].embedding is not None
    assert len(results[0].embedding) == 1536


@pytest.mark.integration
def test_process_event_multi_chunk(preprocessor, mocker):
    mocker.patch("patcha.process.config", openai_api_key="test-key",
                 max_embedding_tokens=10, embedding_chunk_overlap=2)
    long_text = "word " * 200
    event = make_event(raw=long_text)
    results = preprocessor.process_event(event)
    assert len(results) > 1
    for i, r in enumerate(results):
        assert r.metadata.get("chunk_index") == i
        assert r.metadata.get("total_chunks") == len(results)


@pytest.mark.integration
def test_process_event_chunk_source_doc_id(preprocessor, mocker):
    mocker.patch("patcha.process.config", openai_api_key="test-key",
                 max_embedding_tokens=10, embedding_chunk_overlap=2)
    event = make_event(raw="word " * 200)
    results = preprocessor.process_event(event)
    assert len(results) > 1
    assert results[0].source_doc_id == "doc::001::chunk::0"
    assert results[1].source_doc_id == "doc::001::chunk::1"


@pytest.mark.integration
def test_process_event_embedding_failure(preprocessor):
    preprocessor.client.embeddings.create.side_effect = Exception("API down")
    event = make_event()
    results = preprocessor.process_events([event])
    assert len(results) == 1
    assert results[0].embedding is None


@pytest.mark.integration
def test_build_embedding_text_all_types_nonempty():
    for et in EventType:
        event = make_event(type_=et, raw="hello world content here")
        result = _build_embedding_text(event)
        assert result.strip(), f"empty for {et}"
