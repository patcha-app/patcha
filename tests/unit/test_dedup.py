import json
from datetime import datetime, timezone

import pytest

from patcha.db.models import Event, EventType


def make_event(type_, raw, embedding=None, meta=None):
    return Event(
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        type=type_,
        source="test",
        raw_content=raw,
        metadata=meta or {},
        embedding=embedding,
    )


@pytest.fixture
def compactor(mocker):
    mocker.patch("patcha.compaction.VectorStore")
    mocker.patch("patcha.compaction.TaskStore")
    mocker.patch("patcha.compaction.EventPreprocessor")
    mocker.patch("patcha.compaction.OpenAI")
    from patcha.compaction import DailyCompactor
    return DailyCompactor()


@pytest.mark.unit
def test_dedup_by_content_removes_duplicate_browser(compactor):
    url = json.dumps({"url": "https://github.com", "title": "GH", "domain": "github.com"})
    events = [make_event(EventType.BROWSER, url), make_event(EventType.BROWSER, url)]
    result = compactor._dedup_by_content(events)
    assert len(result) == 1


@pytest.mark.unit
def test_dedup_by_content_removes_duplicate_terminal(compactor):
    cmd = json.dumps({"command": "git status"})
    events = [make_event(EventType.TERMINAL, cmd), make_event(EventType.TERMINAL, cmd)]
    result = compactor._dedup_by_content(events)
    assert len(result) == 1


@pytest.mark.unit
def test_dedup_by_content_keeps_different(compactor):
    e1 = make_event(EventType.BROWSER, json.dumps({"url": "https://a.com", "title": "A", "domain": "a.com"}))
    e2 = make_event(EventType.BROWSER, json.dumps({"url": "https://b.com", "title": "B", "domain": "b.com"}))
    result = compactor._dedup_by_content([e1, e2])
    assert len(result) == 2


@pytest.mark.unit
def test_dedup_by_content_git_always_kept(compactor):
    raw = json.dumps({"message": "fix", "hash": "abc", "author": "x",
                      "timestamp": "2026-01-01", "files_changed": [], "insertions": 0,
                      "deletions": 0, "branch": "main"})
    events = [make_event(EventType.GIT_COMMIT, raw), make_event(EventType.GIT_COMMIT, raw)]
    result = compactor._dedup_by_content(events)
    assert len(result) == 2


@pytest.mark.unit
def test_dedup_by_vector_removes_near_duplicate(compactor):
    vec = [1.0, 0.0, 0.0] * 512
    e1 = make_event(EventType.BROWSER, "a", embedding=vec)
    e2 = make_event(EventType.BROWSER, "b", embedding=vec)
    result = compactor._dedup_by_vector([e1, e2])
    assert len(result) == 1


@pytest.mark.unit
def test_dedup_by_vector_keeps_different(compactor):
    v1 = [1.0, 0.0, 0.0] + [0.0] * (1536 - 3)
    v2 = [0.0, 1.0, 0.0] + [0.0] * (1536 - 3)
    e1 = make_event(EventType.BROWSER, "a", embedding=v1)
    e2 = make_event(EventType.BROWSER, "b", embedding=v2)
    result = compactor._dedup_by_vector([e1, e2])
    assert len(result) == 2


@pytest.mark.unit
def test_dedup_by_vector_no_embedding_always_kept(compactor):
    events = [make_event(EventType.BROWSER, "a"), make_event(EventType.BROWSER, "b")]
    result = compactor._dedup_by_vector(events)
    assert len(result) == 2
