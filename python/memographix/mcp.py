from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .integrations import current_mgx_command
from .registry import _configured_ancestor, list_registered_repos, resolve_repo
from .workspace import Workspace


def tool_resolve_task(
    root: str,
    question: str,
    token_budget: int = 800,
    repo: str | None = None,
    dry_run: bool = False,
    verification_id: str = "",
    agent: str = "",
) -> dict[str, Any]:
    workspace, error, resolution = _workspace_for(root, repo=repo, hint=question)
    if error:
        return _resolve_error(question, token_budget, error)
    data = workspace.automatic_context(
        question,
        budget=token_budget,
        dry_run=dry_run,
        source="mcp",
        verification_id=verification_id,
        agent=agent,
    )
    data["repo_root"] = str(workspace.root)
    data["matched_by"] = resolution.matched_by
    return data


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
    repo: str | None = None,
    resolve_event_id: int | None = None,
    verification_id: str = "",
    agent: str = "",
) -> dict[str, Any]:
    commands = commands or []
    tests = tests or []
    if validation:
        commands.extend(str(item) for item in validation.get("commands", []) or [])
        tests.extend(str(item) for item in validation.get("tests", []) or [])
        outcome = outcome or validation.get("outcome")
    workspace = _workspace_for_resolve_event(
        root,
        resolve_event_id=resolve_event_id,
        question=question,
    )
    if workspace:
        return workspace.capture(
            question=question,
            answer=answer,
            evidence=evidence,
            changed_files=changed_files,
            commands=commands,
            tests=tests,
            outcome=outcome,
            resolve_event_id=resolve_event_id,
            source="mcp",
            verification_id=verification_id,
            agent=agent,
        )
    workspace, error, _resolution = _workspace_for(root, repo=repo, hint=question)
    if error:
        return {
            "saved": False,
            "task_id": None,
            "reason": error["reason"],
            "evidence": [],
            "candidates": error.get("candidates", []),
            "final_status_line": f"Memographix: not saved - {error['reason']}",
        }
    return workspace.capture(
        question=question,
        answer=answer,
        evidence=evidence,
        changed_files=changed_files,
        commands=commands,
        tests=tests,
        outcome=outcome,
        resolve_event_id=resolve_event_id,
        source="mcp",
        verification_id=verification_id,
        agent=agent,
    )


def tool_remember_task(
    root: str,
    question: str,
    answer: str,
    evidence: list[str] | None = None,
    validation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return tool_capture_task(root, question, answer, evidence=evidence, validation=validation)


def tool_freshness_check(root: str, repo: str | None = None) -> dict[str, Any]:
    workspace, error, _resolution = _workspace_for(root, repo=repo)
    if error:
        return {
            "stale_tasks": [],
            "error": error["reason"],
            "candidates": error.get("candidates", []),
        }
    stale = workspace.changed()
    return {"stale_tasks": [task.to_dict() for task in stale]}


def tool_graph_stats(root: str, repo: str | None = None) -> dict[str, Any]:
    workspace, error, _resolution = _workspace_for(root, repo=repo)
    if error:
        return {"error": error["reason"], "candidates": error.get("candidates", [])}
    return workspace.stats()


def tool_list_repos() -> dict[str, Any]:
    return {"repos": list_registered_repos()}


def tool_activation_status(root: str, repo: str | None = None) -> dict[str, Any]:
    workspace, error, resolution = _workspace_for(root, repo=repo)
    if error:
        return {
            "resolved": False,
            "reason": error["reason"],
            "candidates": error.get("candidates", []),
        }
    status = workspace.status()
    savings = workspace.savings()
    verification = workspace.engine.verification_status()
    return {
        "resolved": True,
        "repo_root": str(workspace.root),
        "matched_by": resolution.matched_by,
        "enabled": status["enabled"],
        "strict_mode": status["strict_mode"],
        "configured": status["configured"],
        "setup_completed": status["setup_completed"],
        "stats": status["stats"],
        "last_resolve_at": savings.get("last_resolve_at", ""),
        "last_capture_at": savings.get("last_capture_at", ""),
        "last_capture_status": savings.get("last_capture_status", ""),
        "last_mcp_call_at": savings.get("last_mcp_call_at", ""),
        "last_tool_source": savings.get("last_tool_source", ""),
        "agent_verified": verification.get("agent_verified", False),
        "last_verified_agent_at": verification.get("last_verified_agent_at", ""),
        "last_verified_agent": verification.get("last_verified_agent", ""),
        "last_unverified_warning": verification.get("last_unverified_warning", ""),
        "repair_command": f'mgx --root "{workspace.root}" doctor --live --repair',
        "cli_fallback_ask": f'mgx --root "{workspace.root}" ask "<task>" --budget 800',
        "cli_fallback_capture_template": (
            f'mgx --root "{workspace.root}" remember --question "<task>" '
            '--answer "<final answer>" --evidence <repo-local evidence files> '
            '--commands <commands run> --tests <tests run> --outcome "<outcome>"'
        ),
        "configured_mgx_command": current_mgx_command(),
        "installed_version": __version__,
    }


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
                        "repo": {"type": "string"},
                        "dry_run": {"type": "boolean", "default": False},
                        "verification_id": {"type": "string"},
                        "agent": {"type": "string"},
                    },
                    "required": ["question"],
                },
            ),
            types.Tool(
                name="capture_task",
                description=(
                    "Automatically save a completed developer task when safe evidence exists."
                ),
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
                        "repo": {"type": "string"},
                        "resolve_event_id": {"type": "integer"},
                        "verification_id": {"type": "string"},
                        "agent": {"type": "string"},
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
                inputSchema={"type": "object", "properties": {"repo": {"type": "string"}}},
            ),
            types.Tool(
                name="list_repos",
                description="List repos registered for automatic Memographix activation.",
                inputSchema={"type": "object", "properties": {}},
            ),
            types.Tool(
                name="activation_status",
                description="Report whether Memographix can route to a configured repo.",
                inputSchema={"type": "object", "properties": {"repo": {"type": "string"}}},
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
        if name == "resolve_task":
            data = tool_resolve_task(
                workspace_root,
                arguments["question"],
                int(arguments.get("token_budget", 800)),
                arguments.get("repo"),
                bool(arguments.get("dry_run", False)),
                str(arguments.get("verification_id", "")),
                str(arguments.get("agent", "")),
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
                arguments.get("repo"),
                arguments.get("resolve_event_id"),
                str(arguments.get("verification_id", "")),
                str(arguments.get("agent", "")),
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
            data = tool_freshness_check(workspace_root, arguments.get("repo"))
        elif name == "graph_stats":
            data = tool_graph_stats(workspace_root, arguments.get("repo"))
        elif name == "list_repos":
            data = tool_list_repos()
        elif name == "activation_status":
            data = tool_activation_status(workspace_root, arguments.get("repo"))
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
    tools = [
        "resolve_task",
        "capture_task",
        "remember_task",
        "freshness_check",
        "graph_stats",
        "list_repos",
        "activation_status",
    ]
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            req = json.loads(line)
            tool = req.get("tool")
            if tool == "list_tools":
                data = {"tools": tools}
            elif tool == "resolve_task":
                data = tool_resolve_task(
                    workspace_root,
                    req["question"],
                    int(req.get("token_budget", 800)),
                    req.get("repo"),
                    bool(req.get("dry_run", False)),
                    str(req.get("verification_id", "")),
                    str(req.get("agent", "")),
                )
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
                    req.get("repo"),
                    req.get("resolve_event_id"),
                    str(req.get("verification_id", "")),
                    str(req.get("agent", "")),
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
                data = tool_freshness_check(workspace_root, req.get("repo"))
            elif tool == "graph_stats":
                data = tool_graph_stats(workspace_root, req.get("repo"))
            elif tool == "list_repos":
                data = tool_list_repos()
            elif tool == "activation_status":
                data = tool_activation_status(workspace_root, req.get("repo"))
            else:
                data = {"error": f"unknown tool: {tool}"}
        except Exception as exc:
            data = {"error": str(exc)}
        print(json.dumps(data), flush=True)


def _workspace_for(
    root: str,
    *,
    repo: str | None = None,
    hint: str = "",
):
    resolution = resolve_repo(repo, cwd=root, hint=hint)
    if not resolution.ok or resolution.root is None:
        return (
            None,
            {"reason": resolution.reason, "candidates": resolution.candidates or []},
            resolution,
        )
    return Workspace.open(resolution.root), None, resolution


def _workspace_for_resolve_event(
    root: str,
    *,
    resolve_event_id: int | None,
    question: str,
) -> Workspace | None:
    if not resolve_event_id:
        return None
    candidates: list[Path] = []
    current = _configured_ancestor(Path(root).resolve())
    if current:
        candidates.append(current)
    for item in list_registered_repos():
        try:
            candidate = Path(str(item.get("root", ""))).resolve()
        except OSError:
            continue
        if candidate not in candidates:
            candidates.append(candidate)
    matches = [
        candidate
        for candidate in candidates
        if _repo_has_resolve_event(candidate, resolve_event_id, question=question)
    ]
    exact = [
        candidate
        for candidate in matches
        if _repo_has_resolve_event(
            candidate,
            resolve_event_id,
            question=question,
            require_question=True,
        )
    ]
    if len(exact) == 1:
        return Workspace.open(exact[0])
    if len(matches) == 1:
        return Workspace.open(matches[0])
    return None


def _repo_has_resolve_event(
    root: Path,
    event_id: int,
    *,
    question: str,
    require_question: bool = False,
) -> bool:
    db_path = root / ".memographix" / "graph.sqlite"
    if not db_path.exists():
        return False
    try:
        with sqlite3.connect(db_path) as con:
            row = con.execute(
                "select question from memory_events where id = ? and event_type = 'resolve_task'",
                (event_id,),
            ).fetchone()
    except sqlite3.Error:
        return False
    if row is None:
        return False
    if require_question:
        return str(row[0]) == question
    return True


def _resolve_error(question: str, token_budget: int, error: dict[str, Any]) -> dict[str, Any]:
    reason = error["reason"]
    status = "needs_repo" if reason in {"repo required", "ambiguous repo"} else "disabled"
    return {
        "question": question,
        "status": status,
        "enabled": False,
        "strict_mode": False,
        "reason": reason,
        "candidates": error.get("candidates", []),
        "token_budget": token_budget,
        "estimated_tokens": 0,
        "summary": "Memographix could not route this request to one configured repo.",
        "matched_task": None,
        "evidence": [],
        "warnings": [reason],
        "context": "",
        "repo_root": "",
        "event_id": None,
        "verification_id": "",
    }
