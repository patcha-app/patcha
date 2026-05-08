import json
from datetime import datetime, timezone

import pytest

from patcha.db.models import Event, EventType
from patcha.process import _build_embedding_text


def make_event(type_, raw_content, project=None):
    return Event(
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        type=type_,
        source="test",
        raw_content=raw_content,
        project=project,
    )


@pytest.mark.unit
def test_browser_event_extracts_title_and_domain():
    raw = json.dumps(
        {"title": "GitHub", "url": "https://github.com", "domain": "github.com"}
    )
    result = _build_embedding_text(make_event(EventType.BROWSER, raw))
    assert "GitHub" in result
    assert "github.com" in result


@pytest.mark.unit
def test_terminal_event_extracts_command():
    raw = json.dumps(
        {"command": "git status", "timestamp": "2026-01-01", "working_dir": "/"}
    )
    result = _build_embedding_text(make_event(EventType.TERMINAL, raw))
    assert "git status" in result


@pytest.mark.unit
def test_git_commit_extracts_message_and_files():
    raw = json.dumps(
        {
            "hash": "abc123",
            "message": "fix bug",
            "author": "dev",
            "timestamp": "2026-01-01",
            "files_changed": ["foo.py", "bar.py"],
            "insertions": 1,
            "deletions": 0,
            "branch": "main",
            "diff": "",
        }
    )
    result = _build_embedding_text(make_event(EventType.GIT_COMMIT, raw))
    assert "fix bug" in result
    assert "foo.py" in result


@pytest.mark.unit
def test_screen_event_returns_raw_content():
    result = _build_embedding_text(make_event(EventType.SCREEN, "some screen text"))
    assert "some screen text" in result


@pytest.mark.unit
def test_project_appended():
    raw = json.dumps({"title": "Docs", "domain": "docs.python.org"})
    result = _build_embedding_text(
        make_event(EventType.BROWSER, raw, project="myproject")
    )
    assert "[myproject]" in result


@pytest.mark.unit
def test_no_project_no_brackets():
    raw = json.dumps({"title": "Docs", "domain": "docs.python.org"})
    result = _build_embedding_text(make_event(EventType.BROWSER, raw))
    assert "[" not in result


@pytest.mark.unit
def test_malformed_json_falls_back_to_raw():
    result = _build_embedding_text(make_event(EventType.BROWSER, "not-json"))
    assert "not-json" in result


@pytest.mark.unit
def test_all_event_types_produce_nonempty_string():
    for et in EventType:
        event = make_event(et, "some content")
        result = _build_embedding_text(event)
        assert result.strip(), f"empty result for {et}"
