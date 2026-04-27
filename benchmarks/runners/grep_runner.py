from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from .common import (
    average_quality,
    base_result,
    copy_sandbox,
    estimate_tokens,
    iter_repo_files,
    p50,
    quality_metrics,
    remove_sandbox,
)


STOPWORDS = {
    "about",
    "after",
    "and",
    "are",
    "does",
    "for",
    "from",
    "how",
    "into",
    "the",
    "this",
    "where",
    "with",
}


def run_grep(
    corpus: Path,
    sandbox_root: Path,
    tasks: list[dict[str, Any]],
    budgets: list[int],
    timeout: int,
    allow_external_installs: bool = False,
) -> dict[str, Any]:
    del timeout, allow_external_installs
    result = base_result("grep")
    result["version"] = "deterministic-path-content-grep"
    result["install_command"] = "none"
    result["capabilities"] = {
        "offline": True,
        "mcp": False,
        "task_memory": False,
        "freshness_check": False,
        "agent_ready_context": False,
    }
    tool_root = sandbox_root / "grep"
    repo = copy_sandbox(corpus, sandbox_root, "grep")
    try:
        t0 = time.perf_counter()
        corpus_index = _load_file_index(repo)
        index_ms = int((time.perf_counter() - t0) * 1000)

        first_tokens: list[int] = []
        repeated_tokens_by_budget: dict[str, list[int]] = {str(b): [] for b in budgets}
        latencies: list[int] = []
        quality: list[dict[str, Any]] = []

        for task in tasks:
            context, paths = _ranked_context(corpus_index, task["question"], budget=800)
            first_tokens.append(estimate_tokens(context))
            quality.append(
                quality_metrics(
                    returned_paths=paths,
                    expected_patterns=task.get("expected_evidence", []),
                    context_text=context,
                    required_concepts=task.get("required_concepts", []),
                    forbidden_hallucinations=task.get("forbidden_hallucinations", []),
                )
            )

        for budget in budgets:
            for _session in (2, 3):
                for task in tasks:
                    t1 = time.perf_counter()
                    context, _paths = _ranked_context(corpus_index, task["question"], budget=budget)
                    latencies.append(int((time.perf_counter() - t1) * 1000))
                    repeated_tokens_by_budget[str(budget)].append(estimate_tokens(context))

        quality_avg = average_quality(quality)
        result["status"] = "ok"
        result["metrics"] = {
            "files_indexed": len(corpus_index),
            "symbols_indexed": None,
            "edges_indexed": None,
            "first_index_ms": index_ms,
            "warm_reindex_ms": index_ms,
            "first_context_avg_tokens": sum(first_tokens) // max(1, len(first_tokens)),
            "repeated_context_avg_tokens_by_budget": {
                budget: sum(values) // max(1, len(values))
                for budget, values in repeated_tokens_by_budget.items()
            },
            "repeated_recall_p50_ms": p50(latencies),
            "stale_evidence_detected": False,
            "stale_evidence_count": 0,
            "evidence_precision_avg": quality_avg["evidence_precision_avg"],
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


def _load_file_index(repo: Path) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    for path in iter_repo_files(repo):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")[:100_000]
        except OSError:
            continue
        rel = path.relative_to(repo).as_posix()
        rows.append((rel, text, text.lower()))
    return rows


def _ranked_context(
    corpus_index: list[tuple[str, str, str]],
    question: str,
    budget: int,
) -> tuple[str, list[str]]:
    terms = _terms(question)
    scored: list[tuple[int, str, str]] = []
    for rel, text, lower in corpus_index:
        rel_lower = rel.lower()
        score = 0
        for term in terms:
            score += 12 * rel_lower.count(term)
            score += min(8, lower.count(term))
        if score:
            scored.append((score, rel, text))
    scored.sort(key=lambda item: (-item[0], item[1]))
    parts: list[str] = []
    paths: list[str] = []
    for _score, rel, text in scored[:12]:
        paths.append(rel)
        snippet = _snippet(text, terms)
        parts.append(f"file: {rel}\n{snippet}")
        if estimate_tokens("\n\n".join(parts)) >= budget:
            break
    context = "\n\n".join(parts)
    max_chars = max(1, budget * 4)
    return context[:max_chars], paths


def _snippet(text: str, terms: list[str]) -> str:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        lower = line.lower()
        if any(term in lower for term in terms):
            start = max(0, index - 3)
            end = min(len(lines), index + 9)
            return "\n".join(lines[start:end])[:2_000]
    return "\n".join(lines[:12])[:2_000]


def _terms(text: str) -> list[str]:
    terms = []
    for term in re.findall(r"[a-z0-9_]+", text.lower()):
        if len(term) > 2 and term not in STOPWORDS and term not in terms:
            terms.append(term)
    return terms
