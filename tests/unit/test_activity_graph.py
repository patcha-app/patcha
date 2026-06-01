import json
from datetime import datetime, timedelta, timezone

import pytest

from patcha.db.activity_graph import (
    ActivityGraph,
    EDGE_CONTAINS,
    EDGE_SWITCHED_FROM,
)
from patcha.db.models import Event, EventType, Task, TaskPriority, TaskStatus

T0 = datetime(2026, 5, 31, 10, 0, 0, tzinfo=timezone.utc)


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
    return ActivityGraph(db_path=str(tmp_path / "graph.db"))


def _ingest(graph, events):
    prev = None
    for ev in events:
        sid = graph.current_session(ev.timestamp)
        prev = graph.upsert_event(ev, prev_event_id=prev, session_id=sid)


def test_upsert_is_idempotent(graph):
    ev1 = _event(T0, EventType.SCREEN, {"app_name": "Code", "window_title": "a.py"}, "x", "screen::1::aa")
    ev2 = _event(T0 + timedelta(seconds=30), EventType.SCREEN, {"app_name": "Slack"}, "y", "screen::2::bb")

    _ingest(graph, [ev1, ev2])
    first = graph.get_stats()
    _ingest(graph, [ev1, ev2])
    second = graph.get_stats()

    assert first == second
    assert first["nodes_by_label"]["ScreenEvent"] == 2


def test_followed_by_and_switched_from(graph):
    code = _event(T0, EventType.SCREEN, {"app_name": "Code", "window_title": "a.py"}, "x", "screen::1::aa")
    slack = _event(
        T0 + timedelta(seconds=30),
        EventType.SCREEN,
        {"app_name": "Slack", "trigger": "app_switch", "transition": "switch"},
        "y",
        "screen::2::bb",
    )
    _ingest(graph, [code, slack])

    slack_id = graph.event_node_id(slack)
    code_id = graph.event_node_id(code)

    before = graph.before(slack_id, 1)
    assert [n["props"]["app_name"] for n in before] == ["Code"]
    assert (slack_id, EDGE_SWITCHED_FROM, code_id) in graph._edge_keys


def test_git_files_and_events_touching(graph):
    commit = _event(
        T0,
        EventType.GIT_COMMIT,
        raw=json.dumps({"message": "fix", "files_changed": ["patcha/x.py", "patcha/y.py"]}),
        sdi="repo:abc",
        project="patcha",
    )
    _ingest(graph, [commit])

    touching = graph.events_touching("file::patcha/x.py")
    assert [n["id"] for n in touching] == ["repo:abc"]


def test_session_split_on_idle_gap(graph):
    a = _event(T0, EventType.SCREEN, {"app_name": "Code"}, "x", "screen::1::aa")
    b = _event(T0 + timedelta(seconds=30), EventType.SCREEN, {"app_name": "Slack"}, "y", "screen::2::bb")
    far = _event(T0 + timedelta(seconds=30 + graph.gap_seconds + 60), EventType.SCREEN, {"app_name": "Code"}, "z", "screen::3::cc")

    sid_a = graph.current_session(a.timestamp)
    graph.upsert_event(a, session_id=sid_a)
    sid_b = graph.current_session(b.timestamp)
    graph.upsert_event(b, session_id=sid_b)
    sid_far = graph.current_session(far.timestamp)
    graph.upsert_event(far, session_id=sid_far)

    assert sid_a == sid_b
    assert sid_far != sid_a
    assert sorted(n["props"]["name"] for n in graph.apps_in_session(sid_a)) == ["Code", "Slack"]


def test_persistence_reload(graph, tmp_path):
    ev = _event(T0, EventType.SCREEN, {"app_name": "Code", "window_title": "a.py"}, "x", "screen::1::aa")
    _ingest(graph, [ev])
    stats = graph.get_stats()

    reloaded = ActivityGraph(db_path=graph.db_path)
    assert reloaded.get_stats() == stats


def test_task_contains_edges_via_tskey(graph):
    code = _event(T0, EventType.SCREEN, {"app_name": "Code"}, "x", "screen::1::aa")
    commit = _event(
        T0 + timedelta(seconds=60),
        EventType.GIT_COMMIT,
        raw=json.dumps({"files_changed": ["patcha/x.py"]}),
        sdi="repo:abc",
        project="patcha",
    )
    _ingest(graph, [code, commit])

    task = Task(
        id="t1",
        title="T",
        description="d",
        status=TaskStatus.COMPLETED,
        priority=TaskPriority.MEDIUM,
        start_time=T0,
        end_time=T0 + timedelta(seconds=60),
        activities=[
            f"{code.type.value}_{code.timestamp.isoformat()}",
            f"{commit.type.value}_{commit.timestamp.isoformat()}",
        ],
    )
    # Compaction-reconstructed events carry no source_doc_id; the (label, ts_ms)
    # index must still resolve them to the live nodes.
    lookup = {
        aid: _event(ev.timestamp, ev.type, ev.metadata, ev.raw_content)
        for aid, ev in {
            task.activities[0]: code,
            task.activities[1]: commit,
        }.items()
    }
    graph.upsert_task(task, lookup)

    contains = sorted(d for (s, t, d) in graph._edge_keys if t == EDGE_CONTAINS)
    assert contains == ["repo:abc", "screen::1::aa"]
