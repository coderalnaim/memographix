from __future__ import annotations

import argparse
import json
import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from .tokens import estimate_tokens
from .workspace import Workspace


def run_benchmark(
    corpus: Path,
    tasks_path: Path,
    output_dir: Path | None = None,
    mutate: bool = True,
) -> dict:
    tasks = _load_tasks(tasks_path)
    started = datetime.now(timezone.utc).isoformat()
    with tempfile.TemporaryDirectory(prefix="memographix-bench-") as tmp:
        sandbox = Path(tmp) / "repo"
        shutil.copytree(
            corpus,
            sandbox,
            ignore=shutil.ignore_patterns(
                ".git",
                ".venv",
                "node_modules",
                ".pytest_cache",
                ".ruff_cache",
                ".mypy_cache",
                "logs",
                ".memographix",
            ),
        )
        ws = Workspace.open(sandbox)
        t0 = time.perf_counter()
        first = ws.index()
        first_index_ms = int((time.perf_counter() - t0) * 1000)

        naive_tokens = _naive_repo_tokens(sandbox)
        first_packets = []
        repeated_packets = []
        seeded_evidence_paths: list[str] = []
        for task in tasks:
            packet = ws.context(task["question"], budget=800)
            first_packets.append(packet.to_dict())
            evidence = [ev.path for ev in packet.evidence[:3]]
            seeded_evidence_paths.extend(path for path in evidence if path not in seeded_evidence_paths)
            ws.capture(
                question=task["question"],
                answer=task["answer"],
                evidence=evidence,
                tests=["benchmark seeded"],
                outcome="seeded repeated-task memory",
            )

        t1 = time.perf_counter()
        for task in tasks:
            repeated_packets.append(ws.context(task["question"], budget=500).to_dict())
        repeated_query_ms = int((time.perf_counter() - t1) * 1000)

        stale_detected = 0
        reindex_ms = None
        if mutate:
            candidate = sandbox / (seeded_evidence_paths[0] if seeded_evidence_paths else "README.md")
            if candidate.exists():
                candidate.write_text(
                    candidate.read_text(encoding="utf-8", errors="ignore")
                    + "\n\n<!-- memographix benchmark mutation -->\n",
                    encoding="utf-8",
                )
            t2 = time.perf_counter()
            second = ws.index()
            reindex_ms = int((time.perf_counter() - t2) * 1000)
            stale_detected = len(ws.changed())
        else:
            second = first

        repeated_avg_tokens = sum(p["estimated_tokens"] for p in repeated_packets) // max(
            1, len(repeated_packets)
        )
        first_avg_tokens = sum(p["estimated_tokens"] for p in first_packets) // max(1, len(first_packets))
        reduction = round(naive_tokens / max(1, repeated_avg_tokens), 2)

        result = {
            "created_at": started,
            "corpus": str(corpus),
            "tasks": str(tasks_path),
            "sandbox_destroyed": True,
            "first_index_ms": first_index_ms,
            "reported_first_index_ms": first.duration_ms,
            "reindex_ms": reindex_ms,
            "files": first.files,
            "symbols": first.symbols,
            "edges": first.edges,
            "second_index": second.to_dict(),
            "naive_repo_tokens": naive_tokens,
            "first_avg_context_tokens": first_avg_tokens,
            "repeated_avg_context_tokens": repeated_avg_tokens,
            "repeated_total_query_ms": repeated_query_ms,
            "repeated_p50_query_ms_estimate": repeated_query_ms // max(1, len(tasks)),
            "repeated_token_reduction_vs_naive": reduction,
            "stale_tasks_detected_after_mutation": stale_detected,
        }
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "memographix-benchmark.json").write_text(
            json.dumps(result, indent=2),
            encoding="utf-8",
        )
    return result


def _load_tasks(path: Path) -> list[dict[str, str]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    tasks = []
    for item in raw:
        question = item.get("question")
        answer = item.get("answer") or item.get("seed_answer")
        if not question or not answer:
            raise ValueError(f"benchmark task must include question and answer/seed_answer: {item}")
        tasks.append({"question": question, "answer": answer})
    if not tasks:
        raise ValueError("benchmark tasks file must contain at least one task")
    return tasks


def _naive_repo_tokens(root: Path) -> int:
    chars = 0
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in {".memographix", ".git", ".venv", "node_modules"} for part in path.parts):
            continue
        try:
            if path.stat().st_size > 1_000_000:
                continue
            chars += len(path.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
    return estimate_tokens("x" * chars)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m memographix.benchmark")
    parser.add_argument("corpus", type=Path)
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path(".mgx-local/benchmark-results"))
    args = parser.parse_args(argv)
    result = run_benchmark(args.corpus.resolve(), args.tasks.resolve(), args.out)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
