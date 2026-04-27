from __future__ import annotations

import argparse
import json
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .runners import RUNNERS
    from .runners.common import validate_result, write_json
except ImportError:  # pragma: no cover - used when executed as `python benchmarks/run.py`
    from runners import RUNNERS
    from runners.common import validate_result, write_json


DEFAULT_TOOLS = ["memographix", "naive", "grep"]
DEFAULT_BUDGETS = [200, 500, 800, 1500]


def load_tasks(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def run_suite(
    corpus: Path,
    out: Path,
    tools: list[str],
    tasks_path: Path,
    budgets: list[int],
    timeout: int,
    allow_external_installs: bool,
) -> dict[str, Any]:
    tasks = load_tasks(tasks_path)
    started = datetime.now(timezone.utc).isoformat()
    results = []
    with tempfile.TemporaryDirectory(prefix="mgx-docker-bench-") as tmp:
        sandbox_root = Path(tmp)
        for tool in tools:
            runner = RUNNERS.get(tool)
            if runner is None:
                results.append(
                    {
                        "tool": tool,
                        "status": "unknown_tool",
                        "version": "unknown",
                        "install_command": None,
                        "metrics": {},
                        "capabilities": {},
                        "errors": [f"unknown benchmark tool: {tool}"],
                        "artifacts": {"sandbox_destroyed": True},
                    }
                )
                continue
            t0 = time.perf_counter()
            result = runner(
                corpus=corpus,
                sandbox_root=sandbox_root,
                tasks=tasks,
                budgets=budgets,
                timeout=timeout,
                allow_external_installs=allow_external_installs,
            )
            result["duration_ms"] = int((time.perf_counter() - t0) * 1000)
            validate_result(result)
            results.append(result)
    sandbox_destroyed = not sandbox_root.exists()

    comparison = build_comparison(
        corpus=corpus,
        tasks_path=tasks_path,
        budgets=budgets,
        started=started,
        results=results,
        sandbox_destroyed=sandbox_destroyed,
    )
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / "comparison.json", comparison)
    (out / "comparison.md").write_text(render_markdown(comparison), encoding="utf-8")
    for result in results:
        write_json(out / f"{result['tool']}.json", result)
    return comparison


def build_comparison(
    corpus: Path,
    tasks_path: Path,
    budgets: list[int],
    started: str,
    results: list[dict[str, Any]],
    sandbox_destroyed: bool,
) -> dict[str, Any]:
    winners: dict[str, str | None] = {}
    ok_results = [r for r in results if r["status"] == "ok"]

    def min_metric(path: list[str], prefer_true: bool = False):
        candidates = []
        for result in ok_results:
            value: Any = result
            for part in path:
                value = value.get(part) if isinstance(value, dict) else None
                if value is None:
                    break
            if value is None:
                continue
            if isinstance(value, dict):
                continue
            if isinstance(value, bool):
                score = 0 if value is prefer_true else 1
            else:
                score = value
            candidates.append((score, result["tool"]))
        return sorted(candidates)[0][1] if candidates else None

    def max_metric(path: list[str]):
        candidates = []
        for result in ok_results:
            value: Any = result
            for part in path:
                value = value.get(part) if isinstance(value, dict) else None
                if value is None:
                    break
            if value is None or isinstance(value, dict) or isinstance(value, bool):
                continue
            candidates.append((value, result["tool"]))
        return sorted(candidates, reverse=True)[0][1] if candidates else None

    winners["first_index_ms"] = min_metric(["metrics", "first_index_ms"])
    winners["warm_reindex_ms"] = min_metric(["metrics", "warm_reindex_ms"])
    winners["repeated_recall_p50_ms"] = min_metric(["metrics", "repeated_recall_p50_ms"])
    winners["quality_score_avg"] = max_metric(["metrics", "quality_score_avg"])
    winners["evidence_recall_avg"] = max_metric(["metrics", "evidence_recall_avg"])
    winners["required_concept_coverage_avg"] = max_metric(["metrics", "required_concept_coverage_avg"])
    winners["stale_evidence_detected"] = min_metric(
        ["metrics", "stale_evidence_detected"],
        prefer_true=True,
    )
    for budget in budgets:
        winners[f"repeated_tokens_{budget}"] = min_metric(
            ["metrics", "repeated_context_avg_tokens_by_budget", str(budget)]
        )

    return {
        "created_at": started,
        "corpus": str(corpus),
        "tasks": str(tasks_path),
        "budgets": budgets,
        "sandbox_destroyed": sandbox_destroyed,
        "results": results,
        "winners": winners,
        "claim_policy": (
            "Only claim wins for metrics where Memographix is the winner in this file. "
            "Unavailable competitors must be disclosed."
        ),
    }


def render_markdown(comparison: dict[str, Any]) -> str:
    lines = [
        "# Benchmark Comparison",
        "",
        f"Corpus: `{comparison['corpus']}`",
        f"Created: `{comparison['created_at']}`",
        f"Sandbox destroyed: `{comparison['sandbox_destroyed']}`",
        "",
        "## Results",
        "",
        "| Tool | Status | Index ms | Re-index ms | Recall p50 ms | Stale detected | Tokens @500 | Tokens @800 | Quality | Evidence recall | Concept coverage |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for result in comparison["results"]:
        metrics = result.get("metrics", {})
        repeated = metrics.get("repeated_context_avg_tokens_by_budget", {})
        lines.append(
            "| {tool} | {status} | {index} | {reindex} | {recall} | {stale} | {t500} | {t800} | {quality} | {evidence_recall} | {concepts} |".format(
                tool=result["tool"],
                status=result["status"],
                index=_fmt(metrics.get("first_index_ms")),
                reindex=_fmt(metrics.get("warm_reindex_ms")),
                recall=_fmt(metrics.get("repeated_recall_p50_ms")),
                stale=_fmt(metrics.get("stale_evidence_detected")),
                t500=_fmt(repeated.get("500")),
                t800=_fmt(repeated.get("800")),
                quality=_fmt(metrics.get("quality_score_avg")),
                evidence_recall=_fmt(metrics.get("evidence_recall_avg")),
                concepts=_fmt(metrics.get("required_concept_coverage_avg")),
            )
        )
    lines.extend(["", "## Winners", ""])
    for metric, winner in comparison["winners"].items():
        lines.append(f"- `{metric}`: `{winner or 'n/a'}`")
    lines.extend(["", comparison["claim_policy"], ""])
    return "\n".join(lines)


def _fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def parse_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def parse_budgets(value: str) -> list[int]:
    return [int(item) for item in parse_csv(value)]


def main() -> None:
    root = Path(__file__).resolve().parent
    repo_root = root.parent
    parser = argparse.ArgumentParser(prog="benchmarks/run.py")
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=repo_root / ".mgx-local" / "benchmark-results")
    parser.add_argument("--tasks", type=Path, default=root / "tasks" / "golden_tasks.json")
    parser.add_argument("--tools", default=",".join(DEFAULT_TOOLS))
    parser.add_argument("--budgets", default=",".join(str(b) for b in DEFAULT_BUDGETS))
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--allow-external-installs", action="store_true")
    args = parser.parse_args()
    comparison = run_suite(
        corpus=args.corpus.resolve(),
        out=args.out.resolve(),
        tools=parse_csv(args.tools),
        tasks_path=args.tasks.resolve(),
        budgets=parse_budgets(args.budgets),
        timeout=args.timeout,
        allow_external_installs=args.allow_external_installs,
    )
    print(json.dumps(comparison, indent=2))


if __name__ == "__main__":
    main()
