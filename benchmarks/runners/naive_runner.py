from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .common import average_quality, base_result, copy_sandbox, p50, repo_quality_metrics, repo_token_estimate, remove_sandbox


def run_naive(
    corpus: Path,
    sandbox_root: Path,
    tasks: list[dict[str, Any]],
    budgets: list[int],
    timeout: int,
    allow_external_installs: bool = False,
) -> dict[str, Any]:
    del budgets, timeout, allow_external_installs
    result = base_result("naive")
    result["version"] = "raw-files"
    result["install_command"] = "none"
    result["capabilities"] = {
        "offline": True,
        "mcp": False,
        "task_memory": False,
        "freshness_check": False,
        "agent_ready_context": False,
    }
    tool_root = sandbox_root / "naive"
    repo = copy_sandbox(corpus, sandbox_root, "naive")
    try:
        t0 = time.perf_counter()
        tokens = repo_token_estimate(repo)
        scan_ms = int((time.perf_counter() - t0) * 1000)
        query_latencies = []
        quality = [repo_quality_metrics(repo, task) for task in tasks]
        for _session in (1, 2, 3):
            for _task in tasks:
                t1 = time.perf_counter()
                _ = repo_token_estimate(repo)
                query_latencies.append(int((time.perf_counter() - t1) * 1000))
        quality_avg = average_quality(quality)
        result["status"] = "ok"
        result["metrics"] = {
            "files_indexed": None,
            "symbols_indexed": None,
            "edges_indexed": None,
            "first_index_ms": scan_ms,
            "warm_reindex_ms": scan_ms,
            "first_context_avg_tokens": tokens,
            "repeated_context_avg_tokens_by_budget": {
                "200": tokens,
                "500": tokens,
                "800": tokens,
                "1500": tokens,
            },
            "repeated_recall_p50_ms": p50(query_latencies),
            "stale_evidence_detected": False,
            "stale_evidence_count": 0,
            "evidence_precision_avg": 0.0,
            "quality_score_avg": quality_avg["quality_score_avg"],
            "evidence_recall_avg": quality_avg["evidence_recall_avg"],
            "required_concept_coverage_avg": quality_avg["required_concept_coverage_avg"],
            "hallucination_risk_flags": quality_avg["hallucination_risk_flags"],
            "disk_footprint_bytes": 0,
        }
    except Exception as exc:  # pragma: no cover
        result["status"] = "error"
        result["errors"].append(str(exc))
    finally:
        result["artifacts"]["sandbox_destroyed"] = remove_sandbox(tool_root)
    return result
