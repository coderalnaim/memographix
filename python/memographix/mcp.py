from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from .workspace import Workspace


def tool_resolve_task(root: str, question: str, token_budget: int = 800) -> dict[str, Any]:
    return Workspace.open(root).automatic_context(question, budget=token_budget)


def tool_capture_task(
    root: str,
    question: str,
    answer: str,
    evidence: list[str] | None = None,
    changed_files: list[str] | None = None,
    commands: list[str] | None = None,
    tests: list[str] | None = None,
    outcome: str | None = None,
    validation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    commands = commands or []
    tests = tests or []
    if validation:
        commands.extend(str(item) for item in validation.get("commands", []) or [])
        tests.extend(str(item) for item in validation.get("tests", []) or [])
        outcome = outcome or validation.get("outcome")
    return Workspace.open(root).capture(
        question=question,
        answer=answer,
        evidence=evidence,
        changed_files=changed_files,
        commands=commands,
        tests=tests,
        outcome=outcome,
    )


def tool_remember_task(
    root: str,
    question: str,
    answer: str,
    evidence: list[str] | None = None,
    validation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return tool_capture_task(root, question, answer, evidence=evidence, validation=validation)


def tool_freshness_check(root: str) -> dict[str, Any]:
    stale = Workspace.open(root).changed()
    return {"stale_tasks": [task.to_dict() for task in stale]}


def tool_graph_stats(root: str) -> dict[str, Any]:
    return Workspace.open(root).stats()


def serve(root: str = ".") -> None:
    """Start an MCP server when `mcp` is installed, otherwise a JSONL tool loop."""
    try:
        from mcp import types
        from mcp.server import Server
        from mcp.server.stdio import stdio_server
    except ImportError:
        serve_jsonl(root)
        return

    server = Server("memographix")
    workspace_root = str(Path(root).resolve())

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name="resolve_task",
                description="Return a fresh, token-budgeted context packet for a developer task.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "question": {"type": "string"},
                        "token_budget": {"type": "integer", "default": 800},
                    },
                    "required": ["question"],
                },
            ),
            types.Tool(
                name="capture_task",
                description="Automatically save a completed developer task when safe evidence exists.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "question": {"type": "string"},
                        "answer": {"type": "string"},
                        "evidence": {"type": "array", "items": {"type": "string"}},
                        "changed_files": {"type": "array", "items": {"type": "string"}},
                        "commands": {"type": "array", "items": {"type": "string"}},
                        "tests": {"type": "array", "items": {"type": "string"}},
                        "outcome": {"type": "string"},
                        "validation": {"type": "object"},
                    },
                    "required": ["question", "answer"],
                },
            ),
            types.Tool(
                name="remember_task",
                description="Backward-compatible alias for capture_task.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "question": {"type": "string"},
                        "answer": {"type": "string"},
                        "evidence": {"type": "array", "items": {"type": "string"}},
                        "validation": {"type": "object"},
                    },
                    "required": ["question", "answer"],
                },
            ),
            types.Tool(
                name="freshness_check",
                description="List remembered tasks whose evidence changed or disappeared.",
                inputSchema={"type": "object", "properties": {}},
            ),
            types.Tool(
                name="graph_stats",
                description="Return indexed file, symbol, edge, and task counts.",
                inputSchema={"type": "object", "properties": {}},
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
        if name == "resolve_task":
            data = tool_resolve_task(
                workspace_root,
                arguments["question"],
                int(arguments.get("token_budget", 800)),
            )
        elif name == "capture_task":
            data = tool_capture_task(
                workspace_root,
                arguments["question"],
                arguments["answer"],
                arguments.get("evidence"),
                arguments.get("changed_files"),
                arguments.get("commands"),
                arguments.get("tests"),
                arguments.get("outcome"),
                arguments.get("validation"),
            )
        elif name == "remember_task":
            data = tool_remember_task(
                workspace_root,
                arguments["question"],
                arguments["answer"],
                arguments.get("evidence"),
                arguments.get("validation"),
            )
        elif name == "freshness_check":
            data = tool_freshness_check(workspace_root)
        elif name == "graph_stats":
            data = tool_graph_stats(workspace_root)
        else:
            data = {"error": f"unknown tool: {name}"}
        return [types.TextContent(type="text", text=json.dumps(data, indent=2))]

    async def main() -> None:
        async with stdio_server() as streams:
            await server.run(streams[0], streams[1], server.create_initialization_options())

    asyncio.run(main())


def serve_jsonl(root: str = ".") -> None:
    """Tiny local fallback useful for smoke tests and non-MCP clients.

    Input one JSON object per line:
      {"tool":"resolve_task","question":"...","token_budget":800}
      {"tool":"capture_task","question":"...","answer":"...","evidence":["app.py"]}
    """
    workspace_root = str(Path(root).resolve())
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            req = json.loads(line)
            tool = req.get("tool")
            if tool == "resolve_task":
                data = tool_resolve_task(workspace_root, req["question"], int(req.get("token_budget", 800)))
            elif tool == "capture_task":
                data = tool_capture_task(
                    workspace_root,
                    req["question"],
                    req["answer"],
                    req.get("evidence"),
                    req.get("changed_files"),
                    req.get("commands"),
                    req.get("tests"),
                    req.get("outcome"),
                    req.get("validation"),
                )
            elif tool == "remember_task":
                data = tool_remember_task(
                    workspace_root,
                    req["question"],
                    req["answer"],
                    req.get("evidence"),
                    req.get("validation"),
                )
            elif tool == "freshness_check":
                data = tool_freshness_check(workspace_root)
            elif tool == "graph_stats":
                data = tool_graph_stats(workspace_root)
            else:
                data = {"error": f"unknown tool: {tool}"}
        except Exception as exc:
            data = {"error": str(exc)}
        print(json.dumps(data), flush=True)
