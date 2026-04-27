from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .activation import live_activation_check
from .agent import SUPPORTED_AGENTS, install_agent_rules
from .config import ensure_repo_config, load_repo_control, update_repo_control
from .engine import IndexStats, LocalEngine
from .integrations import (
    current_mgx_command,
    install_mcp_integrations,
    integration_status,
    repair_mcp_configs,
)
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
                "strict_agent_memory": True,
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
        verification_id: str = "",
        agent: str = "",
    ) -> ContextPacket:
        packet, _event_id = self._context_with_event(
            question,
            budget=budget,
            refresh=refresh,
            record_event=record_event,
            source=source,
            verification_id=verification_id,
            agent=agent,
        )
        return packet

    def resolve(
        self,
        question: str,
        budget: int = 800,
        *,
        refresh: bool = False,
        record_event: bool = False,
        source: str = "api",
        verification_id: str = "",
        agent: str = "",
    ) -> dict[str, Any]:
        packet, event_id = self._context_with_event(
            question,
            budget=budget,
            refresh=refresh,
            record_event=record_event,
            source=source,
            verification_id=verification_id,
            agent=agent,
        )
        data = packet.to_dict()
        data["repo_root"] = str(self.root)
        data["event_id"] = event_id
        data["verification_id"] = verification_id
        data["source"] = source
        return data

    def _context_with_event(
        self,
        question: str,
        budget: int = 800,
        *,
        refresh: bool = False,
        record_event: bool = False,
        source: str = "api",
        verification_id: str = "",
        agent: str = "",
    ) -> tuple[ContextPacket, int | None]:
        if refresh or self.stats()["files"] == 0:
            self.index()
        packet = self.engine.recall(question, budget=budget)
        event_id = None
        if record_event:
            event_id = self.engine.record_resolve_event(
                packet,
                source=source,
                verification_id=verification_id,
                agent=agent,
            )
        return packet, event_id

    def recall(
        self,
        question: str,
        budget: int = 800,
        *,
        refresh: bool = False,
        record_event: bool = False,
        source: str = "api",
        verification_id: str = "",
        agent: str = "",
    ) -> ContextPacket:
        return self.context(
            question,
            budget=budget,
            refresh=refresh,
            record_event=record_event,
            source=source,
            verification_id=verification_id,
            agent=agent,
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
        verification_id: str = "",
        agent: str = "",
    ) -> dict[str, Any]:
        control = load_repo_control(self.root)
        if not control.configured or not control.setup_completed:
            reason = control.disabled_status or "repo not configured"
            return {
                "saved": False,
                "task_id": None,
                "reason": reason,
                "evidence": [],
                "final_status_line": f"Memographix: not saved - {reason}",
            }
        if not control.enabled:
            reason = control.disabled_status or "repo disabled"
            return {
                "saved": False,
                "task_id": None,
                "reason": reason,
                "evidence": [],
                "final_status_line": "Memographix: disabled for this repo",
            }
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
            verification_id=verification_id,
            agent=agent,
        )

    def changed(self) -> list[TaskMemory]:
        return self.engine.changed()

    def stats(self) -> dict[str, int]:
        return self.engine.stats()

    def savings(self, since_days: int = 30) -> dict[str, Any]:
        return self.engine.savings(since_days=since_days)

    def start_agent_verification(self, agent: str = "codex") -> dict[str, Any]:
        status = self.status()
        verification_id = f"mgx-verify-{uuid.uuid4().hex[:12]}"
        prompt = _verification_prompt(self.root, agent, verification_id)
        self.engine.record_agent_verification_start(
            verification_id=verification_id,
            agent=agent,
            prompt=prompt,
        )
        return {
            "root": str(self.root),
            "agent": agent,
            "verification_id": verification_id,
            "prompt": prompt,
            "configured": status["configured"],
            "enabled": status["enabled"],
            "strict_mode": status["strict_mode"],
        }

    def wait_agent_verification(
        self,
        verification_id: str,
        *,
        agent: str = "codex",
        wait_seconds: int = 120,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + max(0, wait_seconds)
        result = self.engine.verification_result(verification_id)
        while wait_seconds > 0 and not result["verified"] and time.monotonic() < deadline:
            time.sleep(1)
            result = self.engine.verification_result(verification_id)
        if result["verified"]:
            self.engine.record_agent_verification_result(
                verification_id=verification_id,
                agent=agent,
                status="verified",
            )
            final_status = "verified"
            reason = ""
        else:
            final_status = "failed"
            reason = "no matching real resolve_task and saved capture_task events were recorded"
            self.engine.record_agent_verification_result(
                verification_id=verification_id,
                agent=agent,
                status=final_status,
                reason=reason,
            )
        verification = self.engine.verification_result(verification_id)
        status = self.status()
        return {
            "root": str(self.root),
            "agent": agent,
            "verification_id": verification_id,
            "wait_seconds": wait_seconds,
            "configured": status["configured"],
            "enabled": status["enabled"],
            "strict_mode": status["strict_mode"],
            "verified": verification["verified"],
            "status": "verified" if verification["verified"] else final_status,
            "reason": "" if verification["verified"] else reason,
            "resolve_events": verification["resolve_events"],
            "capture_events": verification["capture_events"],
            "last_resolve_at": verification["last_resolve_at"],
            "last_capture_at": verification["last_capture_at"],
            "last_capture_status": verification["last_capture_status"],
        }

    def verify_agent(
        self,
        agent: str = "codex",
        wait_seconds: int = 120,
        *,
        repair: bool = False,
    ) -> dict[str, Any]:
        repair_result = self.heal(agents=agent) if repair else None
        started = self.start_agent_verification(agent=agent)
        result = self.wait_agent_verification(
            started["verification_id"],
            agent=agent,
            wait_seconds=wait_seconds,
        )
        result["prompt"] = started["prompt"]
        if repair_result is not None:
            result["repair"] = repair_result
        return result

    def guard(self, since_hours: int = 24) -> dict[str, Any]:
        status = self.status()
        if not status["configured"] or not status["setup_completed"]:
            return {
                "root": str(self.root),
                "status": "not_configured",
                "ok": True,
                "issues": [],
                "reason": status["reason"] or "repo not configured",
                "modified_files": [],
            }
        if not status["enabled"]:
            return {
                "root": str(self.root),
                "status": "disabled",
                "ok": True,
                "issues": [],
                "reason": status["reason"] or "repo disabled",
                "modified_files": [],
            }
        since_days = max(1, (since_hours + 23) // 24)
        savings = self.savings(since_days=since_days)
        modified_files = sorted(self.engine.recent_changed_files())
        issues = []
        if not savings.get("last_mcp_call_at"):
            issues.append("no_mcp_usage")
        if savings.get("last_resolve_at") and (
            not savings.get("last_capture_at")
            or str(savings["last_capture_at"]) < str(savings["last_resolve_at"])
        ):
            issues.append("resolve_without_capture")
        if modified_files and not (
            savings.get("captures_saved", 0) or savings.get("skipped_captures", 0)
        ):
            issues.append("modified_files_without_capture")
        return {
            "root": str(self.root),
            "status": "clean" if not issues else "warning",
            "ok": not issues,
            "issues": issues,
            "since_hours": since_hours,
            "modified_files": modified_files,
            "last_mcp_call_at": savings.get("last_mcp_call_at", ""),
            "last_resolve_at": savings.get("last_resolve_at", ""),
            "last_capture_at": savings.get("last_capture_at", ""),
            "last_capture_status": savings.get("last_capture_status", ""),
        }

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
            "strict_mode": control.strict_agent_memory,
            "strict_agent_memory": control.strict_agent_memory,
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
        verification_id: str = "",
        agent: str = "",
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
            verification_id=verification_id,
            agent=agent,
        )
        data = packet.to_dict()
        data["enabled"] = True
        data["strict_mode"] = status["strict_mode"]
        data["repo_root"] = str(self.root)
        data["event_id"] = event_id
        data["verification_id"] = verification_id
        data["dry_run"] = dry_run
        return data

    def _doctor_snapshot(self, *, live: bool = False) -> dict[str, Any]:
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
        verification_status = self.engine.verification_status() if status["db_exists"] else {}
        result = {
            "root": str(self.root),
            "db_exists": status["db_exists"],
            "config_exists": status["configured"],
            "configured": status["configured"],
            "setup_completed": status["setup_completed"],
            "setup_agents": status["setup_agents"],
            "enabled": status["enabled"],
            "strict_mode": status["strict_mode"],
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
                "last_capture_status": savings.get("last_capture_status", ""),
                "last_mcp_call_at": savings.get("last_mcp_call_at", ""),
                "last_tool_source": savings.get("last_tool_source", ""),
                "has_agent_calls": bool(
                    savings.get("last_resolve_at") or savings.get("last_capture_at")
                ),
                "agent_verified": verification_status.get("agent_verified", False),
                "last_verified_agent_at": verification_status.get("last_verified_agent_at", ""),
                "last_verified_agent": verification_status.get("last_verified_agent", ""),
                "last_unverified_warning": verification_status.get(
                    "last_unverified_warning", ""
                ),
            },
        }
        if live:
            result["live"] = live_activation_check(self.root)
        return result

    def doctor(self, *, live: bool = False, repair: bool = False) -> dict[str, Any]:
        result = self._doctor_snapshot(live=live)
        result["repaired"] = False
        result["repair_actions"] = []
        result["remaining_issues"] = _doctor_remaining_issues(result)
        if not repair:
            return result

        selected = ",".join(result["setup_agents"] or list(SUPPORTED_AGENTS))
        repair_result = self.heal(agents=selected, run_doctor=False)
        result = self._doctor_snapshot(live=live)
        result["repaired"] = bool(
            repair_result.get("setup", {}).get("config_written")
            or repair_result.get("repair", {}).get("removed_entries")
            or repair_result.get("repair", {}).get("refreshed_entries")
        )
        result["repair_actions"] = repair_result["actions"]
        result["repair"] = repair_result
        result["remaining_issues"] = _doctor_remaining_issues(result)
        return result

    def repos(self) -> list[dict[str, Any]]:
        repos = []
        for item in list_registered_repos():
            enriched = dict(item)
            root = Path(str(item.get("root", ""))).resolve()
            db_path = root / ".memographix" / "graph.sqlite"
            if db_path.exists():
                repo_ws = Workspace.open(root)
                stats = repo_ws.stats()
                savings = repo_ws.savings()
                enriched.update(
                    {
                        "files": stats["files"],
                        "tasks": stats["tasks"],
                        "resolve_events": savings.get("resolve_events", 0),
                        "capture_events": savings.get("captures_saved", 0)
                        + savings.get("skipped_captures", 0),
                        "last_resolve_at": savings.get("last_resolve_at", ""),
                        "last_capture_at": savings.get("last_capture_at", ""),
                    }
                )
            else:
                enriched.update(
                    {
                        "files": 0,
                        "tasks": 0,
                        "resolve_events": 0,
                        "capture_events": 0,
                        "last_resolve_at": "",
                        "last_capture_at": "",
                    }
                )
            repos.append(enriched)
        return repos

    def repair_mcp(self, agents: str = "all") -> dict[str, Any]:
        selected_agents = _parse_agents(agents)
        installed = []
        for agent in selected_agents:
            path = install_agent_rules(self.root, agent)
            installed.append({"agent": agent, "path": str(path)})
        mcp_config = self._write_mcp_config()
        registry = register_repo(self.root)
        repair = repair_mcp_configs(self.root, selected_agents)
        repair["agents"] = installed
        repair["mcp_config"] = str(mcp_config)
        repair["registry"] = registry
        return repair

    def heal(self, agents: str = "all", *, run_doctor: bool = True) -> dict[str, Any]:
        selected_agents = _parse_agents(agents)
        actions = [
            "setup",
            "repair_mcp",
            "register_repo",
            "install_agent_rules",
            "rewrite_mcp_configs",
        ]
        setup = self.setup(agents=",".join(selected_agents))
        repair = self.repair_mcp(agents=",".join(selected_agents))
        doctor = self._doctor_snapshot(live=True) if run_doctor else {}
        remaining_issues = _doctor_remaining_issues(doctor) if doctor else []
        return {
            "root": str(self.root),
            "agents": selected_agents,
            "actions": actions,
            "setup": setup,
            "repair": repair,
            "doctor": doctor,
            "remaining_issues": remaining_issues,
            "ok": not remaining_issues,
        }

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
                    "command": current_mgx_command(),
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


def _doctor_remaining_issues(result: dict[str, Any]) -> list[str]:
    if not result:
        return []
    issues: list[str] = []
    if not result.get("configured"):
        issues.append("repo_not_configured")
    if not result.get("setup_completed"):
        issues.append("setup_not_completed")
    if not result.get("registry_registered"):
        issues.append("repo_not_registered")
    if result.get("manual_mcp_config_required"):
        issues.append("mcp_integration_missing_or_stale")
    live = result.get("live")
    if isinstance(live, dict) and not live.get("ok"):
        issues.append("live_mcp_check_failed")
    return issues


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
        "strict_mode": bool(status.get("strict_mode", False)),
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
        "verification_id": "",
        "dry_run": False,
    }


def _verification_prompt(root: Path, agent: str, verification_id: str) -> str:
    return (
        "Memographix verification task.\n"
        f"Repo: {root}\n"
        f"Agent: {agent}\n"
        f"Verification ID: {verification_id}\n\n"
        "Follow these steps exactly:\n"
        "1. Call the Memographix MCP `resolve_task` tool with "
        f"`question=\"Memographix verification {verification_id}: confirm activation\"`, "
        f"`repo=\"{root}\"`, `verification_id=\"{verification_id}\"`, and "
        f"`agent=\"{agent}\"`.\n"
        "2. Call the Memographix MCP `capture_task` tool with the same question, "
        "`answer=\"Memographix verification completed.\"`, the `resolve_event_id` returned "
        "by `resolve_task`, "
        f"`verification_id=\"{verification_id}\"`, `agent=\"{agent}\"`, and "
        "`outcome=\"verified\"`.\n"
        "3. Reply only with the `final_status_line` returned by `capture_task`."
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
