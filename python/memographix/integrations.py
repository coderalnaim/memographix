from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any

from .agent import SUPPORTED_AGENTS


def install_mcp_integrations(root: Path, agents: list[str]) -> list[dict[str, Any]]:
    return [_install_agent(root, agent) for agent in agents]


def integration_status(root: Path) -> list[dict[str, Any]]:
    return [_status_for_agent(root, agent) for agent in SUPPORTED_AGENTS]


def repair_mcp_configs(root: Path) -> dict[str, Any]:
    actions = [_repair_codex_config(root)]
    for path, section in (
        (root / ".mcp.json", "mcpServers"),
        (root / ".cursor" / "mcp.json", "mcpServers"),
        (root / ".vscode" / "mcp.json", "servers"),
        (root / ".gemini" / "settings.json", "mcpServers"),
        (root / "opencode.json", "mcp"),
        (_windsurf_config_path(), "mcpServers"),
    ):
        actions.append(_repair_json_config(path, section))
    removed = sum(len(item.get("removed", [])) for item in actions)
    return {
        "root": str(root),
        "removed_entries": removed,
        "actions": actions,
        "integrations": integration_status(root),
    }


def _install_agent(root: Path, agent: str) -> dict[str, Any]:
    if agent == "codex":
        return _write_codex_config(root)
    if agent == "claude":
        return _write_mcp_servers_json(root, agent, root / ".mcp.json")
    if agent == "cursor":
        return _write_mcp_servers_json(root, agent, root / ".cursor" / "mcp.json")
    if agent == "copilot":
        return _write_vscode_mcp_json(root)
    if agent == "gemini":
        return _write_mcp_servers_json(root, agent, root / ".gemini" / "settings.json")
    if agent == "opencode":
        return _write_opencode_json(root)
    if agent == "windsurf":
        return _write_global_mcp_servers_json(root, agent, _windsurf_config_path())
    return _rules_only_status(root, agent)


def _status_for_agent(root: Path, agent: str) -> dict[str, Any]:
    if agent == "codex":
        return _codex_status(root)
    if agent == "claude":
        return _json_status(root, agent, root / ".mcp.json", "mcpServers")
    if agent == "cursor":
        return _json_status(root, agent, root / ".cursor" / "mcp.json", "mcpServers")
    if agent == "copilot":
        return _json_status(root, agent, root / ".vscode" / "mcp.json", "servers")
    if agent == "gemini":
        return _json_status(root, agent, root / ".gemini" / "settings.json", "mcpServers")
    if agent == "opencode":
        return _opencode_status(root)
    if agent == "windsurf":
        return _json_status(root, agent, _windsurf_config_path(), "mcpServers")
    return _rules_only_status(root, agent)


def _write_mcp_servers_json(root: Path, agent: str, path: Path) -> dict[str, Any]:
    return _merge_json_server(
        root=root,
        agent=agent,
        path=path,
        section="mcpServers",
        server=_stdio_server(root),
        mode="mcp",
    )


def _write_global_mcp_servers_json(root: Path, agent: str, path: Path) -> dict[str, Any]:
    return _merge_json_server(
        root=root,
        agent=agent,
        path=path,
        section="mcpServers",
        server=_global_stdio_server(),
        mode="mcp",
    )


def _write_vscode_mcp_json(root: Path) -> dict[str, Any]:
    server = _stdio_server(root)
    server["type"] = "stdio"
    return _merge_json_server(
        root=root,
        agent="copilot",
        path=root / ".vscode" / "mcp.json",
        section="servers",
        server=server,
        mode="mcp",
    )


def _write_opencode_json(root: Path) -> dict[str, Any]:
    return _merge_json_server(
        root=root,
        agent="opencode",
        path=root / "opencode.json",
        section="mcp",
        server={
            "type": "local",
            "command": [_mgx_command(), "--root", str(root), "serve"],
            "enabled": True,
        },
        mode="mcp",
    )


def _merge_json_server(
    *,
    root: Path,
    agent: str,
    path: Path,
    section: str,
    server: dict[str, Any],
    mode: str,
) -> dict[str, Any]:
    data, error = _load_json(path)
    if error:
        return _integration_result(
            root,
            agent,
            mode=mode,
            path=path,
            registered=False,
            updated=False,
            reason=error,
        )
    servers = data.setdefault(section, {})
    server_name = mcp_server_name(root)
    updated = servers.get(server_name) != server
    servers[server_name] = server
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return _integration_result(root, agent, mode=mode, path=path, registered=True, updated=updated)


def _write_codex_config(root: Path) -> dict[str, Any]:
    path = _codex_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    server_name = mcp_server_name(root)
    header = f"[mcp_servers.{server_name}]"
    block = "\n".join(
        [
            "",
            header,
            f"command = {_toml_string(_mgx_command())}",
            f"args = [{_toml_string('serve')}]",
            "",
        ]
    )
    if header not in existing:
        path.write_text(existing.rstrip() + block, encoding="utf-8")
        updated = True
    else:
        updated = False
    return _integration_result(
        root, "codex", mode="mcp", path=path, registered=True, updated=updated
    )


def _codex_status(root: Path) -> dict[str, Any]:
    path = _codex_config_path()
    registered = path.exists() and f"[mcp_servers.{mcp_server_name(root)}]" in path.read_text(
        encoding="utf-8"
    )
    return _integration_result(
        root, "codex", mode="mcp", path=path, registered=registered, updated=False
    )


def _json_status(root: Path, agent: str, path: Path, section: str) -> dict[str, Any]:
    data, error = _load_json(path)
    registered = False if error else mcp_server_name(root) in data.get(section, {})
    return _integration_result(
        root,
        agent,
        mode="mcp",
        path=path,
        registered=registered,
        updated=False,
        reason=error or "",
    )


def _opencode_status(root: Path) -> dict[str, Any]:
    data, error = _load_json(root / "opencode.json")
    registered = False if error else mcp_server_name(root) in data.get("mcp", {})
    return _integration_result(
        root,
        "opencode",
        mode="mcp",
        path=root / "opencode.json",
        registered=registered,
        updated=False,
        reason=error or "",
    )


def _rules_only_status(root: Path, agent: str) -> dict[str, Any]:
    return _integration_result(
        root,
        agent,
        mode="rules",
        path=root / "AGENTS.md",
        registered=(root / "AGENTS.md").exists(),
        updated=False,
        reason="native MCP auto-config is not available for this agent",
    )


def _integration_result(
    root: Path,
    agent: str,
    *,
    mode: str,
    path: Path,
    registered: bool,
    updated: bool,
    reason: str = "",
) -> dict[str, Any]:
    return {
        "agent": agent,
        "mode": mode,
        "server": mcp_server_name(root),
        "registered": registered,
        "ready": registered,
        "updated": updated,
        "path": str(path),
        "reason": reason,
    }


def _load_json(path: Path) -> tuple[dict[str, Any], str]:
    if not path.exists() or not path.read_text(encoding="utf-8").strip():
        return {}, ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {}, f"invalid JSON in {path}: {exc}"
    if not isinstance(data, dict):
        return {}, f"expected JSON object in {path}"
    return data, ""


def _repair_json_config(path: Path, section: str) -> dict[str, Any]:
    data, error = _load_json(path)
    if error or not path.exists():
        return {"path": str(path), "removed": [], "reason": error or "not present"}
    servers = data.get(section)
    if not isinstance(servers, dict):
        return {"path": str(path), "removed": [], "reason": f"missing {section}"}
    removed = [
        key
        for key in list(servers)
        if _is_memographix_server_key(key) and key != "memographix"
    ]
    for key in removed:
        servers.pop(key, None)
    if removed:
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return {"path": str(path), "removed": removed, "reason": ""}


def _repair_codex_config(root: Path) -> dict[str, Any]:
    path = _codex_config_path()
    if not path.exists():
        return {"path": str(path), "removed": [], "reason": "not present"}
    text = path.read_text(encoding="utf-8")
    blocks = _split_toml_blocks(text)
    kept: list[str] = []
    removed: list[str] = []
    for header, block in blocks:
        if header and _is_memographix_toml_header(header) and header != "[mcp_servers.memographix]":
            removed.append(header.strip("[]").split(".", 1)[1])
            continue
        kept.append(block)
    if removed:
        path.write_text("".join(kept).rstrip() + "\n", encoding="utf-8")
    return {"path": str(path), "removed": removed, "reason": ""}


def _split_toml_blocks(text: str) -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    current_header = ""
    current_lines: list[str] = []
    for line in text.splitlines(keepends=True):
        if line.startswith("[") and line.strip().endswith("]"):
            if current_lines:
                blocks.append((current_header, "".join(current_lines)))
            current_header = line.strip()
            current_lines = [line]
        else:
            current_lines.append(line)
    if current_lines:
        blocks.append((current_header, "".join(current_lines)))
    return blocks


def _is_memographix_toml_header(header: str) -> bool:
    return header.startswith("[mcp_servers.memographix")


def _is_memographix_server_key(key: str) -> bool:
    return key == "memographix" or key.startswith("memographix_") or key.startswith("memographix-")


def _stdio_server(root: Path) -> dict[str, Any]:
    return {
        "command": _mgx_command(),
        "args": ["--root", str(root), "serve"],
    }


def _global_stdio_server() -> dict[str, Any]:
    return {
        "command": _mgx_command(),
        "args": ["serve"],
    }


def mcp_server_name(root: Path) -> str:
    return "memographix"


def legacy_mcp_server_name(root: Path) -> str:
    slug = re.sub(r"[^a-z0-9_]+", "_", root.name.lower()).strip("_") or "repo"
    digest = hashlib.sha1(str(root).encode("utf-8")).hexdigest()[:8]
    return f"memographix_{slug}_{digest}"


def _codex_config_path() -> Path:
    override = os.environ.get("MEMOGRAPHIX_CODEX_CONFIG")
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".codex" / "config.toml"


def _windsurf_config_path() -> Path:
    override = os.environ.get("MEMOGRAPHIX_WINDSURF_CONFIG")
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".codeium" / "mcp_config.json"


def _mgx_command() -> str:
    current = Path(sys.argv[0])
    if current.name in {"mgx", "memographix"} and current.exists():
        return str(current.resolve())
    return shutil.which("mgx") or "mgx"


def _toml_string(value: str) -> str:
    return json.dumps(value)
