"""MCP server — exposes patcha context retrieval as tools for AI agents."""

import asyncio
import logging
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import click
import uvicorn
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.types import TextContent, Tool
from starlette.applications import Starlette
from starlette.routing import Mount

from patcha.db.retrieval.context import (
    get_working_memory,
    get_recent_activity,
    search_activity,
)
from patcha.db.retrieval.graph_context import (
    get_activity_context,
    get_session,
    find_connected,
)
from patcha.db.activity_graph import ActivityGraph
from patcha.db.store import VectorStore
from patcha.process import EventPreprocessor

server = Server("patcha")

_store: VectorStore | None = None
_preprocessor: EventPreprocessor | None = None
_graph: ActivityGraph | None = None


def _get_store() -> VectorStore:
    global _store
    if _store is None:
        _store = VectorStore()
    return _store


def _get_graph() -> ActivityGraph:
    global _graph
    if _graph is None:
        _graph = ActivityGraph()
    return _graph


def _get_preprocessor() -> EventPreprocessor:
    global _preprocessor
    if _preprocessor is None:
        _preprocessor = EventPreprocessor()
    return _preprocessor


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="get_working_memory",
            description=(
                "Get a compact summary of the user's recent device activity. "
                "Shows open apps (with app-switch transitions noted), browser research, "
                "terminal commands, and git activity from the last N minutes. "
                "Use this to understand what the user is currently working on before "
                "answering questions or making suggestions."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "minutes": {
                        "type": "integer",
                        "description": "How many minutes back to look. Default 15.",
                        "default": 15,
                    }
                },
            },
        ),
        Tool(
            name="search_activity",
            description=(
                "Search the user's full activity history semantically. "
                "Use this to find specific past work — e.g. 'qdrant vector search setup', "
                "'authentication bug fix', 'npm install error', 'what changed in the auth fix'. "
                "Returns the most relevant past events with similarity scores. "
                "For git commits and stashes, the full diff is included in the result. "
                "Optionally filter to events from a specific app (e.g. 'Arc', 'Zed', 'WezTerm')."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What to search for in past activity.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Number of results to return. Default 10.",
                        "default": 10,
                    },
                    "app": {
                        "type": "string",
                        "description": (
                            "Filter to a specific app (e.g. 'Arc', 'Zed', 'WezTerm', 'Cursor'). "
                            "Applies to screen and window events which capture app_name."
                        ),
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="get_recent_activity",
            description=(
                "Get a deduped log of the user's raw activity over the last N hours. "
                "Use this for broader historical context — what the user has been working on "
                "over the last few hours, not just the last few minutes. "
                "Optionally filter to a specific app."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "hours": {
                        "type": "integer",
                        "description": "How many hours back to look. Default 3.",
                        "default": 3,
                    },
                    "app": {
                        "type": "string",
                        "description": (
                            "Filter to a specific app (e.g. 'Arc', 'Zed', 'WezTerm', 'Cursor'). "
                            "Applies to screen and window events which capture app_name."
                        ),
                    },
                },
            },
        ),
        Tool(
            name="get_activity_context",
            description=(
                "Get the temporal context around a moment of activity — what the user was "
                "doing right before and after a specific event. Answers questions like "
                "'what was I doing before I opened Slack?' or 'what happened around 2pm?'. "
                "Anchor the moment by app name, by time, or both (the app's event nearest "
                "that time). Returns the surrounding events in chronological order plus the "
                "work session they belonged to. Use this for ordering/adjacency questions "
                "that semantic search can't answer."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "app": {
                        "type": "string",
                        "description": "Anchor on the most relevant event from this app (e.g. 'Slack').",
                    },
                    "time": {
                        "type": "string",
                        "description": "Anchor near this ISO 8601 timestamp (e.g. '2026-06-01T14:30:00Z').",
                    },
                    "direction": {
                        "type": "string",
                        "enum": ["before", "after", "both"],
                        "description": "Which side of the anchor to show. Default 'both'.",
                        "default": "both",
                    },
                    "count": {
                        "type": "integer",
                        "description": "How many events on each side. Default 3.",
                        "default": 3,
                    },
                },
            },
        ),
        Tool(
            name="get_session",
            description=(
                "Get the full work session around a moment — every app and event between "
                "two idle gaps. Answers 'what apps did I use in the same session as commit X?' "
                "or 'show me everything from the session around 3pm'. Anchor by app name, "
                "time, or both. Returns the session time span, the apps involved, and the "
                "events in chronological order."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "app": {
                        "type": "string",
                        "description": "Anchor on the most relevant event from this app.",
                    },
                    "time": {
                        "type": "string",
                        "description": "Anchor near this ISO 8601 timestamp.",
                    },
                },
            },
        ),
        Tool(
            name="find_connected",
            description=(
                "Find every event structurally connected to a file, project, url, or app — "
                "across event types. Answers 'show me all activity touching accessibility.py' "
                "or 'everything related to the patcha project'. Provide exactly one of file, "
                "project, url, or app. Returns the connected events in chronological order."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "description": "File path, e.g. 'patcha/collectors/accessibility.py'.",
                    },
                    "project": {
                        "type": "string",
                        "description": "Project name, e.g. 'patcha'.",
                    },
                    "url": {"type": "string", "description": "Exact URL visited."},
                    "app": {"type": "string", "description": "App name, e.g. 'Arc'."},
                },
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    if name == "get_working_memory":
        minutes = arguments.get("minutes", 15)
        result = get_working_memory(_get_store(), minutes=minutes)

    elif name == "search_activity":
        query = arguments["query"]
        limit = arguments.get("limit", 5)
        app = arguments.get("app")
        result = search_activity(
            _get_store(), _get_preprocessor(), query, limit=limit, app_filter=app
        )

    elif name == "get_recent_activity":
        hours = arguments.get("hours", 3)
        app = arguments.get("app")
        result = get_recent_activity(_get_store(), hours=hours, app_filter=app)

    elif name == "get_activity_context":
        result = get_activity_context(
            _get_graph(),
            app=arguments.get("app"),
            time=arguments.get("time"),
            direction=arguments.get("direction", "both"),
            count=arguments.get("count", 3),
        )

    elif name == "get_session":
        result = get_session(
            _get_graph(),
            app=arguments.get("app"),
            time=arguments.get("time"),
        )

    elif name == "find_connected":
        result = find_connected(
            _get_graph(),
            file=arguments.get("file"),
            project=arguments.get("project"),
            url=arguments.get("url"),
            app=arguments.get("app"),
        )

    else:
        result = f"Unknown tool: {name}"

    return [TextContent(type="text", text=result)]


def _build_http_app() -> Starlette:
    session_manager = StreamableHTTPSessionManager(app=server, stateless=True)

    @asynccontextmanager
    async def lifespan(app: Starlette):
        async with session_manager.run():
            yield

    return Starlette(
        routes=[Mount("/mcp", app=session_manager.handle_request)],
        lifespan=lifespan,
    )


async def _serve_stdio() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream, write_stream, server.create_initialization_options()
        )


_SETTINGS_DB = (
    Path.home() / "Library" / "Application Support" / "patcha" / "settings.db"
)


def _write_port(port: int) -> None:
    _SETTINGS_DB.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(_SETTINGS_DB) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)"
        )
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('mcp_port', ?)",
            (str(port),),
        )


def _clear_port() -> None:
    if not _SETTINGS_DB.exists():
        return
    with sqlite3.connect(_SETTINGS_DB) as conn:
        conn.execute("DELETE FROM settings WHERE key = 'mcp_port'")


@click.command()
@click.option(
    "--http",
    "transport",
    flag_value="http",
    default=False,
    help="Serve over HTTP instead of stdio.",
)
@click.option(
    "--stdio",
    "transport",
    flag_value="stdio",
    default=True,
    help="Serve over stdio (default).",
)
@click.option(
    "--port", default=6969, show_default=True, help="Port for HTTP transport."
)
@click.option(
    "--host", default="127.0.0.1", show_default=True, help="Host for HTTP transport."
)
def main(transport: str, port: int, host: str) -> None:
    logging.basicConfig(level=logging.WARNING)
    if transport == "http":
        _write_port(port)
        try:
            app = _build_http_app()
            uvicorn.run(app, host=host, port=port)
        finally:
            _clear_port()
    else:
        asyncio.run(_serve_stdio())
