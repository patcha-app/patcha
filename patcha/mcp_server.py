"""MCP server — exposes patcha context retrieval as tools for AI agents."""

import asyncio
import logging
import socket
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
from patcha.db.store import VectorStore
from patcha.process import EventPreprocessor

server = Server("patcha")

_store: VectorStore | None = None
_preprocessor: EventPreprocessor | None = None


def _get_store() -> VectorStore:
    global _store
    if _store is None:
        _store = VectorStore()
    return _store


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
                "For git commits and stashes, the full diff is included in the result."
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
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="get_recent_activity",
            description=(
                "Get a deduped log of the user's raw activity over the last N hours. "
                "Use this for broader historical context — what the user has been working on "
                "over the last few hours, not just the last few minutes."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "hours": {
                        "type": "integer",
                        "description": "How many hours back to look. Default 3.",
                        "default": 3,
                    }
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
        result = search_activity(_get_store(), _get_preprocessor(), query, limit=limit)

    elif name == "get_recent_activity":
        hours = arguments.get("hours", 3)
        result = get_recent_activity(_get_store(), hours=hours)

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


_SETTINGS_DB = Path.home() / "Library" / "Application Support" / "patcha" / "settings.db"


def _find_free_port(start: int, attempts: int = 10) -> int:
    for port in range(start, start + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"No free port found in range {start}–{start + attempts - 1}")


def _write_port(port: int) -> None:
    _SETTINGS_DB.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(_SETTINGS_DB) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('mcp_port', ?)", (str(port),))


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
    "--port", default=7861, show_default=True, help="Port for HTTP transport."
)
@click.option(
    "--host", default="127.0.0.1", show_default=True, help="Host for HTTP transport."
)
def main(transport: str, port: int, host: str) -> None:
    logging.basicConfig(level=logging.WARNING)
    if transport == "http":
        actual_port = _find_free_port(port)
        _write_port(actual_port)
        try:
            app = _build_http_app()
            uvicorn.run(app, host=host, port=actual_port)
        finally:
            _clear_port()
    else:
        asyncio.run(_serve_stdio())
