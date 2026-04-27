from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path


BLOCKED_PREFIXES = (
    ".github/",
    ".mgx-local/",
    "benchmark_results/",
    "benchmarks/",
    "docs/",
    "scripts/",
    "tests/",
)
BLOCKED_NAMES = {
    ".dockerignore",
    "BENCHMARKS.md",
    "SECURITY.md",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dist", type=Path)
    parser.add_argument("--wheel-only", action="store_true")
    parser.add_argument("--sdist-only", action="store_true")
    parser.add_argument("--install-smoke", action="store_true")
    args = parser.parse_args()

    artifacts = sorted(path for path in args.dist.iterdir() if path.is_file())
    wheels = [path for path in artifacts if path.suffix == ".whl"]
    sdists = [path for path in artifacts if path.name.endswith(".tar.gz")]
    if args.wheel_only and not wheels:
        raise SystemExit("no wheels found")
    if args.sdist_only and not sdists:
        raise SystemExit("no sdist found")
    if not args.wheel_only and not args.sdist_only and (not wheels or not sdists):
        raise SystemExit("release requires at least one wheel and one sdist")

    for artifact in artifacts:
        names = _archive_names(artifact)
        blocked = [name for name in names if _is_blocked(name)]
        if blocked:
            sample = ", ".join(blocked[:10])
            raise SystemExit(f"{artifact.name} contains blocked release files: {sample}")

    if args.install_smoke:
        _install_smoke(args.dist)

    print("verified release artifacts:")
    for artifact in artifacts:
        print(f"- {artifact.name}")


def _archive_names(path: Path) -> list[str]:
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            return archive.namelist()
    if path.name.endswith(".tar.gz"):
        with tarfile.open(path) as archive:
            return [
                member.name.split("/", 1)[1] if "/" in member.name else member.name
                for member in archive.getmembers()
            ]
    return []


def _is_blocked(name: str) -> bool:
    clean = name.removeprefix("./")
    return clean in BLOCKED_NAMES or clean.startswith(BLOCKED_PREFIXES)


def _install_smoke(dist: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="mgx-release-smoke-") as tmp:
        root = Path(tmp)
        venv = root / "venv"
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
        bin_dir = venv / ("Scripts" if os.name == "nt" else "bin")
        python = bin_dir / ("python.exe" if os.name == "nt" else "python")
        pip = bin_dir / ("pip.exe" if os.name == "nt" else "pip")
        mgx = bin_dir / ("mgx.exe" if os.name == "nt" else "mgx")
        subprocess.run(
            [
                str(pip),
                "install",
                "--no-index",
                "--find-links",
                str(dist.resolve()),
                "memographix==0.1.0",
            ],
            check=True,
        )
        subprocess.run([str(pip), "check"], check=True)
        repo = root / "repo"
        repo.mkdir()
        (repo / "app.py").write_text("def handle():\n    return True\n", encoding="utf-8")
        subprocess.run(
            [str(mgx), "--root", str(repo), "setup", "--agents", "codex"],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        subprocess.run([str(mgx), "--root", str(repo), "status", "--json"], check=True)
        subprocess.run(
            [str(mgx), "--root", str(repo), "disable", "--reason", "release smoke"],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        subprocess.run(
            [str(mgx), "--root", str(repo), "enable"],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        subprocess.run(
            [str(mgx), "--root", str(repo), "ask", "how does handle work?", "--json"],
            check=True,
        )
        subprocess.run([str(mgx), "--root", str(repo), "savings", "--json"], check=True)
        subprocess.run([str(python), "-c", "import memographix"], check=True)


if __name__ == "__main__":
    main()
