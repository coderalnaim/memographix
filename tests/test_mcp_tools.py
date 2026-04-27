from __future__ import annotations

from pathlib import Path

from memographix.mcp import (
    tool_capture_task,
    tool_freshness_check,
    tool_graph_stats,
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
    assert captured == {
        "saved": False,
        "task_id": None,
        "reason": "disabled during benchmark",
        "evidence": [],
    }
    assert ws.stats()["tasks"] == 0
