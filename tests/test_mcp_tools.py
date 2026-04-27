from __future__ import annotations

from pathlib import Path

from memographix.mcp import (
    tool_activation_status,
    tool_capture_task,
    tool_freshness_check,
    tool_graph_stats,
    tool_list_repos,
    tool_remember_task,
    tool_resolve_task,
)
from memographix.workspace import Workspace


def test_mcp_tool_functions(tmp_path: Path) -> None:
    (tmp_path / "memory.py").write_text("def save_memory():\n    return True\n", encoding="utf-8")
    Workspace.open(tmp_path).enable(reindex=True)
    stats = tool_graph_stats(str(tmp_path))
    assert stats["files"] == 1
    remembered = tool_remember_task(
        str(tmp_path),
        "How is memory saved?",
        "save_memory stores the memory.",
        ["memory.py"],
    )
    assert remembered["task_id"] == 1
    packet = tool_resolve_task(str(tmp_path), "How is memory saved?", 300)
    assert packet["status"] == "fresh"
    assert packet["repo_root"] == str(tmp_path)
    assert packet["strict_mode"] is True
    assert packet["event_id"] is not None
    assert tool_freshness_check(str(tmp_path))["stale_tasks"] == []


def test_capture_task_evidence_gate(tmp_path: Path) -> None:
    (tmp_path / "worker.py").write_text("def run_worker():\n    return True\n", encoding="utf-8")
    Workspace.open(tmp_path).enable(reindex=True)

    skipped = tool_capture_task(
        str(tmp_path),
        "What runs the worker?",
        "run_worker runs it.",
    )
    assert skipped["saved"] is False
    assert "evidence" in skipped["reason"]
    assert skipped["final_status_line"].startswith("Memographix: not saved - ")

    saved = tool_capture_task(
        str(tmp_path),
        "What runs the worker?",
        "run_worker runs it.",
        changed_files=["worker.py"],
        commands=["pytest -q"],
        outcome="passed",
    )
    assert saved["saved"] is True
    assert saved["evidence"] == ["worker.py"]
    assert saved["final_status_line"] == "Memographix: saved task memory"


def test_capture_can_reuse_resolve_event_evidence(tmp_path: Path) -> None:
    (tmp_path / "worker.py").write_text("def run_worker():\n    return True\n", encoding="utf-8")
    Workspace.open(tmp_path).setup(agents="codex")

    packet = tool_resolve_task(str(tmp_path), "What runs the worker?", 300)
    assert packet["event_id"] is not None
    saved = tool_capture_task(
        str(tmp_path),
        "What runs the worker?",
        "run_worker runs it.",
        commands=["pytest -q"],
        outcome="passed",
        resolve_event_id=packet["event_id"],
    )
    assert saved["saved"] is True
    assert saved["evidence"]


def test_capture_routes_by_resolve_event_when_repo_context_is_wrong(tmp_path: Path) -> None:
    repo = tmp_path / "support-frontend"
    repo.mkdir()
    (repo / "worker.py").write_text("def run_worker():\n    return True\n", encoding="utf-8")
    Workspace.open(repo).setup(agents="codex")

    packet = tool_resolve_task(
        str(tmp_path),
        "What runs the worker?",
        300,
        repo=str(repo),
    )
    assert packet["event_id"] is not None

    saved = tool_capture_task(
        str(tmp_path),
        "What runs the worker?",
        "run_worker runs it.",
        repo=str(tmp_path),
        resolve_event_id=packet["event_id"],
        commands=["pytest -q"],
        outcome="passed",
    )

    assert saved["saved"] is True
    assert saved["final_status_line"] == "Memographix: saved task memory"
    assert Workspace.open(repo).stats()["tasks"] == 1


def test_global_router_resolves_registered_repo_by_alias(tmp_path: Path) -> None:
    repo = tmp_path / "nocfo-support-agent-frontend"
    repo.mkdir()
    (repo / "main.tsx").write_text("export const main = true;\n", encoding="utf-8")
    Workspace.open(repo).setup(agents="codex")

    listed = tool_list_repos()
    assert listed["repos"][0]["root"] == str(repo)
    status = tool_activation_status(str(tmp_path), repo="support agent frontend")
    assert status["resolved"] is True
    assert status["repo_root"] == str(repo)
    assert status["strict_mode"] is True
    assert status["agent_verified"] is False
    assert status["repair_command"] == f'mgx --root "{repo}" doctor --live --repair'
    assert status["cli_fallback_ask"] == f'mgx --root "{repo}" ask "<task>" --budget 800'
    assert status["installed_version"]
    packet = tool_resolve_task(
        str(tmp_path),
        "Explain the support agent frontend main entrypoint.",
        300,
    )
    assert packet["repo_root"] == str(repo)


def test_explicit_unconfigured_repo_path_does_not_fall_back_to_registered_repo(
    tmp_path: Path,
) -> None:
    registered = tmp_path / "registered-repo"
    registered.mkdir()
    (registered / "main.py").write_text("def main():\n    return True\n", encoding="utf-8")
    Workspace.open(registered).setup(agents="codex")

    unconfigured = tmp_path / "unconfigured-repo"
    unconfigured.mkdir()
    (unconfigured / "README.md").write_text("# Demo\n", encoding="utf-8")

    packet = tool_resolve_task(
        str(tmp_path),
        "Explain this repo.",
        300,
        repo=str(unconfigured),
    )

    assert packet["status"] == "disabled"
    assert packet["reason"] == "repo not configured"
    assert packet["repo_root"] == ""


def test_mcp_automatic_resolve_requires_setup(tmp_path: Path) -> None:
    (tmp_path / "module.py").write_text("def run():\n    return True\n", encoding="utf-8")
    packet = tool_resolve_task(str(tmp_path), "How does run work?", 300)
    assert packet["status"] == "disabled"
    assert packet["enabled"] is False
    assert packet["reason"] == "repo not configured"
    assert packet["context"] == ""
    assert not (tmp_path / ".memographix").exists()


def test_mcp_disabled_repo_skips_resolve_and_capture(tmp_path: Path) -> None:
    (tmp_path / "module.py").write_text("def run():\n    return True\n", encoding="utf-8")
    ws = Workspace.open(tmp_path)
    ws.enable(reindex=True)
    ws.disable("disabled during benchmark")

    packet = tool_resolve_task(str(tmp_path), "How does run work?", 300)
    assert packet["status"] == "disabled"
    assert packet["enabled"] is False
    assert packet["reason"] == "disabled during benchmark"

    captured = tool_capture_task(
        str(tmp_path),
        "How does run work?",
        "run returns True.",
        evidence=["module.py"],
    )
    assert captured["saved"] is False
    assert captured["reason"] == "disabled during benchmark"
    assert captured["evidence"] == []
    assert captured["final_status_line"] == "Memographix: disabled for this repo"
    assert ws.stats()["tasks"] == 0


def test_dry_run_resolve_does_not_record_event(tmp_path: Path) -> None:
    (tmp_path / "module.py").write_text("def run():\n    return True\n", encoding="utf-8")
    ws = Workspace.open(tmp_path)
    ws.setup(agents="codex")

    packet = tool_resolve_task(str(tmp_path), "How does run work?", 300, dry_run=True)
    assert packet["dry_run"] is True
    assert packet["event_id"] is None
    assert ws.savings()["resolve_events"] == 0


def test_agent_verification_requires_real_resolve_and_capture(tmp_path: Path) -> None:
    (tmp_path / "module.py").write_text("def run():\n    return True\n", encoding="utf-8")
    ws = Workspace.open(tmp_path)
    ws.setup(agents="codex")

    verification = ws.verify_agent(agent="codex", wait_seconds=0)
    assert verification["verified"] is False
    verification_id = verification["verification_id"]

    packet = tool_resolve_task(
        str(tmp_path),
        f"Memographix verification {verification_id}: confirm activation",
        300,
        verification_id=verification_id,
        agent="codex",
    )
    saved = tool_capture_task(
        str(tmp_path),
        f"Memographix verification {verification_id}: confirm activation",
        "Memographix verification completed.",
        resolve_event_id=packet["event_id"],
        outcome="verified",
        verification_id=verification_id,
        agent="codex",
    )

    assert saved["saved"] is True
    result = ws.engine.verification_result(verification_id)
    assert result["verified"] is True
    status = tool_activation_status(str(tmp_path))
    assert status["agent_verified"] is True
    assert status["last_verified_agent"] == "codex"
    assert ws.savings()["resolve_events"] == 1
