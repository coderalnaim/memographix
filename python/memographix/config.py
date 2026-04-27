from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_TEXT = """[retrieval]
max_file_bytes = 2000000
task_match_threshold = 0.22
"""


DEFAULT_SUPPORTED_EXTENSIONS = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".rb": "ruby",
    ".cs": "csharp",
    ".kt": "kotlin",
    ".swift": "swift",
    ".php": "php",
    ".md": "markdown",
    ".mdx": "markdown",
    ".txt": "text",
    ".rst": "text",
    ".toml": "config",
    ".yaml": "config",
    ".yml": "config",
    ".json": "config",
    ".ini": "config",
    ".sql": "sql",
    ".sh": "shell",
    ".ps1": "powershell",
}

DEFAULT_STOPWORDS = {
    "about",
    "again",
    "after",
    "agent",
    "also",
    "and",
    "are",
    "can",
    "code",
    "does",
    "for",
    "from",
    "have",
    "how",
    "into",
    "issue",
    "make",
    "need",
    "please",
    "repo",
    "same",
    "that",
    "the",
    "this",
    "what",
    "when",
    "where",
    "with",
    "why",
}


@dataclass(frozen=True)
class RetrievalConfig:
    supported_extensions: dict[str, str]
    stopwords: frozenset[str]
    max_file_bytes: int = 2_000_000
    task_match_threshold: float = 0.22


@dataclass(frozen=True)
class RepoControl:
    config_path: Path
    configured: bool
    setup_completed: bool
    enabled: bool
    disabled_reason: str = ""
    last_enabled_at: str = ""
    last_disabled_at: str = ""

    @property
    def disabled_status(self) -> str:
        if not self.configured:
            return "repo not configured"
        if not self.setup_completed:
            return "repo setup incomplete"
        if not self.enabled:
            return self.disabled_reason or "repo disabled"
        return ""


def load_retrieval_config(root: Path) -> RetrievalConfig:
    config_path = root / ".memographix" / "config.toml"
    data = _load_toml(config_path) if config_path.exists() else {}
    retrieval = data.get("retrieval", data)

    extensions = dict(DEFAULT_SUPPORTED_EXTENSIONS)
    if isinstance(retrieval.get("supported_extensions"), dict):
        extensions = _normalize_extensions(retrieval["supported_extensions"])
    if isinstance(retrieval.get("extra_extensions"), dict):
        extensions.update(_normalize_extensions(retrieval["extra_extensions"]))

    stopwords = set(DEFAULT_STOPWORDS)
    if isinstance(retrieval.get("stopwords"), list):
        stopwords = {str(item).lower() for item in retrieval["stopwords"]}
    if isinstance(retrieval.get("extra_stopwords"), list):
        stopwords.update(str(item).lower() for item in retrieval["extra_stopwords"])
    if isinstance(retrieval.get("remove_stopwords"), list):
        stopwords.difference_update(str(item).lower() for item in retrieval["remove_stopwords"])

    max_file_bytes = int(retrieval.get("max_file_bytes", 2_000_000))
    task_match_threshold = float(retrieval.get("task_match_threshold", 0.22))
    return RetrievalConfig(
        supported_extensions=extensions,
        stopwords=frozenset(stopwords),
        max_file_bytes=max_file_bytes,
        task_match_threshold=task_match_threshold,
    )


def load_repo_control(root: Path) -> RepoControl:
    config_path = root / ".memographix" / "config.toml"
    if not config_path.exists():
        return RepoControl(
            config_path=config_path,
            configured=False,
            setup_completed=False,
            enabled=False,
            disabled_reason="repo not configured",
        )
    data = _load_toml(config_path)
    return RepoControl(
        config_path=config_path,
        configured=True,
        setup_completed=_as_bool(data.get("setup_completed", True)),
        enabled=_as_bool(data.get("enabled", True)),
        disabled_reason=str(data.get("disabled_reason", "")),
        last_enabled_at=str(data.get("last_enabled_at", "")),
        last_disabled_at=str(data.get("last_disabled_at", "")),
    )


def ensure_repo_config(root: Path, values: dict[str, Any]) -> tuple[Path, bool]:
    config_path = root / ".memographix" / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    created = not config_path.exists()
    text = config_path.read_text(encoding="utf-8") if config_path.exists() else DEFAULT_CONFIG_TEXT
    config_path.write_text(_set_top_level_values(text, values), encoding="utf-8")
    return config_path, created


def update_repo_control(root: Path, values: dict[str, Any]) -> Path:
    path, _created = ensure_repo_config(root, values)
    return path


def _normalize_extensions(raw: dict[Any, Any]) -> dict[str, str]:
    normalized = {}
    for key, value in raw.items():
        suffix = str(key)
        if not suffix.startswith("."):
            suffix = f".{suffix}"
        normalized[suffix.lower()] = str(value)
    return normalized


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _toml_literal(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    text = str(value)
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _set_top_level_values(text: str, values: dict[str, Any]) -> str:
    lines = text.splitlines()
    section_index = len(lines)
    for index, line in enumerate(lines):
        if line.strip().startswith("[") and line.strip().endswith("]"):
            section_index = index
            break

    prefix = lines[:section_index]
    suffix = lines[section_index:]
    seen: set[str] = set()
    rewritten: list[str] = []
    key_re = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=")
    for line in prefix:
        match = key_re.match(line.strip())
        if match and match.group(1) in values:
            key = match.group(1)
            rewritten.append(f"{key} = {_toml_literal(values[key])}")
            seen.add(key)
        else:
            rewritten.append(line)

    for key, value in values.items():
        if key not in seen:
            rewritten.append(f"{key} = {_toml_literal(value)}")

    if rewritten and rewritten[-1].strip() and suffix:
        rewritten.append("")
    return "\n".join([*rewritten, *suffix]).rstrip() + "\n"


def _load_toml(path: Path) -> dict[str, Any]:
    try:
        import tomllib  # Python 3.11+
    except ModuleNotFoundError:
        return _parse_minimal_toml(path.read_text(encoding="utf-8"))
    with path.open("rb") as f:
        return tomllib.load(f)


def _parse_minimal_toml(text: str) -> dict[str, Any]:
    data: dict[str, Any] = {}
    section: dict[str, Any] = data
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            name = line[1:-1].strip()
            section = data.setdefault(name, {})
            continue
        if "=" not in line:
            continue
        key, value = [part.strip() for part in line.split("=", 1)]
        section[key] = _parse_value(value)
    return data


def _parse_value(value: str) -> Any:
    if value.startswith("{") and value.endswith("}"):
        pairs = {}
        body = value[1:-1].strip()
        if not body:
            return pairs
        for item in body.split(","):
            key, val = [part.strip() for part in item.split("=", 1)]
            pairs[key.strip("\"'")] = _parse_value(val)
        return pairs
    try:
        return ast.literal_eval(value)
    except (SyntaxError, ValueError):
        if value.lower() in {"true", "false"}:
            return value.lower() == "true"
        try:
            return int(value)
        except ValueError:
            try:
                return float(value)
            except ValueError:
                return value.strip("\"'")
