import json
from datetime import datetime, timedelta, timezone

import pytest

from patcha.db.activity_graph import ActivityGraph
from patcha.db.models import Event, EventType
from patcha.db.retrieval.graph_context import (
    find_connected,
    get_activity_context,
    get_session,
)

T0 = datetime(2026, 6, 1, 14, 0, 0, tzinfo=timezone.utc)


def _event(t, etype, meta=None, raw="", sdi=None, project=None):
    return Event(
        timestamp=t,
        type=etype,
        source="test",
        raw_content=raw,
        metadata=meta or {},
        source_doc_id=sdi,
        project=project,
    )


@pytest.fixture
def graph(tmp_path):
    g = ActivityGraph(db_path=str(tmp_path / "graph.db"))
    events = [
        _event(
            T0,
            EventType.SCREEN,
            {"app_name": "Zed", "window_title": "accessibility.py"},
            "x",
            "screen::1::aa",
        ),
        _event(
            T0 + timedelta(minutes=2),
            EventType.TERMINAL,
            raw=json.dumps({"command": "pytest"}),
            sdi="term::1",
            project="patcha",
        ),
        _event(
            T0 + timedelta(minutes=4),
            EventType.SCREEN,
            {
                "app_name": "Slack",
                "window_title": "general",
                "trigger": "app_switch",
                "transition": "switch",
            },
            "y",
            "screen::2::bb",
        ),
        _event(
            T0 + timedelta(minutes=6),
            EventType.GIT_COMMIT,
            raw=json.dumps(
                {
                    "message": "fix",
                    "files_changed": ["patcha/collectors/accessibility.py"],
                }
            ),
            sdi="repo::abc",
            project="patcha",
        ),
    ]
    prev = None
    for ev in events:
        sid = g.current_session(ev.timestamp)
        prev = g.upsert_event(ev, prev_event_id=prev, session_id=sid)
    return g


def test_context_before_app_anchor(graph):
    out = get_activity_context(graph, app="Slack", direction="before", count=3)
    assert "Zed — accessibility.py" in out
    assert "> [" in out  # anchor marker
    assert "Session:" in out


def test_context_anchor_is_app_case_insensitive(graph):
    out = get_activity_context(graph, app="slack", direction="before")
    assert "No matching event" not in out


def test_context_time_anchor(graph):
    out = get_activity_context(graph, time="2026-06-01T14:02:00Z", count=1)
    # anchor is the terminal event nearest 14:02
    assert "> [2026-06-01 14:02] terminal" in out


def test_session_lists_all_members(graph):
    out = get_session(graph, app="Slack")
    assert "Events (4):" in out
    assert "Apps: Slack, Zed" in out


def test_find_connected_file(graph):
    out = find_connected(graph, file="patcha/collectors/accessibility.py")
    assert "git_commit" in out
    assert "Events (1):" in out


def test_find_connected_project(graph):
    out = find_connected(graph, project="patcha")
    assert "Events (2):" in out


def test_find_connected_missing_node(graph):
    out = find_connected(graph, file="does/not/exist.py")
    assert "No such node" in out


def test_find_connected_requires_arg(graph):
    out = find_connected(graph)
    assert "Provide one of" in out


def test_context_no_match(graph):
    out = get_activity_context(graph, app="Photoshop")
    assert "No matching event found" in out
