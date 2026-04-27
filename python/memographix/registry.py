from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REGISTRY_VERSION = 1


@dataclass(slots=True)
class RepoResolution:
    ok: bool
    root: Path | None = None
    matched_by: str = ""
    reason: str = ""
    candidates: list[dict[str, Any]] | None = None


def memographix_home() -> Path:
    override = os.environ.get("MEMOGRAPHIX_HOME")
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".memographix"


def registry_path() -> Path:
    return memographix_home() / "repos.json"


def register_repo(root: str | Path) -> dict[str, Any]:
    path = Path(root).resolve()
    data = _load_registry()
    repos = data.setdefault("repos", {})
    now = _utc_now()
    existing = repos.get(str(path), {})
    item = {
        "root": str(path),
        "name": path.name,
        "aliases": _aliases_for(path),
        "registered_at": existing.get("registered_at") or now,
        "updated_at": now,
    }
    repos[str(path)] = item
    _write_registry(data)
    return item


def list_registered_repos() -> list[dict[str, Any]]:
    repos = _load_registry().get("repos", {})
    items = [item for item in repos.values() if isinstance(item, dict)]
    return sorted(items, key=lambda item: item.get("name", ""))


def resolve_repo(
    repo: str | None = None,
    *,
    cwd: str | Path | None = None,
    hint: str = "",
) -> RepoResolution:
    query = (repo or "").strip()
    if query:
        path_match = _resolve_path_query(query)
        if path_match:
            return RepoResolution(ok=True, root=path_match, matched_by="path")

    repos = list_registered_repos()
    if query:
        matches = _match_registered_repos(repos, query)
        if len(matches) == 1:
            return RepoResolution(
                ok=True,
                root=Path(matches[0]["root"]),
                matched_by="repo",
            )
        if len(matches) > 1:
            return RepoResolution(
                ok=False,
                reason="ambiguous repo",
                candidates=_candidate_list(matches),
            )

    cwd_root = _configured_ancestor(Path(cwd).resolve()) if cwd else None
    if cwd_root:
        return RepoResolution(ok=True, root=cwd_root, matched_by="cwd")

    hint_matches = _match_registered_repos(repos, hint, substring=True) if hint else []
    if len(hint_matches) == 1:
        return RepoResolution(
            ok=True,
            root=Path(hint_matches[0]["root"]),
            matched_by="question",
        )
    if len(hint_matches) > 1:
        return RepoResolution(
            ok=False,
            reason="ambiguous repo",
            candidates=_candidate_list(hint_matches),
        )

    if len(repos) == 1:
        return RepoResolution(ok=True, root=Path(repos[0]["root"]), matched_by="only_registered")
    if repos:
        return RepoResolution(
            ok=False,
            reason="repo required",
            candidates=_candidate_list(repos),
        )
    return RepoResolution(ok=False, reason="repo not configured", candidates=[])


def _load_registry() -> dict[str, Any]:
    path = registry_path()
    if not path.exists():
        return {"version": REGISTRY_VERSION, "repos": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"version": REGISTRY_VERSION, "repos": {}}
    if not isinstance(data, dict):
        return {"version": REGISTRY_VERSION, "repos": {}}
    data.setdefault("version", REGISTRY_VERSION)
    data.setdefault("repos", {})
    if not isinstance(data["repos"], dict):
        data["repos"] = {}
    return data


def _write_registry(data: dict[str, Any]) -> None:
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _resolve_path_query(query: str) -> Path | None:
    candidate = Path(query).expanduser()
    if not candidate.exists():
        return None
    if candidate.is_file():
        candidate = candidate.parent
    return _configured_ancestor(candidate.resolve())


def _configured_ancestor(path: Path) -> Path | None:
    for candidate in (path, *path.parents):
        if (candidate / ".memographix" / "config.toml").exists():
            return candidate
    return None


def _match_registered_repos(
    repos: list[dict[str, Any]],
    query: str,
    *,
    substring: bool = False,
) -> list[dict[str, Any]]:
    needle = _normalize(query)
    if not needle:
        return []
    matches = []
    for item in repos:
        aliases = [item.get("name", ""), *(item.get("aliases") or []), item.get("root", "")]
        normalized_aliases = {_normalize(str(alias)) for alias in aliases if str(alias).strip()}
        if needle in normalized_aliases:
            matches.append(item)
            continue
        if substring and any(_alias_in_hint(alias, needle) for alias in normalized_aliases):
            matches.append(item)
    return matches


def _alias_in_hint(alias: str, hint: str) -> bool:
    if len(alias) < 6:
        return False
    return alias in hint


def _candidate_list(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "name": str(item.get("name", "")),
            "root": str(item.get("root", "")),
            "aliases": list(item.get("aliases") or [])[:6],
        }
        for item in items
    ]


def _aliases_for(root: Path) -> list[str]:
    tokens = [token for token in re.split(r"[-_\s]+", root.name.lower()) if token]
    aliases = {root.name, root.name.lower(), " ".join(tokens)}
    for index in range(1, len(tokens)):
        suffix = " ".join(tokens[index:])
        if len(suffix) >= 6:
            aliases.add(suffix)
    if len(tokens) >= 2:
        aliases.add("-".join(tokens))
        aliases.add("_".join(tokens))
    return sorted(alias for alias in aliases if alias)


def _normalize(value: str) -> str:
    return " ".join(token for token in re.split(r"[^a-z0-9]+", value.lower()) if token)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
