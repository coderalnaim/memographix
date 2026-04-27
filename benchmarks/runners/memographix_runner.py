from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

from .common import (
    average_quality,
    base_result,
    copy_sandbox,
    dir_size_bytes,
    p50,
    precision,
    quality_metrics,
    remove_sandbox,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
PYTHON_SRC = REPO_ROOT / "python"
try:
    from memographix import __version__
    from memographix.workspace import Workspace
except ImportError:  # pragma: no cover - source checkout fallback
    if str(PYTHON_SRC) not in sys.path:
        sys.path.insert(0, str(PYTHON_SRC))
    from memographix import __version__  # noqa: E402
    from memographix.workspace import Workspace  # noqa: E402


def run_memographix(
    corpus: Path,
    sandbox_root: Path,
    tasks: list[dict[str, Any]],
    budgets: list[int],
    timeout: int,
    allow_external_installs: bool = False,
) -> dict[str, Any]:
    del timeout, allow_external_installs
    result = base_result("memographix")
    result["version"] = __version__
    result["install_command"] = "pip install memographix"
    result["capabilities"] = {
        "offline": True,
        "mcp": True,
        "task_memory": True,
        "freshness_check": True,
        "agent_ready_context": True,
    }
    tool_root = sandbox_root / "memographix"
    repo = copy_sandbox(corpus, sandbox_root, "memographix")
    try:
        ws = Workspace.open(repo)
        ws.enable(reindex=False)
        t0 = time.perf_counter()
        stats = ws.index()
        index_ms = int((time.perf_counter() - t0) * 1000)

        first_tokens: list[int] = []
        repeated_tokens_by_budget: dict[str, list[int]] = {str(b): [] for b in budgets}
        repeated_latency_ms: list[int] = []
        evidence_precision: list[float] = []
        first_quality: list[dict[str, Any]] = []
        repeated_quality: list[dict[str, Any]] = []
        seeded_evidence: list[str] = []

        for task in tasks:
            packet = ws.context(task["question"], budget=800)
            first_tokens.append(packet.estimated_tokens)
            evidence = []
            for ev in packet.evidence:
                if ev.path not in evidence:
                    evidence.append(ev.path)
            seeded_evidence.extend(path for path in evidence if path not in seeded_evidence)
            evidence_precision.append(precision(evidence, task.get("expected_evidence", [])))
            first_quality.append(
                quality_metrics(
                    returned_paths=evidence,
                    expected_patterns=task.get("expected_evidence", []),
                    context_text=_packet_quality_text(packet.to_dict()),
                    required_concepts=task.get("required_concepts", []),
                    forbidden_hallucinations=task.get("forbidden_hallucinations", []),
                )
            )
            ws.capture(
                question=task["question"],
                answer=task["seed_answer"],
                evidence=evidence,
                tests=["benchmark seeded"],
                outcome="seeded repeated-task memory",
            )

        for budget in budgets:
            for _session in (2, 3):
                for task in tasks:
                    t1 = time.perf_counter()
                    packet = ws.context(task["question"], budget=budget)
                    repeated_latency_ms.append(int((time.perf_counter() - t1) * 1000))
                    repeated_tokens_by_budget[str(budget)].append(packet.estimated_tokens)
                    if budget == 800 and _session == 2:
                        repeated_quality.append(
                            quality_metrics(
                                returned_paths=[ev.path for ev in packet.evidence],
                                expected_patterns=task.get("expected_evidence", []),
                                context_text=_packet_quality_text(packet.to_dict()),
                                required_concepts=task.get("required_concepts", []),
                                forbidden_hallucinations=task.get("forbidden_hallucinations", []),
                            )
                        )

        mutation_target = seeded_evidence[0] if seeded_evidence else "README.md"
        target = repo / mutation_target
        if target.exists():
            target.write_text(
                target.read_text(encoding="utf-8", errors="ignore")
                + "\n\n<!-- memographix benchmark mutation -->\n",
                encoding="utf-8",
            )
        t2 = time.perf_counter()
        warm_stats = ws.index()
        reindex_ms = int((time.perf_counter() - t2) * 1000)
        stale_count = len(ws.changed())

        repeated_avg_by_budget = {
            budget: sum(values) // max(1, len(values))
            for budget, values in repeated_tokens_by_budget.items()
        }
        first_quality_avg = average_quality(first_quality)
        repeated_quality_avg = average_quality(repeated_quality)
        result["status"] = "ok"
        result["metrics"] = {
            "files_indexed": stats.files,
            "symbols_indexed": stats.symbols,
            "edges_indexed": stats.edges,
            "first_index_ms": index_ms,
            "warm_reindex_ms": reindex_ms,
            "first_context_avg_tokens": sum(first_tokens) // max(1, len(first_tokens)),
            "repeated_context_avg_tokens_by_budget": repeated_avg_by_budget,
            "repeated_recall_p50_ms": p50(repeated_latency_ms),
            "stale_evidence_detected": stale_count > 0,
            "stale_evidence_count": stale_count,
            "evidence_precision_avg": round(
                sum(evidence_precision) / max(1, len(evidence_precision)),
                4,
            ),
            "quality_score_avg": repeated_quality_avg["quality_score_avg"],
            "evidence_recall_avg": repeated_quality_avg["evidence_recall_avg"],
            "required_concept_coverage_avg": repeated_quality_avg["required_concept_coverage_avg"],
            "hallucination_risk_flags": repeated_quality_avg["hallucination_risk_flags"],
            "first_quality_score_avg": first_quality_avg["quality_score_avg"],
            "first_evidence_recall_avg": first_quality_avg["evidence_recall_avg"],
            "disk_footprint_bytes": dir_size_bytes(repo / ".memographix"),
            "warm_files_indexed": warm_stats.files,
        }
    except Exception as exc:  # pragma: no cover - exercised by docker failures
        result["status"] = "error"
        result["errors"].append(str(exc))
    finally:
        result["artifacts"]["sandbox_destroyed"] = remove_sandbox(tool_root)
    return result


def _packet_quality_text(packet: dict[str, Any]) -> str:
    evidence = "\n".join(item.get("path", "") for item in packet.get("evidence", []))
    return "\n".join(
        [
            packet.get("summary", ""),
            packet.get("context", ""),
            evidence,
        ]
    )
