from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCK = ROOT / "benchmarks" / "corpora" / "kubernetes.lock.json"


def run(cmd: list[str], cwd: Path | None = None) -> None:
    subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=True)


def load_lock(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def fetch(lock_path: Path = DEFAULT_LOCK) -> Path:
    lock = load_lock(lock_path)
    repo = lock["repo"]
    commit = lock["commit"]
    target = (ROOT / lock["path"]).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        run(["git", "clone", "--filter=blob:none", repo, str(target)])
    run(["git", "fetch", "--depth", "1", "origin", commit], cwd=target)
    run(["git", "checkout", "--detach", commit], cwd=target)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch a pinned public benchmark corpus.")
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    args = parser.parse_args()
    print(fetch(args.lock).as_posix())


if __name__ == "__main__":
    main()
