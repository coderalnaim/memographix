from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path

SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".memographix",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".tox",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    "node_modules",
    "site-packages",
    "dist",
    "build",
    "target",
    "coverage",
    ".next",
    ".turbo",
}

SKIP_FILES = {
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "Cargo.lock",
    "poetry.lock",
    "Pipfile.lock",
    "Gemfile.lock",
    "composer.lock",
    "go.sum",
}

SENSITIVE_PATTERNS = [
    re.compile(r"(^|[\\/])\.(env|envrc)(\.|$)", re.IGNORECASE),
    re.compile(r"\.(pem|key|p12|pfx|cert|crt|der|p8)$", re.IGNORECASE),
    re.compile(r"(credential|secret|passwd|password|token|private_key)", re.IGNORECASE),
    re.compile(r"(id_rsa|id_dsa|id_ecdsa|id_ed25519)(\.pub)?$", re.IGNORECASE),
    re.compile(r"(\.netrc|\.pgpass|\.htpasswd)$", re.IGNORECASE),
]


def load_ignore_patterns(root: Path) -> list[str]:
    patterns: list[str] = []
    for name in (".memographixignore", ".gitignore"):
        ignore_file = root / name
        if not ignore_file.exists():
            continue
        for line in ignore_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            clean = line.strip()
            if clean and not clean.startswith("#") and not clean.startswith("!"):
                patterns.append(clean)
    return patterns


def is_sensitive(path: Path) -> bool:
    text = str(path)
    return any(pattern.search(text) for pattern in SENSITIVE_PATTERNS)


def is_ignored(path: Path, root: Path, patterns: list[str]) -> bool:
    try:
        rel = str(path.relative_to(root)).replace(os.sep, "/")
    except ValueError:
        rel = str(path).replace(os.sep, "/")
    for pattern in patterns:
        p = pattern.strip("/")
        if not p:
            continue
        if fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(path.name, p):
            return True
        parts = rel.split("/")
        if any(fnmatch.fnmatch(part, p) for part in parts):
            return True
    return False

