"""Structural/temporal context retrieval — Layer 3, backed by the activity graph.

Answers the queries vector search can't: what happened before/after an event, what
belonged to the same work session, and what events are connected to a file/project/url.
"""

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from patcha.db.activity_graph import ActivityGraph

log = logging.getLogger(__name__)


def _parse_time(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        log.warning("graph_context: could not parse time %r", value)
        return None


def _hhmm(ts: str) -> str:
    return (ts[:10] + " " + ts[11:16]) if len(ts) >= 16 else "??:??"


def _format_node(node: Optional[dict]) -> str:
    if not node:
        return "(unknown)"
    p = node["props"]
    ts = p.get("timestamp", "")
    etype = p.get("type", node.get("label", "?"))
    gist = p.get("gist")
    if gist:
        detail = gist
    else:
        app = p.get("app_name") or ""
        title = p.get("window_title") or ""
        detail = f"{app} — {title}" if title else (app or p.get("project") or "")
    if p.get("transition") == "switch" or p.get("trigger") in ("app_switch", "space_switch"):
        detail = f"[switch] {detail}"
    return f"[{_hhmm(ts)}] {etype}: {detail}".rstrip()


def _anchor_header(app: Optional[str], time: Optional[str]) -> str:
    parts = []
    if app:
        parts.append(f"app={app}")
    if time:
        parts.append(f"time={time}")
    return f" ({', '.join(parts)})" if parts else " (most recent)"


def get_activity_context(
    graph: "ActivityGraph",
    app: Optional[str] = None,
    time: Optional[str] = None,
    direction: str = "both",
    count: int = 3,
) -> str:
    """What was happening around an anchor event (before / after / both)."""
    anchor = graph.find_event(app=app, at=_parse_time(time))
    header = f"# Activity context{_anchor_header(app, time)}"
    if anchor is None:
        return f"{header}\nNo matching event found in the activity graph."

    anchor_id = anchor["id"]
    lines = []

    if direction in ("before", "both"):
        before = graph.before(anchor_id, count)
        for node in reversed(before):  # chronological order
            lines.append("  " + _format_node(node))

    lines.append("> " + _format_node(anchor))

    if direction in ("after", "both"):
        for node in graph.after(anchor_id, count):
            lines.append("  " + _format_node(node))

    session = graph.session_of(anchor_id)
    if session:
        apps = sorted(n["props"]["name"] for n in graph.apps_in_session(session["id"]))
        sp = session["props"]
        span = f"{_hhmm(sp.get('started_at',''))} → {_hhmm(sp.get('ended_at',''))}"
        lines.append(f"\nSession: {span} | apps: {', '.join(apps)}")

    return f"{header}\n" + "\n".join(lines)


def get_session(
    graph: "ActivityGraph",
    app: Optional[str] = None,
    time: Optional[str] = None,
) -> str:
    """Everything in the work session around an anchor (apps, events, time span)."""
    anchor = graph.find_event(app=app, at=_parse_time(time))
    header = f"# Session{_anchor_header(app, time)}"
    if anchor is None:
        return f"{header}\nNo matching event found in the activity graph."

    session = graph.session_of(anchor["id"])
    if session is None:
        return f"{header}\nAnchor event has no session."

    sp = session["props"]
    span = f"{_hhmm(sp.get('started_at',''))} → {_hhmm(sp.get('ended_at',''))}"
    apps = sorted(n["props"]["name"] for n in graph.apps_in_session(session["id"]))
    events = sorted(
        graph.events_in_session(session["id"]),
        key=lambda n: n["props"].get("ts_ms", 0),
    )
    lines = [f"Span: {span}", f"Apps: {', '.join(apps)}", f"Events ({len(events)}):"]
    lines += ["  " + _format_node(n) for n in events]
    return f"{header}\n" + "\n".join(lines)


def find_connected(
    graph: "ActivityGraph",
    file: Optional[str] = None,
    project: Optional[str] = None,
    url: Optional[str] = None,
    app: Optional[str] = None,
) -> str:
    """Events structurally connected to a file, project, url, or app (multi-hop)."""
    if file:
        node_id, label = f"file::{file}", f"file '{file}'"
    elif project:
        node_id, label = f"project::{project}", f"project '{project}'"
    elif url:
        node_id, label = f"url::{url}", f"url '{url}'"
    elif app:
        node_id, label = f"app::{app}", f"app '{app}'"
    else:
        return "# Connected activity\nProvide one of: file, project, url, app."

    header = f"# Connected activity — {label}"
    if graph.get_node(node_id) is None:
        return f"{header}\nNo such node in the activity graph."

    events = sorted(
        graph.events_touching(node_id),
        key=lambda n: n["props"].get("ts_ms", 0),
    )
    if not events:
        return f"{header}\nNode exists but no events reference it."

    lines = [f"Events ({len(events)}):"] + ["  " + _format_node(n) for n in events]
    return f"{header}\n" + "\n".join(lines)
