"""MCP server — exposes memorai context retrieval as tools for AI agents."""

import asyncio
import logging
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from memorai.db.retrieval.context import get_working_memory, search_activity
from memorai.db.store import VectorStore
from memorai.process import EventPreprocessor
from memorai.utils.compaction import Compactor

server = Server("memorai")

_store: VectorStore | None = None
_preprocessor: EventPreprocessor | None = None
_compactor: Compactor | None = None


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


def _get_compactor() -> Compactor:
    global _compactor
    if _compactor is None:
        _compactor = Compactor()
    return _compactor


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="get_working_memory",
            description=(
                "Get a compact summary of the user's recent device activity. "
                "Shows open apps, browser research, terminal commands, and git activity "
                "from the last N minutes. Use this to understand what the user is currently "
                "working on before answering questions or making suggestions."
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
                "'authentication bug fix', 'npm install error'. "
                "Returns the most relevant past events with similarity scores."
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
                        "description": "Number of results to return. Default 5.",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="get_recent_digests",
            description=(
                "Get compact AI-generated summaries of recent activity windows (each ~30 min). "
                "Use this for broader historical context — what the user has been working on "
                "over the last few hours, not just the last few minutes."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "n": {
                        "type": "integer",
                        "description": "Number of digest windows to return. Default 3.",
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

    elif name == "get_recent_digests":
        n = arguments.get("n", 3)
        result = _get_compactor().get_recent_digests(n=n)

    else:
        result = f"Unknown tool: {name}"

    return [TextContent(type="text", text=result)]


async def _serve() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(_serve())
