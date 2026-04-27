from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from benchmarks.run import build_comparison, run_suite


ROOT = Path(__file__).resolve().parents[1]


def test_benchmark_smoke_memographix_vs_naive(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "auth.py").write_text(
        "def load_user(user_id):\n    return {'id': user_id}\n\n"
        "def auth_flow(user_id):\n    return load_user(user_id)\n",
        encoding="utf-8",
    )
    tasks = tmp_path / "tasks.json"
    tasks.write_text(
        json.dumps(
            [
                {
                    "id": "auth-flow",
                    "question": "How does auth flow load a user?",
                    "seed_answer": "auth_flow calls load_user.",
                    "expected_evidence": ["auth.py"],
                    "required_concepts": ["auth_flow", "load_user"],
                    "forbidden_hallucinations": ["remote authorization service"],
                }
            ]
        ),
        encoding="utf-8",
    )
    out = tmp_path / "results"
    comparison = run_suite(
        corpus=corpus,
        out=out,
        tools=["memographix", "naive", "grep"],
        tasks_path=tasks,
        budgets=[200, 500],
        timeout=120,
        allow_external_installs=False,
    )
    assert (out / "comparison.json").exists()
    assert (out / "comparison.md").exists()
    assert comparison["sandbox_destroyed"] is True
    assert comparison["winners"]["stale_evidence_detected"] == "memographix"
    assert comparison["winners"]["quality_score_avg"] in {"memographix", "naive", "grep"}


def test_package_contract_excludes_benchmark_tooling() -> None:
    pyproject_text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r"(?m)^dependencies\s*=\s*\[\s*\]$", pyproject_text)
    assert 'features = ["extension-module", "abi3-py310"]' in pyproject_text
    assert '"benchmarks/**"' in pyproject_text
    assert '"benchmark_results/**"' in pyproject_text
    assert '".mgx-local/**"' in pyproject_text
    assert '"docs/**"' in pyproject_text
    assert '"scripts/**"' in pyproject_text
    assert '"BENCHMARKS.md"' in pyproject_text
    assert '"SECURITY.md"' in pyproject_text

    manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
    assert "prune benchmarks" in manifest
    assert "prune benchmark_results" in manifest
    assert "prune .mgx-local" in manifest
    assert "prune docs" in manifest
    assert "prune scripts" in manifest
    assert "exclude BENCHMARKS.md" in manifest
    assert "exclude SECURITY.md" in manifest

    package_files = list((ROOT / "python" / "memographix").rglob("*"))
    assert all("benchmarks" not in str(path) for path in package_files)
    assert not (ROOT / ".gitignore").exists()


def test_readme_stays_simple() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert len(readme.splitlines()) < 120
    assert "docker" not in readme.lower()
    quickstart = readme.split("## Quick Start", 1)[1].split("##", 1)[0]
    assert "mgx remember" not in quickstart
    assert "mgx setup" in quickstart
    assert "mgx savings" in quickstart
    command_lines = [
        line for line in quickstart.splitlines()
        if line.startswith("mgx ") or line.startswith("pip ")
    ]
    assert len(command_lines) <= 5


def test_hygiene_script_blocks_private_paths() -> None:
    proc = subprocess.run(
        [sys.executable, "scripts/check_hygiene.py", "--all-files"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr


def test_kubernetes_benchmark_tasks_are_public() -> None:
    tasks = json.loads((ROOT / "benchmarks" / "tasks" / "golden_tasks.json").read_text())
    ids = {task["id"] for task in tasks}
    assert "kubelet-pod-lifecycle" in ids
    assert "scheduler-plugin-selection" in ids
    joined = json.dumps(tasks).lower()
    assert "noc" + "fo" not in joined
    assert "support " + "agent" not in joined
    for task in tasks:
        assert task["required_concepts"]
        assert isinstance(task["forbidden_hallucinations"], list)

    lock = json.loads(
        (ROOT / "benchmarks" / "corpora" / "kubernetes.lock.json").read_text(encoding="utf-8")
    )
    assert lock["repo"] == "https://github.com/kubernetes/kubernetes.git"
    assert len(lock["commit"]) == 40


def test_comparison_ignores_missing_external_metrics(tmp_path: Path) -> None:
    comparison = build_comparison(
        corpus=tmp_path,
        tasks_path=tmp_path / "tasks.json",
        budgets=[500],
        started="2026-04-27T00:00:00+00:00",
        sandbox_destroyed=True,
        results=[
            {
                "tool": "memographix",
                "status": "ok",
                "version": "0.1.1",
                "install_command": "pip install memographix",
                "metrics": {
                    "first_index_ms": 10,
                    "quality_score_avg": 0.8,
                    "repeated_context_avg_tokens_by_budget": {"500": 100},
                },
                "capabilities": {},
                "errors": [],
                "artifacts": {},
            },
            {
                "tool": "external",
                "status": "ok",
                "version": "unknown",
                "install_command": "external",
                "metrics": {
                    "first_index_ms": 5,
                    "quality_score_avg": 0.2,
                    "repeated_context_avg_tokens_by_budget": {},
                },
                "capabilities": {},
                "errors": [],
                "artifacts": {},
            },
        ],
    )
    assert comparison["winners"]["first_index_ms"] == "external"
    assert comparison["winners"]["repeated_tokens_500"] == "memographix"
    assert comparison["winners"]["quality_score_avg"] == "memographix"
