from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .activation import live_activation_check
from .agent import SUPPORTED_AGENTS, install_agent_rules
from .config import ensure_repo_config, load_repo_control, update_repo_control
from .engine import IndexStats, LocalEngine
from .integrations import install_mcp_integrations, integration_status, repair_mcp_configs
from .models import ContextPacket, TaskMemory
from .registry import list_registered_repos, register_repo


class Workspace:
    """High-level Memographix workspace API."""

    def __init__(self, root: str | Path = "."):
        self.root = Path(root).resolve()
        self.engine = LocalEngine(self.root)

    @classmethod
    def open(cls, root: str | Path = ".") -> Workspace:
        return cls(root)

    def init(self) -> Path:
        return self.engine.init()

    def index(self) -> IndexStats:
        return self.engine.index()

    def setup(self, agents: str = "all") -> dict[str, Any]:
        self.init()
        now = _utc_now()
        selected_agents = _parse_agents(agents)
        config_path, config_written = ensure_repo_config(
            self.root,
            {
                "enabled": True,
                "setup_completed": True,
                "setup_agents": ",".join(selected_agents),
                "disabled_reason": "",
                "last_enabled_at": now,
            },
        )
        stats = self.index()
        registry = register_repo(self.root)
        installed = []
        for agent in selected_agents:
            path = install_agent_rules(self.root, agent)
            installed.append({"agent": agent, "path": str(path)})
        mcp_config = self._write_mcp_config()
        integrations = install_mcp_integrations(self.root, selected_agents)
        codex_mcp = next((item for item in integrations if item["agent"] == "codex"), None)
        return {
            "root": str(self.root),
            "db": str(self.engine.db_path),
            "config": str(config_path),
            "config_written": config_written,
            "mcp_config": str(mcp_config),
            "integrations": integrations,
            "codex_mcp": codex_mcp,
            "agents": installed,
            "index": stats.to_dict(),
            "registry": registry,
            "status": self.status(),
        }

    def enable(self, reindex: bool = True) -> dict[str, Any]:
        update_repo_control(
            self.root,
            {
                "enabled": True,
                "setup_completed": True,
                "disabled_reason": "",
                "last_enabled_at": _utc_now(),
            },
        )
        stats = self.index().to_dict() if reindex else None
        registry = register_repo(self.root)
        status = self.status()
        return {
            "enabled": True,
            "reindexed": bool(reindex),
            "index": stats,
            "registry": registry,
            "status": status,
        }

    def disable(self, reason: str = "") -> dict[str, Any]:
        reason = reason.strip() or "repo disabled"
        update_repo_control(
            self.root,
            {
                "enabled": False,
                "setup_completed": True,
                "disabled_reason": reason,
                "last_disabled_at": _utc_now(),
            },
        )
        return {"enabled": False, "reason": reason, "status": self.status()}

    def is_enabled(self) -> bool:
        control = load_repo_control(self.root)
        return control.configured and control.setup_completed and control.enabled

    def context(
        self,
        question: str,
        budget: int = 800,
        *,
        refresh: bool = False,
        record_event: bool = False,
        source: str = "api",
    ) -> ContextPacket:
        packet, _event_id = self._context_with_event(
            question,
            budget=budget,
            refresh=refresh,
            record_event=record_event,
            source=source,
        )
        return packet

    def _context_with_event(
        self,
        question: str,
        budget: int = 800,
        *,
        refresh: bool = False,
        record_event: bool = False,
        source: str = "api",
    ) -> tuple[ContextPacket, int | None]:
        if refresh or self.stats()["files"] == 0:
            self.index()
        packet = self.engine.recall(question, budget=budget)
        event_id = None
        if record_event:
            event_id = self.engine.record_resolve_event(packet, source=source)
        return packet, event_id

    def recall(
        self,
        question: str,
        budget: int = 800,
        *,
        refresh: bool = False,
        record_event: bool = False,
        source: str = "api",
    ) -> ContextPacket:
        return self.context(
            question,
            budget=budget,
            refresh=refresh,
            record_event=record_event,
            source=source,
        )

    def remember(
        self,
        question: str,
        answer: str,
        evidence: list[str] | None = None,
        validation: dict[str, Any] | None = None,
    ) -> int:
        return self.engine.remember(
            question, answer, evidence_paths=evidence, validation=validation
        )

    def capture(
        self,
        question: str,
        answer: str,
        evidence: list[str] | None = None,
        changed_files: list[str] | None = None,
        commands: list[str] | None = None,
        tests: list[str] | None = None,
        outcome: str | None = None,
        resolve_event_id: int | None = None,
        source: str = "api",
    ) -> dict[str, Any]:
        control = load_repo_control(self.root)
        if not control.configured or not control.setup_completed:
            reason = control.disabled_status or "repo not configured"
            return {"saved": False, "task_id": None, "reason": reason, "evidence": []}
        if not control.enabled:
            reason = control.disabled_status or "repo disabled"
            return {"saved": False, "task_id": None, "reason": reason, "evidence": []}
        if self.stats()["files"] == 0:
            self.index()
        return self.engine.capture(
            question=question,
            answer=answer,
            evidence=evidence,
            changed_files=changed_files,
            commands=commands,
            tests=tests,
            outcome=outcome,
            resolve_event_id=resolve_event_id,
            source=source,
        )

    def changed(self) -> list[TaskMemory]:
        return self.engine.changed()

    def stats(self) -> dict[str, int]:
        return self.engine.stats()

    def savings(self, since_days: int = 30) -> dict[str, Any]:
        return self.engine.savings(since_days=since_days)

    def status(self) -> dict[str, Any]:
        control = load_repo_control(self.root)
        db_exists = self.engine.db_path.exists()
        stats = self.stats() if db_exists else {"files": 0, "symbols": 0, "edges": 0, "tasks": 0}
        last_indexed_at = self.engine.last_indexed_at() if db_exists else ""
        stale_count = len(self.changed()) if db_exists else 0
        integrations = integration_status(self.root)
        codex_mcp = next((item for item in integrations if item["agent"] == "codex"), None)
        setup_agents = control.setup_agents or (
            tuple(SUPPORTED_AGENTS) if control.configured and control.setup_completed else ()
        )
        registry_items = list_registered_repos()
        registry_entry = next(
            (item for item in registry_items if item.get("root") == str(self.root)),
            None,
        )
        agents = []
        for agent in SUPPORTED_AGENTS:
            path = _agent_rule_path(self.root, agent)
            agents.append(
                {
                    "agent": agent,
                    "selected": agent in setup_agents,
                    "rules_installed": path.exists(),
                    "path": str(path),
                }
            )
        return {
            "root": str(self.root),
            "configured": control.configured,
            "setup_completed": control.setup_completed,
            "setup_agents": list(setup_agents),
            "enabled": control.configured and control.setup_completed and control.enabled,
            "reason": control.disabled_status,
            "disabled_reason": control.disabled_reason,
            "last_enabled_at": control.last_enabled_at,
            "last_disabled_at": control.last_disabled_at,
            "config": str(control.config_path),
            "db_exists": db_exists,
            "mcp_config_exists": (self.root / ".memographix" / "mcp.json").exists(),
            "integrations": integrations,
            "codex_mcp_registered": bool(codex_mcp and codex_mcp["registered"]),
            "codex_mcp_config": codex_mcp["path"] if codex_mcp else "",
            "last_indexed_at": last_indexed_at,
            "stale_count": stale_count,
            "stats": stats,
            "agents": agents,
            "registry_registered": registry_entry is not None,
            "registry": registry_entry or {},
        }

    def automatic_context(
        self,
        question: str,
        budget: int = 800,
        *,
        dry_run: bool = False,
        source: str = "mcp",
    ) -> dict[str, Any]:
        status = self.status()
        if not status["configured"] or not status["setup_completed"]:
            return _disabled_response(
                question, budget, status["reason"] or "repo not configured", status
            )
        if not status["enabled"]:
            return _disabled_response(question, budget, status["reason"] or "repo disabled", status)
        packet, event_id = self._context_with_event(
            question,
            budget=budget,
            refresh=True,
            record_event=not dry_run,
            source=source,
        )
        data = packet.to_dict()
        data["enabled"] = True
        data["repo_root"] = str(self.root)
        data["event_id"] = event_id
        data["dry_run"] = dry_run
        return data

    def doctor(self, *, live: bool = False) -> dict[str, Any]:
        try:
            from . import _native  # noqa: F401

            native_available = True
        except ImportError:
            native_available = False
        try:
            import mcp  # noqa: F401

            mcp_package = True
        except ImportError:
            mcp_package = False
        status = self.status()
        selected_agents = set(status["setup_agents"])
        mcp_integrations = [
            item
            for item in status["integrations"]
            if item["mode"] == "mcp" and item["agent"] in selected_agents
        ]
        savings = self.savings() if status["db_exists"] else {}
        result = {
            "root": str(self.root),
            "db_exists": status["db_exists"],
            "config_exists": status["configured"],
            "configured": status["configured"],
            "setup_completed": status["setup_completed"],
            "setup_agents": status["setup_agents"],
            "enabled": status["enabled"],
            "disabled_reason": status["disabled_reason"],
            "status_reason": status["reason"],
            "mcp_config_exists": status["mcp_config_exists"],
            "codex_mcp_registered": status["codex_mcp_registered"],
            "codex_mcp_config": status["codex_mcp_config"],
            "mcp_package_installed": mcp_package,
            "native_index_available": native_available,
            "last_indexed_at": status["last_indexed_at"],
            "stale_count": status["stale_count"],
            "stats": status["stats"],
            "agents": status["agents"],
            "integrations": status["integrations"],
            "mcp_runtime_required": not mcp_package,
            "manual_mcp_config_required": any(not item["ready"] for item in mcp_integrations),
            "registry_registered": status["registry_registered"],
            "registry": status["registry"],
            "activation": {
                "resolve_events": savings.get("resolve_events", 0),
                "capture_events": savings.get("captures_saved", 0)
                + savings.get("skipped_captures", 0),
                "last_resolve_at": savings.get("last_resolve_at", ""),
                "last_capture_at": savings.get("last_capture_at", ""),
                "last_mcp_call_at": savings.get("last_mcp_call_at", ""),
                "last_tool_source": savings.get("last_tool_source", ""),
                "has_agent_calls": bool(
                    savings.get("last_resolve_at") or savings.get("last_capture_at")
                ),
            },
        }
        if live:
            result["live"] = live_activation_check(self.root)
        return result

    def repos(self) -> list[dict[str, Any]]:
        return list_registered_repos()

    def repair_mcp(self) -> dict[str, Any]:
        return repair_mcp_configs(self.root)

    def export_json(self) -> dict:
        return self.engine.export_json()

    def write_export(self, path: str | Path) -> Path:
        out = Path(path)
        out.write_text(json.dumps(self.export_json(), indent=2), encoding="utf-8")
        return out

    def _write_mcp_config(self) -> Path:
        path = self.root / ".memographix" / "mcp.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "mcpServers": {
                "memographix": {
                    "command": "mgx",
                    "args": ["--root", str(self.root), "serve"],
                }
            }
        }
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return path


def _parse_agents(agents: str) -> list[str]:
    if agents == "all":
        return list(SUPPORTED_AGENTS)
    selected = [agent.strip().lower() for agent in agents.split(",") if agent.strip()]
    unknown = sorted(set(selected) - set(SUPPORTED_AGENTS))
    if unknown:
        raise ValueError(f"unknown agent(s): {', '.join(unknown)}")
    return selected


def _agent_rule_path(root: Path, agent: str) -> Path:
    if agent == "claude":
        return root / "CLAUDE.md"
    if agent == "gemini":
        return root / "GEMINI.md"
    if agent == "cursor":
        return root / ".cursor" / "rules" / "memographix.mdc"
    if agent == "copilot":
        return root / ".github" / "copilot-instructions.md"
    return root / "AGENTS.md"


def _disabled_response(
    question: str,
    budget: int,
    reason: str,
    status: dict[str, Any],
) -> dict[str, Any]:
    return {
        "question": question,
        "status": "disabled",
        "enabled": False,
        "reason": reason,
        "token_budget": budget,
        "estimated_tokens": 0,
        "summary": "Memographix automatic memory is disabled for this repository.",
        "matched_task": None,
        "evidence": [],
        "warnings": [reason],
        "context": "",
        "repo_status": status,
        "repo_root": str(status.get("root", "")),
        "event_id": None,
        "dry_run": False,
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
