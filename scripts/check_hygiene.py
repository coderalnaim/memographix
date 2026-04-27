from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BLOCKED_PATH_PREFIXES = (
    ".mgx-local/",
    "benchmark_results/",
    "benchmarks/results/",
)
BLOCKED_PATHS = {".gitignore"}
LOCAL_SKIP_PARTS = {
    ".git",
    ".memographix",
    ".mgx-local",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "build",
    "dist",
    "target",
}
BLOCKED_TEXT = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        re.escape("/Users/" + "naim"),
        "noc" + "fo" + r"-support-agent-backend",
        "support " + "agent backend",
    )
]
TEXT_SUFFIXES = {
    ".cfg",
    ".ini",
    ".json",
    ".md",
    ".py",
    ".rs",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}


def tracked_files() -> list[Path]:
    proc = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [ROOT / item.decode() for item in proc.stdout.split(b"\0") if item]


def check_paths(paths: list[Path]) -> list[str]:
    errors: list[str] = []
    for path in paths:
        rel = path.relative_to(ROOT).as_posix()
        if rel in BLOCKED_PATHS:
            errors.append(f"blocked tracked path: {rel}")
        if rel.startswith(BLOCKED_PATH_PREFIXES):
            errors.append(f"blocked tracked output path: {rel}")
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for pattern in BLOCKED_TEXT:
            if pattern.search(text):
                errors.append(f"blocked text pattern {pattern.pattern!r} in {rel}")
    return errors


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--all-files", action="store_true", help="scan all non-git files")
    args = parser.parse_args()
    if args.all_files:
        paths = [
            path
            for path in ROOT.rglob("*")
            if path.is_file()
            and not any(part in LOCAL_SKIP_PARTS or part.startswith(".venv") for part in path.parts)
        ]
    else:
        paths = tracked_files()
    errors = check_paths(paths)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
