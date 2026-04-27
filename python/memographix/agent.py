from __future__ import annotations

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

This project can use Memographix local task memory in `.memographix/`.

Use Memographix automatically for implementation, architecture, debugging, and
test-failure work:

- Before answering, call the `resolve_task` MCP tool with the user's task.
- If MCP is unavailable, run `mgx ask "<user task>" --budget 800`.
- After useful work, call the `capture_task` MCP tool with the final answer,
  changed files, commands, tests, and outcome.

Rules:
- Prefer fresh Memographix task capsules over re-reading raw files.
- If Memographix reports `status: stale`, inspect only the changed evidence files.
- If Memographix reports `status: disabled`, `enabled: false`, `repo disabled`,
  or `repo not configured`, continue normally without Memographix context and do
  not call `capture_task`.
- Do not ask the developer to manually save memory unless automatic capture fails.
- Never treat stale memory as authoritative.
"""


def install_agent_rules(root: Path, agent: str) -> Path:
    agent = agent.lower()
    if agent == "codex":
        path = root / "AGENTS.md"
    elif agent == "claude":
        path = root / "CLAUDE.md"
    elif agent == "gemini":
        path = root / "GEMINI.md"
    elif agent == "cursor":
        path = root / ".cursor" / "rules" / "memographix.mdc"
        path.parent.mkdir(parents=True, exist_ok=True)
        content = "---\nalwaysApply: true\n---\n\n" + AGENT_RULES
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
    if "This project can use Memographix local task memory" not in existing:
        path.write_text((existing.rstrip() + "\n\n" + AGENT_RULES).lstrip(), encoding="utf-8")
    return path
