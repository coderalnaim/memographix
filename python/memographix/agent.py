from __future__ import annotations

import os
import re
from pathlib import Path

SUPPORTED_AGENTS = (
    "codex",
    "claude",
    "cursor",
    "copilot",
    "gemini",
    "opencode",
    "aider",
    "windsurf",
)

AGENT_RULES = """# Memographix

This project uses strict Memographix local task memory in `.memographix/`.

Mandatory workflow for any repo-specific answer, file inspection, shell
exploration, debugging, implementation, review, README/doc edit, or test work:

- Before reading files, running repo commands, or answering, call the
  `resolve_task` MCP tool with the user's task.
- Always pass `repo` as this repo's absolute path:
  `{repo_root}`.
- If MCP tools are missing or the MCP call fails, self-repair without asking the
  user first by running:
  `mgx --root "{repo_root}" doctor --live --repair`
- After repair, retry MCP once. If MCP still cannot be used, fall back to the
  explicit CLI commands:
  `mgx --root "{repo_root}" ask "<user task>" --budget 800`
  and after work:
  `mgx --root "{repo_root}" remember --question "<user task>" --answer "<final answer>"`
  with `--evidence`, `--commands`, `--tests`, and `--outcome` as available.
- After useful repo work, call the `capture_task` MCP tool with the final
  answer, changed files, commands, tests, outcome, and the `resolve_event_id`
  and `repo_root` returned by `resolve_task` when available.
- End every final answer with exactly one short Memographix status line:
  `Memographix: saved task memory`,
  `Memographix: not saved - <reason>`, or
  `Memographix: disabled for this repo`.

Rules:
- Prefer fresh Memographix task capsules over re-reading raw files.
- If Memographix reports `status: stale`, inspect only the changed evidence files.
- If Memographix reports `status: disabled`, `enabled: false`, `repo disabled`,
  or `repo not configured`, continue normally without Memographix context and do
  not call `capture_task`.
- Do not ask the developer to manually save memory unless automatic capture fails.
- It is safe to run `mgx --root "{repo_root}" doctor --live --repair` or
  `mgx --root "{repo_root}" heal` without asking; both commands are repo-local,
  idempotent, and do not delete memory.
- Do not say task memory was saved unless `capture_task` returns `saved: true`.
- Never treat stale memory as authoritative.
- If `resolve_task` succeeded but `capture_task` returns `repo not configured`,
  retry capture once with `repo` set to the `repo_root` returned by `resolve_task`.
"""

CODEX_SKILL = """---
name: memographix
description: >
  Use before any repo-specific coding, docs, review, debugging, shell, or
  file-editing task to resolve and capture Memographix local task memory.
metadata:
  short-description: Use local repo memory for coding work
---

# Memographix

Use this skill for repo-specific development work. It exists because MCP server
configuration only makes tools available; the agent must still deliberately call
them.

## Required Workflow

1. If the Memographix MCP tools are not visible yet, discover them first using
   the available tool discovery mechanism for `Memographix resolve_task
   capture_task`.
2. Before reading files, running repo commands, editing files, reviewing code,
   debugging, or answering a repo-specific question, call `resolve_task` with the
   user's task. Always pass `repo` as the absolute repository root when it is
   known. Pass the repo name/path when the user names a repo or when the chat is
   not clearly opened inside the repo.
3. If MCP tools are missing or the MCP call fails, run
   `mgx --root "<absolute repo root>" doctor --live --repair` without asking the
   user first, then retry MCP once. This repair command is repo-local,
   idempotent, and does not delete memory.
4. If MCP still cannot be used after repair, use the explicit CLI fallback:
   `mgx --root "<absolute repo root>" ask "<user task>" --budget 800`.
5. Use the returned context only when Memographix reports it is enabled and
   fresh. If it is disabled or not configured, continue normally without
   Memographix context.
6. After useful repo work, call `capture_task` with the answer, changed files,
   evidence, commands, tests, outcome, and the `resolve_event_id` returned by
   `resolve_task` when available. Also pass `repo` as the `repo_root` returned by
   `resolve_task`.
7. If MCP capture is still unavailable after repair, use
   `mgx --root "<absolute repo root>" remember --question "<user task>" --answer "<final answer>"`
   with `--evidence`, `--commands`, `--tests`, and `--outcome` as available.
8. End the final answer with exactly the `final_status_line` returned by
   `capture_task`. If capture was not attempted because the repo is disabled or
   not configured, use the corresponding Memographix disabled/not-saved status.

Never say memory was saved unless `capture_task` returns `saved: true`.
If `resolve_task` succeeded but `capture_task` returns `repo not configured`,
retry capture once with `repo` set to the `repo_root` returned by `resolve_task`.
"""

MEMOGRAPHIX_HEADING_RE = re.compile(r"(?m)^# Memographix\s*$")


def install_agent_rules(root: Path, agent: str) -> Path:
    agent = agent.lower()
    rules = render_agent_rules(root)
    if agent == "codex":
        path = root / "AGENTS.md"
        install_codex_skill()
    elif agent == "claude":
        path = root / "CLAUDE.md"
    elif agent == "gemini":
        path = root / "GEMINI.md"
    elif agent == "cursor":
        path = root / ".cursor" / "rules" / "memographix.mdc"
        path.parent.mkdir(parents=True, exist_ok=True)
        content = "---\nalwaysApply: true\n---\n\n" + rules
        path.write_text(content, encoding="utf-8")
        return path
    elif agent == "copilot":
        path = root / ".github" / "copilot-instructions.md"
        path.parent.mkdir(parents=True, exist_ok=True)
    elif agent in {"opencode", "aider", "windsurf"}:
        path = root / "AGENTS.md"
    else:
        raise ValueError(f"unknown agent: {agent}")

    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    path.write_text(_merge_agent_rules(existing, rules), encoding="utf-8")
    return path


def install_codex_skill() -> Path:
    path = codex_skill_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.read_text(encoding="utf-8") != CODEX_SKILL:
        path.write_text(CODEX_SKILL, encoding="utf-8")
    return path


def codex_skill_path() -> Path:
    return _codex_skills_dir() / "memographix" / "SKILL.md"


def codex_skill_installed() -> bool:
    path = codex_skill_path()
    return path.exists() and "name: memographix" in path.read_text(encoding="utf-8")


def render_agent_rules(root: Path) -> str:
    return AGENT_RULES.format(repo_root=str(root.resolve()))


def _merge_agent_rules(existing: str, rules: str) -> str:
    match = MEMOGRAPHIX_HEADING_RE.search(existing)
    if not match:
        return (existing.rstrip() + "\n\n" + rules).lstrip()
    next_heading = re.search(r"(?m)^# (?!Memographix\s*$).+$", existing[match.end() :])
    end = match.end() + next_heading.start() if next_heading else len(existing)
    merged = existing[: match.start()].rstrip() + "\n\n" + rules
    if existing[end:].strip():
        merged += "\n\n" + existing[end:].lstrip()
    return merged.lstrip()


def _codex_skills_dir() -> Path:
    override = os.environ.get("MEMOGRAPHIX_CODEX_SKILLS_DIR")
    if override:
        return Path(override).expanduser().resolve()
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        return Path(codex_home).expanduser().resolve() / "skills"
    return Path.home() / ".codex" / "skills"
