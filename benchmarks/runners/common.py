from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from statistics import median
from typing import Any, Iterable


REQUIRED_FIELDS = {
    "tool",
    "status",
    "version",
    "install_command",
    "metrics",
    "capabilities",
    "errors",
    "artifacts",
}


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def repo_token_estimate(repo: Path) -> int:
    chars = 0
    for path in repo.rglob("*"):
        if not path.is_file():
            continue
        if any(part in {".git", ".venv", "node_modules", ".memographix"} for part in path.parts):
            continue
        try:
            if path.stat().st_size > 1_000_000:
                continue
            chars += len(path.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
    return estimate_tokens("x" * chars)


def iter_repo_files(repo: Path) -> list[Path]:
    paths: list[Path] = []
    for path in repo.rglob("*"):
        if not path.is_file():
            continue
        if any(part in {".git", ".venv", "node_modules", ".memographix"} for part in path.parts):
            continue
        try:
            if path.stat().st_size > 1_000_000:
                continue
        except OSError:
            continue
        paths.append(path)
    return paths


def copy_sandbox(corpus: Path, sandbox_root: Path, tool: str) -> Path:
    repo = sandbox_root / tool / "repo"
    if repo.exists():
        shutil.rmtree(repo)
    repo.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        corpus,
        repo,
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
    return repo


def remove_sandbox(path: Path) -> bool:
    if path.exists():
        shutil.rmtree(path)
    return not path.exists()


def dir_size_bytes(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for item in path.rglob("*"):
        if item.is_file():
            try:
                total += item.stat().st_size
            except OSError:
                pass
    return total


def run_cmd(
    cmd: list[str],
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: int = 300,
) -> dict[str, Any]:
    started = time.perf_counter()
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            env=merged_env,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        return {
            "cmd": cmd,
            "returncode": proc.returncode,
            "stdout": proc.stdout[-8000:],
            "stderr": proc.stderr[-8000:],
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "cmd": cmd,
            "returncode": 124,
            "stdout": (exc.stdout or "")[-8000:] if isinstance(exc.stdout, str) else "",
            "stderr": (exc.stderr or "")[-8000:] if isinstance(exc.stderr, str) else "timeout",
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }


def precision(returned_paths: list[str], expected_patterns: list[str]) -> float:
    if not returned_paths:
        return 0.0
    hits = 0
    for path in returned_paths:
        if any(path == pattern or path.startswith(pattern.rstrip("/") + "/") for pattern in expected_patterns):
            hits += 1
    return round(hits / len(returned_paths), 4)


def recall(returned_paths: list[str], expected_patterns: list[str]) -> float:
    if not expected_patterns:
        return 0.0
    hits = 0
    for pattern in expected_patterns:
        if any(_path_matches(path, pattern) for path in returned_paths):
            hits += 1
    return round(hits / len(expected_patterns), 4)


def quality_metrics(
    *,
    returned_paths: list[str],
    expected_patterns: list[str],
    context_text: str,
    required_concepts: list[str] | None = None,
    forbidden_hallucinations: list[str] | None = None,
) -> dict[str, Any]:
    required = required_concepts or []
    forbidden = forbidden_hallucinations or []
    covered = [concept for concept in required if _concept_present(context_text, concept)]
    flags = [item for item in forbidden if _phrase_present(context_text, item)]
    evidence_recall = recall(returned_paths, expected_patterns)
    evidence_precision = precision(returned_paths, expected_patterns)
    concept_coverage = round(len(covered) / len(required), 4) if required else 0.0
    penalty = 0.2 if flags else 0.0
    score = max(
        0.0,
        min(
            1.0,
            (0.40 * evidence_recall)
            + (0.35 * concept_coverage)
            + (0.25 * evidence_precision)
            - penalty,
        ),
    )
    return {
        "quality_score": round(score, 4),
        "evidence_recall": evidence_recall,
        "evidence_precision": evidence_precision,
        "required_concepts_covered": covered,
        "required_concepts_total": len(required),
        "concept_coverage": concept_coverage,
        "hallucination_risk_flags": flags,
    }


def average_quality(records: list[dict[str, Any]]) -> dict[str, Any]:
    if not records:
        return {
            "quality_score_avg": None,
            "evidence_recall_avg": None,
            "evidence_precision_avg": None,
            "required_concept_coverage_avg": None,
            "hallucination_risk_flags": [],
        }
    flags: list[str] = []
    for record in records:
        for flag in record.get("hallucination_risk_flags", []):
            if flag not in flags:
                flags.append(flag)
    return {
        "quality_score_avg": _avg(record["quality_score"] for record in records),
        "evidence_recall_avg": _avg(record["evidence_recall"] for record in records),
        "evidence_precision_avg": _avg(record["evidence_precision"] for record in records),
        "required_concept_coverage_avg": _avg(record["concept_coverage"] for record in records),
        "hallucination_risk_flags": flags,
    }


def repo_quality_metrics(repo: Path, task: dict[str, Any]) -> dict[str, Any]:
    paths = [path.relative_to(repo).as_posix() for path in iter_repo_files(repo)]
    concepts = task.get("required_concepts", [])
    text = _repo_text_for_concepts(repo, concepts)
    return quality_metrics(
        returned_paths=paths,
        expected_patterns=task.get("expected_evidence", []),
        context_text=text,
        required_concepts=concepts,
        forbidden_hallucinations=task.get("forbidden_hallucinations", []),
    )


def extract_path_like_strings(value: Any) -> list[str]:
    found: list[str] = []

    def walk(item: Any) -> None:
        if isinstance(item, dict):
            for key, nested in item.items():
                walk(key)
                walk(nested)
        elif isinstance(item, list):
            for nested in item:
                walk(nested)
        elif isinstance(item, str):
            clean = item.strip().replace("\\", "/")
            if (
                "/" in clean
                and "://" not in clean
                and len(clean) < 500
                and not clean.startswith("/")
                and clean not in found
            ):
                found.append(clean.removeprefix("./"))

    walk(value)
    return found


def p50(values: list[int]) -> int | None:
    if not values:
        return None
    return int(median(values))


def base_result(tool: str) -> dict[str, Any]:
    return {
        "tool": tool,
        "status": "not_run",
        "version": "unknown",
        "install_command": None,
        "metrics": {},
        "capabilities": {
            "offline": False,
            "mcp": False,
            "task_memory": False,
            "freshness_check": False,
            "agent_ready_context": False,
        },
        "errors": [],
        "artifacts": {},
    }


def validate_result(result: dict[str, Any]) -> None:
    missing = REQUIRED_FIELDS - set(result)
    if missing:
        raise ValueError(f"{result.get('tool', 'unknown')} missing result fields: {sorted(missing)}")


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _path_matches(path: str, pattern: str) -> bool:
    clean_path = path.strip().replace("\\", "/").removeprefix("./")
    clean_pattern = pattern.strip().replace("\\", "/").removeprefix("./").rstrip("/")
    return clean_path == clean_pattern or clean_path.startswith(clean_pattern + "/")


def _concept_present(text: str, concept: str) -> bool:
    if _phrase_present(text, concept):
        return True
    text_terms = set(re.findall(r"[a-z0-9_]+", text.lower()))
    concept_terms = set(re.findall(r"[a-z0-9_]+", concept.lower()))
    return bool(concept_terms) and concept_terms.issubset(text_terms)


def _phrase_present(text: str, phrase: str) -> bool:
    return phrase.strip().lower() in text.lower()


def _repo_text_for_concepts(repo: Path, concepts: list[str]) -> str:
    if not concepts:
        return ""
    matched: set[str] = set()
    parts: list[str] = []
    for path in iter_repo_files(repo):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        rel = path.relative_to(repo).as_posix()
        combined = f"{rel}\n{text[:200_000]}"
        parts.append(combined)
        for concept in concepts:
            if concept not in matched and _concept_present(combined, concept):
                matched.add(concept)
        if len(matched) == len(concepts):
            break
    return "\n".join(parts)


def _avg(values: Iterable[float]) -> float:
    data = list(values)
    if not data:
        return 0.0
    return round(sum(data) / len(data), 4)
