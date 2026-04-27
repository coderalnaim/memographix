from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

EXPECTED_TOOLS = [
    "resolve_task",
    "capture_task",
    "remember_task",
    "freshness_check",
    "graph_stats",
    "list_repos",
    "activation_status",
]


def live_activation_check(
    root: str | Path,
    repo: str | None = None,
    timeout: float = 5.0,
) -> dict[str, Any]:
    root_path = Path(root).resolve()
    command = _mgx_command()
    request_lines = [
        {"tool": "list_tools"},
        {"tool": "list_repos"},
        {"tool": "activation_status", "repo": repo or str(root_path)},
        {
            "tool": "resolve_task",
            "repo": repo or str(root_path),
            "question": "Memographix live activation check",
            "token_budget": 200,
            "dry_run": True,
        },
    ]
    try:
        proc = subprocess.run(
            [*command, "--root", str(root_path), "serve", "--jsonl"],
            input="\n".join(json.dumps(item) for item in request_lines) + "\n",
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "ok": False,
            "server_starts": False,
            "tools_verified": False,
            "repo_routing_verified": False,
            "dry_run_resolve_verified": False,
            "reason": str(exc),
            "command": " ".join(command),
        }

    responses: list[dict[str, Any]] = []
    for line in proc.stdout.splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            value = {"error": f"invalid JSON response: {line}"}
        if isinstance(value, dict):
            responses.append(value)

    tools = responses[0].get("tools", []) if responses else []
    activation = responses[2] if len(responses) > 2 else {}
    resolve = responses[3] if len(responses) > 3 else {}
    tools_verified = all(name in tools for name in EXPECTED_TOOLS)
    repo_routing_verified = bool(activation.get("resolved"))
    dry_run_resolve_verified = bool(resolve.get("dry_run")) and resolve.get("event_id") is None
    ok = (
        proc.returncode == 0
        and tools_verified
        and repo_routing_verified
        and dry_run_resolve_verified
        and not any("error" in item for item in responses)
    )
    return {
        "ok": ok,
        "server_starts": proc.returncode == 0,
        "tools_verified": tools_verified,
        "repo_routing_verified": repo_routing_verified,
        "dry_run_resolve_verified": dry_run_resolve_verified,
        "expected_tools": EXPECTED_TOOLS,
        "tools": tools,
        "command": " ".join(command),
        "returncode": proc.returncode,
        "stderr": proc.stderr.strip(),
        "activation_status": activation,
        "dry_run_resolve": resolve,
    }


def _mgx_command() -> list[str]:
    current = Path(sys.argv[0])
    if current.name in {"mgx", "memographix"} and current.exists():
        return [str(current.resolve())]
    command = shutil.which("mgx")
    if command:
        return [command]
    return [sys.executable, "-c", "from memographix.cli import main; main()"]
