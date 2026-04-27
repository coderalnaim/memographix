from __future__ import annotations

import json
import shutil
import tempfile
import time
import os
import sys
from pathlib import Path
from typing import Any

from .common import (
    average_quality,
    base_result,
    copy_sandbox,
    dir_size_bytes,
    extract_path_like_strings,
    quality_metrics,
    remove_sandbox,
    run_cmd,
)


EXTERNAL_DEFINITIONS: dict[str, dict[str, Any]] = {
    "graphify": {
        "install": ["python", "-m", "pip", "install", "graphifyy"],
        "version": ["python", "-m", "pip", "show", "graphifyy"],
        "command": ["python", "-m", "graphify", "benchmark"],
        "capabilities": {"offline": False, "mcp": True, "task_memory": False, "freshness_check": False, "agent_ready_context": True},
    },
    "aider-repomap": {
        "install": ["python", "-m", "pip", "install", "aider-chat"],
        "version": ["aider", "--version"],
        "command": ["aider", "--help"],
        "capabilities": {"offline": False, "mcp": False, "task_memory": False, "freshness_check": False, "agent_ready_context": True},
    },
    "graphrag": {
        "install": ["python", "-m", "pip", "install", "graphrag"],
        "version": ["python", "-m", "pip", "show", "graphrag"],
        "command": ["graphrag", "--help"],
        "capabilities": {"offline": False, "mcp": False, "task_memory": False, "freshness_check": False, "agent_ready_context": False},
    },
    "codegraphcontext": {
        "install": ["python", "-m", "pip", "install", "codegraphcontext"],
        "version": ["python", "-m", "pip", "show", "codegraphcontext"],
        "command": ["python", "-m", "pip", "show", "codegraphcontext"],
        "capabilities": {"offline": True, "mcp": True, "task_memory": False, "freshness_check": False, "agent_ready_context": True},
    },
    "codegraph-cli": {
        "install": ["python", "-m", "pip", "install", "codegraph-cli"],
        "version": ["cg", "--version"],
        "command": ["cg", "--help"],
        "capabilities": {"offline": False, "mcp": False, "task_memory": False, "freshness_check": False, "agent_ready_context": True},
    },
    "gitnexus": {
        "install": ["npm", "install", "-g", "gitnexus"],
        "version": ["gitnexus", "--version"],
        "command": ["gitnexus", "--help"],
        "capabilities": {"offline": True, "mcp": True, "task_memory": False, "freshness_check": False, "agent_ready_context": True},
    },
    "narsil": {
        "install": ["cargo", "install", "narsil-mcp", "--locked"],
        "version": ["narsil-mcp", "--version"],
        "command": ["narsil-mcp", "--help"],
        "capabilities": {"offline": True, "mcp": True, "task_memory": False, "freshness_check": False, "agent_ready_context": True},
    },
}


def make_external_runner(tool: str):
    definition = EXTERNAL_DEFINITIONS[tool]

    def run_external(
        corpus: Path,
        sandbox_root: Path,
        tasks: list[dict[str, Any]],
        budgets: list[int],
        timeout: int,
        allow_external_installs: bool = False,
    ) -> dict[str, Any]:
        result = base_result(tool)
        result["install_command"] = " ".join(definition["install"])
        result["capabilities"].update(definition["capabilities"])
        tool_root = sandbox_root / tool
        repo = copy_sandbox(corpus, sandbox_root, tool)
        try:
            if not allow_external_installs:
                result["status"] = "skipped"
                result["errors"].append("external installs disabled; rerun with --allow-external-installs")
                return result
            with tempfile.TemporaryDirectory(prefix=f"{tool}-venv-") as tmp:
                venv = Path(tmp)
                venv_create = run_cmd([sys.executable, "-m", "venv", str(venv)], cwd=repo, timeout=120)
                result["artifacts"]["venv"] = venv_create
                if venv_create["returncode"] != 0:
                    result["status"] = "unavailable"
                    result["errors"].append("venv creation failed")
                    return result

                bin_dir = venv / ("Scripts" if os.name == "nt" else "bin")
                python = bin_dir / ("python.exe" if os.name == "nt" else "python")
                path_parts = [str(bin_dir), os.environ.get("PATH", "")]
                for binary in ("npm", "cargo"):
                    found = shutil.which(binary)
                    if found:
                        path_parts.append(str(Path(found).parent))
                env = {"PATH": os.pathsep.join(path_parts)}
                install = run_cmd(
                    _using_venv_python(definition["install"], python),
                    cwd=repo,
                    env=env,
                    timeout=timeout,
                )
                result["artifacts"]["install"] = install
                if install["returncode"] != 0:
                    result["status"] = "unavailable"
                    result["errors"].append("install failed")
                    return result
                version = run_cmd(
                    _using_venv_python(definition["version"], python),
                    cwd=repo,
                    env=env,
                    timeout=60,
                )
                result["version"] = (version["stdout"] or version["stderr"] or "unknown").strip()[:500]
                if tool == "graphify":
                    return _run_graphify(
                        result=result,
                        repo=repo,
                        tool_root=tool_root,
                        python=python,
                        env=env,
                        budgets=budgets,
                        tasks=tasks,
                        timeout=timeout,
                    )
                t0 = time.perf_counter()
                command = run_cmd(
                    _using_venv_python(definition["command"], python),
                    cwd=repo,
                    env=env,
                    timeout=timeout,
                )
                result["artifacts"]["command"] = command
                elapsed = int((time.perf_counter() - t0) * 1000)
                result["status"] = "ok" if command["returncode"] == 0 else "error"
                if command["returncode"] != 0:
                    result["errors"].append("command failed")
                result["metrics"] = {
                    "first_index_ms": elapsed,
                    "warm_reindex_ms": None,
                    "first_context_avg_tokens": None,
                    "repeated_context_avg_tokens_by_budget": {},
                    "repeated_recall_p50_ms": None,
                    "stale_evidence_detected": False,
                    "stale_evidence_count": 0,
                    "evidence_precision_avg": None,
                    "quality_score_avg": None,
                    "evidence_recall_avg": None,
                    "required_concept_coverage_avg": None,
                    "hallucination_risk_flags": [],
                    "disk_footprint_bytes": dir_size_bytes(repo),
                }
        except Exception as exc:  # pragma: no cover
            result["status"] = "error"
            result["errors"].append(str(exc))
        finally:
            result["artifacts"]["sandbox_destroyed"] = remove_sandbox(tool_root)
        return result

    return run_external


def _run_graphify(
    result: dict[str, Any],
    repo: Path,
    tool_root: Path,
    python: Path,
    env: dict[str, str],
    budgets: list[int],
    tasks: list[dict[str, Any]],
    timeout: int,
) -> dict[str, Any]:
    try:
        update = run_cmd([str(python), "-m", "graphify", "update", "."], cwd=repo, env=env, timeout=timeout)
        result["artifacts"]["update"] = update
        if update["returncode"] != 0:
            result["status"] = "error"
            result["errors"].append("graphify update failed")
            return result

        graph_path = repo / "graphify-out" / "graph.json"
        bench_script = (
            "import json; "
            "from graphify.benchmark import run_benchmark; "
            "print(json.dumps(run_benchmark('graphify-out/graph.json')))"
        )
        benchmark = run_cmd([str(python), "-c", bench_script], cwd=repo, env=env, timeout=timeout)
        result["artifacts"]["benchmark"] = benchmark
        if benchmark["returncode"] != 0:
            result["status"] = "error"
            result["errors"].append("graphify benchmark failed")
            return result

        benchmark_data = json.loads(benchmark["stdout"])
        graph_data = json.loads(graph_path.read_text(encoding="utf-8"))
        token_cost = benchmark_data.get("avg_query_tokens")
        repeated = {str(budget): token_cost for budget in budgets if token_cost is not None}
        graph_paths = extract_path_like_strings(graph_data)
        graph_text = json.dumps(graph_data)[:2_000_000]
        quality = [
            quality_metrics(
                returned_paths=graph_paths,
                expected_patterns=task.get("expected_evidence", []),
                context_text=graph_text,
                required_concepts=task.get("required_concepts", []),
                forbidden_hallucinations=task.get("forbidden_hallucinations", []),
            )
            for task in tasks
        ]
        quality_avg = average_quality(quality)

        warm = _touch_first_code_file(repo)
        warm_update = run_cmd([str(python), "-m", "graphify", "update", "."], cwd=repo, env=env, timeout=timeout)
        result["artifacts"]["warm_update"] = warm_update
        result["artifacts"]["warm_mutation"] = warm

        result["status"] = "ok"
        result["metrics"] = {
            "files_indexed": None,
            "symbols_indexed": benchmark_data.get("nodes"),
            "edges_indexed": len(graph_data.get("links") or graph_data.get("edges") or []),
            "first_index_ms": update["duration_ms"],
            "warm_reindex_ms": warm_update["duration_ms"] if warm_update["returncode"] == 0 else None,
            "first_context_avg_tokens": benchmark_data.get("corpus_tokens"),
            "repeated_context_avg_tokens_by_budget": repeated,
            "repeated_recall_p50_ms": None,
            "stale_evidence_detected": False,
            "stale_evidence_count": 0,
            "evidence_precision_avg": quality_avg["evidence_precision_avg"],
            "quality_score_avg": quality_avg["quality_score_avg"],
            "evidence_recall_avg": quality_avg["evidence_recall_avg"],
            "required_concept_coverage_avg": quality_avg["required_concept_coverage_avg"],
            "hallucination_risk_flags": quality_avg["hallucination_risk_flags"],
            "disk_footprint_bytes": dir_size_bytes(repo),
            "graph_nodes": benchmark_data.get("nodes"),
            "graph_edges": benchmark_data.get("edges"),
        }
        return result
    except Exception as exc:
        result["status"] = "error"
        result["errors"].append(str(exc))
        return result
    finally:
        result["artifacts"]["sandbox_destroyed"] = remove_sandbox(tool_root)


def _touch_first_code_file(repo: Path) -> str | None:
    for path in repo.rglob("*"):
        if path.suffix.lower() in {".py", ".js", ".ts", ".tsx", ".go", ".rs", ".java"} and path.is_file():
            marker = "#" if path.suffix.lower() == ".py" else "//"
            text = path.read_text(encoding="utf-8", errors="ignore")
            path.write_text(f"{text}\n{marker} memographix benchmark mutation\n", encoding="utf-8")
            return path.relative_to(repo).as_posix()
    return None


def _using_venv_python(command: list[str], python: Path) -> list[str]:
    if command and command[0] == "python":
        return [str(python), *command[1:]]
    return command


EXTERNAL_RUNNERS = {tool: make_external_runner(tool) for tool in EXTERNAL_DEFINITIONS}
